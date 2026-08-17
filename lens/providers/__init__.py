"""Provider registry.

Adding a provider is one new module plus one line in REGISTRY — nothing in the
UI or the action layer imports a provider-specific symbol.
"""

from __future__ import annotations

from ..config import Config
from .anthropic import AnthropicProvider
from .base import Provider, ProviderError
from .claude_code import ClaudeCodeProvider
from .ollama import OllamaProvider
from .openai import GroqProvider, OpenAICompatibleProvider, OpenAIProvider

REGISTRY: dict[str, type[Provider]] = {
    "anthropic": AnthropicProvider,
    "claude-code": ClaudeCodeProvider,
    "openai": OpenAIProvider,
    "openai-compatible": OpenAICompatibleProvider,
    "groq": GroqProvider,
    "ollama": OllamaProvider,
}

__all__ = ["Provider", "ProviderError", "REGISTRY", "build"]


def build(cfg: Config) -> Provider:
    if not cfg.provider:
        raise ProviderError(
            "No AI provider configured.",
            "Install the Claude Code CLI, export ANTHROPIC_API_KEY or "
            "OPENAI_API_KEY, sign in with `ant auth login`, or run Ollama on "
            "localhost:11434.\n"
            "To pick one explicitly, see the config file:\n"
            f"{_config_hint()}",
        )
    cls = REGISTRY.get(cfg.provider)
    if cls is None:
        known = ", ".join(sorted(REGISTRY))
        raise ProviderError(
            f"Unknown provider {cfg.provider!r}.", f"Supported: {known}"
        )
    if not cfg.model:
        raise ProviderError(
            f"No model configured for {cfg.provider}.",
            "Set `model` under [ai] in the config file.",
        )
    if cfg.timeout is not None and not (0 < cfg.timeout <= 600):
        raise ProviderError(
            f"`timeout` must be between 0 and 600 seconds, not {cfg.timeout}.",
            "Set it under [ai] in the config file.",
        )
    provider = cls(
        model=cfg.model,
        endpoint=cfg.endpoint,
        api_key_env=cfg.api_key_env,
        api_key_file=cfg.api_key_file,
        api_key_command=cfg.api_key_command,
        auth=cfg.auth,
    )
    if cfg.timeout is not None:
        # Per-instance, so the class default still documents the sane value.
        provider.timeout = float(cfg.timeout)
    return provider


def _config_hint() -> str:
    from ..config import config_path

    return str(config_path())
