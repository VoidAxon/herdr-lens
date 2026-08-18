"""Reading the visual selection out of a running Neovim.

The reason this exists: inside Neovim the drag belongs to Neovim, so Herdr never
sees a selection and the clipboard holds something older. Neovim knows, and is
already listening.
"""

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from lens import nvim

HAVE_NVIM = shutil.which("nvim") is not None


class SocketDiscovery(unittest.TestCase):
    def test_a_socket_is_found_by_pid(self):
        with tempfile.TemporaryDirectory() as tmp:
            sock = Path(tmp) / "nvim.4242.0"
            sock.touch()
            with mock.patch.object(Path, "is_socket", return_value=True):
                self.assertEqual(
                    nvim.socket_for(4242, {"XDG_RUNTIME_DIR": tmp}), str(sock)
                )

    def test_the_trailing_index_is_not_assumed_to_be_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "nvim.4242.7").touch()
            with mock.patch.object(Path, "is_socket", return_value=True):
                self.assertTrue(
                    nvim.socket_for(4242, {"XDG_RUNTIME_DIR": tmp}).endswith(".7")
                )

    def test_another_instances_socket_is_not_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "nvim.9999.0").touch()
            with mock.patch.object(Path, "is_socket", return_value=True):
                self.assertEqual(nvim.socket_for(4242, {"XDG_RUNTIME_DIR": tmp}), "")

    def test_a_plain_file_is_not_a_socket(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "nvim.4242.0").touch()
            self.assertEqual(nvim.socket_for(4242, {"XDG_RUNTIME_DIR": tmp}), "")

    def test_a_missing_runtime_dir_is_not_an_error(self):
        self.assertEqual(
            nvim.socket_for(4242, {"XDG_RUNTIME_DIR": "/nonexistent/xyz"}), ""
        )


