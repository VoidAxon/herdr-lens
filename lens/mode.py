"""Deciding what the user actually selected.

Three outcomes, decided locally in microseconds — no network, no model call:

- `JUNK`    box-drawing, progress bars, hashes. Rejected instantly.
- `WORD`    a natural-language word, in any script. Gets a dictionary entry.
- `TERM`    a bare identifier — `SIGTERM`, `--global`, `$PATH`. Gets explained.
- `GENERAL` everything else: prose to translate, or a command line to explain.

`TERM` exists because one prompt cannot reliably do two jobs. Asked to either
translate *or* explain depending on the input, the model guesses, and bare
identifiers came back untouched about half the time. Splitting the decision out
— trivially detectable here, since an identifier is a single token with no
whitespace — lets each prompt do exactly one thing.

The split is deliberate about where judgment lives. Whether something *looks*
like a word is a cheap, deterministic check, so code does it. Whether a given
word is really a command name, and whether a passage is prose or a shell
invocation, are fuzzy — the model does those, and the word prompt carries an
explicit escape hatch for the cases this file waves through.
"""

from __future__ import annotations

import re

JUNK = "junk"
WORD = "word"
TERM = "term"
GENERAL = "general"

# Not detected — requested. Which key was pressed says what the user wants,
# and no amount of looking at the text could tell you instead.
EXPLAIN = "explain"
SUMMARIZE = "summarize"

# Above this, a selection is almost certainly a stray select-all rather than
# something someone means to read in a popup.
MAX_INPUT_CHARS = 8000

# Summarising is the one case where selecting a lot is the point, so it gets
# its own ceiling — a build log worth summarising is longer than a paragraph
# worth translating.
MAX_SUMMARY_CHARS = 40000

# Two to thirty-two letters, allowing an internal hyphen or apostrophe.
_WORD_RE = re.compile(r"^[A-Za-z][A-Za-z'\-]{1,31}$")

# Japanese, Chinese, and Korean do not put spaces between words, so a single
# token carries no clue about whether it is a word or a whole sentence —
# length is the only available signal. Eight characters comfortably holds a
# compound noun (コンピューター) while excluding anything sentence-shaped.
_MAX_UNSPACED_WORD = 8

# An unbroken 32+ character run of identifier-safe characters is a hash, UUID,
# access token, or base64 blob — never prose.
_BLOB_RE = re.compile(r"^[A-Za-z0-9+/=_-]{32,}$")

# Below this share of letters, the selection is mostly punctuation.
_MIN_LETTER_RATIO = 0.25

# Punctuation that can sit at the edge of ordinary prose.
_EDGE_PUNCTUATION = "!?.,;:\"'()…" + "。、，．：；！？「」『』（）〈〉《》…～"

# Box drawing, block elements, geometric shapes — the characters TUIs draw
# frames and progress bars with. A stray label inside a border keeps a high
# letter ratio, so these need their own signal.
_DECORATION_RANGES = ((0x2500, 0x257F), (0x2580, 0x259F), (0x25A0, 0x25FF))

# A table row uses a few separators; a border is mostly decoration. The line
# between them sits well above the former and well below the latter.
_MAX_DECORATION_RATIO = 0.40


def _is_decoration(ch: str) -> bool:
    point = ord(ch)
    return any(low <= point <= high for low, high in _DECORATION_RANGES)


def classify(text: str, word_lookup: bool = True) -> str:
    """Route a selection to the handling it deserves.

    Junk rules are deliberately conservative: waving something through costs
    one wasted request, while a false rejection reads as the plugin being
    broken.
    """
    stripped = text.strip()
    if not stripped:
        return JUNK

    letters = sum(1 for ch in stripped if ch.isalpha())
    if letters < 2:
        return JUNK

    visible = sum(1 for ch in stripped if not ch.isspace())
    if visible and letters / visible < _MIN_LETTER_RATIO:
        return JUNK

    decoration = sum(1 for ch in stripped if _is_decoration(ch))
    if visible and decoration / visible >= _MAX_DECORATION_RATIO:
        return JUNK

    if _BLOB_RE.match(stripped):
        return JUNK

    if word_lookup and _is_word(stripped):
        return WORD

    if not any(ch.isspace() for ch in stripped):
        # An identifier carries something a word never does: *internal*
        # punctuation, digits, or an ALL-CAPS shape. Trailing punctuation does
        # not make a word an identifier — "Hello!" is prose — and a token of
        # pure letters is a word in any script, so `기본적으로` must not be
        # read as a symbol just because the word pattern is Latin-only.
        core = stripped.strip(_EDGE_PUNCTUATION)
        if core.isalpha() and not (core.isupper() and core.isascii()):
            return GENERAL
        return TERM

    return GENERAL


def _is_word(token: str) -> bool:
    """Would a dictionary entry be the useful answer for this token?"""
    # ALL-CAPS single tokens are abbreviations and signal names (SIGTERM,
    # PATH), not dictionary words — they fall through to be explained.
    if token.isupper() and token.isascii():
        return False
    if _WORD_RE.match(token):
        return True
    # A script without spaces makes it hard to select a word without catching
    # the punctuation beside it, so judge the core rather than the raw token.
    # Latin is left alone: there, a space already delimits the word, so
    # trailing punctuation means the user grabbed the end of a sentence.
    core = token.strip(_EDGE_PUNCTUATION)
    return (
        not core.isascii()
        and core.isalpha()
        and 2 <= len(core) <= _MAX_UNSPACED_WORD
    )


def is_junk(text: str) -> bool:
    """Cheap pre-check for the popup's fast path.

    Every junk rule runs before the word rule, so the answer never depends on
    the `word_lookup` setting — which means this can be asked before the config
    file is read, keeping the config load off the path to first paint.
    """
    return classify(text) == JUNK


def input_limit(mode: str) -> int:
    return MAX_SUMMARY_CHARS if mode == SUMMARIZE else MAX_INPUT_CHARS


def truncate(text: str, limit: int = MAX_INPUT_CHARS) -> tuple[str, bool]:
    """Cap the payload. Returns (text, was_truncated).

    Translating the head of an over-long selection beats refusing it: selecting
    a long passage of documentation is a legitimate thing to do.
    """
    if len(text) <= limit:
        return text, False
    return text[:limit], True
