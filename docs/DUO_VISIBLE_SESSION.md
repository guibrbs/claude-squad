# Opening a visible session in the Duo app

`duo-send.py send` (see [DUO_SEND.md](DUO_SEND.md)) runs Claude Code on the
Duo's account, but the session lives in the Duo's Code profile, not in the
Duo window. `duo-send.py open` puts it on the Duo's screen, through a
`claude-dev://` link that only the Duo registers.

```bash
# a new Code session in the Duo window, folder chosen, prompt filled in (not sent):
/usr/bin/python3 -I ~/claude-squad/duo/duo-send.py open --repo ~/path/to/repo --prompt "task"
# run the task on the Duo's account, then import the session into the Duo window:
/usr/bin/python3 -I ~/claude-squad/duo/duo-send.py send "task" --repo ~/path/to/repo --open
# import a session by id (a background one, once it is idle):
/usr/bin/python3 -I ~/claude-squad/duo/duo-send.py open --session <uuid>
```

## Turning it on

Links are off by default. From an up-to-date checkout, run the installer
again with `--links`:

```bash
~/claude-squad/duo/install-duo.sh --links
```

It adds `@links claude-dev` right after `@apps user` in the Duo config
(`~/.config/claude-instances/claude-duo.conf`), installs the toolset of this
checkout (the builder and the integrity helper both changed) and rebuilds the
Duo, so Claude and Claude Duo quit and reopen. From inside Claude it hands the
build to "Update Claude Squad", as on a first install. The config ends up as:

```
@mode original-copy
@apps user
@links claude-dev
Duo | io.github.<you>.claude-duo | ~/Library/Application Support/Claude-Duo | 212 | CLAUDE_CONFIG_DIR=~/.claude-duo
```

Editing the config by hand and running only `update-duo.sh` is not enough: the
toolset installed earlier does not know `@links`. To turn links off, change
the line to `@links none` and run `update-duo.sh`.

Without the line, `open` and `send --open` refuse before running anything and
say what to run.

## What the Desktop accepts

Evidence from Claude Desktop 2.42304.0 (`app.asar`, main process bundle),
2026-10-20.

- `app.on("open-url")` hands every URL to one handler. It accepts only a fixed
  list of protocols, hard-coded in the bundle: `claude:`, `claude-dev:`,
  `claude-nest:`, `claude-nest-dev:`, `claude-nest-prod:`. Anything else is
  dropped with `claudeURLHandler: invalid protocol`. The handler switches on
  the host, the same way for every protocol in the list.
- `<scheme>://code/new?folder=<path>&q=<prompt>` opens a new Code session with
  the folder chosen and the prompt filled in (also `prompt`, `file`,
  `environment`, `runner_pool`). The Finder service "New Claude Code Session
  Here" uses this link. Nothing in the handler sends the prompt.
- `<scheme>://resume?session=<uuid>` imports a CLI session by id
  (`importCliSession`) and opens it. The id must be a UUID, which is what
  `claude -p` reports as `session_id`. The Duo's Code profile is
  `CLAUDE_CONFIG_DIR=~/.claude-duo`, the same one `duo-send` writes to.
- `<scheme>://code/continue?session=last|local_<id>` and
  `<scheme>://code/needs-input` exist too, but are ignored when the "entry
  points" setting is off. `duo-send` does not use them.
- The enterprise policy `authentication.disableDeepLinks` drops every link
  except sign-in ones; under it, `open` does nothing.
- Handoff (`NSUserActivity` `com.anthropic.claude.code.session`) is only
  swallowed, not handled.

## Why `claude-dev://`

| Path | What changes | Cost | Verdict |
|---|---|---|---|
| `open -a` the Duo with a `claude://` link | nothing in the app | small | unconfirmed: LaunchServices may not deliver a scheme the app does not declare |
| **`claude-dev://` in the Duo's `Info.plist`** | builder, integrity record, config | medium, done here | **chosen** |
| `claude-duo://` | the above **plus** a JavaScript patch in `app.asar` at every update | high; breaks the "only `productName` changes" guarantee | no |

- `claude://` belongs to the official app. The builder deletes
  `CFBundleURLTypes` and `NSUserActivityTypes` from the copy, so copies never
  fight over `claude://` and `msauth://`. That stays true.
- `claude-duo:` is not in the Desktop's protocol list, so it would also need a
  JavaScript patch. `claude-dev:` already is, and the official app does not
  register it (it is meant for Anthropic's development builds).
- `duo-send` hands the link to the Duo by bundle id (`open -b <id> <link>`),
  so even another app registering `claude-dev` would not receive it.

## What changed in the build

- **Config:** `@links claude-dev [name]` or `@links none` (the default), once,
  before the instance rows, and only in `original-copy` mode. With several
  copies the name is required and picks the one copy that takes the links;
  the others declare no scheme. Any other value, a name that is not
  configured, a late line or a repeated one is refused.
- **Builder** (`bin/claude-build-worker.sh`): after deleting the stock URL
  types, it adds exactly one: `CFBundleURLName` `Claude <name> links`,
  `CFBundleURLSchemes` `[claude-dev]`.
- **Integrity record** (`bin/claude-integrity.c`): `record` takes a `LINKS`
  argument, and the expected plist, regenerated from the signed source, gets
  the same single entry. Any other scheme, an extra one or a missing one fails
  with "Info.plist differs beyond planned changes" before publication.
  `verify` still compares the whole tree byte for byte.
- **Binding:** the links setting is part of the config binding, so turning it
  on or off makes `--verify` fail until a rebuild. A config without the line
  keeps its old binding: existing installs verify as before.
- **LaunchServices:** the builder already runs `lsregister -f` on the
  published copy, which registers the scheme.

## Limits

- The prompt in a `code/new` link is visible in process listings while `open`
  runs; `open --prompt` refuses more than 4000 characters. Long briefs go
  through `send`, with `--open` importing the session by id.
- A `--bg` session still running would be imported as a copy, so `send --bg
  --open` is refused; open it with `open --session <uuid>` once it is idle.
- Not checked live in this change: rebuilding quits the Duo. After
  `install-duo.sh --links`, run `duo-send.py open --repo ~` once; a new Code session in
  your home folder confirms it. If nothing opens, look for `claudeURLHandler`
  in the Duo's main log.
- A future Desktop could drop `claude-dev:` from its list. The link would then
  be refused with "invalid protocol" and nothing else would break.
