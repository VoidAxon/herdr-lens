"""Colour for the popup body.

Two invariants make this safe to bolt onto a width-sensitive renderer:

1. **Styling is purely additive.** Stripping the escapes from a styled line
   returns the original line exactly. Wrapping, padding, and truncation all
   run on plain text, so the layout cannot be thrown off by colour.
2. **Spans are computed before any escape is inserted**, then emitted in one
   pass. Substituting escapes and then matching again over the result is how
   nesting bugs and half-closed sequences happen.

Styling splits in two, because the two kinds cannot be decided the same way:

- **Line roles** are decided on the *source* lines, before wrapping, and every
  wrapped row inherits its source line's role. A wrapped row cannot be
  classified alone — the continuation of `1. An example...` and the
  translation beneath it carry the same indent, and only the source structure
  separates them.
- **Inline spans** are matched on the wrapped row, since they are positional.

Colours are the basic ANSI set rather than 256-colour or truecolor, so the
user's terminal theme decides the actual hues. Each one carries one meaning:

    bold      the word being looked up, and the model's own emphasis
    cyan      anything reproduced verbatim — code, identifiers, paths, codes
    magenta   part of speech
    yellow    the markers you scan by — list bullets, example numbers
    dim       secondary text — pronunciation, and an example's translation
"""

from __future__ import annotations

import re
from collections.abc import Callable

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"
YELLOW = "\033[33m"
REVERSE = "\033[7m"

HEADWORD = BOLD
IDENTIFIER = BOLD + CYAN
CODE = CYAN
PART_OF_SPEECH = MAGENTA
MARKER = YELLOW
SECONDARY = DIM
# Inverse video for a hit, the convention every pager uses. The one you are
# standing on gets a background instead, so `n` visibly lands somewhere rather
# than just changing a counter.
MATCH = REVERSE
MATCH_CURRENT = "\033[43m\033[30m"

# `--verbose`, `core.autocrlf` — the model marks these itself, and the
# backticks stay visible so the styled line keeps its original width.
#
# Scanned with carried state rather than a regex per row, because a span can
# wrap. Matching `` `[^`]+` `` on a row that holds only half a span pairs the
# closing backtick with the *next* opening one and colours the text between
# two unrelated spans — visible as `` `、` `` turning cyan between two flags.

# The model's own emphasis. Like backticks, the asterisks stay visible.
_BOLD_SPAN = re.compile(r"\*\*[^*\n]+\*\*")

# Paths the model reproduced without marking them, which is most of them in a
# summary. Deliberately narrow: either an explicit `./` or `../`, or an
# absolute path of at least two segments. A bare `/` between two words — `和/或`,
# `and/or` — must not read as a path.
_PATH = re.compile(
    r"(?:\.{1,2}/[A-Za-z0-9_.\-/]+|/(?:[A-Za-z0-9_.\-]+/)+[A-Za-z0-9_.\-]+)"
)

# `TS2345`, `E404` — error codes appear bare in build output.
_ERROR_CODE = re.compile(r"\b[A-Z]{1,5}\d{3,5}\b")

# The pronunciation on a dictionary entry's first line.
_IPA = re.compile(r"/[^/\s][^/]*/")

# `1.` / `2.` opening a numbered example.
_EXAMPLE_NUMBER = re.compile(r"^\s*\d+\.")

# `- item`, `* item`, `• item`. The trailing space keeps `**bold**` out.
_BULLET = re.compile(r"^\s*[-*•]\s")

# `n.  meaning` / `形容詞  意思` — the label, then the column gap the prompt
# asks for. Matching the gap rather than a list of abbreviations is what makes
# this work for a target language whose grammar terms are not `adj.`.
_LABELLED = re.compile(r"^(\S+)\s{2,}\S")

# The identifier a TERM entry reproduces on its first line.
_LEADING_TOKEN = re.compile(r"^\s*(\S+)")

Span = tuple[int, int, str]


def _apply(line: str, spans: list[Span], base: str = "") -> str:
    """Emit `line` with non-overlapping styled spans, over an optional base.

    `base` styles the whole line. Each inner span re-opens it after its own
    reset, since a reset would otherwise end the base early and leave the rest
    of the line unstyled.
    """
    if not spans:
        return f"{base}{line}{RESET}" if base else line
    spans = sorted(spans)
    out, cursor = [base] if base else [], 0
    for start, end, code in spans:
        if start < cursor:  # an earlier span already claimed these columns
            continue
        out.append(line[cursor:start])
        out.append(f"{code}{line[start:end]}{RESET}{base}")
        cursor = end
    out.append(line[cursor:])
    if base:
        out.append(RESET)
    return "".join(out)


