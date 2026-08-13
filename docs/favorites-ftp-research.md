# Favorites FTP research

This document records transport, security, and storage evidence for the
Milestone 22.6 read-only Favorites FTP backend. It does not define or authorize
scanner-side mutation.

## Scope

The first FTP slice adds a renderer-neutral read-only Favorites storage source
that produces the existing `FavoritesStorageSnapshot` representation. The
transport preserves exact catalog and HPD bytes and does not introduce
FTP-specific semantics into workspace, hierarchy, comparison, editing, or
write-plan layers.

FTP access is intended only for trusted local networks or trusted VPNs. Plain
FTP is not treated as a transport-secure remote-access boundary.

## Credential separation

The SDS200 may be configured with separate read-only and writable FTP accounts.
That separation is part of the storage-safety model.

For the read-only FTP source:

- only an explicitly configured read credential may be resolved;
- a missing or rejected read credential fails closed;
- a writable credential is not accepted by the read configuration and is never
  tried as a fallback;
- password values are referenced by environment-variable name rather than
  stored in ordinary Favorites configuration objects; and
- passwords do not appear in exceptions, logs, diagnostics, snapshots, reports,
  or public API values.

Writable credential resolution belongs only inside a later explicit write
workflow after backup, staging, validation, target-identity, conflict, and
operator-intent checks have succeeded.

## Read-only transport contract

A read-only FTP backend:

1. connects to one explicitly configured scanner host and bounded FTP endpoint;
2. authenticates with the read-only credential only;
3. enters one explicitly configured Favorites directory whose path rejects
   traversal and command-control characters;
4. obtains a bounded deterministic immediate listing;
5. requires one listed `f_list.cfg`;
6. validates every listed name as one immediate non-command-injecting child;
7. retrieves `f_list.cfg` and every immediate `.hpd` candidate as exact bytes;
8. enforces per-file and complete-snapshot byte limits;
9. repeats the complete listing and retrieval pass and requires exact snapshot
   equality; and
10. closes the FTP session deterministically on success or failure.

The backend feeds the existing storage projection unchanged after snapshot
capture. FTP transport failures are not reinterpreted as Favorites schema or
workspace diagnostics.

## Remote-change evidence

FTP does not provide the inode, mount, or filesystem identity available to the
copied-tree and Linux USB backends. Milestone 22.6 therefore does not invent
local-filesystem identity for remote files.

The initial stability rule is intentionally conservative: two consecutive
complete snapshots must have the same safe listing and exact catalog/document
bytes. A mismatch is a stale/changing remote observation and is refused.

Later physical research may establish useful scanner FTP metadata, but a future
write executor must define its own stronger stale-target and concurrency
contract before any mutation.

## Fake transport boundary

Normal automated tests do not require a physical scanner or a network FTP
server. The production adapter sits behind a narrow fixture-friendly session
protocol that can model authentication, listing, exact binary retrieval,
transport interruption, changing remote content, limits, and deterministic
cleanup.

Tests must also verify that the production adapter uses only connect, login,
`cwd`, `nlst`, `RETR`, `quit`, and local close behavior. No upload, delete,
rename, directory creation, permission change, or other mutating FTP command is
part of this milestone.

## Privacy and diagnostics

Favorites programming content is private scanner data. Synthetic fixtures
remain the normal automated-test substrate. Stable failure classes are exposed
without embedding passwords or arbitrary FTP server response text.

## Physical validation

After synthetic and fake-transport coverage is clean, guarded SDS200 validation
remains read-only. It should establish the observed scanner Favorites FTP
directory and listing shape, verify repeatable exact snapshot retrieval with the
read-only account, and verify an unavailable or invalid read secret fails
without writable-credential fallback or Favorites mutation.

No write credential is required for Milestone 22.6 validation.

## Deferred write boundary

A later FTP write slice must preserve the project-wide mandatory
backup-before-write rule. It must define remote target identity, exclusivity or
conflict detection, complete verified host backup, host staging, exact intended
readback, activation semantics, rollback/recovery, durable reporting, and
explicit writable-secret resolution without silent last-writer-wins behavior.
