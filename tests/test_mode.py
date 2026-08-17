import unittest

from lens import mode


class Junk(unittest.TestCase):
    """Rejected locally, with no request. Table-driven because these are the
    cases that actually show up in a terminal."""

    def test_no_letters_at_all(self):
        for text in ("====", "───────", ">>>", "###", "1234", "0.5", "#!@$%",
                     "|||", "...", "-> <-", "[  ] 45%", "+-+-+-+"):
            with self.subTest(text=text):
                self.assertEqual(mode.classify(text), mode.JUNK)

    def test_box_art_with_a_stray_label(self):
        for text in ("┌─ Lens ─┐", "══ ok ══════════════════════"):
            with self.subTest(text=text):
                self.assertEqual(mode.classify(text), mode.JUNK)

    def test_progress_bar_fragments(self):
        for text in ("[████░░░░] 45%", "45% ▓▓▓▓▓░░░░░"):
            with self.subTest(text=text):
                self.assertEqual(mode.classify(text), mode.JUNK)

    def test_hashes_uuids_and_tokens(self):
        for text in (
            "550e8400-e29b-41d4-a716-446655440000",
            "2e4cfe5bcf6771fd91739d8b07ded83738b4d744",
            # Shaped like a credential without matching any real key format:
            # a fixture that does match trips GitHub push protection.
            "tok_" + "A1b2C3d4" * 4,
            "dGhpcyBpcyBhIGJhc2U2NCBibG9iIG9mIHNvbWUgbGVuZ3Ro",
        ):
            with self.subTest(text=text):
                self.assertEqual(mode.classify(text), mode.JUNK)

    def test_single_letter(self):
        self.assertEqual(mode.classify("a"), mode.JUNK)

    def test_blank(self):
        for text in ("", "   ", "\n\t "):
            with self.subTest(text=repr(text)):
                self.assertEqual(mode.classify(text), mode.JUNK)


class Word(unittest.TestCase):
    def test_plain_english_words(self):
        for text in ("prefix", "verbose", "recursive", "deprecated", "idempotent"):
            with self.subTest(text=text):
                self.assertEqual(mode.classify(text), mode.WORD)

    def test_surrounding_whitespace_is_ignored(self):
        self.assertEqual(mode.classify("  prefix \n"), mode.WORD)

    def test_hyphenated_and_apostrophed(self):
        for text in ("read-only", "don't", "well-formed"):
            with self.subTest(text=text):
                self.assertEqual(mode.classify(text), mode.WORD)

    def test_words_in_scripts_without_spaces(self):
        # Japanese, Chinese and Korean words never matched the Latin pattern,
        # so they used to fall through to a bare translation instead of an
        # entry — which is the whole point of looking a word up.
        for text in ("既定", "冗長", "デフォルト", "引数", "コンピューター", "기본적으로"):
            with self.subTest(text=text):
                self.assertEqual(mode.classify(text), mode.WORD)

    def test_an_unspaced_sentence_is_not_a_word(self):
        # Without spaces, length is the only thing separating the two.
        for text in ("デフォルトでは一致した行を出力します",
                     "既定ではすべての行を表示します"):
            with self.subTest(text=text):
                self.assertEqual(mode.classify(text), mode.GENERAL)

    def test_punctuation_caught_alongside_the_word(self):
        # Japanese has no spaces, so a mouse selection routinely picks up the
        # punctuation next to the word. Losing the entry over a stray 。 is
        # the difference between a dictionary and a shrug.
        for text in ("既定。", "引数、", "「冗長」", "デフォルト，"):
            with self.subTest(text=text):
                self.assertEqual(mode.classify(text), mode.WORD)

    def test_latin_is_left_alone_by_that_rule(self):
        # A space already delimits Latin words, so trailing punctuation there
        # means the end of a sentence was grabbed, not a word.
        for text in ("Hello!", "done.", "wait..."):
            with self.subTest(text=text):
                self.assertEqual(mode.classify(text), mode.GENERAL)

    def test_the_boundary_is_the_character_count(self):
        self.assertEqual(mode.classify("あ" * mode._MAX_UNSPACED_WORD), mode.WORD)
        self.assertEqual(mode.classify("あ" * (mode._MAX_UNSPACED_WORD + 1)), mode.GENERAL)




