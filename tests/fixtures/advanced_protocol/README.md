# Advanced protocol fixtures

Every fixture in this directory is explicitly synthetic and is not derived from scanner hardware.
It contains no private scanner data. The reviewed source is the *Uniden SDS
Series Remote Command Specification V2.00*, dated 2025-07-07. No model-specific
or firmware-specific physical validation is implied. Endpoint values are fixture
identifiers, not real scanner addresses.

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
