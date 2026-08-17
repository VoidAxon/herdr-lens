import unittest

from lens import language


class Scripts(unittest.TestCase):
    def test_kana_settles_japanese_even_beside_kanji(self):
        # Japanese mixes kana with kanji, so any kana at all is decisive.
        self.assertEqual(language.dominant_script("既定の"), "kana")
        self.assertEqual(language.dominant_script("デフォルト"), "kana")

    def test_hangul_settles_korean(self):
        self.assertEqual(language.dominant_script("기본적으로"), "hangul")

    def test_han_alone_settles_nothing(self):
        self.assertEqual(language.dominant_script("既定"), "han")

    def test_latin(self):
        self.assertEqual(language.dominant_script("verbose"), "latin")

    def test_punctuation_and_digits_carry_no_script(self):
        self.assertIsNone(language.dominant_script("--- 123 ---"))

    def test_kana_outranks_latin_in_mixed_text(self):
        self.assertEqual(language.dominant_script("grep で検索"), "kana")


class Normalise(unittest.TestCase):
    def test_a_bare_string_becomes_a_list(self):
        self.assertEqual(language.normalise("ja"), ["ja"])

    def test_a_list_passes_through(self):
        self.assertEqual(language.normalise(["en", "ja"]), ["en", "ja"])

    def test_auto_is_not_a_candidate(self):
        self.assertEqual(language.normalise("auto"), [])
        self.assertEqual(language.normalise(["auto", "ja"]), ["ja"])

    def test_empty_configurations(self):
        for value in (None, "", []):
            self.assertEqual(language.normalise(value), [])


class Resolve(unittest.TestCase):
    """Script narrows by characters, candidates narrow by configuration; the
    two together usually leave exactly one answer without any guessing."""

    def test_candidates_disambiguate_a_han_only_word(self):
        # The whole point: English uses no Han, so 既定 can only be Japanese.
        self.assertEqual(language.resolve("既定", ["en", "ja"]), "Japanese")

    def test_han_stays_ambiguous_when_both_candidates_use_it(self):
        answer = language.resolve("既定", ["zh-CN", "ja"])
        self.assertIn("Chinese", answer)
        self.assertIn("Japanese", answer)

    def test_han_without_candidates_states_the_ambiguity(self):
        answer = language.resolve("既定", "auto")
        self.assertIn("Chinese or Japanese", answer)
        self.assertIn("no kana", answer)

    def test_latin_resolves_against_candidates(self):
        self.assertEqual(language.resolve("verbose", ["en", "ja"]), "English")

    def test_latin_without_candidates_says_nothing(self):
        # Listing every Latin-script language would be noise, not a hint.
        self.assertEqual(language.resolve("verbose", "auto"), language.AUTO)

    def test_kana_wins_over_the_candidate_order(self):
        self.assertEqual(language.resolve("デフォルト", ["en", "ja"]), "Japanese")

    def test_korean_is_recognised_even_when_not_a_candidate(self):
        # Asserting "English" for Hangul would be worse than saying Korean.
        self.assertEqual(language.resolve("기본", ["en", "ja"]), "Korean")

    def test_a_single_candidate_is_used_directly(self):
        self.assertEqual(language.resolve("默认", ["zh-CN"]), "Chinese (Simplified)")

    def test_unknown_scripts_fall_back_to_the_candidates(self):
        self.assertEqual(language.resolve("123", ["ja"]), "Japanese")

    def test_nothing_configured_and_nothing_detectable(self):
        self.assertEqual(language.resolve("123", "auto"), language.AUTO)

    def test_several_candidates_are_listed_readably(self):
        answer = language.resolve("verbose", ["en", "fr", "de"])
        self.assertEqual(answer, "English, French or German")


class Display(unittest.TestCase):
    """Prompts are read by a model, not a config parser."""

    def test_a_code_becomes_a_name(self):
        self.assertEqual(language.display("zh-CN"), "Chinese (Simplified)")
        self.assertEqual(language.display("ja"), "Japanese")

    def test_a_region_falls_back_to_the_base_language(self):
        self.assertEqual(language.display("pt-BR"), "Portuguese")

    def test_an_unknown_code_passes_through(self):
        # A code the model can guess at beats dropping the instruction.
        self.assertEqual(language.display("xx"), "xx")

    def test_empty(self):
        self.assertEqual(language.display(""), "")


if __name__ == "__main__":
    unittest.main()
