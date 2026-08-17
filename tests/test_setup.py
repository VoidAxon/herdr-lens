"""Keybinding setup.

This writes to a file Herdr Lens does not own, so most of these tests are
about what it must *not* do: never overwrite a binding, never lose a comment,
never write to a file Herdr does not read, and never touch a config it cannot
understand.
"""

import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from lens import setup


class ConfigPath(unittest.TestCase):
    """Guessing the path is the failure that looks like success: the keys get
    written, the command reports victory, and the keypress does nothing."""

    def test_herdr_config_path_wins(self):
        got = setup.config_path({"HERDR_CONFIG_PATH": "/tmp/custom.toml",
                                 "XDG_CONFIG_HOME": "/tmp/xdg"})
        self.assertEqual(got, Path("/tmp/custom.toml"))

    def test_an_empty_herdr_config_path_means_no_config_at_all(self):
        self.assertIsNone(setup.config_path({"HERDR_CONFIG_PATH": ""}))

    def test_xdg_config_home_is_honoured(self):
        got = setup.config_path({"XDG_CONFIG_HOME": "/tmp/xdg"})
        self.assertEqual(got, Path("/tmp/xdg/herdr/config.toml"))

    def test_the_default_is_the_conventional_location(self):
        got = setup.config_path({})
        self.assertEqual(got, Path.home() / ".config/herdr/config.toml")


class Plan(unittest.TestCase):
    def test_a_fresh_config_gets_every_binding(self):
        add, clash = setup.plan("")
        self.assertEqual(len(add), len(setup.BINDINGS))
        self.assertEqual(clash, [])

    def test_a_key_taken_by_something_else_is_never_stolen(self):
        text = '[[keys.command]]\nkey = "prefix+t"\ntype = "toggle_sidebar"\n'
        add, clash = setup.plan(text)
        self.assertNotIn("prefix+t", [k for k, _, _ in add])
        self.assertTrue(any("toggle_sidebar" in c for c in clash))

    def test_our_own_block_is_not_a_conflict_with_itself(self):
        """Without this, a second run reports every key as taken."""
        text = setup.block(setup.BINDINGS)
        add, clash = setup.plan(text)
        self.assertEqual(clash, [])
        self.assertEqual(len(add), len(setup.BINDINGS))

    def test_an_unparseable_config_is_refused_not_appended_to(self):
        """Herdr discards a config it cannot parse, so keys written here would
        not load — and the bindings we must not overwrite are invisible."""
        with self.assertRaises(setup.SetupError) as raised:
            setup.plan("this is not [ valid toml")
        self.assertIn("config check", str(raised.exception))


