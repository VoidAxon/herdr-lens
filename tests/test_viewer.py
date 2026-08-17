import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lens import mode as modes
from lens import viewer
from lens.config import Config
from lens.ui import frame


def plain(painted: str) -> str:
    return painted.replace(frame.HOME, "").replace(frame.DIM, "").replace(frame.RESET, "")


class LoadJob(unittest.TestCase):
    def test_reads_and_then_deletes_the_job_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "job.json"
            job.write_text(json.dumps({"text": "hello"}))
            with mock.patch.dict("os.environ", {"LENS_JOB": str(job)}):
                payload = viewer.load_job()
            self.assertEqual(payload["text"], "hello")
            self.assertFalse(job.exists(), "the selection must not linger on disk")

    def test_missing_env_var_is_not_fatal(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(viewer.load_job(), {})

    def test_corrupt_job_file_is_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "job.json"
            job.write_text("{not json")
            with mock.patch.dict("os.environ", {"LENS_JOB": str(job)}):
                self.assertEqual(viewer.load_job(), {})


class Compose(unittest.TestCase):
    def test_loading_state_shows_a_spinner(self):
        state = viewer.State()
        painted = plain(viewer.compose(state, 40, 12))
        self.assertIn("translating…", painted)
        self.assertTrue(any(ch in painted for ch in frame.SPINNER))

    def test_spinner_advances_with_the_tick(self):
        state = viewer.State()
        first = plain(viewer.compose(state, 40, 12))
        state.tick += 1
        second = plain(viewer.compose(state, 40, 12))
        self.assertNotEqual(first, second)

    def test_result_state_shows_the_translation_and_provider(self):
        state = viewer.State(
            title="Translation", text="默认情况下，grep 会输出匹配的行。",
            done=True, status="Anthropic · claude-opus-5",
        )
        painted = plain(viewer.compose(state, 60, 14))
        self.assertIn("默认情况下", painted)
        self.assertIn("Translation", painted)
        self.assertIn("claude-opus-5", painted)

    def test_error_state_shows_message_and_hint(self):
        state = viewer.State(
            error=("No AI provider configured.", "Export ANTHROPIC_API_KEY."), done=True
        )
        painted = plain(viewer.compose(state, 50, 12))
        self.assertIn("No AI provider configured.", painted)
        self.assertIn("ANTHROPIC_API_KEY", painted)

    def test_footer_confirms_a_copy(self):
        state = viewer.State(text="x", done=True, copied_ticks=5)
        self.assertIn("copied to clipboard", plain(viewer.compose(state, 40, 12)))

    def test_footer_lists_the_keys_by_default(self):
        state = viewer.State(text="x", done=True)
        painted = plain(viewer.compose(state, 60, 12))
        for key in ("[c] copy", "[j/k] scroll", "[Esc] close"):
            self.assertIn(key, painted)

    def test_scroll_is_clamped_against_the_content(self):
        state = viewer.State(text="one line", done=True, scroll=500)
        viewer.compose(state, 40, 12)
        self.assertEqual(state.scroll, 0)

    def test_long_result_scrolls(self):
        state = viewer.State(text="\n".join(f"line {i}" for i in range(50)), done=True)
        top = plain(viewer.compose(state, 40, 12))
        state.scroll = 20
        scrolled = plain(viewer.compose(state, 40, 12))
        self.assertIn("line 0", top)
        self.assertNotIn("line 0\n", scrolled)
        self.assertIn("line 20", scrolled)


class Copyable(unittest.TestCase):
    def test_copies_the_translation(self):
        self.assertEqual(viewer.copyable(viewer.State(text="译文", done=True)), "译文")

    def test_copies_the_error_so_it_can_be_pasted_into_a_bug_report(self):
        state = viewer.State(error=("Cannot reach the AI provider.", "api.openai.com"), done=True)
        self.assertIn("api.openai.com", viewer.copyable(state))


class Keys(unittest.TestCase):
    def state(self):
        return viewer.State(text="\n".join(str(i) for i in range(100)), done=True)

    def test_esc_closes(self):
        self.assertFalse(viewer.handle_key(b"\x1b", self.state(), 10))

    def test_q_closes(self):
        self.assertFalse(viewer.handle_key(b"q", self.state(), 10))

    def test_ctrl_c_closes(self):
        self.assertFalse(viewer.handle_key(b"\x03", self.state(), 10))

    def test_j_and_down_arrow_scroll_down(self):
        for key in (b"j", b"\x1b[B"):
            with self.subTest(key=key):
                s = self.state()
                viewer.handle_key(key, s, 10)
                self.assertEqual(s.scroll, 1)

    def test_k_never_scrolls_above_the_top(self):
        s = self.state()
        viewer.handle_key(b"k", s, 10)
        self.assertEqual(s.scroll, 0)

    def test_page_keys_move_by_a_page(self):
        s = self.state()
        viewer.handle_key(b"\x1b[6~", s, 10)
        self.assertEqual(s.scroll, 10)
        viewer.handle_key(b"\x1b[5~", s, 10)
        self.assertEqual(s.scroll, 0)

    def test_g_jumps_to_the_top(self):
        s = self.state()
        s.scroll = 40
        viewer.handle_key(b"g", s, 10)
        self.assertEqual(s.scroll, 0)

    def test_mouse_wheel_scrolls(self):
        s = self.state()
        viewer.handle_key(b"\x1b[<65;10;5M", s, 10)
        self.assertEqual(s.scroll, 3)
        viewer.handle_key(b"\x1b[<64;10;5M", s, 10)
        self.assertEqual(s.scroll, 0)

    def test_unknown_keys_are_ignored(self):
        s = self.state()
        self.assertTrue(viewer.handle_key(b"\x1b[Z", s, 10))
        self.assertEqual(s.scroll, 0)


def a_config(**kw):
    base = dict(
        target_language="zh-CN", source_language="auto", provider="anthropic",
        model="claude-sonnet-5", endpoint=None, api_key_env=None, api_key_file=None,
        api_key_command=None, auth=None,
        prompt="TRANSLATE {target_language}", word_prompt="DEFINE {target_language}", term_prompt="TERM {target_language}",
        explain_prompt="EXPLAIN {target_language}", summarize_prompt="SUMMARISE {target_language}",
        word_lookup=True,
    )
    base.update(kw)
    return Config(**base)


class Worker(unittest.TestCase):
    def test_provider_error_lands_in_the_error_state(self):
        from lens.providers import ProviderError

        state = viewer.State()
        with mock.patch("lens.viewer.build", side_effect=ProviderError("boom", "hint")):
            viewer.translate({"text": "hi"}, state, a_config())
        self.assertEqual(state.error, ("boom", "hint"))
        self.assertTrue(state.done)

    def test_empty_response_is_reported(self):
        state = viewer.State()
        provider = mock.Mock(name="p", model="m")
        provider.translate.return_value = ""
        with mock.patch("lens.viewer.build", return_value=provider):
            viewer.translate({"text": "hi"}, state, a_config())
        self.assertIn("empty", state.error[0].lower())

    def test_unexpected_exception_still_renders(self):
        state = viewer.State()
        with mock.patch("lens.viewer.build", side_effect=RuntimeError("kaboom")):
            viewer.translate({"text": "hi"}, state, a_config())
        self.assertTrue(state.done)
        self.assertIn("kaboom", state.error[1])

    def test_a_word_gets_the_dictionary_prompt_and_title(self):
        state = viewer.State()
        provider = mock.Mock(model="m")
        provider.name = "P"
        provider.translate.return_value = "prefix /ˈpriːfɪks/\n\nn. 前缀"
        with mock.patch("lens.viewer.build", return_value=provider):
            viewer.translate({"text": "prefix"}, state, a_config())

        self.assertEqual(provider.translate.call_args[0][3], "DEFINE Chinese (Simplified)")
        self.assertEqual(state.title, "Dictionary")

    def test_a_sentence_gets_the_translation_prompt_and_title(self):
        state = viewer.State()
        provider = mock.Mock(model="m")
        provider.name = "P"
        provider.translate.return_value = "默认情况下…"
        with mock.patch("lens.viewer.build", return_value=provider):
            viewer.translate({"text": "By default, grep prints matches."}, state, a_config())

        self.assertEqual(provider.translate.call_args[0][3], "TRANSLATE Chinese (Simplified)")
        self.assertEqual(state.title, "Translation")

    def test_word_lookup_disabled_routes_words_to_translation(self):
        state = viewer.State()
        provider = mock.Mock(model="m")
        provider.name = "P"
        provider.translate.return_value = "前缀"
        with mock.patch("lens.viewer.build", return_value=provider):
            viewer.translate({"text": "prefix"}, state, a_config(word_lookup=False))

        self.assertEqual(provider.translate.call_args[0][3], "TRANSLATE Chinese (Simplified)")
        self.assertEqual(state.title, "Translation")

    def test_success_sets_the_translation(self):
        state = viewer.State()
        provider = mock.Mock(model="claude-opus-5")
        provider.name = "Anthropic"
        provider.translate.return_value = "默认情况下…"
        with mock.patch("lens.viewer.build", return_value=provider):
            viewer.translate({"text": "By default, grep prints matches."}, state, a_config())
        self.assertEqual(state.text, "默认情况下…")
        self.assertEqual(state.title, "Translation")
        self.assertIsNone(state.error)


class FastFail(unittest.TestCase):
    """Junk is settled before the worker starts: no provider, no request."""

    def test_junk_is_rejected_without_starting_a_worker(self):
        for text in ("====", "[████░░░░] 45%", "┌─ Lens ─┐",
                     "550e8400-e29b-41d4-a716-446655440000"):
            with self.subTest(text=text):
                state = viewer.State()
                self.assertIsNone(viewer.prepare({"text": text}, state))
                self.assertTrue(state.done)
                self.assertEqual(state.error, viewer.JUNK_MESSAGE)

    def test_empty_selection_explains_copy_on_select(self):
        state = viewer.State()
        self.assertIsNone(viewer.prepare({"text": "   "}, state))
        self.assertIn("No text selected", state.error[0])
        self.assertIn("copy_on_select", state.error[1])

    def test_real_text_is_handed_to_the_worker(self):
        state = viewer.State()
        prepared = viewer.prepare({"text": "Permission denied"}, state)
        self.assertEqual(prepared["text"], "Permission denied")
        self.assertFalse(state.done)
        self.assertIsNone(state.error)

    def test_a_word_shows_a_lookup_spinner_label(self):
        state = viewer.State()
        viewer.prepare({"text": "prefix"}, state)
        self.assertEqual(state.title, "Looking up…")

    def test_junk_renders_as_an_error_frame(self):
        state = viewer.State(error=viewer.JUNK_MESSAGE, done=True)
        painted = plain(viewer.compose(state, 50, 12))
        self.assertIn("Nothing to translate", painted)


class Truncation(unittest.TestCase):
    def test_oversized_selection_is_capped_before_sending(self):
        state = viewer.State()
        prepared = viewer.prepare({"text": "word " * 5000}, state)
        self.assertLessEqual(len(prepared["text"]), modes.MAX_INPUT_CHARS)

    def test_truncation_is_surfaced_in_the_status_line(self):
        state = viewer.State()
        viewer.prepare({"text": "word " * 5000}, state)
        self.assertIn(str(modes.MAX_INPUT_CHARS), plain(viewer.compose(state, 60, 12)))

    def test_a_normal_selection_is_not_flagged_as_truncated(self):
        state = viewer.State()
        viewer.prepare({"text": "Permission denied"}, state)
        self.assertEqual(state.status, "")


if __name__ == "__main__":
    unittest.main()


class ForcedModes(unittest.TestCase):
    """Which key was pressed decides what the user wants. The same selection
    can legitimately be translated, explained, or summarised, and the text
    cannot say which — so an explicit action must override classification."""

    def test_explain_overrides_what_the_text_looks_like(self):
        state = viewer.State()
        # A bare word would otherwise become a dictionary entry.
        viewer.prepare({"text": "verbose", "mode": "explain"}, state)
        self.assertEqual(state.mode, modes.EXPLAIN)
        self.assertEqual(state.title, "Explaining…")

    def test_summarise_overrides_a_single_sentence(self):
        state = viewer.State()
        viewer.prepare({"text": "Permission denied", "mode": "summarize"}, state)
        self.assertEqual(state.mode, modes.SUMMARIZE)
        self.assertEqual(state.title, "Summarising…")

    def test_translate_still_classifies_by_content(self):
        state = viewer.State()
        viewer.prepare({"text": "verbose", "mode": None}, state)
        self.assertEqual(state.mode, modes.WORD)

    def test_junk_is_still_rejected_under_a_forced_mode(self):
        # Asking to summarise box art is still nothing to summarise.
        state = viewer.State()
        self.assertIsNone(viewer.prepare({"text": "====", "mode": "summarize"}, state))
        self.assertEqual(state.error, viewer.JUNK_MESSAGE)

    def test_summarising_allows_a_far_larger_selection(self):
        state = viewer.State()
        prepared = viewer.prepare({"text": "word " * 4000, "mode": "summarize"}, state)
        self.assertGreater(len(prepared["text"]), modes.MAX_INPUT_CHARS)
        self.assertLessEqual(len(prepared["text"]), modes.MAX_SUMMARY_CHARS)

    def test_translation_keeps_the_smaller_cap(self):
        state = viewer.State()
        prepared = viewer.prepare({"text": "word " * 4000, "mode": None}, state)
        self.assertLessEqual(len(prepared["text"]), modes.MAX_INPUT_CHARS)

    def test_each_forced_mode_picks_its_own_prompt(self):
        for mode, expected in [("explain", "EXPLAIN Chinese (Simplified)"), ("summarize", "SUMMARISE Chinese (Simplified)")]:
            with self.subTest(mode=mode):
                state = viewer.State()
                provider = mock.Mock(model="m")
                provider.name = "P"
                provider.translate.return_value = "结果"
                with mock.patch("lens.viewer.build", return_value=provider):
                    viewer.translate({"text": "anything at all", "mode": mode}, state, a_config())
                self.assertEqual(provider.translate.call_args[0][3], expected)

    def test_the_title_reflects_the_mode_on_completion(self):
        for mode, title in [("explain", "Explanation"), ("summarize", "Summary")]:
            with self.subTest(mode=mode):
                state = viewer.State()
                provider = mock.Mock(model="m")
                provider.name = "P"
                provider.translate.return_value = "结果"
                with mock.patch("lens.viewer.build", return_value=provider):
                    viewer.translate({"text": "x", "mode": mode}, state, a_config())
                self.assertEqual(state.title, title)
