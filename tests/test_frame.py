import unittest

from lens.ui import frame


class DisplayWidth(unittest.TestCase):
    def test_ascii_is_one_column_per_char(self):
        self.assertEqual(frame.display_width("grep"), 4)

    def test_cjk_is_two_columns_per_char(self):
        self.assertEqual(frame.display_width("默认情况下"), 10)

    def test_mixed_run(self):
        self.assertEqual(frame.display_width("默认 grep"), 4 + 1 + 4)

    def test_combining_marks_take_no_width(self):
        self.assertEqual(frame.display_width("é"), 1)


class Wrap(unittest.TestCase):
    def test_short_text_is_one_line(self):
        self.assertEqual(frame.wrap("hello", 20), ["hello"])

    def test_breaks_on_spaces(self):
        self.assertEqual(frame.wrap("aaa bbb ccc", 7), ["aaa bbb", "ccc"])

    def test_preserves_explicit_newlines(self):
        self.assertEqual(frame.wrap("a\n\nb", 10), ["a", "", "b"])

    def test_cjk_wraps_by_columns_not_characters(self):
        # Ten CJK chars occupy 20 columns, so a width of 10 must split them 5/5.
        lines = frame.wrap("默认情况下默认情况下", 10)
        self.assertEqual(lines, ["默认情况下", "默认情况下"])

    def test_every_wrapped_line_fits_the_width(self):
        text = "git config --global core.autocrlf false 默认情况下会输出匹配的行"
        for line in frame.wrap(text, 24):
            self.assertLessEqual(frame.display_width(line), 24)

    def test_unbreakable_token_is_split_midword(self):
        lines = frame.wrap("/very/long/path/without/spaces", 10)
        self.assertTrue(all(frame.display_width(l) <= 10 for l in lines))
        self.assertEqual("".join(lines), "/very/long/path/without/spaces")


class Render(unittest.TestCase):
    def frame_rows(self, **kwargs):
        painted = frame.render(**kwargs)
        return painted.replace(frame.HOME, "").split("\r\n")

    def test_row_count_matches_height(self):
        rows = self.frame_rows(
            title="Translation", body=["a"], footer="[Esc]", width=40, height=12
        )
        self.assertEqual(len(rows), 12)

    def test_short_body_is_padded_to_fill_the_pane(self):
        rows = self.frame_rows(
            title="T", body=["only line"], footer="f", width=30, height=10
        )
        self.assertEqual(len(rows), 10)

    def test_overflowing_body_shows_a_position_indicator(self):
        body = [f"line {i}" for i in range(50)]
        painted = frame.render(
            title="Translation", body=body, footer="f", width=40, height=10
        )
        self.assertIn("/50", painted)

    def test_scroll_is_clamped_to_content(self):
        body = [f"line {i}" for i in range(10)]
        painted = frame.render(
            title="T", body=body, footer="f", width=40, height=10, scroll=999
        )
        self.assertIn("line 9", painted)

    def test_no_row_exceeds_the_width(self):
        body = frame.wrap("默认情况下，grep 会输出匹配的行。" * 4, 30)
        rows = self.frame_rows(
            title="Translation", body=body, footer="[c] copy", width=30, height=12
        )
        for row in rows:
            plain = row.replace(frame.DIM, "").replace(frame.RESET, "")
            self.assertLessEqual(frame.display_width(plain), 30)


class HangingIndent(unittest.TestCase):
    """An option list must keep its shape when it wraps."""

    def test_continuation_lines_inherit_the_indent(self):
        lines = frame.wrap("  -F, --fixed-strings   将 PATTERNS 解释为固定字符串。", 34)
        self.assertGreater(len(lines), 1)
        for line in lines:
            self.assertTrue(line.startswith("  "), f"lost indent: {line!r}")

    def test_unindented_text_gains_no_indent(self):
        for line in frame.wrap("a b c d e f g h i j k l m n o p", 10):
            self.assertFalse(line.startswith(" "))

    def test_indented_lines_still_fit_the_width(self):
        text = "    --long-option-name    描述文字描述文字描述文字描述文字描述文字"
        for line in frame.wrap(text, 30):
            self.assertLessEqual(frame.display_width(line), 30)

    def test_an_indent_too_wide_to_be_useful_is_dropped(self):
        # Otherwise a deeply indented line would wrap into a single column.
        lines = frame.wrap(" " * 25 + "some text that needs wrapping here", 30)
        self.assertTrue(all(frame.display_width(l) <= 30 for l in lines))


