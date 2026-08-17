"""One-command keybinding setup, and its undo.

Herdr's manifest has no place to declare a keybinding — `PluginManifestAction`
carries id, title, description, command and platforms, and nothing else — so a
plugin cannot ship its own keys. Every user has to add `[[keys.command]]`
blocks by hand, which for three actions means copying three near-identical
stanzas and getting the action ids exactly right.

    herdr plugin action invoke lens-setup
    herdr plugin action invoke lens-remove-keys

Invoked explicitly, because this writes to a file Herdr Lens does not own.

The keys go inside a marked block, and only that block is ever rewritten. That
is what makes the setup both repeatable and reversible: re-running it replaces
its own block rather than appending a second copy, and removing is exact
instead of asking the user to find our lines among theirs. Everything outside
the markers — comments, ordering, every binding the user wrote — is carried
across untouched.

Prior art: jhochenbaum/herdr-hunk-diff solves the same problem with a managed
block and a matching remove action. Both ideas are taken from it, as is the
refusal to write into a config that does not parse.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

# One family, one mnemonic letter each. `e` and `s` are Herdr's own
# (edit_scrollback, settings), so those letters are only reachable behind a
# modifier — alt rather than shift, because Herdr uses prefix+alt itself for
# secondary actions and shift reads as "the same key, louder".
#
# `prefix+t` is listed too, and deliberately: fumbling alt on `prefix+alt+t`
# lands there and still translates. There is no equivalent safety net for e and
# s — a fumbled alt reaches `prefix+e`, which opens the scrollback in $EDITOR.
# That is recoverable but startling, and worth knowing before it happens.
#
# `ctrl+t` is not here. It is the nicest key for the most frequent action, but
# it is `transpose-chars` in readline — a trade to offer in the README, not to
# make on every user's behalf.
BINDINGS = [
    ("prefix+alt+t", "lens-translate", "Translate selection"),
    ("prefix+alt+e", "lens-explain", "Explain selection"),
    ("prefix+alt+s", "lens-summarize", "Summarise selection"),
    ("prefix+t", "lens-translate", "Translate selection"),
]

BEGIN = "# >>> herdr-lens keybindings >>>"
END = "# <<< herdr-lens keybindings <<<"


class SetupError(Exception):
    """A refusal the user can act on."""


def config_path(env: dict[str, str] | None = None) -> Path | None:
    """Herdr's own config precedence, mirrored.

    Guessing `~/.config/herdr/config.toml` is wrong for anyone who sets either
    variable: the keys would be written to a file Herdr never reads, and the
    failure is a keypress that does nothing. Returns None when Herdr is
    configured to read no file at all.
    """
    env = os.environ if env is None else env
    override = env.get("HERDR_CONFIG_PATH")
    if override is not None:
        # Set-but-empty means Herdr loads no config, so there is nowhere to write.
        return Path(override) if override else None
    xdg = env.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "herdr/config.toml"


def strip_block(text: str) -> str:
    """Remove our managed block, leaving everything else exactly as it was."""
    start = text.find(BEGIN)
    if start == -1:
        return text
    end = text.find(END, start)
    if end == -1:
        return text
    after = end + len(END)
    if after < len(text) and text[after] == "\n":
        after += 1
    # Drop the blank line we inserted before the block, so repeated runs do
    # not accumulate whitespace.
    if start >= 1 and text[start - 1] == "\n" and start >= 2 and text[start - 2] == "\n":
        start -= 1
    return text[:start] + text[after:]


def existing(text: str) -> dict[str, str]:
    """Map key chord -> whatever holds it. Raises if the config does not parse."""
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise SetupError(
            f"{exc}\n\n"
            "Herdr discards a config it cannot parse and falls back to its "
            "defaults, so keys added here would not load. It also hides the "
            "bindings you already have, and this never overwrites one.\n"
            "Nothing was changed. Run `herdr config check`, fix the file, and "
            "try again."
        ) from exc
    commands = (raw.get("keys") or {}).get("command") or []
    return {
        # Built-in bindings carry `type` and no `command`; naming what holds
        # the key is the difference between a useful report and "something".
        entry["key"]: entry.get("command") or entry.get("type", "")
        for entry in commands
        if isinstance(entry, dict) and "key" in entry
    }


def block(bindings: list[tuple[str, str, str]]) -> str:
    lines = [BEGIN, "# Written by `herdr plugin action invoke lens-setup`.",
             "# Edit freely — re-running setup replaces this whole block."]
    for key, command, description in bindings:
        lines += ["", "[[keys.command]]", f'key = "{key}"',
                  'type = "plugin_action"', f'command = "{command}"',
                  f'description = "{description}"']
    lines += [END, ""]
    return "\n".join(lines)


def plan(text: str) -> tuple[list[tuple[str, str, str]], list[str]]:
    """Decide what can be installed, against the config minus our own block.

    Our own block is excluded first, so a re-run sees the user's bindings only
    and never reports a conflict with itself.
    """
    bound = existing(strip_block(text))
    add, clash = [], []
    for key, command, description in BINDINGS:
        if key in bound:
            clash.append(f"{key} is already bound to {bound[key] or 'something else'}")
        else:
            add.append((key, command, description))
    return add, clash


def backup(path: Path) -> Path | None:
    """Copy the config aside before rewriting it."""
    if not path.exists():
        return None
    target = path.with_suffix(path.suffix + ".lens-backup")
    try:
        shutil.copyfile(path, target)
    except OSError:
        return None
    return target


def reload_herdr() -> bool:
    herdr = os.environ.get("HERDR_BIN_PATH") or "herdr"
    try:
        result = subprocess.run(
            [herdr, "server", "reload-config"],
            capture_output=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def install() -> int:
    path = config_path()
    if path is None:
        raise SetupError(
            "HERDR_CONFIG_PATH is set to an empty value, so Herdr reads no "
            "config file and there is nowhere to put the keys.\n"
            "Unset it, or point it at the file you want."
        )
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    add, clash = plan(text)

    for line in clash:
        print(f"  skipped   {line}")
    if not add:
        print("\nEvery key is already taken by something else. "
              "Free one, or bind the actions to keys of your own.")
        return 1

    saved = backup(path)
    body = strip_block(text).rstrip("\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text((body + "\n\n" if body else "") + block(add), encoding="utf-8")

    for key, command, _ in add:
        print(f"  bound     {key} → {command}")
    print(f"\nWrote {path}")
    if saved:
        print(f"Previous version saved to {saved.name}")
    print("Reloaded Herdr. The keys work now." if reload_herdr()
          else "Run `herdr server reload-config` to pick them up.")
    if clash:
        print("\nBind the skipped actions to keys of your own, or free the key.")
    return 0


def remove() -> int:
    path = config_path()
    if path is None or not path.exists():
        print("Nothing to remove.")
        return 0
    text = path.read_text(encoding="utf-8")
    stripped = strip_block(text)
    if stripped == text:
        print("No Lens keybindings found. Nothing to remove.")
        return 0
    saved = backup(path)
    path.write_text(stripped, encoding="utf-8")
    print(f"Removed the Lens keybindings from {path}")
    if saved:
        print(f"Previous version saved to {saved.name}")
    print("Reloaded Herdr." if reload_herdr()
          else "Run `herdr server reload-config` to pick that up.")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        return remove() if argv and argv[0] == "remove" else install()
    except SetupError as exc:
        print(f"herdr-lens: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
