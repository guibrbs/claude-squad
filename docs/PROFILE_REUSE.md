# Reusing the original's configuration in Claude Duo

Requirement added on 10/17/2026: the same person uses two accounts and wants to
reuse their preferences, MCPs, skills and plugins without setting everything up
by hand. This document started as an **implementation proposal**. On 10/17/2026
the user chose a simpler path, described right below and implemented; the full
selective importer remains a proposal.

## Implemented: sharing through links (the user's decision)

[`scripts/share-code-profile.py`](../scripts/share-code-profile.py) makes the
Duo's Code (`~/.claude-duo`) use the **same files** as the original (`~/.claude`).
`--account Trio` does the same for another managed copy, with the Code folder
and app of its line in the installed config; **Update Claude Squad** refreshes
every copy that has a `.shared-from-original.json`:

- Links: `CLAUDE.md`, `skills`, `output-styles`, `tools`, `plugins` and each
  `projects/<project>/memory`. A memory saved by one app shows up in the other.
- `settings.json` is copied **once**. Keys that select a different account or
  provider are left out (`apiKeyHelper`, `forceLogin*`, credential helpers and
  `env` with `ANTHROPIC_*`, `AWS_*`, `CLAUDE_CODE_USE_*` etc.); only the names
  of those keys appear in the output. The Duo's `extraKnownMarketplaces` is
  merged. After that, changes made in the Duo are preserved.
- It never reads or writes `.claude.json`, sessions, history, transcripts
  (`projects/*/*.jsonl`), caches, policies or account state: only the list above.
  The one exception is opt-in, below.
