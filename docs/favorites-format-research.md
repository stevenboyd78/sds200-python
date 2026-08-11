# Favorites format research

This document records format evidence used to design the Milestone 21 Favorites
Workspace. It is intentionally read-only research. It does not define a scanner
write path.

## Research inputs

The current evidence set consists of:

- Uniden SDS100/200 File Specification v1.08, dated 2025-10-16; and
- a copied `BCDx36HP` storage tree from an SDS200 running firmware 1.26.01.

The scanner copy is private research input. Raw user programming data must not be
committed as repository fixtures. Automated tests should use sanitized synthetic
records that retain the structural characteristics documented here.

## Format-level observations

Favorites programming is not represented by one standalone hierarchy file.

`favorites_lists/f_list.cfg` maps Favorites List display names and settings to
individual `.hpd` filenames. Each `.hpd` file then carries the programming
records for one Favorites List.

The observed files use the expected `BCDx36HP` target and report
`FormatVersion 1.00` even though the reviewed specification revision is v1.08.
Specification revision and on-disk format version must therefore be treated as
different concepts.

The files are line-oriented positional records. Fields are tab-separated and the
copied scanner data uses CRLF line endings. Empty positions are meaningful.
Favorites `.hpd` hierarchy cannot rely on `MyId` and `ParentId`: the positions
were blank throughout the reviewed Favorites files, so hierarchy is substantially
encoded by ordered record context.

Milestone 21 parsing must preserve:

- complete source record order;
- command spelling;
- every positional field;
- blank positions;
- duplicate values and names;
- trailing empty fields;
- unknown commands;
- undocumented additional fields; and
- sufficient raw representation for a future byte-aware round-trip layer.

Typed models must not silently repair or normalize source data.

## Copied scanner inventory

The reviewed `favorites_lists` directory contained:

- one `f_list.cfg`;
- fourteen mapped Favorites `.hpd` files; and
- no missing or orphaned Favorites `.hpd` file within that mapping.

The fourteen `.hpd` files contained 20,721 total records.

Observed record counts were:

| Record | Count |
| --- | ---: |
| `TargetModel` | 14 |
| `FormatVersion` | 14 |
| `Conventional` | 42 |
| `Trunk` | 19 |
| `DQKs_Status` | 61 |
| `C-Group` | 212 |
| `C-Freq` | 2,517 |
| `T-Group` | 452 |
| `TGID` | 9,772 |
| `Site` | 799 |
| `T-Freq` | 5,443 |
| `BandPlan_Mot` | 302 |
| `BandPlan_P25` | 13 |
| `UnitIds` | 1,060 |
| `UnitID` | 1 |

That gives 61 observed systems: 42 conventional and 19 trunked.

## Specification-versus-device discrepancies

Real scanner output contains structures that must not be rejected merely because
they exceed the reviewed field tables.

### `T-Freq`

Observed field counts:

- 3,879 records with eight tab-separated fields including the command; and
- 1,564 records with nine fields.

The ninth position in those extended records was observed as `Any`.

The parser must preserve the additional position even if the typed projection
does not yet assign semantics to it.

### `BandPlan_P25`

Observed field counts:

- nine records with 34 fields; and
- four records with 50 fields.

The longer records carry positions beyond the documented Base/Spacing range.
Those positions must be preserved losslessly until their semantics are supported
by evidence.

### `TGID`

Observed field counts:

- 9,771 records with 17 fields; and
- one record with 18 fields.

The extended record demonstrates that strict documented field-count rejection
would make real scanner data unreadable.

### `UnitID`

One bare `UnitID` marker record was observed immediately before `UnitIds`
records. The reviewed specification documents `UnitIds`; the singular marker
must therefore be accepted as observed-but-not-yet-semantically-defined data.

## Hierarchy implications

The typed hierarchy should distinguish conventional and trunked structures.

At minimum, the projection needs concepts corresponding to:

- Favorites List;
- conventional system;
- trunked system;
- conventional department/group;
- trunked department/group;
- conventional frequency/channel;
- talkgroup channel;
- trunked site;
- site frequency;
- Motorola band-plan data;
- P25 band-plan data;
- unit-ID records; and
- other preserved source records that are not yet modeled semantically.

Record ownership and hierarchy should be inferred from documented and observed
ordering rules while retaining the source records from which that hierarchy was
derived.

## Milestone 21.1 boundary

Milestone 21.1 should provide:

1. immutable lossless line/record representation;
2. parsing of copied `f_list.cfg` and `.hpd` bytes;
3. preservation of CRLF/field structure needed by later round-trip work;
4. tolerant handling of unknown commands and extra positional fields;
5. a renderer-neutral read-only hierarchy projection; and
6. sanitized synthetic fixtures covering both documented and observed-extension
   shapes.

Milestone 21.1 should not provide:

- FTP access;
- USB mass-storage discovery;
- direct live scanner-volume access;
- scanner-storage writes;
- automatic repair or normalization;
- Favorites search/filter UI;
- comparison UI;
- RadioReference synchronization;
- `GLT` or `FQK` control behavior; or
- renderer-specific Favorites presentation.

## Later storage boundary

A later read-only Milestone 21 slice may add local copied-image and FTP storage
backends behind one read interface.

FTP must preserve privilege separation between independently configurable
read-only and writable accounts. The Milestone 21 read path must never resolve or
fall back to the write credential.

Writable storage capability belongs to Milestone 22 and remains subject to the
mandatory backup-before-write, staging, readback, verification, conflict, and
rollback requirements in the project vision.
