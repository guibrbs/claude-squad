# Claude Duo for Windows

**Two Claude Desktop accounts on one Windows PC, side by side.** The official
**Claude** app keeps one account and **Claude Duo** opens with another: personal
and work, two organizations, or two plans. Each app has its own sign-in, its own
Claude Code profile and its own conversations, and both can be open at the same
time. The Duo has a blue icon and its own taskbar button.

This is the Windows counterpart of the macOS tooling in this repository.

> ⚠️ **Experimental and unofficial.** An independent project, not affiliated with
> or endorsed by Anthropic. Use it at your own risk and follow the terms of use of
> each account and your organization's rules.

The scripts' prompts and messages are in Portuguese (Brazil) for now.

## Install

Requirements: Windows 10 or 11 and the official Claude installed from the
Microsoft Store or its MSIX installer. Python 3 (`py -3`) is needed only for the
optional tools.

1. Download or clone this repository.
2. Double-click **`windows\Install Claude Duo.cmd`**.

The installer:

1. checks that the official Claude (MSIX) is installed;
2. copies the app to `%LOCALAPPDATA%\Programs\ClaudeDuo\app` and renames the
   executable to `Claude Duo.exe`. Copying takes a few minutes. Nothing inside the
   copy changes, so the executable keeps Anthropic's signature; the installer
   checks it and refuses a copy whose signature is not valid;
3. makes the blue icon from the installed Claude's icon (Anthropic's artwork is
   not stored in the repository);
4. creates the **Claude Duo** shortcut in the Start menu and on the desktop, with
   the Duo's own profile (`--user-data-dir`) and the taskbar ID of its window;
