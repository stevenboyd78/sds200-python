# Advanced protocol research

This document establishes the evidence and fixture foundation for advanced
scanner protocol work. It does not define public scanner behavior.

## Milestone 24.1 boundary

Milestone 24.1 inventories the existing protocol architecture, records evidence
for every preserved Milestone 24 operation, defines fixture provenance, and
identifies later framing and lifecycle prerequisites. It creates no command,
API, CLI, TUI, web, Home Assistant, renderer, or automatic/background behavior.
It does not probe scanner hardware, implement an advanced command, or refactor
unrelated code.

In particular, this slice prohibits live probing of reported `MSM,1` behavior
and requires no disruptive discovery. Advanced operations remain
specification-backed or fixture-tested until each is physically exercised and
documented with its own model, firmware, transport, scenario, and limitations.

## Milestone 24.2 implementation boundary

Milestone 24.2 implements the narrow evidence-backed Favorites-list retrieval
request `GLT,FL`. It adds public `GetGltFavorites`, `GltRecord`, `GltResponse`,
and `SDSScanner`/`SDS200.get_glt_favorites()` surfaces through the normal
command-correlation path. `GetGltFavorites.wire` is exactly `GLT,FL`, and its
response command is exactly `GLT`; there is no arbitrary or free-form GLT
keyword API.

One authoritative immutable internal framing definition relates supported XML
commands to their bounded document roots:

- `GSI` -> `ScannerInfo`;
- `PSI` -> `ScannerInfo`; and
- `GLT` -> `GLT`.

CR-delimited bounded assembly and UDP XML detection and correlation share this
definition. It is internal implementation framing, not a new package-level API.
`XmlResponseAssembler` is command/root-aware and continues to accept an
explicit constructor mapping override for future bounded-XML extensions. A new
recognized XML header is also a resynchronization point for an incomplete
document.

Domain parsing remains separate from framing and transport: `GltParser`
produces `GltResponse`, rather than embedding GLT semantics in
`XmlResponseAssembler` or the UDP network layer. The represented `GLT,FL` shape
is intentionally lossless: root attributes are preserved; direct child records
are retained as an ordered tuple; repeated and unknown child tags are
preserved; and every child attribute and the complete raw XML are retained.
`Index`, `Q_Key`, `Monitor`, `N_Tag`, and unknown values are not speculatively
converted. Raw XML remains the fallback preservation boundary for structure not
modeled by `GltRecord`.

### Serial and replay-style transport

A response begins with `GLT,<XML>,`, after which the generalized assembler
collects the bounded `<GLT>...</GLT>` document. All recognized XML headers,
including GLT, remain resynchronization points. This extends framing without
changing existing GSI or PSI behavior.

### SDS200 UDP transport

Sending exact `GLT,FL` creates a one-shot GLT expectation. Bare `<GLT>` XML is
correlated only when the expected root matches: GLT cannot claim
`ScannerInfo`, and GSI/PSI cannot claim `GLT`. Explicitly prefixed GLT XML is
also validated against the `GLT` root. Existing numbered `Footer`/`Foot`
fragment reassembly applies to GLT, and the exact original `GLT,FL` wire command
is retained for retry after retryable sequence failures. Successful GLT
completion ends its one-shot expectation and retry state, while PSI remains the
persistent `ScannerInfo` stream.

Malformed supported XML may still be forwarded to domain parsing so a
`protocol_error` can be emitted. It does not count as a completed UDP XML
document, consume the pending one-shot expectation, or erase GLT retry state.
An incomplete numbered sequence likewise does not count as completion.

### Evidence and exposure limits

`tests/fixtures/advanced_protocol/synthetic-glt-fl.jsonl` remains the
representative replay fixture. It is synthetic, not hardware-derived; its
repeated `FL` records and deliberate unknown `FutureAttr` enforce source order
and forward-compatible preservation. Deterministic fake UDP datagrams validate
transport mechanics. No scanner, firmware, or model-specific physical GLT
capability claim is made, no live scanner probing occurred, and physical GLT
validation is future evidence rather than a closure prerequisite for this
offline/synthetic implementation slice.

No CLI, TUI, web, daemon, MQTT, or Home Assistant exposure was added. This
slice also adds no FQK, QSH, URC, AST, APR, waterfall, menu, disruptive, or
recovery behavior. Broader GLT arguments and hierarchy forms remain deferred
until evidence establishes their exact request and response semantics.

## Milestone 24.3 implementation boundary

The first Milestone 24.3 slice implements only the reviewed FQK ordinary-line
read and control forms. The public `FavoritesQuickKeyState` integer enum names
`NONEXISTENT = 0`, `DISABLED = 1`, and `ENABLED = 2`.
`FavoritesQuickKeys` is an immutable response containing exactly 100 typed
states and the original generic `Packet`, preserving its raw line and fields.
`GetFavoritesQuickKeys` and `SDSScanner.get_favorites_quick_keys()` provide the
read surface; `SetFavoritesQuickKeys` and
`SDSScanner.set_favorites_quick_keys()` provide the separate mutation surface.

