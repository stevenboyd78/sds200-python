# Daemon ownership runtime

Milestone 19.3 introduces the renderer-neutral `DaemonRuntime` ownership
foundation. It coordinates one scanner control session, continuous PSI, one
SDS200 RTSP/RTP audio session, one decoded-PCM fanout, and dynamic PCM
destinations.

Milestone 19.4 adds `DaemonSignalController`, `DaemonProcess`, and the foreground
`sdsctl daemon` command. The process host owns signal installation, waits for a
shutdown request, stops the runtime deterministically, and restores the previous
signal handlers before returning.

Milestone 19.5 adds a private Unix-domain socket, a strict versioned JSON Lines
protocol, and bounded read-only access to daemon capabilities and authoritative
runtime snapshots.

Milestone 19.6 adds a second private Unix-domain socket for ordered daemon events.
Every event subscription starts with an authoritative runtime snapshot and then
receives later runtime, scanner, PSI, radio-state, audio-lifecycle, and
destination-health events.

Milestone 19.7 adds a third private Unix-domain socket for accepted RTP PCMU
packets. Each client owns an independent bounded queue, receives the original
payload before decode, and observes RTP continuity plus cumulative local queue
loss.

Milestone 19.8 adds capability-checked daemon operations for hold, next,
previous, and bounded reconnect. Mutations are single-owner, concurrent requests
are rejected rather than queued, completion follows scanner acknowledgement, and
successful responses include authoritative runtime snapshots. Reconnect is
limited to the directly owned SDS200 UDP control transport.

Milestone 19.9 adds explicit CLI daemon-client status, authoritative snapshot,
safe-control, ordered event-watch, and PCMU playback or WAV-recording workflows
while preserving the standalone top-level scanner and direct-audio commands.
Milestone 19.10 adds explicit daemon-backed TUI operation using the API, event,
and PCMU services while preserving standalone TUI ownership as the default.
Milestone 19.11 adds validated saved-destination activation and transactional
`SIGHUP` replacement. Milestone 20.5 adds a daemon-owned recording manager over
the shared decoded-PCM router plus a fourth private Unix-domain socket for
bounded finalized-recording access. Milestone 20.6 adds default-on semantic PSI
silence detection and bounded recovery inside the foreground ownership loop.
Milestone 20.8 adds an optional daemon-owned MQTT publication worker over the
existing authoritative event stream. It publishes semantic state without opening
scanner hardware, skips packet-rate PSI events, and isolates broker retry/backoff
from scanner, PSI, audio, recording, and local-service ownership. Milestone 20.9
adds explicitly opt-in MQTT scanner controls through the same semantic daemon API
dispatcher used by local clients, without adding another scanner owner. Milestone
20.10 adds optional Home Assistant MQTT device discovery over those
existing semantic topics, including birth-triggered republication, without adding
another scanner owner. Milestone 20.12.3 adds seven dedicated Home Assistant
control topics that translate four desired-state Hold switches plus Previous
Channel, Next Channel, and Reconnect Scanner into fresh typed daemon-control
requests. The worker derives navigation only from ordered daemon-owned radio
state and clears that context after scanner disconnect or event-sequence
resynchronization. The Home Assistant App keeps the independent generic MQTT
request-envelope command input disabled. No Home Assistant path opens scanner
hardware directly. Decoded-PCM CLI subscriptions and automatic daemon discovery
and selection remain follow-on work. The process does not fork or create a
pidfile.

## Foreground process contract

Start the process with an explicit SDS200 network host:

```bash
sdsctl --log-level INFO --host 192.168.0.251 daemon
```

A saved network or fallback profile is also accepted:

```bash
sdsctl --log-level INFO --profile home daemon
```

