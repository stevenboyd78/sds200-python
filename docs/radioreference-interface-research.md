# RadioReference documented-interface research

This document records the provider-specific research and security boundary begun
in Milestone 23.2, extended by the WSDL-contract work in Milestone 23.3, the
offline SOAP response decoder in Milestone 23.4, the offline SOAP request
serializer in Milestone 23.5, and the offline provider-to-observation mapping
foundation in Milestone 23.6. It remains intentionally separate from the
renderer-neutral external Favorites model introduced in Milestone 23.1.

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

## Current documented findings

The provider documentation was reviewed again on 2026-08-13 before defining the
first code boundary.

The current support article, updated 2026-06-20, confirms that:

- the database service is SOAP/XML and the published WSDL remains the normative
  machine-readable interface;
- approved radio/radio-programming applications may receive an application key;
- every end user must authenticate with that user's own RadioReference account
  and must satisfy the provider's premium-subscription requirement;
- credentials must not be pooled or silently substituted between users;
- the service covers the RadioReference database rather than Broadcastify
  services; and
- mirroring or substituting for the public RadioReference website is outside the
  standard radio-programming use case.

The human-readable SOAP2 documentation identifies production version 18 as the
current `latest` version. It documents an integer-or-`latest` version selector,
`rpc` and `doc` SOAP styles, and an `authInfo` structure containing application
key, username, password, version, and style.

The same documentation explicitly names or references programming-relevant
operations including:

- `getCountryInfo`, `getCountyInfo`, and `getAgencyInfo` for geographic,
  agency, and subcategory relationships;
- `getCountyFreqsByTag` and `getAgencyFreqsByTag` for tagged conventional
  frequency data;
- `searchCountyFreq`, `searchMetroFreq`, and `searchStateFreq` for
  frequency-oriented searches;
- `getTrsDetails` for trunked-system details; and
- `getTrsSites` for trunked sites and site-frequency information.

Version notes also document provider data such as frequency/talkgroup encryption,
DMR color code/talkgroup/slot values, NXDN channel IDs and RAN, site RFSS/NAC,
location rectangles, and the version-18 trunked-site `tdma_cc` attribute. These
fields are provider evidence only; their presence does not establish an SDS
Favorites mapping.

The accessible human-readable documentation does not fully enumerate every
current WSDL operation signature or establish a generic stable-record-ID,
revision-token, change-feed, or deletion-feed contract. That absence must not be
treated as proof that such fields or operations do not exist. A production
adapter remains blocked on direct inspection of the then-current WSDL and
sanitized fixtures for every operation actually used.

The current API guidance distinguishes approved radio-programming use from
redistribution or broader commercial data products, while the general site terms
reserve additional licensing rights for non-personal/commercial reuse. Approval
for an application key therefore must not be treated as blanket redistribution
permission. Provider data must not be copied into repository fixtures or exposed
as a mirror by this project.

## Direct WSDL inspection evidence

A read-only operator audit on 2026-08-13 fetched the documented public
`https://api.radioreference.com/soap2/?wsdl&v=latest` resource without
credentials. The response was HTTP 200 with content type `text/xml; charset=utf-8`
and contained 55,955 bytes. Its SHA-256 was
`1bb8090cf6415e429eb432dd964b1d26164af7eb2240a8b6d345007821d12f33`.

That fingerprint is point-in-time research evidence for the meaning of `latest`
during the audit. It must not be treated as a permanent expected hash because the
provider can legitimately revise the current WSDL.

The inspected document reported:

- root element `definitions`;
- target namespace `http://api.radioreference.com/soap2`;
- one service named `RRWsdl`;
- one port named `RRWsdlPort`;
- one binding named `RRWsdlBinding`;
- RPC SOAP style over the SOAP HTTP transport URI;
- 31 port-type operations;
- 62 WSDL messages; and
- 74 complex types.

The service address embedded in that WSDL was
`http://api.radioreference.com/soap2/index.php`, even though the WSDL itself was
retrieved successfully over HTTPS. Because `authInfo` carries the application key
and end-user password on authenticated calls, the implementation must not
silently follow or construct a cleartext HTTP credential path. Production
transport work remains blocked until the approved/documented HTTPS invocation
endpoint and redirect/TLS behavior are explicitly validated.

Programming-relevant operations present in the inspected WSDL include:

