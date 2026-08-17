import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lens import config


def write(tmp: Path, body: str) -> Path:
    path = tmp / "config.toml"
    path.write_text(body, encoding="utf-8")
    return path


class ZeroConfig(unittest.TestCase):
    def test_missing_file_still_loads(self):
        cfg = config.load(Path("/nonexistent/config.toml"), env={"LANG": "en_US.UTF-8"})
        self.assertEqual(cfg.source_language, "auto")
        self.assertEqual(cfg.target_language, "en")
        self.assertIsNone(cfg.provider)
        self.assertIn("Translate", cfg.prompt)

    def test_target_language_comes_from_the_locale(self):
        for lang, expected in [
            ("zh_CN.UTF-8", "zh-CN"),
            ("zh_TW.UTF-8", "zh-TW"),
            ("ja_JP.UTF-8", "ja"),
            ("de_DE.UTF-8", "de"),
        ]:
            with self.subTest(lang=lang):
                self.assertEqual(config.default_target_language({"LANG": lang}), expected)

    def test_unknown_locale_falls_back_to_english(self):
        self.assertEqual(config.default_target_language({"LANG": "xx_YY.UTF-8"}), "en")
        self.assertEqual(config.default_target_language({}), "en")

    def test_lc_all_wins_over_lang(self):
        env = {"LC_ALL": "ja_JP.UTF-8", "LANG": "en_US.UTF-8"}
        self.assertEqual(config.default_target_language(env), "ja")