The read wire is exactly `FQK`, and only an `FQK` packet with exactly 100
fields whose text is exactly `0`, `1`, or `2` is accepted. Missing, extra,
blank, whitespace-decorated, and other values fail closed. A write requires and
immutably normalizes exactly 100 integer or enum states, explicitly excluding
booleans, and emits `FQK` followed by those 100 decimal fields. Success is only
the exact acknowledgement `FQK,OK`; documented general negative
acknowledgements remain errors. Controller write status `0` is preserved
without reinterpretation because the reviewed specification intentionally says
the scanner ignores that position when setting.

FQK is specification- and synthetic-fixture-validated, not physically
validated on a scanner. This slice does not add transport framing, parser
specialization, or any CLI, TUI, web, daemon, MQTT, Home Assistant, or
background behavior.

QSH remains unimplemented and blocked pending stronger evidence for the exact
`FRQ` syntax. The QSH-looking literal used by replay redaction testing is not
protocol evidence and does not weaken the existing warning in the evidence
ledger below.

## Milestone 24.4 implementation boundary

Milestone 24.3 completed and merged the reviewed FQK read/control forms. QSH
remains deferred because the exact `FRQ` representation is unresolved. This
first narrow Milestone 24.4 slice adds only an immutable ordered/repeated
`ScannerInfo` projection containing every non-root node from the existing source
traversal. The existing tag-keyed projection remains compatible and continues
to select the last occurrence, and existing typed properties retain their
semantics. Raw XML remains exact.

This preservation foundation retains repeated and unknown elements and their
attributes for later evidence-led modeling. It does not invent new
`ScannerInfo` RAN, Color Code, Area, activity, or quality field names or
semantics, and it does not parse `SAD` into a speculative structured code model.
Existing raw `SAD` examples are evidence for values including CTCSS and NAC;
they do not establish additional field names for RAN, Color Code, or Area.
Synthetic regression evidence verifies preservation behavior but is not
physical scanner validation.

## Milestone 24.5 implementation boundary

Milestone 24.4 completed and merged the ordered/repeated `ScannerInfo`
losslessness foundation. This first narrow Milestone 24.5 slice implements only
the V2.00 specification-backed ordinary-line `URC` recording-status read and
recording-control mutation. `ScannerRecordingStatus` represents exact status
`0` as `STOPPED` and `1` as `RECORDING`; the immutable
`ScannerRecordingStatusResponse` preserves the successful read's exact generic
`Packet`. Separate `GetScannerRecordingStatus` and
`SetScannerRecordingStatus` commands expose exact `URC`, `URC,0`, and `URC,1`
wires through corresponding `SDSScanner` methods. Reads accept only one exact
`0` or `1` field, and writes accept only exact `URC,OK` acknowledgements.

Exact `URC,ERR,<code>` operation errors use one shared strict parser and raise
`ScannerRecordingControlError`. Codes `0001`, `0002`, `0003`, and `0004` map
respectively to `FILE ACCESS`, `LOW BATTERY`, `SESSION OVER LIMIT`, and
`RTC LOST`; an unknown well-formed four-digit code remains available exactly
with no invented reason. Malformed fields, extra fields, whitespace decoration,
and undocumented status values fail closed as protocol errors. Stable exception
text does not expose arbitrary scanner payload text.

The synthetic `synthetic-urc.jsonl` capture exercises stopped/read,
start/acknowledgement, recording/read, stop/acknowledgement, every documented
operation error, and one deliberate unknown error through the production replay
API. It is derived from the reviewed Uniden SDS Series Remote Command
Specification V2.00, not physical scanner capture evidence. No physical scanner
validation is claimed. Transport restrictions, firmware distinctions,
per-command model availability, storage and SD-card behavior, additional
statuses or errors, session-limit semantics, and recovery behavior remain
unknown.

Scanner-side `URC` control remains separate from the project's network-audio,
daemon, recording-metadata, recording-inventory, and WAV workflows. This slice
adds no integration or convenience start/stop methods, capability gating,
parser or response-dispatch changes, renderer exposure, background behavior, or
`QSH` support. `QSH` remains deferred for lack of exact `FRQ` evidence.

## Milestone 24.6 implementation boundary

Milestone 24.5 completed and merged the specification-backed `URC` slice. This
first narrow Milestone 24.6 slice implements only exact
`AST,CURRENT_ACTIVITY,[Site Index]` and `AST,LCN_MONITOR,[Site Index]` starts,
lossless `AST` XML framing and modeling, and the one documented combined
`APR,[Analize Mode]` pause/resume operation with exact `APR,OK`
acknowledgement. The public `AnalysisMode` grammar contains the six exact APR
tokens documented by V2.00, but that enumeration does not implement the other
modes' runtime protocols.

The specification establishes repeated Current Activity and LCN Monitor XML,
approximately 200 ms and one-second transmission intervals respectively, and
the exact spelling `ReceiveStaus` in the reviewed example. The implementation
preserves attributes and unknown/repeated descendant records without assigning
semantic meaning. Synthetic replay delays are structural test data and are not
physical cadence measurements. The specification also advises entering Scan
Mode before AST loads hpdb data; this slice does not automate that mode change.

The reviewed section titled “Analize Pauze/Resume” supplies one APR wire form.
It does not supply distinct pause, resume, `AST,STOP`, `APR,STOP`, or other stop
forms. Therefore separate pause/resume/stop APIs, local toggle state, and
termination behavior are deliberately absent. Treating AST as a persistent
response family and using the ordinary correlation path for its first response
is an architectural inference from the recurring-response shape, not a claimed
scanner lifecycle or termination fact.