- `getCountryInfo`, `getStateInfo`, `getCountyInfo`, and `getAgencyInfo`;
- `getSubcatFreqs`, `getCountyFreqsByTag`, and `getAgencyFreqsByTag`;
- `searchCountyFreq`, `searchStateFreq`, and `searchMetroFreq`;
- `getTrsDetails`, `getTrsSites`, `getTrsTalkgroupCats`, and
  `getTrsTalkgroups`; and
- supporting lookup operations including `getTag`, `getMode`, `getTrsType`,
  `getTrsFlavor`, and `getTrsVoice`.

The WSDL also contains FCC, user, and feed-oriented operations. Their presence
does not place them in the scanner-programming scope for this project.

The `authInfo` complex type contains `username`, `password`, `appKey`, `version`,
and `style`, matching the human-readable authentication documentation.

The inspected provider types expose useful provider-side identity and update
evidence, including:

- conventional `freq`: `fid`, `scid`, and `lastUpdated`;
- `Talkgroup`: `tgId`, `tgCid`, and `tgDate`;
- `TalkgroupCat`: `tgCid`, `sid`, and `lastUpdated`;
- `TrsSite`: `siteId` and `sid`;
- `TrsListDef`: `sid` and `lastUpdated`;
- `AgencyInfo`: `aid`, `ctid`, `stid`, and `lastUpdated`;
- `CountyInfo`: `ctid`, `stid`, and `lastUpdated`;
- `StateInfo`: `stid`;
- `CountryInfo`: `coid`; and
- `Trs`: `lastUpdated` plus provider system-identification/bandplan structures.

Those fields are evidence of the documented schema only. They do not establish
that every identifier is immutable for the lifetime of a provider record, that
`lastUpdated` or `tgDate` is a revision token, or that an omitted record represents
a deletion.

Programming-relevant data fields observed directly in the WSDL include
conventional output/input frequency, callsign, description, alpha tag, tone,
color code, DMR talkgroup/slot, mode, encryption, class, tags, and sort order;
trunked talkgroup decimal/subfleet/slot/description/alpha/mode/encryption/tags;
trunked site number/zone/RFSS/NAC/RAN/modulation/location, TDMA control-channel
evidence, licenses, frequencies, and bandplan; and location rectangles/ranges on
several geographic and provider grouping types.

The port-type operation declarations inspected by the audit did not contain
explicit WSDL `fault` message declarations. That must not be interpreted as proof
that authenticated SOAP calls cannot return SOAP Fault responses or transport
errors.

Milestone 23.3 must inspect the exact request message parts, return/container
types, binding SOAP actions, and nested schema relationships for the operation
subset it intends to model before accepting parser or DTO implementation.

## Milestone 23.3 operation and provider-schema audit evidence

A second read-only audit on 2026-08-13 re-fetched the exact reviewed WSDL
fingerprint and extracted the operation contracts and reachable provider schema
used by the programming subset.

Every selected programming operation contained an `authInfo` request part. The
binding uses RPC style with SOAP encoded bodies and the SOAP encoding URI
`http://schemas.xmlsoap.org/soap/encoding/`. The selected operations expose SOAP
actions in the form
`http://api.radioreference.com/soap2#<operation>`.

The selected response graph reaches 54 provider complex types and 24 SOAP array
containers. Important exact container relationships include:

- `Freqs -> freq[]`;
- `searchFreqResults -> searchFreqResult[]`;
- `TalkgroupCats -> TalkgroupCat[]`;
- `Talkgroups -> Talkgroup[]`;
- `TrsSites -> TrsSite[]`;
- `TrsSiteFreqs -> TrsSiteFreq[]`;
- `TrsSiteLicenses -> TrsSiteLicense[]`;
- `TrsSysid -> trsSysidDef[]`;
- `TrsBandplan -> trsBandplanDef[]`;
- `TrsList -> TrsListDef[]`;
- `Cats -> cat[]` and `SubCats -> subcat[]`; and
- geographic/support arrays for states, counties, agencies, tags, modes,
  rectangles, and provider lookup records.

No explicit `minOccurs`, `maxOccurs`, `nillable`, `default`, or `fixed` metadata
was present on the reachable provider fields inspected by the audit. That is
schema evidence only; it must not be used to invent provider-side optionality,
nullability, or live response guarantees.

