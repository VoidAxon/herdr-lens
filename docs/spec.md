# Herdr Lens — Specification (v2)

> v2 rewrites v1 (kept as `herdr-lens-spec.v1.md`) against the **measured**
> capabilities of Herdr 0.8.0. Everything marked ✅ VERIFIED below was executed
> against the running Herdr server, not inferred from documentation.
>
> The two driving changes from v1:
> 1. Herdr 0.8.0 does **not** populate `selected_text` on the keybinding path,
>    so the selection arrives via the clipboard. v1's four-tier
>    `SelectionProvider` collapses to two tiers — see §15.
> 2. **Configuration must be optional.** Herdr Lens works with an empty config
>    directory. Everything below is designed backwards from that requirement.

---

## 1. Overview

Herdr Lens lets a user select text in a Herdr terminal pane and translate it
with AI, without leaving Herdr. The target content is English technical
documentation, command output, and error messages.

The MVP ships exactly one action: **translate**. `explain`, `summarize`, and
`ask` are deliberately deferred, but §16 constrains the design so they can be
added without restructuring.

---

## 2. Verified Platform Capabilities

Measured against Herdr 0.8.0, protocol 19.

| Capability | Mechanism | Status |
|---|---|---|
| Plugin manifest | `herdr-plugin.toml`; requires `id`, `name`, `version`, `min_herdr_version` | ✅ VERIFIED |
| Install | `herdr plugin install <owner>/<repo>` or `herdr plugin link <path>` | ✅ VERIFIED |
| Actions | `[[actions]]` with `id`, `title`, `command` (argv), `contexts` | ✅ VERIFIED |
| Action contexts | enum `global｜workspace｜tab｜pane｜selection` | ✅ VERIFIED |
| Keybinding → action | `[[keys.command]]`, `type = "plugin_action"`, `command = "<action-id>"` | ✅ VERIFIED — **bare id only**; a qualified `plugin/action` string returns `plugin_action_not_found` |
| Popup pane | `[[panes]]` with `placement = "popup"`, `width`/`height` in cells or `"NN%"` | ✅ VERIFIED |
| Passing data to popup | `herdr plugin pane open --env KEY=VALUE` | ✅ VERIFIED |
| Selection delivery | `HERDR_PLUGIN_CONTEXT_JSON` → `.selected_text` | ❌ **NOT populated on the keybinding path.** The field exists and `contexts = ["selection"]` is accepted, but 0.8.0 leaves it absent — verified under a live mouse selection with `copy_on_select` both on and off. See §12. |
| Selection via clipboard | `copy_on_select = true` (Herdr default) puts every mouse selection on the system clipboard | ✅ VERIFIED end-to-end (WSL, `win32yank`, ~370 ms) |
| Copy to clipboard | OSC 52 (`ESC ] 52 ; c ; <base64> BEL`); Herdr passes it through | ✅ sequence present in Herdr |
| Plugin config dir | `$HERDR_PLUGIN_CONFIG_DIR` = `~/.config/herdr/plugins/config/<id>/` | ✅ VERIFIED |
| Plugin state dir | `$HERDR_PLUGIN_STATE_DIR` = `~/.local/state/herdr/plugins/<id>/` | ✅ VERIFIED |
| API callback | `$HERDR_SOCKET_PATH`; plugin may call any socket API method | ✅ VERIFIED |

### 2.1 Environment handed to an action process

Confirmed by direct observation:

```text
HERDR_PLUGIN_ID            HERDR_PLUGIN_ROOT
HERDR_PLUGIN_ACTION_ID     HERDR_PLUGIN_CONFIG_DIR
HERDR_PLUGIN_CONTEXT_JSON  HERDR_PLUGIN_STATE_DIR
HERDR_SOCKET_PATH          HERDR_WORKSPACE_ID / HERDR_TAB_ID / HERDR_PANE_ID
```

`HERDR_PLUGIN_CONTEXT_JSON` observed payload:

