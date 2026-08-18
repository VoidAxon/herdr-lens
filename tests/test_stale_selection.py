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


class Suppression(unittest.TestCase):
    """Refusing to open at all, when opening could only mislead.

    Two conditions, and both are needed. A program owning the mouse means a
    drag never reached Herdr; an unchanged clipboard means no other copy
    happened either. Together there is nothing new to answer about. Either one
    alone is normal: `"+y` in vim changes the clipboard, and re-translating the
    same text in a shell is a perfectly ordinary thing to do.
    """

    def invoke(self, *, owner, changed, source="clipboard"):
        import io
        import tempfile
        from pathlib import Path as P

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"HERDR_PLUGIN_STATE_DIR": tmp}):
                if not changed:
                    action.remember_selection("hello there")
                with mock.patch("lens.selection.acquire", return_value=mock.Mock(
                        text="hello there", source=source, backend="x")):
                    with mock.patch("lens.action.mouse_owner", return_value=owner):
                        with mock.patch("lens.action.notify") as notified:
                            with mock.patch("lens.action.open_popup",
                                            return_value=0) as opened:
                                with mock.patch("sys.stderr", new=io.StringIO()):
                                    code = action.main(["translate"])
                remembered = (P(tmp) / "last-selection").exists()
        return code, opened, notified, remembered

    def test_a_stale_clipboard_in_vim_does_not_open_a_popup(self):
        code, opened, notified, _ = self.invoke(owner="nvim", changed=False)
        self.assertEqual(code, 0)
        opened.assert_not_called()
        notified.assert_called_once()

    def test_the_notification_says_what_to_do_instead(self):
        _, _, notified, _ = self.invoke(owner="nvim", changed=False)
        body = " ".join(str(a) for a in notified.call_args[0])
        self.assertIn("nvim", body)
        self.assertIn('"+y', body)

    def test_a_yank_in_vim_still_opens(self):
        """`"+y` reaches the clipboard, so this keypress is correct."""
        _, opened, notified, _ = self.invoke(owner="nvim", changed=True)
        opened.assert_called_once()
        notified.assert_not_called()

    def test_repeating_the_same_selection_in_a_shell_still_opens(self):
        """Nothing owns the mouse, so an unchanged clipboard means the user
        genuinely wants that text again."""
        _, opened, _, _ = self.invoke(owner="", changed=False)
        opened.assert_called_once()

    def test_a_context_selection_is_never_suppressed(self):
        """Herdr handed us the text directly; the clipboard is irrelevant."""
        _, opened, _, _ = self.invoke(owner="nvim", changed=False, source="context")
        opened.assert_called_once()

    def test_the_keypress_is_never_silent(self):
        """Whatever happens, something appears — a popup or a notification."""
        for owner, changed in ((("nvim"), False), ("nvim", True), ("", False)):
            with self.subTest(owner=owner, changed=changed):
                _, opened, notified, _ = self.invoke(owner=owner, changed=changed)
                self.assertEqual(
                    opened.called or notified.called, True,
                    "the key did nothing visible",
                )

    def test_the_selection_itself_is_never_written_to_disk(self):
        """This file outlives a job on purpose, so it must not hold the text."""
        import tempfile
        from pathlib import Path as P

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"HERDR_PLUGIN_STATE_DIR": tmp}):
                action.remember_selection("a secret selection")
                stored = (P(tmp) / "last-selection").read_text()
        self.assertNotIn("secret", stored)
        self.assertEqual(len(stored), 64, "a sha256 digest, not the text")