The operation audit also performed credential-free HEAD requests against both
`https://api.radioreference.com/soap2/index.php` and the WSDL-advertised HTTP
address. Both returned HTTP 200 without redirect during the audit. This proves
only that an HTTPS endpoint currently answers a credential-free request; it does
not establish an approved authenticated invocation contract. A production client
must remain HTTPS-only and blocked on explicit approved/documented live transport
validation.

Milestone 23.3 may therefore define immutable provider-record DTOs and static
reviewed operation metadata from this schema without adding SOAP parsing or live
network behavior. Those DTOs must keep provider IDs, timestamps, strings,
decimals, arrays, and nested records separate from
`FavoritesExternalRecordObservation`, and must not infer SDS mappings, deletion
semantics, generic revisions, or provider identifier lifetime guarantees.

## Milestone 23.4 offline SOAP decoding boundary

Milestone 23.3 intentionally stopped before SOAP response decoding. The reviewed
WSDL proves an RPC/encoded contract, operation names, request parts, response
types, SOAP actions, and provider schema, but it does not prove the exact
serializer representation that RadioReference will emit for every successful or
fault response. No private authenticated response was captured for repository
fixtures.

Milestone 23.4 therefore treats wire decoding as an offline protocol boundary.
Schema-derived and hand-authored fixtures may exercise standards-compatible SOAP
1.1 RPC/encoded forms, including inline values, SOAP-ENC arrays, and local
`id`/`href` references. Passing those fixtures proves only that the project can
decode those protocol forms deterministically; it does not claim live-provider
compatibility until a later approved authenticated validation explicitly observes
the production endpoint.

The decoder must be bounded and fail closed. Its XML input remains in memory,
must not resolve external resources, and must reject malformed envelopes,
unexpected operations, response-type mismatches, duplicate required members,
missing required members, malformed arrays, duplicate or missing reference IDs,
external references, reference cycles, and excessive reference graphs.

Scalar decoding must remain schema-faithful rather than scanner-aware:

- `xsd:string` values are preserved verbatim rather than stripped or normalized;
- `xsd:int` stays within the signed 32-bit XML Schema range already enforced by
  the provider DTOs;
- `xsd:decimal` is represented by finite `Decimal` values;
- `xsd:boolean` accepts only XML Schema boolean lexical representations; and
- `xsd:dateTime` is parsed deterministically as timestamp evidence without
  promoting it to a generic revision token.

SOAP Fault text and arbitrary malformed-response details are provider-controlled
input and must not escape through public errors. Parser failures should map to
stable redacted RadioReference failure classes while preserving the existing
secret-handling boundary.

This milestone remains offline. It does not add request serialization, HTTP/TLS
transport, credential use, live provider calls, provider-to-SDS mapping,
`FavoritesExternalRecordObservation` generation, automatic synchronization,
MyRR integration, or Favorites mutation.

## Milestone 23.5 offline SOAP request serialization boundary

The reviewed WSDL evidence is sufficient to define an offline request serializer
without introducing a production transport. The selected programming operations
use RPC style, encoded input bodies, the SOAP encoding URI
`http://schemas.xmlsoap.org/soap/encoding/`, the target namespace
`http://api.radioreference.com/soap2`, and operation-specific SOAP actions of the
form `http://api.radioreference.com/soap2#<operation>`.

The WSDL audit also established the exact `authInfo` complex type used by every
selected programming operation. Its fields, in schema order, are:

- `username: xsd:string`;
- `password: xsd:string`;
- `appKey: xsd:string`;
- `version: xsd:string`; and
- `style: xsd:string`.

Milestone 23.5 may promote that evidence into immutable contract metadata and use
it with the existing exact operation request parts. The request-side programming
subset otherwise needs only `xsd:int`, `xsd:decimal`, and `xsd:string`.

The serializer must remain offline and dependency-free. It may create
standards-compatible SOAP 1.1 RPC/encoded request bytes from synthetic or
ephemeral inputs, but passing offline serialization tests must not be described
as proof that the provider accepted a live request. The audited programming
binding is RPC; this milestone must not invent an unreviewed document-style wire
representation.

Serialized `authInfo` necessarily contains resolved secrets. Request XML is
therefore secret-bearing ephemeral material, not diagnostic or provenance data.
It must not be retained in public DTOs, logged, included in exception messages,
stored in fixtures, or copied into documentation. Automated tests must use only
synthetic credentials.

