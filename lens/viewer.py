"""Process 2: the popup.

This owns the UI *and* the AI request. The request must happen here, not in
the action, so the popup is on screen before the network round-trip starts.
"""

from __future__ import annotations

import json
import os
import select
import shutil
import sys
import termios
import threading
import tty
from dataclasses import dataclass, field
from pathlib import Path

from . import action, clipboard, config, language, mode as modes
from .providers import ProviderError, build
from .ui import frame, style as styling

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
    status: str = ""
    mode: str = modes.GENERAL
    tick: int = 0
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
        provider = build(cfg)
        with state.lock:
            state.status = f"{provider.name} · {provider.model}"
            state.mode = kind
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

        footer = "[c] copy   [j/k] scroll   [Esc] close"
        if state.copied_ticks > 0:
            footer = "copied to clipboard"
        state.scroll = min(state.scroll, frame.max_scroll(body, height))
        scroll = state.scroll
        # Errors are prose, whatever mode produced them.
        mode = modes.GENERAL if state.error else state.mode
        style = styling.styler(mode, shown, sources)

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


def handle_key(data: bytes, state: State, page: int) -> bool:
    """Apply one input chunk. Returns False when the popup should close."""
    if data in (b"\x1b", b"q", b"Q", b"\x03"):
        return False

    with state.lock:
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
            "Select text in a pane with the mouse, then press the key again.\n"
            "Herdr Lens reads the selection from the clipboard, so `copy_on_select` "
            "must stay enabled (it is on by default).",
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

    fd = sys.stdin.fileno()
    try:
        saved = termios.tcgetattr(fd)
    except termios.error:
        saved = None

    out = sys.stdout
    out.write(frame.CLEAR + frame.HIDE_CURSOR + "\033[?1000;1006h")
    out.flush()
    if saved is not None:
        tty.setraw(fd)

    last = ""
    try:
        while True:
            size = shutil.get_terminal_size(fallback=(80, 24))
            painted = compose(state, size.columns, size.lines)
            if painted != last:
                out.write(painted)
                out.flush()
                last = painted

            ready, _, _ = select.select([fd], [], [], TICK)
            if ready:
                data = os.read(fd, 1024)
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
    except (KeyboardInterrupt, OSError):
        pass
    finally:
        if saved is not None:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        out.write("\033[?1000;1006l" + frame.SHOW_CURSOR + frame.CLEAR)
        out.flush()
    return 0


def main() -> int:
    return run(load_job())


if __name__ == "__main__":
    raise SystemExit(main())
