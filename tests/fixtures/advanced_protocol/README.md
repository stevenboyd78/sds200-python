# Advanced protocol fixtures

Every fixture in this directory is explicitly synthetic and is not derived from scanner hardware.
It contains no private scanner data. Official protocol evidence is versioned:
the *SDS100/SDS200 Remote Command Specification V1.02*, dated 2023-12-22, and
the later *Uniden SDS Series Remote Command Specification V2.00*, dated
2025-07-07, are compared when a command appears in both documents. A fixture
must not silently treat V2.00 behavior as universal when the versions differ,
and later-version-only commands must retain their version provenance. No
model-specific or firmware-specific physical validation is implied. Endpoint
values are fixture identifiers, not real scanner addresses.

Unknown fields and elements are deliberately retained where present. These
fixtures model framing and evidence only; they do not establish public command
support or scanner runtime behavior. Exact transport-specific behavior remains
unverified unless separately documented.

- `synthetic-glt-fl.jsonl` asserts `GLT,FL` bounded multiline XML framing, two
  repeated `FL` records in source order, exact reviewed attributes, and lossless
  retention of one deliberate future attribute.
- `synthetic-fqk.jsonl` asserts an ordinary `FQK` line read with exactly 100
  reviewed status positions, a distinct 100-position write, and the exact
  `FQK,OK` acknowledgement.
- `synthetic-urc.jsonl` asserts the reviewed V2.00 `URC` status read, explicit
  start and stop writes, exact `URC,OK` acknowledgements, all four documented
  operation-error codes, and one deliberate unknown synthetic code. It is
  specification-derived replay evidence only; no event was captured from
  scanner hardware.
- `synthetic-ast-apr.jsonl` is explicitly synthetic, is based on the reviewed
  Uniden SDS Series Remote Command Specification V2.00 dated 2025-07-07, and is
  not derived from hardware. It covers exact Current Activity and LCN Monitor
  start wires, structural CR-line-compatible AST XML framing, ordered repeated
  and unknown-field preservation, and exact combined APR pause/resume wires and
  acknowledgements. Its zero-delay events are deterministic structural replay
  evidence, not physical timing, model, firmware, transport, or termination
  validation.
- `synthetic-pwf-gwf.jsonl` is receive-only synthetic framing evidence for the
  first Milestone 24.7 slice. It preserves variable-length PWF fields including
  an empty field and deliberate unknown value, plus one GWF record with exactly
  240 uninterpreted FFT fields. It contains no start/stop transmission and does
  not establish ON/OFF token semantics, GW2 binary framing, transport behavior,
  model/firmware applicability, cadence, termination, or physical validation.
- `synthetic-msi.jsonl` is receive-only synthetic bounded-XML evidence for the
  first Milestone 24.8 slice. It proves only the reviewed `<MSI ...>` root and
  lossless structural preservation using deliberately synthetic unknown
  elements and attributes. It contains no MSI command transmission, does not
  register MSI in the default XML command map, and does not establish menu field
  semantics, transport behavior, model/firmware applicability, menu lifecycle,
  or MNU, MSV, or MSB control behavior.
- `synthetic-msi-retrieval.jsonl` adds deterministic replay evidence for the
  second narrow Milestone 24.8 slice: exact `MSI` request transmission followed
  by the reviewed bounded `<MSI ...>` XML shape with the same deliberately
  unknown/repeated structural data. It establishes only software command
  correlation plus CR-line/replay integration. The shared UDP XML command map
  remains unchanged, so this fixture does not establish UDP expectation, retry,
  fragment, or bare-XML behavior; it also does not establish menu field
  semantics, physical scanner/model/firmware applicability, menu lifecycle, or
  MNU, MSV, or MSB control behavior.
- `synthetic-mnu-indexed.jsonl` is deterministic synthetic replay evidence for
  the third narrow Milestone 24.8 slice. The V1.02 and V2.00 official command
  tables agree on the exact `MNU,[MENU_ID],[INDEX]` request, `MNU,OK`
  acknowledgement, and the six rows whose INDEX column names a concrete index
  kind: `SCAN_SYSTEM`, `SCAN_DEPARTMENT`, `SCAN_SITE`, `SCAN_CHANNEL`,
  `SRCH_RANGE`, and `FTO_CHANNEL`. The zero-padded index strings in this fixture
  are deliberately fabricated opaque tokens; neither specification establishes
  those literal values, an index range, or a numeric encoding. Unindexed MNU
  rows, negative/error responses, menu lifecycle/state semantics, MSV/MSB
  execution, transport/model/firmware applicability, renderer exposure, and
  physical scanner validation remain deferred.
- `synthetic-msi-menu-projection.jsonl` is deterministic synthetic replay
  evidence for the fourth narrow Milestone 24.8 slice. V1.02 and V2.00 have the
  same MSI attribute table for the root plus `MenuItem`, `MenuInput`,
  `MenuLocation`, and `MenuErrorMsg`. Four separate transactions represent the
  documented `TypeSelect`, `TypeInput`, `TypeLocation`, and `TypeError` shapes.
  All values are deliberately synthetic exact strings, including index-like,
  length-like, latitude-like, selected, and scan-button values; the fixture does
  not establish numeric/boolean coercion, runtime ranges, or physically valid
  scanner menu state. Deliberate future attributes/elements verify that the
  typed projection does not replace lossless preservation. It adds no new wire
  command beyond existing MSI retrieval and does not establish MSV/MSB reserved
  field serialization, menu mutation/lifecycle semantics, UDP support,
  model/firmware applicability, renderer exposure, or physical validation.
- The fifth narrow Milestone 24.8 slice adds no new fixture file. Instead,
  deterministic fake SDS200 UDP datagrams reuse the existing synthetic MSI
  shapes to validate software-only one-shot expectation, bare/prefixed root
  correlation, numbered fragment reassembly, exact-wire retry, capture-wrapper
  behavior, and state-neutral typed retrieval. This does not convert synthetic
  transport tests into physical scanner, firmware, or model evidence. Fallback
  MSI, unindexed MNU, MSV/MSB execution, menu lifecycle, and renderer behavior
  remain outside the slice.
