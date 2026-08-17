"""Only the selected text may leave the machine.

Herdr hands the plugin a context object carrying the workspace cwd, the focused
pane's cwd, and the agent's status. None of that is the user's selection, and
none of it may end up in an outbound request body.
"""

import io
import json
import subprocess
import unittest
from unittest import mock

from lens import selection
from lens.providers import REGISTRY

SELECTION = "By default, grep prints the matching lines."

LEAKY_CONTEXT = {
    "workspace_id": "w2",
    "workspace_label": "~",
    "workspace_cwd": "/home/pasys/secret-project",
    "tab_id": "w2:t2",
    "focused_pane_id": "w2:p2",
    "focused_pane_cwd": "/home/pasys/secret-project/src",
    "focused_pane_agent": "claude",
    "focused_pane_status": "working",
    "invocation_source": "keybinding",
    "correlation_id": "keybinding",
    "selected_text": SELECTION,
}

SECRETS = [
    "secret-project",
    "w2:p2",
    "workspace_cwd",
    "focused_pane_agent",
    "correlation_id",
    "keybinding",
]


class Capture:
    def __init__(self):
        self.body = None

    def __call__(self, url, body, headers):
        self.body = body
        return {
            "content": [{"type": "text", "text": "ok"}],
            "choices": [{"message": {"content": "ok"}}],
            "message": {"content": "ok"},
        }


class OutboundBody(unittest.TestCase):
    """Whatever leaves the process — an HTTP body or a CLI argv — carries the
    selection and none of the context Herdr handed us alongside it."""

    def assert_clean(self, name, payload):
        self.assertIn(SELECTION, payload)
        for secret in SECRETS:
            self.assertNotIn(secret, payload, f"{name} leaked {secret!r}")

    def test_http_providers_send_the_selection_and_nothing_else(self):
        for name, cls in REGISTRY.items():
            if name == "claude-code":
                continue  # not HTTP; covered below
            with self.subTest(provider=name):
                provider = cls(model="m", api_key_env=None)
                capture = Capture()
                provider._post = capture

                provider.translate(SELECTION, "auto", "zh-CN", "Translate.")

                self.assert_clean(name, json.dumps(capture.body, ensure_ascii=False))

    def cli_call(self):
        proc = mock.Mock()
        proc.stdout = io.StringIO(json.dumps({"type": "result", "result": "ok"}) + "\n")
        proc.poll.return_value = 0
        proc.returncode = 0
        provider = REGISTRY["claude-code"](model="sonnet")
        with mock.patch("lens.providers.claude_code.shutil.which", return_value="/bin/claude"):
            with mock.patch("subprocess.Popen", return_value=proc) as popen:
                provider.translate(SELECTION, "auto", "zh-CN", "Translate.")
        return popen

    def test_the_claude_code_cli_is_handed_the_selection_and_nothing_else(self):
        self.assert_clean("claude-code", "\n".join(self.cli_call().call_args[0][0]))

    def test_the_cli_runs_outside_the_users_project(self):
        # Running in the project directory would pull that project's CLAUDE.md
        # into a translation request.
        kwargs = self.cli_call().call_args.kwargs
        self.assertNotIn("workspace", kwargs["cwd"])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)


class SelectionAcquisition(unittest.TestCase):
    class FakeClipboard:
        def __init__(self, text=""):
            self.text = text
            self.calls = 0

        def read(self, env=None):
            self.calls += 1
            return self.text, "fake"

    def test_context_selected_text_is_preferred(self):
        env = {"HERDR_PLUGIN_CONTEXT_JSON": json.dumps(LEAKY_CONTEXT)}
        clip = self.FakeClipboard("clipboard contents")
        sel = selection.acquire(env=env, clipboard=clip)
        self.assertEqual(sel.text, SELECTION)
        self.assertEqual(sel.source, "context")
        self.assertEqual(clip.calls, 0, "clipboard should not be read when context has the text")

    def test_clipboard_is_the_fallback(self):
        context = dict(LEAKY_CONTEXT)
        del context["selected_text"]
        env = {"HERDR_PLUGIN_CONTEXT_JSON": json.dumps(context)}
        sel = selection.acquire(env=env, clipboard=self.FakeClipboard(SELECTION))
        self.assertEqual(sel.text, SELECTION)
        self.assertEqual(sel.source, "clipboard")
        self.assertEqual(sel.backend, "fake")

    def test_blank_context_and_blank_clipboard_yield_nothing(self):
        sel = selection.acquire(env={}, clipboard=self.FakeClipboard("   \n"))
        self.assertEqual(sel.text, "")
        self.assertEqual(sel.source, "none")

    def test_whitespace_only_context_falls_through_to_clipboard(self):
        env = {"HERDR_PLUGIN_CONTEXT_JSON": json.dumps({"selected_text": "  \n "})}
        sel = selection.acquire(env=env, clipboard=self.FakeClipboard(SELECTION))
        self.assertEqual(sel.source, "clipboard")

    def test_malformed_context_json_does_not_crash(self):
        env = {"HERDR_PLUGIN_CONTEXT_JSON": "{not json"}
        sel = selection.acquire(env=env, clipboard=self.FakeClipboard(SELECTION))
        self.assertEqual(sel.source, "clipboard")


if __name__ == "__main__":
    unittest.main()


class ClickedText(unittest.TestCase):
    """A link handler hands the clicked text over directly — no clipboard."""

    class NeverCalled:
        def read(self, env=None):
            raise AssertionError("the clipboard must not be read for a click")

    def test_clicked_url_wins_over_everything(self):
        env = {"HERDR_PLUGIN_CONTEXT_JSON": json.dumps(
            {**LEAKY_CONTEXT, "clicked_url": "verbose", "invocation_source": "link_click"}
        )}
        sel = selection.acquire(env=env, clipboard=self.NeverCalled())
        self.assertEqual(sel.text, "verbose")
        self.assertEqual(sel.source, "click")

    def test_the_env_var_form_is_accepted_too(self):
        env = {"HERDR_PLUGIN_CLICKED_URL": "SIGTERM"}
        sel = selection.acquire(env=env, clipboard=self.NeverCalled())
        self.assertEqual(sel.text, "SIGTERM")

    def test_a_blank_click_falls_through(self):
        env = {"HERDR_PLUGIN_CONTEXT_JSON": json.dumps({"clicked_url": "  "})}

        class Clip:
            def read(self, env=None):
                return "from clipboard", "fake"

        sel = selection.acquire(env=env, clipboard=Clip())
        self.assertEqual(sel.source, "clipboard")
