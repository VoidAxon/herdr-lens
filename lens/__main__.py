"""`python3 -m lens` is the CLI; the plugin entry points are lens.action and
lens.viewer, which Herdr names explicitly in herdr-plugin.toml."""

from .cli import main

raise SystemExit(main())