class General(unittest.TestCase):
    def test_word_lookup_off_still_translates_rather_than_explains(self):
        # Someone who disabled the dictionary wants a translation, not an
        # explanation — a real word must not fall through to TERM.
        self.assertEqual(mode.classify("prefix", word_lookup=False), mode.GENERAL)


class Term(unittest.TestCase):
    """Bare identifiers: reproduced and explained, never defined."""

    def test_technical_identifiers(self):
        for text in ("--global", "core.autocrlf", "$PATH", "/usr/bin",
                     "LC_ALL", "argv[0]", "std::vector", "SIGTERM"):
            with self.subTest(text=text):
                self.assertEqual(mode.classify(text), mode.TERM)

    def test_all_caps_is_never_a_dictionary_word(self):
        for text in ("SIGTERM", "PATH", "TODO", "FIXME"):
            with self.subTest(text=text):
                self.assertEqual(mode.classify(text), mode.TERM)

    def test_trailing_punctuation_does_not_make_a_word_an_identifier(self):
        for text in ("Hello!", "Really?", "done.", "wait..."):
            with self.subTest(text=text):
                self.assertEqual(mode.classify(text), mode.GENERAL)

    def test_anything_with_whitespace_is_not_a_bare_identifier(self):
        for text in ("git config --global", "pull request"):
            with self.subTest(text=text):
                self.assertEqual(mode.classify(text), mode.GENERAL)

    def test_sentences(self):
        for text in (
            "By default, grep prints the matching lines.",
            "Permission denied",
            "fatal: not a git repository",
        ):
            with self.subTest(text=text):
                self.assertEqual(mode.classify(text), mode.GENERAL)

    def test_commands(self):
        for text in (
            "git config --global core.autocrlf false",
            "chmod 755 script.sh",
            "grep -rn 'pattern' .",
        ):
            with self.subTest(text=text):
                self.assertEqual(mode.classify(text), mode.GENERAL)

    def test_multi_word_phrases(self):
        self.assertEqual(mode.classify("pull request"), mode.GENERAL)

    def test_urls_are_not_mistaken_for_blobs(self):
        self.assertEqual(
            mode.classify("https://example.com/some/fairly/long/path/here"), mode.TERM
        )

    def test_non_latin_prose(self):
        for text in ("デフォルトでは、grep は一致した行を出力します。",
                     "기본적으로 모든 줄을 출력합니다"):
            with self.subTest(text=text):
                self.assertEqual(mode.classify(text), mode.GENERAL)

    def test_multiline_output(self):
        self.assertEqual(mode.classify("DESCRIPTION\n  grep searches..."), mode.GENERAL)

    def test_a_table_row_is_content_not_decoration(self):
        # Separators are sparse here; a border is mostly decoration. Selecting
        # a row to read it is legitimate, so it must survive the junk filter.
        for text in ("│ name │ value │", "| Capability | Mechanism | Status |"):
            with self.subTest(text=text):
                self.assertEqual(mode.classify(text), mode.GENERAL)


class Truncate(unittest.TestCase):
    def test_short_text_is_untouched(self):
        text, cut = mode.truncate("hello")
        self.assertEqual(text, "hello")
        self.assertFalse(cut)

    def test_text_at_the_limit_is_untouched(self):
        text, cut = mode.truncate("x" * mode.MAX_INPUT_CHARS)
        self.assertFalse(cut)
        self.assertEqual(len(text), mode.MAX_INPUT_CHARS)

    def test_oversized_text_is_cut_and_flagged(self):
        text, cut = mode.truncate("x" * (mode.MAX_INPUT_CHARS + 1))
        self.assertTrue(cut)
        self.assertEqual(len(text), mode.MAX_INPUT_CHARS)


if __name__ == "__main__":
    unittest.main()
