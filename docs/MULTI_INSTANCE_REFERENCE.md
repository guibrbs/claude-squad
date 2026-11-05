> **Technical reference from the original project.** This is the README of
> [multi-claude-desktop](https://github.com/cgclark/multi-claude-desktop) by
> Christopher Clark, with the hardening added in this repository. To install and
> use Claude Duo, see the [main README](../README.md).

# Multiple Claude Desktop Instances (macOS)

This local revision includes script hardening and regression tests. Read
[HARDENING.md](../HARDENING.md) before installing; it describes stricter config
validation, safe rebuild behavior, migration, and the signing/isolation limits
that remain. Copy the shared `claude-instances-common.sh` with the other scripts.
The [deeper limitation review and roadmap](LIMITATIONS_AND_ROADMAP.md)
includes controlled evidence and the proposed GUI launch architecture; it does
not claim that all remaining limitations have been fixed.
The [current execution roadmap](ROADMAP.md) lists the
remaining work, release criteria and the steps that need account login. The
hardened branch is still experimental; graphical two-account Code behavior has
not been validated.
**The first live Duo launch is blocked:** it requested access to the existing
`Claude Safe Storage` Keychain entry; the user denied access before signing in.
See the [finding and resumption criteria](KEYCHAIN_FIRST_LAUNCH.md).
The current candidate is not approved for daily two-account use.
Two [namespace experiments](KEYCHAIN_NAMESPACE_EXPERIMENTS.md) now
distinguish a failed bundle/helper rename from a product-name prototype that
opened without permission prompts and had a distinct named Keychain item.
The latter still needs Desktop-flow validation and production integration;
the installed Duo has not been replaced.
The preferred deployment is now **the official Claude app plus one managed
copy**, each using a different account and Desktop/Code data pair. The original
keeps its official updater; automatic detection and rebuilding of the copy are
planned in the roadmap. Current watchdog checks only notify. This documentation
does not change an existing installation or disable legacy copies.
The copy is named **Claude Duo**. The [versioned tool installer](TOOLSET_INSTALLATION.md)
now stages/verifies complete pinned toolsets and activates them atomically;
it does not migrate or rename existing apps or profiles.
The [graphical validation plan](GUI_VALIDATION.md) uses the existing
macOS session, new Duo data directories and an opt-in private `~/Applications`
destination. No additional macOS user is requested. The original's existing
profile participates in GUI testing; new Duo directories are not OS isolation.
The [configuration reuse proposal](PROFILE_REUSE.md) covers importing
preferences, MCP definitions and plugin selections while keeping authentication
separate; the importer is not implemented yet.

The intended workflow is side-by-side Claude Desktop instances with distinct
accounts, profile directories and Dock icons. Separate directories are implemented;
independent authentication still requires the blocked GUI validation above.

Claude Desktop accepts Electron's `--user-data-dir`, so isolating a second profile is
easy. Giving each instance its own **Dock icon** is not: a thin wrapper `.app` can't do
it, because the running process belongs to whichever bundle it launched. So each
instance is built as a standalone copy with its own bundle identity — costing **~2 MB**
thanks to APFS cloning, rather than an 823 MB duplicate.

Built on Apple Silicon with an APFS volume. On Intel, drop the `arch -arm64` pin.

---

## Daily use

Target layout for original + one copy (graphical login validation is pending):

| App | Desktop profile | Code configuration |
|---|---|---|
| `/Applications/Claude.app` | `~/Library/Application Support/Claude` | `~/.claude` |
| `/Applications/Claude Duo.app` | `~/Library/Application Support/Claude-Duo` | `~/.claude-duo` |

Use the original for one account and the copy for the other. Reserve both
default data directories for the original; only the copy belongs in the managed
instance configuration. The source app is never a rebuild destination.

**Legacy installations:** a `Claude Personal.app` copy may share the original's
default profile. Do not run that legacy copy and the original together. Review
the transition before changing existing configuration, shortcuts or apps. The
explicit `@mode original-copy` policy now reserves the defaults for the
original; it accepts one or more managed copies. Headerless configs retain the older deployment.

```bash
claude-instance-check.sh      # health check
claude-apps-refresh.sh --list # what's configured
```

---

## Configure original + Claude Duo

Instances are defined in `~/.config/claude-instances/instances.conf`:

```
# name | bundle id | profile dir | icon hue | env (optional)
@mode original-copy
Duo | com.example.claude-duo | ~/Library/Application Support/Claude-Duo | 212 | CLAUDE_CONFIG_DIR=~/.claude-duo
```

This example lists only the managed copy. The original keeps its normal
default directories and is not listed for rebuilding.

The mode directive must appear once before instance rows. It rejects the
original's default Desktop/Code paths, physical/case aliases and multiple copies.
Existing installation/config remains unchanged until reviewed migration. A
policy change needs a reviewed rebuild; verification rejects an older launcher's
policy. Multiple managed copies remain a legacy mode, selected explicitly with
`@mode legacy-copies` or retained by headerless configs.

Hues: `0` red · `30` orange · `55` yellow · `142` green · `175` teal · `212` blue ·
`275` purple · `320` pink.

### Per-instance environment

The optional 5th column supports **only `CLAUDE_CONFIG_DIR=path`** in this
subscription-account mode, with leading `~` expanded as data. Provider/API keys,
endpoint, proxy and runtime overrides are rejected. Never store tokens here.
Every additional Code account must use a distinct `CLAUDE_CONFIG_DIR`; otherwise
configuration validation fails. In original-copy mode the default `~/.claude`
is reserved for the original; the legacy mode allows at most one copy to use it.
`--list` displays variable names without revealing their values.

The launcher uses privileged-mode Bash to ignore inherited startup files and
starts the child with a minimal environment and a fixed system `PATH`. It refuses
all caller arguments, including debug/sandbox/profile flags and URL callbacks.
If an inherited proxy variable is present, launch fails rather than silently
removing an approved proxy and connecting directly. Provider/proxy customization
is not supported by this mode; use your approved Claude configuration instead.

Code sessions can also load selected variables from shell initialization and
their local environment editor/settings, so `.zshrc` is not universally
irrelevant to GUI Code. Parent-process exports alone do not prove the effective
configuration of every Code session. See the official
[local environment documentation](https://code.claude.com/docs/en/desktop#local-sessions)
and the [residual limitation analysis](LIMITATIONS_AND_ROADMAP.md).

Data paths must use the existing flat `~/Library/Application Support/Claude`
or `Claude-*` layout and `~/.claude` or `.claude-*`, or managed
`~/Library/Application Support/Multi Claude Desktop/Profiles/<UUID>/desktop`
and `/code` directories. Legacy suffixes must be printable ASCII to avoid Unicode
filesystem aliases. All Desktop/Code directory pairs must be disjoint. Files and
ancestors must belong to this user, without group/other write access or custom
allow ACLs. Standard macOS deny-delete ACLs are allowed. The config itself must
be inside home with the same ownership/permission checks. Do not run with sudo.
No history migration happens automatically; see [HARDENING.md](../HARDENING.md).

The motivating case is `CLAUDE_CONFIG_DIR`. Claude Code reads hooks from
`$CLAUDE_CONFIG_DIR/settings.json` and stores agent-mode session history in
`$CLAUDE_CONFIG_DIR/projects/`. Left at the default, every instance shares `~/.claude`, so
hooks from a work instance fire against your personal configuration. Setting it per
instance splits both.

```
Enterprise | com.example.claude-enterprise | …/Claude-Enterprise | 212 | CLAUDE_CONFIG_DIR=~/.claude-work
```

Check the complete generated launcher and local signature without dumping the
running process's environment:

```bash
claude-apps-refresh.sh --verify
```

⚠️ **Pointing an instance at a new config dir does not bring its history along.** Agent-mode
sessions live in `$CLAUDE_CONFIG_DIR/projects/<encoded-path>/*.jsonl`. Switch the variable
and the app looks somewhere new — past sessions appear to vanish, though nothing is
deleted. Keep existing paths until a closed-app backup and compatible migration
have been reviewed. The [content checkpoint primitive](PROFILE_CHECKPOINTS.md)
does not migrate histories or establish that a restore is safe.

Watch for a config dir that already holds a **stale copy** of the same session id: the app
will show the older one and the newer history looks lost. Compare entry counts and last
timestamps rather than file mtimes, which can be misleading.

Rules the builder enforces, because both are silently destructive:

- **Bundle ids must be unique.** Two instances sharing one id are the same app to macOS.
- **Profiles must be unique.** Two instances on one profile can corrupt it.
- **Code config directories must be unique.** Existing symlinks are resolved;
  dot components and paths differing only in ASCII case are rejected.

Changing an instance's bundle id later can force a fresh sign-in for that instance — macOS
treats it as a different app for keychain purposes.

### What is isolated, and what is not

Everything under `~/Library/Application Support/<profile>` is per-instance: account,
conversations, settings, MCP config, caches — **and Cowork files**. On macOS the Cowork
user-files location resolves under Electron's `userData`, which is the profile, so each
instance gets its own by default. `scratch-workspaces/` and `cowork-enabled-cli-ops.json`
live inside the profile directory for the same reason.

(Claude's `NSDocumentsFolderUsageDescription` mentions storing "scheduled tasks, live
artifacts and other Cowork files" in Documents, which is easy to misread. That is a TCC
prompt string. In the app, `getPath('documents')` is used on **Windows only** — on macOS
Documents is never the Cowork location. `~/Documents/Claude`, if you have one, is your own
folder, not something Claude manages.)

Claude Code's config — hooks, agent-mode session history — is **not** in the profile. It
lives in `$CLAUDE_CONFIG_DIR`, default `~/.claude`, which is shared by every instance
unless you set it per instance. See [Per-instance environment](#per-instance-environment).

| | Isolated per instance | Shared |
|---|---|---|
| Desktop profile files: conversations, settings, MCP config | Separate requested `userData` paths | Effective account independence remains unverified |
| Keychain / login | Unverified; first live Duo launch requested the existing `Claude Safe Storage` entry and was denied | Bundle name remains `Claude`; fresh directories and a distinct bundle ID did not prevent that request |
| Cowork files, scratch workspaces | ✅ | |
| Claude Code hooks + agent-mode sessions | ✅ *once `CLAUDE_CONFIG_DIR` is set* | ⚠️ otherwise |
| SSH keys, git config, the rest of your home dir | | ⚠️ always |

**The last row is the one that matters** and no amount of configuration here fixes it.
Every instance runs as the same Unix user with the same permissions, so an MCP server or
agent running in one instance can read files belonging to another. This setup gives you
*organisational* separation — separate accounts, separate histories, separate icons — not
a boundary the OS enforces. If you need the latter, for a client with confidentiality
obligations, use a **separate macOS user account**: separate home, keychain, TCC grants
and processes that genuinely cannot read across. In that model each account just runs
stock Claude and this tooling is unnecessary.

**Choosing an explicit Cowork location.** `coworkUserFilesPath` is an optional, persisted,
per-profile config key. Set it if you want a named folder per instance
(`~/Documents/Claude-Client`) rather than the in-profile default — the app verifies a
stored path and falls back if it cannot. That is for your convenience; the default is
already per-instance.

Finally: each instance has a **new bundle identity**, so macOS treats it as a brand-new
app. Expect fresh permission prompts for Documents, Desktop, Downloads, Microphone and
Screen Recording, plus a full first-run setup against an empty profile.

---

## ⚠️ Recovery after Claude.app updates

**This is the one maintenance task.**

### What happens

Each instance is a **point-in-time copy** of `Claude.app`, made with APFS cloning, and
independent at the bundle-file level once made. This does not establish credential
independence. When `Claude.app` updates itself, the copies **do not
change** — they keep running the old version, silently and indefinitely.

Worse: `Claude.app` only updates itself **when it runs**. If you only ever launch the
coloured copies, nothing updates anything, and everything freezes at the version it was
built at.

### Symptoms you are stale

- New Claude features in the release notes but not in your apps
- `claude-apps-refresh.sh --status` shows a version mismatch
- Disk usage creeping up (see *Disk creep*)

### The routine — every few weeks

```bash
# 1. Check for drift
claude-apps-refresh.sh --status

# 2. Quit every managed copy (Cmd-Q)

# 3. Let the original Claude finish its official update, then quit it (Cmd-Q)
open -a "/Applications/Claude.app"

# 4. Rebuild
claude-apps-refresh.sh

# 5. Verify
claude-apps-refresh.sh --verify
claude-instance-check.sh
```

Without updating stock Claude there is no new version to rebuild *from*, and
step 4 just reproduces the same version.
The builder requires observed Desktop apps/helpers, including the original,
to be closed. The watchdog currently only checks and notifies; waiting for idle
apps and automatically rebuilding the copy after an official update are planned,
not installed behavior. Until reviewed transition, follow the configuration of
your actual deployment rather than removing a legacy copy from this example.

### What a rebuild touches

| Preserved | Rebuilt from scratch |
|---|---|
| Every profile (accounts, conversations, settings, MCP config) | The app bundles |
| Instance config and icon masters | Bundle identity, launcher, signature |

The builder does not migrate profile contents: profiles live **outside** the
bundles. Opening a newer app may migrate them, so retaining an older bundle
alone does not make a later downgrade safe.

### Disk creep

Each instance normally costs ~2 MB real, because APFS clone-sharing means unchanged
files share blocks with `Claude.app`. **A Claude update breaks that sharing** — old
blocks are retained for your copies and real usage climbs toward the full 823 MB each.
Rebuilding re-establishes sharing for the current copies. Retained previous
bundles still keep old blocks alive: the hardened builder does not automatically
prune them or promise that a rebuild reclaims all old space.

```bash
df -h /     # real usage; Finder will always claim ~825 MB per copy
```

### If a rebuild fails partway

The hardened builder verifies a staged copy, writes an immutable private journal
and uses a native atomic exchange for a replacement. A first install uses an
exclusive rename that refuses to overwrite a path appearing concurrently. There
is no delete/move fallback if the filesystem refuses the required operation.

The printed `/Applications/.claude-txn.XXXXXXXX` directory holds the journal and,
after a replacement, the previous bundle at `instance.app`. Inspect the printed
transaction before retrying:

```bash
claude-apps-refresh.sh --recover .claude-txn.XXXXXXXX
```

This only observes the recorded layout and checks a committed launcher/signature;
it does not restore, replay, launch or delete bundles/profiles. Unknown state fails
closed. Later rebuilds can supersede older journal layouts. After resolving the
reported failure, a fresh rebuild creates a new transaction:

```bash
claude-apps-refresh.sh personal    # or any instance name, or with no argument for all
```

If the source itself is broken, reinstall Claude, then rebuild.

Rebuild/recovery, full verification/watchdog, cache cleanup and checkpoints
require macOS 12+ and Xcode Command Line Tools. Install the whole `bin/claude-*`
set, including all workers, C sources and the maintenance header. A shared kernel lock holds
across the worker/children and releases on exit; do not remove its persistent
`.claude-instances.lock` file to force another build. Old `.refresh-lock`
directories still require inspection. Retained bundles are not automatically
pruned or treated as safe profile downgrades; see [HARDENING.md](../HARDENING.md).

### If Claude.app updates while a copy is running

Nothing breaks. The running copy holds its own cloned files. Quit and rebuild whenever.

### ⚠️ "Update available" *inside* a coloured instance — don't click it

Each copy carries Claude's own updater (Squirrel/ShipIt). Squirrel replaces the bundle
**at its own path** — so an update accepted inside `Claude Enterprise.app` would try to
overwrite *that* app, not stock Claude. The bundle is user-writable, so nothing on the
filesystem stops it.

In practice it will most likely **fail**: ShipIt validates the downloaded app's code
signature against the running one. The copies are ad-hoc signed (no Team ID, identifier
`com.example.claude-*`) while the download is Developer ID signed by Anthropic, so the
check should reject it — possibly with a vague or silent error.

But if it ever *succeeds*, it overwrites the instance's identity:

| | Before | After a successful in-app update |
|---|---|---|
| `CFBundleIdentifier` | `com.example.claude-enterprise` | `com.anthropic.claudefordesktop` |
| `CFBundleExecutable` | `launcher` | `Claude` |
| Icon | your colour | orange |

Losing `CFBundleExecutable=launcher` is the damaging part: the launcher is what injects
`--user-data-dir`. Without it, that app falls back to Electron's **default profile** —
your personal one — so two apps end up on one profile, which is exactly the collision
`claude-instance-check.sh` exists to catch.

**So: dismiss update prompts inside the coloured apps.** Update stock `Claude.app`
instead, then rebuild (see the routine above).

To check whether an instance still has its identity:

```bash
claude-apps-refresh.sh --verify
```

```
Claude Personal: launcher/signature/content-record checks passed (ad-hoc copy; user-writable record; account isolation not established)
Claude Enterprise: FAILED -- rebuild required
```

Exit 1 if any instance was clobbered. The fix is always the same — rebuild it.

Verification also requires a private full-tree build record: an added/modified
resource followed by local re-signing still fails. Missing legacy records need
a rebuild; no new baseline is silently trusted. See [APP_INTEGRITY.md](APP_INTEGRITY.md)
for the exact transformations and remaining user-writable-record limits.

*(Reasoned from the bundle's updater machinery and signing state, not observed: at the
time of writing there was no pending update to test against.)*

---

## Health check

```bash
claude-instance-check.sh
```

```
PID      APP                      PROFILE
79012    Claude Personal.app      /Users/you/Library/Application Support/Claude
79210    Claude Enterprise.app    /Users/you/Library/Application Support/Claude-Enterprise
OK: observed profiles are distinct.
```

- **Exit 0** — healthy. One instance running is fine; that is not an error.
- **Exit 1** — **profile collision**: two or more processes on one profile, each row
  flagged `<-- COLLISION`. Quit all but one immediately; concurrent writes to one
  LevelDB can corrupt it.
- **Exit 2** — state unknown: process enumeration or profile inspection failed.
  Do not interpret this as permission to rebuild or delete caches.

The collision case is the whole point of the check, and it is easy to hit by accident —
launching stock `Claude.app` while `Claude Personal` is open does it, since both default
to the same profile.

It matches executables ending in `/Contents/MacOS/Claude`, catching every copy while
excluding `Claude Helper` processes.

---

## Scripts

Legacy installations expose these in `~/bin`. The versioned installer keeps
its own complete releases; use its dispatcher to pin one release for an
operation. Existing `~/bin` entries remain unchanged until reviewed integration.

| Script | What it does |
|---|---|
| `claude-instance-check.sh` | Health check; detects profile collisions |
| `claude-apps-refresh.sh` | Build/rebuild instances. No args = all; or a name; `--status`; `--list`; `--verify`; `--recover <transaction>` |
| `claude-recolor-icon.sh` | Generate an icon master: `claude-recolor-icon.sh <hue> <out.icns>` |
| `claude-stale-check.sh` | Watchdog: stale versions or broken identity/signature/launcher |
| `claude-clear-caches.sh` | Quit an instance, delete only regenerable caches, relaunch |
| `claude-toolset.sh` | Reviewed installer entry; verify a pinned package and activate complete toolsets; see [protocol](TOOLSET_INSTALLATION.md) |

---

## Reclaiming disk space

Finder badly overstates what this setup costs. Ranked by what is actually recoverable:

| Target | Typical size | Notes |
|---|---|---|
| `vm_bundles` (per profile) | **~11 GB each** | the Cowork VM sandbox. Re-downloads on demand (~1.2 GB compressed → 10 GiB image) |
| `Cache` + `Code Cache` (per profile) | 0.3–1.5 GB | regenerable; `claude-clear-caches.sh` |
| `~/.claude/projects/<dir>` | can reach several GB | agent-mode session transcripts — your history, prune by date deliberately |
| The app bundles | **~0** | see below |

**Do not try to slim the app bundles.** They report ~885 MB each in Finder but are APFS
clones of the source, so their real cost is single-digit megabytes — measured repeatedly
at `-7 MB`, `-4 MB`, `3 MB` on rebuild. Worse, *most* slimming makes things worse:
`lipo`-thinning the universal binaries or stripping `.lproj` locales rewrites those files,
which breaks the clone and makes each modified file start consuming real blocks. Thinning
the 381 MB Electron Framework would turn a ~0 MB copy into a ~190 MB one.

The only case where bundles cost real space is drift: after `Claude.app` updates, the old
blocks are retained for the copies until you rebuild. Rebuilding is what reclaims it.

### Is a VM bundle safe to delete?

A VM that has never booted is just a downloaded image. These five files appear only once
a VM has actually started:

```
machineIdentifier   vmIP   gvisorMacAddress   sessiondata.img   efivars.fd
```

If none of them exist in `<profile>/vm_bundles/claudevm.bundle`, that VM has never run and
the whole directory is safe to delete. If any exist, it holds real state — leave it.
Check `lsof +D` on the directory first, and never delete it while that instance is running.

`claude-recolor-icon.sh` uses `iconutil`, `sips` and `swift` (via
`claude-recolor-icon.swift`) — macOS system tooling only. It previously used
python3 + PIL + numpy, which broke twice: once when Homebrew moved to python
3.14, and once when an OS upgrade left miniconda without them. Both times icon
generation failed silently. Nothing in this setup now depends on Homebrew or pip;
it does need the Xcode command line tools (`xcode-select --install`).

Config and assets:

```
~/.config/claude-instances/instances.conf     instance definitions
~/.local/share/claude-instances/<name>.icns   icon masters (auto-generated)
```

Icon masters live outside the bundles so rebuilds keep them. Delete one and the next
build regenerates it from the configured hue. Regeneration is deterministic but not
byte-identical to a previous master; visually indistinguishable.

**A missing icon never blocks a rebuild.** If the master is gone and cannot be
regenerated, the build continues with the stock orange icon and warns loudly. Rebuilds
carry version and security fixes, so failing one over cosmetics is the wrong trade.

---

## Automated checks (`claude-stale-check.sh`)

A provided LaunchAgent template, if installed at
`~/Library/LaunchAgents/com.example.claude-stale-check.plist`, runs the watchdog
**at login and every 6 hours**, across **every instance in the config**. It only
reads and reports — it never rebuilds, because the builder refuses to touch a running app
and a background job that quits your windows mid-conversation is worse than a stale copy.

It catches silent version and identity failures:

| | What it means |
|---|---|
| **STALE** | `Claude.app` updated; the copy did not, and will run the old version indefinitely |
| **BROKEN** | missing app, changed bundle id, launcher, signature or environment — including an in-app update that replaces the launcher |

Results go to `~/Library/Logs/claude-instances/watchdog.log` as well as a notification, so the
check still works if notification banners are suppressed. Run it by hand any time:

```bash
claude-stale-check.sh                        # every instance
claude-stale-check.sh Enterprise             # just one
CLAUDE_STALE_CHECK_DELAY=0 claude-stale-check.sh   # skip the 45s settle delay
```

### After a macOS upgrade

An OS upgrade is the one event that hits everything at once. Expect, in this order:

1. **All instances signed out simultaneously** — including stock Claude. The upgrade
   invalidates stored credentials at the keychain level. Nothing about this setup causes
   it and nothing here can prevent it; just sign back in.
2. **Check which account each instance signed into.** If a forced re-login picks up a
   different web session, an instance can end up on the right profile with the wrong
   account. Fix it by signing out and in *inside* that instance — rebuilding won't.
3. **`Claude.app` will have updated**, so every copy is stale. Rebuild them.
4. **Profiles may balloon** — the Cowork VM sandbox (`vm_bundles`) is ~11 GB *per
   profile* and re-bootstraps. That is normal, not corruption.

```bash
claude-stale-check.sh          # what drifted
claude-apps-refresh.sh         # rebuild all (quit the copies first)
claude-apps-refresh.sh --verify
claude-instance-check.sh
```

---

## How the build works

For each instance, `claude-apps-refresh.sh`:

1. **`cp -Rc`** — APFS clonefile copy of `Claude.app`. Unchanged files share blocks.
2. **Patches `Info.plist`** — bundle id, display name, icon, executable.
3. **Installs the icon**, generating it from the hue if missing.
4. **Writes a launcher** pinning the profile and the CPU architecture.
5. **Re-signs** the main binary and outer bundle, ad-hoc.
6. **Publishes atomically** under a kernel lock, after syncing a private intention
   journal. A replacement retains the previous bundle for reviewed recovery.

### Why each choice — don't "simplify" these

| Choice | Reason |
|---|---|
| `cp -Rc` (clone) | ~2 MB instead of 823 MB per instance |
| Re-sign **only** main binary + outer bundle | The binary's signature seals `Info.plist`, which we edit, so it must be re-signed. Re-signing the 381 MB Electron Framework rewrites it and destroys the clone. Mixed signatures load fine because we sign **without** hardened runtime, so library validation (which demands matching Team IDs) is off |
| `CFBundleName` stays `"Claude"` | Electron uses it to locate the existing helper apps. It also influences the macOS Safe Storage service name: the first live Duo launch requested the existing `Claude Safe Storage` entry. Changing this field alone is not a validated fix. See the [blocked GUI finding](KEYCHAIN_FIRST_LAUNCH.md) |
| Delete `CFBundleIconName` | Points into `Assets.car` (Claude's own icon) and **outranks** `CFBundleIconFile`. Leave it and the orange icon comes back |
| Delete `CFBundleURLTypes` | Stops copies fighting over `claude://` and `msauth://` links |
| `arch -arm64` in the launcher | **Critical** — see below |
| No `--deep` on codesign | `--deep` follows symlinks and would re-sign `/Applications/Claude.app` in place |

---

## Traps

### 1. The Rosetta trap (the big one)

**Never `exec` Claude's binary directly from a shell script.** It is a universal binary
(x86_64 + arm64). A script-launched `exec` inherits the wrapper's architecture
preference, which resolves to **x86_64**, so Claude silently runs the Intel slice under
Rosetta on Apple Silicon. Symptoms: painfully slow typing, a renderer pegged above 100%
CPU.

Use `open -n -a` (LaunchServices picks the native slice) or `arch -arm64` explicitly.
The generated launcher does the latter.

**Spot it:** Activity Monitor → **Kind** column. `Apple` = native, `Intel` = Rosetta. Or:

```bash
lsappinfo list | grep -A1 "pid = <PID>" | grep -o 'Arch=[A-Za-z0-9_]*'
```

### 2. `open -n`, not `open -a`

Without `-n`, LaunchServices *activates* an already-running instance instead of starting
another. Matters for any manual launch.

### 3. A thin wrapper can't recolour the running Dock tile

The first attempt was a 20 KB wrapper `.app` launching stock Claude with
`--user-data-dir`. It works and costs nothing, but the *running* process belongs to
`Claude.app`, so the Dock showed the orange icon and the name "Claude". Only a standalone
copy with its own identity gets its own tile. That is why these copies exist.

### 4. codesign rejects symlinks that escape the bundle

The icon can't live outside `Contents/Resources` via a symlink —
`invalid destination for symbolic link in bundle`. Masters are *copied* in at build time.

---

## Recovery scenarios

### Login broken / signed out after a rebuild

Re-signing drops Anthropic's team-scoped entitlements (`keychain-access-groups` for
MSAL/Entra SSO and WebAuthn). Those carry Anthropic's Team ID and **cannot** be
reproduced under any other signing identity.

**Verified working on the author's setup** — enterprise SSO signs in fine. If login ever
breaks after an update, this is the first suspect. Expect a keychain prompt after a
rebuild (click **Allow**), and possibly a fresh sign-in.

Fall back to stock Claude while investigating; profiles are untouched.

### An app won't launch

```bash
codesign --verify --strict "/Applications/Claude Enterprise.app"   # should be silent
claude-apps-refresh.sh enterprise
```

Run the launcher from Terminal to see the real error:

```bash
"/Applications/Claude Enterprise.app/Contents/MacOS/launcher"
```

`Unable to find helper app` means `CFBundleName` is no longer `"Claude"`.

### Icon went orange again

`CFBundleIconName` came back — it outranks `CFBundleIconFile`. Rebuild. If the bundle is
right but Finder is stale, it's the icon cache: `killall Finder`.

### Profile checkpoints and missing conversations

Preserve the current data before changing paths or attempting repair. A newer
app may have migrated its databases; replacing just the bundle or one directory
does not prove safe restoration.

The optional [checkpoint tool](PROFILE_CHECKPOINTS.md) verifies both Desktop
and Code directories together with the apps/other users closed:

```bash
claude-profile-checkpoint.sh create Enterprise
claude-profile-checkpoint.sh verify Enterprise <printed-checkpoint-UUID>
```

It publishes private verified content copies, refuses unsupported metadata and
retains interrupted staging for inspection. Copies use full storage, may contain
sensitive credentials/history and are not encrypted by this tool. Keychain and
external projects are excluded. Repeated observations cannot block external
writers. There is no restore command or automatic downgrade; a compatible restore
into fresh paths still needs a separate implementation and validation.

### Removing an instance

```bash
rm -rf "/Applications/Claude Client.app"
# then delete its line from ~/.config/claude-instances/instances.conf
```

The profile survives; delete it separately if you actually want the data gone.

### Full teardown — back to one stock Claude

```bash
rm -rf "/Applications/Claude Personal.app" "/Applications/Claude Enterprise.app"
```

Profiles survive. Stock `Claude.app` picks up the personal profile automatically. Reach
another profile temporarily with:

```bash
open -n -a "/Applications/Claude.app" --args \
  --user-data-dir="$HOME/Library/Application Support/Claude-Enterprise"
```

### Rebuild everything from nothing

If the apps and icon masters are gone but `Claude.app`, the config, and the profiles
remain:

```bash
claude-apps-refresh.sh          # icons regenerate from the configured hues
claude-instance-check.sh
```

---

## Locations

| What | Where |
|---|---|
| Instance config | `~/.config/claude-instances/instances.conf` |
| Icon masters | `~/.local/share/claude-instances/<name>.icns` |
| Private app content records | `~/.local/share/claude-instances/integrity/<UUID>/record.txt` |
| Scripts and native helper sources | `~/bin/claude-*` |
| Verified content checkpoints | `~/Library/Application Support/Multi Claude Desktop/Checkpoints/<UUID>` |
| Legacy backups (unverified; not imported) | `~/Library/Application Support/Claude*.backup-*` |

`/Applications/Claude.app` is **never modified** — every script only reads it:

```bash
codesign --verify --strict -R '=anchor apple generic and certificate leaf[subject.OU] = "Q6L2SF6YDW" and identifier "com.anthropic.claudefordesktop"' /Applications/Claude.app
spctl --assess --type execute /Applications/Claude.app
```

---

## Credits

The wrapper approach, and the observation that truly distinct running Dock icons require
a full standalone copy, come from
<https://gist.github.com/aoxborrow/5a3a0ac0aa37819cc19439b70a552221>.