```json
{
  "workspace_id": "w2", "workspace_label": "~",
  "workspace_cwd": "/home/user/project",
  "tab_id": "w2:t2", "tab_label": "2",
  "focused_pane_id": "w2:p2",
  "focused_pane_cwd": "/home/user/project",
  "focused_pane_agent": "claude",
  "focused_pane_status": "working",
  "invocation_source": "cli",
  "selected_text": "...",
  "correlation_id": "cli:plugin"
}
```

`invocation_source` is one of `cli`, `keybinding`, `link_click`.

### 2.2 Capabilities Herdr does NOT provide

These are the plugin's own responsibility. v1 assumed some of them existed.

- **No rich popup renderer.** A popup pane is a terminal running your command.
  Border, scrolling, footer, and spinner are all plugin-side code.
- **No clipboard API method.** Copy must go through OSC 52.
- **No `plugin_pane` keybinding type.** A key can only trigger an *action*;
  the action must then open the pane. See §3.
- **No selection-read API.** The plugin receives the selection at invocation
  time or not at all.

---

## 3. Architecture

The single most important structural constraint, discovered during the spike:

> **The action and the popup are two separate processes.** A keybinding can
> only invoke an action; only an action can open a popup pane.

This dictates where the AI call lives.

```text
  Ctrl+B  Alt+T
       │
       ▼
  ┌──────────────────────────────────────────┐
  │ PROCESS 1 — action `lens-translate`      │   short-lived, must exit fast
  │                                          │   measured: ~490 ms on WSL
  │  acquire selection (§15):                │
  │    context.selected_text → clipboard     │
  │  write job file, mode 0600, in state dir │
  │  herdr plugin pane open                  │
  │      --entrypoint viewer --focus         │
  │      --env LENS_JOB=<path>               │
  └──────────────────────────────────────────┘
       │
       ▼
  ┌──────────────────────────────────────────┐
  │ PROCESS 2 — pane `viewer` (popup)        │   the actual UI
  │                                          │
  │  paint frame + spinner IMMEDIATELY       │
  │  ├─ worker thread: Provider.translate()  │
  │  └─ main thread: input loop              │
  │        j/k/PgUp/PgDn/wheel → scroll      │
  │        c    → OSC 52 copy                │
  │        Esc  → exit                       │
  └──────────────────────────────────────────┘
```

**The AI request MUST happen in process 2, never in process 1.** Putting it in
the action would delay the popup until the response arrived, breaking §14. A
test asserts the action never constructs a provider.

The selection travels by **file, not env var**: selections are unbounded while
`environ` is not, and the plugin's state directory is already private. The
viewer deletes the job the moment it reads it; stale jobs from a popup that
never opened are swept after 5 minutes.

### 3.1 Module boundaries

```text
herdr-lens/
  herdr-plugin.toml
  lens/
    action.py      entry for process 1 — selection → job file → pane open.
    viewer.py      entry for process 2 — owns the TUI event loop and the AI call.
    config.py      load + auto-detect. Pure; no I/O beyond reading the file.
    selection.py   two-tier acquisition (§15). No UI, no network.
    clipboard.py   multi-backend read + OSC 52 write.
    providers/
      base.py      Provider ABC + shared HTTP; translate(text, src, tgt, prompt)
      anthropic.py
      openai.py    also serves openai-compatible endpoints
      ollama.py
    ui/
      frame.py     width-aware wrapping, scrolling, layout, spinner
```

Each provider is independently testable against a recorded HTTP fixture with no
terminal involved. `frame.py` is testable by rendering to a string buffer with
no network involved. That separation is the point.

---

## 4. Implementation Language

**Python 3, standard library only.** No third-party packages, no build step.

Rationale, in priority order against the "configuration must be simple"
requirement:

- `herdr plugin install owner/repo` must yield a working plugin. A Go or Rust
  plugin needs a `[[build]]` step and therefore a toolchain on the user's
  machine; that is a configuration burden in disguise.
- `urllib.request` covers every provider's HTTP API. `termios` + `tty` give raw
  mode. `threading` gives the async call. `base64` gives OSC 52. Nothing is
  missing.
- Startup latency of a Python process is well inside the budget for a popup
  that is about to wait on a network round-trip anyway.

