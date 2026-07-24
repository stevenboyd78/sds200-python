# Release process

This checklist prepares a GitHub prerelease. Set `VERSION` to the intended package version before starting.

## 1. Prepare the repository

- Confirm the default branch is clean and current.
- Confirm `pyproject.toml` and `sds200.__version__` both contain the intended release version.
- Update `CHANGELOG.md`.
- Verify README examples against the current CLI.
- Confirm no traces, scanner identifiers, private IP details, or credentials
  were committed accidentally.
- Update the GitHub repository About description to:

  > Python control for Uniden SDS100, SDS150, and SDS200 scanners over USB and SDS200 Ethernet.

- Suggested repository topics:
  `uniden`, `sds100`, `sds150`, `sds200`, `radio-scanner`, `python`, `serial`, `udp`.

## 2. Run validation

```bash
python -m pip install -e ".[dev]"

ruff check .
mypy src/sds200
pytest
python scripts/check_docs.py

rm -rf build dist
python -m build
python -m twine check dist/*
```

Inspect the built wheel:

```bash
python -m zipfile -l dist/sds200-VERSION-py3-none-any.whl
```

Confirm it contains:

- `sds200/`
- `sds200/py.typed`
- Package metadata
- The MIT license

## 3. Hardware smoke tests

Run over USB for each available model:

```bash
sds200 --model SDS100 info
sds200 --model SDS150 info
sds200 --model SDS200 info
sds200 scanner-info
sds200 monitor
```

For an SDS100 or SDS150, also run `sds200 --model MODEL battery` and verify the
reported charge fields are plausible.

Run over SDS200 Ethernet:

```bash
sds200 --host SCANNER_IP info
sds200 --host SCANNER_IP scanner-info
sds200 --host SCANNER_IP monitor
sds200 discover --network SCANNER_SUBNET --network-only
```

Check profile and health paths:

```bash
sds200 profile list
sds200 profile repair PROFILE --network SCANNER_SUBNET --dry-run
sds200 --profile PROFILE health --history
sds200 --profile PROFILE events --json
```

For a long-running reliability check, leave `events --json` and
`health --watch 5 --history --json` running while disconnecting and restoring
USB and Ethernet in turn. Confirm backoff, failover, PSI restart, and clean
shutdown behavior.

Record the scanner model, firmware, Python version, operating system, and
transports tested in the release notes. Do not publish private channel or network data.

## 4. Create the tag

```bash
git switch main
git pull --ff-only
git status
git tag -a vVERSION -m "sds200-python vVERSION"
git push origin vVERSION
```

## 5. Create the GitHub release

- Create a release from tag `vVERSION`.
- Title it `sds200-python vVERSION`.
- Mark it as a **pre-release**.
- Use the matching version section of `CHANGELOG.md` as the starting release notes.
- State that the API is alpha and may change before 1.0.
- Include the tested scanner firmware and transports.
- Attach the wheel and source distribution from `dist/` if desired.

## 6. Optional package-index validation

Before publishing to PyPI, verify ownership of the `sds200` package name and
upload to TestPyPI first. Do not publish automatically from a local workstation
until trusted publishing and release provenance are configured.

After release, verify installation in a clean environment and update the
installation instructions when a package-index release becomes available.
