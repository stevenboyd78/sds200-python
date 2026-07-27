# Reliability and observability

The reliability layer provides reconnect backoff, fallback failover, opt-in
preferred transport recovery, bounded health history, structured events,
endpoint repair, and independent SDS200 RTP audio telemetry.

## Reconnect policy

Serial, UDP, and fallback transports use `ReconnectPolicy` after a live
disconnect. Delays grow exponentially and stop at the configured maximum.

```python
from sds200 import ReconnectPolicy

policy = ReconnectPolicy(
    initial_delay=1.0,
    multiplier=2.0,
    max_delay=30.0,
    max_attempts=8,
)
```

`max_attempts=None` retries indefinitely. CLI value `--reconnect-attempts 0`
selects that behavior. Reconnect waits use the transport stop event, so shutdown
cancels them immediately.

## Health history

Every explicit health check or snapshot is recorded in a bounded in-memory
history. The default limit is 100 samples and can be changed with
`--health-history-limit` or the `SDSScanner` constructor.

```bash
sdsctl --profile home health --watch 5 --history
sdsctl --profile home health --watch 5 --history --json
```

The summary reports sample counts by status, error rate, average and maximum
latency, connection changes, reconnects, failovers, preferred recoveries, and
up to five recent errors. History is process-local and intentionally not persisted.

## Network audio statistics

`NetworkAudioTransport.statistics` is a per-session snapshot independent from
radio health history. It reports datagrams and bytes received, PCMU packets and
payload bytes delivered, sequence gaps, estimated packet loss, duplicates, late
and malformed packets, timestamp discontinuities and missing samples, socket and
callback errors, keepalive results, teardown count, sequence endpoints, final RTP
timestamp, and SSRC.

The `sdsctl audio` summary prints the most actionable counters after recording:

```text
RTP lost: 0
RTP duplicates: 0
RTP late: 0
RTP malformed: 0
Timestamp discontinuities: 0
```

A five-minute wired-LAN hardware soak delivered 7,500 320-sample packets without
loss, duplication, reordering, malformed datagrams, or timestamp discontinuities.

## Structured events

```bash
sdsctl --profile home events --json
```

The command emits JSON Lines suitable for log processors. Event categories
include:

- `connection.connected` and `connection.disconnected`
- `transport.reconnect_scheduled`, `transport.reconnect_failed`, and
  `transport.reconnect_exhausted`
- `transport.failover_requested` and `transport.transport_activated`
- `transport.preferred_recovery_probe`,
  `transport.preferred_recovery_probe_failed`,
  `transport.preferred_recovery_deferred`, and
  `transport.preferred_recovery_succeeded`
- `state.changed`

Each event includes an ISO-8601 timestamp, endpoint, message, and structured
data. State events include the changed field names and current synchronized
state.

## Status thresholds

A connected response below 750 ms is healthy by default. Latency at or above
750 ms is degraded, and latency at or above 2000 ms is unhealthy. A failed
check is degraded while connected; a closed transport is disconnected. Python
callers can provide custom `HealthThresholds` through `SDSScanner.from_transport`.

## Profile repair

```bash
sdsctl profile repair home --network 192.168.0.0/24 --dry-run
sdsctl profile repair home --network 192.168.0.0/24
```

Repair preserves the profile name, bind configuration, fallback preference, and preferred-recovery policy.
It updates only unambiguous discovery matches and refuses to overwrite a profile
when discovery cannot safely identify the required scanner.