- **MCP servers (`--mcp-servers`, added on 10/20/2026):** copies the user-scope
  servers (`mcpServers` in the original's `~/.claude.json`) into the Duo's
  `~/.claude-duo/.claude.json`, one name at a time. Only `mcpServers` changes
  in the Duo's file; login, trust decisions and the rest stay as they are, and
  the original's file is only read. A name the Duo already has is kept and
  reported (`kept mcp`); a server deleted in the original stays in the Duo. The
  choice is recorded in `.shared-from-original.json`, so later runs, including
  the one after every Duo update, copy servers that are new to the original.
  The output shows names and transport only. Servers are copied **as they
  are**: command, arguments, `env` and headers, credentials included. Both apps
  then reach the same external systems with the same credentials, which goes
  against the "new connection by default" row below; it is the user's choice
  for their own servers. Project-scope servers (`.mcp.json` in a repository)
  and servers that plugins bring already reach both apps.
- The default is a preview with no changes. `--apply` requires the Duo to be
  closed. An item that already exists in the Duo is refused, or moved (never
  deleted) to `.share-backup-<date>` with `--backup-existing`. Links that point
  elsewhere, sources that are already links and folders writable by others are
  refused. Running it again is idempotent and links memories of the original's
  new projects; memory that exists only in the Duo is kept and reported, with
  no mixing.

Caveats of this choice, which goes against the original proposal of separate
copies:

- Plugins, hooks and skills are executable code and now take effect in both
  apps. Install and remove plugins from the original; an installation done in
  the Duo writes `~/.claude-duo` paths into the shared inventory.
- Both accounts read and write the same memories. Do not store information in
  them that one of the accounts should not see.
- Checkpoints of the Duo's Code profile are now refused, because the checkpoint
  helper does not follow links. This is intentional.
- An interrupted `--apply` may leave only some of the links. Running it again
  completes the missing ones; nothing in the original is changed.
- The script runs from the reviewed checkout; it is not part of the versioned
  toolset.

## Intended behavior

The original is the initial reference. The Duo offers to import the compatible
items and reports what depends on reconnection or review. Afterward, detect
changes and update only previously chosen items, keeping changes exclusive to
the Duo. A change to executable code or to access requires a new decision; an
earlier choice does not approve any future plugin or command.

The two accounts keep their logins, sessions and histories separate. Account B's
name, email, plan, organization and features come from the service after it
signs in. Do not replace this data with account A's to reproduce the profile's
appearance. Personal instructions and preferences can be reused selectively.

This distinction is necessary: Anthropic's documentation states that
`~/.claude.json` holds both MCPs and preferences and the login session and trust
decisions. `CLAUDE_CONFIG_DIR` also redirects data that includes sessions and
plugins. That is why this file must not be copied whole and the configuration
folder must not be shared through links. [Settings files and precedence](https://code.claude.com/docs/en/settings).

## Handling by category

| Category | Proposed handling |
|---|---|
| Interface and behavior preferences | Import only documented keys with known format and semantics; unknown keys stay pending |
| Personal instructions, skills and commands | Select content and dependencies, keep separate copies and check for changes; they may contain private information or executable instructions |
| MCP definitions | Reuse reviewed names, transport and configuration; separate credentials and check commands, arguments, endpoints and paths |
| Plugins and marketplaces | Reproduce the selection by exact source and revision/version; verify package and dependencies; installation/activation is an explicit step |
| Hooks, permissions, additional directories and variables | Specific review: they can run commands, widen access or select another provider/account; do not inherit automatically |
| Service-linked connectors and extensions | Reconnect in account B when required; reusing a local declaration does not transfer authorization or license |
| MCPs with password, token, header or encrypted configuration | New connection in the Duo by default; future reuse of a third-party secret requires a specific choice, without transferring the Claude login |
| Configuration of projects already opened by both | Inventory what both already load from the project; avoid duplicating definitions and preserve each profile's trust decisions |
| Sessions, history, cookies, OAuth, Keychain, cache and account state | Exclude from import; use the official login flows |
| Organization-managed policy | Do not copy it to work around the other account's policy; respect the effective configuration and record incompatibilities |

In the current documentation, local Code can also receive MCPs from the Desktop
configuration, in addition to the Code/project files. This makes checking only
`settings.json` insufficient: the trial must confirm the effective source and
precedence in each app. Plugins can include hooks and MCP servers; copying their
cache folder does not demonstrate a valid installation. [Desktop: settings, MCPs and plugins](https://code.claude.com/docs/en/desktop).

The initial contract will be separate copies per profile. Do not point the Duo
at all of `~/.claude`, `~/.claude.json` or the original's Desktop profile.
Sharing executables installed on the system is different from sharing mutable
authentication, cache or configuration data. An MCP endpoint can give access to
the same external data in both apps; separating the Claude login does not
separate that data automatically.

## Implementation flow

1. **Local inventory and preview with no changes.** Define adapters for the
   tested Desktop/Code versions. Read only selected sources, with size/depth
   limits, without following links, special files or external paths. Reject
   ambiguous JSON, including duplicate keys. Diagnostics show categories,
   actions and conflicts; never secret values.
2. **Per-item plan.** Validate keys against an allowlist and schema, preserving
   authentication exclusions in all structures. Do not try to identify every
   secret by key name or by regular expressions. `command`, `args`, URLs and
   free text can also contain secrets. Commands/hooks/plugins are not run during
   inventory and preview.
3. **Controlled reproduction.** Define separately copying declarative data,
   installing a verified extension and activating code. Plugin references must
   bind source, version/revision and content; do not replace an unavailable
   revision with the latest version. Duplicate MCPs or incompatible sources need
   visible resolution, preserving the destination's configuration.
4. **Applying with the apps closed.** Use the existing locking and process
   observation; revalidate hashes and identity of source/destination. Prepare in
   private staging and publish the change without partial overwrites. For
   multiple files, define a recovery protocol before promising atomicity of the
   set. Do not edit the original's files or restore its authentication state
   over B. A failure leaves an inspectable operation.
5. **One-way updates.** Store hashes of the last imported content and compare
   the original, the imported version and the Duo. A change only in the original
   can update a previously approved item; a change in both creates a conflict.
   Deleting something in the original does not automatically delete the Duo's
   customization. A change in the Duo does not flow back to the original. Do not
   sync during active sessions or mix this process with updating the
   application bundle.
6. **Validation.** First use made-up inert files and extensions in the
   [lab](GUI_VALIDATION.md). Then validate the selected real items, without
   sending the private inventory to GitHub. Login and reconnections remain the
   user's job in the official interface.

The tool installer has a fixed inventory and protocol. Implementing a new import
component requires reviewing packaging, compatibility, updating and recovery;
do not distribute a standalone script outside the set.

## Additional acceptance criteria

- Account A remains active while importing, using or removing an extension in B.
  Confirm the effective account in Code; identical preferences do not prove it.
- The source remains unchanged and the Duo loads only the chosen items.
  Fake Claude credentials, MCP tokens and URLs with secrets used in fixtures do
  not appear in the output and are not transferred as preferences.
- Configurations with `env`, headers, a credential helper, provider, hooks,
  broad permissions, external paths or an unknown schema are identified and
  handled by the specific rule; they do not go through a generic copy.
- Files changed after the preview, links/hardlinks, unsafe metadata,
  concurrency, lack of space and interruptions do not trigger a silent partial
  import. Recovery is rehearsed before offering automatic application.
- An item customized in the Duo survives synchronization; conflicts stay
  pending. A change of extension version or revision reopens the required
  validation.
- Third-party reconnection, SSO and organization limitations are shown as real
  pending items; the system does not promise to copy authorizations that the
  service ties to the account.

First proposed delivery: inventory and preview with fixtures, with no profile
writes. Applying and syncing depend on this contract and on proof of independent
authentication in the lab. The preference for GUI use and the security priority
remain; there is no need to create a new native manager to start this
validation.
