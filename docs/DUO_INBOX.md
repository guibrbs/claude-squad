# The Duo inbox: the original Claude hands work to a visible Duo session

The original Claude can already run work on the Duo's account (`duo-send.py
send`), but that work happens out of sight, in `claude -p` or `--bg`. The Duo
inbox puts it in the Claude Duo window instead. It is a Desktop session that
takes tasks by message, does them where you can watch and step in, and
reports back to the original.

```
original Claude ──SendMessage "duo-inbox task: …"──▶ Duo inbox (Claude Duo window)
       ▲                                                   │ does the task
       └──────── "duo-inbox report: … — done" ─────────────┘
```

## Why a standing session, not a new one per task

Claude Desktop starts a new session only when a person asks in that session's
own window. The app refuses `start_session` on turns that another session's
message started ("Start sessions only on the user's own words to you here"),
and it checks that a person is present for it. Deep links (`code/new`) only
fill in the prompt. So the original cannot open and run a Duo session on its
own, and this project does not try to get around that.

What the app does allow is this: a session that receives a message from
another session runs a full turn on it, visible in the window. The inbox is
that session, set up once with your Enter key, and then fed tasks.

## Setting it up

`duo/install-duo.sh` installs two skills: `duo-send` in the original
(`~/.claude/skills`) and `duo-inbox` in the Duo's Code folder (the
`CLAUDE_CONFIG_DIR` of the Duo line). Then, once per folder:

```bash
/usr/bin/python3 -I ~/.local/share/claude-duo/duo-send.py inbox --open --repo ~/path/to/repo
```

This opens a Code session in the Duo window, in that folder, with
`/duo-inbox` filled in. Press Enter. The session renames itself `Duo inbox`
and waits. The original Claude does this step for you when no inbox is open,
and asks you to press Enter.

With several copies, every copy's Code folder gets `duo-inbox`, and `inbox`
takes `--to trio` (or any copy's name) like the other commands; without it,
the Duo. `whois` reads the registries of the original and of every copy.

## Sending it work (the original Claude)

Ask the original Claude something like "give this to the Duo inbox". Its
`duo-send` skill then:

1. looks the inbox up right before sending, because the address changes when
   the session restarts:
   `duo-send.py inbox --repo ~/path/to/repo` prints `uds:/tmp/cc-socks/<pid>.sock`;
2. shows you the brief, and sends it with `SendMessage` to that address, with
   `duo-inbox task: <title>` as the first line;
3. relays what comes back: `duo-inbox queued: …`, `duo-inbox question: …`
   (it answers these), and `duo-inbox report: … — done | blocked | refused`.

## What the inbox does with a task (`duo-inbox` skill)

- **Checks the sender** with `duo-send.py whois <from>`. That command reads
  every account's session registry and says whose session answers on the
  address. The inbox takes work only from the original's or the Duo's own
  sessions.
- **One task at a time.** A task that arrives mid-work gets `queued` and runs
  next.
- **Works in its own folder, with its own permissions.** A sender cannot raise
  them. Code changes go on a new branch, and commits happen only when the brief
  asks. No merges, no pushes to the default branch, no changes to settings,
  permissions or `CLAUDE.md`.
- **Asks the sender** about the brief, and asks **you** (`AskUserQuestion` in
  the Duo window) what only a person can decide.
- **Does not start sessions:** the app would refuse. Subagents are fine.
- **Reports** to the sender's address when done. If the sender is gone, the
  report stays in the Duo window.

## Limits

- **Same permission mode on both sides.** A session in a different permission
  mode than the sender holds cross-session messages for your approval. Approve
  them in the Duo window, or run both apps in the same mode.
- **One inbox per folder.** `inbox` refuses when two match, and when none is
  open.
- **Undocumented transport.** Delivery uses the session socket address, a
  detail of Claude Code (tested with 2.1.304 in both apps). If a version
  changes it, `inbox` finds no address and `duo-send.py send` still works.
- **Both accounts' usage:** the inbox's work counts against the Duo; the
  original's turns against the original.