The command constructs exactly one `DaemonRuntime`, one `PcmSinkRouter`, one
`NetworkAudioTransport`, one compatibility-named `DaemonReadOnlyApi`, one
bounded `DaemonApiServer`, one `DaemonEventStream`, one bounded
`DaemonEventServer`, one `PcmuStream`, one bounded `DaemonPcmuServer`, one
`DaemonRecordingManager`, one bounded `DaemonRecordingFileServer`, one
`DaemonDestinationCoordinator`, and one `DaemonDestinationReloader`. When a
daemon MQTT manifest is present, construction also creates one
`DaemonMqttWorker` using the existing `DaemonEventStream`; an absent manifest
creates no MQTT worker and does not require Paho MQTT. The process constructs one
`DaemonReadOnlyApi` instance and shares it with both `DaemonApiServer` and the
MQTT worker, so enabled MQTT commands reuse the exact local semantic-control
boundary. The API class retains its historical public name while exposing
backward-compatible reads, explicit safe controls, and daemon recording
operations. The PCMU stream
subscribes to the same authoritative transport used by the decoded-PCM fanout.
The coordinator activates the validated startup destination set against the
shared decoded-PCM router, while the recording manager attaches and detaches its
WAV sink from that same router. Daemon-client audio continues to consume PCMU
independently. Decoded-PCM client subscriptions remain follow-on work.

The audio endpoint must come from either `--host` or a network-capable SDS200
profile. A fallback profile may select serial control at runtime, but its saved
network host still supplies the RTSP/RTP audio endpoint. Serial-only profiles,
bare serial selection, replay captures, and non-SDS200 network-audio selections
are rejected.

The command runs in the foreground. It does not daemonize itself, fork, create a
pidfile, change privileges, install a service unit, or request socket activation.
The event and PCMU services own their sockets before runtime startup so the
event stream can observe lifecycle transitions and PCMU clients can subscribe
before authoritative audio begins. The finalized-recording service opens after
runtime and destination activation, and the request-response API opens last so
recording operations are not admitted before all required local services exist.

### Signals and exit behavior

`DaemonSignalController` installs stop handlers for `SIGINT` and `SIGTERM`
and, where available, a reload handler for `SIGHUP`. Stop handlers record the
terminating signal and wake the normal process loop. `SIGHUP` records a pending
destination reload without requesting shutdown. No runtime or destination work
occurs inside a signal handler.

Previous signal handlers are restored after the process loop exits. Partial
signal-installation failures roll back handlers that were already replaced.
Restoration attempts continue after an individual restoration failure.

A controlled `SIGINT` or `SIGTERM` shutdown returns success after the runtime
stops. Startup, configuration, transport, or shutdown failures produce the
normal `sdsctl` error path. When process work and cleanup both fail, the primary
process failure remains authoritative and the cleanup failure is logged by type
without exposing its message.

`SIGHUP` reloads the same destination manifest selected during daemon startup.
The manifest is loaded and validated before the coordinator transaction begins.
Loader and activation failures are logged by exception type and leave the
previous committed destination set running. Post-commit cleanup failures are
reported without rolling back the successfully activated replacement. A stop
request takes priority over a pending reload.

### Local service process lifecycle

At the process-host level, startup occurs in this order:

1. bind and start the local `DaemonEventServer`;
2. bind and start the local `DaemonPcmuServer`;
3. start `DaemonRuntime`;
4. start the optional daemon MQTT worker;
5. activate the validated daemon destination configuration;
6. bind and start the local `DaemonRecordingFileServer`;
7. bind and start the local `DaemonApiServer`; and
8. wait for `SIGHUP`, `SIGINT`, `SIGTERM`, or another process-loop failure.

Starting the event service first allows an already connected client to observe
runtime startup transitions. Starting the PCMU service before the runtime allows
clients to subscribe before the shared transport begins publishing accepted
packets. MQTT starts only after the runtime is authoritative, so its first broker
session can publish a running snapshot and, when enabled, Home Assistant device
Discovery, but before destinations so it can observe their later health. Broker
connectivity and Home Assistant birth-topic handling stay inside the MQTT worker
and do not make scanner ownership depend on broker or Home Assistant
availability. Starting the
recording-file service after runtime and destination activation makes finalized
inventory access available before recording API requests are admitted. Starting
the API last ensures every admitted request observes an initialized runtime and
configured recording service.

Shutdown occurs in this order:

