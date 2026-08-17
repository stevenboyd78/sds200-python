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
