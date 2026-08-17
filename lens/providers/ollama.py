"""Ollama provider — a local model, no credentials involved."""

from __future__ import annotations

from .base import Provider


class OllamaProvider(Provider):
    name = "Ollama"
    default_endpoint = "http://localhost:11434"

    def translate(self, text: str, source: str, target: str, prompt: str, on_chunk=None) -> str:
        body = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": self._user_message(text, source, target)},
            ],
        }
        payload = self._post(
            f"{self.endpoint}/api/chat", body, {"content-type": "application/json"}
        )
        return (payload.get("message", {}).get("content") or "").strip()