HTTP/TLS transport, production endpoint and redirect behavior, live
authentication, provider-to-SDS mapping, normalized external-observation
generation, update previews, synchronization, MyRR integration, and Favorites
mutation remain separate follow-on work.

## Milestone 23.6 offline observation mapping boundary

Milestone 23.6 begins the provider-specific normalization layer without opening a
network session or mutating Favorites data. The mapper consumes reviewed immutable
RadioReference provider DTOs and produces immutable
`FavoritesExternalRecordObservation` values for the source-neutral preview
machinery.

The caller supplies `FavoritesExternalSourceIdentity` and a timezone-aware
observation time. The source provider must be exactly `radioreference`; the
dataset remains opaque caller-owned identity and is not invented from provider
records. Provider timestamps such as `lastUpdated` and `tgDate` remain provider
evidence only and are not promoted to generic revision tokens. Normalized
observation evidence therefore uses the caller's observation time with
`revision=None`.

The reviewed conventional-frequency slice maps only fields supported by both the
provider schema and observed HPD semantics:

- provider `frequency_id` becomes the namespaced external record ID
  `frequency-<id>`;
- provider `alphaTag` becomes normalized field `name`, preserving exact text,
  including empty or padded strings, without falling back to description; and
- provider output frequency, represented by an exact finite `Decimal` in MHz,
  becomes normalized field `frequency` as the whole-Hz decimal string used by
  observed HPD conventional channel records.

The MHz-to-Hz conversion must be exact. Values requiring fractional-Hz rounding
are rejected rather than silently rounded or truncated.

The reviewed talkgroup slice is narrower because observed TGID records have both
17- and 18-field shapes and the extended form contains an additional position
whose semantics remain intentionally unmodeled. The current mapping therefore
uses only:

- provider `talkgroup_id` as namespaced external record ID `talkgroup-<id>`; and
- provider `alphaTag` as normalized field `name`, with the same exact-text and
  no-fallback semantics as conventional frequencies.

The provider talkgroup decimal value is deliberately not normalized yet. The
project has not established a stable source-neutral field contract that is safe
to apply across both observed TGID shapes. Mode, tone, service/tag translation,
encryption, input frequency, hierarchy placement, deletion inference, search
frequency results without provider record identity, trunk-system records without
a single reviewed system ID, additional trunked records, and provider omission
semantics likewise remain unmapped.

This mapping work remains dependency-free and offline. Automated tests use
synthetic provider DTOs and existing local Favorites fixtures only. HTTP/TLS
transport, live authenticated calls, production endpoint validation, automatic
synchronization, MyRR integration, operator merge acceptance, and Favorites
storage mutation remain separate follow-on work.

## Milestone 23.7 offline SOAP result observation adapter boundary

Milestone 23.7 connects the reviewed offline SOAP decoder to the Milestone 23.6
provider-record mappers without adding network access or changing the
source-neutral Favorites boundary. The adapter consumes both the reviewed
`RadioReferenceWsdlOperation` and its decoded result so operation semantics remain
explicit even when a valid provider response decodes to an empty tuple.

The reviewed adapter supports only operations whose decoded records already have
stable provider identities and Milestone 23.6 mappings:

- `GET_SUBCATEGORY_FREQUENCIES`;
- `GET_COUNTY_FREQUENCIES_BY_TAG`;
- `GET_AGENCY_FREQUENCIES_BY_TAG`; and
- `GET_TRUNKED_TALKGROUPS`.

The three conventional-frequency operations require an exact immutable tuple of
`RadioReferenceFrequency` values. The talkgroup operation requires an exact
immutable tuple of `RadioReferenceTalkgroup` values. Supported empty tuples
produce empty immutable observation tuples. Result-container or item-type
mismatches fail closed instead of being coerced or partially mapped. Duplicate
provider record identities in a mapped result also fail closed rather than being
deduplicated or deferred to preview behavior.

The adapter reuses the caller-supplied `FavoritesExternalSourceIdentity` and
timezone-aware observation time and delegates each record to the reviewed
Milestone 23.6 mapper. It preserves decoded provider result order. Deterministic
cross-record ordering remains the responsibility of the existing
`RadioReferenceSource` normalized-observation validation boundary.

