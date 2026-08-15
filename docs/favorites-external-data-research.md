# External Favorites data research

This document records the source-neutral data, provenance, security, and
synchronization constraints for Milestone 23.1. It does not authorize live
provider access, automatic synchronization, or scanner-storage mutation.

## Scope

Milestone 23.1 starts the external-data phase by defining a renderer-neutral
foundation for previewable external Favorites updates. The local lossless
Favorites representation remains authoritative for scanner-compatible source
bytes and unknown material.

The first slice should make it possible to represent what an external provider
claims, where that claim came from, which local fields are externally or locally
owned, and what would change if an operator later accepts an import proposal.
It must not make acceptance implicit.

## Source-neutral boundary

Provider transport and provider-specific schema belong behind a narrow adapter
boundary. The renderer-neutral layer should consume immutable normalized
external observations rather than HTTP responses, HTML, provider SDK objects,
or credential-bearing client state.

External observations should carry enough stable evidence to distinguish:

- provider/source identity;
- provider record identity;
- observed provider revision or equivalent update evidence when available;
- observation time when useful;
- normalized candidate values;
- field-level provenance and ownership; and
- explicit absence versus an unknown or unprovided value.

Provider identities must not be reused as SDS Favorites filenames, record
indexes, scanner identifiers, or source-line provenance.

## Local authority and field ownership

Local Favorites records preserve scanner-compatible exact source material.
Unknown commands, positional extensions, blank fields, ordering, and physical
line endings must remain protected by the existing lossless model.

External import logic should therefore distinguish at least:

- externally owned values eligible for provider-assisted refresh;
- locally owned values that an external refresh must not silently overwrite;
- local-only annotations or material with no provider counterpart; and
- detached values/records that retain their current local value but no longer
  participate in provider-assisted updates.

Ownership changes must be explicit. Importing one provider value must not imply
that every field in the containing scanner record becomes externally owned.

## Preview and conflicts

An import/update proposal is evidence, not a write operation. Its change kinds
describe the relationship between normalized external observations and captured
local provenance; they do not claim that a provider value is already mapped to a
supported SDS Favorites field or that a proposed external record can already be
created on scanner storage.

A deterministic preview should be able to identify:

- proposed external additions;
- proposed external replacements;
- proposed removals when the provider explicitly supports that meaning;
- unchanged externally owned values;
- local-only values;
- ownership conflicts;
- explicit provider absence versus an unprovided field; and
- the provider revision/update evidence attached to the observation.

The preview carries exact `FavoritesRecordTarget` provenance for linked local
records. Snapshot-bound stale/ambiguity revalidation remains an acceptance-time
safety check through the existing Favorites editing/planning boundary rather
than being inferred from external observations alone.

Provider-specific mapping into supported SDS Favorites fields, record templates,
and scanner-schema representability also remains a later adapter/acceptance
concern. This source-neutral foundation must not interpret an external field name
or provider identifier as proof that a scanner mutation is safe.

No proposal should silently apply last-writer-wins behavior. Acceptance belongs
to a later explicit editing/planning step using the existing Favorites services.

## Milestone 23.9 provenance binding boundary

Milestone 23.9 begins the assisted-import bridge by making initial local
provenance binding explicit and pure. One existing exact `FavoritesRecordTarget`
may be linked to one active external observation only through caller-supplied
normalized field names, exact local source-field indexes, and explicit ownership.

The binding layer does not infer SDS mappings from provider field names. An
externally owned field can be established only when the selected provider field
contains an observed value that exactly matches the current local source value;
binding therefore records already-accepted provenance without rewriting local
Favorites bytes. A locally owned binding remains local and carries no field-level
external provenance, so later provider differences are surfaced by the existing
conflict preview rules rather than silently accepted.

Initial detached ownership is not a binding mode. Detach remains an explicit
later transition through the existing source-neutral detach operations.

This slice does not define provider-to-SDS template mapping, scanner record
creation, hierarchy placement, operator acceptance, persisted provenance
serialization, or storage writes. Those remain separate acceptance-layer work.

## Milestone 23.10 name acceptance planning boundary

Milestone 23.10 adds the first explicit operator-acceptance planning seam, but
only for a normalized `name` field that is already bound to an exact existing
Favorites source field with external ownership. The planner recomputes preview
classification from the linked record state and supplied observation, then
requires an active externally owned `REPLACED` name value.

