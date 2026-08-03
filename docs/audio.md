# Audio subsystem architecture

Version 0.11.0 introduced hardware-validated SDS200 network audio while keeping
its lifecycle independent from scanner control. Milestone 16.1 adds a decoded-PCM
fanout layer for local playback and simultaneous recording. Audio failures never
switch, close, or delay USB serial, UDP control, fallback profiles, or preferred
recovery.

## Layers

- `NetworkAudioTransport` performs RTSP negotiation over TCP and receives RTP over
  one UDP client port. The SDS200 requires `RTP/AVP;unicast;client_port=PORT`
  rather than the conventional RTP/RTCP port pair.
- `AudioStream` owns subscriptions and lifecycle without depending on
  `SDSScanner`.
- `AudioChunk.data` contains the raw payload type 0 G.711 mu-law bytes from one
  accepted RTP packet.
- `AudioFanoutSession` decodes each accepted packet once and submits 8 kHz mono
  signed 16-bit PCM to one or more independently buffered `PcmSink` destinations.
- `SoundDevicePlaybackSink` sends PCM to the selected PortAudio output device.
- `PcmWavSink` moves WAV writes to a worker thread and finalizes the
  `PcmuWavRecorder` during shutdown.
- `PcmStreamSink` writes raw PCM through a bounded nonblocking descriptor worker
  for foreground process integrations such as Asterisk custom Music on Hold.

A sink's `submit_pcm()` method must not block on device, disk, encoder, or network
I/O. Each sink owns its buffering and failure behavior so one destination cannot
hold up RTP reception or another destination. This contract is also the extension
point for future remote streaming adapters listed in [the roadmap](../ROADMAP.md).

## CLI playback and recording

Install the optional local-playback backend:

```bash
python -m pip install "sds200[playback]"
```

The Python extra installs `sounddevice`, but Linux also needs the PortAudio shared
library. On Debian or Raspberry Pi OS:

```bash
sudo apt update
sudo apt install libportaudio2
```

Inspect the active PortAudio version, host APIs, default output, and available
output-capable devices without opening a scanner connection:

```bash
sdsctl audio-devices
```

PortAudio may expose Linux audio through ALSA, PipeWire or PulseAudio
compatibility, or JACK, depending on the operating-system configuration and
PortAudio build. Direct PipeWire, PulseAudio, and ALSA sinks remain planned
backends rather than claims of current native support.

Listen through the operating system's default output device:

```bash
sdsctl --host 192.168.0.251 audio --play
```

Play and record from the same RTSP/RTP session:

```bash
sdsctl --host 192.168.0.251 audio \
  --play \
  --output scanner-audio.wav \
  --duration 30
```

Use `--device DEVICE` to select a PortAudio output device and `--buffer-ms` to
change the bounded playback queue. Omit `--duration` to run until `Ctrl+C`. Use
`--force` to replace an existing output file. At least one of `--play` or
`--output` is required.

Overflow drops the oldest queued playback audio to preserve live latency.
Underflow fills the device request with silence. Both conditions are counted in
the command summary. Recording uses its own bounded worker queue and does not
perform disk writes in the RTP callback.

## Python lifecycle

```python
from pathlib import Path

from sds200 import (
    AudioFanoutSession,
    AudioStream,
    NetworkAudioTransport,
    PcmWavSink,
    PcmuWavRecorder,
    SoundDevicePlaybackSink,
)

transport = NetworkAudioTransport("192.168.0.251")
stream = AudioStream(transport)
sinks = (
    SoundDevicePlaybackSink(),
    PcmWavSink(PcmuWavRecorder(Path("scanner-audio.wav"))),
)

with AudioFanoutSession(stream, sinks):
    run_application()
```

The direct one-shot `AudioRecordingSession` API remains available for callers that
want one stream and one recorder. The TUI uses `TuiAudioSession` with a dynamic PCM
sink router: one long-lived fanout owns RTSP/RTP reception while live playback,
repeatable WAV sinks, and saved-recording playback are attached or detached without
opening a second scanner audio session.

