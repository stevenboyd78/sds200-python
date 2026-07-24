# Reliability and observability

Version 0.7.0 adds a shared recovery policy, bounded health history, structured
events, and endpoint repair while keeping network audio deferred.

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
latency, connection changes, reconnects, failovers, and up to five recent
errors. History is process-local and intentionally not persisted.

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

Repair preserves the profile name, bind configuration, and fallback preference.
It updates only unambiguous discovery matches and refuses to overwrite a profile
when discovery cannot safely identify the required scanner.