Acceptance does not introduce a generic source-field editor. The existing
schema-aware `rename_favorites_record()` operation remains authoritative for
scanner representation and stale/ambiguous-target revalidation. After deriving
the intended snapshot, the acceptance planner verifies that the exact bound
provenance index is the only source field changed by that editor. A caller cannot
therefore use a provider field name or a mistaken provenance index to authorize
an unrelated SDS mutation.

The resulting intended snapshot flows through ordinary `plan_favorites_write()`
validation and blockers. The planner also returns refreshed in-memory provenance
for the intended renamed record so a later layer can preserve accepted external
evidence without treating persistence as already implemented.

Unbound provider fields remain preview evidence only. In particular, a
RadioReference whole-Hz `frequency` observation that has no reviewed SDS binding
is not accepted merely because another field on the same provider record is
accepted. A simultaneous replacement or removal of any other already-bound
field also fails closed rather than being silently folded into a name-only
acceptance. Local or detached fields, provider removals, unresolved conflicts,
record creation, arbitrary-field replacement, template/hierarchy inference,
persisted provenance serialization, storage execution, live transport, MyRR, and
automatic synchronization remain deferred.

## Milestone 23.11 name acceptance execution completion boundary

Milestone 23.11 composes the already-pure Milestone 23.10 name-acceptance plan
with existing write execution without making the external-data layer a new
storage backend. The completion seam accepts an immutable
`FavoritesExternalNameAcceptancePlan`, passes only its ordinary
`FavoritesWritePlan` to an injected executor, and treats the executor's return
value as opaque backend-specific success evidence.

A successful executor return is necessary but not sufficient to advance
external provenance. The completion layer must read the target again through an
injected `FavoritesStorageSource` and require exact equality with the write
plan's `intended_snapshot`. Only after that exact readback succeeds may the
existing planned `intended_state` be returned as accepted in-memory provenance.
The completion layer does not independently rewrite, normalize, or infer any
field.

Executor failures propagate without a post-write provenance claim. Readback
failure, malformed storage evidence, or any exact snapshot mismatch fails closed
even if the underlying executor returned successfully. This rule prevents
backend-specific success signaling from being mistaken for proof that the
planned scanner-compatible bytes are currently active.

The copied-tree and USB executors retain their existing target qualification,
stale-baseline checks, locking, backup/staging, activation, verification,
recovery, and durable operation evidence. Milestone 23.11 does not wrap those
details in a new generic storage abstraction and does not add a production
executor call site. Provenance remains in memory: serialization, durable
provenance storage, restart recovery of external links, arbitrary-field
acceptance, provider-to-SDS mapping, record creation/removal acceptance,
renderer workflows, live RadioReference transport, MyRR, and automatic
synchronization remain deferred.

## Milestone 23.12 provenance serialization and rebinding boundary

Milestone 23.12 introduces a durable *representation* without yet introducing a
durable filesystem owner. The source-neutral format serializes already-linked or
detached `FavoritesExternalRecordState` values as bounded, canonical UTF-8 JSON.
The document has an explicit schema/version and retains only normalized external
identity/evidence, field ownership/index provenance, detach state, and a compact
local target locator.

The serialized local locator is intentionally not a copied
`FavoritesRecordTarget`. Raw Favorites record bytes and complete storage
snapshots remain outside the provenance document. Instead, the locator records
the source kind, exact source index, HPD document index/filename when applicable,
and a lowercase SHA-256 digest of the exact source record bytes that were bound.
This is stale-target evidence, not a provider identity and not permission to
mutate storage.

Restoration requires a fresh caller-supplied `FavoritesStorageSnapshot`. The
decoder reselects the target through the existing editing boundary and requires
the selected source kind, indexes, filename provenance, and exact record digest
to match before rebuilding `FavoritesExternalRecordState`. Missing, ambiguous,
moved, renamed, or changed records fail closed. No heuristic search by provider
ID, local name, frequency, or neighboring record is permitted during rebinding.

The codec follows the repository's established durable-record discipline:
bounded encoded bytes, record count, and per-record field count; strict UTF-8 and
JSON; duplicate-key rejection; exact key/type and schema-version checks;
canonical JSON reserialization; timezone-aware observation evidence; and
deterministic errors that do not echo malformed provider-controlled payloads.
Provider credentials and session data have no fields in this format; the existing
rule that secrets never enter exportable provenance remains unchanged.