## Recording metadata foundation

Milestone 16.4.0 adds the renderer-neutral `RecordingMetadata` schema without
changing recording behavior. It combines immutable start and stop
`AudioSessionSnapshot` values with optional `RadioStateSnapshot` boundary state.
The versioned JSON records the WAV filename and fixed PCM format, scanner identity
and endpoint, UTC boundaries, system, department, site, channel, frequency, audio
totals, and RTP reliability counters.

`recording_metadata_path()` uses an unambiguous adjacent `<recording>.wav.json`
name. `write_recording_metadata()` serializes sorted, indented JSON through a
same-directory temporary file and refuses to replace an existing sidecar unless
`overwrite=True` is explicit.

TUI recording sidecars are opt-in. Add `--audio-metadata` with either
`--audio-directory` or `--audio-output`:

```bash
sdsctl --host 192.168.0.251 tui \
  --audio-directory ~/scanner-recordings \
  --audio-metadata
```

`ScannerTuiApp` forwards each immutable live PSI snapshot to `TuiAudioSession`.
The session captures the latest available scanner state at successful recording
start and stop boundaries, then writes the sidecar after WAV finalization.
A requested metadata-write failure marks the recording operation failed rather
than silently reporting success. Timestamp allocation also avoids an existing
sidecar, so an orphaned `<recording>.wav.json` cannot be overwritten by a later
repeatable recording. The standalone `sdsctl audio` command is unchanged because
it does not own a scanner-control PSI stream.

```python
from sds200 import RecordingMetadata, write_recording_metadata

metadata = RecordingMetadata.from_snapshots(
    started_snapshot,
    stopped_snapshot,
    scanner="SDS200",
    started_state=state_at_start,
    stopped_state=state_at_stop,
)
sidecar_path = write_recording_metadata(metadata)
```

## Recording identity components

Milestone 17.1 adds renderer-neutral `RecordingIdentity` derivation without
changing current recording paths. Identity values come from immutable finalized
metadata rather than a live mutable scanner state. Start-boundary state is
preferred, and an absent start value is filled from the stop boundary.

`safe_recording_component()` applies Unicode compatibility normalization,
collapses whitespace and punctuation to one hyphen, protects reserved portable
names, supplies an explicit fallback, and enforces a component length limit.
The identity excludes the current recording path so moving an audio-and-sidecar
pair does not change its scanner-derived organization values.

```python
from sds200 import RecordingIdentity

identity = RecordingIdentity.from_metadata(metadata)
components = identity.filename_components()

print(components["date"])
print(components["scanner"])
print(components["system"])
print(components["department"])
print(components["site"])
print(components["channel"])
```

The component map also includes `timestamp`, `endpoint`, `mode`, `frequency`,
`modulation`, `service_type`, `talkgroup_id`, and `unit_id`. Milestone 17.2
consumes these values through configurable path policies; identity derivation
itself does not rename or move recordings.

## Recording organization

Milestone 17.2 lets repeatable TUI recordings use ordered directories derived at
recording start. Supply a comma-separated list with `--audio-organize-by`; the
supported components are `scanner`, `date`, `system`, `department`, `site`, and
`channel`:

```bash
sdsctl --host 192.168.0.251 tui \
  --audio-directory ~/scanner-recordings \
  --audio-organize-by scanner,date,system,department,site,channel \
  --audio-metadata
```

Organization is opt-in. Without `--audio-organize-by`, the existing flat local
`--audio-directory` behavior and timestamp filenames remain unchanged. Each
configured value is normalized with `safe_recording_component()`; unavailable
start-boundary values become `unknown`. The `date` component uses the identity's
UTC start date.

The path is selected once from the recording start time and scanner state. A later
PSI change does not rename or move the active or finalized WAV. Collision suffixes
remain in the selected directory, and an adjacent metadata sidecar participates in
allocation so the WAV and `<recording>.wav.json` remain together.

