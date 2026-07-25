# Discovery profiles, failover, and preferred recovery

Fallback profiles contain both SDS200 control endpoints: USB serial and native
UDP. Create one from discovery:

```bash
sdsctl profile discover home \
  --network 192.168.0.0/24 \
  --prefer network
```

When one USB scanner and one network scanner are found, the saved profile is a
`fallback` profile containing both endpoints. If only one endpoint is found, a
normal serial or network profile is saved. Discovery refuses to guess when more
than one scanner of the same transport type is present.

Manual fallback profiles are also supported:

```bash
sdsctl profile add home \
  --port /dev/serial/by-id/usb-UNIDEN_AMERICA_CORP._SDS200_Serial_Port-if00 \
  --host 192.168.0.251 \
  --prefer network
```

Use the profile normally:

```bash
sdsctl --profile home monitor
```

Override its saved preference for one invocation:

```bash
sdsctl --profile home --prefer serial monitor
```

The preferred transport is attempted first. If it cannot connect, the alternate
is used. If the active transport disconnects later, `FallbackTransport` switches
to the next candidate and preserves the high-level `SDSScanner` API. A command
whose write detects the failure is retried once after a successful switch.
Fallback profiles are SDS200-only because the handheld models do not expose
native UDP control.

## Preferred transport recovery

Automatic return to the preferred endpoint is opt-in so existing profiles keep
their non-flapping behavior. Enable it while creating or discovering a fallback
profile:

```bash
sdsctl profile discover home \
  --network 192.168.0.0/24 \
  --prefer network \
  --recover-preferred \
  --recovery-probe-interval 30 \
  --recovery-probe-timeout 2 \
  --recovery-stability-window 5 \
  --recovery-cooldown 30
```

The recovery coordinator runs only while an alternate endpoint is active. It:

1. waits for the cooldown and probe interval;
2. opens the inactive higher-priority endpoint;
3. sends the read-only `MDL` command and verifies the expected SDS200 response;
4. keeps the probe connection alive for the configured stability window;
5. sends and validates a second `MDL` probe;
6. promotes the endpoint only when no request/response command is pending.

The active fallback remains available throughout probing. Failed probes do not
disconnect or replace it. A successful promotion is seamless at the fallback
connection boundary, and an active PSI stream is restarted on the recovered
endpoint.

Override saved settings for one process:

```bash
sdsctl --profile home \
  --recover-preferred \
  --recovery-probe-interval 15 \
  --recovery-stability-window 10 \
  monitor
```

Disable recovery from an enabled profile temporarily:

```bash
sdsctl --profile home --no-recover-preferred monitor
```

`health` transport statistics include whether recovery is enabled, probe
attempts and failures, deferred promotions, successful recoveries, policy
values, and the last probe, recovery, and failure details. Structured event
kinds include `transport.preferred_recovery_probe`,
`transport.preferred_recovery_probe_failed`,
`transport.preferred_recovery_deferred`, and
`transport.preferred_recovery_succeeded`.

## Reconnect policy

After a live disconnect, fallback sweeps the remaining candidates immediately.
If every candidate fails, subsequent sweeps use capped exponential backoff. The
wait is interruptible, so `Ctrl-C` and normal shutdown do not wait for the delay
to expire.

```bash
sdsctl --profile home \
  --reconnect-attempts 8 \
  --reconnect-initial-delay 1 \
  --reconnect-multiplier 2 \
  --reconnect-max-delay 30 \
  monitor
```

A reconnect-attempt count of `0` means unlimited recovery. Failover statistics
record the previous endpoint, active endpoint, reason, attempts, failures, and
exhaustion count.
