# loop-duo: one account develops, the other reviews

With Claude Duo, the same Mac has two Claude Code accounts: the original
(`~/.claude`) and the Duo's (the `CLAUDE_CONFIG_DIR` of the Duo line in the
config, `~/.claude-duo` by default). `duo/loop-duo.py` puts both to work on the
same repository. One develops and the other reviews, round after round, until
the reviewer approves the goal or a ceiling stops the loop.

```bash
/usr/bin/python3 -I ~/claude-squad/duo/loop-duo.py run "goal" --repo ~/path/to/repo
/usr/bin/python3 -I ~/claude-squad/duo/loop-duo.py list
```

The script is self-contained: only the Python 3.9 standard library, which
comes with the Command Line Tools. It does not depend on the Duo toolset and
does not touch the apps.

## How a round goes

1. The loop creates the `loop-duo/<goal>-<id>` branch from HEAD, in a worktree
   of its own at `~/.local/share/claude-duo/loops/<id>/tree`. With
   `--no-worktree`, the branch stays in the folder itself, which must have no
   pending changes.
2. The developer (the original account by default) gets the goal, works,
   commits and closes its turn with the `review` state.
3. The loop commits the tracked changes left uncommitted, so the reviewer sees
   a closed round. It never adds untracked files: a `.env`, a log or a dump a
   test left behind could otherwise reach a push. It lists them to the
   developer once per round, to commit or ignore, and whatever is still
   untracked stays out of every commit; the reviewer is told about it.
4. The reviewer (the Duo's account by default) gets the goal, the summary, the
   commits and the `diff --stat` since the base. It closes with `approved` or
   `changes`. The changes go back to the developer in the next round.

Each account continues its own session (`claude -p --resume`) for the whole
loop. Both run in the worktree folder. The conversation between the agents is
in `loops/<id>/conversation.md` and the state in `loops/<id>/loop.json`: the
folder is 0700 and the files 0600.

## Why a text protocol

In `claude -p`, the non-interactive mode, the tool for asking the user
(AskUserQuestion) is not offered to the model. We checked this in Claude Code
2.1.303. The only documented way to answer that tool outside the interface is
the Agent SDK's `canUseTool`, which a standard-library script does not have.
With nothing in its place, an agent's question would hang or become a guess.

So every turn ends with a block fenced by ` ```loop-duo `, holding JSON that
tells the loop what comes next:

| Who | State | Fields |
|---|---|---|
| developer | `review` | `summary` |
| developer | `blocked` | `reason` |
| both | `question` | `questions`: `[{"question": "...", "options": ["..."]}]` |
| both | `answer` | `answers`: one per question, in the same order |
| reviewer | `approved` | `summary` (the verdict) |
| reviewer | `changes` | `comments`: one item per fix |

A question that would go to the user, including a skill's questionnaire,
becomes a `question` block. The loop takes the questions to the other agent and
brings the answers back, in both directions. The reviewer decides as a tech
lead would. What only a person can decide (access, credentials, spending)
comes back as `blocked`.

A turn without a valid block is asked for once. If the agent still leaves it
out, the loop stops as `failed`.

## Accounts and environment

`--dev` and `--reviewer` take `original` or any managed copy of the installed
config, by its name in any case (`duo`, `trio`…); the folder is the
`CLAUDE_CONFIG_DIR` of the copy's line. An unknown name is refused with the
list of accounts this Mac has. Without a config there is one copy, the Duo at
`~/.claude-duo`.

The loop removes the `CLAUDE*` and `ANTHROPIC_*` variables from each
`claude`'s environment. A Desktop session exports its own account in them, and
the other account must not inherit it. The Duo's account gets only its own
`CLAUDE_CONFIG_DIR`. The original runs without the variable: pointed at
`~/.claude`, it would move `~/.claude.json` into that folder.

Each account's command line must be signed in, apart from its Desktop app:
before the first round, `claude auth status` runs on both accounts, and a
signed-out one stops the loop with the command to run in a Terminal
(`CLAUDE_CONFIG_DIR=~/.claude-duo claude auth login` for the Duo's). Both
turns run with `claude -p`, which does not ask for folder trust, so the
loop's worktree needs none.

## Permissions

- **Default mode `auto`.** `--permission` accepts `auto`, `acceptEdits`,
  `manual`, `dontAsk` and `plan`.
- **`bypassPermissions` is refused:** the loop runs with nobody watching.
- **Denied tool:** each agent's role asks it not to insist. It takes another
  path, asks the other agent or closes as `blocked`.
- **Reviewer without edit tools:** it runs with
  `--disallowedTools Edit Write NotebookEdit`.
- **An approval covers only the developer's work.** The reviewer keeps Bash
  for the tests and `git diff`, so it could still patch a file or commit. The
  loop records HEAD and the tracked files before every reviewer turn; if
  either changed when the turn ends, the loop stops as `reviewer_edited` and
  the verdict does not count. Untracked files, such as test caches, are not
  checked: the loop never commits them.
- **This is not a sandbox.** The reviewer still runs commands. What holds the
  review in place is its role, the separate branch and that check.

## Ceilings

- `--rounds` (default 6): rounds without approval stop the loop as
  `round_ceiling`.
- `--max-cost`: stops as `cost_ceiling` when the **estimated** cost reaches
  the ceiling. It is the sum of the `total_cost_usd` the CLI reports. On a
  subscription, it is an estimate, not the bill.
- Up to 4 question exchanges per round. More than that, or a question without
  text, stops as `blocked`.
- An account that does not answer, or that closes without a block after the
  reminder, stops as `failed`.

Every round uses both accounts' usage.

## Why there is no automatic merge

`--until local` (the default) leaves the branch ready. `--until pr` pushes and
opens a **draft** PR with the goal, what was done and the reviewer's verdict,
and only after approval. Merging stays with a person.

The reviewer's approval comes from another agent, not from a human review. The
original design tried an automatic merge, and Claude Code's auto mode blocked
it. The final design keeps that limit: there is no `merge` level.

## The real test

Before this port, the design ran with two real accounts:

1. The developer asked "always float, or int?", and the reviewer answered.
2. The developer implemented and committed.
3. The reviewer ran the tests and approved in 1 round.

The estimated cost was US$ 0.62.

## Limits

- **Running and resuming.** The loop is sequential and runs in the
  foreground. An interrupted loop does not resume: `list` shows it as
  `interrupted`, and the branch keeps what was committed.
- **Accounts.** Both must have opened Code on this Mac, and both `claude`
  commands must be signed in (see [Accounts and environment](#accounts-and-environment)).
- **Worktrees.** They live in `~/.local/share/claude-duo/loops`. To remove one,
  run `git worktree remove` in the repository. Uninstalling the Duo moves
  `~/.local/share/claude-duo` to the Trash, loops included.

## Credit

The idea and the first design came from `contas`, a personal Claude Code
account-management plugin by the author of this port. This script is the port
to Claude Duo, standard library only, and uses the two accounts the Duo already
manages.