The second narrow Milestone 24.6 slice adds only local bounded AST publication
and subscription ownership for already-parsed `AnalysisResponse` values. One
radio-owned publisher assigns globally ordered sequence numbers and fans each
response into isolated bounded consumer queues. Each consumer independently
drops its oldest unread response on overflow and exposes cumulative drop and
overflow accounting. Explicit local subscription close removes and wakes that
consumer; radio close locally closes the publisher and wakes all remaining
blocked consumers. These operations issue no new scanner command.

This remains short of a full analysis-session owner. The evidence still supplies
no documented `AST,STOP` or `APR,STOP`, and no distinct pause and resume wire
forms. Local subscription or publisher close is not a claim that scanner
analysis has terminated, and no running, paused, or stopped state is inferred.
There is no reconnect restoration, transport/model/firmware applicability
claim, new mode runtime, or physical scanner validation claim. Activity Log,
LCN Finder, Band Scope, Raw Data Output, System Status, RF Power Plot, and
broader session ownership remain deferred. `QSH` remains blocked. No UDP
expectation, retry, bare-XML correlation, transport recovery, or Scan Mode
behavior changes in this slice.

## Milestone 24.7 implementation boundary

Milestone 24.6 completed the narrow AST/APR foundation without inventing stop
wires or authoritative analysis-session state. The first narrow Milestone 24.7
slice is receive-only: it models already-received `PWF` and `GWF` line records
and gives typed waterfall data bounded local publication/subscription ownership.
It sends no waterfall command.

`PWF` preserves every received positional field exactly as a string, including
empty and unknown values, and retains the complete source `Packet`. No fixed PWF
arity or FFT value semantics are inferred. `GWF` is promoted to a typed data
response only for the reviewed shape of exactly 240 FFT fields; those fields
likewise remain uninterpreted strings with the source packet preserved. Other
GWF line shapes remain generic lossless packets so this slice does not invent
acknowledgement, error, or malformed-data semantics that the reviewed evidence
does not establish.

A synthetic receive-only fixture covers variable PWF shape, an empty field, an
unknown value, and one exact 240-field GWF data record. It is
specification-derived structural evidence rather than physical capture evidence.

One radio-owned waterfall publisher assigns globally ordered local sequence
numbers and fans typed PWF/GWF responses into isolated bounded queues. Consumer
overflow drops only that consumer's oldest unread response and records
cumulative loss. Subscription close and radio close are local ownership
operations only; they send no scanner wire and do not claim scanner-side
waterfall termination.

The reviewed control notation `PWF,[FFT_TYPE],[ON/OFF]` and
`GWF,[TYPE],[ON/OFF]` is not promoted to a command API in this slice. No
running/stopped state, start/stop behavior, reconnect restoration, cadence,
numeric FFT scale, transport applicability, model/firmware support, or renderer
integration is inferred. `GW2` remains deferred: V2.00 describes it as a
binary/no-separator form, while the current serial and UDP control receive paths
decode bytes to text before radio dispatch. Preserving GW2 therefore requires a
separate evidence-backed binary transport/framing contract rather than forcing
binary data through the line parser.

## Evidence policy

Material claims must identify their strongest evidence as specification,
synthetic fixture, sanitized capture, physical observation, inference, or
unknown. Specification revision, model, firmware, and transport applicability
must be explicit when known and marked unknown otherwise. A model-level
validation label is not evidence that every command was exercised.

Synthetic fixtures are the default. Hardware-derived evidence must be sanitized
without losing protocol structure. Raw scanner programming and user data must
not be committed. Inferences may guide planning but must not be presented as
wire-format or firmware facts.

## Reviewed specification baseline

The primary reviewed source is Uniden's *SDS Series Remote Command
Specification* V2.00, dated 2025-07-07. Its header covers SDS100 (UB383Z),
SDS200 (UB384Z), and SDS150 (UB391Z). The earlier reviewed SDS200 specification
is V1.02, dated 2023-12-22.

The V2.00 history records 0.01 on 2018-04-13, 1.01 adding Waterfall on
2023-12-11, 1.02 adding `GST` on 2023-12-22, 1.03 adding `POF`/`GCS` on
2024-12-04, and 2.00 adding `GW2`/`KAL` on 2025-07-07. The reviewed V2.00
command list includes the existing core commands and `AST`, `APR`, `URC`, menu,
waterfall, `POF`, and other advanced commands discussed here. V2.00 is the
current baseline; V1.02 remains useful earlier SDS200 evidence, not a competing
current definition.

The specifications' model coverage does not establish exact per-command
firmware availability. Repository documentation records physical validation of
SDS200 USB, Ethernet control, and RTSP/RTP and SDS100 core USB behavior on
firmware 1.26.01; SDS150 is specification-only. Those statements do not provide
physical validation for the advanced commands below. Their precise firmware
applicability is unknown unless separately stated.

## Repository protocol architecture

### Ordinary command/response path

`Command[T]` provides `wire`, `response_command`, and one
`parse_response(response)` operation. `SDSScanner` serializes execution with a
command lock, installs one pending queue keyed by response command, and awaits
one object. Generic line parsing normalizes the command while preserving every
remaining positional field and the raw line. Typed parsing specializes known
commands and otherwise returns the lossless `Packet`.