class Layout(unittest.TestCase):
    def test_short_content_keeps_the_full_width(self):
        lines, gutter, _ = frame.layout("one short line", 60, 14)
        self.assertFalse(gutter)

    def test_overflowing_content_reserves_the_gutter(self):
        text = "\n".join(f"line {i}" for i in range(50))
        lines, gutter, _ = frame.layout(text, 60, 14)
        self.assertTrue(gutter)
        for line in lines:
            self.assertLessEqual(frame.display_width(line), 60 - frame.GUTTER)


class Scrollbar(unittest.TestCase):
    def bar(self, painted, width):
        """The last visible column of each content row."""
        rows = painted.replace(frame.HOME, "").split("\r\n")[2:-2]
        return "".join(r.replace(frame.DIM, "").replace(frame.RESET, "")[-1] for r in rows)

    def frame_with(self, count=60, height=14, scroll=0):
        body = [f"line {i}" for i in range(count)]
        return frame.render(
            title="T", body=body, footer="f", width=40, height=height,
            scroll=scroll, gutter=True,
        )

    def test_a_thumb_is_drawn_when_content_overflows(self):
        self.assertIn(frame.THUMB, self.bar(self.frame_with(), 40))

    def test_no_thumb_when_everything_fits(self):
        painted = frame.render(
            title="T", body=["a", "b"], footer="f", width=40, height=14, gutter=True
        )
        self.assertNotIn(frame.THUMB, self.bar(painted, 40))

    def test_the_thumb_starts_at_the_top(self):
        bar = self.bar(self.frame_with(scroll=0), 40)
        self.assertEqual(bar[0], frame.THUMB)

    def test_the_thumb_reaches_the_bottom_at_full_scroll(self):
        bar = self.bar(self.frame_with(scroll=10**6), 40)
        self.assertEqual(bar[-1], frame.THUMB)

    def test_the_thumb_moves_down_as_you_scroll(self):
        top = self.bar(self.frame_with(scroll=0), 40).index(frame.THUMB)
        mid = self.bar(self.frame_with(scroll=25), 40).index(frame.THUMB)
        self.assertGreater(mid, top)

    def test_the_thumb_shrinks_as_content_grows(self):
        short = self.bar(self.frame_with(count=20), 40).count(frame.THUMB)
        long = self.bar(self.frame_with(count=400), 40).count(frame.THUMB)
        self.assertGreater(short, long)
        self.assertGreaterEqual(long, 1, "the thumb must never vanish")

    def test_rows_still_fit_the_width_with_a_gutter(self):
        painted = self.frame_with()
        for row in painted.replace(frame.HOME, "").split("\r\n"):
            plain = row.replace(frame.DIM, "").replace(frame.RESET, "")
            self.assertLessEqual(frame.display_width(plain), 40)


class MaxScroll(unittest.TestCase):
    def test_content_shorter_than_pane_cannot_scroll(self):
        self.assertEqual(frame.max_scroll(["a", "b"], 20), 0)

    def test_content_longer_than_pane_scrolls_by_the_overflow(self):
        self.assertEqual(frame.max_scroll([str(i) for i in range(20)], 10), 20 - 6)



class ListHang(unittest.TestCase):
    def test_a_numbered_item_hangs_under_its_text(self):
        lines = frame.wrap("1. Enable the --verbose flag to see detailed logs.", 38)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("1. "))
        self.assertTrue(lines[1].startswith("   "), f"got {lines[1]!r}")

    def test_bullets_hang_too(self):
        lines = frame.wrap("- a bullet whose text is long enough to wrap around", 24)
        self.assertTrue(lines[1].startswith("  "), f"got {lines[1]!r}")

    def test_ordinary_paragraphs_gain_no_hang(self):
        lines = frame.wrap("plain prose that is long enough to need wrapping here", 24)
        self.assertFalse(lines[1].startswith(" "))

    def test_hanging_lines_still_fit(self):
        for line in frame.wrap("1. " + "词" * 40, 30):
            self.assertLessEqual(frame.display_width(line), 30)


class LineBreakingRules(unittest.TestCase):
    """CJK punctuation must not be stranded at the start of a line."""

    def test_a_full_stop_never_opens_a_line(self):
        lines = frame.wrap("启用标志以在构建过程中查看详细的日志输出。", 20)
        for line in lines[1:]:
            self.assertNotIn(line[:1], "。，、）」")

    def test_closing_brackets_never_open_a_line(self):
        for text in ("这是一个终止信号（可被捕获）", "参见文档「说明」"):
            with self.subTest(text=text):
                for line in frame.wrap(text, 12)[1:]:
                    self.assertNotIn(line[:1], "。，、）」")

    def test_backing_up_never_overflows_the_width(self):
        for width in (10, 12, 16, 20, 24):
            for line in frame.wrap("默认情况下，grep 会打印匹配的行。" * 3, width):
                self.assertLessEqual(frame.display_width(line), width)

if __name__ == "__main__":
    unittest.main()
