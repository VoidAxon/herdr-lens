import os
import tempfile
import unittest
from pathlib import Path
import urllib.error
from io import BytesIO
from unittest import mock

from lens.config import Config
from lens.providers import REGISTRY, ProviderError, build
from lens.providers.anthropic import AnthropicProvider, oauth_token, speed_params
from lens.providers.ollama import OllamaProvider
from lens.providers.openai import OpenAIProvider

PROMPT = "Translate into zh-CN."
TEXT = "By default, grep prints the matching lines."


class Recorder:
    """Stands in for Provider._post and captures the outbound request."""

    def __init__(self, response):
        self.response = response
        self.url = None
        self.body = None
        self.headers = None

    def __call__(self, url, body, headers):
        self.url, self.body, self.headers = url, body, headers
        return self.response


def with_recorder(provider, response):
    rec = Recorder(response)
    provider._post = rec
    return rec


class Anthropic(unittest.TestCase):
    def setUp(self):
        # Patch the environment rather than the class: overriding the api_key
        # property on the type would leak into every later test.
        patcher = mock.patch.dict(os.environ, {"FAKE_KEY": "sk-test"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def provider(self):
        return AnthropicProvider(model="claude-opus-5", api_key_env="FAKE_KEY")

    def test_request_shape(self):
        p = self.provider()
        rec = with_recorder(p, {"content": [{"type": "text", "text": "默认情况下…"}]})

        out = p.translate(TEXT, "auto", "zh-CN", PROMPT)

        self.assertEqual(out, "默认情况下…")
        self.assertTrue(rec.url.endswith("/v1/messages"))
        self.assertEqual(rec.body["model"], "claude-opus-5")
        self.assertEqual(rec.body["system"], PROMPT)
        self.assertEqual(rec.headers["anthropic-version"], "2023-06-01")
        self.assertEqual(rec.headers["x-api-key"], "sk-test")

    def test_only_text_blocks_are_joined(self):
        p = self.provider()
        with_recorder(p, {"content": [
            {"type": "thinking", "thinking": "…"},
            {"type": "text", "text": "hello"},
            {"type": "text", "text": " world"},
        ]})
        self.assertEqual(p.translate(TEXT, "auto", "zh-CN", PROMPT), "hello world")

    def test_refusal_is_reported_not_parsed_as_content(self):
        p = self.provider()
        with_recorder(p, {"stop_reason": "refusal", "stop_details": {"category": "cyber"}, "content": []})
        with self.assertRaises(ProviderError) as ctx:
            p.translate(TEXT, "auto", "zh-CN", PROMPT)
        self.assertIn("cyber", ctx.exception.hint)


class OAuthCredentials(unittest.TestCase):
    """The `ant auth login` path, for people without a static API key."""

    def result(self, stdout=b"", stderr=b"", code=0):
        return mock.Mock(stdout=stdout, stderr=stderr, returncode=code)

    def test_token_is_read_from_the_cli(self):
        runner = mock.Mock(return_value=self.result(stdout=b"sk-ant-oat01-abc\n"))
        with mock.patch("lens.providers.anthropic.shutil.which", return_value="/usr/bin/ant"):
            self.assertEqual(oauth_token(runner), "sk-ant-oat01-abc")
        self.assertIn("--access-token", runner.call_args[0][0])

    def test_bearer_header_replaces_the_api_key_header(self):
        p = AnthropicProvider(model="claude-sonnet-5", auth="oauth")
        rec = with_recorder(p, {"content": [{"type": "text", "text": "ok"}]})
        with mock.patch("lens.providers.anthropic.oauth_token", return_value="tok"):
            p.translate(TEXT, "auto", "zh-CN", PROMPT)

        self.assertEqual(rec.headers["authorization"], "Bearer tok")
        self.assertEqual(rec.headers["anthropic-beta"], "oauth-2025-04-20")
        # Sending both credentials is a 401, so the key header must be absent.
        self.assertNotIn("x-api-key", rec.headers)

    def test_api_key_mode_sends_no_bearer(self):
        with mock.patch.dict(os.environ, {"FAKE_KEY": "sk-test"}):
            p = AnthropicProvider(model="claude-sonnet-5", api_key_env="FAKE_KEY")
            rec = with_recorder(p, {"content": [{"type": "text", "text": "ok"}]})
            p.translate(TEXT, "auto", "zh-CN", PROMPT)
        self.assertEqual(rec.headers["x-api-key"], "sk-test")
        self.assertNotIn("authorization", rec.headers)

    def test_missing_cli_tells_you_what_to_install(self):
        with mock.patch("lens.providers.anthropic.shutil.which", return_value=None):
            with self.assertRaises(ProviderError) as ctx:
                oauth_token()
        self.assertIn("ant auth login", ctx.exception.hint)

    def test_not_signed_in_tells_you_to_sign_in(self):
        runner = mock.Mock(return_value=self.result(stderr=b"no active profile", code=1))
        with mock.patch("lens.providers.anthropic.shutil.which", return_value="/usr/bin/ant"):
            with self.assertRaises(ProviderError) as ctx:
                oauth_token(runner)
        self.assertIn("Not signed in", ctx.exception.message)
        self.assertIn("ant auth login", ctx.exception.hint)

    def test_a_credentials_document_is_caught_rather_than_sent_as_a_token(self):
        # `print-credentials` without --access-token prints JSON; using it as a
        # bearer token fails with an opaque protocol error rather than a 401.
        runner = mock.Mock(return_value=self.result(stdout=b'{"access_token": "x"}'))
        with mock.patch("lens.providers.anthropic.shutil.which", return_value="/usr/bin/ant"):
            with self.assertRaises(ProviderError) as ctx:
                oauth_token(runner)
        self.assertIn("--access-token", ctx.exception.hint)

    def test_a_stalled_cli_is_reported(self):
        import subprocess

        runner = mock.Mock(side_effect=subprocess.TimeoutExpired("ant", 15))
        with mock.patch("lens.providers.anthropic.shutil.which", return_value="/usr/bin/ant"):
            with self.assertRaises(ProviderError):
                oauth_token(runner)


class SpeedParams(unittest.TestCase):
    """Translation wants speed, but the knobs are not available on every model."""

    def test_modern_models_get_low_effort_and_no_thinking(self):
        for model in (
            "claude-sonnet-5", "claude-opus-5", "claude-opus-4-8",
            "claude-opus-4-7", "claude-sonnet-4-6",
        ):
            with self.subTest(model=model):
                params = speed_params(model)
                self.assertEqual(params["output_config"], {"effort": "low"})
                self.assertEqual(params["thinking"], {"type": "disabled"})

    def test_models_without_effort_support_get_nothing(self):
        # output_config is a 400 on these, so sending it would break translation
        # for anyone who picked them for speed.
        for model in ("claude-haiku-4-5", "claude-sonnet-4-5", "claude-3-haiku-20240307"):
            with self.subTest(model=model):
                self.assertEqual(speed_params(model), {})

    def test_always_thinking_models_are_not_told_to_stop(self):
        # Fable/Mythos reject an explicit disabled-thinking config with a 400.
        for model in ("claude-fable-5", "claude-mythos-5"):
            with self.subTest(model=model):
                params = speed_params(model)
                self.assertNotIn("thinking", params)
                self.assertEqual(params["output_config"], {"effort": "low"})

    def test_unknown_model_is_treated_conservatively(self):
        self.assertEqual(speed_params("some-future-model"), {})


class AnthropicRequestTuning(unittest.TestCase):
    def build(self, model):
        p = AnthropicProvider(model=model, api_key_env=None)
        return p, with_recorder(p, {"content": [{"type": "text", "text": "ok"}]})

    def test_default_model_sends_the_speed_knobs(self):
        p, rec = self.build("claude-sonnet-5")
        p.translate(TEXT, "auto", "zh-CN", PROMPT)
        self.assertEqual(rec.body["thinking"], {"type": "disabled"})
        self.assertEqual(rec.body["output_config"], {"effort": "low"})

    def test_haiku_request_carries_neither_field(self):
        p, rec = self.build("claude-haiku-4-5")
        p.translate(TEXT, "auto", "zh-CN", PROMPT)
        self.assertNotIn("output_config", rec.body)
        self.assertNotIn("thinking", rec.body)

    def test_core_fields_are_present_regardless_of_model(self):
        for model in ("claude-sonnet-5", "claude-haiku-4-5"):
            with self.subTest(model=model):
                p, rec = self.build(model)
                p.translate(TEXT, "auto", "zh-CN", PROMPT)
                self.assertEqual(rec.body["model"], model)
                self.assertEqual(rec.body["system"], PROMPT)
                self.assertIn("max_tokens", rec.body)


class OpenAI(unittest.TestCase):
    def test_request_shape_and_parsing(self):
        p = OpenAIProvider(model="gpt-4o-mini", api_key_env=None)
        rec = with_recorder(p, {"choices": [{"message": {"content": "默认情况下…"}}]})

        out = p.translate(TEXT, "en", "zh-CN", PROMPT)

        self.assertEqual(out, "默认情况下…")
        self.assertTrue(rec.url.endswith("/chat/completions"))
        self.assertEqual(rec.body["messages"][0], {"role": "system", "content": PROMPT})
        self.assertIn("en", rec.body["messages"][1]["content"])

    def test_empty_choices_yields_empty_string(self):
        p = OpenAIProvider(model="gpt-4o-mini")
        with_recorder(p, {"choices": []})
        self.assertEqual(p.translate(TEXT, "auto", "zh-CN", PROMPT), "")

    def test_compatible_endpoint_is_used_verbatim(self):
        p = REGISTRY["openai-compatible"](model="m", endpoint="https://example.com/v1/")
        rec = with_recorder(p, {"choices": [{"message": {"content": "x"}}]})
        p.translate(TEXT, "auto", "zh-CN", PROMPT)
        self.assertEqual(rec.url, "https://example.com/v1/chat/completions")


class Ollama(unittest.TestCase):
    def test_request_shape_and_parsing(self):
        p = OllamaProvider(model="qwen3")
        rec = with_recorder(p, {"message": {"content": "默认情况下…"}})
        out = p.translate(TEXT, "auto", "zh-CN", PROMPT)
        self.assertEqual(out, "默认情况下…")
        self.assertTrue(rec.url.endswith("/api/chat"))
        self.assertFalse(rec.body["stream"])


URLOPEN = "lens.providers.base.urllib.request.urlopen"


class HTTPErrors(unittest.TestCase):
    def test_http_error_names_provider_and_model(self):
        p = OpenAIProvider(model="gpt-4o-mini")
        failure = urllib.error.HTTPError("u", 429, "Too Many Requests", {}, BytesIO(b"slow down"))
        self.addCleanup(failure.close)
        with mock.patch(URLOPEN, side_effect=failure):
            with self.assertRaises(ProviderError) as ctx:
                p.translate(TEXT, "auto", "zh-CN", PROMPT)
        self.assertIn("429", ctx.exception.hint)
        self.assertIn("gpt-4o-mini", ctx.exception.hint)
        self.assertIn("slow down", ctx.exception.hint)

    def test_a_stall_is_reported_as_a_timeout_not_a_network_error(self):
        # urllib surfaces a read timeout either bare or wrapped in URLError,
        # depending on where it fires; both must reach the user as a timeout.
        for failure in (
            TimeoutError("timed out"),
            urllib.error.URLError(TimeoutError("timed out")),
        ):
            with self.subTest(failure=type(failure).__name__):
                p = OpenAIProvider(model="gpt-4o-mini")
                with mock.patch(URLOPEN, side_effect=failure):
                    with self.assertRaises(ProviderError) as ctx:
                        p.translate(TEXT, "auto", "zh-CN", PROMPT)
                self.assertIn("too long", ctx.exception.message)
                self.assertIn("gpt-4o-mini", ctx.exception.hint)
                self.assertIn("15s", ctx.exception.hint)

    def test_the_timeout_is_short_enough_for_a_popup(self):
        from lens.providers.base import TIMEOUT

        self.assertLessEqual(TIMEOUT, 15.0)

    def test_network_failure_names_the_host(self):
        p = OpenAIProvider(model="gpt-4o-mini")
        with mock.patch(URLOPEN, side_effect=urllib.error.URLError("name resolution failed")):
            with self.assertRaises(ProviderError) as ctx:
                p.translate(TEXT, "auto", "zh-CN", PROMPT)
        self.assertIn("api.openai.com", ctx.exception.hint)


class Registry(unittest.TestCase):
    def blank(self, **kw):
        base = dict(
            target_language="zh-CN", source_language="auto", provider=None,
            model=None, endpoint=None, api_key_env=None, api_key_file=None,
        api_key_command=None, auth=None, prompt="p",
            word_prompt="w", term_prompt="t", explain_prompt="x",
        summarize_prompt="z", word_lookup=True,
        )
        base.update(kw)
        return Config(**base)

    def test_no_provider_gives_setup_instructions(self):
        with self.assertRaises(ProviderError) as ctx:
            build(self.blank())
        self.assertIn("ANTHROPIC_API_KEY", ctx.exception.hint)

    def test_unknown_provider_lists_the_supported_ones(self):
        with self.assertRaises(ProviderError) as ctx:
            build(self.blank(provider="gemini"))
        self.assertIn("ollama", ctx.exception.hint)

    def test_missing_model_is_reported(self):
        with self.assertRaises(ProviderError):
            build(self.blank(provider="openai"))

    def test_every_registered_provider_builds(self):
        for name in REGISTRY:
            with self.subTest(provider=name):
                p = build(self.blank(provider=name, model="m"))
                self.assertTrue(callable(p.translate))


if __name__ == "__main__":
    unittest.main()


class Groq(unittest.TestCase):
    """Groq speaks the OpenAI protocol; the alias exists so nobody has to
    remember the URL."""

    def test_the_endpoint_is_built_in(self):
        p = REGISTRY["groq"](model="llama-3.3-70b-versatile", api_key_env=None)
        rec = with_recorder(p, {"choices": [{"message": {"content": "默认情况下…"}}]})
        out = p.translate(TEXT, "English", "zh-CN", PROMPT)
        self.assertEqual(out, "默认情况下…")
        self.assertEqual(rec.url, "https://api.groq.com/openai/v1/chat/completions")

    def test_the_key_rides_as_a_bearer_token(self):
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "gsk-test"}):
            p = REGISTRY["groq"](model="m", api_key_env="GROQ_API_KEY")
            rec = with_recorder(p, {"choices": [{"message": {"content": "ok"}}]})
            p.translate(TEXT, "auto", "zh-CN", PROMPT)
        self.assertEqual(rec.headers["authorization"], "Bearer gsk-test")

    def test_a_self_hosted_endpoint_still_overrides_it(self):
        p = REGISTRY["groq"](model="m", endpoint="https://proxy.internal/v1")
        rec = with_recorder(p, {"choices": [{"message": {"content": "ok"}}]})
        p.translate(TEXT, "auto", "zh-CN", PROMPT)
        self.assertTrue(rec.url.startswith("https://proxy.internal/v1"))


