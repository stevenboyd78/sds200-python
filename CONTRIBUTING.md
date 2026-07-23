# Contributing

Thank you for helping improve `sds200-python`.

The project is hardware-facing alpha software, so contributions should preserve
a clear separation between transport behavior, protocol parsing, scanner state,
and user interfaces.

## Before opening an issue

Search existing issues and review [SUPPORT.md](SUPPORT.md). Include the project
version, Python version, operating system, scanner firmware, connection type,
and a minimal reproduction.

Remove private or location-sensitive information from traces before posting.
Scanner output can contain system names, channel names, IP addresses, and unit
identifiers.

Security vulnerabilities should follow [SECURITY.md](SECURITY.md), not a public
issue.

## Development setup

```bash
git clone https://github.com/stevenboyd78/sds200-python.git
cd sds200-python

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Required checks

Run these before submitting a pull request:

```bash
ruff check .
mypy src/sds200
pytest
python scripts/check_docs.py
python -m build
python -m twine check dist/*
```

The automated test suite must run without scanner hardware.

## Project structure

- `src/sds200/transport.py`: transport contract and USB serial transport
- `src/sds200/network.py`: UDP transport and network XML datagram handling
- `src/sds200/commands.py`: typed command objects
- `src/sds200/parser.py`: CR-delimited response parsing
- `src/sds200/xml_protocol.py`: scanner-information XML parsing
- `src/sds200/state.py`: synchronized state and change detection
- `src/sds200/cli.py`: command-line interface
- `tests/`: hardware-independent regression tests
- `examples/`: focused usage examples
- `docs/`: architecture and operational guidance

## Adding protocol support

When adding or changing a scanner command:

1. Preserve the raw protocol response in a trace or sanitized fixture.
2. Add a typed command or response model when practical.
3. Keep transport-specific framing out of high-level command code.
4. Add positive, malformed-response, timeout, and validation tests.
5. Document hardware validation separately from simulated tests.
6. Avoid assuming that USB and UDP return identical framing.

Do not add tests that require a live scanner or a specific LAN.

## Hardware validation

A pull request may include optional manual validation notes:

```text
Scanner: SDS200
Firmware: 1.26.01
Python: 3.14.x
Transport: USB / UDP
Commands tested: ...
Observed result: ...
```

Sanitize system, department, channel, unit, and network information before
sharing logs publicly.

## Pull requests

Keep pull requests focused. Describe:

- What changed
- Why it changed
- Tests added or updated
- Local check results
- Hardware validation, when applicable
- Compatibility or security implications

Update documentation and `CHANGELOG.md` when behavior or public APIs change.

By participating, you agree to follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
