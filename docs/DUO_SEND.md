# duo-send: the original account hands work to the Duo's

With Claude Duo, the same Mac has two Claude Code accounts: the original
(`~/.claude`) and the Duo's (the `CLAUDE_CONFIG_DIR` of the Duo line in the
config, `~/.claude-duo` by default). `duo/duo-send.py` lets a Claude Code
session on the original account, or you in a terminal, give a task to the
Duo's account, in a repository you name.

```bash
/usr/bin/python3 -I ~/claude-squad/duo/duo-send.py send "task" --repo ~/path/to/repo
/usr/bin/python3 -I ~/claude-squad/duo/duo-send.py send "task" --repo ~/path/to/repo --bg
/usr/bin/python3 -I ~/claude-squad/duo/duo-send.py agents
```

Like loop-duo, the script uses only the Python 3.9 standard library and does
not touch the apps.

## Before the first send

The Duo app working is not enough: the script runs the Duo's `claude` command,
which needs two things of its own. Seen with Claude Code 2.1.303.

1. **A CLI login on the Duo's account.** The Duo app's sign-in does not cover
   the command line, and the script stops before any work while it is missing;
   the one-time `claude auth login` is in
   [Accounts and environment](#accounts-and-environment).
2. **A trusted folder for `--bg`.** A background session starts only in a
   folder the Duo's account trusts; otherwise `--bg` fails with ``Workspace not
   trusted. Run `claude` in <folder> once and accept the trust prompt``. The
   synchronous mode (`claude -p`) runs in the same folder without it. A folder
   already opened in the Duo's Code tab is trusted; for any other, once, in a
   Terminal outside any Claude session:

   ```bash
   cd ~/path/to/repo && CLAUDE_CONFIG_DIR=~/.claude-duo claude
   ```

   accept the trust prompt and `/exit`.

If the Duo line of the config sets another `CLAUDE_CONFIG_DIR`, or `--to`
names another copy, use that copy's folder instead of `~/.claude-duo`. When `--bg` fails for an untrusted folder,
or a turn still answers `Please run /login` after the sign-in check let it
through, `duo-send.py` prints the command that fixes it, with the right
folders; `--json` puts it in `hint`.

## Two modes

| | synchronous (default) | `--bg` |
|---|---|---|
| runs | `claude -p` on the Duo's account | `claude --bg` on the Duo's account |
| returns | the final answer, after the work | at once, with the session id |
| who can answer its questions | nobody: it reports what is missing | you, with `attach` |
| cost ceiling | `--max-cost` (`--max-budget-usd`) | none: claude enforces it only with `-p` |
| follow along | the tools it uses, on stderr | `agents`, `logs`, `attach`, `stop` |

- **Synchronous** works like a subagent on the other account: the original
  session calls it and reads the answer as the tool result. `--json` returns
  `{ok, session, cost_usd, answer, error, hint}`. To continue the same Duo session,
  pass `--resume <session>`.
- **`--bg`** opens a background session that the Duo's `claude agents` lists.
  It does not appear in the original's list: background sessions belong to
  the account that started them. That is why `duo-send.py` has `agents`,
  `logs <id>`, `stop <id>` and `attach <id>`, which run the same `claude`
  subcommands on the Duo's account. `--name` sets the session's display name.

The task can come from standard input with `-`, so quotes and backticks need
no escaping: `duo-send.py send - --repo … < task.md`.

## Accounts and environment

The account, its environment and the `claude -p` turn come from
`duo/loop-duo.py` (`account`, `environment`, `converse`), so both scripts
isolate the Duo the same way:

- the `CLAUDE*` and `ANTHROPIC_*` variables of the calling session are
  removed, because a Desktop session exports its own account in them; the
  Duo's `claude` gets only its own `CLAUDE_CONFIG_DIR`;
- the Duo's folder comes from the installed config, with `~/.claude-duo` as
  the fallback; a Duo that never opened Code is refused;
- every command takes `--to NAME` for another managed copy of the config
  (`--to trio`): its folder is the `CLAUDE_CONFIG_DIR` of its line, the role
  prompt names it, and `--to original` or an unknown name is refused. `open`
  reaches only the copy the config's `@links claude-dev <name>` line names,
  because one app owns the scheme;
- the Duo's command line must be signed in on its own: the Duo app's
  sign-in does not cover it. Before any work, `claude auth status` runs on the
  Duo's account; when it says signed out, the script stops and prints the
  command to run in a Terminal, outside any Claude session:
  `CLAUDE_CONFIG_DIR=~/.claude-duo claude auth login`, with the Duo's account;
- the work happens in `--repo` (the current folder by default), with no branch
  or worktree of its own. For work that must not touch your checkout, point
  `--repo` at a worktree, or use loop-duo.

## Permissions

- **Default mode `auto`.** `--permission` accepts `auto`, `acceptEdits`,
  `manual`, `dontAsk` and `plan`. `bypassPermissions` is refused: nobody
  watches a task sent this way.
- The Duo's session gets a short appended system prompt: it is on the Duo
  account, sent by the original; it must not merge, push to the default branch
  or change settings and permissions, and it ends with a report. In the
  synchronous mode it is also told that nobody can answer it during the turn.
- Usage counts against the Duo's account.

## From the original Claude: the skill

`duo/install-duo.sh` installs `duo-send.py` (with `loop-duo.py`, which it
imports) in `~/.local/share/claude-duo`, and the skill
`duo/skills/duo-send/SKILL.md` in `~/.claude/skills/duo-send`. The original
Claude then knows when and how to call the script: ask it to "send this to the
Duo", "open it in the Duo" and the like, or call `/duo-send <task>`.

- The installed skill is a copy with a marker line at the end. Running the
  installer again refreshes it; a `duo-send` skill without the marker, or a
  symlink, is yours and is never replaced.
- `--no-skill` skips it. Without a `~/.claude` folder (the original never
  opened Code), it is skipped with a message; run the installer again later.
- `uninstall-duo.sh` moves the installed copy to the Trash with the rest.
- With `share-code-profile.py`, the Duo sees the same skills folder; the skill
  tells the Duo not to send work to itself.

## Opening the session in the Duo app

`duo-send` runs on the Duo's account, but the session lives in its Code
profile, not in the Duo window. After `duo/install-duo.sh --links`:

- `duo-send.py open --repo PATH --prompt "…"` opens a new Code session in the
  Duo window, with the folder chosen and the prompt filled in;
- `send … --open` imports the finished session into the Duo window;
- `open --session <uuid>` imports any Duo session by id.

How to turn it on and how it works: [DUO_VISIBLE_SESSION.md](DUO_VISIBLE_SESSION.md).

## Talking to a session already open in the Duo app

Sessions of the same account talk with `ListAgents` and `SendMessage`. Each
account lists only its own sessions (`<config>/sessions/<pid>.json`), so the
original's sessions never see the Duo's by name. But every session also
answers on a local socket, `/tmp/cc-socks/<pid>.sock`, which both accounts
share, and `SendMessage` accepts that socket as an address:

```bash
/usr/bin/python3 -I ~/.local/share/claude-duo/duo-send.py sessions
/usr/bin/python3 -I ~/.local/share/claude-duo/duo-send.py sessions "part of the name" --address
```

`sessions` reads the Duo's registry and lists its open sessions with status
and address (`uds:/tmp/cc-socks/<pid>.sock`); `--address` prints only the
address of the one session that matches. In the original Claude, `SendMessage`
with `to` set to that address delivers the message to the session in the Duo
window, idle or busy; the Duo session sees the sender's address and its reply
comes back by itself.

- **The address changes** when the session's process restarts (the Desktop
  restarts it at times), so look it up right before sending, never keep it. A
  stale address fails with "ENOENT … the peer process may have restarted":
  look it up again.
- **Tested on 2026-10-21** (Claude Code 2.1.304 in both apps): a session of
  the original app sent to a Duo Desktop session by socket address, the
  message arrived in the Duo window, and the Duo session's `SendMessage` reply
  to the sender's address was delivered. Idle Duo sessions keep their process
  and socket.
- **No cost on either account** beyond the turns the two sessions take; no
  extra session in between.
- It relies on the socket address, an undocumented detail of Claude Code; if a
  future version changes it, `sessions` lists no address and `send` remains.