class CredentialSources(unittest.TestCase):
    """Herdr's server freezes its environment at start-up, so a key must be
    reachable without restarting it — restarting kills every pane it owns."""

    def provider(self, **kw):
        return REGISTRY["groq"](model="m", **kw)

    def test_a_file_is_read_and_stripped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "groq.key"
            path.write_text("gsk-from-file\n")
            path.chmod(0o600)
            self.assertEqual(self.provider(api_key_file=str(path)).api_key, "gsk-from-file")

    def test_a_world_readable_file_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "groq.key"
            path.write_text("gsk-exposed")
            path.chmod(0o644)
            with self.assertRaises(ProviderError) as ctx:
                _ = self.provider(api_key_file=str(path)).api_key
            self.assertIn("chmod 600", ctx.exception.hint)

    def test_a_command_supplies_the_key(self):
        p = self.provider(api_key_command="printf gsk-from-command")
        self.assertEqual(p.api_key, "gsk-from-command")

    def test_a_failing_command_is_reported(self):
        p = self.provider(api_key_command="exit 1")
        with self.assertRaises(ProviderError) as ctx:
            _ = p.api_key
        self.assertIn("api_key_command", ctx.exception.message)

    def test_an_unset_variable_explains_the_server_environment(self):
        p = self.provider(api_key_env="DEFINITELY_UNSET_VAR")
        with self.assertRaises(ProviderError) as ctx:
            _ = p.api_key
        self.assertIn("DEFINITELY_UNSET_VAR", ctx.exception.message)
        self.assertIn("server was started with", ctx.exception.hint)

    def test_a_file_covers_for_an_unset_variable(self):
        # The migration path: keep the env var configured, add a file, and the
        # plugin works before the next server restart.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "groq.key"
            path.write_text("gsk-fallback")
            path.chmod(0o600)
            p = self.provider(api_key_env="DEFINITELY_UNSET_VAR", api_key_file=str(path))
            self.assertEqual(p.api_key, "gsk-fallback")

    def test_the_environment_still_wins_when_it_is_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "groq.key"
            path.write_text("gsk-from-file")
            path.chmod(0o600)
            with mock.patch.dict(os.environ, {"GROQ_TEST_KEY": "gsk-from-env"}):
                p = self.provider(api_key_env="GROQ_TEST_KEY", api_key_file=str(path))
                self.assertEqual(p.api_key, "gsk-from-env")

    def test_no_source_configured_yields_no_key(self):
        self.assertEqual(self.provider().api_key, "")


