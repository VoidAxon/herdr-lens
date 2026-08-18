"""Process 1: the action shim.

A keybinding can only invoke an *action*, and only an action can open a plugin
pane — so this exists purely to grab the selection and hand it to the popup.
It must stay fast and must never call the AI provider: the popup has to be on
screen before the network request starts.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

from . import config, selection

PLUGIN_ID = "herdr-lens"

# Which action fired is the only way to know what the user wants: the same
# selection can legitimately be translated, explained, or summarised, and the
# text itself cannot say which. Translation alone is inferred from content.
FORCED_MODES = {"explain": "explain", "summarize": "summarize"}
VIEWER_ENTRYPOINT = "viewer"
# A job is consumed about 180 ms after it is written. Anything still on disk
# after a minute belongs to a popup that never opened, and the selection it
# holds should not outlive the attempt to show it.
JOB_TTL_SECONDS = 60


def job_dir() -> Path:
    root = os.environ.get("HERDR_PLUGIN_STATE_DIR")
    base = Path(root) if root else Path.home() / ".local/state/herdr/plugins/herdr-lens"
    return base / "jobs"


def sweep(directory: Path | None = None) -> None:
    """Drop job files a viewer never consumed (crash, popup refused to open).

    Called from both processes: the action sweeps before writing, and the
    viewer sweeps on startup. Neither alone is enough — sweeping only at write
    time leaves an orphan on disk indefinitely if the user never translates
    again, which is the one case where a selection outlives its popup.
    """
    directory = job_dir() if directory is None else directory
    cutoff = time.time() - JOB_TTL_SECONDS
    try:
        for stale in directory.glob("*.json"):
            if stale.stat().st_mtime < cutoff:
                stale.unlink(missing_ok=True)
    except OSError:
        pass


def write_job(payload: dict, directory: Path | None = None) -> Path:
    """Persist the request for the viewer.

    A file rather than an env var: selections are unbounded, argv/env are not,
    and the state dir is already private to this plugin.
    """
    directory = job_dir() if directory is None else directory
    directory.mkdir(parents=True, exist_ok=True)
    sweep(directory)
    path = directory / f"{uuid.uuid4().hex}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    return path


def api(method: str, params: dict) -> dict | None:
    """Call Herdr's socket API, or None if it cannot be reached.

    Herdr hands every plugin process `HERDR_SOCKET_PATH` precisely so it can
    call back like this. The socket accepts geometry that this build's CLI
    does not expose, which is the only way `[popup] width/height` can be
    honoured at all.
    """
    path = os.environ.get("HERDR_SOCKET_PATH")
    if not path:
        return None
    request = json.dumps({"id": "lens", "method": method, "params": params})
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(10)
            sock.connect(path)
            sock.sendall((request + "\n").encode("utf-8"))
            return json.loads(sock.makefile().readline() or "{}")
    except (OSError, ValueError):
        return None


def close_popup() -> bool:
    """Close whatever popup is open.

    Herdr permits one popup at a time, and the open one does not appear in
    `pane list`, so there is no id to close it by — but `popup.close` needs
    no id.
    """
    reply = api("popup.close", {})
    return reply is not None and "error" not in reply


# Programs that put the terminal in mouse-reporting mode. While one of these
# has the pane, a drag belongs to *it* — Herdr never sees a selection, so
# `copy_on_select` cannot fire and the clipboard still holds whatever was there
# before. The popup then answers about that instead, silently.
#
# A list rather than "anything that is not a shell", because a TUI does not
# necessarily grab the mouse: agent CLIs run full-screen and selection keeps
# working in them. Add names as they turn up.
MOUSE_GRABBERS = {
    "vim", "nvim", "vi", "emacs", "helix", "hx", "kak", "micro",
    "less", "more", "man", "most",
    "htop", "top", "btop", "btm", "atop",
    "tig", "lazygit", "gitui", "ranger", "yazi", "nnn", "lf", "mc",
    "fzf", "k9s", "ncdu", "tmux", "screen",
}


def foreground_process(pane_id: str) -> str:
    """The name of the program currently in the foreground of `pane_id`."""
    if not pane_id:
        return ""
    reply = api("pane.process_info", {"pane_id": pane_id}) or {}
    info = (reply.get("result") or {}).get("process_info") or {}
    processes = info.get("foreground_processes") or []
    return processes[-1].get("name", "") if processes else ""


def mouse_owner(pane_id: str) -> str:
    """The program holding the mouse in `pane_id`, or "" if the pane is free."""
    name = foreground_process(pane_id)
    return name if name in MOUSE_GRABBERS else ""


def _seen_path() -> Path:
    return job_dir().parent / "last-selection"


def selection_is_new(text: str) -> bool:
    """Has the clipboard changed since the last popup?

    Stored as a hash, never the text: this file outlives a job by design, and
    the selection itself must not.
    """
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    path = _seen_path()
    try:
        previous = path.read_text(encoding="utf-8").strip()
    except OSError:
        previous = ""
    return digest != previous


def remember_selection(text: str) -> None:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    path = _seen_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(digest, encoding="utf-8")
        path.chmod(0o600)
    except OSError:
        pass


def notify(title: str, body: str = "") -> None:
    """The only channel available when a popup cannot be opened.

    Without it the keypress does nothing visible while an older popup sits
    on screen showing an older answer — which reads as a stale result rather
    than a refused request.
    """
    herdr = os.environ.get("HERDR_BIN_PATH") or "herdr"
    argv = [herdr, "notification", "show", title]
    if body:
        argv += ["--body", body]
    try:
        subprocess.run(argv, capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass


def popup_size() -> dict:
    """The configured popup geometry, if any.

    Read here rather than in the viewer because the size has to be decided
    before the pane exists. Reading the config costs about a millisecond and
    only touches the `[popup]` table; a broken config must not stop the popup
    from opening, since the popup is where that error gets reported.
    """
    try:
        cfg = config.load()
    except Exception:  # noqa: BLE001 - the viewer will report it properly
        return {}
    return {k: v for k, v in
            (("width", cfg.popup_width), ("height", cfg.popup_height)) if v}


def open_popup(job_path: Path, retry: bool = True) -> int:
    params = {
        "plugin_id": PLUGIN_ID,
        "entrypoint": VIEWER_ENTRYPOINT,
        "placement": "popup",
        "focus": True,
        "env": {"LENS_JOB": str(job_path)},
        **popup_size(),
    }
    reply = api("plugin.pane.open", params)
    if reply is None:
        # No socket. Nothing has honoured the geometry, but a popup at the
        # manifest's size beats no popup at all.
        return open_popup_via_cli(job_path)

    error = reply.get("error")
    if not error:
        return 0

    detail = str(error.get("message", error))
    sys.stderr.write(detail + "\n")
    # Pressing the key must always answer the selection just made. An older
    # popup still on screen is a stale answer, not a reason to refuse — so
    # replace it rather than reporting a conflict.
    if retry and "popup already open" in detail and close_popup():
        return open_popup(job_path, retry=False)
    notify("Lens could not open", detail.strip()[:120])
    return 1


def open_popup_via_cli(job_path: Path) -> int:
    """Fallback for when the socket is unreachable."""
    herdr = os.environ.get("HERDR_BIN_PATH") or "herdr"
    argv = [
        herdr, "plugin", "pane", "open",
        "--plugin", PLUGIN_ID,
        "--entrypoint", VIEWER_ENTRYPOINT,
        "--focus",
        "--env", f"LENS_JOB={job_path}",
    ]
    try:
        result = subprocess.run(argv, capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"herdr-lens: could not open popup: {exc}", file=sys.stderr)
        return 1
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace")
        sys.stderr.write(detail)
        notify("Lens could not open", detail.strip()[:120])
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    action = argv[0] if argv else "translate"

    sel = selection.acquire()
    pane = selection.focused_pane(os.environ)

    # A program in mouse-reporting mode owns the drag, so Herdr never saw a
    # selection and the clipboard still holds whatever was there before. On its
    # own that is not enough to refuse: `"+y` reaches the clipboard perfectly
    # well, and then this keypress is correct. What settles it is whether the
    # clipboard changed at all — if it did not, there is nothing new to answer
    # about, and a popup would answer confidently about the wrong text.
    #
    # Costs one socket round-trip, measured at 0.6 ms against the 430 ms the
    # clipboard read already takes.
    if sel.source == "clipboard" and not selection_is_new(sel.text):
        owner = mouse_owner(pane)
        if owner:
            notify(
                f"{owner} has the mouse",
                f'Herdr never saw the selection, so Lens would translate '
                f'whatever was copied before. In {owner}, copy with "+y '
                f"and press the key again.",
            )
            return 0

    payload = {
        "action": action,
        "mode": FORCED_MODES.get(action),
        "text": sel.text,
        "selection_source": sel.source,
        "selection_backend": sel.backend,
        # Passed along rather than resolved here: the viewer can ask Herdr what
        # is running while it waits on the network, and this process must not
        # spend a socket round-trip before the popup is on screen.
        "pane_id": pane,
    }
    remember_selection(sel.text)
    return open_popup(write_job(payload))


if __name__ == "__main__":
    raise SystemExit(main())
