# RadioReference documented-interface research

This document records the provider-specific research and security boundary for
Milestone 23.2. It is intentionally separate from the renderer-neutral external
Favorites model introduced in Milestone 23.1.

The project may use RadioReference only through documented and approved
interfaces. This document does not authorize scraping, undocumented/private
endpoint use, live credential testing, automatic synchronization, or Favorites
storage mutation.

## Current documented service

RadioReference currently documents its database integration service as a SOAP
XML Web Service. The current documentation points developers to the published
WSDL at:

`https://api.radioreference.com/soap2/?wsdl&v=latest`

Primary provider documentation reviewed for this milestone:

- `https://support.radioreference.com/hc/en-us/articles/18844460198932-Database-Web-Service-API`
- `https://wiki.radioreference.com/index.php/RadioReference.com_Web_Service3.1`
- `https://www.radioreference.com/terms/`

The provider documentation must be rechecked before a production adapter ships
because authentication, approval, licensing, service versions, data shapes, and
other service rules may change.

## Intended product use

The project use case is radio/scanner programming assistance: obtain documented
reference data, normalize it into source-neutral provider observations, preview
how that data differs from the user's local Favorites data, and later allow an
operator to make explicit merge decisions through the existing Favorites
editing and write-planning safety boundaries.

The project is not intended to reproduce, mirror, or substitute for the public
RadioReference website.

## Authentication and subscription boundary

The documented database service requires an application key approved for the
application. Current provider documentation also requires each end user to
authenticate using that user's own RadioReference account, with the applicable
premium subscription requirement enforced per user.

The implementation should therefore keep separate concepts for:

- non-secret provider/application identity;
- application-key secret reference;
- end-user RadioReference username;
- end-user password secret reference;
- requested/documented Web Service version and SOAP style when needed; and
- normalized provider observations returned after successful authenticated
  access.

Do not share, pool, embed, or silently substitute user credentials.

## Secret handling

Application keys, passwords, tokens, session cookies, and equivalent credentials
must remain behind secret references and out of ordinary serialized Favorites
state.

Secret values must not appear in:

- `FavoritesStorageSnapshot` data;
- `FavoritesExternal*` provider observation/provenance objects intended for
  export or reporting;
- exported Favorites files;
- comparison/import preview output intended for sharing;
- logs;
- diagnostics;
- exception strings;
- test fixtures;
- public API responses; or
- repository documentation/examples.

A sanitized configuration may retain a username and secret-reference names when
those values are needed to identify which credentials should be resolved.

## Provider transport boundary

RadioReference-specific SOAP/WSDL objects must remain behind a narrow adapter
boundary. The existing source-neutral model should continue to consume immutable
`FavoritesExternalRecordObservation` values rather than SOAP clients, XML
elements, WSDL-generated classes, authentication objects, or provider session
state.

The transport boundary should be fakeable so automated tests can exercise:

- authentication request construction without real secrets;
- version/style selection;
- SOAP fault normalization and redaction;
- malformed or incomplete response handling;
- deterministic mapping from sanitized provider DTOs/XML fixtures;
- duplicate provider identifiers;
- unsupported/missing fields;
- explicit provider absence versus unprovided data;
- cleanup after transport failure; and
- stable observation ordering.

Normal tests must remain offline.

## WSDL/data-shape research required before production mapping

Before accepting provider-to-Favorites mapping, inspect the current documented
WSDL and record the exact calls and returned data shapes needed for the project.
At minimum, research should cover the documented interfaces relevant to:

- geographic/state/county/metro lookup needed to select a programming scope;
- agencies and conventional frequencies;
- trunked radio systems;
- trunked sites and site frequencies;
- talkgroups and their grouping/agency relationships;
- tags or service/category metadata useful for scanner programming;
- provider record identifiers;
- update/revision timestamps or other documented change evidence;
- deletion/retirement semantics, if any are documented;
- response-size or pagination behavior, if applicable;
- rate limits, retry rules, and service fault semantics; and
- any attribution, caching, redistribution, or licensing restrictions relevant
  to generated scanner programming data.

Do not infer identifier stability, revision semantics, deletion semantics, or
redistribution rights merely because a field exists in a response.

## Approval and live-access boundary

A production network adapter should not be treated as validated merely because
offline fixtures pass.

Before live validation:

1. obtain an application key through RadioReference's current approved process;
2. confirm that the project's stated scanner-programming use case matches the
   approved use;
3. recheck the current provider documentation and terms;
4. identify the exact WSDL operations the project will call;
5. configure real application/user credentials only through local secret
   resolution; and
6. ensure captured debugging material is sanitized before it can enter tests,
   issues, logs, or the repository.

Live validation should be a separate operator-controlled step and must not be
part of the normal automated test suite.

## MyRR boundary

MyRR remains outside this milestone. Do not infer that the documented database
Web Service also provides a supported MyRR synchronization interface.

Any future MyRR work requires its own documented/approved interface research.
Do not automate the website, scrape account pages, or depend on undocumented
private endpoints.

## Deferred behavior

Milestone 23.2 does not include:

- live production RadioReference synchronization;
- provider-to-SDS field/template mapping;
- implicit scanner record creation from provider objects;
- operator merge/acceptance workflows;
- renderer-specific CLI/TUI/web/Home Assistant import UI;
- automatic or scheduled synchronization;
- MyRR synchronization;
- Favorites storage writes; or
- bypassing the existing Favorites editing, validation, planning, backup, and
  write-execution safety boundaries.