This path fits ordinary one-response line reads and controls. Read and mutation
safety surfaces must remain distinct even when a wire command, such as `FQK`,
supports both. The acknowledgement parser accepts `OK` and rejects
`NG`/`ERR`/`ERROR`, but operation-specific errors such as `URC` require explicit
modeling. Persistent streams and commands with no response do not fit the
current contract.

### ScannerInfo XML path

Before Milestone 24.2, `XmlResponseAssembler` recognized only `GSI,<XML>` and
`PSI,<XML>` and completed only at `</ScannerInfo>`. `ScannerInfoParser` requires
a `ScannerInfo` root. `ScannerNode` preserves every XML attribute and
`ScannerInfo` preserves raw XML. Milestone 24.2 generalizes the assembler around
the explicit command/root definition described above while preserving this
existing behavior.

### SDS200 UDP XML reconstruction

`UdpDatagramDecoder` reconstructs numbered XML fragments using `Foot`/`Footer`,
`No`, and `EOT` after a command is recognized. Milestone 24.2 extended command
expectation and retry bookkeeping from the existing GSI/PSI paths to exact
`GLT,FL`, while reconstruction remains a transport concern and GLT domain
semantics remain above `network.py`. Milestone 24.8's fifth slice now reuses
that same one-shot bounded-XML machinery for exact `MSI`, without changing MSI
domain parsing or claiming physical UDP support.

### Existing stream lifecycle

PSI already has explicit start, stop, renewal, reconnect, and restoration logic
outside an ordinary one-response command. It demonstrates the need for a
session owner, but its cadence and ScannerInfo framing must not be assumed to
describe analysis or waterfall sessions.

## Protocol lifecycle classification

The advanced protocol uses six framing and lifecycle classes:

1. ordinary single-response line read;
2. ordinary single-response line mutation/control;
3. bounded multiline XML retrieval;
4. persistent push/session protocol;
5. no-response command; and
6. disruptive/recovery operation.

An operation may expose more than one surface: `FQK` has distinct line read and
write forms, menu work combines bounded XML and controls, and `APR` controls a
persistent analysis session. Later slices must state the class they implement
and satisfy its framing, ownership, cancellation, error, and recovery needs.

## Operation evidence ledger

Reviewed V2.00 names SDS100, SDS200, and SDS150 in its document scope. That
header does not establish per-command model applicability: applicability is
unknown unless the reviewed command section, release evidence, fixture/capture
evidence, or physical observation establishes it. Exact firmware and transport
behavior is likewise unknown unless separately established. “Likely
abstraction” is a planning inference, not established protocol behavior.

### GLT

- **Evidence:** V2.00 specification, 2025-07-07; status: specification.
- **Shape:** `GLT,FL\r` is a reviewed request example. The bounded response is
  `GLT,<XML>,\r` followed by XML rooted at `<GLT>`. Reviewed keywords include
  `FL`, `SYS`, `DEPT`, `SITE`, `CFREQ`, `TGID`, `STGID`, `SFREQ`, `ATGID`,
  `AFREQ`, `CC`, `WX`, `FTO`, `SWS_FREQ`, `CCHIT`, `CS_BANK`, `CS_FREQ`, and
  `QS_FREQ`, plus additional documented records. A reviewed record is
  `<FL Index="0" Name="Favorites List 1" Monitor="On" Q_Key="1" N_Tag="None" />`.
- **Lifecycle and safety:** bounded multiline XML read; retrieval-oriented and
  non-mutating. Preconditions, operation errors, exact transport restrictions,
  and per-model firmware availability are unknown.
- **Repository fit:** generalized bounded-XML assembler/correlation and a
  lossless GLT model above transport reconstruction; synthetic XML fixtures
  should cover all reviewed record keywords, unknown elements/attributes,
  empty and multiline documents, and truncation/reordering faults.
- **Uncertainty:** detailed semantics and model-specific availability require
  more evidence. SAS means Sub Audio Setting and encompasses documented
  CTCSS/DCS/P25 NAC/Color Code/RAN/Area concepts; it must not be narrowed by
  assumption.

### FQK

- **Evidence:** V2.00 specification; status: specification.
- **Shape:** read `FQK\r` returns `FQK,[S0]...[S99]\r`; write uses the same 100
  status positions and returns `FQK,OK\r`. Status `0` is nonexistent, `1`
  exists disabled, and `2` exists enabled; controller status `0` is ignored
  when setting.
- **Lifecycle and safety:** ordinary line read plus ordinary state-changing
  mutation. Preconditions, errors, transport restrictions, and firmware detail
  are unknown. A later API must keep inspection separate from mutation.
- **Repository fit:** ordinary correlation can likely carry each form, with
  exact arity/value preservation and conservative mutation validation. Fixtures
  need all three states, 100 positions, malformed/extra fields, acknowledgements,
  and generic rejection.

### QSH

- **Evidence:** V2.00 specification; status: specification. The QSH-looking
  value in `tests/test_replay.py` is literal redaction data, not implementation
  evidence.