## Recording inventory

Milestone 17.3 adds a renderer-neutral, read-only inventory for a recording root.
`scan_recording_inventory()` recursively treats each WAV file and adjacent
`<recording>.wav.json` sidecar as one managed unit. It reports compatible,
incompatible, unreadable, and missing audio together with valid, missing,
unreadable, invalid, mismatched, and orphaned metadata.

```python
from pathlib import Path

from sds200 import scan_recording_inventory

inventory = scan_recording_inventory(Path("~/scanner-recordings"))
print(inventory.summary.as_dict())

for entry in inventory.entries:
    print(entry.relative_audio_path, entry.audio_status, entry.metadata_status)
```

Inventory order is deterministic by relative path. Compatible WAV duration, file
and sidecar sizes, metadata-derived UTC start time, aggregate byte totals, and
attention counts are available without changing the filesystem. Directory
symlinks are not traversed, and managed file symlinks that resolve outside the
configured root are reported as unreadable rather than dereferenced.

This foundation does not move, rename, overwrite, or delete artifacts.

## Recording retention planning

Milestone 17.3 also provides deterministic, non-destructive retention previews.
`RecordingRetentionPolicy` supports optional maximum age, managed-unit, and
aggregate-byte limits. `plan_recording_retention()` requires an explicit
timezone-aware planning boundary for age policies and never calls the system clock
or changes the filesystem:

```python
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sds200 import (
    RecordingRetentionPolicy,
    plan_recording_retention,
    scan_recording_inventory,
)

inventory = scan_recording_inventory(Path("~/scanner-recordings"))
policy = RecordingRetentionPolicy(
    maximum_age=timedelta(days=30),
    maximum_units=500,
    maximum_total_bytes=20 * 1024**3,
)
plan = plan_recording_retention(
    inventory,
    policy,
    now=datetime.now(UTC),
)

print(plan.summary.as_dict())
for decision in plan.decisions:
    print(
        decision.entry.relative_audio_path,
        decision.disposition,
        decision.reasons,
    )
```

Eligible units are evaluated oldest-first using their UTC recording timestamp and
relative path as deterministic tie-breakers. Compatible and incompatible WAV files
with valid or missing metadata may be selected. Unreadable or missing audio,
unsafe sidecars, and units without a reliable timestamp are protected and reported.
Protected units continue to count toward projected unit and byte totals, so the
plan explicitly reports limits it cannot satisfy safely.

A selected WAV and its adjacent sidecar remain one managed unit. Planning performs
no move, rename, overwrite, or deletion.

## Recording retention execution

Milestone 17.4 adds a renderer-neutral execution foundation that consumes one
existing retention plan. Execution requires the exact confirmation token derived
from that plan; a token from another policy, inventory, timestamp, or decision set
is rejected before filesystem mutation:

```python
from sds200 import (
    execute_recording_retention,
    recording_retention_confirmation_token,
)

confirmation = recording_retention_confirmation_token(plan)
result = execute_recording_retention(
    plan,
    confirmation=confirmation,
)
print(result.summary.as_dict())
```

Only decisions already marked `select` are considered. Retained and protected
entries are never added implicitly. Before each selected unit is changed, the
executor verifies the resolved inventory root, adjacent sidecar path, regular-file
types, absence of symlinks, captured sizes and modification state, and a fresh
inventory view. A stale or unsafe unit is skipped while later selected units
continue deterministically.

When metadata is present and valid, its sidecar is deleted before the WAV. A
sidecar failure preserves the WAV. A later WAV failure is reported as a partial
failure with the exact deleted-byte and file counts. Missing sidecars remain
valid managed units and require no metadata deletion. Directories and unknown
contents are never removed recursively.

The executor returns immutable completed, skipped, and failed unit reports.
Confirmation-token presentation remains separate from consent; callers must not
treat token generation alone as user approval.

