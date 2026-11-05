# Source authenticity and local content records

The builder checks the original Developer ID/identifier on all architectures,
verifies nested code/resources separately, requires Gatekeeper acceptance and
checks the staged source before changes. `--deep` is used for verification,
never signing, as distinguished in Apple's [code signing tasks](https://developer.apple.com/library/archive/documentation/Security/Conceptual/CodeSigningGuide/Procedures/Procedures.html).

The native integrity helper compares original and generated trees. Differences
are permitted only at these exact paths:

- `Contents/Info.plist`: a separate semantic check permits only the configured
  ID/display name, launcher executable, removed URL/activity keys and optional
  icon keys. Every other dictionary value must equal the original.
- `Contents/MacOS/Claude` (system signer's output) and `Contents/MacOS/launcher`
  (also compared byte for byte to the complete generated launcher).
- `Contents/_CodeSignature/CodeResources`, `CodeDirectory`, `CodeRequirements`
  and `CodeSignature`. The last three can be created by the system signer for
  a script main executable; this is not a directory wildcard exception.
- `Contents/Resources/mcd-build-id` and, only if a custom icon was applied,
  `Contents/Resources/appicon.icns`.
- Only when the copy gets its own product name and the source framework carries
  a used integrity digest slot (Electron 44.9, Claude 4.29): the
  `Electron Framework` binary, each `Contents/Frameworks/<H>.app/Contents/MacOS/<H>`
  helper executable, and their `_CodeSignature` files. Electron hashes the whole
  `ElectronAsarIntegrity` dictionary into a `__DATA_CONST,__asar_integrity` slot
  of the framework and refuses an `Info.plist` that does not match it, so the
  copy's new `app.asar` hash needs a new digest there. The framework and its
  library-validated helpers are then re-signed ad hoc (never `--deep`). The
  record requires each of these thin 64-bit Mach-O files to equal the source
  byte for byte except the trailing signature, the two load-command size fields
  that describe it and, in the framework, the 32 digest bytes, which must be the
  digest of the planned `Info.plist` dictionary. An unused slot leaves all of
  them byte-identical.

The unchanged comparison normalizes group/other permission bits because umask
can restrict them while copying. Owner bits must match. The full generated hash
records actual mode bits and detects subsequent permission changes. The pipeline
trusts the installed system signer for main executable/signature changes; it is
not an independent proof of instruction-level equivalence and cannot restore
Anthropic's signing identity or permissions.

## Record and publication

The app's UUID marker selects
`~/.local/share/claude-instances/integrity/<UUID>/record.txt`. The directory is
0700 and the single-link regular record 0600. Its fixed grammar stores config,
source tree, unchanged resource and generated full tree digests. No credentials,
profile contents or profile paths are stored. The config binding covers instance
name/ID, canonical Desktop/Code paths and hue. The marker is itself hashed.

The immutable record is created exclusively and synced before app publication.
The published app therefore references an existing record. A failed publication
may leave an orphan record; no record is overwritten, promoted or auto-pruned.
Previous bundles retain their own markers/records. Verification works without
the old source still installed. Missing records or changed config require a
rebuild; there is no trust-on-first-use fallback.

The walker hashes sorted byte names, node types, every regular file's bytes,
mode bits, resource forks and `com.apple.cs.*` attributes. Framework symlinks are
recorded as link text, not traversed, and must resolve inside the bundle.
Escaping/absolute/dangling/cyclic links, special files, hardlinks, foreign-owned/
writable entries and mounts fail closed. Named/open descriptor identities and
metadata are checked around reads. Quarantine, provenance, last-used dates and
other non-code attributes are outside the digest; policy metadata is never
stripped to bypass a failure. Custom allow ACLs are rejected.

Build, `--verify`, watchdog and checkpoint prerequisites require the full record
check plus the expected launcher and recursive signature check. An inert added
JS resource followed by local re-signing must fail the record check even if the
new local signature passes. Entry scripts/workers fix PATH before resolving
tools and use privileged-mode Bash to ignore inherited startup files/functions.

If an entry's metadata moves while the tree is observed (for example macOS
adding `com.apple.macl` to a bundle moments after it is published), the helper
exits 75 instead of 1. Callers then repeat the **whole** check, up to three
times, two seconds apart. Content or binding mismatches exit 1 and are never
repeated, so a real change still fails on every attempt.

## Limits

The record detects drift while it remains trusted. Its owner can rewrite both
app and record: it is not an authenticated manifest or same-user security
boundary. The daily launcher does not perform a full tree check. Checks are not
continuous runtime enforcement; external writers/updaters can race observations.
Apps remain ad-hoc signed, and GUI/SSO compatibility and account separation still
require separate validation.

`--verify` and watchdog now also require macOS 12+ and Xcode Command Line Tools
to compile the verifier privately. Daily graphical launching does not compile.
Install the complete version-matched `bin/claude-*` set, including all C sources
and `claude-maintenance.h`. Atomic installation of that set remains future work.
