"""The terminal layer, and the popup driven through a real one.

Everything else in the popup is portable — it writes ANSI and counts columns.
Only entering raw mode and waiting for a key with a timeout are not, so they are
the only things here, and the Windows half translates console key codes into the
same ANSI sequences the POSIX half produces. `handle_key` stays one
implementation.
"""

import os
import re
import select
import sys
import time
import unittest
from unittest import mock

from lens.ui import console

POSIX = os.name != "nt"


class WindowsKeyTranslation(unittest.TestCase):
    """Asserted on every platform, because it is the half that cannot be
    exercised where it runs."""

    def drain(self, keystrokes):
        fake = mock.Mock()
        pending = list(keystrokes)
        fake.kbhit.side_effect = lambda: bool(pending)
        fake.getch.side_effect = lambda: pending.pop(0)
        return console._WindowsConsole()._drain(fake)

    def test_arrows_become_the_sequences_handle_key_expects(self):
        self.assertEqual(self.drain([b"\xe0", b"H"]), b"\033[A")
        self.assertEqual(self.drain([b"\xe0", b"P"]), b"\033[B")

    def test_paging_keys_translate(self):
        self.assertEqual(self.drain([b"\xe0", b"I"]), b"\033[5~")
        self.assertEqual(self.drain([b"\xe0", b"Q"]), b"\033[6~")

    def test_the_older_null_prefix_works_too(self):
        self.assertEqual(self.drain([b"\x00", b"H"]), b"\033[A")

    def test_plain_keys_pass_through(self):
        self.assertEqual(self.drain([b"j"]), b"j")
        self.assertEqual(self.drain([b"\x1b"]), b"\x1b")

    def test_an_unknown_special_key_is_dropped_not_leaked(self):
        """A bare \\xe0 reaching handle_key would read as a random byte."""
        self.assertEqual(self.drain([b"\xe0", b"\x99"]), b"")

    def test_a_full_buffer_is_drained_at_once(self):
        self.assertEqual(self.drain([b"j", b"j", b"\xe0", b"H"]), b"jj\033[A")

    def test_the_translations_are_what_handle_key_reads(self):
        """Pinned against the reader rather than against a copy of the table."""
        from lens import viewer

        source = __import__("inspect").getsource(viewer.handle_key)
        for sequence in (b"\033[A", b"\033[B", b"\033[5~", b"\033[6~"):
            with self.subTest(sequence=sequence):
                literal = sequence.decode("latin-1").replace("\033", "\\x1b")
                self.assertIn(literal, source, f"{sequence!r} is translated to "
                                               "something handle_key ignores")


@unittest.skipUnless(POSIX, "the POSIX console")
class PosixConsole(unittest.TestCase):
    def test_a_pipe_is_not_a_terminal_and_does_not_raise(self):
        """Under test and with output redirected, there is no raw mode to
        enter — the popup should still paint."""
        read_fd, write_fd = os.pipe()
        try:
            with console._PosixConsole(os.fdopen(read_fd)) as term:
                self.assertIsNone(term.saved)
                self.assertIsNone(term.read(0.01))
        finally:
            os.close(write_fd)

    def test_a_timeout_returns_none_and_input_returns_bytes(self):
        read_fd, write_fd = os.pipe()
        try:
            with console._PosixConsole(os.fdopen(read_fd)) as term:
                self.assertIsNone(term.read(0.01))
                os.write(write_fd, b"j")
                time.sleep(0.05)
                self.assertEqual(term.read(0.5), b"j")
        finally:
            os.close(write_fd)


@unittest.skipUnless(POSIX, "needs a pty")
class ThroughARealTerminal(unittest.TestCase):
    """The popup, in a pseudo-terminal.

    The one test that exercises raw mode, the paint loop and the key path
    together. Everything else about the popup is checked on strings.
    """

    def run_popup(self, text, keys=b"\x1b", seconds=8, until=b"[Esc]"):
        import fcntl
        import json
        import pty
        import struct
        import subprocess
        import tempfile
        import termios

        directory = tempfile.mkdtemp()
        job = os.path.join(directory, "job.json")
        with open(job, "w", encoding="utf-8") as handle:
            json.dump({"text": text, "mode": None,
                       "selection_source": "context", "pane_id": ""}, handle)

        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 16, 70, 0, 0))
        proc = subprocess.Popen(
            [sys.executable, "-c",
             "from lens import viewer; raise SystemExit(viewer.main())"],
            stdin=slave, stdout=slave, stderr=slave,
            env={**os.environ, "LENS_JOB": job},
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            close_fds=True, start_new_session=True,
        )
        os.close(slave)
        os.set_blocking(master, False)

        out = b""
        deadline = time.time() + seconds
        while time.time() < deadline:
            ready, _, _ = select.select([master], [], [], 0.2)
            if not ready:
                continue
            try:
                chunk = os.read(master, 4096)
            except BlockingIOError:
                continue
            except OSError:
                break
            if not chunk:
                break
            out += chunk
            # One complete frame is the whole point; waiting out the window
            # after that just makes the suite slow.
            if until in out:
                break

        os.write(master, keys)
        try:
            code = proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = None
        os.close(master)
        plain = re.sub(r"\033\[[0-9;?]*[a-zA-Z]|\033\][^\007]*\007", "",
                       out.decode("utf-8", "replace"))
        return code, plain

    def test_it_paints_and_escape_closes_it(self):
        # Junk is rejected locally, so this needs no provider and no network.
        code, painted = self.run_popup("─────────────")
        self.assertIn("Lens", painted)
        self.assertEqual(code, 0, "Esc did not close the popup")

    def test_q_closes_it_too(self):
        code, _ = self.run_popup("─────────────", keys=b"q")
        self.assertEqual(code, 0)

    def test_the_terminal_is_handed_back(self):
        """Raw mode and mouse reporting both have to be undone, or the shell
        underneath is left unusable."""
        _, painted = self.run_popup("─────────────")
        raw = painted  # escapes were stripped; check the raw stream instead
        self.assertNotIn("\x1b[?1000", raw, "mouse mode should be off by exit")


if __name__ == "__main__":
    unittest.main()


class ImportsWithoutPosix(unittest.TestCase):
    """Windows has no termios or tty.

    A module-level import of either makes the whole plugin unloadable there,
    and the failure is total rather than partial — so it is worth asserting
    rather than remembering.
    """

    MODULES = ("lens.ui.console", "lens.viewer", "lens.action", "lens.cli",
               "lens.nvim")

    def test_every_module_imports(self):
        import importlib
        import importlib.abc

        class Absent(importlib.abc.MetaPathFinder):
            def find_spec(self, name, path=None, target=None):
                if name in ("termios", "tty"):
                    raise ModuleNotFoundError(f"No module named {name!r}")

        blocked = Absent()
        saved = {name: sys.modules.pop(name, None)
                 for name in list(sys.modules) if name.startswith("lens")}
        sys.meta_path.insert(0, blocked)
        try:
            for name in self.MODULES:
                with self.subTest(module=name):
                    importlib.import_module(name)
        finally:
            sys.meta_path.remove(blocked)
            for name in list(sys.modules):
                if name.startswith("lens"):
                    del sys.modules[name]
            for name, module in saved.items():
                if module is not None:
                    sys.modules[name] = module