### CLI retention preview and execution

`sdsctl recordings retention` exposes the same inventory, planning, and execution
services without opening a scanner connection. At least one limit is required.
The default operation is preview-only:

```bash
sdsctl recordings retention ~/scanner-recordings \
  --maximum-age-days 30 \
  --maximum-units 500 \
  --maximum-total-bytes 21474836480
```

The preview prints every decision, projected totals, whether all requested limits
can be satisfied safely, and an exact `delete:<sha256>` confirmation token. Add
`--json` for a stable document containing the complete serialized plan and token.

Execution requires both the destructive `--execute` option and the exact token
from an unchanged preview:

```bash
sdsctl recordings retention ~/scanner-recordings \
  --maximum-units 500 \
  --execute 'delete:<exact-token-from-preview>'
```

Age policies also include the planning timestamp in the confirmed plan. Repeat
the preview's `Planned at` value with `--planned-at` during execution so the exact
plan can be reconstructed:

```bash
sdsctl recordings retention ~/scanner-recordings \
  --maximum-age-days 30 \
  --planned-at '2026-08-03T08:00:00+00:00' \
  --execute 'delete:<exact-token-from-preview>'
```

A mismatched token exits with an error before mutation. Preview exits with status
1 when protected units prevent the requested limits from being satisfied.
Execution exits with status 1 when limits remain unsatisfied or any selected unit
is skipped or fails. Retained, protected, stale, unsafe, and unrelated artifacts
are never added to the execution set.

## Reliability statistics

`NetworkAudioTransport.statistics` returns an immutable session snapshot with
received datagrams and bytes, delivered packets and payload bytes, sequence gaps,
estimated packet loss, duplicates, late packets, malformed packets, unexpected
source and SSRC rejections, RTP timestamp discontinuities, missing-sample
estimates, receive and callback errors, keepalives, teardown count, sequence
endpoints, final timestamp, and SSRC.

Each PCM sink also exposes a `PcmSinkStatistics` snapshot containing submitted,
written, dropped, and queued bytes plus underflow, overflow, and callback-status
counters where applicable.

Sequence tracking begins with the first packet actually received because the
SDS200's `RTP-Info` starting sequence is not a reliable initialization value.
Synthetic fixtures exercise loss, duplicate, late, malformed, wraparound, and
backward-timestamp behavior without requiring scanner hardware.

The RTP socket binds to the local IPv4 interface selected by the route to the
scanner. Packets are accepted only from the source address, server port, and SSRC
negotiated during RTSP `SETUP`; unexpected senders are counted and discarded.
Explicit `0.0.0.0` RTP binds are rejected.

## Remote destination core

Milestone 16.3 introduces a service-neutral `RemotePcmSink` foundation before any
Broadcastify or Asterisk adapter is enabled. The sink owns a bounded newest-audio
queue and performs connection creation, blocking writes, reconnect backoff, and
connection shutdown on its worker thread. Scanner RTP reception and all other PCM
destinations therefore remain independent from a slow or failed remote service.

`RemoteDestinationConfig` rejects credentials embedded in endpoint URLs.
Credentials are represented by named `EnvironmentSecret` references and resolved
only when the worker opens an adapter connection. Resolved values are excluded from
configuration representations, snapshots, and log messages; connection exceptions
are redacted before they are retained or reported.

`RemotePcmSinkSnapshot` reports immutable queue, throughput, connection-attempt,
reconnect, failure, retry, and last-error state. Service adapters receive the
configuration and resolved secret mapping through an injected
`RemoteConnectionFactory`. Adapter connections provide a prompt, thread-safe
`interrupt()` operation so shutdown can unblock an in-flight `write_pcm()` before
the worker finalizes the connection with `close()`. The Broadcastify adapter below
is the first production implementation; command-line configuration is not
available yet.

## Broadcastify feed adapter

