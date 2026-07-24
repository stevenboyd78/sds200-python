# Discovery profiles and transport fallback

Version 0.7.0 can create a connection profile directly from discovery:

```bash
sds200 profile discover home \
  --network 192.168.0.0/24 \
  --prefer network
```

When one USB scanner and one network scanner are found, the saved profile is a
`fallback` profile containing both endpoints. If only one endpoint is found, a
normal serial or network profile is saved. When exactly one endpoint of each
type is found, the library assumes they refer to the same scanner because the
control protocols do not expose a shared unique identifier. Discovery refuses
to guess when more than one scanner of the same transport type is present.

Use the profile normally:

```bash
sds200 --profile home monitor
```

Override its saved preference for one invocation:

```bash
sds200 --profile home --prefer serial monitor
```

The preferred transport is attempted first. If it cannot connect, the alternate
is used. If the active transport disconnects later, `FallbackTransport` switches
to the next candidate and preserves the high-level `SDSScanner` API. Fallback
profiles are SDS200-only because the handheld models do not expose native UDP
control. A command whose
write itself detects the failure is retried once after a successful switch.

Fallback does not automatically switch back to the preferred transport while the
alternate remains healthy. This avoids unnecessary command interruptions and
transport flapping.


## Reconnect policy

After a live disconnect, fallback sweeps the remaining candidates immediately.
If every candidate fails, subsequent sweeps use capped exponential backoff. The
wait is interruptible, so `Ctrl-C` and normal shutdown do not wait for the delay
to expire.

```bash
sds200 --profile home \
  --reconnect-attempts 8 \
  --reconnect-initial-delay 1 \
  --reconnect-multiplier 2 \
  --reconnect-max-delay 30 \
  monitor
```

A reconnect-attempt count of `0` means unlimited recovery. Failover statistics
record the previous endpoint, active endpoint, reason, attempts, failures, and
exhaustion count.
