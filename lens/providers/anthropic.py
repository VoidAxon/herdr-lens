"""Anthropic Messages API provider.

Raw HTTP rather than the `anthropic` SDK: Herdr Lens is stdlib-only so that
`herdr plugin install` yields a working plugin with no pip step.
"""

from __future__ import annotations

import shutil
import subprocess

from .base import Provider, ProviderError

API_VERSION = "2023-06-01"

# OAuth tokens ride on `Authorization: Bearer` and need this beta header;
# sending `x-api-key` alongside them is a 401.
OAUTH_BETA = "oauth-2025-04-20"
OAUTH_TIMEOUT = 15


def oauth_token(runner=subprocess.run) -> str:
    """Borrow a short-lived token from the Anthropic CLI's stored profile.

    This is the credential path for people who sign in with `ant auth login`
    instead of holding a static API key. The CLI refreshes the token when it is
    close to expiring, so it is fetched per request rather than cached.
    """
    if not shutil.which("ant"):
        raise ProviderError(
            "The Anthropic CLI (`ant`) is not installed.",
            "Herdr Lens uses it to sign in without a static API key.\n"
            "Install it, then run:  ant auth login",
        )
    try:
        result = runner(
            ["ant", "auth", "print-credentials", "--access-token"],
            capture_output=True,
            timeout=OAUTH_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProviderError("Could not read your Anthropic credentials.", str(exc)) from exc

    token = result.stdout.decode("utf-8", "replace").strip()
    if result.returncode != 0 or not token:
        detail = result.stderr.decode("utf-8", "replace").strip()[:200]
        raise ProviderError(
            "Not signed in to Anthropic.",
            ("Run:  ant auth login\n" + detail).strip(),
        )
    if token.startswith("{"):
        # `print-credentials` without --access-token prints the whole JSON
        # blob, which yields an empty response rather than an obvious error.
        raise ProviderError(
            "The Anthropic CLI returned a credentials document, not a token.",
            "Herdr Lens expects `ant auth print-credentials --access-token`.",
        )
    return token

# Model families that accept `output_config.effort` and an explicit `thinking`
# mode. Older models (Haiku 4.5, Sonnet 4.5 and earlier) reject `output_config`
# with a 400 and do not think unless asked, so they need neither field.
_TUNABLE = (
    "claude-fable-5", "claude-mythos-5",
    "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7",
    "claude-opus-4-6", "claude-opus-4-5",
    "claude-sonnet-5", "claude-sonnet-4-6",
)

# Thinking is always on for these; sending `{"type": "disabled"}` is a 400.
_THINKING_ALWAYS_ON = ("claude-fable-5", "claude-mythos-5")


def speed_params(model: str) -> dict:
    """Request fields that keep a translation fast.

    Translation is not a reasoning task: thinking is turned off and effort
    dropped to `low` wherever the model supports those controls. Models that
    predate them get neither, because sending the field is an error rather
    than a no-op.
    """
    if not any(model.startswith(prefix) for prefix in _TUNABLE):
        return {}
    params: dict = {"output_config": {"effort": "low"}}
    if not any(model.startswith(prefix) for prefix in _THINKING_ALWAYS_ON):
        # Accepted at effort `high` or below, which `low` satisfies.
        params["thinking"] = {"type": "disabled"}
    return params


class AnthropicProvider(Provider):
    name = "Anthropic"
    default_endpoint = "https://api.anthropic.com"

    def _credential_headers(self) -> dict:
        """One credential or the other — never both, which the API rejects."""
        if self.auth == "oauth":
            return {"authorization": f"Bearer {oauth_token()}", "anthropic-beta": OAUTH_BETA}
        return {"x-api-key": self.api_key}

    def translate(self, text: str, source: str, target: str, prompt: str, on_chunk=None) -> str:
        body = {
            "model": self.model,
            "max_tokens": 8192,
            "system": prompt,
            "messages": [{"role": "user", "content": self._user_message(text, source, target)}],
            **speed_params(self.model),
        }
        headers = {"content-type": "application/json", "anthropic-version": API_VERSION}
        headers.update(self._credential_headers())
        payload = self._post(f"{self.endpoint}/v1/messages", body, headers)

        # A safety classifier can decline with HTTP 200 — check before reading content.
        if payload.get("stop_reason") == "refusal":
            details = payload.get("stop_details") or {}
            category = details.get("category") or "unspecified"
            raise ProviderError(
                "The model declined to translate this text.",
                f"Refusal category: {category}",
            )

        parts = [
            block.get("text", "")
            for block in payload.get("content", [])
            if block.get("type") == "text"
        ]
        return "".join(parts).strip()