class MinimalConfig(unittest.TestCase):
    def test_single_line_config_only_sets_the_language(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(Path(tmp), 'target_language = "zh-CN"\n')
            cfg = config.load(path, env={})
            self.assertEqual(cfg.target_language, "zh-CN")
            self.assertIsNone(cfg.provider)

    def test_explicit_ai_table_is_honoured(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(
                Path(tmp),
                'target_language = "ja"\n'
                "[ai]\n"
                'provider = "openai-compatible"\n'
                'model = "my-model"\n'
                'endpoint = "https://example.com/v1"\n'
                'api_key_env = "MY_KEY"\n',
            )
            cfg = config.load(path, env={})
            self.assertEqual(cfg.provider, "openai-compatible")
            self.assertEqual(cfg.endpoint, "https://example.com/v1")
            self.assertEqual(cfg.api_key_env, "MY_KEY")

    def test_custom_prompt_overrides_the_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(Path(tmp), '[prompt]\ntranslation = "Say it in {target_language}."\n')
            cfg = config.load(path, env={"LANG": "ja_JP.UTF-8"})
            self.assertEqual(cfg.rendered_prompt(), "Say it in Japanese.")


class Credentials(unittest.TestCase):
    def test_literal_api_key_is_rejected_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(Path(tmp), '[ai]\napi_key = "sk-secret"\n')
            with self.assertRaises(config.ConfigError) as ctx:
                config.load(path, env={})
            self.assertIn("api_key_env", str(ctx.exception))

    def test_broken_toml_names_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(Path(tmp), "this is not toml = = =\n")
            with self.assertRaises(config.ConfigError):
                config.load(path, env={})


def no_clis():
    """Detection probes PATH; pin it so the host machine cannot sway a test."""
    return mock.patch("lens.config.shutil.which", return_value=None)


class Detection(unittest.TestCase):
    def blank(self) -> config.Config:
        return config.load(Path("/nonexistent"), env={})

    def test_anthropic_key_wins(self):
        cfg = config.detect(self.blank(), env={"ANTHROPIC_API_KEY": "x", "OPENAI_API_KEY": "y"})
        self.assertEqual(cfg.provider, "anthropic")
        self.assertEqual(cfg.model, "claude-sonnet-5")
        self.assertEqual(cfg.api_key_env, "ANTHROPIC_API_KEY")

    def test_openai_key_is_used_when_anthropic_is_absent(self):
        cfg = config.detect(self.blank(), env={"OPENAI_API_KEY": "y"})
        self.assertEqual(cfg.provider, "openai")
        self.assertEqual(cfg.api_key_env, "OPENAI_API_KEY")

    def test_a_groq_key_selects_groq(self):
        cfg = config.detect(self.blank(), env={"GROQ_API_KEY": "gsk-x"})
        self.assertEqual(cfg.provider, "groq")
        self.assertEqual(cfg.api_key_env, "GROQ_API_KEY")
        self.assertEqual(cfg.model, "openai/gpt-oss-120b")

    def test_explicit_config_is_never_overridden_by_detection(self):
        cfg = self.blank()
        cfg.provider, cfg.model = "ollama", "qwen3"
        detected = config.detect(cfg, env={"ANTHROPIC_API_KEY": "x"})
        self.assertEqual(detected.provider, "ollama")
        self.assertEqual(detected.model, "qwen3")

    def test_nothing_available_leaves_provider_unset(self):
        with no_clis(), mock.patch("lens.config._has_oauth_profile", return_value=False):
            cfg = config.detect(self.blank(), env={}, timeout=0.01)
        self.assertIsNone(cfg.provider)

    def test_the_claude_code_cli_is_used_when_no_key_is_exported(self):
        # A Claude subscription: the CLI already holds the credential.
        with mock.patch("lens.config.shutil.which", return_value="/usr/bin/claude"):
            cfg = config.detect(self.blank(), env={})
        self.assertEqual(cfg.provider, "claude-code")
        self.assertEqual(cfg.model, "sonnet")

    def test_an_exported_key_outranks_the_claude_code_cli(self):
        with mock.patch("lens.config.shutil.which", return_value="/usr/bin/claude"):
            cfg = config.detect(self.blank(), env={"ANTHROPIC_API_KEY": "x"})
        self.assertEqual(cfg.provider, "anthropic")

    def test_an_ant_login_profile_is_used_when_no_key_is_exported(self):
        with no_clis(), mock.patch("lens.config._has_oauth_profile", return_value=True):
            cfg = config.detect(self.blank(), env={})
        self.assertEqual(cfg.provider, "anthropic")
        self.assertEqual(cfg.auth, "oauth")
        self.assertEqual(cfg.model, "claude-sonnet-5")

    def test_an_exported_key_outranks_the_login_profile(self):
        # Matches Anthropic's own credential precedence: API key first.
        with no_clis(), mock.patch("lens.config._has_oauth_profile", return_value=True):
            cfg = config.detect(self.blank(), env={"ANTHROPIC_API_KEY": "x"})
        self.assertEqual(cfg.api_key_env, "ANTHROPIC_API_KEY")
        self.assertIsNone(cfg.auth)


class OAuthProfileDetection(unittest.TestCase):
    def test_absent_without_the_cli(self):
        with mock.patch("lens.config.shutil.which", return_value=None):
            self.assertFalse(config._has_oauth_profile({}))

    def test_absent_when_the_cli_is_installed_but_never_logged_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("lens.config.shutil.which", return_value="/usr/bin/ant"):
                self.assertFalse(config._has_oauth_profile({"ANTHROPIC_CONFIG_DIR": tmp}))

    def test_present_once_a_profile_has_been_stored(self):
        with tempfile.TemporaryDirectory() as tmp:
            credentials = Path(tmp) / "credentials"
            credentials.mkdir()
            (credentials / "default.json").write_text("{}")
            with mock.patch("lens.config.shutil.which", return_value="/usr/bin/ant"):
                self.assertTrue(config._has_oauth_profile({"ANTHROPIC_CONFIG_DIR": tmp}))


class AuthConfig(unittest.TestCase):
    def test_auth_mode_can_be_set_explicitly(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(Path(tmp), '[ai]\nprovider = "anthropic"\nauth = "oauth"\nmodel = "m"\n')
            cfg = config.load(path, env={})
            self.assertEqual(cfg.auth, "oauth")

    def test_auth_defaults_to_unset(self):
        cfg = config.load(Path("/nonexistent"), env={})
        self.assertIsNone(cfg.auth)


class EveryPromptNamesTheTargetLanguage(unittest.TestCase):
    """The one requirement no mode may quietly drop.

    An explanation or a summary in the language you were reading anyway is
    not an answer, and the failure is silent — the output looks fine, it is
    just useless. Adding a mode without wiring the language through is the
    easy way to cause it, so assert it for every mode at once.
    """

    def test_all_modes(self):
        cfg = config.load(Path("/nonexistent"), env={"LANG": "ja_JP.UTF-8"})
        for mode in ("general", "word", "term", "explain", "summarize"):
            with self.subTest(mode=mode):
                rendered = cfg.rendered_prompt(mode)
                self.assertIn("Japanese", rendered)
                self.assertNotIn("{target_language}", rendered)

    def test_the_name_is_used_rather_than_the_code(self):
        cfg = config.load(Path("/nonexistent"), env={"LANG": "zh_CN.UTF-8"})
        self.assertIn("Chinese (Simplified)", cfg.rendered_prompt("explain"))
        self.assertNotIn("zh-CN", cfg.rendered_prompt("explain"))


if __name__ == "__main__":
    unittest.main()
