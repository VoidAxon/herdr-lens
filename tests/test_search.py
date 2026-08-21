"""Finding a line again in a long result.

The one thing `less` gives away free and the hand-written viewer did not. It
matters in summarise mode, where the output is a screenful precisely because
the input was too long to read.
"""

import unittest

from lens import viewer
from lens.ui import style

SUMMARY = "\n".join([
    "构建失败：webpack 编译出现 3 个错误。",
    "- ./src/api/client.ts 第 42 行 TS2345：类型不匹配",
    "- ./src/api/client.ts 第 88 行 TS2564：属性未初始化",
    "- ./src/hooks/useAuth.ts 第 17 行 TS7006：参数隐式为 any",
    "共 3 个类型错误，均来自 TypeScript 编译器。",
    "建议先修 client.ts，另两处依赖它的类型定义。",
    "npm ERR! code ELIFECYCLE",
])

WIDTH, HEIGHT, PAGE = 60, 12, 7


def driver(text=SUMMARY, **kwargs):
    state = viewer.State(text=text, done=True, mode="summarize",
                         title="Summary", status="P", **kwargs)

    def press(*keys):
        for key in keys:
            # The real loop paints before every read, and `_seek` needs the
            # body that painting produces.
            viewer.compose(state, WIDTH, HEIGHT)
            viewer.handle_key(key, state, PAGE)

    def frame():
        return viewer.compose(state, WIDTH, HEIGHT)

    return state, press, frame


def typed(text):
    return [c.encode() for c in text]


class Matching(unittest.TestCase):
    LINE = "- ./src/api/client.ts 第 42 行"

    def test_a_contiguous_hit_is_one_run(self):
        self.assertEqual(style._match_spans(self.LINE, "client.ts"),
                         [(12, 21, style.MATCH)])

    def test_it_is_case_insensitive(self):
        self.assertTrue(style.matches("TS2345 错误", "ts2345"))
        self.assertTrue(style.matches("client.ts", "CLIENT"))

    def test_a_loose_query_still_finds_the_line(self):
        self.assertTrue(style.matches(self.LINE, "clts"))

    def test_a_loose_hit_is_marked_as_one_span(self):
        """Marking each character is the literal truth and reads as damage."""
        spans = style._match_spans(self.LINE, "clts")
        self.assertEqual(len(spans), 1)
        start, end, _ = spans[0]
        self.assertEqual(self.LINE[start:end], "client.ts")

    def test_the_tightest_span_wins(self):
        """Leftmost-greedy would start at the `c` of `src` and stretch the
        highlight across the whole path."""
        start, _, _ = style._match_spans(self.LINE, "clts")[0]
        self.assertEqual(self.LINE[start], "c")
        self.assertTrue(self.LINE[start:].startswith("client"))

    def test_characters_out_of_order_do_not_match(self):
        self.assertFalse(style.matches(self.LINE, "stlc"))

    def test_an_absent_character_does_not_match(self):
        self.assertFalse(style.matches(self.LINE, "clzts"))

    def test_an_empty_query_matches_nothing(self):
        self.assertFalse(style.matches(self.LINE, ""))
        self.assertEqual(style._match_spans(self.LINE, ""), [])

    def test_a_hit_wins_an_overlapping_code_span(self):
        """The thing being looked for cannot be the thing that gets hidden."""
        _, _, frame_of = driver()
        state, press, frame_of = driver()
        press(b"/", *typed("client"), b"\r")
        painted = frame_of()
        self.assertIn(style.MATCH_CURRENT + "client", painted)


class Typing(unittest.TestCase):
    def test_slash_opens_an_input_line_in_the_footer(self):
        state, press, frame_of = driver()
        press(b"/")
        self.assertTrue(state.typing)
        press(*typed("cli"))
        self.assertEqual(state.draft, "cli")
        self.assertIn("/cli", frame_of())

    def test_backspace_edits_the_draft(self):
        state, press, _ = driver()
        press(b"/", *typed("clix"), b"\x7f")
        self.assertEqual(state.draft, "cli")

    def test_escape_cancels_the_search_and_keeps_the_popup(self):
        """A mistyped query must not cost the result you were searching."""
        state, press, _ = driver()
        press(b"/", *typed("cli"))
        self.assertTrue(viewer.handle_key(b"\x1b", state, PAGE))
        self.assertFalse(state.typing)
        self.assertEqual(state.query, "")

    def test_keys_are_text_while_typing_not_commands(self):
        """`q` would otherwise close the popup mid-query."""
        state, press, _ = driver()
        press(b"/", *typed("q"))
        self.assertTrue(state.typing)
        self.assertEqual(state.draft, "q")

    def test_a_control_character_does_not_enter_the_query(self):
        state, press, _ = driver()
        press(b"/", *typed("cli"), b"\x01")
        self.assertEqual(state.draft, "cli")

    def test_a_query_matching_nothing_says_so(self):
        state, press, frame_of = driver()
        press(b"/", *typed("zzzzz"), b"\r")
        self.assertIn("no match", frame_of())


