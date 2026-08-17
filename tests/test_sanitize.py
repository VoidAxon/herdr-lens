"""Control sequences arriving in content.

The selection is embedded in a prompt that tells the model to reproduce code
verbatim, and the reply is written to the user's real terminal. That is a
complete path from "text someone else wrote" to "bytes my terminal executes",
so it has to be closed at both ends.
"""

import unittest

from lens import viewer
from lens.ui import frame, style


class Sanitize(unittest.TestCase):
    def test_osc_52_cannot_reach_the_clipboard(self):
        """The worst of them: OSC 52 writes the system clipboard, so a crafted
        selection could plant a command for the user to paste."""
        evil = "hello \x1b]52;c;bWFsaWNpb3Vz\x07 world"
        self.assertEqual(frame.sanitize(evil), "hello  world")

    def test_the_window_title_cannot_be_rewritten(self):
        self.assertEqual(frame.sanitize("a\x1b]0;HIJACKED\x07b"), "ab")

    def test_a_hard_reset_is_removed(self):
        # ESC c resets the terminal outright.
        self.assertEqual(frame.sanitize("a\x1bcb"), "ab")

    def test_screen_clearing_and_cursor_moves_are_removed(self):
        self.assertEqual(frame.sanitize("a\x1b[2J\x1b[Hb"), "ab")

    def test_colour_codes_are_removed(self):
        self.assertEqual(frame.sanitize("\x1b[31mred\x1b[0m"), "red")

    def test_dcs_strings_are_removed(self):
        self.assertEqual(frame.sanitize("a\x1bPq#0;2;0;0;0\x1b\\b"), "ab")

    def test_stray_control_bytes_go_too(self):
        self.assertEqual(frame.sanitize("a\x00\x07\x08b\x7f"), "ab")

    def test_newlines_survive_because_layout_needs_them(self):
        self.assertEqual(frame.sanitize("a\nb"), "a\nb")

    def test_carriage_returns_become_newlines(self):
        self.assertEqual(frame.sanitize("a\r\nb\rc"), "a\nb\nc")

    def test_tabs_become_spaces(self):
        """Not dangerous, but every column calculation here counts characters."""
        self.assertNotIn("\t", frame.sanitize("a\tb"))

    def test_ordinary_text_is_untouched(self):
        for line in ("verbose  /vərˈboʊs/", "使用 `--verbose` 标志",
                     "./src/index.js", "a — b", "1. 例句"):
            with self.subTest(line=line):
                self.assertEqual(frame.sanitize(line), line)

    def test_a_literal_backslash_escape_is_not_a_control_character(self):
        """Selecting the *source code* of an escape must still be explainable."""
        source = r"printf '\033[31mred\033[0m'"
        self.assertEqual(frame.sanitize(source), source)


class EndToEnd(unittest.TestCase):
    def test_nothing_dangerous_survives_a_full_render(self):
        evil = ("看这段：\x1b[31m\x1b]0;HIJACKED\x07\x1b[2J"
                "\x1b]52;c;bWFsaWNpb3Vz\x07\x1bc done")
        body, gutter, sources = frame.layout(evil, 40, 10)
        out = frame.render(title="T", body=body, footer="f", width=40,
                           height=10, gutter=gutter,
                           style=style.styler("general", evil, sources))
        for probe in ("\x1b]", "\x1b[2J", "\x1bc", "\x07"):
            with self.subTest(probe=probe):
                self.assertNotIn(probe, out)

    def test_only_our_own_styling_escapes_remain(self):
        body, gutter, sources = frame.layout("plain `code` text", 40, 10)
        out = frame.render(title="T", body=body, footer="f", width=40,
                           height=10, gutter=gutter,
                           style=style.styler("general", "plain `code` text", sources))
        allowed = {"\x1b[0m", "\x1b[1m", "\x1b[2m", "\x1b[33m", "\x1b[35m",
                   "\x1b[36m", "\x1b[H"}
        found = set(style._ESCAPE.findall(out)) | set(
            m for m in ("\x1b[H",) if m in out
        )
        self.assertTrue(found <= allowed, f"unexpected escapes: {found - allowed}")

    def test_the_provider_never_sees_a_control_sequence(self):
        state = viewer.State()
        prepared = viewer.prepare({"text": "hi \x1b]52;c;eA==\x07 there",
                                   "mode": None}, state)
        self.assertIsNotNone(prepared)
        self.assertNotIn("\x1b", prepared["text"])


if __name__ == "__main__":
    unittest.main()
