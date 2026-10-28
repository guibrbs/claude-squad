# Sharing the Claude Code profile with the Duo (Windows)

`windows/duo/share-code-profile.py` makes the Duo use the original Claude Code's
instructions, skills and memories, without mixing sign-in or history.

```powershell
py -3 -I "$env:LOCALAPPDATA\Programs\ClaudeDuo\tools\share-code-profile.py"                   # dry run
py -3 -I "$env:LOCALAPPDATA\Programs\ClaudeDuo\tools\share-code-profile.py" --apply --backup-existing
```

## What is linked

| Item (source `~\.claude`) | In the Duo (`~\.claude-duo`) |
|---|---|
| `skills`, `output-styles`, `tools`, `plugins` | a junction to the original's folder |
| `projects\<project>\memory` | a junction, one per project |
| `CLAUDE.md` | a symbolic link; a hardlink when Windows does not allow one |
| `settings.json` | **copied once**, without `apiKeyHelper`, `forceLoginMethod`, `forceLoginOrgUUID`, `aws*`, `gcp*`, `otelHeadersHelper` and without the `ANTHROPIC_*`, `CLAUDE_CODE_USE_*`, `CLAUDE_CODE_OAUTH*`, `AWS_*`, `GOOGLE_*`, `VERTEX_*`, `CLOUD_ML_*` and `CLAUDE_CONFIG_DIR` variables |
| user-scope MCP servers (`--mcp-servers`) | copied name by name into the Duo's `.claude.json`; a name the Duo already has is kept |

Nothing else is read or written: `.credentials.json`, `sessions`,
`history.jsonl`, transcripts and account state stay separate.

## Why junctions

On Windows a symbolic link needs administrator rights or Developer Mode. A
junction needs neither and works for folders. For `CLAUDE.md`, a file, the script
tries a symbolic link and falls back to a hardlink with a warning. A hardlink
breaks when an editor saves by replacing the file; in that case turn on Developer
Mode and run the script again.

## Safety rules

- The default is a **dry run**. `--apply` runs only with Claude Duo closed.
- An item that already exists in the Duo is never deleted. With
  `--backup-existing` it moves to `~\.claude-duo\.share-backup-<date>`; without it
  the script refuses.
- A source that is already a link or junction is refused: links are not chained.
- `~\.claude-duo\.shared-from-original.json` records what was linked; a later run
  does not overwrite a `settings.json` you edited in the Duo.
- **Plugins:** install and update them from the original. Both see the same folder.
