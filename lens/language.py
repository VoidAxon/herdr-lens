"""Working out what language a selection is written in.

Detecting the language of a *single word* is not solvable by detection. Kana
and Hangul identify themselves, but a Han-only token is genuinely ambiguous:
`東京` is valid Japanese and valid Traditional Chinese, and purpose-built
libraries say plainly that this needs long passages to work at all.

So this module does not try to be a language detector. It combines two things
that are each cheap and certain:

- **Script**, which is decidable from the characters themselves.
- **The candidate languages the user configured**, which shrink the hypothesis
  space from "every language" to a handful.

Together they usually leave exactly one answer. With `source_language =
["en", "ja"]`, a Han-only word cannot be English, so it is Japanese — no
guessing involved. When one answer does not fall out, the narrowed candidates
are handed to the model rather than a coin flip.
"""

from __future__ import annotations

AUTO = "auto"

NAMES = {
    "en": "English", "ja": "Japanese", "ko": "Korean",
    "zh": "Chinese", "zh-CN": "Chinese (Simplified)", "zh-TW": "Chinese (Traditional)",
    "fr": "French", "de": "German", "es": "Spanish", "it": "Italian",
    "pt": "Portuguese", "ru": "Russian",
}

# Which scripts each language is written in — the part that does the work.
_HAN = {"ja", "zh", "zh-CN", "zh-TW"}
_KANA = {"ja"}
_HANGUL = {"ko"}
_LATIN = {"en", "fr", "de", "es", "it", "pt"}

_RANGES = {
    "kana": ((0x3040, 0x309F), (0x30A0, 0x30FF), (0x31F0, 0x31FF), (0xFF66, 0xFF9D)),
    "hangul": ((0x1100, 0x11FF), (0x3130, 0x318F), (0xAC00, 0xD7A3)),
    "han": ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF)),
    "cyrillic": ((0x0400, 0x04FF),),
    "latin": ((0x0041, 0x005A), (0x0061, 0x007A), (0x00C0, 0x024F)),
}

_SCRIPT_LANGUAGES = {
    "kana": _KANA, "hangul": _HANGUL, "han": _HAN,
    "latin": _LATIN, "cyrillic": {"ru"},
}


def scripts(text: str) -> set[str]:
    """Which writing systems appear in `text`."""
    found = set()
    for ch in text:
        point = ord(ch)
        for name, ranges in _RANGES.items():
            if any(low <= point <= high for low, high in ranges):
                found.add(name)
                break
    return found


def dominant_script(text: str) -> str | None:
    """The script that decides the language, or None if nothing does.

    Kana and Hangul outrank Han: Japanese mixes kana with kanji, so any kana
    at all settles it, whereas Han alone settles nothing.
    """
    present = scripts(text)
    for script in ("kana", "hangul", "cyrillic", "han", "latin"):
        if script in present:
            return script
    return None


def normalise(configured) -> list[str]:
    """Accept a single code or a list, and drop `auto` entries."""
    if not configured:
        return []
    codes = [configured] if isinstance(configured, str) else list(configured)
    return [c for c in codes if isinstance(c, str) and c and c != AUTO]


def resolve(text: str, configured=None) -> str:
    """Describe the source language for the prompt.

    Returns `"auto"` when nothing useful can be said, a single language name
    when script and candidates agree on one, or a short disjunction when they
    narrow it without settling it.
    """
    candidates = normalise(configured)
    script = dominant_script(text)

    if script is None:
        return _phrase(candidates) if candidates else AUTO

    plausible = _SCRIPT_LANGUAGES.get(script, set())
    if candidates:
        narrowed = [c for c in candidates if c in plausible]
        # A candidate list that rules everything out is a misconfiguration, not
        # a reason to assert something false — fall back to the script alone.
        if narrowed:
            return _phrase(narrowed)

    if len(plausible) == 1:
        return NAMES.get(next(iter(plausible)), AUTO)
    return _script_hint(script)


def _phrase(codes: list[str]) -> str:
    names = [NAMES.get(c, c) for c in dict.fromkeys(codes)]
    if len(names) == 1:
        return names[0]
    return " or ".join((", ".join(names[:-1]), names[-1]))


def _script_hint(script: str) -> str:
    if script == "han":
        # The honest statement of the ambiguity, rather than a guess.
        return "Chinese or Japanese, written in Han characters with no kana"
    languages = _SCRIPT_LANGUAGES.get(script, ())
    if len(languages) == 1:
        return NAMES.get(next(iter(languages)), AUTO)
    # Listing every language that happens to use the Latin alphabet tells the
    # model nothing it cannot see for itself, and crowds the prompt.
    return AUTO


def display(code: str) -> str:
    """The language's name, for putting in a prompt.

    Prompts are read by a model, not by a config parser: `zh-CN` is an
    identifier it has to decode, `Chinese (Simplified)` is an instruction.
    Unknown codes pass through unchanged — a code the model can guess at beats
    dropping the instruction entirely.
    """
    if not code:
        return code
    return NAMES.get(code) or NAMES.get(code.split("-")[0].lower()) or code
