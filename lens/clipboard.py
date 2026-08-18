"""Reading the system clipboard, and writing to it via OSC 52.

Herdr 0.8.0 does not populate `selected_text` on the keybinding path, so the
clipboard is how the selection actually reaches the plugin — see
`selection.py`. Backends are tried native-first; on WSL the clipboard lives on
the Windows side and needs an interop binary.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys

# (executable, argv, required env var or None, timeout seconds)
_BACKENDS = [
    ("wl-paste", ["wl-paste", "--no-newline"], "WAYLAND_DISPLAY", 2),
    ("xclip", ["xclip", "-selection", "clipboard", "-o"], "DISPLAY", 2),
    ("xsel", ["xsel", "--clipboard", "--output"], "DISPLAY", 2),
    ("pbpaste", ["pbpaste"], None, 2),
    ("win32yank.exe", ["win32yank.exe", "-o"], None, 5),
    ("powershell.exe", ["powershell.exe", "-NoProfile", "-Command", "Get-Clipboard"], None, 10),
    # Native Windows, where the interop suffix is not part of the name.
    ("powershell", ["powershell", "-NoProfile", "-Command", "Get-Clipboard"], None, 10),
]


def read(env: dict[str, str] | None = None) -> tuple[str, str]:
    """Return (text, backend_name). Text is empty when nothing could be read."""
    env = os.environ if env is None else env
    for name, argv, required_env, timeout in _BACKENDS:
        if required_env and not env.get(required_env):
            continue
        if not shutil.which(argv[0]):
            continue
        try:
            out = subprocess.run(
                argv, capture_output=True, timeout=timeout, check=False
            ).stdout.decode("utf-8", "replace")
        except (OSError, subprocess.TimeoutExpired):
            continue
        # Windows backends hand back CRLF; normalise so wrapping behaves.
        text = out.replace("\r\n", "\n").replace("\r", "")
        if text.strip():
            return text, name
    return "", "none"


def osc52(text: str, stream=None) -> None:
    """Copy `text` to the clipboard by asking the terminal to do it.

    Herdr forwards OSC 52 to the host terminal, which is the only copy path
    available to a plugin — the socket API has no clipboard method.
    """
    stream = sys.stdout if stream is None else stream
    payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
    stream.write(f"\033]52;c;{payload}\a")
    stream.flush()