The cost is hand-written TUI code instead of a framework. §3.1 keeps that cost
contained to `ui/frame.py`.

If Python 3.11+ is unavailable, the plugin fails with a clear message rather
than degrading. `tomllib` (3.11+) parses the config; there is no TOML parser in
older stdlib.

---

## 5. Configuration

### 5.1 The zero-config requirement

Herdr Lens MUST work with **no configuration file at all**. This is a hard
requirement, not a convenience.

With an empty config directory:

- Target language resolves from `$LANG` / `$LC_ALL`, falling back to `en`.
- Source language is `auto`.
- The provider is auto-detected (§5.2).
- The prompt is the built-in default (§7).

### 5.2 Provider auto-detection

When the config declares no `[ai]` table, resolve in this order and stop at the
first hit:

1. `ANTHROPIC_API_KEY` present → Anthropic API, `claude-sonnet-5`.
2. `OPENAI_API_KEY` present → OpenAI API, `gpt-4o-mini`.
3. The `claude` CLI on PATH → the Claude Code provider (§5.2.3).
4. An `ant auth login` profile on disk → Anthropic API with `auth = "oauth"`.
3. `http://localhost:11434/api/tags` answers within 300 ms → Ollama, first
   installed model.
4. Nothing found → the popup renders the setup hint from §9.

Detection result is cached in `$HERDR_PLUGIN_STATE_DIR/detected.json` so the
Ollama probe does not run on every invocation. The cache is invalidated when
the relevant environment variables change.

### 5.2.1 Default models are chosen for translation

Translation wants **accuracy and low latency**, not reasoning depth. Defaults
are therefore the fast high-quality tiers rather than the flagships, and the
request is tuned to match:

- thinking is **disabled** and `effort` set to `low`, on models that support
  those controls;
- both fields are **omitted entirely** on models that do not — `output_config`
  is a 400 on Haiku 4.5 and earlier, and an explicit disabled-thinking config
  is a 400 on Fable/Mythos. Sending a field a model rejects would break
  translation for exactly the users who picked that model for speed.

The capability mapping lives in one function with its own tests; it is the kind
of per-model detail that rots silently, so it is not scattered through the
request builder.

### 5.2.3 The Claude Code provider

Not every user has API billing. A Claude subscription — Pro, Max, Team — buys
the Claude apps, not `/v1/messages`, so for many people there is no key to
export and never will be.

The Claude Code CLI is itself a supported non-interactive interface. Lens
shells out to `claude -p` the way a script shells out to `git`: it uses the
product through its own documented surface and never reads the credential
behind it. Reusing a token issued to another product would be neither
supported nor durable; invoking the product is neither of those things.

The call is stripped down to what a translation needs:

| Flag | Why |
|---|---|
| `-p` | non-interactive, print and exit |
| `--system-prompt` | the mode's prompt, plus the framing below |
| `--disallowedTools` | a translation has no business reading the filesystem |
| `--strict-mcp-config` | the user's MCP servers are irrelevant here |
| `--no-session-persistence` | the selection must not be written to a session log |
| `cwd` = an empty directory | otherwise the project's `CLAUDE.md` is discovered and pulled in |
| `stdin` closed | the CLI otherwise waits 3 s for input that never comes |

**Two adaptations were found only by running it, not by reading docs:**

1. **The model is still framed as a coding assistant.** Handed `fatal: not a
   git repository`, it offered to help fix the repository. The system prompt
   has to state that it is a translation engine and that the input is data;
   the input has to be delimited so the boundary is unambiguous. Without this,
   selecting anything shaped like a question yields an answer, not a
   translation.
2. **Only the selection may sit inside the delimiters.** Everything in there
   gets translated — including instructions. The first version leaked
   `Target language: zh-CN.` into the output as translated prose. The target
   language now travels in the system prompt, which also makes the `target`
   argument authoritative rather than whatever the prompt template baked in.

`--disallowedTools` is variadic, so it must not be the last flag before the
positional prompt or it swallows the selection as a list of tool names. That
one is a hard failure rather than a subtle one, and it has a regression test.

**Costs, measured.** Where the time actually goes, per invocation:

| | |
|---|---|
| CLI process start | 0.08 s |
| session init + ~18 K token scaffolding prefill | ~1.8 s |
| model generation | ~1.6 s |

The scaffolding is Claude Code's own, and `--system-prompt` does not displace
it. The flags above already cut it from **29,397 tokens / 5.7 s** to **18,012
tokens / 3.4 s**; past that, flag tuning is exhausted — `--effort low` and
disabling hooks via `--settings` both measured as no-ops, and Haiku is *slower*
than Sonnet here (5.4 s vs 3.4 s).

**Output is therefore streamed** (`--output-format stream-json
--include-partial-messages --verbose`). Total time is unchanged, but the first
characters land at ~1.8 s instead of the whole answer at ~3.4 s. A popup is
judged on when it becomes readable, so this halves the latency that matters
without adding a resident process.

A persistent CLI process was measured as the alternative: 3.5 s → 1.7 s per
translation, at the cost of 371 MB resident, a socket, and a process lifecycle
to manage. Streaming captures nearly all of the perceived benefit for a
callback, so the daemon was rejected.

The provider carries its own 25 s timeout instead of the shared 15 s, enforced
by a watchdog that kills the child — `subprocess.run`'s timeout is unavailable
once output is being read incrementally. Usage draws on the Claude Code
subscription, not on API billing.

---

### 5.2.2 Credentials without an API key

Not every user has a key to export — a Claude subscription seat is a different
product from API billing. Lens therefore supports two credential modes on the
Anthropic provider, selected by `[ai].auth`:

| `auth` | Credential | Headers |
|---|---|---|
| `api_key` (default) | `$<api_key_env>` | `x-api-key` |
| `oauth` | short-lived token from `ant auth print-credentials --access-token` | `Authorization: Bearer` + `anthropic-beta: oauth-2025-04-20` |

**Exactly one credential is sent.** Supplying both headers is a 401, so the
two modes are mutually exclusive by construction rather than by convention.

Detection prefers an exported key over a stored profile, matching Anthropic's
own precedence. The profile check is a filesystem test (`ant` on PATH plus a
non-empty credentials directory), not a subprocess, so it stays off the path to
first paint.

Tokens are fetched per request rather than cached: they are short-lived, and
the CLI refreshes them transparently.

Two failure modes get named errors instead of opaque ones — the CLI not being
installed or not signed in, and `print-credentials` returning its full JSON
document because `--access-token` was omitted (which would otherwise be sent
as a bearer token and fail as a protocol error).

**Whether a given subscription can reach `/v1/messages` this way is outside
Lens's control**, and is not asserted anywhere in this spec. The design goal is
that the failure is legible when it happens.

---

### 5.3 Config file

Location: `$HERDR_PLUGIN_CONFIG_DIR/config.toml`
(`~/.config/herdr/plugins/config/herdr-lens/config.toml`).

**Every key is optional.** The realistic config, and the only line most users
will ever write:

```toml
target_language = "zh-CN"
```

The full surface, for users who want control:

```toml
target_language = "ja"      # default: from $LANG, else "en"
source_language = "auto"    # default: "auto"

[ai]
provider = "openai-compatible"   # openai | anthropic | ollama | openai-compatible
model = "my-model"
endpoint = "https://example.com/v1"
api_key_env = "MY_AI_API_KEY"    # variable NAME, never the key itself

[prompt]
translation = """
...overrides the built-in prompt; {target_language} is substituted...
"""
```

Language codes are BCP-47-ish (`zh-CN`, `zh-TW`, `ja`, `en`, `ko`, `fr`, `de`,
`es`). They are passed to the model as-is; no validation table is maintained.

### 5.4 Credentials

API keys MUST NOT appear in the config file. `api_key_env` names an environment
variable; the plugin reads it at invocation time. A config containing a literal
`api_key` is rejected at load with an explicit error rather than silently
honoured, so the mistake surfaces immediately.

### 5.5 Installation

The complete install, as documented to users:

```bash
herdr plugin install <owner>/herdr-lens
```

then one block in `~/.config/herdr/config.toml`:

```toml
[[keys.command]]
key = "prefix+alt+t"
type = "plugin_action"
command = "lens-translate"
description = "Translate selection"
```

followed by `herdr server reload-config`. That is the entire setup for a user
who already has `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` exported.

---

## 6. Actions

```text
lens-translate       what the selection needs, decided from the selection
lens-explain         what this is and what it says
lens-summarize       what a long stretch of output amounts to
```

**Translate infers; explain and summarise are told.** The same selection can
legitimately be translated *or* explained, and no amount of looking at the text
resolves that — it is a fact about the reader's intent. So classification
decides the translate path, and the key decides the other two. A forced mode
overrides classification entirely: pressing summarise on one sentence still
summarises. Junk rejection still applies, because there is nothing to summarise
about box drawing either.

`lens-summarize` carries its own input ceiling of 40 000 characters against the
8 000 used elsewhere: selecting a lot is the point of summarising.

**Action ids resolve in a single global namespace across every installed
plugin** — a keybinding's `command` is a bare action id with no plugin prefix,
and a duplicate id across two plugins is ambiguous. Ids are therefore prefixed
by hand: `lens-translate`, not `translate`.

`lens-ask` — selecting text and typing a question — is deliberately not built.
Once the question is fixed ("what is this", "what does this say") it *is*
explain, and a fixed question needs no input field. What would remain is a text
input in the popup, which is the only part of this design that would touch the
TUI's input handling; the value did not justify it while a Claude Code pane sits
one keystroke away.

Herdr Lens ships no default keybinding. It declares the action; the user binds
it. §5.5 gives the recommended binding.

---

## 6.1 Selection modes

A selection is routed by what it *is*, decided locally before any request:

| Mode | Trigger | Output |
|---|---|---|
| `JUNK` | no letters; letters < 25% of visible chars; box/block chars ≥ 40%; a 32+ char identifier-safe run | rejected in-process, instantly |
| `WORD` | 2–32 Latin letters, **or** ≤8 letters in a script that does not space its words | reading, senses, two examples |
| `TERM` | a single token that is not a word: internal punctuation, digits, or an ALL-CAPS shape | the identifier reproduced, then explained |
| `GENERAL` | everything else | translation, or a command line explained with its syntax intact |

**`TERM` earns its own mode because one prompt cannot reliably do two jobs.**
Asked to translate *or* explain depending on the input, the model guessed, and
bare identifiers came back untouched roughly half the time. The distinction is
trivially detectable locally — an identifier is a single token that is not a
word — so splitting it out lets each prompt do exactly one thing.

**Scripts without spaces need a length bound.** Japanese, Chinese, and Korean
put no spaces between words, so a single token says nothing about whether it is
a word or a whole sentence. The Latin-only pattern sent every one of them to
`GENERAL`, which meant looking up 既定 returned a bare translation rather than
an entry — the one thing a lookup exists to avoid. Length is the only signal
available: eight characters holds a compound noun (コンピューター) and excludes
anything sentence-shaped.

Two edges that a naive "single token" rule gets wrong, both under test:

- **Trailing punctuation does not make a word an identifier.** `Hello!` is
  prose; the check strips edge punctuation before deciding.
- **A token of pure letters is a word in any script.** `기본적으로` must not be
  read as a symbol merely because the word pattern only recognises Latin.

**Where judgment lives is the design decision.** Whether something *looks* like
a word is cheap and deterministic, so code decides it. Whether a word is really
a command name (`grep`, `argv`), and whether a passage is prose or a shell
invocation, are fuzzy — the model decides those, and the word prompt carries an
explicit instruction to abandon the dictionary shape when the input is not a
lexical item.

Junk rules are evaluated **before** the word rule, so junk detection never
depends on `word_lookup`. That lets the popup ask "is this junk?" before
reading the config file, keeping config load off the path to first paint.

The rules are deliberately conservative. Waving something through costs one
wasted request; a false rejection reads as the plugin being broken.

**ALL-CAPS single tokens are never dictionary words.** `SIGTERM`, `PATH`, and
`TODO` are abbreviations — they fall through to `GENERAL` and get explained.

