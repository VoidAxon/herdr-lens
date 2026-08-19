# herdr-lens

[![CI](https://github.com/VoidAxon/herdr-lens/actions/workflows/ci.yml/badge.svg)](https://github.com/VoidAxon/herdr-lens/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776ab.svg)
![herdr 0.8+](https://img.shields.io/badge/herdr-0.8%2B-8a2be2)
![platforms: linux • macOS](https://img.shields.io/badge/platforms-linux%20%E2%80%A2%20macOS-informational)

**Select text in a Herdr pane, press a key, read it in your own language.** Lens
works out what you selected — a passage, a single word, a bare identifier, a
screenful of build output — and answers in the shape that fits. No config file
required, no API key if you already have Claude Code.

https://github.com/user-attachments/assets/1e30daca-74a9-4bfc-9873-679cf247c9fe

*A word looked up in a man page, then a paragraph translated — the same key both
times, deciding for itself which one you meant.*


> [!IMPORTANT]
> **Selected text leaves your machine.** It goes to whichever AI provider you
> configure. Nothing is sent without an explicit keypress, and only the
> selection is sent — never your scrollback, other panes, environment, or paths.
> Read [Privacy](#privacy) before you bind the key, and use a local model if the
> text must not travel.

## Quick start

```bash
herdr plugin install VoidAxon/herdr-lens
herdr plugin action invoke lens-setup
```

Select text in any pane with the mouse, then press <kbd>Ctrl-B</kbd>
<kbd>Alt-T</kbd>. That is the whole thing.

There is no config file to write. Lens takes your language from `$LANG` and finds
a provider on its own — an exported `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`GROQ_API_KEY` or `GEMINI_API_KEY`, the `claude` CLI already on your `PATH`, or
Ollama on localhost. If you have a Claude subscription, that last-but-one is you,
and you need no API key at all.

The one line most people end up writing, in
`~/.config/herdr/plugins/config/herdr-lens/config.toml`:

```toml
target_language = "ja"    # default: guessed from $LANG
```

Two more keys come with `lens-setup`: <kbd>Ctrl-B</kbd> <kbd>Alt-E</kbd> explains
what something is, <kbd>Ctrl-B</kbd> <kbd>Alt-S</kbd> summarises a screenful of
output. Everything below is detail you can come back for.

## Why you'd want it

- **One key, four answers.** A passage gets translated. A single word gets its
  reading, its senses, and two examples. A bare `SIGTERM` or `--global` gets
  identified rather than mangled into nonsense. A screen of build output gets
  summarised, error codes intact. You do not pick a mode; the selection decides.
- **Nothing to configure.** With no config file at all, Lens takes your target
  language from `$LANG` and finds a provider: an exported key, the `claude` CLI
  already on your `PATH`, or Ollama on localhost. One line of TOML changes the
  language; you may never write a second.
- **Fails fast on nonsense.** Select a hash, a box-drawing border, or a row of
  punctuation and the popup says so immediately — locally, without spending a
  request or your time.
- **Reads like the terminal it came from.** Commands, flags, paths, and
  identifiers are reproduced verbatim and coloured as code, never translated.
  CJK text wraps on its own rules, and long results scroll.
- **Your subscription counts.** If you have Claude Code, Lens will use it, so a
  Claude subscription needs no API key at all.
- **Safe with hostile text.** Control sequences are stripped from everything
  Lens renders, so a crafted selection cannot write your clipboard or repaint
  your screen. See [Security](#security).

## Requirements

| | |
|---|---|
| Herdr | 0.8.0 or newer |
| Python | 3.11+ (for `tomllib`), standard library only — nothing to `pip install` |
| Platforms | Linux and macOS, verified. Windows implemented but untested — see [Windows](#windows). WSL counts as Linux. |
| A provider | An API key, the `claude` CLI, or Ollama — see [Configuration](#configuration) |

## Keybindings

`lens-setup` adds the keys and reloads Herdr. Herdr's plugin manifest has nowhere
to declare a key, so some setup step is unavoidable — that is it, and everything
below is only for doing it differently.

It installs `prefix+alt+t`, `prefix+alt+e`, `prefix+alt+s`, and `prefix+t`. The
keys go in a marked block and only that block is ever rewritten, so the
step is both repeatable and reversible: your comments and bindings are carried
across untouched, a key already taken is reported and skipped rather than
stolen, re-running replaces the block instead of appending a second copy, and

```bash
herdr plugin action invoke lens-remove-keys
```

takes them back out exactly. The previous config is copied to
`config.toml.lens-backup` before either write. A config that does not parse is
refused rather than appended to — Herdr ignores such a file entirely, so keys
written into it would not load anyway.

Prefer to do it by hand, or want a different key? The blocks are plain:

```toml
[[keys.command]]
key = "prefix+alt+t"
type = "plugin_action"
command = "lens-translate"   # or lens-explain, lens-summarize
description = "Translate selection"
```

<kbd>Ctrl-T</kbd> is the nicest key for translate and Lens will happily take
it, but it is `transpose-chars` in readline, so it is offered rather than
installed. Add it yourself if you never use that.

Fumbling <kbd>Alt</kbd> on `prefix+alt+t` lands on `prefix+t`, which is also
translate — so the frequent one has no wrong answer. There is no such net for
`e` and `s`: a fumbled <kbd>Alt</kbd> reaches Herdr's own `prefix+e` (edit the
scrollback) and `prefix+s` (settings). Recoverable, but startling the first time.

## Use

Select text with the mouse, then press a key for what you want:

| Key | |
|---|---|
| <kbd>Ctrl-B</kbd> <kbd>Alt-T</kbd> | **translate** — and, depending on the selection, look up or identify it |
| <kbd>Ctrl-B</kbd> <kbd>Alt-E</kbd> | **explain** — what is this, what is it saying |
| <kbd>Ctrl-B</kbd> <kbd>Alt-S</kbd> | **summarise** — a log, a build trace, a man page |

One letter each: t, e, s. They sit behind <kbd>Alt</kbd> because <kbd>Ctrl-B</kbd>
<kbd>E</kbd> and <kbd>Ctrl-B</kbd> <kbd>S</kbd> are Herdr's own — edit the
scrollback, and open settings. Fumble the <kbd>Alt</kbd> and that is where you
land; <kbd>Ctrl-B</kbd> <kbd>T</kbd> is also bound to translate so the frequent
one has no wrong answer.

Translate decides what to do from the selection. Explain and summarise are
requests: the same text can legitimately be translated *or* explained, and
nothing about the text says which — so the key does.

Summarising accepts a much larger selection (40 000 characters against 8 000),
because selecting a lot is the point of it.

Lens looks at what you selected and answers in kind:

| You selected | You get |
|---|---|
| a sentence or passage | the translation |
| a single word (`verbose`, `既定`, `冗長`) | reading, senses, and two examples |
| a bare identifier (`SIGTERM`, `--global`, `$PATH`) | the identifier kept intact, plus what it is |
| a command line (`git config --global …`) | the command kept intact, plus what it does |
| punctuation, box art, a hash | an instant "nothing to translate" — no request is made |

```
┌─ Dictionary ─────────────────────────────────┐
│ prefix  /ˈpriːfɪks/                          │
│                                              │
│ n.   前缀                                     │
│ v.   给…加前缀                                │
│                                              │
│ 1. Add a prefix to every generated filename. │
│    给每个生成的文件名加上前缀。                 │
│ 2. The command accepts a prefix argument.    │
│    该命令接受一个前缀参数。                     │
└──────────────────────────────────────────────┘
```

Whether something *looks* like a word or a bare identifier is decided locally,
in microseconds. Whether a given word is really a command name, and whether a
passage is prose or a shell invocation, are left to the model.

Each shape gets its own prompt rather than one prompt with branches. That is
not tidiness: asked to either translate *or* explain depending on the input, the
model guesses, and bare identifiers came back untranslated about half the time.

Lookups work in any script. The reading follows the word's own language — IPA
for English, kana for Japanese, pinyin for Chinese — and the part of speech is
named the way that language names it. When the word is already in your target
language you get a monolingual entry, without each example repeated as its own
translation.

Word lookup can be switched off with `word_lookup = false`.

Output is coloured: the headword or identifier stands out, pronunciation and
list markers recede, and anything the model put in backticks is tinted. The
palette is the basic ANSI set, so it follows your terminal theme.

Long results scroll. A bar appears on the right edge with the position
(`6-15/15`) in the header; neither takes a column when the result fits. Wrapped
lines keep their original indent, so an option list or a man-page block holds
its shape instead of collapsing to column zero.

| Key | |
|---|---|
| `c` | copy the result |
| `j` / `k` / arrows / `PgUp` / `PgDn` / wheel | scroll |
| `g` / `G` | jump to top / bottom |
| `Esc` / `q` | close |

## Configuration

Optional. The file lives at
`~/.config/herdr/plugins/config/herdr-lens/config.toml`
(`herdr plugin config-dir herdr-lens` prints the directory).

With no file at all, Herdr Lens picks your target language from `$LANG` and
detects a provider in this order:

1. `ANTHROPIC_API_KEY` is set → Anthropic API, `claude-sonnet-5`
2. `OPENAI_API_KEY` is set → OpenAI API, `gpt-4o-mini`
3. `GROQ_API_KEY` is set → Groq, `openai/gpt-oss-120b`
4. `GEMINI_API_KEY` is set → Gemini, `gemini-3.7-flash`
5. the `claude` CLI is on PATH → **Claude Code** (see below)
6. an `ant auth login` profile exists → Anthropic API over OAuth
7. Ollama answering on `localhost:11434` → its first installed model
8. none of the above → the popup tells you what to do

An exported key outranks everything else, matching Anthropic's own credential
precedence — a key means you want the API directly, which is faster and does
not draw on your Claude Code usage.

The line most people will write, and nothing else:

```toml
target_language = "zh-CN"
```

Everything available:

```toml
target_language = "ja"           # default: from $LANG, else "en"
source_language = ["en", "ja"]   # a code, a list of codes, or "auto"
word_lookup = true          # false disables dictionary mode entirely

[ai]
provider = "openai-compatible"   # anthropic | claude-code | openai | groq |
                                 # gemini | ollama | openai-compatible
model = "my-model"
endpoint = "https://example.com/v1"
api_key_env = "MY_AI_API_KEY"    # the variable NAME — never the key itself
api_key_file = "~/.config/herdr/plugins/config/herdr-lens/groq.key"
api_key_command = "pass show groq/api"   # or a keychain helper
timeout = 15                     # seconds; raise it for slow models

# Optional: a different provider for single-word lookups only. Naming the
# provider is enough — it uses that provider's own default model.
[ai.word]
provider = "claude-code"

[popup]
width  = "47%"                   # overrides the plugin's own default
height = "44%"

[prompt]
translation = """..."""   # sentences and passages
word        = """..."""   # single-word lookups
term        = """..."""   # bare identifiers
explain     = """..."""   # the explain key
summarize   = """..."""   # the summarise key
```

Every prompt is overridable, so tuning the output shape never means editing
code — and `[popup]` keeps the window size out of the plugin's manifest, which
is code too.

### Limits

A selection over 8000 characters is truncated to that and marked in the status
line — long enough for any passage worth reading in a popup, short enough that
a stray select-all does not become an expensive request.

A provider that has not answered in 15 seconds is given up on, naming the
provider and model that stalled. A translation popup that spins for half a
minute has already failed. Raise `timeout` under `[ai]` if you run a slow local
model; it accepts 0–600 seconds.

A literal `api_key` in this file is rejected at load with an explicit error
rather than silently honoured.

**An environment variable may not reach the plugin.** Herdr runs as a
long-lived server and plugin processes inherit the environment *it* was started
with, so exporting a key in a shell afterwards does not reach them until the
server restarts — and restarting it closes every pane it owns. `api_key_file`
(mode 600; Lens refuses to read it otherwise) and `api_key_command` both work
immediately. All three are consulted in that order, so configuring an env var
*and* a file gives you the file today and the variable after the next restart.

### Using your Claude Code subscription

If you have a Claude subscription rather than API billing, you have no key to
export — and you do not need one. When the `claude` CLI is on your PATH, Lens
runs it in non-interactive mode:

```toml
[ai]
provider = "claude-code"
model = "sonnet"        # a CLI alias: sonnet | haiku | opus, or a full model id
```

Detected automatically, so in practice you write nothing at all.

Lens shells out to `claude -p` the way a script shells out to `git`: it uses
the product through its own documented interface and never touches the
credential behind it. Tools, MCP servers, session persistence, and project
`CLAUDE.md` discovery are all switched off for the call — a translation has no
business reading your filesystem.

Two costs to know about:

- **It is slower.** A sentence takes roughly 3–4 s against 1–2 s for a direct
  API call. Output streams in, so the first characters appear at about 1.8 s
  and you start reading well before it finishes.
- **It draws on your Claude Code usage**, not on separate API billing.

### Groq

Groq speaks the OpenAI protocol, so it needs nothing but a key:

```bash
export GROQ_API_KEY=gsk_...
```

Detected automatically. To pin the model:

```toml
[ai]
provider = "groq"
model = "openai/gpt-oss-120b"      # or openai/gpt-oss-20b, roughly twice as fast
api_key_env = "GROQ_API_KEY"
```

Groq's draw is throughput — hundreds of tokens per second, well above the
other hosted options — which for a popup you are waiting on is the number that
matters. Weigh it against translation quality: the Llama models are strong on
English prose, and worth checking on your own material before committing if you
lean on the dictionary entries, which ask for kana readings, pinyin, and
part-of-speech naming in the word's own language.

### Gemini

Gemini speaks the OpenAI protocol, so it needs no more than a name:

```toml
[ai]
provider = "gemini"
api_key_env = "GEMINI_API_KEY"
```

The endpoint defaults to `https://generativelanguage.googleapis.com/v1beta/openai`
and stays overridable, because Google has already moved this path once — it was
`/v1beta/chat/completions` before `/v1beta/openai/chat/completions`.

Model names change often here. A retired one gives a 404 that says how to list
the current ones.

> [!WARNING]
> **On Gemini's free tier, Google uses your content to improve their products.**
> Their pricing page marks "Content used to improve our products" as *Yes* for
> the free tier and *No* for paid. Everything you press the key on would be in
> scope. For work code, internal identifiers, or customer data, that is a
> different decision from choosing a fast provider — use the paid tier, another
> provider, or a local model.

### Telling Lens what you read

`source_language` accepts a list, and it is worth filling in if you read more
than one language.

The reason is narrow but real: a Han-only word such as 既定 is valid Japanese
*and* valid Chinese, and no detector can separate them from a single word —
the libraries built for this say plainly that they need long passages. Even 東京
defeats them.

Naming your languages sidesteps the problem rather than attacking it. With

```toml
source_language = ["en", "ja"]
```

a Han-only word cannot be English, so it can only be Japanese. That is a
deduction, not a guess. English words still resolve as English, because the
script settles those on its own.

Left at `auto`, Lens tells the model the word is "Chinese or Japanese, written
in Han characters with no kana" and lets it decide — honest about the
ambiguity, but a coin flip on words like 既定.

### Signing in without an API key

The Anthropic CLI stores a login profile Lens can borrow, for API access
without a static key:

```bash
ant auth login          # opens a browser, stores a profile locally
```

Lens then picks it up automatically — no config file needed. To pin it
explicitly:

```toml
[ai]
provider = "anthropic"
auth = "oauth"          # "api_key" (default) or "oauth"
model = "claude-sonnet-5"
```

In this mode Lens asks the CLI for a short-lived token per request
(`ant auth print-credentials --access-token`) and sends it as a bearer token.
No key is ever stored in Lens's config.

> **A subscription is not the same thing as API access.** Whether your seat can
> call `/v1/messages` this way depends on your organization's setup. If it
> can't, you get an explicit error rather than a silent failure — and the
> Claude Code path above needs no credentials at all.

### A second provider for the dictionary

Pronunciation is the one output where a fast hosted model was wrong in a way
that matters. Asked for `verbose`, Groq's `gpt-oss-120b` returned `/ˈvɜːrbəs/`
six times in eight — that is "VER-bus" for a word said "ver-BOSE", stress and
vowel both wrong. A dictionary entry reads as authoritative, so a wrong reading
does not stay on the screen; it ends up in your speech.

```toml
[ai.word]
provider = "claude-code"
```

Single-word lookups then go to the slower, more accurate provider and everything
else stays where it was:

| Selection | Provider | |
|---|---|---|
| `verbose` | Claude Code · sonnet | 7.5s |
| `既定` | Claude Code · sonnet | 6.4s |
| a sentence | Groq · gpt-oss-120b | 0.4s |
| `SIGTERM` | Groq · gpt-oss-120b | 0.5s |
| a build log | Groq · gpt-oss-120b | 0.8s |

Only this one mode is routed. Sentences, identifiers and summaries were accurate
on the fast provider, and sending them here too would cost every translation
seven times the latency to fix a problem they do not have. A word lookup is also
the one place where waiting is tolerable — you are reading to learn, not
skimming.

Note that Japanese readings were already correct on the fast provider (`既定` →
`きてい`, kana rather than romaji). The gap is specifically English IPA.

### Picking a model

Defaults are tuned for translation — accuracy at low latency — not for
reasoning depth. Lens also turns thinking **off** and drops effort to `low` on
models that support those controls, because translation is not a reasoning
task and thinking only adds delay before the first word appears.

| Want | `model` |
|---|---|
| the default: accurate and fast | `claude-sonnet-5` |
| cheapest | `claude-haiku-4-5` |
| hardest passages, latency no object | `claude-opus-5` |

**Haiku is not faster on the Claude Code path** — measured 5.4 s against
Sonnet's 3.4 s on the same input. Pick it to save money, not time.

```toml
[ai]
provider = "anthropic"
model = "claude-haiku-4-5"
```

Request tuning is applied per model, so picking an older model that rejects
`output_config` does not break the request — Lens simply omits the fields it
cannot use.

### If the popup looks like a black box

Herdr paints an opaque background behind popup panes, so on a terminal with
transparency the popup is the one opaque rectangle on screen. Lens cannot fix
that from its side — it emits no background colour at all. Herdr can:

```toml
# ~/.config/herdr/config.toml
[theme.custom]
panel_bg = "reset"
```

`reset` falls back to the terminal's own background. It is not in Herdr's
published config reference — the only record is a commented line in the
binary's own config template. Note that `herdr config check` does not validate
colour values, so a typo here fails silently rather than reporting an error.

## Without Herdr

The plugin needs Herdr because a keybinding cannot open a popup by itself. The
translation never did, so the same thing runs as a plain command — in any
terminal, and in pipes, which no popup can serve:

```bash
lens "By default, grep prints the matching lines."
#   默认情况下，grep 会打印匹配的行。

lens verbose                       # a word still gets the dictionary
kubectl logs pod-xyz | lens --summarize
lens --explain 'git rebase --onto main feature~3 feature'
lens --target ja "…"               # override the configured language
```

From a clone, `python3 -m lens`. To get the short name:

```bash
alias lens='python3 -m lens'       # with the clone on PYTHONPATH
```

It shares the plugin's config file, so a provider set up for one works for the
other. Output streams when stdout is a terminal and arrives whole when it is a
pipe — a partial value down a pipe reads as a complete one.

## Windows

WSL works and is what this is developed against: the plugin sees Linux, and
nothing here knows the difference.

Native Windows is **supported but unverified**. Everything platform-specific has
a Windows path — the popup reads keys through `msvcrt` and translates console
key codes into the same ANSI sequences the POSIX side produces, Neovim is looked
up through `\\.\pipe` instead of a Unix socket, and child processes are found
with `Get-CimInstance` where there is no `/proc`. None of it can be exercised
from a POSIX machine, so it is asserted in shape only.

What is verified even so: every module imports without `termios` or `tty`, the
key translation table matches what the key handler actually reads, and the
version guard was checked against a real Windows Python 3.10 (it refuses, which
is correct — 3.11 is the floor).

Expect the mouse wheel to be the first thing that does not work; it needs
virtual-terminal input, which the console layer asks for but cannot insist on.
[Issues welcome](https://github.com/VoidAxon/herdr-lens/issues) — a report of
what happened is more useful than the guess in this paragraph.

## Privacy

Sent: the selected text, and only that.

Never sent: terminal scrollback, other panes, environment variables, file
contents, your current directory. Herdr hands the plugin a context object
containing several of these — a test asserts none of them reach the request
body.

The selection is written to a private (`0600`) file in the plugin's state
directory to reach the popup, and deleted the moment the popup reads it. Any
file a popup never collected is swept after 60 seconds, from both processes, so
a selection cannot outlive the attempt to show it.

**What that means in practice.** One keypress sends whatever is selected to a
third party, so treat the selection the way you would treat a paste into a chat
window: do not press the key on a token, a password, a private key, or a
customer record. Lens cannot tell those apart from prose, and it will not try to
guess.

If the text must not leave the machine, run a local model — set `provider =
"ollama"` and nothing goes further than `localhost:11434`. That is the only
configuration in which no network request leaves your machine.

Providers differ in what they do with what you send. Gemini's **free** tier uses
your content to improve Google's products; its paid tier does not. Check the
terms of whichever provider you configure — Lens cannot know them for you, and
"free" and "private" are not the same axis.

Note also that Lens reads the *clipboard*, because Herdr does not populate
`selected_text` on the keybinding path. With `copy_on_select` enabled the
clipboard is your selection, but if you press the key without selecting
anything first, whatever you copied earlier is what gets sent.

## Security

The reply is written to your real terminal, and the prompt tells the model to
reproduce code verbatim — so anything in the selection can come back out. Every
string Lens renders is stripped of control sequences first: OSC 52 (which writes
the system clipboard), OSC 0 (window title), `ESC c` (hard reset), CSI, and DCS.
A crafted selection cannot plant a command in your clipboard or repaint your
screen. The selection is cleaned on the way in as well, so nothing reaches the
provider either.

API keys are never written to the config file. `api_key_env`, `api_key_file`,
and `api_key_command` all name a *source*; a literal `api_key` is rejected at
load with an explicit error. A key file that other users can read is refused
rather than read.

## How it works

Herdr can only bind a key to an *action*, and only an action can open a plugin
pane — so Lens is two processes:

```
Ctrl-B Alt-T
  └─ action  lens-translate      short-lived
       reads the selection, opens the popup, exits
  └─ pane    viewer (popup)      the UI
       paints immediately, then calls the provider on a worker thread
```

The AI request lives in the popup, never in the action. That is what makes the
popup appear before the network round-trip instead of after it.

### Selecting inside Neovim

A full-screen program that turns on mouse reporting owns the drag — Neovim does
by default (`mouse=a`). Herdr never sees a selection, so `copy_on_select` cannot
fire, and the clipboard still holds whatever was copied before.

For Neovim, Lens asks Neovim. Every instance runs an RPC server without being
told to, so the drag you just made is readable directly:

```
nvim --server $XDG_RUNTIME_DIR/nvim.<pid>.0 --remote-expr \
  'join(getregion(getpos("v"), getpos("."), {"type": mode()}), "\n")'
```

Mouse drag, `v`, `V`, `Ctrl-V` — all of them work, and the clipboard is not
involved. Measured at 4 ms, and it is a pure read: afterwards you are still in
visual mode and your `"0` register is untouched. (The usual advice is to send `y`
and read `"0`, which costs you both.)

`getpos("v")` rather than the `'<` mark, because `'<` is only written when visual
mode *ends* — it reads as zeros at exactly the moment the key is pressed.

### Selecting inside less, htop, or classic Vim

The same trick needs a program that answers questions, and these do not. Classic
Vim has `+clientserver`, but the registry needs an X server and there is no
`getregion()` to call.

There Lens does not open the popup at all. It asks Herdr what is running, notices
the clipboard has not changed either, and raises a notification instead:

> **nvim has the mouse** — Herdr never saw the selection, so Lens would
> translate whatever was copied before. In nvim, copy with `"+y` and press the
> key again.

Both conditions are needed, and either one alone is normal. `"+y` changes the
clipboard, so a yank opens the popup as usual:

```vim
"+y      " yank the visual selection to the system clipboard
```

And re-translating the same text in a shell is an ordinary thing to do, so an
unchanged clipboard on its own suppresses nothing.

If the popup does open on stale text — you copied something elsewhere, then
pressed the key inside vim — the status line still says who has the mouse:

```
Translation                                    ⚠ nvim has the mouse
```

That warning replaces the provider name rather than joining it, because in a
narrow popup only one fits and the provider is the half you can infer.

### Where the selection comes from

Herdr 0.8.0 does not populate `selected_text` on the keybinding path, so Lens
reads the **clipboard** — which works because Herdr's `copy_on_select` defaults
to `true`, putting every mouse selection there automatically. Your workflow is
unchanged; the difference is internal.

If you have set `copy_on_select = false`, Lens cannot see your selection. Turn
it back on, or copy manually before pressing the key.

Lens still checks `selected_text` first, so it will use the native path for
free if a future Herdr version fills it in.

## Adding a provider

One file under `lens/providers/`, one line in `REGISTRY`:

```python
class MyProvider(Provider):
    name = "My provider"
    default_endpoint = "https://api.example.com/v1"

    def translate(self, text, source, target, prompt):
        payload = self._post(f"{self.endpoint}/chat", body, headers)
        return payload["result"]
```

Nothing in the UI or the action layer imports a provider-specific symbol.

## Design notes

[`docs/spec.md`](docs/spec.md) is the working spec: what each mode is for, why
the selection arrives through the clipboard, and which decisions were settled by
measurement rather than taste.

## Development

```bash
git clone https://github.com/VoidAxon/herdr-lens
herdr plugin link ./herdr-lens --enabled
python3 -m unittest discover -s tests -t .
```

Provider, config, frame, and privacy tests all run without a network or a
terminal — nor any installed binary:

```bash
env PATH=/usr/bin:/bin python3 -m unittest discover -s tests -t .
```

That is CI's environment, reproduced locally. A test that passes only because
`claude` happens to be installed is not testing anything, and this is the cheap
way to find out. `herdr plugin log list` shows each action invocation with its exit
code and stderr.

## License

MIT — see [LICENSE](LICENSE).

## Contributing

Issues and pull requests welcome. The tests run without a network or a terminal,
so `python3 -m unittest discover -s tests -t .` is the whole check; CI runs it on
Linux and macOS against Python 3.11 and 3.13. A new dependency is a defect — the
plugin is standard library only so that installing it never means installing
anything else.
