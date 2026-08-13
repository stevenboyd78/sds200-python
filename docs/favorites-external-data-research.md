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

An import/update proposal is evidence, not a write operation.

A deterministic preview should be able to identify:

- proposed additions;
- proposed replacements;
- proposed removals when the provider explicitly supports that meaning;
- unchanged externally owned values;
- local-only values;
- ownership conflicts;
- stale or ambiguous local targets; and
- provider observations that cannot be represented safely in the current
  scanner schema.

No proposal should silently apply last-writer-wins behavior. Acceptance belongs
to a later explicit editing/planning step using the existing Favorites services.

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
- direct storage mutation; or
- bypassing existing Favorites editing, validation, write planning, backup, and
  execution safety boundaries.