### 6.2 Fast failure

`JUNK` produces no provider construction, no network request, and no spinner —
the popup opens directly on its final message. This is the point: the cost of
a mis-drag should be a glance, not a wait.

Two related bounds:

- **Input cap 8000 characters.** Over that, the head is translated and the
  status line says so. Truncating beats refusing — selecting a long passage of
  documentation is legitimate; 8000+ is a stray select-all.
- **Request timeout 15 s**, naming the provider and model that stalled.

---

## 6.3 Source language

`source_language` takes a code, a list of codes, or `auto`.

**Detecting the language of one word is not solvable by detection.** Kana and
Hangul identify themselves, but a Han-only token is genuinely ambiguous —
`東京` is valid Japanese and valid Traditional Chinese, and purpose-built
libraries state that they need long passages to work at all. Chasing better
detection here is chasing a problem the field has not solved.

So Lens does not detect. It intersects two things that are each cheap and
certain:

| | |
|---|---|
| **Script** | decidable from the characters. Kana → Japanese. Hangul → Korean. Han → undecided. |
| **Configured candidates** | the languages the user actually reads. |

With `["en", "ja"]`, a Han-only word cannot be English, so it is Japanese —
a deduction rather than a guess. Kana outranks candidate order, because
Japanese mixes kana with kanji and any kana settles it.

Three deliberate choices in the fallbacks:

- A candidate list that rules out the detected script is treated as a
  misconfiguration: Lens falls back to the script rather than asserting
  something it can see is false. Hangul reports Korean even when Korean is not
  a candidate.
- Latin script with no candidates yields **no hint at all**. Listing every
  language that uses the Latin alphabet tells the model nothing it cannot see,
  and crowds the prompt.
- Han with no candidates states the ambiguity in words rather than picking a
  side.

---

## 7. Prompt

Built-in default, used whenever `[prompt].translation` is absent:

```text
Translate the following terminal text into {target_language}.

Preserve exactly, without translating:
- commands and command syntax
- code
- file paths
- option and flag names
- environment variables
- technical identifiers

Translate only prose. If the input is purely a command, explain what it does
in {target_language} while leaving the command itself verbatim.

Be concise. Output only the translation, with no preamble.
```

The distinction matters for terminal content: `git config --global
core.autocrlf false` must come back as an explanation with the command intact,
not as translated prose.

---

## 7.1 Credentials

Three sources, consulted in order and stopping at the first that yields a key:
`api_key_env`, `api_key_file`, `api_key_command`.

An environment variable is the conventional answer and the wrong default here.
**Herdr is a long-lived server, and plugin processes inherit the environment it
was started with** — so `export` in a shell never reaches them, and the fix
(restarting the server) closes every pane the user has open, agents included.
The failure is silent and misleading: the variable is plainly set in the shell
where the user checks it. The error message therefore names that cause
explicitly rather than only reporting the variable as unset.

`api_key_file` must be mode 600; Lens refuses to read a key other users can.
`api_key_command` covers `pass`, keychain helpers, and anything else that
prints a secret to stdout.

## 7.2 Outbound HTTP

**Every request carries a `User-Agent`.** urllib's default is
`Python-urllib/3.x`, which the Cloudflare tier in front of Groq rejects with a
bare `HTTP 403 / error code 1010` — no mention of the client, nothing that
points at the header. curl succeeded against the same endpoint with the same
key, which is what isolated it. Identifying the client is correct regardless;
this is the case that makes it non-optional.

## 8. Provider Interface

```python
class Provider(ABC):
    @abstractmethod
    def translate(self, text: str, source: str, target: str,
                  prompt: str) -> str: ...
```

The provider owns authentication, request construction, model selection, and
response parsing. `viewer.py` imports `Provider` and nothing provider-specific.

Adding a provider means adding one file under `providers/` and one line in the
registry. Anything speaking the OpenAI protocol — Groq, OpenRouter, Together,
vLLM, an in-house gateway — is a subclass with a different `default_endpoint`
and nothing else; `groq` exists as a named provider only so the config does not
have to carry a URL. The acceptance criterion in §14 that a local provider be addable
"without redesigning the core" is satisfied by construction.