- **Shape:** controller `QSH,[FRQ]\r`; acknowledgement `QSH,OK\r`.
- **Lifecycle and safety:** ordinary state-changing control. Documented invalid
  contexts include Menu Mode, Direct Entry, and Quick Save. Exact frequency
  syntax, model/firmware distinctions, transport restrictions, and detailed
  errors need fixture or physical evidence before support.
- **Repository fit:** likely ordinary acknowledgement command with explicit
  validation and context-safe failure. Fixtures need acknowledgement,
  rejection, and invalid-context cases without asserting undocumented syntax.

### URC

- **Evidence:** V2.00 specification; status: specification.
- **Shape:** `URC\r` returns `URC,[STATUS]\r`; `URC,[STATUS]\r` returns
  `URC,OK\r`; status `0` stops and `1` starts. Errors are
  `URC,ERR,[ERROR CODE]\r`: `0001 FILE ACCESS`, `0002 LOW BATTERY`, `0003
  SESSION OVER LIMIT`, and `0004 RTC LOST`.
- **Lifecycle and safety:** state-changing recording control with a separate
  read surface and operation-specific errors. Storage/session preconditions are
  material; transport restrictions and firmware detail are unknown.
- **Repository fit:** ordinary line correlation plus typed status,
  acknowledgement, and stable redacted error mapping. Fixtures need every
  reviewed error and unknown error preservation.

### AST and APR

- **Evidence:** V2.00 specification; status: specification.
- **Shape:** `AST` includes recurring `CURRENT_ACTIVITY` XML at approximately
  200 ms, `LCN_MONITOR` XML at approximately one second, `ACTIVITY_LOG` line
  records, LCN Finder XML at approximately 500 ms, and Band Scope data tied to
  frequency changes with a documented 10 ms interval. Raw Data Output has an
  A/D discriminator and is USB-only; that analysis output must be paused before
  other remote commands may be issued. System Status and RF Power Plot are
  session-oriented analysis modes.
  `APR` pauses/resumes documented modes including `SYSTEM_STATUS`,
  `RF_POWER_PLOT`, `CURRENT_ACTIVITY`, `LCN_MONITOR`, `ACTIVITY_LOG`, and
  `RAW_DATA_OUTPUT`.
- **Lifecycle and safety:** persistent push/session protocol with `APR` as the
  documented combined pause/resume control. Bounded ownership, cancellation,
  disconnect recovery, and mode-aware errors are future architectural needs
  inferred from the reviewed behavior. The reviewed section supplies no
  explicit stop wire or separate pause and resume forms.
- **Repository fit:** a new analysis-session abstraction above transport, not
  repeated ordinary commands. Synthetic fixtures should separately represent
  each established framing/cadence shape, pause/resume, interleaving, unknown
  records, disconnects, and truncated XML. Detailed payload fields and
  model/firmware support remain unknown where not reviewed.

### PWF, GWF, and GW2

- **Evidence:** V2.00 specification and its history; status: specification.
- **Shape:** `PWF,[FFT_TYPE],[ON/OFF]\r` pushes
  `PWF,[DATA1]...[DATA_n]\r`. `GWF,[TYPE],[ON/OFF]\r` has a reviewed response
  containing 240 FFT values. `GW2`, added in V2.00, is a binary/no-separator
  waterfall form.
- **Lifecycle and safety:** persistent waterfall session/data, not ordinary
  request/response. Exact binary framing, shutdown/error behavior, transport
  applicability, model support, and firmware thresholds remain unresolved.
- **Repository fit:** session ownership plus bounded line/binary data framing;
  `GW2` requires a transport contract capable of preserving binary records.
  Fixture targets include start/stop, 240-value GWF records, PWF variability,
  unknown values, truncation, and structurally faithful GW2 binary samples.
  Implementation is deferred beyond 24.1.

### MNU, MSI, MSV, and MSB

- **Evidence:** official SDS100/SDS200 Remote Command Specification V1.02,
  dated 2023-12-22, compared with official SDS Series Remote Command
  Specification V2.00, dated 2025-07-07; status: shared specification evidence
  for the forms described below.
- **Version comparison:** the MNU/MSI/MSV/MSB request and acknowledgement forms
  reviewed here are the same in V1.02 and V2.00. Future advanced-protocol work
  must continue comparing overlapping commands rather than treating V2.00 as an
  automatic replacement for V1.02; version-specific additions or changed fields
  must retain their own provenance.
