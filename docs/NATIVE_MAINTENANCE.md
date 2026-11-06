# Native observations and confined cache cleanup

Mutating workers enumerate this UID with `proc_listpids` and inspect executable
path, PID and start time using `proc_pidinfo`/`proc_pidpath`. They never request
argv/environment. A second path/identity observation detects observed PID reuse
or exec changes. Missing observer PID, partial visibility or uncertain metadata
returns unknown and refuses mutation. Confirmed gone/zombie processes are not
active writers. APIs are in Apple's [libproc interface](https://github.com/apple-oss-distributions/xnu/blob/main/libsyscall/wrappers/libproc/libproc.h).

A process whose executable was deleted has no path (`proc_pidpath` fails with
`ENOENT`). This happens after Claude updates itself while Chrome keeps the old
`chrome-native-host` alive. Such a process still makes the state unknown; the
helper names each one on stderr with its PID and kernel process name, so the
user knows what to quit.

A zombie (a child that exited and was never reaped, such as a helper Chrome
leaves behind) runs no executable. Newer macOS (seen on 27.0.1) answers both
`proc_pidinfo` and `proc_pidpath` for it with `ESRCH` while `kill(pid, 0)` still
succeeds, so the `SZOMB` check on `proc_pidinfo` is never reached. When either
call fails and the PID still exists, the helper asks `sysctl(KERN_PROC_PID)`;
a process of this user in `SZOMB` is skipped. Anything else still returns
unknown.

App-specific observation includes nested helpers under its `Contents/` path.
Global observation recognizes Claude Desktop main/helper binaries and launchers.
Rebuild/profile preparation, checkpoints and cache mutation require all observed
Desktop apps/helpers closed, including stock Claude. This is stricter than
rebuilding an idle profile while another Desktop account is active. Cache
cleanup can request that its selected app quit, without terminating all others.
Checkpoint open-handle observations still cover Code CLI/editors. The read-only
`claude-instance-check.sh` retains textual profile diagnostics; it does not
authorize destructive maintenance.

## Cache protocol

Internal `clean APPLICATIONS PROFILE` requires the inherited kernel lock checked
against the named lock's inode, owner, mode, link count and ACL. Standalone
uncoordinated cleanup is refused. The native helper independently restricts the
profile to a canonical managed UUID/desktop or existing flat Claude location
under the account's actual home. Test-home/process substitutions are compiled
only in fixture builds, not accepted by production.

Only `Cache`, `Code Cache`, `GPUCache`, `DawnWebGPUCache`, `DawnGraphiteCache` and
`Shared Dictionary` can be selected.

1. Open the profile through a no-follow descriptor. Preflight every existing
   cache slot without reading file contents. Refuse links, special files,
   hardlinks, mounts, flags, set-ID/sticky bits, foreign/writable entries, custom
   allow ACLs and excessive depth/count.
2. Recheck the named profile and native process state. Exclusively create a
   private `.claude-cache.<random>` directory inside the profile.
3. Recheck slot identities and quarantine using descriptor-relative
   `renameatx_np(RENAME_EXCL | RENAME_NOFOLLOW_ANY)`. There is no shell `rm -rf`
   on the live cache path, or weaker move/delete fallback.
4. Check moved inode, profile identity and process state. Walk/open children
   relative to pinned descriptors without following links. Recheck named/open
   identities and remove only those entries with `unlinkat`.
5. Remove empty slots/quarantine and sync parent metadata on success. Local
   Storage and every other unlisted directory are never selected.

A replacement symlink on the live cache path cannot redirect quarantined
deletion. A replaced profile or linked quarantined child causes refusal. Errors,
unknown state or SIGKILL may leave partly cleaned quarantine. There is no
automatic replay, restore or recursive pruning, and failed cleanup does not
relaunch the selected app.

## Limits and tests

Observations are not lifecycle enforcement. The kernel lock coordinates these
tools only. External/same-user programs can start or move/write files after a
check; administrators/root are outside this boundary. Descriptor operations
reduce redirection hazards, but cannot defeat an adversary able to move/change
already-open directories. Application consistency, restore/downgrade safety and
power-loss durability are not established by this protocol.

Tests use real rename/unlink/lock calls on invented trees, including profile/link
replacement and SIGKILL after quarantine. Process policy in filesystem fixtures
is explicitly simulated. Another test uses the production observer and a tiny
compiled fixture process to check actual PID/path matching (including spaces)
and exit, without executing Claude or reading arguments/environment. Stop hooks
are enabled only in test compilation.