Timeouts: 30 s connect+read. One retry on connection error, none on HTTP 4xx.

---

## 9. Errors

All errors render in the same popup, in the same frame as a successful result.
The popup always appears; it never fails to open because of an error condition.

| Condition | Message |
|---|---|
| Empty selection | `No text selected.` / `Select text in a pane with the mouse, then press the key again.` / `Herdr Lens reads the selection from the clipboard, so copy_on_select must stay enabled (it is on by default).` |
| No provider found | `No AI provider configured.` / `Export OPENAI_API_KEY or ANTHROPIC_API_KEY, or run Ollama on localhost:11434.` / `See: <config path>` |
| Named env var unset | `<VAR> is not set.` / `The config points at this variable but it is empty.` |
| Network failure | `Cannot reach the AI provider.` / `<endpoint host>` |
| Provider HTTP error | `Provider returned an error.` / `Provider: <name>  Model: <model>  HTTP <code>` / first 200 chars of body |
| Empty response | `The provider returned an empty response.` |
| Python too old | `Herdr Lens needs Python 3.11 or newer. Found <version>.` |

Every message states what to do next, not only what went wrong.

---

## 10. Popup UI

`placement = "popup"`, `width = "47%"`, `height = "44%"`. Prose reads badly
across a full-width terminal; the extra height pays for the narrower measure.

```text
┌─ Translation ────────────────────────┐
│                                      │
│ translated text, wrapped to width    │
│                                      │
│                                      │
├──────────────────────────────────────┤
│ [c] copy          [j/k] scroll  [Esc]│
└──────────────────────────────────────┘
```

Loading state, painted before the request is issued:

```text
┌─ Translating… ───────────────────────┐
│                                      │
│                 ⠋                    │
│                                      │
└──────────────────────────────────────┘
```

Requirements:

- MUST overlay the terminal without disturbing pane state. (Herdr guarantees
  this for popup panes.)
- MUST dismiss on `Esc`.
- MUST scroll when content exceeds the frame: `j`/`k`, arrows, `PgUp`/`PgDn`,
  and mouse wheel.
- MUST show a scrollbar on the right edge when, and only when, content
  overflows — with a `first-last/total` position in the header alongside it.
  The gutter is claimed by re-wrapping rather than reserved permanently, so a
  result that fits keeps the full width. The thumb is sized by the visible
  fraction and never shrinks below one row.
- MUST colour the body, additively. Styling is computed as spans over the
  plain line and emitted in one pass **after** padding, so wrapping and width
  never see an escape. The invariant — stripping the escapes returns the
  original line — is asserted directly, because a violation would corrupt
  layout rather than merely look wrong. Colours are the basic ANSI set so the
  terminal theme picks the hues: bold headword, bold-cyan identifier, cyan
  backticked code, dim for pronunciation, part-of-speech labels, and list
  numbers. Row-anchored rules carry the row index, so scrolling cannot paint an
  arbitrary line as a headword.
- MUST hang list continuations under their text: `1. ` and `- ` markers set the
  continuation indent, or a wrapped example reads as a new item.
- MUST NOT begin a line with CJK closing punctuation. A stranded `。` is the
  most visible way wrapped Chinese looks broken; the split backs up rather than
  overflowing.
- MUST preserve a line's indent across wrapping. Option lists and indented
  man-page blocks are the plugin's most common input; letting continuations
  fall to column zero destroys exactly the structure that makes them readable.
  An indent wider than half the pane is dropped rather than honoured.
- MUST copy the result on `c`, via OSC 52, with a transient `copied` confirmation.
- Spinner animates at 10 Hz on the main thread while the worker is in flight.

---

## 11. Performance

The popup MUST be painted before the AI request is issued. The measured budget
from keypress to first paint is process-1 exit plus process-2 startup; nothing
in that path may block on the network.

Herdr is never blocked: both processes are detached from the terminal's own
event loop.

---

## 12. Privacy

Only `selected_text` is transmitted. The invocation context also carries
`workspace_cwd`, `focused_pane_cwd`, and agent status — **none of these are
sent to the provider**, and this is asserted by a test.

