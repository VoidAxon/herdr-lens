"""Process 2: the popup.

This owns the UI *and* the AI request. The request must happen here, not in
the action, so the popup is on screen before the network round-trip starts.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

from . import action, clipboard, config, language, mode as modes
from .providers import ProviderError, build
from .ui import console, frame, style as styling
from .ui.console import Console

TICK = 0.1

TITLES = {
    modes.WORD: "Dictionary",
    modes.TERM: "Explanation",
    modes.GENERAL: "Translation",
    modes.EXPLAIN: "Explanation",
    modes.SUMMARIZE: "Summary",
}

WORKING = {
    modes.WORD: "Looking up…",
    modes.EXPLAIN: "Explaining…",
    modes.SUMMARIZE: "Summarising…",
}

JUNK_MESSAGE = (
    "Nothing to translate.",
    "The selection looks like punctuation, box drawing, or an identifier "
    "rather than text.",
)


@dataclass
class State:
    title: str = "Translating…"
    text: str = ""
    error: tuple[str, str] | None = None
    done: bool = False
    scroll: int = 0
    copied_ticks: int = 0
    # Set when the pane's foreground program owns the mouse, so a drag could
    # not have reached the clipboard the selection came from.
    stale_risk: str = ""
    status: str = ""
    mode: str = modes.GENERAL
    tick: int = 0
    # Search. `typing` is the moment between `/` and Enter, when keystrokes
    # are query text rather than commands.
    query: str = ""
    typing: bool = False
    draft: str = ""
    # Which match is current, as an index into the match list. Not the scroll
    # position: scroll is clamped to the content height, so a match in the last
    # screenful would be unreachable if the two were the same number.
    hit: int = -1
    body: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


def load_job() -> dict:
    # Clear anything a previous popup failed to consume, so an orphaned
    # selection cannot sit on disk until the next translation happens.
    try:
        action.sweep()
    except OSError:
        pass

    path = os.environ.get("LENS_JOB")
    if not path:
        return {}
    job_file = Path(path)
    try:
        payload = json.loads(job_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    finally:
        # One-shot: the selection should not linger on disk after it is read.
        try:
            job_file.unlink(missing_ok=True)
        except OSError:
            pass
    return payload


def translate(job: dict, state: State, cfg: config.Config | None = None) -> None:
    """Worker thread. Only the selected text ever leaves this process."""
    try:
        if cfg is None:
            cfg = config.detect(config.load())
        selected = job["text"]
        kind = job.get("mode") or modes.classify(selected, cfg.word_lookup)
        provider = build(cfg, kind)
        with state.lock:
            state.status = f"{provider.name} · {provider.model}"
            state.mode = kind
        # Only worth asking when the text came from the clipboard: that is the
        # path a mouse-grabbing program silently breaks. Done here rather than
        # in the action so it costs the network wait, not the time to first
        # paint.
        if job.get("selection_source") == "clipboard":
            owner = action.mouse_owner(job.get("pane_id", ""))
            if owner:
                with state.lock:
                    state.stale_risk = owner
        def show(partial: str) -> None:
            """Called per token: the popup fills in while the model is still
            writing, which is what most of the perceived speed comes from."""
            with state.lock:
                state.text = partial

        # Script plus the configured candidates usually pin the source down
        # exactly; see lens/language.py for why detection alone cannot.
        source = language.resolve(selected, cfg.source_language)
        result = provider.translate(
            selected, source, language.display(cfg.target_language),
            cfg.rendered_prompt(kind), on_chunk=show,
        )
        with state.lock:
            if result:
                state.title = TITLES.get(kind, "Translation")
                state.text = result
            else:
                state.error = ("The provider returned an empty response.", "")
    except config.ConfigError as exc:
        with state.lock:
            state.error = ("Your Herdr Lens config has a problem.", str(exc))
    except ProviderError as exc:
        with state.lock:
            state.error = (exc.message, exc.hint)
    except Exception as exc:  # noqa: BLE001 - a crash here must still render
        with state.lock:
            state.error = ("Herdr Lens hit an unexpected error.", f"{type(exc).__name__}: {exc}")
    finally:
        with state.lock:
            state.done = True


def compose(state: State, width: int, height: int) -> str:
    with state.lock:
        shown = state.text
        if state.error:
            message, hint = state.error
            shown = f"{message}\n\n{hint}" if hint else message
            body, gutter, sources = frame.layout(shown, width, height)
            title, status = "Lens", ""
        elif state.done:
            body, gutter, sources = frame.layout(shown, width, height)
            title, status = state.title, state.status
        elif state.text:
            # Streaming in: show what has arrived, with the spinner moved to
            # the status line so the text itself does not jump around.
            body, gutter, sources = frame.layout(shown, width, height)
            spin = frame.SPINNER[state.tick % len(frame.SPINNER)]
            title = state.title
            status = f"{spin} {state.status}" if state.status else spin
        else:
            spin = frame.SPINNER[state.tick % len(frame.SPINNER)]
            body, gutter, sources = ["", f"  {spin}  translating…"], False, [0, 1]
            shown = ""
            title, status = state.title, state.status

        if state.stale_risk:
            # Replaces the provider, rather than joining it. Not an error — a
            # `"+y` yank reaches the clipboard perfectly well — but it is the
            # one fact the reader cannot see for themselves, that a *drag* in
            # this pane never got here. The provider they can infer; at a narrow
            # width the warning is what has to survive.
            warning = f"⚠ {state.stale_risk} has the mouse"
            spin = status.split(" ", 1)[0] if status and not state.done else ""
            status = f"{spin} {warning}".strip()

        # handle_key jumps between matches and has no body of its own; compose
        # runs immediately before every read, so this is always the body on
        # screen rather than a stale one.
        state.body = body
        state.scroll = min(state.scroll, frame.max_scroll(body, height))
        scroll = state.scroll
        hits = _hits(body, state.query)
        current = hits[state.hit] if 0 <= state.hit < len(hits) else None

        footer = "[c] copy   [/] find   [j/k] scroll   [Esc] close"
        if state.copied_ticks > 0:
            footer = "copied to clipboard"
        if state.typing:
            # The footer becomes the input line: a popup this small has nowhere
            # else to put one, and it is where the eye already is.
            footer = f"/{state.draft}▏"
        elif state.query:
            here = state.hit + 1 if 0 <= state.hit < len(hits) else 0
            footer = (f"/{state.query}   {here}/{len(hits)}   [n/N] next/prev   [Esc] close"
                      if hits else f"/{state.query}   no match   [Esc] close")
        # Errors are prose, whatever mode produced them.
        mode = modes.GENERAL if state.error else state.mode
        style = styling.styler(mode, shown, sources,
                               highlight=state.query, current=current)

    return frame.render(
        title=title, body=body, footer=footer, width=width, height=height,
        scroll=scroll, status=status, gutter=gutter, style=style,
    )


def copyable(state: State) -> str:
    with state.lock:
        if state.error:
            message, hint = state.error
            return f"{message}\n{hint}".strip()
        return state.text


def _hits(body: list[str], query: str) -> list[tuple[int, int]]:
    """Every occurrence, as (row, start).

    The unit is an occurrence rather than a line. A row can hold more than one,
    and counting lines makes `n` skip hits and makes "current" point at all of
    them at once.
    """
    if not query:
        return []
    return [(row, start)
            for row, line in enumerate(body)
            for start, _ in styling.spans_in(line, query)]


def _seek(state: State, forward: bool, rows_visible: int) -> None:
    """Move to the next occurrence, wrapping, and scroll only to reach it."""
    hits = _hits(state.body, state.query)
    if not hits:
        state.hit = -1
        return
    if state.hit < 0:
        # First jump after a query: start from what is on screen rather than
        # from the top, so searching does not throw away where you were.
        state.hit = next((i for i, (r, _) in enumerate(hits)
                          if r >= state.scroll), 0)
    else:
        state.hit = (state.hit + (1 if forward else -1)) % len(hits)

    row = hits[state.hit][0]
    if row < state.scroll:
        state.scroll = row
    elif row >= state.scroll + rows_visible:
        # Put it on the last visible line rather than the first: the lines
        # after a hit are usually the reason you were looking for it.
        state.scroll = row - rows_visible + 1


def handle_key(data: bytes, state: State, page: int) -> bool:
    """Apply one input chunk. Returns False when the popup should close."""
    with state.lock:
        if state.typing:
            # Everything is query text here, so Esc has to cancel the search
            # rather than close the popup — otherwise a mistyped search costs
            # you the result you were searching.
            if data == b"\x1b":
                state.typing, state.draft = False, ""
            elif data in (b"\r", b"\n"):
                state.typing = False
                state.query, state.draft = state.draft, ""
                state.hit = -1
                _seek(state, True, page)
            elif data in (b"\x7f", b"\x08"):
                state.draft = state.draft[:-1]
            elif data == b"\x03":
                state.typing, state.draft, state.query = False, "", ""
            else:
                text = data.decode("utf-8", "ignore")
                state.draft += "".join(c for c in text if c.isprintable())
            return True

    if data in (b"\x1b", b"q", b"Q", b"\x03"):
        return False

    with state.lock:
        if data == b"/":
            state.typing, state.draft = True, ""
            return True
        if data == b"n":
            _seek(state, True, page)
            return True
        if data == b"N":
            _seek(state, False, page)
            return True
        if data in (b"j", b"\x1b[B"):
            state.scroll += 1
        elif data in (b"k", b"\x1b[A"):
            state.scroll = max(0, state.scroll - 1)
        elif data in (b"\x1b[6~", b" "):
            state.scroll += page
        elif data == b"\x1b[5~":
            state.scroll = max(0, state.scroll - page)
        elif data == b"g":
            state.scroll = 0
        elif data == b"G":
            state.scroll = 10**6  # clamped against content on the next compose
        elif data.startswith(b"\x1b[<65;"):  # SGR wheel down
            state.scroll += 3
        elif data.startswith(b"\x1b[<64;"):  # SGR wheel up
            state.scroll = max(0, state.scroll - 3)
    return True


def prepare(job: dict, state: State) -> dict | None:
    """Settle everything that can be decided without the network.

    Returns the job for the worker, or `None` when the popup already holds its
    final content — an unusable Python, an empty selection, or junk. Rejecting
    here rather than at the provider is what makes those cases instant.
    """
    # Cleaned before anything else looks at it: classification, the prompt, and
    # the display all see the same text, and no control sequence reaches the
    # provider. `frame.layout` strips them again on the way out, because the
    # model's reply is outside text too.
    selected = frame.sanitize(job.get("text") or "")
    job = {**job, "text": selected}

    if not selected.strip():
        state.error = (
            "No text selected.",
            "Select text with the mouse, then press the key again.\n\n"
            "Lens reads the selection from the clipboard, so it needs Herdr's "
            "`copy_on_select` (on by default).\n\n"
            "In a full-screen program — vim, less, htop — the drag belongs to "
            "that program and never reaches the clipboard. Copy to the system "
            "clipboard instead: in vim that is `\"+y`.",
        )
        state.done = True
        return None

    if modes.is_junk(selected):
        state.error = JUNK_MESSAGE
        state.done = True
        return None

    # An explicit action overrides what the text looks like: pressing the
    # summarise key on one sentence still means summarise.
    state.mode = job.get("mode") or modes.classify(selected)
    limit = modes.input_limit(state.mode)
    selected, truncated = modes.truncate(selected, limit)
    state.title = WORKING.get(state.mode, state.title)
    if truncated:
        state.status = f"first {limit} chars"
    return {**job, "text": selected}


def run(job: dict) -> int:
    state = State()

    prepared = prepare(job, state)
    if prepared is not None:
        threading.Thread(target=translate, args=(prepared, state), daemon=True).start()

    out = sys.stdout
    last = ""
    try:
        with Console() as term:
            out.write(frame.CLEAR + frame.HIDE_CURSOR + console.MOUSE_ON)
            out.flush()
            try:
                while True:
                    size = shutil.get_terminal_size(fallback=(80, 24))
                    painted = compose(state, size.columns, size.lines)
                    if painted != last:
                        out.write(painted)
                        out.flush()
                        last = painted

                    data = term.read(TICK)
                    if data is not None:
                        if not data:
                            break
                        if data in (b"c", b"C"):
                            clipboard.osc52(copyable(state), out)
                            with state.lock:
                                state.copied_ticks = 12
                            last = ""
                            continue
                        if not handle_key(data, state, max(1, size.lines - 5)):
                            break
                    with state.lock:
                        if not state.done:
                            state.tick += 1  # keeps the spinner alive while streaming
                        if state.copied_ticks:
                            state.copied_ticks -= 1
            finally:
                out.write(console.MOUSE_OFF + frame.SHOW_CURSOR + frame.CLEAR)
                out.flush()
    except (KeyboardInterrupt, OSError):
        pass
    return 0


def main() -> int:
    return run(load_job())


if __name__ == "__main__":
    raise SystemExit(main())
