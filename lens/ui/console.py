"""Raw keyboard input, on the two kinds of terminal there are.

Everything else in the popup is portable already: it writes ANSI and counts
columns. Only two things are not — putting the terminal into raw mode, and
waiting for a keypress with a timeout — so they live here and nothing above this
line needs to know which platform it is on.

The Windows side translates console key codes into the ANSI sequences the POSIX
side would have produced, rather than teaching the caller a second vocabulary.
An arrow key arrives as `\\xe0H` and leaves here as `\\x1b[A`, so `handle_key`
stays one implementation with one set of tests.
"""

from __future__ import annotations

import os
import sys
import time

WINDOWS = os.name == "nt"

# Mouse tracking on, SGR extended coordinates. Emitted for both platforms: on
# Windows it needs virtual-terminal input, which _WindowsConsole tries to enable
# and works without.
MOUSE_ON = "\033[?1000;1006h"
MOUSE_OFF = "\033[?1000;1006l"


class _PosixConsole:
    def __init__(self, stream=None):
        self.stream = stream or sys.stdin
        self.fd = self.stream.fileno()
        self.saved = None

    def __enter__(self):
        import termios
        import tty

        try:
            self.saved = termios.tcgetattr(self.fd)
        except termios.error:
            # Not a terminal — a pipe under test, or output redirected. The
            # popup still paints; it just cannot read keys.
            self.saved = None
        if self.saved is not None:
            tty.setraw(self.fd)
        return self

    def __exit__(self, *exc):
        import termios

        if self.saved is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)
        return False

    def read(self, timeout: float) -> bytes | None:
        """Bytes if a key arrived, b"" on end of input, None on timeout."""
        import select

        ready, _, _ = select.select([self.fd], [], [], timeout)
        if not ready:
            return None
        return os.read(self.fd, 1024)


# What the Windows console sends for keys that POSIX terminals send escape
# sequences for. The prefix is \xe0 for most, \x00 for a few older ones.
_WINDOWS_KEYS = {
    b"H": b"\033[A",    # up
    b"P": b"\033[B",    # down
    b"K": b"\033[D",    # left
    b"M": b"\033[C",    # right
    b"I": b"\033[5~",   # page up
    b"Q": b"\033[6~",   # page down
    b"G": b"\033[H",    # home
    b"O": b"\033[F",    # end
}
_PREFIXES = (b"\xe0", b"\x00")

# SetConsoleMode flags. Named here rather than inlined because a wrong constant
# is silent: the call succeeds and the feature simply never works.
_ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
_ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200
_STD_INPUT = -10
_STD_OUTPUT = -11


class _WindowsConsole:
    """msvcrt for reading, and virtual-terminal mode for everything else.

    The console is already unbuffered for `getch`, so there is no raw mode to
    enter — but ANSI output and mouse reporting both need modes set explicitly,
    and both are best-effort: a console that refuses still shows text and still
    takes keys.
    """

    def __init__(self, stream=None):
        self.stream = stream or sys.stdin
        self.saved_in = None
        self.saved_out = None

    def _set_mode(self, handle_id: int, add: int):
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(handle_id)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return None
        previous = mode.value
        kernel32.SetConsoleMode(handle, previous | add)
        return previous

    def _restore_mode(self, handle_id: int, mode):
        if mode is None:
            return
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(handle_id), mode)

    def __enter__(self):
        try:
            self.saved_out = self._set_mode(
                _STD_OUTPUT, _ENABLE_VIRTUAL_TERMINAL_PROCESSING
            )
            self.saved_in = self._set_mode(_STD_INPUT, _ENABLE_VIRTUAL_TERMINAL_INPUT)
        except (OSError, AttributeError):
            pass
        return self

    def __exit__(self, *exc):
        try:
            self._restore_mode(_STD_INPUT, self.saved_in)
            self._restore_mode(_STD_OUTPUT, self.saved_out)
        except (OSError, AttributeError):
            pass
        return False

    def read(self, timeout: float) -> bytes | None:
        import msvcrt

        deadline = time.monotonic() + timeout
        while True:
            if msvcrt.kbhit():
                return self._drain(msvcrt)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            # Polled rather than blocked: the popup has to keep repainting for
            # the spinner and for streamed text, so a key is not the only reason
            # to wake up.
            time.sleep(min(0.01, remaining))

    def _drain(self, msvcrt) -> bytes:
        """Everything buffered, with special keys turned into ANSI."""
        out = b""
        while msvcrt.kbhit():
            char = msvcrt.getch()
            if char in _PREFIXES and msvcrt.kbhit():
                out += _WINDOWS_KEYS.get(msvcrt.getch(), b"")
            else:
                out += char
        return out


Console = _WindowsConsole if WINDOWS else _PosixConsole