5. installs the `claude-duo` command (the Claude Code CLI on the Duo's account)
   and the Python tools in `%LOCALAPPDATA%\Programs\ClaudeDuo\tools`.

### After installing, once

1. **Blue taskbar icon:** Start menu > right-click **Claude Duo** > **Pin to
   taskbar**. Pin it from the Start menu, not from the open window: the window does
   not know which profile is the Duo's.
2. **Account:** open Claude Duo and sign in with the second account.
3. **Claude Code profile:** in the Code tab, open the environment selector below
   the prompt box, hover over **Local**, click its gear, add the line
   `CLAUDE_CONFIG_DIR=%USERPROFILE%\.claude-duo` (with the full path the installer
   prints) and select **Save changes**. Quit the Duo from the tray (Quit) and open
   it again. Without this, the Duo's Code tab uses the official app's `~\.claude`.
4. **`claude://` links in the official app:** Settings > Apps > Default apps >
   Choose defaults by link type > **CLAUDE** > **Claude**. See
   [Limitations](#limitations) for why.

Installer options, from PowerShell: `-Name "Claude Work"`, `-Hue 120` (another
color), `-NoDesktopShortcut`, `-WithSkill` (installs the `duo-send` skill in the
original's `~/.claude`) and `-Launch`.

## Update

The copy **does not update itself**. After the official Claude updates, quit the
Duo and run `Install Claude Duo.cmd` again. It copies the new version and keeps
the profiles. When the version has not changed, it does not copy again.

## How it works

| | macOS (this repository) | Windows |
|---|---|---|
| App | copy of Claude.app, signed locally | copy of the app folder with **nothing changed inside**: only the `.exe` name changes, and Anthropic's signature stays valid |
| Separate profile | `--user-data-dir` + a new bundle id | `--user-data-dir` |
| Sign-in encryption | Keychain `<productName> Safe Storage` | DPAPI, with the key in the `Local State` **of each profile** |
| Own icon | recolored icon in the bundle | blue shortcut carrying the window's taskbar ID (`electron.app.Claude`) |

The copy runs outside the MSIX package, so Electron gives its windows the default
taskbar ID `electron.app.Claude` instead of the package's. The shortcut carries
that same ID, so the running Duo shows up under the pinned blue icon and apart
from the official app.

Why no launcher of its own: a first version compiled a small launcher `.exe` on
the machine, and Bitdefender's Advanced Threat Control removed it as suspicious
behavior. Now the only executable is Claude's own, signed by Anthropic.

## Claude Code in the terminal for the Duo's account

The app and the `claude` command keep separate sign-ins. Sign the CLI in once, in
a terminal outside any Claude session:

```bash
claude-duo auth login
```

`claude-duo auth status` shows which account it is. Plain `claude` stays on the
original account.

### Same instructions, skills and memories in Code (optional)

With the Duo closed:

```powershell
py -3 -I "$env:LOCALAPPDATA\Programs\ClaudeDuo\tools\share-code-profile.py" --apply --backup-existing
```

It links the Duo's `CLAUDE.md`, skills, output styles, tools, plugins and
per-project memories to the original's (folders become junctions, which need no
administrator rights). `settings.json` is copied once, without the keys that
select an account or provider. Sign-in, sessions and history stay separate.
Details in [docs/PROFILE_REUSE.md](docs/PROFILE_REUSE.md).

### duo-send: hand a task to the Duo's account

```powershell
py -3 -I "$env:LOCALAPPDATA\Programs\ClaudeDuo\tools\duo-send.py" send "task" --repo C:\path\to\repo
```

Details in [docs/DUO_SEND.md](docs/DUO_SEND.md).

### loop-duo: one account develops, the other reviews

```powershell
py -3 -I "$env:LOCALAPPDATA\Programs\ClaudeDuo\tools\loop-duo.py" run "goal" --repo C:\path\to\repo
```

Details in [docs/LOOP_DUO.md](docs/LOOP_DUO.md).

## What is separate and what is not

| | Separate per account | Shared |
|---|---|---|
| Desktop sign-in, conversations, settings and Cowork | ✅ `%LOCALAPPDATA%\Claude-Duo` | |
| Code sessions (the app's list) | ✅ in each app's profile | |
| Claude Code settings, history, plugins | ✅ `~\.claude-duo`, after step 3 | |
| `claude://` links and the Claude in Chrome extension | | ⚠️ the Duo claims them (see below) |
| Files, SSH keys, git config, the rest of your user | | ⚠️ always |

The separation is **organizational**, not a security boundary: both accounts run
as the same Windows user, so an MCP server or agent in one can read the other's
files. For real isolation, use another Windows user.

Code sessions live on the computer, in each app's profile. Moving an account to
the other app does not take its sessions along.

## Limitations

- **`claude://` links:** every time it starts, the Duo registers itself as their
  handler. A link opened by it would land outside the Duo's profile, because
  Windows starts the `.exe` without `--user-data-dir`. That is why step 4 makes the
  official Claude the default for CLAUDE links; that Windows choice takes
  precedence over the Duo's registration.
- **Claude in Chrome extension:** it talks to whichever of the two apps was opened
  last (or last reacted to a change in Chrome).
- **Manual updates:** run the installer again after each Claude release.
- **Disk space:** the copy takes about 700 MB.
- **Global shortcut** `Ctrl+Alt+Space`: stays with the app opened first.
- **Connectors (Google Drive, Slack…):** connect them in a private browser window
  signed in to the Duo's account (claude.ai > Settings > Connectors), then reload
  the Duo with Ctrl+R.
- **Desktop shortcut:** corporate antivirus software may block it. The installer
  warns and goes on with the Start menu shortcut alone.
- **Still being validated:** Cowork running in both apps at the same time.

## Uninstall

Double-click **`windows\Uninstall Claude Duo.cmd`**. It removes the copy, the
shortcuts, `claude-duo` and the skill, and gives the `claude://` links and the
Chrome extension back to the official Claude. The profiles stay where they are.
To send them to the Recycle Bin:

```powershell
powershell -ExecutionPolicy Bypass -File windows\scripts\uninstall.ps1 -RemoveProfiles
```

## Development

```bash
py -3 -m unittest discover -s windows/tests
```

The tests run in temporary folders and never touch the installed Claude.