This milestone deliberately stops before choosing or writing a host-state file.
XDG state-path integration, atomic/durable publication, permissions, locking,
cleanup/migration policy, automatic restart loading, and lifecycle ownership are
separate persistence-layer work. Arbitrary-field acceptance, provider-to-SDS
mapping, record creation/removal, renderer workflows, live RadioReference
transport, MyRR, and automatic synchronization also remain deferred.

## Milestone 23.13 filesystem durability and explicit loading boundary

Milestone 23.13 gives the existing canonical provenance document one explicit
host-side owner without changing its schema. `ConfigurationPaths` exposes a
deterministic file below the existing XDG state directory, while path resolution
remains read-only. Save/load functions accept explicit paths so tests and future
runtime owners do not need hidden global state.

Publication follows the stronger durable host-record discipline already used by
the verified USB writer rather than the lighter profile-store replacement path.
The application state directory is private and current-user owned; publication
uses a nonblocking in-process guard plus a process-safe sibling advisory lock,
an exclusively created no-follow `0600` temporary file, complete write plus
file `fsync`, exact temporary readback, revalidation of the previously
published target, atomic same-directory `os.replace()`, directory `fsync`, and
exact post-publication readback. Save calls using this API therefore cannot
silently become last-writer-wins, and target changes observed before replacement
are rejected. As with ordinary POSIX atomic replacement, an uncooperative
external writer that changes the target after final revalidation is outside
this advisory coordination boundary.

Loading is explicit rather than automatic. A missing state file returns no
persisted state, while a present canonical document with an empty record array
restores the distinct empty tuple. Present files must be private, stable,
current-user-owned regular files opened with no-follow semantics and bounded
before their exact bytes are passed unchanged to the Milestone 23.12 decoder for
fresh-snapshot rebinding. Filesystem failures use stable redacted diagnostics;
codec and rebinding failures retain the existing provenance error boundary.

This milestone does not choose cleanup or migration behavior and does not wire
loading into application or daemon startup. Runtime lifecycle ownership,
arbitrary-field acceptance, provider-to-SDS mapping, record creation/removal,
renderer workflows, live RadioReference transport, MyRR, and automatic
synchronization remain deferred.

## Milestone 23.14 durable name-acceptance provenance completion boundary

Milestone 23.14 closes the durability gap left after Milestone 23.11 verified a
name-acceptance write and Milestone 23.13 made provenance itself explicitly
durable. The existing storage-neutral name-acceptance executor remains the
authority for mutation and exact post-write `FavoritesStorageSnapshot`
verification; the new composition layer does not duplicate copied-tree, USB,
backup, recovery, or activation behavior.

Each name-acceptance plan now retains both the exact baseline
`FavoritesExternalRecordState` and the exact provider observation used to derive
its preview. Public plan construction must reproduce that exact preview from the
retained baseline state and observation and must also reproduce the exact
name-acceptance provenance transformation: only the bound name field advances to
the retained observed value, the local target advances to the write plan's exact
intended target, and record observation evidence advances to the retained
observation. Historical provenance that does not affect preview classification
is still protected separately by the durable completion preflight, which
requires the complete persisted tuple to contain the exact retained baseline
state exactly once before any Favorites mutation occurs.

Durable completion explicitly loads and rebinds the whole persisted provenance
document against the write plan's exact baseline snapshot. It preserves tuple
ordering, replaces only the exact matched baseline record in place with the
planned intended state, canonicalizes the complete intended document, and
rebinds that document against the exact intended Favorites snapshot before
calling the existing executor. Missing persisted provenance, stale historical
provenance, duplicate/missing exact baseline matches, serialization failures, or
intended-snapshot rebinding failures therefore refuse execution before storage
mutation.

Milestone 23.14 also adds expected-current publication to the Milestone 23.13
filesystem boundary. The conditional save API distinguishes an expected absent
file from an expected present empty document and compares the caller's canonical
expected complete document with the exact current file while holding the
existing publication lock. The ordinary target revalidation later in publication
still detects changes during the replace window. This prevents cooperating
writers from silently overwriting a provenance document that changed after the
durable-completion preflight.

