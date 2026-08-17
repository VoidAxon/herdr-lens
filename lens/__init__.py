"""Herdr Lens — understand anything in your terminal with AI."""

import sys

__version__ = "0.1.0"

# `tomllib` arrived in 3.11 and there is no TOML parser in older stdlib, so
# that is the real floor. The check lives here, not in the entry points,
# because on 3.10 the failure is `import tomllib` raising during *import* —
# before any function in those modules runs. A guard inside prepare() reads
# fine and never executes; the user gets a traceback instead of a sentence.
#
# Both processes import this package before anything else, so this is the
# earliest reachable line in either of them. Nothing above it may import a
# submodule, or the import it is meant to pre-empt happens first.
MIN_PYTHON = (3, 11)

if sys.version_info < MIN_PYTHON:
    _found = ".".join(str(n) for n in sys.version_info[:3])
    raise SystemExit(
        f"Herdr Lens needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer. "
        f"Found {_found}.\n"
        "Set the plugin's command to a newer interpreter in herdr-plugin.toml."
    )
