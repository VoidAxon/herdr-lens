import unittest

from lens.ui import frame, style

WORD_ENTRY = """verbose  /vɜːrˈboʊs/

adj.  冗长的，啰嗦的
形容詞  在计算机中，为提供详细输出而设置的选项

1. Enable the `--verbose` flag to see detailed logs and every step it takes.
   启用 `--verbose` 标志以查看详细日志。
2. The build was verbose.
   构建过程很啰嗦。"""


def paint(mode, text, width=80):
    """Style every wrapped row the way the viewer does."""
    body, _, sources = frame.layout(text, width, 999)
    styler = style.styler(mode, text, sources)
    return body, [styler(line, row) for row, line in enumerate(body)]


class Invariant(unittest.TestCase):
    """Styling is additive. If it ever removes or reorders a character, the
    width maths in `frame` — done on plain text — silently goes wrong."""

    LINES = [
        "verbose  /vɜːrˈboʊs/",
        "adj.  冗长的，啰嗦的",
        "1. Enable the `--verbose` flag to see detailed logs.",
        "   启用 `--verbose` 标志以查看详细日志。",
        "SIGTERM",
        "默认情况下，grep 会打印匹配的行。",
        "",
        "   ",
        "`only code`",
        "no markup at all",
        "* 取消 `feature` 分支上的前几次提交",
        "**内容**：程序申请了内存",
        "./src/index.js 中发生 TS2345 错误",
        "和/或 这不是路径",
    ]

    def test_stripping_the_escapes_returns_the_original(self):
        text = "\n".join(self.LINES)
        for mode in ("word", "term", "general", "explain", "summarize"):
            styler = style.styler(mode, text, list(range(len(self.LINES))))
            for row, line in enumerate(self.LINES):
                with self.subTest(mode=mode, line=line):
                    self.assertEqual(style.strip(styler(line, row)), line)

    def test_display_width_is_unchanged(self):
        text = "\n".join(self.LINES)
        for mode in ("word", "term", "general"):
            styler = style.styler(mode, text, list(range(len(self.LINES))))
            for row, line in enumerate(self.LINES):
                with self.subTest(mode=mode, line=line):
                    self.assertEqual(
                        frame.display_width(style.strip(styler(line, row))),
                        frame.display_width(line),
                    )

    def test_no_colour_leaks_past_the_end_of_a_line(self):
        """The last escape on a line must be a reset, or the colour bleeds
        into whatever the renderer writes next."""
        for mode in ("word", "term", "general"):
            _, painted = paint(mode, "\n".join(self.LINES))
            for row in painted:
                escapes = style._ESCAPE.findall(row)
                if escapes:
                    with self.subTest(mode=mode, row=row):
                        self.assertEqual(escapes[-1], style.RESET, repr(row))


class WordEntry(unittest.TestCase):
    def setUp(self):
        self.body, self.painted = paint("word", WORD_ENTRY)

    def row(self, needle):
        for plain, painted in zip(self.body, self.painted):
            if needle in plain:
                return painted
        self.fail(f"no row containing {needle!r}")

    def test_the_headword_is_bold(self):
        self.assertIn(style.BOLD + "verbose", self.painted[0])

    def test_the_pronunciation_is_muted(self):
        self.assertIn(style.SECONDARY + "/vɜːrˈboʊs/", self.painted[0])

    def test_a_headword_only_counts_on_the_first_line(self):
        # "verbose" also appears in an example; only row 0 is the headword.
        self.assertNotIn(style.BOLD + "verbose", self.row("The build was verbose"))

    def test_the_part_of_speech_gets_its_own_colour(self):
        """Not dim: it is a label to read, not background detail."""
        self.assertIn(style.PART_OF_SPEECH + "adj.", self.row("冗长的，啰嗦的"))

    def test_a_part_of_speech_in_the_target_language_is_found_too(self):
        """`形容詞` has no trailing dot; matching the column gap catches it
        where a list of English abbreviations would not."""
        self.assertIn(style.PART_OF_SPEECH + "形容詞", self.row("为提供详细输出"))

    def test_example_numbers_are_markers(self):
        self.assertIn(style.MARKER + "1.", self.row("Enable the"))

    def test_an_examples_translation_is_dimmed_whole(self):
        painted = self.row("启用")
        self.assertTrue(painted.startswith(style.SECONDARY), repr(painted))

    def test_a_wrapped_example_is_not_mistaken_for_its_translation(self):
        """Both carry the same indent after wrapping; only the source
        structure distinguishes them. This is why roles are decided on the
        source lines rather than on what the renderer ends up with."""
        body, painted = paint("word", WORD_ENTRY, width=40)
        wrapped = [(p, q) for p, q in zip(body, painted)
                   if p.startswith("   ") and "detailed logs" in p or
                   p.startswith("   ") and "every step" in p]
        self.assertTrue(wrapped, "expected the English example to wrap")
        for plain, styled in wrapped:
            self.assertFalse(
                styled.startswith(style.SECONDARY),
                f"continuation of the example was dimmed as a translation: {plain!r}",
            )

    def test_code_inside_a_dimmed_translation_still_shows(self):
        painted = self.row("启用")
        self.assertIn(style.CODE + "`--verbose`", painted)
        # …and the dim resumes after it, rather than ending there.
        self.assertIn(style.RESET + style.SECONDARY, painted)


