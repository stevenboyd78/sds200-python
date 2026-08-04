# Daemon ownership runtime

Milestone 19.3 introduces the renderer-neutral `DaemonRuntime` ownership
foundation. It coordinates one scanner control session, continuous PSI, one
SDS200 RTSP/RTP audio session, one decoded-PCM fanout, and dynamic PCM
destinations.

Milestone 19.4 adds `DaemonSignalController`, `DaemonProcess`, and the foreground
`sdsctl daemon` command. The process host owns signal installation, waits for a
shutdown request, stops the runtime deterministically, and restores the previous
signal handlers before returning.

This foundation is not a socket server, local API, event-stream transport, or
CLI/TUI daemon-client mode. It does not fork or create a pidfile.

## Foreground process contract

Start the process with an explicit SDS200 network host:

```bash
sdsctl --log-level INFO --host 192.168.0.251 daemon
```

A saved network or fallback profile is also accepted:

```bash
sdsctl --log-level INFO --profile home daemon
```

The command constructs exactly one `DaemonRuntime`, one `PcmSinkRouter`, and one
`NetworkAudioTransport`. The router begins without destinations because local
APIs, client subscriptions, playback, recording, and remote-profile activation
remain follow-on work.

The audio endpoint must come from either `--host` or a network-capable SDS200
profile. A fallback profile may select serial control at runtime, but its saved
network host still supplies the RTSP/RTP audio endpoint. Serial-only profiles,
bare serial selection, replay captures, and non-SDS200 network-audio selections
are rejected.

The command runs in the foreground. It does not daemonize itself, fork, create a
pidfile, change privileges, install a service unit, or request socket activation.

### Signals and exit behavior

`DaemonSignalController` installs handlers only for `SIGINT` and `SIGTERM`.
Those handlers record the signal and set a thread-safe stop event; runtime
teardown occurs in normal process flow rather than inside the signal handler.

Previous signal handlers are restored after the process loop exits. Partial
signal-installation failures roll back handlers that were already replaced.
Restoration attempts continue after an individual restoration failure.

A controlled `SIGINT` or `SIGTERM` shutdown returns success after the runtime
stops. Startup, configuration, transport, or shutdown failures produce the
normal `sdsctl` error path. When process work and cleanup both fail, the primary
process failure remains authoritative and the cleanup failure is logged by type
without exposing its message.

`SIGHUP` is deliberately outside this graceful-shutdown contract so a future
milestone can define reload behavior. Service managers should use `SIGTERM` for
orderly termination.

## Ownership graph

One runtime owns:

1. one `SDSScanner` control connection;
2. one active PSI scanner-information stream;
3. one `AudioFanoutSession`;
4. one `PcmSinkRouter` included in that fanout; and
5. any playback, recording, streaming, or integration sinks attached to the
   router.

The scanner's PCMU audio is accepted once, decoded once, and submitted to the
router as 8 kHz mono signed 16-bit PCM. Each attached sink retains its existing
bounded buffering, worker, health, and failure-isolation behavior.

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

## Follow-on work

Later Milestone 19 work may:

- add bounded PCMU subscriptions for local clients;
- expose snapshots and transitions through a local API and event stream;
- activate configured playback, recording, and remote destinations; and
- allow CLI and TUI clients to select daemon-owned sessions while preserving an
  explicit standalone mode.

Those transport, authentication, destination-activation, and client-selection
contracts are not part of Milestone 19.4.
