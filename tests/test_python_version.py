"""The interpreter floor.

This guard was written from the spec and never exercised, and it had rotted
into unreachable code: it sat inside `prepare()`, but on Python 3.10 the run
dies earlier, at `import tomllib`. The tests here pin both halves — the
message, and the ordering that lets it be reached at all.
"""

import re
import sys
import unittest
from pathlib import Path
from unittest import mock

import lens

PACKAGE_INIT = Path(__file__).resolve().parent.parent / "lens" / "__init__.py"


class Guard(unittest.TestCase):
    def _run_init(self):
        """Execute the package body in a throwaway namespace."""
        source = PACKAGE_INIT.read_text(encoding="utf-8")
        exec(compile(source, str(PACKAGE_INIT), "exec"), {"__name__": "lens_probe"})

    def test_an_old_interpreter_is_refused_with_a_sentence(self):
        with mock.patch.object(sys, "version_info", (3, 10, 14)):
            with self.assertRaises(SystemExit) as raised:
                self._run_init()
        message = str(raised.exception)
        self.assertIn("3.11", message)
        self.assertIn("3.10.14", message, "say which version was actually found")

    def test_the_message_says_what_to_do(self):
        with mock.patch.object(sys, "version_info", (3, 9, 0)):
            with self.assertRaises(SystemExit) as raised:
                self._run_init()
        self.assertIn("herdr-plugin.toml", str(raised.exception))

    def test_a_supported_interpreter_passes(self):
        with mock.patch.object(sys, "version_info", (3, 11, 0)):
            self._run_init()  # must not raise


class Ordering(unittest.TestCase):
    """The guard only works if nothing imports tomllib before it."""

    def test_the_package_body_imports_no_submodule(self):
        source = PACKAGE_INIT.read_text(encoding="utf-8")
        relative = re.findall(r"^\s*from\s+\.\s*\w*\s+import|^\s*import\s+lens\.",
                              source, re.MULTILINE)
        self.assertEqual(
            relative, [],
            "a submodule import here would run `import tomllib` before the "
            "guard, which is the bug this guard exists to avoid",
        )

    def test_the_floor_matches_the_module_that_forces_it(self):
        # tomllib is the reason for the floor; if it ever stops being the
        # binding constraint, this number needs revisiting deliberately.
        self.assertEqual(lens.MIN_PYTHON, (3, 11))
        config_source = (PACKAGE_INIT.parent / "config.py").read_text(encoding="utf-8")
        self.assertIn("import tomllib", config_source)


if __name__ == "__main__":
    unittest.main()
