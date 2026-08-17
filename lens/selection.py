"""Getting the user's selected text.

Three tiers, in order:

1. `clicked_url` — the text a link handler matched. Only present when the
   action was fired by a click, and then it is exactly what the user pointed
   at, so nothing else can beat it.
2. `selected_text` from Herdr's invocation context. Verified absent on the
   keybinding path in Herdr 0.8.0, but the field exists in the API schema and
   is populated for other invocation sources, so it is tried before falling
   back and will start working for free if Herdr fills it in later.
3. The system clipboard. Herdr's `copy_on_select` defaults to true, so a mouse
   selection is already there by the time the user presses the key.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass


@dataclass
class Selection:
    text: str
    source: str  # "click" | "context" | "clipboard" | "none"
    backend: str = ""


def _context(env: dict[str, str]) -> dict:
    raw = env.get("HERDR_PLUGIN_CONTEXT_JSON")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def acquire(env: dict[str, str] | None = None, clipboard=None) -> Selection:
    env = os.environ if env is None else env
    if clipboard is None:
        from . import clipboard as clipboard_module

        clipboard = clipboard_module

    ctx = _context(env)

    # A click tells us precisely what the user pointed at.
    clicked = ctx.get("clicked_url") or env.get("HERDR_PLUGIN_CLICKED_URL") or ""
    if clicked.strip():
        return Selection(text=clicked, source="click")

    text = ctx.get("selected_text") or ""
    if text.strip():
        return Selection(text=text, source="context")

    text, backend = clipboard.read(env)
    if text.strip():
        return Selection(text=text, source="clipboard", backend=backend)

    return Selection(text="", source="none")
