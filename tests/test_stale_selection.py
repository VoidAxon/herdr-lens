"""Selecting inside a program that owns the mouse.

Reported as "the popup opens in vim but cannot handle it". The truth was worse
than a failure: nvim keeps the mouse by default (`mouse=a`), so a drag never
reaches Herdr, `copy_on_select` never fires, and the clipboard still holds
whatever was copied earlier. The popup then answers about *that*, confidently
and about the wrong text.

Nothing can recover the selection — but the one fact the reader cannot see for
themselves is that a drag in this pane never arrived, so say it.
"""

import unittest
from unittest import mock

from lens import action, viewer


class Detection(unittest.TestCase):
    def api(self, name):
        return {"result": {"process_info": {"foreground_processes": [{"name": name}]}}}

    def test_an_editor_is_reported(self):
        for name in ("vim", "nvim", "helix", "emacs"):
            with self.subTest(name=name):
                with mock.patch("lens.action.api", return_value=self.api(name)):
                    self.assertEqual(action.mouse_owner("w1:p1"), name)

    def test_a_pager_is_reported(self):
        with mock.patch("lens.action.api", return_value=self.api("less")):
            self.assertEqual(action.mouse_owner("w1:p1"), "less")

    def test_a_shell_is_not(self):
        for name in ("bash", "zsh", "fish"):
            with self.subTest(name=name):
                with mock.patch("lens.action.api", return_value=self.api(name)):
                    self.assertEqual(action.mouse_owner("w1:p1"), "")

    def test_an_agent_cli_is_not(self):
        """Selection keeps working in these, so warning would be noise — a TUI
        does not necessarily put the terminal in mouse-reporting mode."""
        for name in ("claude", "codex", "gemini"):
            with self.subTest(name=name):
                with mock.patch("lens.action.api", return_value=self.api(name)):
                    self.assertEqual(action.mouse_owner("w1:p1"), "")

    def test_no_pane_id_asks_nothing(self):
        with mock.patch("lens.action.api") as api:
            self.assertEqual(action.mouse_owner(""), "")
        api.assert_not_called()

    def test_an_unreachable_socket_is_not_an_error(self):
        """A diagnosis that fails must not break the translation it annotates."""
        with mock.patch("lens.action.api", return_value=None):
            self.assertEqual(action.mouse_owner("w1:p1"), "")

    def test_a_malformed_reply_is_not_an_error(self):
        for reply in ({}, {"result": {}}, {"result": {"process_info": {}}},
                      {"result": {"process_info": {"foreground_processes": []}}}):
            with self.subTest(reply=reply):
                with mock.patch("lens.action.api", return_value=reply):
                    self.assertEqual(action.mouse_owner("w1:p1"), "")


class Surfacing(unittest.TestCase):
    def run_worker(self, source, owner):
        state = viewer.State()
        provider = mock.Mock(model="m")
        provider.name = "P"
        provider.translate.return_value = "译文"
        cfg = mock.Mock(target_language="zh-CN", source_language="auto",
                        word_lookup=True, word_ai=None)
        cfg.rendered_prompt.return_value = "PROMPT"
        with mock.patch("lens.viewer.build", return_value=provider):
            with mock.patch("lens.action.mouse_owner", return_value=owner):
                viewer.translate(
                    {"text": "hello there", "mode": None,
                     "selection_source": source, "pane_id": "w1:p1"},
                    state, cfg,
                )
        return state

    def test_the_warning_reaches_the_status_line(self):
        state = self.run_worker("clipboard", "nvim")
        self.assertEqual(state.stale_risk, "nvim")
        frame = viewer.compose(state, 60, 20)
        self.assertIn("nvim has the mouse", frame)

    def test_the_warning_survives_a_narrow_popup(self):
        """It replaces the provider rather than joining it: at 56 columns both
        do not fit, and the provider is the half the reader can infer."""
        state = self.run_worker("clipboard", "nvim")
        for width in (40, 56, 98):
            with self.subTest(width=width):
                head = viewer.compose(state, width, 10).split("\r\n")[0]
                self.assertIn("nvim", head, f"lost the warning at {width} columns")

    def test_the_provider_is_still_shown_when_there_is_no_warning(self):
        state = self.run_worker("clipboard", "")
        self.assertIn("P", viewer.compose(state, 60, 20))

    def test_a_free_pane_says_nothing(self):
        state = self.run_worker("clipboard", "")
        self.assertEqual(state.stale_risk, "")
        self.assertNotIn("has the mouse", viewer.compose(state, 60, 20))

    def test_a_context_selection_is_never_questioned(self):
        """Herdr handed us the text directly, so the clipboard is irrelevant."""
        state = self.run_worker("context", "nvim")
        self.assertEqual(state.stale_risk, "")

    def test_the_translation_still_happens(self):
        """The warning annotates a result; it must not replace one."""
        state = self.run_worker("clipboard", "nvim")
        self.assertEqual(state.text, "译文")
        self.assertIsNone(state.error)


class EmptySelectionMessage(unittest.TestCase):
    def test_it_names_the_full_screen_case(self):
        """It used to blame copy_on_select, which is not the cause in vim."""
        state = viewer.State()
        viewer.prepare({"text": "   ", "mode": None}, state)
        hint = state.error[1]
        self.assertIn("vim", hint)
        self.assertIn('"+y', hint)


if __name__ == "__main__":
    unittest.main()
