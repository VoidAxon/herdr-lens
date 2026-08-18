"""The standalone command.

The plugin needs Herdr because a keybinding cannot open a popup by itself. The
translation never did — so these tests pin the parts a pipe cares about and a
popup does not: where the text comes from, what reaches stdout, and what the
exit code says.
"""

import io
import unittest
from unittest import mock

from lens import cli, mode as modes


class Parsing(unittest.TestCase):
    def test_bare_text_forces_no_mode(self):
        self.assertEqual(cli.parse(["hello", "world"]), (None, ["hello", "world"], ""))

    def test_mode_flags(self):
        self.assertEqual(cli.parse(["--explain", "x"])[0], modes.EXPLAIN)
        self.assertEqual(cli.parse(["--summarize", "x"])[0], modes.SUMMARIZE)

    def test_both_spellings_of_summarise(self):
        self.assertEqual(cli.parse(["--summarise", "x"])[0], modes.SUMMARIZE)

    def test_target_override_in_either_form(self):
        self.assertEqual(cli.parse(["-t", "ja", "x"])[2], "ja")
        self.assertEqual(cli.parse(["--target=ja", "x"])[2], "ja")

    def test_a_flag_does_not_swallow_the_text(self):
        forced, words, target = cli.parse(["--target", "ja", "hello", "world"])
        self.assertEqual(words, ["hello", "world"])
        self.assertEqual(target, "ja")

    def test_a_dangling_target_does_not_crash(self):
        self.assertEqual(cli.parse(["--target"]), (None, [], ""))


class Input(unittest.TestCase):
    def test_arguments_win_over_stdin(self):
        """`lens --summarize "$(cmd)"` must not also wait on a pipe."""
        with mock.patch("sys.stdin", io.StringIO("piped")):
            self.assertEqual(cli.read_text(["argued"]), "argued")

    def test_stdin_is_read_when_piped(self):
        stdin = io.StringIO("piped text")
        stdin.isatty = lambda: False
        with mock.patch("sys.stdin", stdin):
            self.assertEqual(cli.read_text([]), "piped text")

    def test_a_bare_invocation_on_a_terminal_does_not_hang(self):
        stdin = io.StringIO("")
        stdin.isatty = lambda: True
        with mock.patch("sys.stdin", stdin):
            self.assertEqual(cli.read_text([]), "")


class Running(unittest.TestCase):
    def run_cli(self, argv, reply="译文", tty=False, error=None):
        out, err = io.StringIO(), io.StringIO()
        out.isatty = lambda: tty
        provider = mock.Mock(model="m")
        provider.name = "P"
        if error:
            provider.translate.side_effect = error
        else:
            provider.translate.return_value = reply
        with mock.patch("lens.cli.build", return_value=provider):
            with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
                code = cli.main(argv)
        return code, out.getvalue(), err.getvalue(), provider

    def test_the_result_goes_to_stdout(self):
        code, out, err, _ = self.run_cli(["hello there"])
        self.assertEqual(code, 0)
        self.assertEqual(out, "译文\n")
        self.assertEqual(err, "")

    def test_a_pipe_gets_the_finished_value_and_no_stream(self):
        """Partial output down a pipe can be mistaken for the whole value."""
        _, _, _, provider = self.run_cli(["hello there"], tty=False)
        self.assertIsNone(provider.translate.call_args.kwargs["on_chunk"])

    def test_a_terminal_gets_the_stream(self):
        _, _, _, provider = self.run_cli(["hello there"], tty=True)
        self.assertIsNotNone(provider.translate.call_args.kwargs["on_chunk"])

    def test_junk_fails_without_calling_a_provider(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("lens.cli.build") as build:
            with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
                code = cli.main(["─────────────"])
        self.assertEqual(code, 1)
        build.assert_not_called()
        self.assertIn("nothing translatable", err.getvalue())

    def test_no_input_is_a_usage_error_not_a_failure(self):
        stdin = io.StringIO("")
        stdin.isatty = lambda: True
        with mock.patch("sys.stdin", stdin):
            with mock.patch("sys.stderr", io.StringIO()) as err:
                self.assertEqual(cli.main([]), 2)
        self.assertIn("nothing to read", err.getvalue())

    def test_a_provider_error_is_reported_on_stderr(self):
        from lens.providers import ProviderError

        code, out, err, _ = self.run_cli(
            ["hello there"], error=ProviderError("boom", "try this")
        )
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("boom", err)
        self.assertIn("try this", err)

    def test_help_exits_cleanly(self):
        with mock.patch("sys.stdout", new=io.StringIO()) as out:
            self.assertEqual(cli.main(["--help"]), 0)
        self.assertIn("lens", out.getvalue())

    def test_control_sequences_are_stripped_from_the_reply(self):
        """stdout is a real terminal too — the popup is not the only surface
        that can be hijacked."""
        code, out, _, _ = self.run_cli(["hello there"], reply="ok\x1b]52;c;eA==\x07")
        self.assertNotIn("\x1b", out)

    def test_control_sequences_are_stripped_from_the_input(self):
        _, _, _, provider = self.run_cli(["hello \x1b]0;X\x07 there"])
        self.assertNotIn("\x1b", provider.translate.call_args[0][0])

    def test_a_forced_mode_reaches_the_prompt(self):
        _, _, _, provider = self.run_cli(["--summarize", "a long log line here"])
        self.assertIn("Summarise", provider.translate.call_args[0][3])


if __name__ == "__main__":
    unittest.main()