Milestone 16.3.1 adds a Broadcastify-compatible Icecast source connection on top
of `RemotePcmSink`. Broadcastify's documented mono profile is fixed at 22.05 kHz,
16 kbps constant-bit-rate MP3. The adapter accepts the fanout layer's 8 kHz mono
signed 16-bit PCM and starts an FFmpeg process that resamples and encodes it before
a dedicated connection pump sends the MP3 bytes to the configured Icecast mount.

An approved Broadcastify feed supplies its receiver server, port, mount, and source
password on the feed owner's **Technicals** tab. Supported live-audio ports are 80,
8000, 8080, and 8500. The adapter sends static Icecast source metadata including
the stream name, scanner genre, public flag, bitrate, sample rate, and mono channel
count. Dynamic alpha-tag updates synchronized with PSI state are not enabled yet.

Install an FFmpeg build with the `libmp3lame` encoder and make the executable
available on `PATH`. Keep the source password outside application arguments and
configuration files. For an interactive shell, read it without echoing the value:

```bash
read -rsp "Broadcastify source password: " SDS200_BROADCASTIFY_PASSWORD
printf '\n'
export SDS200_BROADCASTIFY_PASSWORD
```

Create the sink with only an environment-variable reference in Python:

```python
from sds200 import (
    BroadcastifyConfig,
    EnvironmentSecret,
    create_broadcastify_sink,
)

feed = BroadcastifyConfig(
    name="county-feed",
    server="audio1.broadcastify.com",
    port=80,
    mount="/replace-with-technicals-mount",
    password=EnvironmentSecret("SDS200_BROADCASTIFY_PASSWORD"),
    stream_name="County Public Safety",
)
broadcastify_sink = create_broadcastify_sink(feed)
```

Attach `broadcastify_sink` to the same `AudioFanoutSession` used for local playback
or WAV recording. Connection creation, FFmpeg input, encoded-output pumping,
reconnect backoff, and shutdown remain outside the scanner RTP callback. The
adapter's `interrupt()` path closes the Icecast socket and terminates FFmpeg so a
blocked network or encoder operation cannot hold up application shutdown.

Broadcastify currently documents plain Icecast source ports rather than TLS source
endpoints. The source authorization header is therefore transported over an
unencrypted TCP connection. Use only the server and port assigned by Broadcastify,
protect the host running the feed, and never expose the source port or credentials
in logs.