1. close the API listener and connected request-response clients;
2. wait for bounded API worker completion;
3. close the recording-file listener and connected finalized-file readers;
4. wait for bounded recording-file worker completion;
5. close the daemon recording manager, finalizing an active recording when
   possible;
6. stop all configured daemon-owned destinations;
7. publish retained MQTT `offline` when possible and stop the optional MQTT
   worker;
8. stop the daemon runtime while the event service remains available for final
   lifecycle transitions;
9. close the PCMU listener, publisher subscription, and connected clients;
10. wait for bounded PCMU worker completion;
11. close the event listener and connected subscribers; and
12. wait for bounded event-worker completion.

If any component startup fails, cleanup is attempted for every component whose
startup was attempted. Cleanup continues after an individual failure, while the
primary startup or process error remains authoritative. The MQTT worker is
stopped before runtime cleanup so a session that reached retained `online` can
attempt retained `offline`; unexpected broker loss instead relies on its retained
offline last will. See the [daemon MQTT guide](daemon-mqtt.md),
[local daemon API guide](daemon-api.md), and
[local daemon event stream guide](daemon-events.md) for their publication,
socket, framing, permission, limit, and failure-isolation contracts.

## Ownership graph

One runtime owns:

1. one `SDSScanner` control connection;
2. one active PSI scanner-information stream;
3. one `AudioFanoutSession`;
4. one `PcmSinkRouter` included in that fanout; and
5. any playback, recording, streaming, or integration sinks attached to the
   router.

After acquiring the scanner connection, the runtime probes model and firmware
once before starting PSI. Each probe is bounded by the scanner command timeout
and is independently nonfatal. Authoritative snapshots retain successful
values and serialize a failed or empty probe as `null`, without surrendering
scanner ownership or interrupting PSI and audio startup.

The scanner's PCMU audio is accepted once. Each accepted packet is first
published to the bounded PCMU stream with its original payload and RTP continuity
metadata, then decoded once and submitted to the router as 8 kHz mono signed
16-bit PCM. Each attached sink retains its existing bounded buffering, worker,
health, and failure-isolation behavior.

A destination failure does not stop scanner control, PSI, RTP reception, or
another healthy destination.

## Lifecycle

Startup is serialized in ownership order:

1. connect scanner control;
2. start PSI and wait for its initial response;
3. start the audio fanout, which starts the PCM router before opening the audio
   transport; and
4. publish the `running` runtime transition.

Shutdown proceeds in reverse ownership order:

1. stop the audio fanout and router destinations;
2. stop PSI;
3. close scanner control; and
4. publish either `stopped` or `failed`.

A failed startup performs best-effort reverse-order cleanup for every component
whose startup was attempted. Cleanup continues after an individual cleanup
failure so later owners are still released.

`stop()` is idempotent and serialized across concurrent callers. A successfully
stopped or failed runtime cannot be restarted; callers must construct a new
runtime instance.

### PSI silence recovery

The foreground process calls `DaemonRuntime.poll()` from its existing bounded
process loop; recovery does not add another watchdog thread. `DaemonRuntime`
subscribes to `scanner.on_psi()` and records a monotonic timestamp for every
successfully parsed PSI frame, including frames whose radio-state fields did not
change. This makes the watchdog a semantic PSI-liveness check rather than a
generic UDP receive timer.

Automatic recovery is enabled by default. The daemon waits 10 seconds after the
most recent PSI observation before attempting recovery and applies a 60-second
cooldown after a completed recovery attempt. The command-line policy is:

```text
--psi-auto-recover / --no-psi-auto-recover
--psi-recover-after SECONDS
--psi-recovery-cooldown SECONDS
```

A stale stream is recovered with the existing bounded `reconnect(timeout=2.0)`
path, so directly owned SDS200 UDP control transports reopen and restore their
configured PSI interval. Automatic recovery shares the same nonblocking mutation
lock used by API, TUI-daemon-client, and browser scanner controls. If another
mutation owns that slot, `poll()` simply defers recovery and does not consume the
cooldown; the next process-loop poll may retry once the control completes.

Recovery failures are isolated from the daemon process, logged by exception type,
and rate-limited by the same cooldown. The RTSP/RTP audio transport is independent
from scanner control/PSI, so a control-transport reconnect does not intentionally
stop daemon-owned audio.