class TermEntry(unittest.TestCase):
    def test_the_identifier_is_highlighted(self):
        _, painted = paint("term", "SIGTERM\n\nSIGTERM 是一个信号名称。")
        self.assertIn(style.IDENTIFIER + "SIGTERM", painted[0])

    def test_only_on_the_first_line(self):
        _, painted = paint("term", "SIGTERM\n\nSIGTERM 是一个信号名称。")
        self.assertNotIn(style.IDENTIFIER, painted[2])


class Prose(unittest.TestCase):
    """explain and summarise are prose with structure the model marks itself."""

    def test_bullet_markers_are_coloured_but_not_the_text(self):
        _, painted = paint("explain", "* 取消 feature 分支上的提交")
        self.assertIn(style.MARKER + "*", painted[0])
        self.assertNotIn(style.MARKER + "* 取消", painted[0])

    def test_the_models_own_emphasis_is_bold(self):
        _, painted = paint("explain", "**内容**：程序申请了内存")
        self.assertIn(style.BOLD + "**内容**", painted[0])

    def test_a_path_the_model_left_bare_is_still_coloured(self):
        _, painted = paint("summarize", "./src/index.js 中发生错误")
        self.assertIn(style.CODE + "./src/index.js", painted[0])

    def test_an_absolute_path_needs_two_segments(self):
        _, painted = paint("summarize", "见 /home/pasys/notes.txt")
        self.assertIn(style.CODE + "/home/pasys/notes.txt", painted[0])

    def test_a_slash_between_words_is_not_a_path(self):
        """`和/或`, `and/or` — colouring these would just look broken."""
        for line in ("和/或 都可以", "and/or whichever"):
            with self.subTest(line=line):
                _, painted = paint("summarize", line)
                self.assertEqual(painted[0], line)

    def test_error_codes_are_coloured(self):
        _, painted = paint("summarize", "TS2345 错误出现在第 42 行")
        self.assertIn(style.CODE + "TS2345", painted[0])

    def test_a_plain_number_is_not_an_error_code(self):
        _, painted = paint("summarize", "共 42 个错误")
        self.assertEqual(painted[0], "共 42 个错误")


class CodeSpans(unittest.TestCase):
    def test_backticked_text_is_coloured_in_every_mode(self):
        line = "使用 `--verbose` 标志"
        for mode in ("word", "term", "general", "explain", "summarize"):
            with self.subTest(mode=mode):
                _, painted = paint(mode, f"x\n\n{line}")
                self.assertIn(style.CODE + "`--verbose`", painted[2])

    def test_the_backticks_are_kept(self):
        """Dropping them would change the visible width mid-render."""
        _, painted = paint("general", "run `grep -n`")
        self.assertEqual(style.strip(painted[0]), "run `grep -n`")

    def test_several_spans_on_one_line(self):
        _, painted = paint("general", "`a` and `b`")
        self.assertEqual(painted[0].count(style.CODE), 2)

    def test_an_unclosed_backtick_is_left_alone(self):
        _, painted = paint("general", "an ` unmatched tick")
        self.assertEqual(painted[0], "an ` unmatched tick")

    def test_a_span_that_wraps_is_coloured_across_both_rows(self):
        """A row holding half a span must not pair its backtick with the next
        span's opening one — that colours the text *between* two spans."""
        text = "这是一个 Git 命令，由 `git rebase`、`--onto`、`main` 组成。"
        body, painted = paint("general", text, width=24)
        self.assertGreater(len(body), 1, "expected the span to wrap")
        joined = "".join(painted)
        # The separator between two spans must never be styled as code.
        self.assertNotIn(style.CODE + "`、`", joined)
        for plain, styled in zip(body, painted):
            self.assertEqual(style.strip(styled), plain)

    def test_a_wrapped_span_does_not_leak_into_the_next_source_line(self):
        text = "开头 `unclosed\n下一行不该整行变色"
        body, painted = paint("general", text, width=40)
        self.assertEqual(painted[-1], body[-1])


class Rendering(unittest.TestCase):
    def test_a_styled_frame_still_fits_its_width(self):
        text = "verbose  /vɜːrˈboʊs/\nadj.  冗长的\n1. Use `--verbose`."
        body, _, sources = frame.layout(text, 30, 12)
        painted = frame.render(
            title="Dictionary", body=body, footer="f", width=30, height=12,
            style=style.styler("word", text, sources),
        )
        for row in painted.replace(frame.HOME, "").split("\r\n"):
            self.assertLessEqual(frame.display_width(style.strip(row)), 30)

    def test_styling_survives_scrolling_without_shifting(self):
        text = "\n".join(f"line {i}" for i in range(40))
        body, _, sources = frame.layout(text, 30, 12)
        painted = frame.render(
            title="T", body=body, footer="f", width=30, height=12,
            scroll=10, style=style.styler("word", text, sources),
        )
        # Row 0 is off-screen, so nothing on screen should be a headword.
        self.assertNotIn(style.BOLD, painted)


if __name__ == "__main__":
    unittest.main()
