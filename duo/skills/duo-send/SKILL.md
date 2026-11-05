---
name: duo-send
description: Talks to the Claude Duo account and hands it tasks (the second Claude Code account of this Mac, CLAUDE_CONFIG_DIR=~/.claude-duo) through duo/duo-send.py — synchronously, returning its answer like a subagent on the other account, or as a background session (--bg) that the user follows with `duo-send.py agents` and `attach`. Use for "send this to the Duo", "ask the Duo to…", "message the session X in the Duo", "tell the Duo session to…", "open a session on the other account", "abre essa sessão no Claude Duo", "open it in the Duo instead of here", "give it to the Duo inbox", "do it in the Duo where I can see", "let the Duo work on it in the background", or when this account's usage is running out and the work can go on in the Duo. Also reaches the other managed copies of the config (Claude Trio…) with `--to`: "send this to the Trio". Not for review rounds between the two accounts (that is loop-duo) and not from a session that already runs on the Duo account.
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
- `--json` returns `{ok, session, cost_usd, answer, error}`. Relay `answer` to
  the user; on `ok: false`, show `error`.
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

## Open a new session in the Duo instead of here

When the user asks for a session in the Claude Duo ("abre essa sessão no
Claude Duo", "open it in the Duo instead of here"), do what you would do to
start a session of your own, but open it in the Duo:

1. **Write the brief** with the same care you would give one of your own
   sessions: the task, the repository and the files or area, what done looks
   like, what not to do, and the decisions made so far. The Duo session has
   none of this conversation.
2. **Choose the folder** the session works in: the repository the task is
   about, as an absolute path.
3. **Short brief (up to about 3,500 characters):** open it with the brief as
   the prompt:

   ```bash
   /usr/bin/python3 -I ~/.local/share/claude-duo/duo-send.py open --repo /path/to/repo --prompt - <<'BRIEF'
   <the brief>
   BRIEF
   ```

4. **Longer brief:** the prompt travels inside a link, and `open` refuses more
   than 4,000 characters. Write the brief to `<repo>/.claude/duo-briefs/<short-name>.md`,
   then open with a short prompt that points to it: `Read .claude/duo-briefs/<short-name>.md
   and do what it asks. Delete the file when you are done.` Tell the user the
   file is there, untracked.
5. **Tell the user** that the session is open in the Claude Duo window with the
   brief filled in, and that pressing Enter there starts it. The app never
   starts it alone: a new session needs the person in that window.

Show the user the brief before opening only when it holds something they have
not seen or agreed to. Otherwise opening it is enough: nothing runs until they
press Enter.

To have the Duo run the task out of sight first and show the session only
when it is done, use `send --open` instead:

```bash
/usr/bin/python3 -I ~/.local/share/claude-duo/duo-send.py send - --repo /path/to/repo --open --json <<'TASK'
<the brief>
TASK
```

Both need the copy built with links. Only one copy can take `claude-dev://`
links (the config's `@links claude-dev <name>` line names it): `open` to
another copy is refused, and the error says which copy has them. If the script
refuses for that reason, tell the user to run `duo/install-duo.sh --links` from
an up-to-date checkout, outside any Claude session. It rebuilds the copies and
quits and reopens the apps. Do not run it or edit the config yourself.

## Hand a task to the Duo inbox (visible in the Duo window)

The Duo inbox is a session in the Claude Duo window that runs /duo-inbox: it
takes tasks by message, does them where the user can watch and step in, and
reports back to you. Prefer it to `send` when the user wants to see or follow
the work in the Duo. One inbox per folder.

1. Find it right before sending (the address changes when it restarts):

   ```bash
   /usr/bin/python3 -I ~/.local/share/claude-duo/duo-send.py inbox --repo /path/to/repo
   ```

   If it says no inbox is open, run it again with `--open`. That opens a session
   in the Duo window with `/duo-inbox` filled in. Ask the user to press Enter
   there, then look the address up again.
2. Show the user the brief and wait for their OK. Then `SendMessage` with `to`
   set to that address, the first line `duo-inbox task: <short title>`, and the
   full brief below it. The inbox has none of this conversation.
3. Its messages come back to this session:
   - `duo-inbox queued: …`: it is busy and will take yours next;
   - `duo-inbox question: …`: answer with `SendMessage` to its `from`;
   - `duo-inbox report: … — done | blocked | refused`: relay it to the user.
   Nothing to poll. If this session closes first, the report stays in the Duo
   window.

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

## When the script says "not signed in"

The Duo's command line has its own sign-in. Give the user the command the
error prints, to run in a Terminal outside any Claude session and sign in with
the Duo's account; do not run it yourself.

## Do not

- Do not pass `--permission bypassPermissions` (it is refused anyway).
- Do not send credentials, tokens or secrets in the task.
- Do not ask the Duo to merge, push to the default branch or change settings.

<!-- claude-duo: installed by duo/install-duo.sh, which updates it; uninstall-duo.sh removes it -->