## Daemon-owned scanner controls

`DaemonRuntime` exposes typed compatibility `hold()`, semantic
`hold_state()`, `next()`, `previous()`, and `reconnect()` methods for the local
API. It does not expose raw scanner command strings or a generic key operation.

Every control:

- requires a running runtime;
- requires a connected scanner for navigation operations;
- validates a positive finite caller deadline;
- acquires one nonblocking mutation slot;
- executes under runtime lifecycle ownership;
- waits for authoritative scanner completion; and
- returns an immutable `DaemonControlResult` containing sequence, operation,
  start and completion timestamps, and the completion snapshot.

A second mutation arriving while one is active raises
`DaemonControlBusyError`. It is not queued, so separate clients cannot build an
unbounded mutation backlog or interleave scanner command sequences.

Navigation completion requires the scanner's matching `OK` acknowledgement.
Explicit `NG`, `ERR`, or `ERROR` responses are classified as scanner rejection.
Timeout and transport failures remain redacted at the daemon API boundary.

`hold_state(scope, held)` is a separate desired-state contract rather than a
reinterpretation of indexed `hold()`. It accepts `system`, `department`, `site`,
or `channel`, reads authoritative `GSI` before deciding whether any key gesture
is necessary, and no-ops when the requested `On`/`Off` state already matches.
Enabling a hold requires a real current selection index; the SDS200 unsigned-32
maximum sentinel is rejected. Releasing an already held scope does not require
that cached selection to remain available.

The physically verified SDS200 gestures are one `KEY,A,P` for System Hold, one
`KEY,B,P` for Department Hold, `KEY,F,P` followed by `KEY,B,P` for Site Hold,
and one `KEY,C,P` for Channel Hold. Every key in a gesture and all authoritative
completion reads execute inside one daemon mutation. Completion is not inferred
from the key acknowledgement: the runtime polls `GSI` until the requested hold
field reaches the desired state. Site completion intentionally ignores temporary
unrelated field inconsistencies observed during physical validation.

Compatibility navigation and reconnect retain their two-second control maximum.
Semantic hold-state has a separate four-second maximum so the measured SDS200
GSI convergence fits inside the bounded API contract.

Daemon reconnect is available only when `SDSScanner` directly owns an SDS200
`UdpTransport`. Serial, fallback, replay, and injected transports do not
advertise the bounded reconnect capability and are rejected before mutation.

There is no separate `scanner.resume` or unrestricted public `scanner.key`
operation. Release is expressed idempotently as semantic
`scanner.hold_state(..., held=False)`.

## Dynamic PCM destinations

A `PcmSink` may be attached before runtime startup or while the runtime is
running:

```python
runtime.attach_sink(playback)
runtime.attach_sink(recording)
```

Pre-attached destinations start with the router. A destination attached while
running starts immediately. A failed destination start is recorded by
`PcmSinkRouter` and detached without stopping the runtime.

Detach a destination independently:

```python
runtime.detach_sink(recording)
```

The default `stop=True` waits for any in-flight PCM submission and then stops the
destination. Passing `stop=False` only detaches it from future submissions.

No sink may be attached after the runtime reaches a terminal state.

## Snapshots and transitions

`DaemonRuntime.snapshot()` returns an immutable `DaemonRuntimeSnapshot`
containing:

- runtime lifecycle state;
- scanner endpoint and connection state;
- configured PSI interval and current PSI state;
- the latest immutable `RadioStateSnapshot`;
- audio packet, sample, endpoint, and sink statistics;
- the complete `PcmSinkRouterSnapshot`;
- lifecycle timestamps and transition sequence; and
- a redacted last-failure type.

`DaemonRuntimeSnapshot.as_dict()` returns JSON-compatible renderer-neutral data.

Subscribe to ordered lifecycle changes with:

```python
unsubscribe = runtime.on_transition(handle_transition)
```

Each `DaemonRuntimeTransition` includes its sequence, aware UTC observation
timestamp, previous state, new state, and the snapshot captured at that
transition. Listener exceptions are isolated by the shared event bus.

