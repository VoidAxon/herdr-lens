"""The provider interface, and the HTTP plumbing every provider shares."""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

# A translation popup that spins for half a minute has already failed the
# user. Give up early and say which provider stalled.
TIMEOUT = 15.0

# Named so the model cannot mistake the selection for part of the instructions,
# and so nothing outside them looks like content. The CLI provider uses the same
# pair for the same reason.
SELECTION_OPEN = "<<<TERMINAL_TEXT"
SELECTION_CLOSE = "TERMINAL_TEXT>>>"

AUTO_SOURCE = "auto"

# urllib's default is `Python-urllib/3.x`, which Cloudflare in front of some
# providers rejects outright with a 403 and no useful message. Identifying the
# client is the right thing to do anyway.
USER_AGENT = "herdr-lens/0.1.0 (+https://github.com/herdr-lens)"


class ProviderError(Exception):
    """A failure the user can act on. `hint` is the second line in the popup."""

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.message = message
        self.hint = hint


class Provider(ABC):
    name = "provider"

    # Overridable: the Claude Code CLI carries process startup that HTTP does not.
    timeout = TIMEOUT

    def __init__(
        self,
        model: str,
        endpoint: str | None = None,
        api_key_env: str | None = None,
        auth: str | None = None,
        api_key_file: str | None = None,
        api_key_command: str | None = None,
    ):
        self.model = model
        self.endpoint = (endpoint or self.default_endpoint).rstrip("/")
        self.api_key_env = api_key_env
        self.api_key_file = api_key_file
        self.api_key_command = api_key_command
        # "api_key" (default) or "oauth" — see AnthropicProvider.
        self.auth = auth or "api_key"

    default_endpoint = ""

    # Providers that expose an OpenAI-style catalogue set this, so a retired
    # model can be diagnosed without leaving the popup.
    models_path = ""

    @property
    def api_key(self) -> str:
        """Read the credential from whichever source is configured.

        An environment variable is the obvious choice until you notice that
        Herdr is a long-lived server: plugin processes inherit the environment
        the server was started with, so exporting a key in a shell afterwards
        never reaches them. A file or a command works without restarting the
        server — and restarting it kills every pane it owns.
        """
        sources = (
            ("env", self.api_key_env, self._from_env),
            ("file", self.api_key_file, self._from_file),
            ("command", self.api_key_command, self._from_command),
        )
        configured = [(kind, value, reader) for kind, value, reader in sources if value]
        if not configured:
            return ""

        for _, value, reader in configured:
            key = reader(value)
            if key:
                return key

        if len(configured) == 1:
            raise self._missing(*configured[0][:2])
        named = ", ".join(f"{kind} ({value})" for kind, value, _ in configured)
        raise ProviderError("No API key found.", f"Nothing was readable from: {named}")

    @staticmethod
    def _missing(kind: str, value: str) -> ProviderError:
        if kind == "env":
            return ProviderError(
                f"{value} is not set.",
                # Overwhelmingly the cause: the variable exists in the user's
                # shell but not in the server that spawned this process.
                "Plugin processes inherit the environment Herdr's server was "
                "started with, so exporting it in a shell afterwards does not "
                "reach them. Either restart the server, or point `api_key_file` "
                "at a file instead.",
            )
        if kind == "file":
            return ProviderError(f"{value} is empty or unreadable.", "")
        return ProviderError("`api_key_command` produced no key.", f"Command: {value}")

    def _from_env(self, name: str) -> str:
        return os.environ.get(name, "").strip()

    def _from_file(self, path: str) -> str:
        target = Path(path).expanduser()
        try:
            mode = target.stat().st_mode
        except OSError:
            return ""
        if mode & 0o077:
            raise ProviderError(
                f"{target} is readable by other users.",
                f"Run:  chmod 600 {target}",
            )
        try:
            return target.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _from_command(self, command: str) -> str:
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, timeout=10, check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return result.stdout.decode("utf-8", "replace").strip() if result.returncode == 0 else ""

    @abstractmethod
    def translate(
        self,
        text: str,
        source: str,
        target: str,
        prompt: str,
        on_chunk: Callable[[str], None] | None = None,
    ) -> str:
        """Return the translated text, or raise ProviderError.

        `on_chunk` receives the text accumulated so far, if the provider can
        produce output incrementally. Providers that cannot simply ignore it
        and return the whole result at the end; callers must treat streaming
        as an optimisation, never as a guarantee.
        """

    # -- shared HTTP ------------------------------------------------------

    def _post(self, url: str, body: dict, headers: dict) -> dict:
        data = json.dumps(body).encode("utf-8")
        headers = {"user-agent": USER_AGENT, **headers}
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200].strip()
            raise ProviderError(
                "The AI provider returned an error.",
                f"{self.name} / {self.model} / HTTP {exc.code}\n{detail}"
                + self._model_hint(exc.code, detail),
            ) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise self._timeout() from exc
            raise ProviderError(
                "Cannot reach the AI provider.",
                f"{urllib.parse.urlsplit(url).netloc} — {exc.reason}",
            ) from exc
        except TimeoutError as exc:
            raise self._timeout() from exc
        except (OSError, ValueError) as exc:
            raise ProviderError("The AI provider request failed.", str(exc)) from exc

    def _model_hint(self, code: int, detail: str) -> str:
        """Extra guidance when the configured model no longer exists.

        Hosted providers retire models without warning — this default was
        `llama-3.3-70b-versatile` until Groq removed it — so the interesting
        question is never "what went wrong" but "what may I use instead". The
        raw provider message answers the first and not the second.
        """
        looks_missing = code in (400, 404) and (
            "model_not_found" in detail or "does not exist" in detail
        )
        if not looks_missing or not self.models_path:
            return ""
        return (
            "\n\nThe model may have been retired. List the ones available to you:\n"
            f"  curl -s -H \"Authorization: Bearer $KEY\" {self.endpoint}{self.models_path}"
            " | python3 -m json.tool\n"
            "Then set `model` under [ai] in the config file."
        )

    def _timeout(self) -> ProviderError:
        return ProviderError(
            "The AI provider took too long.",
            f"{self.name} · {self.model} — no response in {self.timeout:.0f}s",
        )

    def _user_message(self, text: str, source: str, target: str) -> str:
        """The selection, delimited, and nothing else.

        Two lessons the CLI provider learned first. Prose framing that sits
        beside the text gets translated along with it — a leading "Target
        language: zh-CN." came back rendered in Chinese. And a bare word after a
        thin `---` rule does not read as input at all: asked to define
        `verbose`, the model answered "please provide the word" once in six
        tries, because the scaffolding was longer than the content.

        The language belongs in the system prompt, which already carries it.
        """
        return f"{SELECTION_OPEN}\n{text}\n{SELECTION_CLOSE}"

    def _system_message(self, prompt: str, source: str) -> str:
        """The prompt, plus the source language when it is known."""
        if source == AUTO_SOURCE:
            return prompt
        return f"{prompt}\n\nThe text is written in {source}."
