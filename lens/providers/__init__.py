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


def build(cfg: Config, mode: str = "") -> Provider:
    """The provider for `mode`, which is the configured one unless overridden.

    Only `[ai.word]` exists as an override, and only because a dictionary entry
    is the one output read as authoritative — see Config.word_ai.
    """
    if mode == "word" and cfg.word_ai:
        cfg = _overridden(cfg, cfg.word_ai)
    return _build(cfg)


def _overridden(cfg: Config, over: dict) -> Config:
    import dataclasses

    known = {f.name for f in dataclasses.fields(Config)}
    changes = {k: v for k, v in over.items() if k in known}
    unknown = set(over) - known
    if unknown:
        raise ProviderError(
            f"Unknown key(s) under [ai.word]: {', '.join(sorted(unknown))}.",
            f"Supported: {', '.join(sorted(known & _AI_KEYS))}",
        )
    # A provider named without a model must not inherit the outer one — that
    # model belongs to a different provider. Fall back to the named provider's
    # own default, so `provider = "claude-code"` on its own is enough.
    named = changes.get("provider")
    if named and "model" not in changes:
        from ..config import DEFAULT_MODELS

        changes["model"] = DEFAULT_MODELS.get(named)
    return dataclasses.replace(cfg, **changes)


_AI_KEYS = {"provider", "model", "endpoint", "api_key_env", "api_key_file",
            "api_key_command", "auth", "timeout"}


def _build(cfg: Config) -> Provider:
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