After the Favorites executor returns, durable completion independently rereads
storage through the existing Milestone 23.11 boundary and requires exact equality
with the intended snapshot. Only then may it conditionally publish the complete
updated provenance tuple. The immutable durable result retains both the complete
baseline tuple and complete published tuple and proves that the latter is exactly
the former with one in-place accepted-state replacement.

Favorites storage mutation and provenance publication are intentionally not
presented as one cross-resource atomic transaction. A concurrent provenance
change or filesystem failure can still occur after the Favorites mutation has
been independently verified. In that case the composition raises a distinct
post-write persistence error, does not overwrite the changed provenance, does
not claim durable completion, and does not attempt a generic speculative rollback
of the verified Favorites write. The older provenance will normally fail closed
against the changed Favorites bytes on a later fresh-snapshot rebind.

Loading and completion remain explicit caller-driven operations. This milestone
does not add application or daemon startup restoration, global lifecycle
ownership, cleanup/migration policy, arbitrary-field acceptance, provider-to-SDS
mapping, record creation/removal acceptance, renderer workflows, live
RadioReference transport, MyRR, or automatic synchronization.

## Milestone 23.15 startup restoration lifecycle ownership boundary

Milestone 23.15 adds one renderer-neutral owner for the startup restoration step
without making the scanner daemon, CLI, TUI, web dashboard, or Home Assistant
adapter the Favorites owner. `FavoritesExternalProvenanceLifecycle` accepts an
injected `FavoritesStorageSource` plus an explicit durable provenance path; the
normal host path remains `ConfigurationPaths.favorites_external_provenance_file`.

One `start()` attempt reads exactly one fresh `FavoritesStorageSnapshot`, then
passes that same immutable snapshot to the existing Milestone 23.13 durable
loader. The lifecycle does not perform a second Favorites read, heuristic target
search, or partial provenance recovery. Successful restoration therefore keeps
the Milestone 23.12 exact locator/digest rebinding rule authoritative: moved,
renamed, changed, missing, or ambiguous local records still fail closed.

The active lifecycle snapshot retains the exact fresh Favorites snapshot and the
complete rebound provenance collection. Missing durable state remains `None`,
while a present canonical document containing zero records remains `()`, so
startup ownership does not collapse the Milestone 23.13 absent-versus-empty
distinction. Repeated `start()` calls while active are idempotent and return the
already-restored evidence without rereading either resource.

Startup failures are terminal for that lifecycle instance. The original exception
still propagates through the existing storage or provenance error boundary, but
the lifecycle's public failed state retains only the exception class name and no
partial Favorites/provenance restoration evidence. Callers that intentionally
want a new attempt must construct a new owner, which forces a new explicit fresh
snapshot boundary. `close()` is idempotent, terminal, and does not mutate either
Favorites storage or the durable provenance document.

This milestone establishes lifecycle ownership but deliberately stops before
wiring that owner into application or daemon startup. Renderer-specific
construction, global singleton ownership, cleanup/migration policy, provider
refresh, arbitrary-field acceptance, provider-to-SDS mapping, record
creation/removal acceptance, live RadioReference transport, MyRR, and automatic
synchronization remain separate follow-on work.

## Milestone 23.16 assisted-refresh preview composition boundary

Milestone 23.16 composes the active Milestone 23.15 lifecycle evidence with one
injected `FavoritesExternalSource`. Each explicit session refresh captures one
lifecycle snapshot and requires it to be active before performing exactly one
source observation read. The one immutable observation tuple and the lifecycle's
restored provenance are passed to the existing source-neutral import preview
function; no preview decision, editing, acceptance, or durability rule is
duplicated.

A successful refresh returns immutable evidence containing the exact lifecycle
snapshot, exact observations, and exact preview. Missing durable provenance and
a present canonical empty document are both empty inputs only at the preview
function boundary; the retained lifecycle snapshot continues to expose `None`
versus `()`. Every refresh is a new observation attempt rather than a cached
result.

Source or preview failures propagate without changing or closing the lifecycle
and without retaining partial or last-successful session state, so an explicit
later refresh may retry while the lifecycle remains active. The session owns no
cleanup of its injected dependencies and performs no Favorites or provenance
mutation. Acceptance/publication, record creation or removal, arbitrary-field
acceptance, mapping expansion, live provider transport, credentials, MyRR,
scheduled or background synchronization, daemon/renderer wiring, and cleanup or
migration policy remain deferred.

