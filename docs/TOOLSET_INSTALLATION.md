# Versioned maintenance tool installation

This installs our maintenance tools as a version-matched set. It does not build
or launch Claude, update the official app, migrate accounts, modify `~/bin`,
change the instance config, or install a LaunchAgent. The selected deployment
is the official **Claude** plus **Claude Duo**; graphical account validation and
the transition from existing Personal/Trabalho apps remain separate work.

## Package identity and trust

Start with a reviewed installer checkout. `claude-toolset.sh` uses its adjacent
reviewed common library, snapshots the standalone C installer privately and
compiles that snapshot with `/usr/bin/clang`. The compiled source SHA-256 is
bound into the helper and must match the package's installer source. The shell
fixes PATH and uses privileged-mode Bash. The compiler receives a minimal
environment and a private temporary directory, excluding caller include/SDK
overrides. macOS 12+ and Xcode Command Line Tools are required.

An incoming package contains exactly `manifest.txt` and `bin/`. The bin inventory
is exactly the 17 filenames declared in the native helper and developer builder.
The manifest is bounded to 4096 bytes and has this canonical form:

```text
MCD_TOOLSET 1
protocol 1
commit <40 lowercase hex digits>
file claude-apps-refresh.sh 0700 <64 lowercase hex digits>
...one entry per fixed filename, in the declared order...
```

The complete manifest SHA-256 is the release identifier. Supply that digest
from a separately reviewed release, not by trusting a checksum shipped beside
an arbitrary download. The commit field is descriptive metadata; it does not
prove the content came from GitHub or that the code is benign. There is no
download, archive extraction, manifest evaluation or trust-on-first-use mode.

`scripts/prepare-toolset.py` is a developer-only package builder. It requires a
new destination and emits the manifest digest. By default it refuses dirty or
untracked `bin/` contents before labeling a package with HEAD. An explicit
`--commit` is a declared identifier for exports/fixtures and is not independent
provenance verification. Python is not needed for installation or normal use.

## Private layout

The helper derives the real user's home from `getpwuid`, not HOME or a path
argument. Only a test compilation permits a synthetic home override.

```text
~/.local/share/claude-instances/toolsets/
  .toolset.lock
  dispatcher
  current
  releases/<manifest-SHA256>/
    manifest.txt
    bin/<complete toolset>
  .install-<32 random hex digits>/
    operation.txt           # after preparation
    current                 # candidate, or retained previous pointer
    dispatcher              # candidate, or retained previous dispatcher
    release/                # pre-publication or duplicate candidate
    check-*                 # local compilation preflights
```

Owned home ancestors must be physical, without group/other write permission or
custom allow ACLs. The tool root/release/operation directories are 0700. Records
and data sources are 0600; executable scripts and dispatcher are 0700. Files
must be regular, owned, single-link and free of flags and of extended attributes
other than the OS-managed `com.apple.provenance` (creator metadata, not content);
symlinks, special files, custom allow ACLs and volume crossings are refused.
Standard deny ACLs are allowed. These strict restrictions may reject a package
or location; the installer does not strip source security metadata to proceed.
Input files have their owner permissions normalized to the manifest's fixed
600/700 output modes. Each file is bounded to 16 MiB.

## Preparation and activation

1. Hold an exclusive nonblocking kernel lock on the persistent owned lock file.
   Concurrent installation/activation and readers during preparation refuse
   rather than competing. Do not unlink the lock to bypass it.
2. Validate the complete package and externally supplied manifest digest.
   Copy only the fixed inventory into exclusive private staging. Check metadata
   and named/open identity before/after reads, compare copy-pass hashes, sync
   files/directories and independently re-read the finished package.
3. Parse every staged Bash file with `/bin/bash -p -n`; compile all four C
   helpers, linking CoreFoundation only for the integrity helper. These checks
   execute no package shell code. Optional Swift icon generation remains a
   separate feature check when used. Snapshot/copy the running reviewed native
   installer as the dispatcher candidate and sync it.
4. Publish `releases/<SHA256>` exclusively on the tool volume. A previously
   installed identical release is reused only after complete verification;
   its files are never overwritten. Retain the prepared candidate on failure.
5. Write and sync the exclusive operation record before activation. It contains
   previous/requested release digests and previous/requested dispatcher hashes.
6. Publish the standalone protocol-1 dispatcher first, then switch `current`
   with `RENAME_EXCL` on first installation or `RENAME_SWAP` on replacement,
   always with `RENAME_NOFOLLOW_ANY`. Sync affected directories and verify the
   active package again. There is no delete/move fallback.

The dispatcher and the active payload serve different roles. Dispatchers in
this protocol must read both the old and new protocol-1 packages. Replacing a
dispatcher before changing the pointer therefore leaves the previous complete
release usable. An interrupted first install can leave a dispatcher with no
active release; it refuses `run`. Protocol/inventory changes require explicit
compatibility work before release; compatibility is not assumed for arbitrary
future bootstrap changes.

The **payload switch is one atomic namespace operation**. Dispatcher publication
is a preceding, separately atomic compatible-bootstrap operation; the entire
bootstrap/pointer sequence is not a single filesystem transaction. Equal
content observations and sync calls do not certify power-loss durability.

## Pinned execution and inspection

The installed dispatcher exposes:

```text
dispatcher status
dispatcher verify
dispatcher run claude-apps-refresh.sh --list
dispatcher activate <installed-release-SHA256>
dispatcher inspect .install-<32 hex digits>
```