- **Shape:** both specifications show `MNU,[MENU_ID],[INDEX]\r` returning
  `MNU,OK`. The common menu table contains `TOP`, `MONITOR_LIST`,
  `SCAN_SYSTEM`, `SCAN_DEPARTMENT`, `SCAN_SITE`, `SCAN_CHANNEL`, `SRCH_RANGE`,
  `SRCH_OPT`, `CC`, `CC_BAND`, `WX`, `FTO_CHANNEL`, `SETTINGS`, and
  `BRDCST_SCREEN`. Its INDEX column names System, Department, Site, Channel,
  Custom Bank, and FTO Channel indexes only for `SCAN_SYSTEM`,
  `SCAN_DEPARTMENT`, `SCAN_SITE`, `SCAN_CHANNEL`, `SRCH_RANGE`, and
  `FTO_CHANNEL`; the other reviewed rows show `-`.
  Both specifications show `MSI\r` returning bounded multiline XML rooted at
  `<MSI ...>`. Their MSI tables identically document root attributes `Name`
  (menu title), `Index` (menu index), `MenuType`
  (`TypeSelect`/`TypeInput`/`TypeLocation`/`TypeError`), `Value` (current set
  value), and `Selected` (listed without an additional value description).
  They identically document `MenuItem` attributes `Name`, `Index`, and `Value`;
  `MenuInput` attributes `MaxLength`, `EnableKeys`, and `AddedInformation`;
  `MenuLocation` attributes `MaxLength`, `EnableKeys`, and `IsLatitude`; and
  `MenuErrorMsg` attributes `Text` and `ScanButton`.
  Both specifications also show `MSV,[RSV],[VALUE]\r` returning `MSV,OK` and
  `MSB,[RSV],[RET_LEVEL]\r` returning `MSB,OK`. They describe MSV VALUE as a
  selected-item index for select menus or an input string for input menus, with
  commas in input values replaced by tabs. They preserve the exact
  `RETURN_PREVOUS_MODE` token for exiting menu mode and describe an empty
  RET_LEVEL as one level back. Neither reviewed SDS table establishes the
  serialized value of `[RSV]`.
- **Lifecycle and safety:** mixed bounded-XML retrieval and ordinary controls.
  Menu state is a precondition; index ranges/encoding, negative/error responses,
  detailed field semantics, physical transport behavior, and firmware
  applicability need more evidence.
- **Repository fit:** generalized bounded XML for MSI and separate conservative
  control commands. Fixtures need reviewed menu IDs, unknown fields, malformed
  XML, acknowledgement/rejection, and state-sensitive failures.

## Milestone 24.8 implementation boundary

The first Milestone 24.8 slice is receive/model/parser-only. `MsiParser` accepts
bounded XML only when the document root is exactly `MSI` and returns immutable
`MsiResponse`/`MsiRecord` values that preserve root attributes, every descendant
in source order, repeated and unknown elements, all attributes, and the raw XML.
No menu-field names or values are assigned semantics.

The generic `XmlResponseAssembler` is exercised with an explicit test-local
`{"MSI": "MSI"}` mapping. `MSI` is intentionally not added to the production
default XML command map, so the first slice does not imply UDP expectation,
retry, bare-XML, serial, replay, or any other transport support. Its synthetic
fixture remains receive-only structural evidence and contains no transmitted
`MSI` request.

The second narrow slice adds `GetMsi` with exact wire `MSI` and response command
`MSI`, plus `SDSScanner.get_msi()`. The radio constructs a local
`XmlResponseAssembler` mapping that extends the shared roots with `MSI -> MSI`,
dispatches completed MSI documents only through `MsiParser`, and publishes the
result without updating `RadioState`. Deterministic fake-serial and replay tests
exercise CR-delimited request/response flow and lossless result correlation.

`XML_COMMAND_ROOTS` itself remains unchanged, and `UdpDatagramDecoder` therefore
does not gain MSI bare-XML recognition, expected-command correlation, numbered
fragment handling, completion bookkeeping, or retry behavior. While holding the
scanner command lock, the serialized MSI request/response path rejects direct or
capture-wrapped `udp://` endpoints and all fallback transports before registering
a pending response or writing `MSI`. Network and fallback MSI framing remain
explicit later evidence/transport tasks rather than implicit consequences of the
high-level retrieval API.

The third narrow slice implements only the six MNU rows whose INDEX column
names a concrete index kind in both V1.02 and V2.00: `SCAN_SYSTEM`,
`SCAN_DEPARTMENT`, `SCAN_SITE`, `SCAN_CHANNEL`, `SRCH_RANGE`, and
`FTO_CHANNEL`. The command accepts only those exact MENU_ID tokens plus a
non-empty opaque string index, rejects leading/trailing whitespace and
comma/line-break injection as host-side safety constraints, preserves the index
without assigning a range or numeric encoding, and accepts only exact `MNU,OK`.

Synthetic replay uses deliberately fabricated zero-padded index-shaped tokens;
neither specification establishes those literal values. Rows whose INDEX column
is `-` remain deferred because the common table does not establish an exact
serialized empty/omitted-field form. Negative/error MNU replies are not
classified because no exact rejection shape is established by the reviewed
forms.

The fourth narrow slice adds only a documented read projection over the
lossless MSI model. `MsiMenuProjection` exposes the five documented root
attribute names and groups recognized descendants into immutable
`MsiMenuItem`, `MsiMenuInput`, `MsiMenuLocation`, and `MsiMenuErrorMessage`
values. Every named field remains an exact optional string: documented
number-like or boolean-like values are not coerced, ranges are not enforced on
received data, and the otherwise-undescribed `Selected` value is not assigned
new semantics. Each typed record retains its complete attribute mapping, while
the projection also retains the complete ordered `MsiRecord` tuple so unknown
and future descendants remain available. `MsiResponse` continues to retain the
original root mapping and raw XML.

A synthetic four-transaction replay fixture represents `TypeSelect`,
`TypeInput`, `TypeLocation`, and `TypeError` separately. Its strings are
structural test data, not claims about physically observed menu indexes, values,
limits, keys, or scanner states. `MSV` and `MSB` execution remains blocked:
V1.02 and V2.00 document their outer forms but do not establish the serialized
value of `[RSV]`, and the reviewed official RH-536HP predecessor source contains
no searchable MSV/MSB implementation that resolves it.

