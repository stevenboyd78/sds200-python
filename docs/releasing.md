# Release process

This checklist prepares the first GitHub prerelease, `v0.5.3`.

## 1. Prepare the repository

- Confirm the default branch is clean and current.
- Confirm `pyproject.toml` and `sds200.__version__` both contain `0.5.3`.
- Update `CHANGELOG.md`.
- Verify README examples against the current CLI.
- Confirm no traces, scanner identifiers, private IP details, or credentials
  were committed accidentally.
- Update the GitHub repository About description to:

  > Python control and monitoring for the Uniden SDS200 over USB serial and Ethernet.

- Suggested repository topics:
  `uniden`, `sds200`, `radio-scanner`, `python`, `serial`, `udp`.

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
python -m zipfile -l dist/sds200-0.5.3-py3-none-any.whl
```

Confirm it contains:

- `sds200/`
- `sds200/py.typed`
- Package metadata
- The MIT license

## 3. Hardware smoke tests

Run over USB:

```bash
sds200 info
sds200 scanner-info
sds200 monitor
```

Run over Ethernet:

```bash
sds200 --host SCANNER_IP info
sds200 --host SCANNER_IP scanner-info
sds200 --host SCANNER_IP monitor
sds200 discover --network SCANNER_SUBNET --network-only
```

Check profile and health paths:

```bash
sds200 profile list
sds200 --profile PROFILE health
```

Record the scanner firmware, Python version, operating system, and transports
tested in the release notes. Do not publish private channel or network data.

## 4. Create the tag

```bash
git switch main
git pull --ff-only
git status
git tag -a v0.5.3 -m "sds200-python v0.5.3"
git push origin v0.5.3
```

## 5. Create the GitHub release

- Create a release from tag `v0.5.3`.
- Title it `sds200-python v0.5.3`.
- Mark it as a **pre-release**.
- Use the `0.5.3` section of `CHANGELOG.md` as the starting release notes.
- State that the API is alpha and may change before 1.0.
- Include the tested scanner firmware and transports.
- Attach the wheel and source distribution from `dist/` if desired.

## 6. Optional package-index validation

Before publishing to PyPI, verify ownership of the `sds200` package name and
upload to TestPyPI first. Do not publish automatically from a local workstation
until trusted publishing and release provenance are configured.

After release, verify installation in a clean environment and update the
installation instructions when a package-index release becomes available.