Search-frequency operations remain unsupported because their reviewed decoded
records do not contain a stable provider frequency record identifier. Country,
state, county, agency, trunk-system, site, talkgroup-category, tag, mode, trunk
type, trunk flavor, and trunk voice results likewise remain outside the
observation mapping boundary until explicit identities and normalized mappings
are reviewed. The adapter does not infer deletion from omission or construct
Favorites hierarchy.

Offline integration tests compose synthetic SOAP response bytes through
`RadioReferenceSoapDecoder` and then through the observation adapter for one
conventional-frequency result and one talkgroup result. These tests establish the
local decoder-to-normalization contract only; they are not evidence of live
provider acceptance or transport behavior.

HTTP/TLS transport, credential use, live authenticated calls, provider session
implementation, production endpoint behavior, automatic synchronization, MyRR,
operator merge acceptance, and Favorites storage mutation remain separate
follow-on work.

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

## Milestone 23.8 offline request-plan/session composition boundary

Milestone 23.7 establishes the reviewed decoded-result-to-observation boundary,
but it intentionally does not identify which operation and non-secret request
parameters define one external dataset read. Milestone 23.8 adds that missing
composition layer without treating a fakeable byte exchange as approved live
transport.

The first slice defines an immutable `RadioReferenceObservationRequestPlan`.
Each plan binds one `FavoritesExternalSourceIdentity` to exactly one operation
already accepted by the Milestone 23.7 observation adapter and to the exact
non-`authInfo` parameters declared by the reviewed WSDL contract. Parameter
storage is an immutable tuple of `(name, value)` pairs in WSDL order. The current
mapped operations use only `xsd:int` request parameters, which are validated
without coercion and with the XML Schema 32-bit range.

Request plans are deliberately secret-free. They do not retain application
keys, passwords, serialized SOAP request bytes, response bytes, cookies, tokens,
or provider session state. A fresh ordinary mapping may be produced only when a
serializer call needs it.

The second slice composes a plan through the existing offline SOAP request
serializer, a fakeable operation-aware byte exchange, the bounded response
decoder, and the Milestone 23.7 observation adapter. The exchange receives only
the reviewed operation, its reviewed SOAPAction, and ephemeral request bytes and
returns exact response bytes. This protocol deliberately does not define HTTP
method/header behavior, endpoint selection, redirects, certificates, retries, or
any other production transport semantics.

`RadioReferenceObservationSession` implements the existing normalized session
shape. It obtains one timezone-aware observation time from an injectable wall
clock before producing request bytes, keeps request and response bytes local to a
single read, preserves stable `RadioReferenceError` reasons, redacts arbitrary
exchange and malformed-response failures, and clears its owned application-key
and password references before closing the exchange. Its companion
`RadioReferenceObservationSessionFactory` is compatible with the existing
`RadioReferenceSource` secret-resolution and deterministic cleanup boundary.

Automated composition tests use only synthetic credentials and local SOAP
fixtures. They prove serializer/exchange/decoder/observation wiring and failure
normalization, not provider acceptance or HTTP/TLS behavior.

This milestone still does not establish HTTP method/header behavior, redirect
policy, certificate handling, retry semantics, or any other production HTTP/TLS
contract. Live provider access remains blocked on separate approved/documented
transport validation.

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

The current RadioReference foundation still does not include:

- live production RadioReference synchronization;
- provider-to-SDS template or hierarchy construction beyond the reviewed
  normalized frequency/name observations documented for Milestone 23.6;
- implicit scanner record creation from provider objects;
- arbitrary-field, record-creation/removal, or automatic merge acceptance beyond
  the explicit source-neutral linked-record name-acceptance boundary;
- renderer-specific CLI/TUI/web/Home Assistant assisted-import or acceptance UI;
- automatic application/daemon/renderer construction or startup wiring of the
  renderer-neutral external-provenance lifecycle owner;
- application/daemon/renderer wiring of the renderer-neutral assisted-refresh
  preview session; Milestone 23.16 only composes an injected normalized source
  with one active lifecycle snapshot and retains immutable per-attempt evidence;
- automatic or scheduled synchronization;
- MyRR synchronization; or
- bypassing the existing Favorites editing, validation, planning, backup,
  write-execution, post-write verification, and provenance durability safety
  boundaries.