# -- line roles, decided on source lines ---------------------------------

HEAD, POS, EXAMPLE, TRANSLATION, PLAIN = "head", "pos", "example", "translation", ""


def _word_roles(lines: list[str]) -> list[str]:
    roles = [PLAIN] * len(lines)
    if lines:
        roles[0] = HEAD
    in_example = False
    for i, line in enumerate(lines[1:], start=1):
        if not line.strip():
            continue  # a blank line does not end an example block
        if _EXAMPLE_NUMBER.match(line):
            roles[i], in_example = EXAMPLE, True
        elif in_example and line[:1].isspace():
            roles[i] = TRANSLATION
        else:
            in_example = False
            if _LABELLED.match(line):
                roles[i] = POS
    return roles


def _term_roles(lines: list[str]) -> list[str]:
    return [HEAD if i == 0 else PLAIN for i in range(len(lines))]


def roles(mode: str, text: str) -> list[str]:
    builder = {"word": _word_roles, "term": _term_roles}.get(mode)
    lines = text.split("\n")
    return builder(lines) if builder else [PLAIN] * len(lines)


# -- inline spans, matched on the wrapped row -----------------------------


def _code_spans(
    line: str, inside: bool, continues: bool
) -> tuple[list[Span], bool]:
    """Backtick spans on one row.

    `inside` says the row opens inside a span carried from the previous row;
    `continues` says another row of the same source line follows. Returns the
    spans and whether the row *ends* inside a span.

    `continues` is what separates the two reasons a row can end on an unclosed
    backtick. A span that wraps must colour to the end of the row and carry on;
    a stray backtick in prose — "an ` unmatched tick" — must be left alone. On
    the row itself they look identical, and only the source structure says
    whether there is a next row for the span to continue into.
    """
    spans: list[Span] = []
    cursor = 0
    if inside:
        # Skip the continuation indent: colouring leading spaces shows nothing
        # and makes the span look mispositioned when selected.
        start = len(line) - len(line.lstrip())
        close = line.find("`", cursor)
        if close == -1:
            return ([(start, len(line), CODE)] if start < len(line) else []), True
        spans.append((start, close + 1, CODE))
        cursor = close + 1
    while True:
        open_at = line.find("`", cursor)
        if open_at == -1:
            return spans, False
        close = line.find("`", open_at + 1)
        if close == -1:
            if not continues:
                return spans, False  # a stray tick, not a wrapped span
            spans.append((open_at, len(line), CODE))
            return spans, True
        spans.append((open_at, close + 1, CODE))
        cursor = close + 1


def _inline(
    line: str, inside: bool = False, continues: bool = False
) -> tuple[list[Span], bool]:
    spans, ends_inside = _code_spans(line, inside, continues)
    spans += [(m.start(), m.end(), BOLD) for m in _BOLD_SPAN.finditer(line)]
    spans += [(m.start(), m.end(), CODE) for m in _PATH.finditer(line)]
    spans += [(m.start(), m.end(), CODE) for m in _ERROR_CODE.finditer(line)]
    bullet = _BULLET.match(line)
    if bullet:
        spans.append((bullet.start(), bullet.end() - 1, MARKER))
    return spans, ends_inside


def _role_spans(role: str, line: str, mode: str) -> tuple[list[Span], str]:
    """Spans and base style for a line, given its source line's role."""
    if role == HEAD:
        if mode == "term":
            token = _LEADING_TOKEN.match(line)
            return ([(token.start(1), token.end(1), IDENTIFIER)] if token else []), ""
        spans = []
        token = _LEADING_TOKEN.match(line)
        if token:
            spans.append((token.start(1), token.end(1), HEADWORD))
        ipa = _IPA.search(line)
        if ipa:
            spans.append((ipa.start(), ipa.end(), SECONDARY))
        return spans, ""
    if role == POS:
        label = _LABELLED.match(line)
        return ([(label.start(1), label.end(1), PART_OF_SPEECH)] if label else []), ""
    if role == EXAMPLE:
        number = _EXAMPLE_NUMBER.match(line)
        return ([(number.start(), number.end(), MARKER)] if number else []), ""
    if role == TRANSLATION:
        # The whole line, so an example and its translation stop looking
        # identical in a narrow popup.
        return [], SECONDARY
    return [], ""