`run` acquires a reader lock, reads one canonical pointer, verifies every file
of that release and executes a public entry by its absolute release path with
`/bin/bash -p`. It closes the tool lock before execution, allowing an installer
to switch releases while the existing entry keeps its version-matched library,
workers, C sources and header. Releases are retained and never edited/pruned by
the installer. The application-maintenance lock remains responsible for
coordination between rebuilds, cache cleanup and checkpoints.

Only refresh, cache cleanup, health check, checkpoint, icon recoloring and stale
check entries can be dispatched. Internal workers, the common library, the
installer wrapper and arbitrary paths are refused. Existing `~/bin` scripts do
not start using this dispatcher automatically; reviewed integration is pending.
Daily Claude icons continue using their generated standalone launchers.

`activate` verifies an installed retained release and switches tools only. It
does not downgrade Claude, restore profiles, rewrite login state or attest that
an older tool policy supports a newer app/profile. The dispatcher remains the
currently installed protocol-1 bootstrap.

`inspect` reads bounded canonical records and compares active/dispatcher content
to the recorded previous/requested hashes. It verifies the requested release
and any active release, reports `NO_ACTIVE_RELEASE`, `PREVIOUS_RELEASE_ACTIVE`
or `REQUESTED_RELEASE_ACTIVE`, and refuses unknown/superseded layouts. These are
current content observations, not proof of transaction history. It never replays,
swaps back, launches, promotes partial staging or deletes data. An operation
interrupted before a complete record exists cannot pass inspection. Failure
after pointer publication can leave the requested version active despite a
nonzero installer exit; inspect before interpreting failure as “unchanged”.

## Original + Claude Duo config

Select this policy explicitly, before any instance rows:

```text
@mode original-copy
Duo | com.example.claude-duo | ~/Library/Application Support/Claude-Duo | 212 | CLAUDE_CONFIG_DIR=~/.claude-duo
```

The parser accepts one or more managed copies and reserves both physical,
case-folded default Desktop/Code paths to the original, including cross-type
overlap and symlink aliases. Mode comes from config data; an inherited CI_MODE
cannot weaken it. Duplicate, unknown or late directives are refused. The mode
is represented in the generated launcher and content-record binding. Changing
policy requires a reviewed rebuild, even when other profile values match.

Headerless configs retain the legacy behavior; `@mode legacy-copies` declares it
explicitly. Installation does not add a directive or rename existing apps/data.
In particular, a legacy Personal clone using the default profile must not run
alongside the original. Preserve existing bundle IDs and data until reviewed
migration; choosing the name Duo does not itself migrate an existing account.

An optional `@apps user` header, also once before rows, selects only the fixed
physical `~/Applications` directory. It must already exist, be owned/private
(exact mode 0700, no special bits) and have safe ancestors/ACLs. Loading config
never creates or chmods it. `@apps system`, or no directive, keeps `/Applications`.
Config cannot supply arbitrary paths and inherited scope/path variables are
reset. The official source remains `/Applications/Claude.app`. User location is
bound into expected launcher bytes and the content-record config digest;
moving a system copy is insufficient to pass verification.

All mutation workers use the chosen directory and its kernel lock. Locks do
not span two different app directories using the same profiles; location
changes require reviewed transition and do not disable old launchers. This
opt-in supports the [GUI validation plan](GUI_VALIDATION.md) in the user's
existing session without placing the Duo candidate among shared system apps.
No additional macOS account is requested. The official app still uses its real
existing profile; the private app destination does not isolate credentials.
Retained releases predating `@apps` refuse that directive; activating an older
toolset does not migrate the private apps or make their configuration compatible.

## Validation and remaining limits

Tests use synthetic homes and package data, actual macOS rename/lock/file calls,
syntax checks and C compilation. They cover pinned in-flight execution during
upgrade, retained releases, compatible dispatcher replacement, source mutation,
malformed/linked/tampered records, unsafe files, syntax/compiler failures,
injected ENOSPC/partial journals, SIGKILL at five preparation/publication points,
first-install interruption, concurrency, unsupported atomic operations, and
post-activation failure inspection. A production compilation ignores test home,
stop and failure variables. The wrapper is also exercised with hostile PATH and
Bash startup settings. No Claude executable, real profile or credential is used.

The native binary and local manifests are owner-writable. Trust starts at the
reviewed installer and system compiler; an attacker able to rewrite those,
the dispatcher or releases as that user is outside this boundary. This is
neither notarization nor authenticated provenance against that owner. There is
no automatic pruning, replay, account migration, GUI manager, LaunchAgent
installation or automatic Claude-copy rebuilding. Retained staging/compilation
outputs consume disk space. Only synthetic installations have been exercised;
installed legacy tools/apps/profiles remain untouched.

The installer milestone at `0bee9cc` passed **137 offline tests**, including 25
native installer tests and six additional profile-policy cases; its historical
evidence is in [toolset-installation.json](evidence/toolset-installation.json).
The private-app-location follow-up passes **145 offline tests**, including eight
new path/policy/maintenance cases. A separate real Claude 2.42304.0 source check
passes eight scratch build/signature/content/policy checks at the private user
destination, with lifecycle observations simulated and no vendor execution.
Current tested source hashes, counts and limits are in
[gui-preparation.json](evidence/gui-preparation.json).

CI runs the same suite in three groups per architecture: profiles (61), storage
(59) and toolsets (25). Each group first verifies that their combined test IDs
match full discovery exactly once. Unassigned new modules and duplicates fail
the inventory check. This bounds each job without omitting cases; a serial
Intel attempt exceeded its ten-minute job limit after 75 completed tests with
no recorded test failure. The runtime sources were not changed for grouping.
