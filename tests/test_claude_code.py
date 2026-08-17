import io
import json
import time
import unittest
from unittest import mock

from lens.providers.base import ProviderError
from lens.providers.claude_code import DISALLOWED, ClaudeCodeProvider

TEXT = "By default, grep prints the matching lines."


def event(**kw):
    return json.dumps(kw) + "\n"


def deltas(*pieces):
    """The CLI's per-token stream events."""
    return [
        event(type="stream_event", event={"delta": {"type": "text_delta", "text": p}})
        for p in pieces
    ]


class FakeStdout(io.StringIO):
    """StringIO that can pause between lines, for exercising the watchdog."""

    def __init__(self, text, delay=0.0):
        super().__init__(text)
        self.delay = delay

    def __iter__(self):
        # StringIO.__iter__ returns self, so delegating to super() recurses.
        while True:
            line = self.readline()
            if not line:
                return
            if self.delay:
                time.sleep(self.delay)
            yield line


def fake_proc(lines, returncode=0, delay=0.0):
    proc = mock.Mock()
    proc.stdout = FakeStdout("".join(lines), delay)
    proc.poll.return_value = returncode
    proc.returncode = returncode
    proc.wait.return_value = returncode
    return proc


def run_with(lines=None, returncode=0, on_chunk=None, delay=0.0, timeout=None):
    """Drive the provider against a scripted CLI transcript."""
    if lines is None:
        lines = deltas("默认情况下，") + [event(type="result", result="默认情况下，grep 会打印匹配的行。")]
    provider = ClaudeCodeProvider(model="sonnet")
    if timeout is not None:
        provider.timeout = timeout
    proc = fake_proc(lines, returncode, delay)
    with mock.patch("lens.providers.claude_code.shutil.which", return_value="/bin/claude"):
        with mock.patch("subprocess.Popen", return_value=proc) as popen:
            try:
                return popen, provider.translate(TEXT, "auto", "zh-CN", "Translate.", on_chunk), None
            except ProviderError as exc:
                return popen, None, exc


class Argv(unittest.TestCase):
    def argv(self):
        popen, _, _ = run_with()
        return popen.call_args[0][0]

    def test_the_variadic_tool_flag_is_never_next_to_the_prompt(self):
        """`--disallowedTools` takes a list; if the selection follows it, the
        CLI reads the selection as tool names and the call fails outright."""
        argv = self.argv()
        after_tools = argv[argv.index(DISALLOWED) + 1]
        self.assertTrue(after_tools.startswith("--"), f"got {after_tools!r}")

    def test_the_selection_is_the_final_argument(self):
        self.assertIn(TEXT, self.argv()[-1])

    def test_streaming_is_requested(self):
        argv = self.argv()
        self.assertIn("--output-format", argv)
        self.assertEqual(argv[argv.index("--output-format") + 1], "stream-json")
        self.assertIn("--include-partial-messages", argv)
        # Without --verbose the CLI emits only a final result, never deltas.
        self.assertIn("--verbose", argv)

    def test_the_session_is_not_written_to_disk(self):
        self.assertIn("--no-session-persistence", self.argv())

    def test_the_selection_is_delimited_as_data(self):
        prompt = self.argv()[-1]
        self.assertIn("<<<TERMINAL_TEXT", prompt)
        self.assertIn("TERMINAL_TEXT>>>", prompt)

    def test_only_the_selection_sits_inside_the_delimiters(self):
        prompt = self.argv()[-1]
        body = prompt.split("<<<TERMINAL_TEXT\n")[1].split("\nTERMINAL_TEXT>>>")[0]
        self.assertEqual(body, TEXT)
        self.assertNotIn("Target language", prompt)

    def test_the_system_prompt_says_it_is_not_an_assistant(self):
        argv = self.argv()
        system = argv[argv.index("--system-prompt") + 1]
        self.assertIn("translation engine, not an assistant", system)
        self.assertIn("Translate.", system)

    def test_the_target_language_is_authoritative_from_the_argument(self):
        argv = self.argv()
        system = argv[argv.index("--system-prompt") + 1]
        self.assertIn("Translate into zh-CN", system)

    def test_an_explicit_source_language_goes_to_the_system_prompt(self):
        provider = ClaudeCodeProvider(model="sonnet")
        proc = fake_proc([event(type="result", result="ok")])
        with mock.patch("lens.providers.claude_code.shutil.which", return_value="/bin/claude"):
            with mock.patch("subprocess.Popen", return_value=proc) as popen:
                provider.translate(TEXT, "en", "zh-CN", "Translate.")
        argv = popen.call_args[0][0]
        system = argv[argv.index("--system-prompt") + 1]
        self.assertIn("source language is en", system)
        self.assertNotIn("source language", argv[-1])