## Milestone 23.17 assisted-refresh name-acceptance planning composition boundary

Milestone 23.17 adds one explicit, pure selection seam between the immutable
Milestone 23.16 refresh result and the existing Milestone 23.10 name-acceptance
planner. A caller selects one exact record preview already retained by the
refresh result. The new composition resolves only evidence in that same result:
one exact linked lifecycle provenance state by external identity and exact local
target, and one exact observation by external identity and observation evidence.
It performs no second source read and no lifecycle snapshot call.

The immutable composition result retains the exact refresh result, selected
preview, matched observation, matched baseline state, and resulting
`FavoritesExternalNameAcceptancePlan`. Its public construction independently
rederives the same pure chain and rejects any substituted relationship. Missing
persisted provenance (`None`) and a present-empty document (`()`) cannot support
acceptance because neither contains a linked baseline record. Added/unbound,
removed, conflicting, local-only or detached, unchanged, and otherwise
unsupported selections also fail closed.

The existing name planner remains authoritative for externally owned name
replacement classification, simultaneous bound-field change rejection,
schema-aware rename construction, ordinary Favorites write planning, and the
intended provenance transformation. Provider-only fields without a reviewed SDS
binding remain visible preview evidence: they are neither promoted into scanner
changes nor rejected merely for being present when the authoritative name
planner accepts the selected record.

This milestone stops at the existing pure name-acceptance plan. Favorites writes
and provenance publication, lifecycle invalidation or advancement after a future
durable acceptance, arbitrary-field acceptance, provider-to-SDS mapping
expansion, record creation/removal acceptance, live RadioReference transport and
credentials, renderer/daemon/CLI/TUI/web/Home Assistant wiring, MyRR, and
automatic, scheduled, polling, or background synchronization remain deferred.

## Detach semantics

Detaching an externally sourced record or field should preserve its current
scanner-compatible local value while changing ownership so later provider
refreshes do not replace it automatically.

Detach must not discard unknown source material, rewrite unrelated fields, or
invent a new scanner identity.

## Secrets and exported data

Provider usernames, passwords, application keys, tokens, cookies, and equivalent
credentials are secrets.

Ordinary configuration may retain non-secret provider identifiers and secret
references, but secret values must stay out of:

- `FavoritesStorageSnapshot` values;
- exported Favorites files;
- import/comparison reports intended for sharing;
- provenance structures that may be exported;
- logs and diagnostics; and
- public API representations.

## RadioReference research boundary

RadioReference-assisted import is the first intended provider use case, but a
concrete network adapter should follow documented-interface research rather than
drive the core model.

Before accepting a production adapter, document from an approved source:

- supported authentication/application-key requirements;
- licensing and attribution constraints;
- available record identifiers and their documented stability;
- revision/update metadata or the absence of it;
- supported data shapes relevant to Favorites;
- rate limits and retry requirements;
- deletion/retirement semantics if exposed; and
- any restrictions on caching or redistribution.

Do not scrape HTML or depend on undocumented private endpoints.

## MyRR boundary

MyRR synchronization remains research-only until an approved and documented
interface is established. Milestone 23.1 does not implement MyRR login,
synchronization, scraping, or private-endpoint access.

## Automated validation

Normal tests should remain deterministic and offline. Use synthetic provider
fixtures and fake source adapters to cover provenance, ownership, detach,
conflicts, revision changes, malformed observations, secret redaction, and
preview determinism.

Automated tests must not require RadioReference credentials, internet access,
MyRR access, FTP access, USB storage, or a physical scanner.

## Deferred behavior

Milestone 23.1 does not include:

- automatic or scheduled synchronization;
- background polling;
- live RadioReference network access before documented-interface research is
  accepted;
- MyRR synchronization;
- renderer-specific CLI/TUI/web/Home Assistant import workflows;
- credential persistence UI;
- provider-specific SDS field mapping, record-template selection, and
  scanner-schema representability decisions;
- snapshot-bound acceptance and stale/ambiguous-target revalidation;
- direct storage mutation; or
- bypassing existing Favorites editing, validation, write planning, backup, and
  execution safety boundaries.
