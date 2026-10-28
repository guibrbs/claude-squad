# loop-duo (Windows)

`windows/duo/loop-duo.py` puts both accounts to work on one repository: one
develops, the other reviews, round after round, until the reviewer approves.

```powershell
$tools = "$env:LOCALAPPDATA\Programs\ClaudeDuo\tools"
py -3 -I "$tools\loop-duo.py" run "goal" --repo C:\repo
py -3 -I "$tools\loop-duo.py" run "goal" --repo C:\repo --dev duo --reviewer original --until pr --max-cost 5
py -3 -I "$tools\loop-duo.py" list
```

## How it works

1. Creates the `loop-duo/<goal>-<id>` branch from HEAD, in a worktree at
   `%LOCALAPPDATA%\claude-duo\loops\<id>\tree`. With `--no-worktree` it creates the
   branch in the folder itself, which must be clean.
2. **The developer** (default: original) implements, commits and closes with a
   ```` ```loop-duo ```` JSON block: `review`, `question`, `answer` or `blocked`.
3. Untracked files go back to the developer once per round; the loop never
   commits them.
4. **The reviewer** (default: Duo) runs without `Edit`, `Write` and
   `NotebookEdit` and closes with `approved`, `changes`, `question` or `answer`. A
   turn that changes HEAD or a tracked file stops the loop (`reviewer_edited`),
   and its verdict does not count.
5. `claude -p` has no AskUserQuestion, so one agent's question goes to the other,
   up to 4 per round.

## Ceilings and output

- `--rounds` (default 6) and `--max-cost` (estimated US$, both accounts added up).
- `--until pr`: after approval, pushes the branch and opens a draft PR with `gh`.
  Merging always stays with a person.
- The whole conversation is in
  `%LOCALAPPDATA%\claude-duo\loops\<id>\conversation.md` and the state in
  `loop.json`. `list` marks a loop whose process died as `interrupted`.

## Requirements

- Both accounts with the CLI signed in: `claude auth login` for the original and
  `claude-duo auth login` for the Duo. The loop checks this before it starts.
- `git` and, for `--until pr`, `gh` on the PATH.