class Streaming(unittest.TestCase):
    def test_partial_text_is_delivered_as_it_arrives(self):
        seen = []
        run_with(
            lines=deltas("默认", "情况下，", "grep 会打印匹配的行。")
            + [event(type="result", result="默认情况下，grep 会打印匹配的行。")],
            on_chunk=seen.append,
        )
        self.assertEqual(len(seen), 3)
        self.assertEqual(seen[0], "默认")
        self.assertEqual(seen[-1], "默认情况下，grep 会打印匹配的行。")

    def test_each_chunk_carries_everything_so_far(self):
        """The callback is a whole-text update, not an append — the viewer
        assigns it straight to the rendered body."""
        seen = []
        run_with(lines=deltas("a", "b", "c") + [event(type="result", result="abc")],
                 on_chunk=seen.append)
        self.assertEqual(seen, ["a", "ab", "abc"])

    def test_the_result_event_is_authoritative(self):
        _, out, _ = run_with(
            lines=deltas("partial") + [event(type="result", result="the whole answer")]
        )
        self.assertEqual(out, "the whole answer")

    def test_deltas_are_used_when_the_result_is_empty(self):
        _, out, _ = run_with(lines=deltas("从", "增量", "拼出") + [event(type="result", result="")])
        self.assertEqual(out, "从增量拼出")

    def test_a_missing_callback_is_fine(self):
        _, out, _ = run_with(on_chunk=None)
        self.assertTrue(out)

    def test_unparseable_lines_are_skipped(self):
        _, out, _ = run_with(
            lines=["not json\n", "\n"] + deltas("ok") + [event(type="result", result="ok")]
        )
        self.assertEqual(out, "ok")

    def test_output_is_stripped(self):
        _, out, _ = run_with(lines=[event(type="result", result="  译文  \n")])
        self.assertEqual(out, "译文")


class Failures(unittest.TestCase):
    def test_a_transcript_with_no_result_is_an_error(self):
        _, _, exc = run_with(lines=deltas("half an answer"), returncode=1)
        self.assertIn("returned an error", exc.message)

    def test_a_missing_cli_is_named(self):
        provider = ClaudeCodeProvider(model="sonnet")
        with mock.patch("lens.providers.claude_code.shutil.which", return_value=None):
            with self.assertRaises(ProviderError) as ctx:
                provider.translate(TEXT, "auto", "zh-CN", "Translate.")
        self.assertIn("claude", ctx.exception.message)

    def test_a_stall_is_killed_and_reported(self):
        _, _, exc = run_with(
            lines=deltas("slow") * 20 + [event(type="result", result="never gets here")],
            delay=0.05,
            timeout=0.01,
        )
        self.assertIsNotNone(exc)
        self.assertIn("too long", exc.message)

    def test_it_gets_more_headroom_than_the_http_providers(self):
        from lens.providers.base import TIMEOUT

        self.assertGreater(ClaudeCodeProvider.timeout, TIMEOUT)



class ArgvSafety(unittest.TestCase):
    """The selection is a positional argument, so it must never be readable as
    a flag — `--dangerously-skip-permissions` pasted into a pane would
    otherwise become one."""

    def argv_for(self, text):
        # `which` is patched because the provider refuses before it builds argv
        # when the CLI is absent. Without this the test passes only on a machine
        # that happens to have `claude` installed — which is not a test.
        with mock.patch("lens.providers.claude_code.shutil.which",
                        return_value="/bin/claude"):
            with mock.patch.object(
                ClaudeCodeProvider, "_stream", return_value="ok"
            ) as run:
                ClaudeCodeProvider(model="sonnet").translate(
                    text, "auto", "Chinese (Simplified)", "PROMPT"
                )
        return run.call_args[0][0]

    def test_a_selection_that_looks_like_a_flag_stays_a_positional(self):
        argv = self.argv_for("--dangerously-skip-permissions")
        self.assertFalse(
            argv[-1].startswith("-"),
            "the prompt argument must not begin with a dash",
        )

    def test_the_selection_is_wrapped_in_delimiters(self):
        argv = self.argv_for("--help")
        self.assertTrue(argv[-1].startswith("<<<"))
        self.assertIn("--help", argv[-1])


if __name__ == "__main__":
    unittest.main()
