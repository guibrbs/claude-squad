---
name: duo-send
description: Talks to the Claude Duo account and hands it tasks (the second Claude Code account of this Windows PC, CLAUDE_CONFIG_DIR=~/.claude-duo) through duo-send.py — synchronously, returning its answer like a subagent on the other account, or as a background session (--bg) that the user follows with `claude-duo agents` and `attach`. Use for "send this to the Duo", "ask the Duo to…", "message the session X in the Duo", "open a session on the other account", "let the Duo work on it in the background", or when this account's usage is running out and the work can go on in the Duo. Not for review rounds between the two accounts (that is loop-duo) and not from a session that already runs on the Duo account.
---

# duo-send

`duo-send.py` runs Claude Code on the Duo's account, in the repository you name.
`windows\scripts\install.ps1` installs it at
`%LOCALAPPDATA%\Programs\ClaudeDuo\tools\duo-send.py` (with `loop-duo.py` next to
it). Always run it with `py -3 -I`. If the file is missing, tell the user to run
`windows\Install Claude Duo.cmd` again.

In the examples, `$TOOLS` is `$env:LOCALAPPDATA\Programs\ClaudeDuo\tools` in
PowerShell and `"$LOCALAPPDATA/Programs/ClaudeDuo/tools"` in Git Bash.

## Before sending

- **Are you already the Duo?** If `CLAUDE_CONFIG_DIR` in your environment is the
  Duo's folder (`~/.claude-duo` by default), stop: the work would go to your own
  account. Say so to the user.
- **Show the user the task text and the repository, and wait for their OK.** The
  task uses the Duo account's usage.
- Write the task as a complete brief: the repository, the files or area, what
  done looks like, and what not to do. The Duo session has none of this
  conversation.

## Synchronous: wait for the answer

```bash
py -3 -I "$TOOLS/duo-send.py" send - --repo "C:/path/to/repo" --json --max-cost 2 <<'TASK'
<the brief>
TASK
```

- Prefer `-` with a quoted heredoc: quotes and backticks need no escaping.
- `--json` returns `{ok, session, cost_usd, answer, error}`. Relay `answer` to the
  user; on `ok: false`, show `error`.
- Nobody can answer the Duo during this turn: when it needs a decision, it says
  what is missing in its answer. Ask the user, then continue the same session with
  `--resume <session>`.
- Long tasks: run the command in the background of your shell and wait for it to
  finish, or use `--bg`.

## Background: a session the user follows

```bash
py -3 -I "$TOOLS/duo-send.py" send - --repo "C:/path/to/repo" --bg --name "short name" --json <<'TASK'
<the brief>
TASK
```

- Returns the session `id` and the follow-up commands. Confirm it started with
  `duo-send.py agents` (JSON; look for the id with status `busy`).
- Give the user the commands: `claude-duo attach <id>` (in their terminal, to
  answer questions), `claude-duo logs <id>`, `claude-duo stop <id>`. The original
  account's `claude agents` does not list it.
- `--max-cost` does not work with `--bg`.

## Talk to a session already open in the Duo app

```bash
py -3 -I "$TOOLS/duo-send.py" sessions
py -3 -I "$TOOLS/duo-send.py" sessions "part of its name" --address
```

- Then call `SendMessage` with `to` set to the printed address
  (`uds:\\.\pipe\LOCAL\cc-msg-…`). Show the user the text and the target session
  first.
- The reply arrives here as a message from another session; nothing to poll.
- Look the address up right before each send: it changes when the Duo session
  restarts. Never send to an address you did not just read from `sessions`.

## When the script says "não está logado" (not signed in)

The Duo's command line has its own sign-in. Give the user the command the error
prints (`claude-duo auth login`), to run in a terminal outside any Claude session
and sign in with the Duo's account; do not run it yourself.

## Do not

- Do not pass `--permission bypassPermissions` (it is refused anyway).
- Do not send credentials, tokens or secrets in the task.
- Do not ask the Duo to merge, push to the default branch or change settings.

<!-- claude-duo: instalado por windows\scripts\install.ps1 -WithSkill; uninstall.ps1 removes it -->