Runtime states are:

- `idle`
- `starting`
- `running`
- `stopping`
- `stopped`
- `failed`

## Python example

```python
from sds200 import (
    AudioFanoutSession,
    AudioStream,
    DaemonRuntime,
    NetworkAudioTransport,
    PcmSinkRouter,
    SDSScanner,
)

host = "192.168.0.251"

scanner = SDSScanner.network(host)
router = PcmSinkRouter(name="daemon-pcm")
audio = AudioFanoutSession(
    AudioStream(NetworkAudioTransport(host)),
    (router,),
)
runtime = DaemonRuntime(scanner, audio, router)

runtime.attach_sink(playback_sink)

with runtime:
    run_application()
```

Application code is responsible for constructing concrete destinations and
deciding how long the runtime remains active.

## Physical SDS200 validation

Validated on 2026-08-04 with a physical SDS200 network endpoint:

- foreground startup opened scanner control, completed the initial PSI response,
  and started the RTSP/RTP decoded-PCM fanout;
- `Ctrl+C` produced a controlled `SIGINT` shutdown with reverse-order cleanup and
  exit status 0;
- an externally delivered `SIGTERM`, matching the documented systemd contract,
  produced reverse-order cleanup and exit status 0; and
- both runs received live scanner audio before shutdown.

The Milestone 19.5 local API layer was also validated on 2026-08-04 against
the same physical SDS200:

- the managed socket directory and socket used modes `0700` and `0600`;
- all six read-only protocol operations returned correlated successful
  responses while scanner control, PSI, audio, and the router were live;
- malformed JSON was isolated and the same client connection remained usable;
- an independent second client completed a capability request;
- the runtime received seven RTP packets and 2,240 decoded samples; and
- `SIGTERM` returned exit status 0 after closing clients and removed the owned
  socket before process exit.

The Milestone 19.6 `events.sock` service was physically validated on
2026-08-05 against the same SDS200:

- the caller-managed validation directory used mode `0700`, and both
  `daemon.sock` and `events.sock` used mode `0600`;
- two independent event clients received authoritative `stream.snapshot`
  envelopes at sequence 11, while a third connection above the configured limit
  was closed without receiving an event;
- the existing request-response API completed a correlated `ping` while both
  event clients remained connected;
- the primary client received 76 valid events from sequence 11 through 86 with
  no gaps, regressions, malformed lines, or reader errors;
- live traffic produced 38 `scanner.psi` and 34 `radio.state` events;
- controlled `SIGTERM` delivered final `audio.state`, `scanner.connection`, and
  `daemon.transition` events while the event service remained active;
- the runtime received 507 RTP packets and 162,240 decoded samples; and
- the process returned exit status 0 and removed both owned sockets.

The Milestone 19.7 `pcmu.sock` service was physically validated on
2026-08-05 against the same SDS200:

- the caller-managed validation directory used mode `0700`, and all three local
  sockets used mode `0600`;
- one API client completed 61 successful pings while one event client and two
  PCMU clients remained connected;
- the event client received 231 ordered messages from sequence 1 through 231
  without a gap;
- both PCMU clients received the same 1,503 frames and 480,960 payload bytes
  without queue loss, overflow, stream gaps, RTP discontinuity, timestamp
  reversal, or mismatched overlapping frames;
- a third PCMU connection above the configured limit was rejected;
- decoded audio advanced by 1,500 packets and 480,000 samples during the
  60-second simultaneous-client interval; and
- controlled `SIGTERM` returned exit status 0 and removed `daemon.sock`,
  `events.sock`, and `pcmu.sock`.

Milestone 19.11 destination activation and reload were physically validated on
2026-08-06 against the same SDS200:

- startup activated an initial recording destination;
- `SIGHUP` transactionally replaced it with recording plus audible playback;
- an invalid version 2 manifest failed with `ConfigurationError` while the
  committed destinations and daemon runtime continued;
- a valid empty manifest removed all active destinations;
- finalized recordings remained valid 8 kHz mono signed 16-bit WAV files; and
- controlled `SIGTERM` returned exit status 0 and removed all three sockets.

