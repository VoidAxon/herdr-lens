"""OpenAI Chat Completions provider.

Also serves any OpenAI-compatible endpoint (vLLM, LM Studio, OpenRouter,
Together, …) — those differ only in `endpoint` and `api_key_env`.
"""

from __future__ import annotations

from .base import Provider


class OpenAIProvider(Provider):
    models_path = "/models"
    name = "OpenAI"
    default_endpoint = "https://api.openai.com/v1"

    def translate(self, text: str, source: str, target: str, prompt: str, on_chunk=None) -> str:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self._system_message(prompt, source)},
                {"role": "user", "content": self._user_message(text, source, target)},
            ],
        }
        headers = {"content-type": "application/json"}
        if self.api_key_env:
            headers["authorization"] = f"Bearer {self.api_key}"

        payload = self._post(f"{self.endpoint}/chat/completions", body, headers)
        choices = payload.get("choices") or []
        if not choices:
            return ""
        return (choices[0].get("message", {}).get("content") or "").strip()


class OpenAICompatibleProvider(OpenAIProvider):
    name = "OpenAI-compatible"
    default_endpoint = "http://localhost:8000/v1"


class GroqProvider(OpenAIProvider):
    """Groq speaks the OpenAI protocol, so only the endpoint differs.

    It exists as a named provider purely so the config can say `groq` instead
    of asking the user to remember a URL.
    """

    name = "Groq"
    default_endpoint = "https://api.groq.com/openai/v1"


class GeminiProvider(OpenAIProvider):
    """Gemini through Google's OpenAI-compatible endpoint.

    Same reason GroqProvider exists: the protocol is identical, so only the URL
    differs, and a named provider means the config does not have to carry it.

    Google moved this path once already — it was `/v1beta/chat/completions`
    before `/v1beta/openai/chat/completions` — so the endpoint stays overridable.
    """

    name = "Gemini"
    default_endpoint = "https://generativelanguage.googleapis.com/v1beta/openai"