Never sent: terminal scrollback, other panes, environment variables, file
contents, the current directory.

Nothing is transmitted without an explicit keypress. There is no automatic or
background translation.

The README states plainly, above the fold, that selected text leaves the
machine and reaches the configured provider.

---

## 13. Testing

- **Providers** — each against a recorded fixture; request shape, response
  parsing, and every error branch in §9. No network, no terminal.
- **Config** — the zero-config path, each auto-detection branch, precedence of
  explicit config over detection, and rejection of a literal `api_key`.
- **Frame** — render to a string buffer: wrapping, scroll offsets, overflow
  indicator, resize. No terminal.
- **Privacy** — assert the outbound request body contains the selection and
  none of the context fields listed in §12.
- **End-to-end** — link the plugin, invoke via `herdr plugin action invoke`,
  assert the popup opens and exits 0. This is the one test that needs a running
  Herdr.

---

## 14. MVP Acceptance Criteria

- [ ] `herdr plugin install` yields a working plugin with no further steps
      beyond one keybinding, given an already-exported API key.
- [ ] Plugin runs correctly with an entirely empty config directory.
- [ ] Selecting text and pressing the bound key translates it, with no manual
      copy or paste.
- [ ] Target language configurable; source language `auto` or explicit.
- [ ] At least one cloud provider works end to end.
- [ ] A local/OpenAI-compatible provider is addable as one new file.
- [ ] No credential is ever required in the config file.
- [ ] Result renders in a Herdr popup that scrolls.
- [ ] `c` copies; `Esc` closes.
- [ ] The popup is visible before the AI request is issued.
- [ ] Every error in §9 renders in the popup with an actionable next step.
- [ ] Only the selection reaches the provider — asserted by test.

---

## 15. Resolved: how the selection actually arrives

**`selected_text` is not populated on the keybinding path in Herdr 0.8.0.**
Verified directly: with a live mouse selection and `invocation_source:
"keybinding"`, the context JSON contains no `selected_text` key. Setting
`copy_on_select = false` (which retains the highlight instead of clearing it on
mouse-up) does not change this, so the cause is not a cleared selection — the
field simply isn't filled for this invocation source.

**The clipboard is the delivery mechanism.** Herdr's `copy_on_select` defaults
to `true`, so releasing the mouse has already put the selection on the system
clipboard. The user's gesture is unchanged — select, press the key — and only
the plugin's internals differ.

`SelectionProvider` therefore returns, in two tiers (not v1's four):

```text
1. context.selected_text   preferred; costs nothing to try, and starts
                           working for free if Herdr populates it later
2. system clipboard        the path that actually fires today
```

Clipboard backends are probed native-first: `wl-paste` → `xclip` → `xsel` →
`pbpaste` → `win32yank.exe` → `powershell.exe`. On WSL the clipboard lives on
the Windows side, so an interop binary is required; measured cost there is
**~370 ms**, which the popup hides because it is already painted (§11).

**Consequences the design must carry:**

- Acquisition latency is non-zero and platform-dependent. It sits in process 1,
  before the popup opens — so §11's budget is process-1 exit *plus* clipboard
  read, and the popup must still paint before any network call.
- A user who has set `copy_on_select = false` cannot be read. The empty-selection
  error in §9 names this explicitly rather than saying "no text selected".
- The clipboard is shared state. Lens reads it and never writes to it except on
  an explicit `c` keypress.

---

## 16. Future Capabilities

The architecture accommodates these without restructuring, because the action
is a thin shim and the provider interface is prompt-driven:

- **Explain** — same pipeline, different prompt. New action id, no new plumbing.
- **Summarize** — same, aimed at man pages, build logs, CI output.
- **Ask** — needs an input line in `ui/frame.py`; the provider interface gains
  a `question` parameter. The only future capability that touches the UI layer.
- **Language profiles** — a `[profiles]` table plus per-profile action ids, so
  a user can bind separate keys for separate target languages.

Positioning stays broader than translation: *Herdr Lens — understand anything
in your terminal.* Translate is the first lens, not the product.