class Seeking(unittest.TestCase):
    def test_the_counter_counts_matches_not_scroll(self):
        state, press, frame_of = driver()
        press(b"/", *typed("client.ts"), b"\r")
        self.assertIn("1/3", frame_of())
        press(b"n")
        self.assertIn("2/3", frame_of())

    def test_n_cycles_forward_and_wraps(self):
        state, press, frame_of = driver()
        press(b"/", *typed("client.ts"), b"\r")
        for expected in ("2/3", "3/3", "1/3"):
            press(b"n")
            with self.subTest(expected=expected):
                self.assertIn(expected, frame_of())

    def test_shift_n_goes_back(self):
        state, press, frame_of = driver()
        press(b"/", *typed("client.ts"), b"\r", b"n")
        press(b"N")
        self.assertIn("1/3", frame_of())

    def test_a_match_in_the_last_screenful_is_reachable(self):
        """The bug that shipped in the first draft: using `scroll` as the match
        cursor caps it at max_scroll, so anything in the final screenful can
        never become current."""
        long = "\n".join(
            f"line {i}: " + ("TS2345" if i % 7 == 0 else "nothing")
            for i in range(1, 41)
        )
        state, press, frame_of = driver(text=long)
        press(b"/", *typed("TS2345"), b"\r")
        total = len(viewer._hits(state.body, "TS2345"))
        self.assertGreater(total, 1)
        for _ in range(total - 1):
            press(b"n")
        self.assertIn(f"{total}/{total}", frame_of())

    def test_it_only_scrolls_when_the_match_is_off_screen(self):
        """All three fit on screen here, so the view must not jump."""
        state, press, _ = driver()
        press(b"/", *typed("client.ts"), b"\r")
        before = state.scroll
        press(b"n")
        self.assertEqual(state.scroll, before)

    def test_two_hits_on_one_row_count_separately(self):
        """The unit is an occurrence: counting lines makes `n` skip hits and
        paints every hit on the row as the current one."""
        state, press, frame_of = driver(
            text="client.ts 和 client.ts 都要改\n另一行提到 client.ts")
        press(b"/", *typed("client.ts"), b"\r")
        self.assertIn("1/3", frame_of())

    def test_only_one_hit_is_ever_current(self):
        state, press, frame_of = driver(
            text="client.ts 和 client.ts 都要改\n另一行提到 client.ts")
        press(b"/", *typed("client.ts"), b"\r")
        painted = frame_of()
        self.assertEqual(painted.count(style.MATCH_CURRENT), 1)

    def test_n_walks_hits_within_one_row(self):
        state, press, frame_of = driver(
            text="client.ts 和 client.ts 都要改\n另一行提到 client.ts")
        press(b"/", *typed("client.ts"), b"\r", b"n")
        self.assertIn("2/3", frame_of())
        self.assertEqual(state.hit, 1)

    def test_the_current_match_is_coloured_differently(self):
        state, press, frame_of = driver()
        press(b"/", *typed("client.ts"), b"\r")
        painted = frame_of()
        self.assertIn(style.MATCH_CURRENT, painted)
        self.assertIn(style.MATCH, painted)

    def test_the_current_colour_moves_with_n(self):
        state, press, frame_of = driver()
        press(b"/", *typed("client.ts"), b"\r")
        first = frame_of()
        press(b"n")
        self.assertNotEqual(first, frame_of())

    def test_searching_starts_from_where_you_were(self):
        """Not from the top: a search should not throw away your position."""
        long = "\n".join(f"line {i}: TS2345" if i % 10 == 0 else f"line {i}"
                         for i in range(1, 61))
        state, press, _ = driver(text=long)
        press(b"G")
        state.scroll = 30
        press(b"/", *typed("TS2345"), b"\r")
        hits = viewer._hits(state.body, "TS2345")
        self.assertGreaterEqual(hits[state.hit][0], 30)


class Invariant(unittest.TestCase):
    def test_highlighting_stays_additive(self):
        """The width maths runs on plain text, so a highlight that changed the
        characters would corrupt the frame."""
        for query in ("client.ts", "clts", "TS", "错误"):
            with self.subTest(query=query):
                styler = style.styler("summarize", SUMMARY,
                                      list(range(SUMMARY.count("\n") + 1)),
                                      highlight=query, current=1)
                for row, line in enumerate(SUMMARY.split("\n")):
                    self.assertEqual(style.strip(styler(line, row)), line)

    def test_no_colour_leaks_past_a_highlighted_line(self):
        styler = style.styler("summarize", SUMMARY,
                              list(range(SUMMARY.count("\n") + 1)),
                              highlight="client.ts", current=1)
        for row, line in enumerate(SUMMARY.split("\n")):
            painted = styler(line, row)
            escapes = style._ESCAPE.findall(painted)
            if escapes:
                with self.subTest(row=row):
                    self.assertEqual(escapes[-1], style.RESET)

    def test_a_frame_with_matches_still_fits_its_width(self):
        from lens.ui import frame as framing

        state, press, frame_of = driver()
        press(b"/", *typed("client.ts"), b"\r")
        for row in frame_of().replace(framing.HOME, "").split("\r\n"):
            self.assertLessEqual(framing.display_width(style.strip(row)), WIDTH)


if __name__ == "__main__":
    unittest.main()