Milestone 19.8 safe-control contracts are covered by hardware-independent tests,
including acknowledgements, rejection, deadlines, unsupported transports,
concurrent requests, shutdown interaction, and unchanged read-only operations.

Milestone 20.6 semantic hold-state control was physically validated on
2026-08-08 against SDS200 firmware 1.26.01. Single `A`, `B`, and `C` key presses
toggled System, Department, and Channel Hold in both directions; `F` followed by
`B` toggled Site Hold in both directions without changing Department Hold.
Measured authoritative `GSI` convergence after acknowledgement ranged from
0.115 seconds for Channel release to 2.519 seconds for Site activation. Those
measurements include scanner reporting and polling behavior and are not claimed
as physical switch latency.

The complete safe-control sequence was physically validated on 2026-08-05
against the same SDS200:

- capability negotiation advertised hold, next, previous, and reconnect with the
  documented two-second maximum deadline;
- TGID hold, next, previous, hold release, and bounded reconnect completed with
  increasing control sequences and healthy authoritative runtime snapshots;
- the validator bound navigation to the PSI-reported held selection so normal
  scanning movement between the precondition snapshot and hold acknowledgement
  did not make restoration ambiguous;
- hold returned to `Off`, reconnect produced both connection transitions, and
  API, event, PSI, RTSP/RTP, decoded-audio, and PCMU activity remained healthy;
- two PCMU clients received 410 identical loss-free frames each while the event
  client received 82 ordered messages without a gap; and
- controlled `SIGTERM` returned exit status 0 and removed all three local
  sockets.

### CLI daemon audio client validation

A separate 2026-08-05 physical run exercised `sdsctl daemon-client audio`
through the private PCMU socket with simultaneous default-device playback and
WAV recording. The client received 258 consecutive frames from sequence 16
through 273 and 82,560 samples without PCMU stream gaps, daemon queue loss,
RTP loss, missing samples, or timestamp reversal. The WAV finalized as 8 kHz
mono signed 16-bit PCM with a duration of 10.320 seconds. The independent
bounded playback queue wrote 159,942 bytes and reported six overflows dropping
2,088 PCM bytes without underflow. API health remained authoritative after the
client exited, and controlled `SIGTERM` removed all three sockets with exit
status 0.

### Daemon-backed TUI validation

A physical SDS200 run on August 5, 2026, exercised
`sdsctl tui --daemon-client` through explicit API, event, and PCMU sockets. The
TUI rendered cleanly, followed live scanner state, completed a safe control,
automatically started playback, toggled playback with `A`, and finalized a
53.120-second 8 kHz mono WAV plus metadata. Quitting the TUI left the original
daemon process, scanner connection, PSI, RTSP/RTP audio, and decoded-PCM router
running. Controlled `SIGTERM` subsequently removed all three sockets.

### Daemon PSI silence recovery validation

Milestone 20.6 PSI recovery was physically validated against SDS200 firmware
1.26.01 by dropping only inbound UDP packets from scanner port `50536` to the
daemon's current local scanner-control port. The rule dropped 19 datagrams while
leaving the separate audio socket untouched.

The running daemon detected 10.1 seconds without a parsed PSI frame, logged one
stale-stream warning, executed its bounded reconnect, and reopened scanner control
on a new local UDP port. Ordered `scanner.psi` events resumed immediately and
continued with live state changes. The daemon PID remained unchanged, the web
process stayed running, and daemon audio advanced from 3,997 to 4,495 accepted RTP
packets across the test. This validates recovery from silent PSI without opening
a second scanner controller or intentionally interrupting the independent audio
path.

## Follow-on work

Later work may:

- add bounded decoded-PCM subscriptions for local clients;
- add daemon discovery and automatic client selection; and
- add decoded-PCM CLI client workflows.

Decoded-PCM subscription, discovery, and automatic selection remain follow-on
work. Saved destination activation and validated `SIGHUP` replacement are part
of the current daemon contract.

See [Daemon deployment and upgrade guide](daemon-deployment.md) for service
installation, explicit socket paths, destination manifests, reload, migration,
and upgrade procedures.