class ForkedProcess(unittest.TestCase):
    """An interactive Neovim forks, and the socket is named after the child.

    This is the bug that shipped. A `--headless` Neovim does not fork, so the
    test setup matched the pid the socket was named after and the code looked
    correct. Against a real one:

        herdr reports    584324
        socket is        nvim.584325.0
        584325's parent  584324

    The test environment was simpler than the thing it stood for, which is the
    failure mode a mock cannot warn you about.
    """

    def test_a_child_pids_socket_is_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "nvim.584325.0").touch()
            with mock.patch.object(nvim, "_descendants",
                                   return_value=[584324, 584325]):
                with mock.patch.object(Path, "is_socket", return_value=True):
                    found = nvim.socket_for(584324, {"XDG_RUNTIME_DIR": tmp})
        self.assertTrue(found.endswith("nvim.584325.0"), found)

    def test_the_parents_own_socket_still_wins_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "nvim.100.0").touch()
            (Path(tmp) / "nvim.101.0").touch()
            with mock.patch.object(nvim, "_descendants", return_value=[100, 101]):
                with mock.patch.object(Path, "is_socket", return_value=True):
                    found = nvim.socket_for(100, {"XDG_RUNTIME_DIR": tmp})
        self.assertTrue(found.endswith("nvim.100.0"), found)

    def spawn_with_child(self):
        return subprocess.Popen(
            ["bash", "-c", "sleep 5 & wait"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def test_descendants_reads_a_real_process_tree(self):
        """Read from the operating system, so it is worth checking against a
        real one rather than a stubbed /proc."""
        parent = self.spawn_with_child()
        try:
            time.sleep(0.6)
            found = nvim._descendants(parent.pid)
            self.assertIn(parent.pid, found)
            self.assertGreater(len(found), 1, "the child was not found")
        finally:
            parent.kill()
            parent.wait(timeout=5)

    def test_it_works_without_proc(self):
        """macOS has no /proc, and this plugin claims to support macOS. Without
        a fallback the Neovim path fails there exactly as it failed on Linux
        before the fork was accounted for."""
        parent = self.spawn_with_child()
        try:
            time.sleep(0.6)
            with mock.patch.object(Path, "iterdir", side_effect=OSError("no /proc")):
                found = nvim._children(parent.pid)
            self.assertTrue(found, "pgrep fallback found nothing")
        finally:
            parent.kill()
            parent.wait(timeout=5)

    def test_a_dead_pid_is_quiet(self):
        """`iterdir` is a generator, so a missing directory raises while being
        iterated, not when it is called — the try has to wrap the loop."""
        self.assertEqual(nvim._descendants(999999), [999999])
        self.assertEqual(nvim._children(999999), [])

    def test_a_cycle_cannot_loop_forever(self):
        with mock.patch.object(nvim, "_children", side_effect=lambda p: [p]):
            self.assertEqual(nvim._descendants(7), [7])


class Expression(unittest.TestCase):
    def test_it_uses_the_live_positions_not_the_marks(self):
        """`'<` and `'>` are written when visual mode ends, so they read as
        zeros at exactly the moment we ask."""
        self.assertIn('getpos("v")', nvim.EXPRESSION)
        self.assertIn('getpos(".")', nvim.EXPRESSION)
        self.assertNotIn("'<", nvim.EXPRESSION)

    def test_it_asks_for_nothing_outside_visual_mode(self):
        """In normal mode getregion() would return the line under the cursor,
        which the user did not select."""
        self.assertIn("mode()", nvim.EXPRESSION)

    def test_it_does_not_yank(self):
        """The common advice is to send `y` and read `"0`. That costs the user
        their register and their visual mode; this is a pure read."""
        for destructive in ("remote-send", "normal", "yank", "@0", 'setreg'):
            self.assertNotIn(destructive, nvim.EXPRESSION)


class Failures(unittest.TestCase):
    """Every uninteresting case has to look the same to the caller."""

    def test_no_socket_returns_empty(self):
        with mock.patch.object(nvim, "socket_for", return_value=""):
            self.assertEqual(nvim.visual_selection(1), "")

    def test_no_client_returns_empty(self):
        with mock.patch.object(nvim, "socket_for", return_value="/tmp/s"):
            with mock.patch.object(nvim, "_client", return_value=""):
                self.assertEqual(nvim.visual_selection(1), "")

    def test_a_timeout_returns_empty(self):
        with mock.patch.object(nvim, "socket_for", return_value="/tmp/s"):
            with mock.patch.object(nvim, "_client", return_value="/bin/nvim"):
                with mock.patch("subprocess.run",
                                side_effect=subprocess.TimeoutExpired("nvim", 2)):
                    self.assertEqual(nvim.visual_selection(1), "")

    def test_a_nonzero_exit_returns_empty(self):
        """An older Neovim without getregion() fails this way."""
        with mock.patch.object(nvim, "socket_for", return_value="/tmp/s"):
            with mock.patch.object(nvim, "_client", return_value="/bin/nvim"):
                with mock.patch("subprocess.run",
                                return_value=mock.Mock(returncode=1, stdout=b"")):
                    self.assertEqual(nvim.visual_selection(1), "")

    def test_the_client_comes_from_the_running_process(self):
        """Herdr's PATH need not contain the nvim the user actually launched."""
        with mock.patch("os.readlink", return_value="/opt/nvim/bin/nvim"):
            with mock.patch.object(Path, "exists", return_value=True):
                self.assertEqual(nvim._client(1), "/opt/nvim/bin/nvim")


@unittest.skipUnless(HAVE_NVIM, "needs a real nvim on PATH")
class Live(unittest.TestCase):
    """Against a real Neovim, because the whole feature is one interaction with
    a program this code does not contain."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.file = Path(cls.tmp) / "sample.txt"
        cls.file.write_text("alpha beta gamma\nsecond line here\n", encoding="utf-8")
        cls.sock = str(Path(cls.tmp) / "nvim.sock")
        cls.proc = subprocess.Popen(
            ["nvim", "--headless", "--listen", cls.sock, str(cls.file)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
        for _ in range(60):
            if os.path.exists(cls.sock):
                break
            time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()

    def send(self, keys):
        subprocess.run(["nvim", "--server", self.sock, "--remote-send", keys],
                       capture_output=True, timeout=5, check=False)
        time.sleep(0.3)

    def read(self):
        with mock.patch.object(nvim, "socket_for", return_value=self.sock):
            return nvim.visual_selection(self.proc.pid)

    def test_a_character_selection(self):
        self.send("<Esc>gg0wve")
        self.assertEqual(self.read(), "beta")

    def test_a_line_selection_spans_lines(self):
        self.send("<Esc>ggVj")
        self.assertEqual(self.read(), "alpha beta gamma\nsecond line here")

    def test_normal_mode_yields_nothing(self):
        self.send("<Esc>gg")
        self.assertEqual(self.read(), "")

    def test_reading_leaves_visual_mode_intact(self):
        """The user is mid-selection; being dropped out of it would be worse
        than no answer."""
        self.send("<Esc>gg0wve")
        self.read()
        mode = subprocess.run(
            ["nvim", "--server", self.sock, "--remote-expr", "mode()"],
            capture_output=True, timeout=5, check=False,
        ).stdout.decode().strip()
        self.assertTrue(mode.startswith("v"), f"mode became {mode!r}")

    def test_reading_does_not_touch_the_yank_register(self):
        self.send('<Esc>ggyy')
        before = subprocess.run(
            ["nvim", "--server", self.sock, "--remote-expr", 'getreg("0")'],
            capture_output=True, timeout=5, check=False).stdout
        self.send("<Esc>gg0wve")
        self.read()
        after = subprocess.run(
            ["nvim", "--server", self.sock, "--remote-expr", 'getreg("0")'],
            capture_output=True, timeout=5, check=False).stdout
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