The fifth narrow slice promotes exact `MSI` into `XML_COMMAND_ROOTS` and the
existing SDS200 direct-UDP one-shot bounded-XML machinery. An exact bare `MSI`
request establishes the expectation; a nonexact `MSI,` or `MSI,...` request does
not. Matching bare or explicitly prefixed MSI XML is correlated by root,
numbered `Footer`/`Foot` fragments reuse the existing ordered reassembly path,
retryable sequence failures resend the exact original `MSI` wire, and successful
completion clears MSI one-shot retry state. `SDSScanner.get_msi()` is therefore
allowed on the repository's direct `UdpTransport`, including when wrapped by
capture recording.

The sixth narrow slice adds no production protocol behavior. It hardens the
existing direct-UDP contract with explicit behavioral regressions for root
correlation and one-shot retry cleanup: while `MSI` is expected, unrelated bare
`ScannerInfo`, `GLT`, or `AST` XML remains uncorrelated and does not consume the
MSI expectation; after a retryable MSI fragment gap and a subsequent successful
MSI completion, another stray MSI fragment gap cannot reuse the completed
request's automatic-retry authority. The latter assertion waits for a later
ordinary decoded response before checking the transmitted datagrams, avoiding a
negative-write race.

All UDP evidence in the fifth and sixth slices is deterministic fake-datagram
software evidence.
It does not establish physical SDS200 behavior, firmware availability, or a
broader transport guarantee. Fallback transports remain fail-closed because
their active transport can change and no fallback-wide MSI framing contract is
established. Custom controls that merely present an `udp://` endpoint are not
treated as evidence-equivalent to `UdpTransport`. Unindexed MNU, menu
lifecycle/state ownership, renderer exposure, model/firmware applicability,
physical transport applicability, and physical scanner validation remain
deferred.

### Richer NAC, RAN, color-code, area, activity, and quality data

The reviewed GLT glossary establishes SAS as a family encompassing CTCSS/DCS,
P25 NAC, Color Code, RAN, and Area concepts. Existing ScannerInfo models already
preserve raw XML and all attributes while exposing selected projections. This
area is a research and lossless-modeling target, not an established new wire
format. Evidence status for additional field names and semantics is unknown.
Later fixtures must include deliberate unknown attributes and values before any
typed interpretation is added.

### Discovery, system-status, and RF-power modes

The reviewed AST/APR evidence relates System Status and RF Power Plot to
analysis-session behavior, while conventional and trunking discovery remain
preserved research areas. Detailed record layouts, transitions, errors,
transport constraints, and model/firmware applicability are unknown. They must
be researched on the analysis substrate without inventing wire formats.

### KAL and POF adjacent architecture evidence

`KAL\r`, added in V2.00, is explicitly documented as having no response. It is
evidence that the current mandatory `response_command`/one-awaited-response
contract is incomplete for the protocol. Its framing class is no-response;
statefulness, safety purpose, transport restrictions, and firmware behavior
remain unknown. It is not implemented or scheduled by 24.1.

`POF\r` returns `POF,OK\r` and was added in the reviewed 1.03 history. It is an
official disruptive operation and therefore belongs to the
disruptive/recovery class even though it acknowledges. It is adjacent evidence,
not automatic justification for public support and not a substitute for the
separately reported reboot behavior.

### Reported MSM,1 reboot behavior

The preserved roadmap reports that `MSM,1` briefly enters mass-storage mode
before reboot. No `MSM` command or reboot reference was found in the reviewed
official V2.00 specification. Its evidence is therefore separately sourced,
high uncertainty, and not specification fact.

Milestone 24.1 prohibits live probing. Any later work requires independent
protocol and firmware validation, explicit operator intent, bounded outage
handling, and post-reboot control, PSI, and RTSP recovery checks. Its framing is
disruptive/recovery; model applicability beyond the reported SDS200 context,
wire response, errors, transports, and firmware range are unknown.

## Fixture and provenance convention

The existing replay schema remains unchanged: schema/version, endpoint,
TX/RX/connection events, and event timing. Advanced metadata belongs next to a
fixture or test rather than inside that schema. Each fixture set should record:

- synthetic or sanitized-capture provenance and the source/spec revision/date;
- scanner model, firmware, and transport when physically sourced;
- sanitization transforms and confirmation that no user programming remains;
- expected framing, lifecycle, cadence where relevant, and termination rule;
- deliberate unknown fields, elements, attributes, values, or records; and
- limitations, unverified assumptions, and expected parser/recovery behavior.

Fixtures must be protocol-complete for the behavior they represent. Use
synthetic provider values, deterministic fake transports, replay tests, fault
injection, and platform-independent timing. Sanitization must preserve field
count, delimiters, XML hierarchy, stream boundaries, binary length/structure,
and error shapes needed by the test. Physical validation remains separately
documented and never inferred from a fixture.

Representative targets are GLT bounded XML with all reviewed record families;
FQK read/write and malformed status vectors; QSH acknowledgements and invalid
contexts; all URC errors; MSI XML plus menu acknowledgements; each established
AST framing mode and lifecycle transition; PWF/GWF/GW2 session records; KAL
no-response timeout avoidance; and disruptive-operation recovery simulations.
No fixture target authorizes live disruptive discovery.

