"""Process 3: the same thing, without Herdr.

    lens "By default, grep prints the matching lines."
    kubectl logs pod-xyz | lens --summarize
    lens --explain 'git rebase --onto main feature~3 feature'

The plugin exists because a Herdr keybinding cannot open a popup by itself. The
*translation* never needed Herdr at all — it is a selection, a mode, and a
provider — so this exposes it directly for terminals that are not Herdr, and for
pipes, which no popup can serve.

Output goes to stdout as plain text so it composes with everything else. It
streams only when stdout is a terminal: a pipe wants one clean value, not a
value assembled in front of it.
"""

from __future__ import annotations

import sys

from . import config, mode as modes, viewer
from .providers import ProviderError, build
from .ui import frame

USAGE = """\
lens — read any terminal text in your own language

  lens TEXT                    translate, look up, or identify it
  lens --explain TEXT          say what it is and what it means
  lens --summarize TEXT        condense a long stretch of output
  … | lens                     read the text from stdin

  -t, --target LANG            override the target language
  -h, --help                   this

Configuration is shared with the Herdr plugin:
  ~/.config/herdr/plugins/config/herdr-lens/config.toml
"""

FLAGS = {"--explain": modes.EXPLAIN, "--summarize": modes.SUMMARIZE,
         "--summarise": modes.SUMMARIZE}


def parse(argv: list[str]) -> tuple[str | None, list[str], str]:
    """Return (forced mode, remaining words, target override)."""
    forced, words, target = None, [], ""
    rest = list(argv)
    while rest:
        arg = rest.pop(0)
        if arg in FLAGS:
            forced = FLAGS[arg]
        elif arg in ("-t", "--target"):
            target = rest.pop(0) if rest else ""
        elif arg.startswith("--target="):
            target = arg.split("=", 1)[1]
        else:
            words.append(arg)
    return forced, words, target


def read_text(words: list[str]) -> str:
    """Arguments if given, otherwise stdin.

    Checked in that order so `lens --summarize "$(cmd)"` and `cmd | lens
    --summarize` both work, and so an accidental `lens` on a terminal does not
    hang waiting for input that is not coming.
    """
    if words:
        return " ".join(words)
    if sys.stdin.isatty():
        return ""
    return sys.stdin.read()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "-h" in argv or "--help" in argv:
        sys.stdout.write(USAGE)
        return 0

    forced, words, target = parse(argv)
    text = frame.sanitize(read_text(words))
    if not text.strip():
        sys.stderr.write("lens: nothing to read. Pass text, or pipe it in.\n")
        return 2

    try:
        cfg = config.load()
    except config.ConfigError as exc:
        sys.stderr.write(f"lens: {exc}\n")
        return 1
    if target:
        import dataclasses

        cfg = dataclasses.replace(cfg, target_language=target)
    cfg = config.detect(cfg)

    kind = forced or modes.classify(text, cfg.word_lookup)
    if kind == modes.JUNK:
        sys.stderr.write("lens: nothing translatable in that.\n")
        return 1
    text = text[: modes.input_limit(kind)]

    # Streaming is for a human watching it arrive. A pipe gets the finished
    # value, once, so partial output can never be mistaken for the whole.
    live = sys.stdout.isatty()
    seen = 0

    def show(partial: str) -> None:
        nonlocal seen
        sys.stdout.write(partial[seen:])
        sys.stdout.flush()
        seen = len(partial)

    from . import language

    try:
        provider = build(cfg, kind)
        result = provider.translate(
            text,
            language.resolve(text, cfg.source_language),
            language.display(cfg.target_language),
            cfg.rendered_prompt(kind),
            on_chunk=show if live else None,
        )
    except ProviderError as exc:
        sys.stderr.write(f"lens: {exc.message}\n")
        if exc.hint:
            sys.stderr.write(f"{exc.hint}\n")
        return 1

    result = frame.sanitize(result)
    if live:
        # The stream may have stopped short of the final text, or never run.
        sys.stdout.write(result[seen:] if result.startswith(result[:seen]) else result)
    else:
        sys.stdout.write(result)
    if result and not result.endswith("\n"):
        sys.stdout.write("\n")
    return 0 if result else 1


if __name__ == "__main__":
    raise SystemExit(main())