See [Broadcastify's alternative-client requirements][broadcastify-alternative]
and [Barix Icecast source setup][broadcastify-barix] for the service-side profile.

[broadcastify-alternative]: https://support.broadcastify.com/hc/en-us/articles/204740015-Alternative-Broadcasting-Software-and-Clients
[broadcastify-barix]: https://support.broadcastify.com/hc/en-us/articles/22099461024539-Barix-Instreamer-Setup-for-Broadcastify

## Asterisk Music-on-Hold bridge

Milestone 16.3.2 adds a foreground `sdsctl asterisk-moh` bridge for Asterisk's
custom Music-on-Hold mode. Asterisk starts the command and reads raw audio from
its standard output. The bridge reserves `stdout` exclusively for 8 kHz mono
16-bit signed-linear PCM; logging and errors remain on `stderr` so text can
never corrupt the audio stream.

The bridge reuses the decoded fanout PCM directly. It does not start FFmpeg,
resample, or add an Asterisk Python dependency. `PcmStreamSink` duplicates the
output descriptor, places it in nonblocking mode, and writes from a bounded
worker queue. Short writes are completed, slow-reader overflow discards the
oldest queued audio, and a closed Asterisk pipe ends the command cleanly.

Create a network-capable profile where the Asterisk service account can read
it. The profile stores the scanner address, not a remote-service credential:

```bash
sudo install -d -o asterisk -g asterisk /etc/asterisk/sds200
sudo -u asterisk /opt/sds200/.venv/bin/sdsctl \
  --config /etc/asterisk/sds200/profiles.toml \
  profile add scanner-moh --host 192.168.0.251
```

Add a custom class to `musiconhold.conf`. Use the absolute path to the installed
`sdsctl` executable because Asterisk services commonly have a restricted
`PATH`:

```ini
[scanner]
mode=custom
application=/opt/sds200/.venv/bin/sdsctl --config /etc/asterisk/sds200/profiles.toml --profile scanner-moh asterisk-moh
format=slin
kill_escalation_delay=5000
```

Asterisk's sample configuration defines `mode=custom` as an application whose
output Asterisk consumes and uses `format` to identify that output. `slin` is
the 8 kHz signed-linear format, matching the SDS200 fanout's 8 kHz, mono,
little-endian 16-bit PCM. The longer escalation delay gives RTSP teardown and
worker cleanup time after Asterisk sends `SIGHUP`; the command also handles
`SIGTERM` and `SIGINT`. The default process-group kill method is retained so
Asterisk's escalation also reaches any service wrapper or descendants.

Reload Music on Hold and confirm the class is visible:

```bash
asterisk -rx "module reload res_musiconhold.so"
asterisk -rx "moh show classes"
```

For a service-account smoke test outside Asterisk, capture a few seconds of raw
signed-linear audio. `timeout` sends `SIGTERM`, which the bridge converts into
an orderly stop:

```bash
sudo -u asterisk timeout --signal=TERM 5 \
  /opt/sds200/.venv/bin/sdsctl \
  --config /etc/asterisk/sds200/profiles.toml \
  --profile scanner-moh asterisk-moh > /tmp/scanner-moh.sln
```

The Asterisk service account must be able to reach the scanner's RTSP and RTP
ports. Keep that traffic on the same trusted LAN or secured VPN described
below. See Asterisk's [Music-on-Hold sample configuration][asterisk-moh-sample]
and [audio-format reference][asterisk-audio-formats] for the custom-process and
signed-linear contracts.

[asterisk-moh-sample]: https://github.com/asterisk/asterisk/blob/master/configs/samples/musiconhold.conf.sample
[asterisk-audio-formats]: https://docs.asterisk.org/Operation/Asterisk-Audio-and-Video-Capabilities/

## Remote audio validation

Milestone 16.3.3 validates the remote-audio pipeline against a physical SDS200
running firmware 1.26.01. The validation host ran Ubuntu 26.04 LTS, Python
3.14.4, FFmpeg 8.0.1 with `libmp3lame`, and Asterisk 22.5.2.

### Asterisk service validation

The `scanner` custom Music-on-Hold class was installed under the `asterisk`
service account with an absolute `sdsctl` path, a service-readable saved network
profile, `format=slin`, the default process-group kill method, and a 5-second
kill-escalation delay.

A direct service-account capture submitted and wrote 136,320 PCM bytes with zero
drops. A Local-channel Asterisk test then recorded exactly 15 seconds of mono,
8 kHz, 16-bit signed-linear audio: 120,000 frames, 119,865 nonzero samples, a
peak amplitude of 10,876, and RMS amplitude of 2,960.45. A normal module reload
retained the unchanged custom source. A controlled module unload removed the
bridge without leaving an orphan, and loading the module recreated the bridge
with a distinct process ID.

### Broadcastify-compatible loopback validation

Two manual validators exercise the production Broadcastify adapter with the
physical scanner and real FFmpeg encoder while keeping all traffic on
`127.0.0.1:8500`:

- `scripts/validate_broadcastify_loopback.py` accepts one Icecast-compatible
  source session, verifies and sanitizes its request, captures the MP3 stream,
  probes its codec profile, decodes it, and rejects silence or PCM loss.
- `scripts/validate_broadcastify_reconnect.py` deliberately resets the first
  source connection, requires a successful retry and second source session, and
  verifies post-reconnect encoded audio and cleanup.

Set `SDS200_BROADCASTIFY_PASSWORD` to an ephemeral local test value before
running either validator; the value is checked during authentication but never
written to the evidence files. Stop any other SDS200 RTSP/RTP audio consumer
before starting a validation run.

```bash
export SDS200_BROADCASTIFY_PASSWORD="$(
  python -c 'import secrets; print(secrets.token_urlsafe(32))'
)"

python scripts/validate_broadcastify_loopback.py \
  --host 192.168.0.251 \
  --duration 15 \
  --output-dir /tmp/sds200-broadcastify-loopback

python scripts/validate_broadcastify_reconnect.py \
  --host 192.168.0.251 \
  --drop-after-bytes 4096 \
  --post-reconnect-duration 10 \
  --output-dir /tmp/sds200-broadcastify-reconnect

unset SDS200_BROADCASTIFY_PASSWORD
```

The uninterrupted loopback delivered 376 RTP packets and 120,320 audio samples
with every RTP reliability counter at zero. All 240,640 submitted PCM bytes were
written without drops or queue overflow. The captured stream was mono MP3 at
22.05 kHz and 16 kbps, with 14.811 seconds of decoded non-silent audio.

The forced-disconnect run delivered 339 RTP packets without transport loss or
callback errors. It recorded two connection attempts, two successful
connections, one failure, and one reconnect. The interrupted write accounted
for 640 dropped PCM bytes while queue overflows remained zero. The second source
session produced 10.005 seconds of non-silent mono MP3 at 22.05 kHz and 16 kbps.
Both runs stopped FFmpeg cleanly.

These loopback results validate scanner reception, secret handling, Icecast
request construction, MP3 encoding, retry/backoff, reconnection, and shutdown.

### Live Broadcastify service validation

`scripts/validate_broadcastify_live.py` validates the assigned production feed
without accepting credentials on the command line or retaining endpoint details
in its evidence file. Set the assigned server, port, mount, stream name, and
source password through environment variables, then stop any competing SDS200
audio consumer before running the validator:

```bash
export SDS200_BROADCASTIFY_SERVER='assigned receiver server'
export SDS200_BROADCASTIFY_PORT='assigned source port'
export SDS200_BROADCASTIFY_MOUNT='/assigned mount'
export SDS200_BROADCASTIFY_STREAM_NAME='approved feed name'

read -rsp 'Broadcastify source password: ' SDS200_BROADCASTIFY_PASSWORD
printf '\n'
export SDS200_BROADCASTIFY_PASSWORD

python scripts/validate_broadcastify_live.py \
  --host 192.168.0.251 \
  --duration 60 \
  --output /tmp/sds200-broadcastify-live-summary.json

unset SDS200_BROADCASTIFY_PASSWORD
unset SDS200_BROADCASTIFY_SERVER
unset SDS200_BROADCASTIFY_PORT
unset SDS200_BROADCASTIFY_MOUNT
unset SDS200_BROADCASTIFY_STREAM_NAME
```

The assigned production service accepted the source on the first connection
attempt over its assigned port. The 60-second run delivered 1,505 RTP packets
and 481,600 audio samples, representing 60.200 seconds of decoded audio. All
963,200 submitted PCM bytes were written with zero drops or queue overflows.
The transport reported no packet loss, sequence gaps, duplicate, late,
malformed, unexpected-source, SSRC-mismatch, timestamp, receive, or callback
errors. Shutdown sent RTSP teardown, left the sink stopped with an empty queue,
and left no FFmpeg process running.

The retained JSON contains counters and state only. It excludes the source
password, receiver server, assigned mount, and approved feed name. Together
with the loopback and forced-disconnect results, this validates production
Broadcastify authorization and routing, the fixed MP3 encoder profile, scanner
audio delivery, secret handling, recovery behavior, and orderly shutdown.

## SDS200 LAN security

The protocol is unauthenticated and unencrypted. Keep RTSP TCP port 554 and its
negotiated RTP UDP port on a trusted LAN or behind a secured VPN. Remote streaming
credentials must not be passed through command-line arguments or written to logs.