### Initial representative fixture set

The initial fixture-only slice adds
`tests/fixtures/advanced_protocol/synthetic-glt-fl.jsonl`, covering bounded-XML
framing, repeated ordered `FL` records, and one deliberate unknown attribute;
and `tests/fixtures/advanced_protocol/synthetic-fqk.jsonl`, covering
100-position read/write vectors and acknowledgement. Both are synthetic,
specification-based evidence only. QSH remains without a concrete fixture until
exact frequency syntax has stronger evidence. These fixtures validate evidence
structure, not command implementation or hardware support.

## Unknown-field preservation

Unknown preservation is a hard acceptance boundary. Line parsing must retain
every positional field, including blanks and extensions, plus the raw record.
XML must retain unknown elements, repeated elements, attributes, ordering where
semantically relevant, and raw XML sufficient for future re-evaluation. Binary
formats must retain exact bytes and framing metadata. Typed projections may add
meaning but must not discard or normalize the source evidence they project.

The current `ScannerNode` mapping loses repeated elements with the same tag at
the top-level `nodes` projection even though `raw_xml` preserves the source.
Future GLT and analysis models therefore need a structure that preserves
repetition and order in addition to raw XML; copying the current tag-keyed
projection alone would not meet this milestone's lossless requirement.

## Architectural conclusions

Ordinary single-response line reads and acknowledgements can build on
`Command`, provided reads and mutations remain distinct and operation-specific
errors are preserved. Bounded XML needs generalized command/root-aware assembly,
correlation, limits, resynchronization, and an intentional expansion of UDP
recognition/retry behavior. Persistent analysis and waterfall work needs a
session abstraction with ownership and recovery. KAL demonstrates the need for
a no-response execution path. Disruptive operations need a separately gated
recovery workflow rather than ordinary reconnect.

Milestone 24.1 selected GLT as the safest leading Milestone 24.2 candidate: it
is retrieval-oriented and bounded, complements the existing Favorites
foundation, and forces generalized bounded-XML framing to be solved before more
stateful protocols. Milestone 24.2 now implements only exact `GLT,FL`. Broader
GLT arguments and hierarchy forms remain deferred until their exact request and
response semantics have sufficient evidence. This remains an evidence-led
scope decision, not hardware validation or a promise of model/firmware support.

## Proposed Milestone 24 slicing

- 24.1: research, evidence ledger, and fixture/provenance foundation;
- 24.2: exact GLT Favorites-list (`GLT,FL`) retrieval and generalized
  bounded-XML framing;
- 24.3: completed FQK read/control; QSH remains deferred pending evidence for
  its exact FRQ representation;
- 24.4: ordered/repeated ScannerInfo preservation foundation before any
  evidence-backed richer NAC/RAN/color-code/area/activity/quality modeling;
- 24.5: URC scanner recording control;
- 24.6: AST/APR analysis-session foundation;
- 24.7: PWF/GWF/GW2 waterfall session and data work;
- 24.8: MNU/MSI/MSV/MSB menu operations; and
- later discovery, System Status, and RF Power Plot work on the analysis
  substrate.

Later numbering may move as physical evidence is collected. Reported `MSM,1`
reboot/recovery remains separately gated pending stronger evidence; `POF` is
adjacent official disruptive evidence and must not silently replace it.

## Milestone 24.1 acceptance criteria

- Every preserved Milestone 24 operation is accounted for.
- The V1.02/V2.00 evidence relationship and reviewed revision history are
  recorded.
- Provenance is explicit for every material claim.
- Model, firmware, and transport applicability is explicit where known and
  unknown where it is not.
- No disruptive live discovery or `MSM,1` probe is required or permitted.
- Unknown fields and exact binary/source evidence remain preservable.
- Each later slice states its framing and lifecycle prerequisite.
- The GLT-first planning conclusion is evidence-based and is not presented as
  hardware validation.
- Planned fixtures are protocol-complete, sanitized, deterministic, and keep
  the replay schema unchanged.
- Before milestone closure, normal repository-wide Ruff, MyPy, pytest,
  documentation, package-build, and Twine validation remains required; this
  research slice does not waive those release checks.

## Milestone 24.2 acceptance criteria

- Public retrieval sends exact `GLT,FL` only; no free-form GLT syntax is
  exposed.
- The typed response model losslessly preserves ordered and repeated records,
  unknown tags and attributes, root attributes, and complete raw XML.
- One shared internal command/root definition governs bounded CR-delimited
  assembly and UDP XML recognition and correlation.
- Serial/replay and UDP paths both support GLT while strict root correlation
  prevents GLT and ScannerInfo cross-claiming.
- UDP retries preserve the exact original `GLT,FL` wire command, and successful
  one-shot completion clears GLT expectation and retry state.
- Malformed supported XML and incomplete numbered sequences do not count as UDP
  document completion; malformed XML can still produce a `protocol_error`.
- Existing GSI and persistent PSI behavior remains compatible.
- The representative synthetic GLT fixture is reused, and deterministic fake
  UDP datagrams cover transport mechanics.
- Targeted and full repository validation remain required before closure.
- Physical GLT validation remains future evidence, not a closure prerequisite
  for this offline/synthetic implementation slice.
