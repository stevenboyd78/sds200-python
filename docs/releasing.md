# Release process

This checklist prepares a GitHub release. Set `VERSION` to the intended package
version before starting.

## 1. Prepare the repository

- Confirm the default branch is clean and current.
- Confirm `pyproject.toml` and `sds200.__version__` both contain the intended release version.
- Confirm `home-assistant/sds200/config.yaml` contains that same release version.
- Confirm the Home Assistant App changelog contains that release version.
- Confirm `sdsctl -V` and `sdsctl --version` report that same version.
- Update `CHANGELOG.md` and leave a fresh `Unreleased` section.
- Audit every repository Markdown file for stale release, milestone, installation,
  security, and deferred-feature wording.
- Update reviewed wiki source under `wiki/` whenever a user-facing workflow changed.
- Verify README examples against the current CLI.
- Confirm no traces, scanner identifiers, private IP details, or credentials
  were committed accidentally.
- Update the GitHub repository About description to:

  > Python control for Uniden SDS100, SDS150, and SDS200 scanners over USB and SDS200 Ethernet.

- Suggested repository topics:
  `uniden`, `sds100`, `sds150`, `sds200`, `radio-scanner`, `python`, `serial`, `udp`.

Before release validation, run a semantic search for stale version and feature
language in addition to the normal broken-link checker. Historical changelog and
roadmap references may remain when they accurately describe older releases.

## 2. Run validation

```bash
python -m pip install -e ".[dev]"

ruff check .
mypy src/sds200
pytest
python scripts/check_docs.py
git diff --check

python -m pytest -q tests/test_home_assistant_app_packaging.py

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
sdsctl --model SDS100 info
sdsctl --model SDS150 info
sdsctl --model SDS200 info
sdsctl scanner-info
sdsctl monitor
```

For an SDS100, run `sdsctl --model SDS100 battery` and verify it reports the
optional GSI value or `unavailable` without sending `GCS`. For an SDS150, run
`sdsctl --model SDS150 battery` and verify the detailed charge fields are plausible.

Run over SDS200 Ethernet:

```bash
sdsctl --host SCANNER_IP info
sdsctl --host SCANNER_IP scanner-info
sdsctl --host SCANNER_IP monitor
sdsctl discover --network SCANNER_SUBNET --network-only
```

For a v0.15 or later release, run the Textual interface on the intended Raspberry
Pi or workstation terminal, including an opt-in audio destination:

```bash
rm -f /tmp/sds200-tui-release.wav
sdsctl --host SCANNER_IP tui \
  --audio-output /tmp/sds200-tui-release.wav
```

Verify the compact 64 by 20 and standard 80 by 24 layouts, press `?` to open and
close keyboard help, switch themes with `T`, reconnect with `C`, and smoke-test
hold, navigation, volume, and squelch controls. Press `R` to start and stop a
recording, confirm live audio and RTP counters update without delaying scanner
controls, and verify the finalized file is 8 kHz mono signed 16-bit PCM. Repeat
while quitting with `Q` during an active recording and confirm the WAV is finalized
without leaving PSI, control, or audio threads running. Remove the temporary file
after validation.

Exercise automatic stale-PSI recovery with persistent logging. Use accelerated
thresholds for the test, temporarily block inbound UDP control replies from the
scanner, and then restore them:

```bash
rm -f /tmp/sdsctl-recovery.log /tmp/sds200-recovery-test.wav
sdsctl --log-level INFO --log-file /tmp/sdsctl-recovery.log \
  --host SCANNER_IP tui \
  --stale-after 3 \
  --psi-recover-after 5 \
  --psi-recovery-cooldown 10 \
  --audio-output /tmp/sds200-recovery-test.wav
```

While recording, block only authorized test traffic using the host firewall.
Confirm the TUI reports stale PSI and rate-limited recovery failures while audio
packet and sample totals continue increasing. Remove the rule, then confirm a later
attempt restores live PSI without pressing `C` or restarting audio. The log must
show every reconnect retaining the configured PSI interval, followed by
`PSI stream recovered`. Verify the WAV is 8 kHz mono signed 16-bit PCM, the
firewall rule is removed, and no `sdsctl` process remains after exit. Remove the
temporary files after recording the release evidence.

Record and play a native WAV file, then run the five-minute audio soak:

```bash
sdsctl --host SCANNER_IP audio \
  --output /tmp/sds200-release-audio.wav \
  --duration 30 \
  --force

sdsctl --host SCANNER_IP audio \
  --output /tmp/sds200-release-audio-soak.wav \
  --duration 300 \
  --force
```

Confirm both WAV files are 8 kHz mono signed 16-bit PCM and play successfully.
For the soak, inspect packet loss, duplicate, late, malformed, and timestamp
counters. Record all nonzero values in the release notes and investigate them
before publishing. Remove the temporary audio files after validation.

Check profile and health paths:

```bash
sdsctl profile list
sdsctl profile repair PROFILE --network SCANNER_SUBNET --dry-run
sdsctl --profile PROFILE health --history
sdsctl --profile PROFILE events --json
sdsctl --profile PROFILE --recover-preferred health
```

