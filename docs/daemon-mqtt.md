# Daemon MQTT publication

Milestone 20.8 adds an optional daemon-owned MQTT publication service. It mirrors
semantic state from the existing authoritative `DaemonEventStream`; it does not
open scanner control hardware, create another PSI subscription, or open another
RTSP/RTP audio session.

This milestone is the generic broker substrate for later automation and Home
Assistant work. It is publication-only: inbound MQTT commands, Home Assistant
MQTT Discovery, Home Assistant App packaging, Ingress, and the Lovelace card are
not part of this contract yet.

## Installation

MQTT support is optional:

```bash
python -m pip install "sds200[mqtt]"
```

The extra installs Paho MQTT 2.x. When no daemon MQTT manifest exists, normal
daemon startup does not require or preflight Paho.

## Configuration

The default user manifest is:

```text
${XDG_CONFIG_HOME:-~/.config}/sdsctl/daemon-mqtt.toml
```

Select another file with:

```bash
sdsctl --host 192.168.0.251 daemon \
  --mqtt-config /etc/sdsctl/daemon-mqtt.toml
```

The manifest is strict version 1 and must contain a `[broker]` table:

```toml
version = 1

[broker]
host = "mqtt.example.lan"
port = 1883
client_id = "sdsctl-scanner"
username = "sdsctl"
password_environment_variable = "SDSCTL_MQTT_PASSWORD"
topic_prefix = "sdsctl/scanner"
qos = 1
retain = true
keepalive_seconds = 60
reconnect_initial_delay = 1.0
reconnect_multiplier = 2.0
reconnect_max_delay = 30.0
```

Supported broker fields are:

| Field | Default | Meaning |
| --- | --- | --- |
| `host` | required | Broker hostname or address, not a URL |
| `port` | `1883` | Broker TCP port |
| `client_id` | unset | Optional MQTT client ID |
| `username` | unset | Optional broker username |
| `password_environment_variable` | unset | Environment-variable name containing the password |
| `topic_prefix` | `sdsctl` | Root topic; wildcards and empty levels are rejected |
| `qos` | `1` | MQTT QoS `0`, `1`, or `2` |
| `retain` | `true` | Whether canonical semantic state topics are retained |
| `keepalive_seconds` | `60` | MQTT keepalive |
| `reconnect_initial_delay` | `1.0` | First worker-owned retry delay |
| `reconnect_multiplier` | `2.0` | Exponential retry multiplier |
| `reconnect_max_delay` | `30.0` | Retry-delay ceiling |
| `reconnect_max_attempts` | unset | Optional bounded retry budget; unset retries indefinitely |

The daemon loads and validates this document before scanner construction. If the
file is absent, MQTT is disabled. If the file is present but invalid, or Paho is
not installed, startup fails before the scanner is selected or opened.

A password reference requires `username`. Only the environment-variable name is
stored in TOML or serialized configuration. At worker connection time the value is
resolved from the daemon environment; missing or empty values become isolated
MQTT worker failures. Resolved password values are redacted from worker failure
diagnostics.

## Topic contract

Every topic is rooted at `topic_prefix`.

| Topic | Retained | Payload |
| --- | --- | --- |
| `<prefix>/availability` | always | literal `online` or `offline` |
| `<prefix>/state/daemon` | follows `retain` | daemon lifecycle timestamps, transition sequence, and last failure |
| `<prefix>/state/scanner/info` | follows `retain` | endpoint, model, firmware, PSI interval, and PSI-active state |
| `<prefix>/state/scanner/connection` | follows `retain` | endpoint and connected boolean |
| `<prefix>/state/radio` | follows `retain` | current semantic `RadioStateSnapshot` mapping |
| `<prefix>/state/audio` | follows `retain` | current daemon audio state |
| `<prefix>/state/recording` | follows `retain` | current daemon recording state |
| `<prefix>/state/destinations/<id>` | follows `retain` | one decoded-PCM destination health snapshot |
| `<prefix>/events` | never | original non-snapshot semantic daemon event JSON envelope |