def _greedy(haystack: str, needle: str, start: int) -> list[int]:
    at, found = start, []
    for ch in needle:
        at = haystack.find(ch, at)
        if at == -1:
            return []
        found.append(at)
        at += 1
    return found


def _subsequence(haystack: str, needle: str) -> list[int]:
    """Positions of `needle`'s characters in order, or [] if not all present.

    Tried from every possible first character and the tightest span wins.
    Plain leftmost-greedy would answer `clts` on `./src/api/client.ts` with the
    `c` of `src`, scattering the highlight across the whole path instead of
    landing on `client.ts` — the same characters, but unreadable as an
    explanation of why the line matched.
    """
    best = []
    for i, ch in enumerate(haystack):
        if ch != needle[0]:
            continue
        found = _greedy(haystack, needle, i)
        if found and (not best or found[-1] - found[0] < best[-1] - best[0]):
            best = found
        if best and best[-1] - best[0] == len(needle) - 1:
            break  # contiguous; nothing can be tighter
    return best


def spans_in(line: str, query: str) -> list[tuple[int, int]]:
    """Where `query` occurs in `line`, as (start, end) pairs."""
    return [(a, b) for a, b, _ in _match_spans(line, query)]


def matches(line: str, query: str) -> bool:
    """Does `line` match, contiguously or loosely?"""
    if not query:
        return False
    haystack, needle = line.lower(), query.lower()
    return needle in haystack or bool(_subsequence(haystack, needle))


def _match_spans(line: str, query: str, code: str = MATCH) -> list[Span]:
    """Where `query` matched, case-insensitively.

    Contiguous occurrences first; only if there are none does it fall back to a
    loose subsequence, and then the whole span from the first matched character
    to the last is marked as one run.

    Marking each matched character instead would be the literal truth —
    `./src/api/[c][l]ien[t].t[s]` — but it reads as damage rather than as an
    answer. The span covers a few characters that did not match, and that is
    the better lie: the point of the highlight is to find the line again, not
    to explain the algorithm, and those characters are inside the thing you
    were looking for anyway.
    """
    if not query:
        return []
    haystack, needle = line.lower(), query.lower()
    spans, at = [], haystack.find(needle)
    while at != -1:
        spans.append((at, at + len(needle), code))
        at = haystack.find(needle, at + len(needle))
    if spans:
        return spans
    found = _subsequence(haystack, needle)
    return [(found[0], found[-1] + 1, code)] if found else []


def styler(mode: str, text: str = "", sources: list[int] | None = None,
           highlight: str = "", current: tuple[int, int] | None = None):
    """Return a per-line styling function for `mode`.

    `text` is the unwrapped result and `sources` maps each wrapped row to its
    source line, so a role decided once is applied to every row of that line.
    Without them only inline spans apply, which is always safe.
    """
    line_roles = roles(mode, text) if text else []
    # Carried across the rows of one source line, reset at each new one: a
    # backtick span belongs to a single line of the model's output.
    open_span: dict[int, bool] = {}

    def style(line: str, row: int) -> str:
        index = sources[row] if sources and row < len(sources) else row
        role = line_roles[index] if index < len(line_roles) else PLAIN
        spans, base = _role_spans(role, line, mode)
        # Inline spans apply in every mode and every role; a translation of
        # prose often quotes the command it left untranslated.
        continues = bool(
            sources and row + 1 < len(sources) and sources[row + 1] == index
        )
        inline, ends_inside = _inline(line, open_span.get(row, False), continues)
        spans += inline
        # A hit wins every overlap: it is the thing being looked for, and a
        # code span swallowing it would hide the answer.
        # Marked per occurrence, not per line: a row can hold several, and
        # painting all of them as "current" points at more than one thing.
        hits = [
            (a, b, MATCH_CURRENT if current == (row, a) else MATCH)
            for a, b, _ in _match_spans(line, highlight)
        ]
        if hits:
            spans = [s for s in spans
                     if not any(a < s[1] and s[0] < b for a, b, _ in hits)]
            spans += hits
        open_span[row + 1] = ends_inside and continues
        return _apply(line, spans, base)

    return style


_ESCAPE = re.compile(r"\033\[[0-9;]*m")


def strip(text: str) -> str:
    """Remove styling — the inverse the invariant above is stated in terms of."""
    return _ESCAPE.sub("", text)


def plain_style(line: str, row: int) -> str:
    return line