class Writing(unittest.TestCase):
    def run_setup(self, initial, argv=()):
        tmp = Path(tempfile.mkdtemp()) / "config.toml"
        if initial is not None:
            tmp.write_text(initial, encoding="utf-8")
        with mock.patch("lens.setup.config_path", return_value=tmp):
            with mock.patch("lens.setup.reload_herdr", return_value=True):
                with mock.patch("sys.stdout"):
                    code = setup.main(list(argv))
        return code, (tmp.read_text(encoding="utf-8") if tmp.exists() else "")

    def test_existing_content_survives_verbatim(self):
        initial = '# my careful notes\n[theme]\nname = "catppuccin"\n'
        _, result = self.run_setup(initial)
        self.assertTrue(result.startswith('# my careful notes'))
        self.assertIn('name = "catppuccin"', result)

    def test_the_result_parses_and_binds_everything(self):
        _, result = self.run_setup('[theme]\nname = "x"\n')
        bound = {e["key"]: e["command"] for e in tomllib.loads(result)["keys"]["command"]}
        for key, command, _ in setup.BINDINGS:
            self.assertEqual(bound[key], command)

    def test_a_second_run_replaces_its_block_rather_than_duplicating_it(self):
        _, once = self.run_setup('[theme]\nname = "x"\n')
        tmp = Path(tempfile.mkdtemp()) / "config.toml"
        tmp.write_text(once, encoding="utf-8")
        with mock.patch("lens.setup.config_path", return_value=tmp):
            with mock.patch("lens.setup.reload_herdr", return_value=True):
                with mock.patch("sys.stdout"):
                    setup.main([])
        twice = tmp.read_text(encoding="utf-8")
        self.assertEqual(twice.count(setup.BEGIN), 1)
        self.assertEqual(twice, once, "a repeat run must be byte-identical")

    def test_a_missing_config_file_is_created(self):
        code, result = self.run_setup(None)
        self.assertEqual(code, 0)
        self.assertIn("lens-translate", result)

    def test_a_file_without_a_trailing_newline_is_handled(self):
        _, result = self.run_setup('[theme]\nname = "x"')
        tomllib.loads(result)  # must not raise

    def test_a_broken_config_is_left_untouched(self):
        broken = "this is not [ valid toml"
        code, result = self.run_setup(broken)
        self.assertEqual(code, 1)
        self.assertEqual(result, broken)

    def test_every_key_taken_is_a_failure_not_a_silent_success(self):
        taken = "".join(
            f'[[keys.command]]\nkey = "{k}"\ntype = "zoom"\n\n'
            for k, _, _ in setup.BINDINGS
        )
        code, result = self.run_setup(taken)
        self.assertEqual(code, 1)
        self.assertNotIn(setup.BEGIN, result, "nothing should have been written")


class Removing(unittest.TestCase):
    def test_removal_leaves_the_users_own_config_exactly_as_it_was(self):
        initial = '# mine\n[theme]\nname = "x"\n\n[[keys.command]]\nkey = "f9"\ntype = "zoom"\n'
        tmp = Path(tempfile.mkdtemp()) / "config.toml"
        tmp.write_text(initial, encoding="utf-8")
        with mock.patch("lens.setup.config_path", return_value=tmp):
            with mock.patch("lens.setup.reload_herdr", return_value=True):
                with mock.patch("sys.stdout"):
                    setup.main([])
                    setup.main(["remove"])
        self.assertEqual(tmp.read_text(encoding="utf-8"), initial)

    def test_removing_when_nothing_is_installed_says_so(self):
        tmp = Path(tempfile.mkdtemp()) / "config.toml"
        tmp.write_text('[theme]\nname = "x"\n', encoding="utf-8")
        with mock.patch("lens.setup.config_path", return_value=tmp):
            with mock.patch("sys.stdout"):
                self.assertEqual(setup.main(["remove"]), 0)
        self.assertEqual(tmp.read_text(encoding="utf-8"), '[theme]\nname = "x"\n')


class Defaults(unittest.TestCase):
    def test_no_binding_claims_a_bare_control_key(self):
        """ctrl+t is transpose-chars in readline. Offering it is fine;
        taking it on every user's behalf is not."""
        for key, _, _ in setup.BINDINGS:
            self.assertTrue(key.startswith("prefix+"), f"{key} is not a prefix chord")

    def test_a_fumbled_modifier_on_translate_still_translates(self):
        """`prefix+alt+t` mistyped as `prefix+t` must not do nothing — and must
        not do something else either."""
        by_key = {k: c for k, c, _ in setup.BINDINGS}
        for key, command in list(by_key.items()):
            if not key.startswith("prefix+alt+"):
                continue
            fumbled = "prefix+" + key.rsplit("+", 1)[1]
            if fumbled in by_key:
                self.assertEqual(
                    by_key[fumbled], command,
                    f"{fumbled} is bound to something other than {command}",
                )

    def test_a_repeated_action_does_not_confuse_the_plan(self):
        """translate appears twice on purpose; a fresh config must get both."""
        add, clash = setup.plan("")
        self.assertEqual(len(add), len(setup.BINDINGS))
        self.assertEqual(clash, [])


if __name__ == "__main__":
    unittest.main()