Destination IDs are percent-encoded into one safe MQTT topic segment.

`retain = false` disables retention for canonical semantic state only.
Availability is always retained so consumers can recover daemon availability
without waiting for another state change. The Paho session also configures a
retained `offline` last will at the configured QoS.

## Initial state and ordered updates

After a broker connection succeeds the worker:

1. publishes retained `online`;
2. subscribes to the existing daemon event stream;
3. receives that stream's authoritative `stream.snapshot`;
4. expands the snapshot into canonical state topics; and
5. only then resets its reconnect attempt counter and treats the session as
   healthy.

Later daemon transitions refresh the canonical runtime-derived topics from the
transition's authoritative snapshot. Scanner connection, radio state, audio,
recording, and destination-health events update their corresponding state topics.

The worker does **not** forward `scanner.psi`. PSI arrives at packet/update rate
and is deliberately skipped before either state or event publication. Semantic
`radio.state` changes remain eligible for MQTT.

Every other non-snapshot semantic daemon event is also copied to
`<prefix>/events` as the original JSON event envelope without its trailing
newline. That event topic is never retained.

If the daemon event sequence has a gap, the worker closes that subscription,
opens a new one, and waits for a fresh authoritative snapshot before continuing.
This avoids trying to reconstruct missing semantic state from partial events.

## Broker lifecycle and failure isolation

The worker owns broker connectivity in a separate daemon thread. Paho automatic
reconnect is disabled; retry policy belongs to `DaemonMqttWorker`.

The Paho adapter uses callback API version 2 and MQTT 3.1.1. A connection is not
considered established until the broker CONNACK callback succeeds. Publications
wait for Paho publication completion. While the daemon event stream is idle, the
worker checks broker health every event-poll iteration so an asynchronous
disconnect does not require a later scanner event to be detected.

Connection, publication, event-subscription, and broker-health failures are
recorded in the MQTT worker snapshot and enter the configured reconnect policy.
They do not raise through daemon event callbacks and do not stop scanner control,
PSI, audio, recording, local API/event/PCMU services, or decoded-PCM
destinations.

If `reconnect_max_attempts` is exhausted, the MQTT worker enters its terminal
`failed` state and waits for daemon shutdown. The rest of the daemon continues
running.

A session that successfully published `online` attempts retained `offline` before
every graceful connection close, including local worker failures and normal
daemon shutdown. If the network connection is lost before that publication can
succeed, the broker's retained offline last will is the fallback.

## Daemon lifecycle ordering

The process host starts MQTT after `DaemonRuntime` and before daemon PCM
destinations. This makes the runtime authoritative before the first MQTT snapshot
while allowing later destination health to flow through the same event stream.

On shutdown, API clients and recording readers are stopped first, active daemon
recording is finalized, and configured destinations stop. MQTT then publishes
offline when possible and stops before `DaemonRuntime`, PCMU, and event services.
Keeping runtime and the event stream alive through MQTT shutdown preserves a clean
availability boundary.

## Security boundary

The current Paho adapter connects to the configured TCP host and port using MQTT
3.1.1 and does not configure TLS. Username/password authentication therefore does
not encrypt credentials or scanner state in transit.

Keep the broker on localhost, a trusted local network, or a trusted VPN. Do not
treat broker authentication alone as transport security, and do not expose this
foundation over an untrusted network.

Milestone 20.8 also has no inbound MQTT command subscription. Future control work
must route through the daemon's existing semantic operations and preserve the
single-owner scanner boundary; unrestricted raw scanner-key control is not part
of the MQTT design.

See [Daemon ownership runtime](daemon-runtime.md),
[Daemon deployment and upgrade guide](daemon-deployment.md), and
[Project Vision](project-vision.md) for the surrounding lifecycle, service, and
Home Assistant direction.