For a fallback profile, test preferred recovery in both directions when the
SDS200 USB and Ethernet endpoints are available. Start with one preferred
endpoint unavailable, confirm fallback activation, restore it, and verify two
validated `MDL` probes precede a seamless recovery. Repeat with the opposite
preference. Confirm an active PSI stream resumes after promotion.

For a long-running reliability check, leave `events --json` and
`health --watch 5 --history --json` running while disconnecting and restoring
USB and Ethernet in turn. Confirm backoff, failover, preferred recovery, anti-flapping behavior, PSI restart, and clean
shutdown behavior.

Record the scanner model, firmware, Python version, operating system,
transports tested, audio soak duration, packet count, sample count, and RTP
reliability counters in the release notes. Do not publish private channel,
recorded audio, or network data.

## 4. Publish reviewed wiki source

If the release changes files under `wiki/`, merge the release-preparation pull
request first, then publish those reviewed files to the separate GitHub Wiki
repository using [Publishing the Wiki](../wiki/Publishing.md).

Verify that the published Home page, sidebar, installation guide, and
troubleshooting guide match the merged repository source before creating the
release tag.

## 5. Tag and publish through release workflows

The `pypi` GitHub environment and PyPI Trusted Publisher must match
`.github/workflows/release.yml`. No long-lived PyPI token is stored in the
repository.

For releases that contain the Home Assistant App,
`.github/workflows/home-assistant-app-image.yml` must also be present on the
tagged commit. The App version, package version, and `vVERSION` tag must match.

```bash
git switch main
git pull --ff-only
git status
git tag -a vVERSION -m "sdsctl vVERSION"
git push origin vVERSION
```

The tag starts both release paths:

- the Python release workflow verifies the tag, runs the release checks, builds
  the distributions, and publishes them to PyPI through GitHub OIDC; and
- the Home Assistant App image workflow verifies that the package and App
  versions match the tag, publishes amd64 and aarch64 GHCR images, and creates
  the generic multi-architecture image manifest.

Wait for both workflows to pass before creating the GitHub release.

## 6. Verify Home Assistant repository installation

Before creating the GitHub release, validate the public Home Assistant
distribution path on Home Assistant OS.

1. Confirm the tagged amd64 and aarch64 images and generic multi-architecture
   GHCR image were published.
2. Confirm the image can be pulled without repository-development credentials.
3. In Home Assistant, open **Settings > Apps > App store**, open the top-right
   three-dot menu, choose **Repositories**, and add
   `https://github.com/stevenboyd78/sdsctl`.
4. Confirm the **sds200** App appears as repository-managed rather than Local and
   shows the release version and matching documentation.
5. Install or upgrade the repository App, set `scanner_host`, and start it.
6. Confirm the Configuration page renders the translated scanner host, MQTT topic
   prefix, and recording-directory names/descriptions; verify the default
   recording directory resolves below `/media`.
7. Confirm `/local/sds200/sds200-card.js` is available, register it as a
   JavaScript Module when needed, and verify **SDS200 Scanner** appears in the
   card picker with a working graphical editor and read-only state rendering.
8. Confirm the discovered SDS200 device exposes seventeen components: ten
   state/diagnostic components plus four Hold switches and Previous Channel,
   Next Channel, and Reconnect Scanner buttons.
9. Exercise all four Hold scopes when meaningful, Previous and Next with a valid
   current channel selection, and Reconnect Scanner. Confirm Home Assistant state
   remains authoritative after each action and the App does not enable the
   generic `<prefix>/commands` request-envelope input.
10. Validate Ingress loading, live scanner state, browser audio, recording, saved
    playback, App restart, and continued single-owner scanner/PSI/RTSP-RTP/control
    behavior.
11. Confirm recordings expected to persist across repository App restart or
    upgrade are still available.
12. Record the Home Assistant OS/Supervisor version and SDS200 firmware used for
    the smoke test in the release evidence.

Do not assume data from a previously staged Local App belongs to the
repository-managed App. Preserve any recordings or configuration needed from
the development installation before replacing it.

## 7. Create the GitHub release

- Create a release from tag `vVERSION`.
- Title it `sdsctl vVERSION`.
- For a normal versioned release, leave **pre-release** and **draft** unchecked.
- Mark a release as a pre-release only when the version is intentionally being
  published for prerelease testing.
- Use the matching version section of `CHANGELOG.md` as the starting release notes.
- State that the API is alpha and may change before 1.0.
- Include the tested scanner firmware and transports.
- Attach the wheel and source distribution from `dist/` if desired.
- Confirm GitHub marks the newest normal release as **Latest**.

## 8. Verify the published package

Install the exact release in a clean environment after the Trusted Publishing workflow succeeds:

```bash
python -m venv /tmp/sds200-release-check
source /tmp/sds200-release-check/bin/activate
python -m pip install --upgrade pip
python -m pip install --no-cache-dir sds200==VERSION
sdsctl --help
sdsctl --version
python -c "import sds200; print(sds200.__version__)"
deactivate
rm -rf /tmp/sds200-release-check
```

Do not reuse or move a tag after PyPI has accepted that version. PyPI
filenames and release versions are immutable.
