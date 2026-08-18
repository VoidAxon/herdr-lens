"""Reading the live visual selection out of a running Neovim.

A drag inside Neovim belongs to Neovim: it is in mouse-reporting mode, so Herdr
never sees a selection and the clipboard is not updated. But Neovim knows what
is selected, and it is listening — every instance starts an RPC server at
`$XDG_RUNTIME_DIR/nvim.<pid>.0` without being asked.

The expression matters. `'<` and `'>` are only written when visual mode *ends*,
so they read as zeros while a selection is being made — which is exactly when we
are asked. `getpos('v')` and `getpos('.')` are live, and `getregion()` turns them
into text.

The common advice is to send `y` and read `"0` instead. That works and costs the
user their register, their `"0` history, and their visual mode. This is a pure
read: measured at 4 ms, and afterwards `mode()` is still `v` and `"0` is
untouched.

Neovim only. Classic Vim has `+clientserver`, but it needs an X server for the
registry and has no `getregion()`; there the `"+y` route remains.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

# Only ask when a selection is actually in progress; in normal mode getregion()
# would return the line under the cursor, which the user did not select.
EXPRESSION = (
    'mode() =~ "^[vV\\<C-v>]" '
    '? join(getregion(getpos("v"), getpos("."), {"type": mode()}), "\\n") '
    ': ""'
)

TIMEOUT = 2.0


def _children(pid: int) -> list[int]:
    """Direct children of `pid`.

    `/proc` first because it costs nothing, then `pgrep`, because macOS has no
    `/proc` at all — and this plugin claims to support macOS. Without the
    fallback the Neovim path silently fails there in exactly the way it failed
    on Linux before the fork was accounted for.
    """
    children: list[int] = []
    try:
        for task in Path(f"/proc/{pid}/task").iterdir():
            # Read inside the loop, not around the call: `iterdir` is a
            # generator, so a missing directory raises here rather than above.
            try:
                children += [int(c) for c in (task / "children").read_text().split()]
            except (OSError, ValueError):
                continue
        return children
    except OSError:
        pass

    if os.name == "nt":
        # No /proc and no pgrep. wmic is deprecated but present far more widely
        # than a PowerShell one-liner is fast to start.
        return _windows_children(pid)

    pgrep = shutil.which("pgrep")
    if not pgrep:
        return []
    try:
        result = subprocess.run([pgrep, "-P", str(pid)],
                                capture_output=True, timeout=2, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [int(line) for line in result.stdout.split() if line.isdigit()]


def _windows_children(pid: int) -> list[int]:
    query = (
        "Get-CimInstance Win32_Process -Filter "
        f"\"ParentProcessId={pid}\" | Select-Object -ExpandProperty ProcessId"
    )
    shell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not shell:
        return []
    try:
        result = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-Command", query],
            capture_output=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [int(line) for line in result.stdout.split() if line.strip().isdigit()]


def _descendants(pid: int, depth: int = 2) -> list[int]:
    """`pid` and its children, because an interactive Neovim forks.

    Herdr reports the process it launched; the socket is named after whichever
    process actually opened it, and those are not the same pid. A `--headless`
    Neovim does not fork, which is why this only shows up against a real one:

        herdr says       584324
        socket is        nvim.584325.0
        584325's parent  584324
    """
    found = [pid]
    frontier = [pid]
    for _ in range(depth):
        children = [c for parent in frontier for c in _children(parent)]
        children = [c for c in children if c not in found]
        if not children:
            break
        found += children
        frontier = children
    return found


def socket_for(pid: int, env: dict[str, str] | None = None) -> str:
    """Neovim's own RPC socket for `pid` or one of its children, or "".

    Globbed rather than assembled: the trailing index is not always 0, and the
    runtime directory moves with `$XDG_RUNTIME_DIR`.
    """
    env = os.environ if env is None else env
    if os.name == "nt":
        return _windows_pipe(pid)

    # getuid is POSIX-only, so it is reached only after the branch above.
    runtime = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    bases = (Path(runtime), Path(env.get("TMPDIR", "/tmp")))
    for candidate in _descendants(pid):
        for base in bases:
            try:
                matches = sorted(base.glob(f"nvim.{candidate}.*"))
            except OSError:
                continue
            for match in matches:
                if match.is_socket():
                    return str(match)
    return ""


def _windows_pipe(pid: int) -> str:
    r"""Neovim's endpoint on Windows, which is a named pipe and not a socket.

    The `\\.\pipe` namespace is listable, so the same "which pid owns it" question is asked
    the same way — only the namespace differs.
    """
    try:
        names = set(os.listdir(r"\\.\pipe"))
    except OSError:
        return ""
    for candidate in _descendants(pid):
        prefix = f"nvim.{candidate}."
        for name in sorted(names):
            if name.startswith(prefix):
                return rf"\\.\pipe\{name}"
    return ""


def _client(pid: int) -> str:
    """The nvim binary to talk with.

    Taken from the running process where possible: the plugin's `PATH` is
    Herdr's, which need not contain the nvim the user actually launched.
    """
    try:
        exe = os.readlink(f"/proc/{pid}/exe")
        if exe and Path(exe).exists():
            return exe
    except OSError:
        pass
    return shutil.which("nvim") or ""


def visual_selection(pid: int, env: dict[str, str] | None = None) -> str:
    """The text selected in the Neovim running as `pid`, or "".

    Empty covers every uninteresting case identically — not in visual mode, no
    socket, no client, a version without `getregion()` — because the caller's
    next move is the same for all of them.
    """
    sock = socket_for(pid, env)
    client = _client(pid)
    if not sock or not client:
        return ""
    try:
        result = subprocess.run(
            [client, "--server", sock, "--remote-expr", EXPRESSION],
            capture_output=True, timeout=TIMEOUT, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.decode("utf-8", "replace").strip("\n")
