# Claude Squad

[![Security regression](https://github.com/guibrbs/claude-squad/actions/workflows/security-regression.yml/badge.svg)](https://github.com/guibrbs/claude-squad/actions/workflows/security-regression.yml)

**Several Claude Desktop accounts on one Mac, side by side.** Keep the official
**Claude** app signed in to one account and run managed copies signed in to the
others: **Claude Duo** for the second account, **Claude Trio** (or any name) for
a third, and so on. Personal and work, several organizations, or several plans.
Each app has its own account, its own Claude Code profile and its own Keychain
item, and all of them can be open at the same time.

> ⚠️ **Experimental and unofficial.** This is an independent project, not
> affiliated with or endorsed by Anthropic. Each copy is a copy of the official
> app that is **signed locally**, without Anthropic's signature. Some scenarios
> are still being validated (see [Limitations](#limitations)). Use it at your own
> risk and follow the terms of use of each account and your organization's rules.

**Names:** the project was called Claude Duo while it managed a single copy.
The first copy keeps that name, and the installed paths keep their `claude-duo`
names (`~/.local/share/claude-duo`, `claude-duo.conf`, the launch agent), so an
existing installation goes on working without a migration.

## Why

Claude Desktop holds one account at a time. Switching means signing out and in,
losing your place in Chat, Cowork and Code. Simply duplicating the app is not
enough either: the copy reuses the original's encrypted storage and asks for
your login Keychain password, and claude.ai treats a renamed copy as a web
browser. Claude Squad handles those details and keeps the copies up to date.

## Quick start

Requirements: macOS 12 or later, the official Claude in `/Applications`, and the
Xcode Command Line Tools (`xcode-select --install`).

```bash
git clone https://github.com/guibrbs/claude-squad.git ~/claude-squad
```

```bash
~/claude-squad/duo/install-duo.sh
```

The installer:

1. checks the requirements;
2. installs the versioned toolset (content-verified);
3. writes the config to `~/.config/claude-instances/claude-duo.conf`;
4. adds the **Update Claude Squad** app to `~/Applications`;
5. adds an hourly check that offers to update the Duo after Claude updates
   (skip it with `--no-auto-check`);
6. builds the Duo. It **asks before quitting Claude**, because building needs
   both apps closed, and reopens Claude at the end. When the installer runs
   inside Claude (in the Code tab, for example), it does not build right away,
   because that would end its own session: it opens **Update Claude Squad**,
   which builds outside Claude.

Then open **~/Applications/Claude Duo** and sign in with your second account.

## Everyday use

- **Open:** `Claude Duo` from the Dock, Launchpad or Spotlight. The original is
  still your usual `Claude`.
- **Update:** when the official Claude updates, a prompt asks *"Update now?"*.
  You can also open **Update Claude Squad** at any time. Either way, Claude and
  every copy quit, the copies are rebuilt and verified, and whatever was open
  reopens.
  Prompts and notifications follow your macOS language (English or Portuguese).
- **Previous versions:** every update keeps the previous Duo in
  `~/Applications/.claude-txn.*` and nothing prunes them, so they take up disk
  space (up to the size of the app each). To inspect one, use the identifier
  the log shows:
  `CLAUDE_INSTANCES_CONF=~/.config/claude-instances/claude-duo.conf ~/.local/share/claude-instances/toolsets/dispatcher run claude-apps-refresh.sh --recover .claude-txn.XXXXXXXX`.
- **Connectors (Google Drive, Slack…):** connect them in a private browser
  window signed in to the Duo's account (claude.ai → Settings → Connectors),
  then press ⌘R in the Duo. Connector sign-in started inside the Duo does not
  come back to it, because `claude://` links belong to the official app.
- **Same instructions, memories and skills in Code (optional):** with the Duo
  closed, run the command below. It links the Duo's `CLAUDE.md`, skills, output
  styles, tools, plugins and per-project memories to the original's, and copies
  `settings.json` once. Sign-in, sessions and history stay separate. Install
  plugins from the original. Add `--mcp-servers` to also copy your user-scope
  MCP servers, credentials included; a server the Duo already has is kept.
  Details in [docs/PROFILE_REUSE.md](docs/PROFILE_REUSE.md).

```bash
/usr/bin/python3 -I ~/claude-squad/scripts/share-code-profile.py --apply --backup-existing
```

For another copy, add `--account Trio`.

## A third account (or more)

Each extra account is one more copy, `Claude <name>`, with its own bundle id,
Desktop profile, Code folder, Keychain item and icon color. Add it with the
installer, which appends one line to the config and builds it:

```bash
~/claude-squad/duo/install-duo.sh --add Trio
```

The new line uses `~/Library/Application Support/Claude-Trio` and
`~/.claude-trio`. Then open **~/Applications/Claude Trio** and sign in with
the other account. **Update Claude Squad** and the hourly check cover every
copy: an update quits Claude and all copies, rebuilds them and reopens what was
open. `claude-dev://` links (`--links`) go to one copy only, the Duo by
default; the config names it (`@links claude-dev Duo`). To remove just one
copy, run `uninstall-duo.sh --only Trio`.

## Claude Code in the terminal for the Duo's account (loop-duo, duo-send)

The Duo app and the `claude` command keep separate sign-ins: a Duo that works
in its window still has a command line that never signed in. loop-duo and
duo-send run `claude` on the Duo's account, so sign it in once, in a Terminal
outside any Claude session, with the Duo's account:

```bash
CLAUDE_CONFIG_DIR=~/.claude-duo claude auth login
```

`CLAUDE_CONFIG_DIR=~/.claude-duo claude auth status` shows which account it
is. The original's sign-in (`~/.claude`) is not touched. Both scripts check
this before they start and print this command when it is missing.

## loop-duo: one account develops, the other reviews (optional)

With both Code accounts on this Mac, one can develop and the other review in
the same repository, round after round, until the reviewer approves:

```bash
/usr/bin/python3 -I ~/claude-squad/duo/loop-duo.py run "goal" --repo ~/path/to/repo
```

- **Roles:** by default the original account develops and the Duo's reviews.
  To swap them, use `--dev duo --reviewer original`. Any copy works the
  same way: `--dev trio --reviewer duo`.
- **Branch:** the work goes to a `loop-duo/<goal>-<id>` branch, created from
  HEAD in a worktree of its own. The reviewer runs without the edit tools,
  and a reviewer turn that changes the branch anyway does not count. Untracked
  files are never committed: they go back to the developer.
- **Questions:** one agent's question goes to the other, because `claude -p`
  has no way to ask the user.
- **Ceilings:** `--rounds` (6) and `--max-cost` (estimated US$). Every round
  uses both accounts' usage.
- **PR:** `--until pr` pushes and opens a draft PR after approval. Merging
  stays with you.
- **Follow along:** `loop-duo.py list` shows the loops. The conversation
  between the agents is in `~/.local/share/claude-duo/loops/<id>/conversation.md`.

Details and limits: [docs/LOOP_DUO.md](docs/LOOP_DUO.md).

## duo-send: hand a task to the Duo's account (optional)

The original Claude Code, or you in a terminal, can give a task to the Duo's
account, in a repository you name:

```bash
/usr/bin/python3 -I ~/claude-squad/duo/duo-send.py send "task" --repo ~/path/to/repo
/usr/bin/python3 -I ~/claude-squad/duo/duo-send.py send "task" --repo ~/path/to/repo --bg
```

- **Synchronous (default):** `claude -p` on the Duo's account; prints the
  answer when the work is done. `--max-cost` caps the spending, `--json`
  returns the answer, session and cost, `--resume` continues a session.
- **`--bg`:** a background session on the Duo's account; returns its id at
  once. `duo-send.py agents`, `logs <id>`, `attach <id>` and `stop <id>` reach
  the Duo's sessions, which the original's `claude agents` does not list.
- **`--bg` in a new folder:** a background session starts only in a folder
  the Duo's account trusts. See
  [docs/DUO_SEND.md](docs/DUO_SEND.md#before-the-first-send).
- **Another copy:** every command takes `--to trio` (or the name of any copy
  in the config) to reach that account instead of the Duo's.
- **Talk to a session open in the Duo window:** `duo-send.py sessions` lists
  the Duo's open sessions with their `uds:/tmp/cc-socks/<pid>.sock` address;
  the original Claude's `SendMessage` to that address reaches the session, and
  the reply comes back.
- **Same isolation as loop-duo:** the Duo's account, environment and
  `claude -p` turn come from `loop-duo.py`. `bypassPermissions` is refused.
- **From the original Claude:** `install-duo.sh` installs the script in
  `~/.local/share/claude-duo` and the `duo-send` skill in
  `~/.claude/skills`, so you can just ask the original Claude to send work to
  the Duo. It never replaces a `duo-send` skill of your own; `--no-skill`
  skips it, and `uninstall-duo.sh` removes it.

- **On the Duo's screen:** after `duo/install-duo.sh --links` (it adds
  `@links claude-dev` to the config and rebuilds), `duo-send.py open --repo … --prompt "…"`
  opens a new Code session in the Duo window, and `send … --open` imports the
  finished session there. Details in
  [docs/DUO_VISIBLE_SESSION.md](docs/DUO_VISIBLE_SESSION.md).

Details: [docs/DUO_SEND.md](docs/DUO_SEND.md).

## How it works

- The Duo is a copy of the official `Claude.app` in `~/Applications` with its
  own bundle identifier, its own Desktop profile
  (`~/Library/Application Support/Claude-Duo`) and its own Code profile
  (`CLAUDE_CONFIG_DIR=~/.claude-duo`).
- **Its own Keychain item:** Electron names its encryption key
  `<productName> Safe Storage`. The builder changes only `productName` inside
  `app.asar` to `Duo Claude`, without changing the file size, and reseals the
  archive hashes. The Duo therefore never asks for the original's
  `Claude Safe Storage`. The name is `Duo Claude` rather than `Claude Duo`
  because the user agent drops spaces (`DuoClaude/…`) and claude.ai only enables
  the desktop UI for a `claude/` token.
- **Checks:** the source must carry Anthropic's signature and pass Gatekeeper.
  The integrity record **regenerates the expected result from the source** and
  requires byte-for-byte equality; any other difference is refused. Publishing
  is atomic and keeps the previous version for inspection.
- Details: [HARDENING.md](HARDENING.md), [docs/APP_INTEGRITY.md](docs/APP_INTEGRITY.md),
  [docs/KEYCHAIN_NAMESPACE_EXPERIMENTS.md](docs/KEYCHAIN_NAMESPACE_EXPERIMENTS.md)
  and [docs/TOOLSET_INSTALLATION.md](docs/TOOLSET_INSTALLATION.md).

## Limitations

- Local signature: the Duo loses Anthropic's identity, and macOS asks again for
  permissions such as folders, microphone or automation.
- It must be rebuilt after every official Claude update; don't use the Duo's
  own built-in updater.
- Still being validated: the effective Code account in each app, signing out of
  one account without affecting the other, and both apps open for long periods
  ([validation plan](docs/GUI_VALIDATION.md), [roadmap](docs/ROADMAP.md)).
- It does not protect against malicious software running as your user; the
  local records detect changes but are not a security boundary.

## Uninstall

Quit Claude Duo (and any other copy) and run:

```bash
~/claude-squad/duo/uninstall-duo.sh
```

The script shows the list and asks for confirmation. It then turns off the
update check and moves everything into one folder in the Trash, deleting
nothing:

- `~/Applications/Claude Duo.app`, every other copy the config lists
  (`Claude Trio.app`…) and `~/Applications/Update Claude Squad.app`
  (and the earlier `Update Claude Duo.app` and `Atualizar Claude Duo.app`, if
  they are still there);
- the update check, in `~/Library/LaunchAgents/io.github.guibrbs.claude-duo.update-check.plist`;
- the Claude Squad tools, in `~/.local/share/claude-duo`, and the config, in
  `~/.config/claude-instances/claude-duo.conf`;
- the logs, in `~/Library/Logs/Claude Duo Updater`;
- the toolset, in `~/.local/share/claude-instances`, and the previous
  versions, in `~/Applications/.claude-txn.*`. These two stay while another
  config in `~/.config/claude-instances` still uses them.

The accounts' data stays unless you run it with `--include-account-data`.
That moves the Desktop and Code profiles each copy's line names, for the Duo by default
`~/Library/Application Support/Claude-Duo` and `~/.claude-duo`. It never moves
a folder outside the profile locations the toolset accepts for the Duo, such as
the original's. If you
used Code sharing, the shared items inside `~/.claude-duo` are links: moving
them does not affect the originals in `~/.claude`. The `Duo Claude Safe
Storage` item (and `Trio Claude Safe Storage`, for each copy) stays in the
Keychain; delete it in Keychain Access if you want.

`--only Trio` removes a single copy instead: its app (and, with
`--include-account-data`, its profiles) go to the Trash, and its line leaves
the config, together with `@links` when it names that copy. The Duo, the
updater and the tools stay.

## Development

```bash
python3 scripts/run-security-tests.py all
```

Tests run only in temporary folders with synthetic apps and accounts. CI runs
the same suite on arm64 and x86_64, plus the Clang static analyzer.
Contributions and issue reports are welcome.

## Credits and license

Based on [multi-claude-desktop](https://github.com/cgclark/multi-claude-desktop)
by Christopher Clark; its original technical documentation is in
[docs/MULTI_INSTANCE_REFERENCE.md](docs/MULTI_INSTANCE_REFERENCE.md).
MIT License ([LICENSE](LICENSE)). "Claude" is a trademark of Anthropic; this
project is not affiliated with or endorsed by Anthropic.
