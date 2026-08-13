# RadioReference documented-interface research

This document records the provider-specific research and security boundary begun
in Milestone 23.2 and extended by the WSDL-contract work in Milestone 23.3. It is
intentionally separate from the renderer-neutral external Favorites model
introduced in Milestone 23.1.

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
