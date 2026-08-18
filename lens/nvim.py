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


def socket_for(pid: int, env: dict[str, str] | None = None) -> str:
    """Neovim's own RPC socket for `pid`, or "" if it is not there.

    Globbed rather than assembled: the trailing index is not always 0, and the
    runtime directory moves with `$XDG_RUNTIME_DIR`.
    """
    env = os.environ if env is None else env
    runtime = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    for base in (Path(runtime), Path(env.get("TMPDIR", "/tmp"))):
        try:
            matches = sorted(base.glob(f"nvim.{pid}.*"))
        except OSError:
            continue
        for match in matches:
            if match.is_socket():
                return str(match)
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