class UserAgent(unittest.TestCase):
    """Cloudflare, in front of some providers, rejects urllib's default
    `Python-urllib/3.x` with a bare 403 — identifying the client fixes it."""

    def test_every_request_identifies_the_client(self):
        from lens.providers.base import USER_AGENT

        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def read(self):
                return b'{"choices":[{"message":{"content":"ok"}}]}'

        def fake_urlopen(req, timeout=None):
            captured["headers"] = {k.lower(): v for k, v in req.header_items()}
            return FakeResponse()

        with mock.patch("lens.providers.base.json.load", return_value={"choices": [{"message": {"content": "ok"}}]}):
            with mock.patch(URLOPEN, side_effect=fake_urlopen):
                REGISTRY["groq"](model="m").translate(TEXT, "auto", "zh-CN", PROMPT)

        self.assertEqual(captured["headers"].get("user-agent"), USER_AGENT)
        self.assertNotIn("python-urllib", captured["headers"].get("user-agent", "").lower())

    def test_a_provider_may_still_override_its_headers(self):
        p = REGISTRY["anthropic"](model="claude-sonnet-5", api_key_env=None)
        rec = with_recorder(p, {"content": [{"type": "text", "text": "ok"}]})
        p.translate(TEXT, "auto", "zh-CN", PROMPT)
        self.assertEqual(rec.headers["anthropic-version"], "2023-06-01")
