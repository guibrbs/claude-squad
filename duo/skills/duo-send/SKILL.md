---
name: duo-send
description: Talks to the Claude Duo account and hands it tasks (the second Claude Code account of this Mac, CLAUDE_CONFIG_DIR=~/.claude-duo) through duo/duo-send.py — synchronously, returning its answer like a subagent on the other account, or as a background session (--bg) that the user follows with `duo-send.py agents` and `attach`. Use for "send this to the Duo", "ask the Duo to…", "message the session X in the Duo", "tell the Duo session to…", "open a session on the other account", "let the Duo work on it in the background", or when this account's usage is running out and the work can go on in the Duo. Also reaches the other managed copies of the config (Claude Trio…) with `--to`: "send this to the Trio". Not for review rounds between the two accounts (that is loop-duo) and not from a session that already runs on the Duo account.
---

# duo-send

`duo-send.py` runs Claude Code on the Duo's account, in the repository you
name. `duo/install-duo.sh` installs it at `~/.local/share/claude-duo/duo-send.py`
(with `loop-duo.py` next to it) and this skill at `~/.claude/skills/duo-send`.
Always run it with `/usr/bin/python3 -I`. If the file is missing, tell the user
to run `duo/install-duo.sh` again from an up-to-date checkout.

## Which account

Every command goes to the Duo unless it has `--to NAME`: `--to trio` reaches
Claude Trio, and any other copy the installed config lists works the same way.
Use the copy the user named; when they only say "the other account" and there
is more than one copy, ask which. An unknown name is refused with the list of
accounts. The examples below use the Duo; add `--to <name>` for another copy.

## Before sending

- **Are you already that copy?** If `CLAUDE_CONFIG_DIR` in your environment is
  the target's folder (`~/.claude-duo` for the Duo, `~/.claude-trio` for the
  Trio), stop: the work would go to your own account. Say so to the user.
- **Show the user the task text and the repository, and wait for their OK.**
  The task uses the Duo account's usage.
- Write the task as a complete brief: the repository, the files or area, what
  done looks like, and what not to do. The Duo session has none of this
  conversation.

## Synchronous: wait for the answer

```bash
/usr/bin/python3 -I ~/.local/share/claude-duo/duo-send.py send - --repo /path/to/repo --json --max-cost 2 <<'TASK'
<the brief>
TASK
```

- Prefer `-` with a quoted heredoc: quotes and backticks need no escaping.
- `--json` returns `{ok, session, cost_usd, answer, error, hint}`. Relay
  `answer` to the user; on `ok: false`, show `error`, and `hint` when there is
  one (see "When the script says…" below).
- Nobody can answer the Duo during this turn: when it needs a decision, it
  says what is missing in its answer. Ask the user, then continue the same
  session with `--resume <session>`.
- Long tasks: run the command in the background of your shell and wait for it
  to finish, or use `--bg`.

## Background: a session the user follows

```bash
/usr/bin/python3 -I ~/.local/share/claude-duo/duo-send.py send - --repo /path/to/repo --bg --name "short name" --json <<'TASK'
<the brief>
TASK
```

- Returns the session `id` and the follow-up commands. Confirm it started with
  `duo-send.py agents` (JSON; look for the id with status `busy`).
- Give the user the commands: `duo-send.py attach <id>` (in their terminal, to
  answer questions), `duo-send.py logs <id>`, `duo-send.py stop <id>`. The
  original account's `claude agents` does not list it.
- `--max-cost` does not work with `--bg`.
- On `ok: false`, show `error` and pass on `hint` as in the synchronous mode.

## On the Duo's screen

When the user wants to see or drive the session in the Claude Duo window:

```bash
/usr/bin/python3 -I ~/.local/share/claude-duo/duo-send.py open --repo /path/to/repo --prompt "short prompt"
/usr/bin/python3 -I ~/.local/share/claude-duo/duo-send.py send - --repo /path/to/repo --open --json <<'TASK'
<the brief>
TASK
```

- `open` starts a new Code session in the Duo window with the prompt filled
  in; the user presses Enter there. `send --open` runs the task, then imports
  the session into the Duo window.
- It needs the copy built with links. Only one copy can take
  `claude-dev://` links (the config's `@links claude-dev <name>` line names
  it): `open` to another copy is refused, and the error says which copy has
  them. If the script refuses for that reason, tell the user to run
  `duo/install-duo.sh --links` from an up-to-date checkout, which rebuilds
  the copies and quits and reopens the apps; do not run it or edit the
  config yourself.

## Talk to a session already open in the Duo app

To message a session that is open in the Claude Duo window (instead of
starting new work there):

```bash
/usr/bin/python3 -I ~/.local/share/claude-duo/duo-send.py sessions
/usr/bin/python3 -I ~/.local/share/claude-duo/duo-send.py sessions "part of its name" --address
```

- Then call `SendMessage` with `to` set to the printed `uds:/tmp/cc-socks/<pid>.sock`
  address. Show the user the text and the target session first.
- The reply arrives here as a message from another session; nothing to poll.
- Look the address up right before each send: it changes when the Duo session
  restarts. On "ENOENT … peer process may have restarted", look it up again
  once.
- Never send to an address you did not just read from `sessions`.

## When the script says "not signed in" or gives a `hint`

The Duo's command line has its own sign-in, and `--bg` needs a folder the
Duo's account trusts. Give the user the command the error or the `hint`
prints, to run in a Terminal outside any Claude session (and, for the login,
sign in with the Duo's account); they are interactive, so do not run them
yourself.

## Do not

- Do not pass `--permission bypassPermissions` (it is refused anyway).
- Do not send credentials, tokens or secrets in the task.
- Do not ask the Duo to merge, push to the default branch or change settings.

<!-- claude-duo: installed by duo/install-duo.sh, which updates it; uninstall-duo.sh removes it -->
