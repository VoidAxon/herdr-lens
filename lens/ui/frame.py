"""Terminal rendering: width-aware wrapping, scrolling, and the popup layout.

Pure string manipulation — `render` returns the frame as text, so every case
here is testable without a terminal. Herdr draws the popup's own border, so
this fills the pane rather than drawing a second box.
"""

from __future__ import annotations

import re
import unicodedata

CLEAR = "\033[2J\033[H"
HOME = "\033[H"
HIDE_CURSOR = "\033[?25l"
SHOW_CURSOR = "\033[?25h"
DIM = "\033[2m"
RESET = "\033[0m"

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def char_width(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def display_width(text: str) -> int:
    """Columns `text` occupies. CJK counts double, which plain len() misses."""
    return sum(char_width(c) for c in text)


# Escape sequences arriving in *content*. Removed rather than displayed: a
# control sequence is invisible by definition, so stripping it loses nothing a
# reader could have seen — and leaving it in is an injection channel. The model
# is told to reproduce code verbatim, so anything in the selection can come
# back out, and from here it goes straight to the user's real terminal.
#
# The dangerous ones are not hypothetical. OSC 52 writes the system clipboard,
# OSC 0 rewrites the window title, ESC c is a hard terminal reset, and CSI 2J
# clears the screen — enough to plant a command in the clipboard or hide what
# actually happened. They also break the width arithmetic in this file, which
# assumes every character it counts is printable.
_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?")
_CSI = re.compile(r"\x1b\[[0-9;:?<=>!]*[ -/]*[@-~]?")
_STRING_CMD = re.compile(r"\x1b[P^_X][^\x1b]*(?:\x1b\\)?")
_ESC_PAIR = re.compile(r"\x1b.?")
_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

TAB_WIDTH = 8


def sanitize(text: str) -> str:
    """Strip control sequences from text that came from outside.

    Applied to every string this module lays out, so it covers the model's
    reply, a streaming partial, and an error message alike. Normal selections
    are unaffected: Herdr copies the text a pane *renders*, which the terminal
    has already stripped of escapes.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for pattern in (_OSC, _STRING_CMD, _CSI, _ESC_PAIR):
        text = pattern.sub("", text)
    # Tabs are not dangerous but they are width-ambiguous, and every column
    # calculation here counts characters.
    text = text.expandtabs(TAB_WIDTH)
    return _CONTROLS.sub("", text)


def wrap(text: str, width: int) -> list[str]:
    """Wrap to `width` columns, preserving explicit newlines."""
    return [line for line, _ in wrap_indexed(text, width)]


def wrap_indexed(text: str, width: int) -> list[tuple[str, int]]:
    """Wrap, tagging each line with the index of the source line it came from.

    Breaks on spaces where possible and mid-word otherwise, so a long path or
    a run of CJK with no spaces still fits.

    The index exists because a wrapped line cannot be classified on its own: a
    continuation of `1. An example...` and the translation beneath it are both
    indented by the same amount, and only the source structure says which is
    which. Styling decides per source line and lets the wrapped rows inherit.
    """
    if width < 1:
        return [(line, i) for i, line in enumerate(text.splitlines() or [""])]

    lines: list[tuple[str, int]] = []
    for source, paragraph in enumerate(text.split("\n")):
        if not paragraph:
            lines.append(("", source))
            continue

        # Continuation lines inherit the original indent, so an option list or
        # an indented man-page block keeps its shape instead of collapsing to
        # column zero when it wraps.
        indent = paragraph[: len(paragraph) - len(paragraph.lstrip())]
        indent_w = display_width(indent)
        if indent_w > width // 2:  # an indent that leaves no room is no help
            indent, indent_w = "", 0
        paragraph = paragraph[len(indent):]

        # A numbered or bulleted line hangs its continuations under the text,
        # not under the marker — otherwise "1. Enable the flag to see" wraps to
        # a second line that reads as a new item.
        marker = _LIST_MARKER.match(paragraph)
        hang = " " * (display_width(marker.group()) if marker else 0)
        if display_width(indent + hang) > width // 2:
            hang = ""

        first_width = max(1, width - indent_w)
        rest_width = max(1, first_width - len(hang))

        produced = 0

        def emit(chunk: str, source: int = source) -> None:
            nonlocal produced
            lines.append((indent + ("" if produced == 0 else hang) + chunk, source))
            produced += 1

        width_here = first_width
        current, current_w = "", 0
        for token in _tokenize(paragraph):
            # Trailing spaces collapse at a line break, so they must not count
            # toward the fit test — only toward the running width.
            word = token.rstrip(" ")
            spaces = token[len(word):]
            word_w = display_width(word)
            if current and current_w + word_w > width_here:
                emit(current.rstrip())
                current, current_w = "", 0
                width_here = rest_width
            while word_w > width_here:
                head, word = _split_at_width(word, width_here)
                emit(head)
                width_here = rest_width
                word_w = display_width(word)
            current += word + spaces
            current_w += word_w + len(spaces)
        emit(current.rstrip())
    return lines


# `1. `, `2) `, `- `, `* ` — a list marker whose width becomes the hang.
_LIST_MARKER = re.compile(r"^(?:\d+[.)]|[-*•])\s+")


def _tokenize(paragraph: str) -> list[str]:
    """Split into words with their trailing spaces attached."""
    tokens, buf = [], ""
    for ch in paragraph:
        if ch == " ":
            buf += ch
        else:
            if buf.endswith(" "):
                tokens.append(buf)
                buf = ""
            buf += ch
    if buf:
        tokens.append(buf)
    return tokens


# Characters that may not open a line. CJK typesetting forbids it, and a
# stranded 。 or 」 is the most visible way wrapped Chinese looks broken.
_NO_LINE_START = "。、，．：；！？）］｝」』〉》〕｣!?,.:;)]}"


def _split_at_width(text: str, width: int) -> tuple[str, str]:
    total = 0
    for i, ch in enumerate(text):
        w = char_width(ch)
        if total + w > width:
            # Back up rather than push forward, so the head never overflows.
            while i > 1 and text[i] in _NO_LINE_START:
                i -= 1
            return text[:i], text[i:]
        total += w
    return text, ""


def pad(text: str, width: int) -> str:
    return text + " " * max(0, width - display_width(text))


def truncate(text: str, width: int) -> str:
    if display_width(text) <= width:
        return text
    head, _ = _split_at_width(text, max(0, width - 1))
    return head + "…"


# One blank column plus the bar itself.
GUTTER = 2

TRACK = "│"
THUMB = "█"


def body_height(height: int) -> int:
    """Rows available for content: the frame costs a header, footer, and rules."""
    return max(1, max(6, height) - 4)


def layout(text: str, width: int, height: int) -> tuple[list[str], bool, list[int]]:
    """Wrap `text`, giving up columns to a scrollbar only when one is needed.

    Wrapping twice is cheaper than permanently reserving the gutter: short
    results keep the full width, and the second pass is pure string work.

    Returns the lines, whether a gutter was reserved, and each line's source
    index for the styler.
    """
    width = max(20, width)
    text = sanitize(text)
    tagged = wrap_indexed(text, width)
    if len(tagged) > body_height(height):
        tagged = wrap_indexed(text, max(10, width - GUTTER))
        gutter = True
    else:
        gutter = False
    return [line for line, _ in tagged], gutter, [i for _, i in tagged]


def _scrollbar(total: int, visible_rows: int, scroll: int) -> list[str]:
    """A column of track characters with the thumb positioned within it."""
    thumb_len = max(1, round(visible_rows * visible_rows / total))
    thumb_len = min(thumb_len, visible_rows)
    span = max(1, total - visible_rows)
    top = round(scroll / span * (visible_rows - thumb_len))
    return [
        THUMB if top <= row < top + thumb_len else TRACK
        for row in range(visible_rows)
    ]


def render(
    *,
    title: str,
    body: list[str],
    footer: str,
    width: int,
    height: int,
    scroll: int = 0,
    status: str = "",
    gutter: bool = False,
    style=None,
) -> str:
    """Compose one full frame. `body` is already-wrapped lines."""
    width = max(20, width)
    height = max(6, height)
    rows_available = body_height(height)
    text_width = width - GUTTER if gutter else width

    scroll = max(0, min(scroll, max(0, len(body) - rows_available)))
    visible = body[scroll : scroll + rows_available]
    visible += [""] * (rows_available - len(visible))

    overflowing = len(body) > rows_available
    if overflowing:
        position = f"{scroll + 1}-{min(scroll + rows_available, len(body))}/{len(body)}"
        status = f"{status}  {position}" if status else position

    header = pad(truncate(title, width), width)
    if status:
        shown = truncate(status, max(0, width - display_width(title) - 2))
        header = pad(truncate(title, width), width - display_width(shown) - 1) + " " + shown

    bar = (
        _scrollbar(len(body), rows_available, scroll)
        if gutter and overflowing
        else [" "] * rows_available
    )

    content = []
    for offset, (line, mark) in enumerate(zip(visible, bar)):
        # Pad first, style second: colour is additive, so the padded width is
        # already correct and the escapes cannot disturb it.
        plain = pad(truncate(line, text_width), text_width)
        shown = style(plain, scroll + offset) if style else plain
        content.append(shown + (f" {DIM}{mark}{RESET}" if gutter else ""))

    rule = "─" * width
    rows = [
        header,
        f"{DIM}{rule}{RESET}",
        *content,
        f"{DIM}{rule}{RESET}",
        f"{DIM}{pad(truncate(footer, width), width)}{RESET}",
    ]
    # Home rather than clear-screen: every row is padded to full width and the
    # row count is fixed, so overwriting in place avoids redraw flicker.
    return HOME + "\r\n".join(rows)


def max_scroll(body: list[str], height: int) -> int:
    return max(0, len(body) - body_height(height))
