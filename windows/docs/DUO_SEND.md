# duo-send (Windows)

`windows/duo/duo-send.py` lets the original Claude Code, or you in a terminal,
hand a task to the Duo's account, in a repository you name.

Sign the Duo's CLI in once, in a terminal outside Claude:

```bash
claude-duo auth login
```

## Commands

```powershell
$tools = "$env:LOCALAPPDATA\Programs\ClaudeDuo\tools"
py -3 -I "$tools\duo-send.py" send "task" --repo C:\repo                 # waits for the answer
py -3 -I "$tools\duo-send.py" send "task" --repo C:\repo --max-cost 2    # with a US$ ceiling
py -3 -I "$tools\duo-send.py" send "task" --repo C:\repo --bg --name x   # background session
py -3 -I "$tools\duo-send.py" agents                                     # the Duo's background sessions
py -3 -I "$tools\duo-send.py" sessions                                   # open sessions and their address
```

- **Synchronous:** runs `claude -p` on the Duo's account and prints the answer.
  With `--json` it returns `{ok, session, cost_usd, answer, error}`. Continue the
  same session with `--resume <session>`.
- **`--bg`:** runs `claude --bg` and returns its id at once. Follow it with
  `claude-duo agents`, `claude-duo logs <id>`, `claude-duo attach <id>` and
  `claude-duo stop <id>`. The original account's `claude agents` does not list it.
- **`sessions`:** reads the `~\.claude-duo\sessions\<pid>.json` registry and shows
  the address of each live session, such as `uds:\\.\pipe\LOCAL\cc-msg-…`. A
  session of the original sends to it with `SendMessage`. Read the address right
  before sending: it changes when the session restarts.

## Differences from macOS

- No `open` or `--open`. On macOS they show a session in the Duo window through a
  `claude-dev://` link; on Windows protocol links belong to the official Claude, so
  there is no way to target the Duo yet.
- The `SendMessage` address is a named pipe, not a Unix socket.
- `attach` runs `claude` as a child process in the same console: Windows has no
  `exec`.

## Isolation

The child `claude` loses every inherited `CLAUDE*` and `ANTHROPIC_*` variable and
gets only `CLAUDE_CONFIG_DIR` set to the Duo's folder, so a Desktop session that
exports its own account does not leak into the Duo. `bypassPermissions` is
refused.
