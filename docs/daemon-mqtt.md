# Daemon MQTT publication

Milestone 20.8 adds an optional daemon-owned MQTT publication service. It mirrors
semantic state from the existing authoritative `DaemonEventStream`; it does not
open scanner control hardware, create another PSI subscription, or open another
RTSP/RTP audio session.

Milestone 20.9 adds explicitly opt-in semantic scanner controls on that same
worker. MQTT commands reuse the daemon's existing versioned control dispatcher
and therefore preserve the single-owner scanner boundary. Milestone 20.10 adds
optional Home Assistant MQTT device discovery over the same generic state and
availability topics. Milestone 20.11 consumes that contract from a Home Assistant
App that hosts the existing daemon and web dashboard with Supervisor MQTT service
adaptation and Ingress. Milestone 20.12.2 adds the bundled read-only Lovelace
card. Milestone 20.12.3 adds a separate Home Assistant-specific control
translation layer over the same typed daemon-control dispatcher without enabling
the generic MQTT request-envelope command input.

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
commands_enabled = false
keepalive_seconds = 60
reconnect_initial_delay = 1.0
reconnect_multiplier = 2.0
reconnect_max_delay = 30.0

[home_assistant]
enabled = false
discovery_prefix = "homeassistant"
birth_topic = "homeassistant/status"
birth_payload = "online"
controls_enabled = false
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
| `commands_enabled` | `false` | Explicitly enable semantic scanner-control subscriptions |
| `keepalive_seconds` | `60` | MQTT keepalive |
| `reconnect_initial_delay` | `1.0` | First worker-owned retry delay |
| `reconnect_multiplier` | `2.0` | Exponential retry multiplier |
| `reconnect_max_delay` | `30.0` | Retry-delay ceiling |
| `reconnect_max_attempts` | unset | Optional bounded retry budget; unset retries indefinitely |

The optional `[home_assistant]` table supports:

| Field | Default | Meaning |
| --- | --- | --- |
| `enabled` | `false` | Publish Home Assistant MQTT device discovery and subscribe to the configured birth topic |
| `discovery_prefix` | `homeassistant` | Home Assistant MQTT discovery prefix |
| `birth_topic` | `homeassistant/status` | Exact Home Assistant birth/status topic to subscribe to at QoS 0 |
| `birth_payload` | `online` | Exact UTF-8 payload that triggers discovery republication |
| `controls_enabled` | `false` | Enable the dedicated Home Assistant control adapter; requires `enabled = true` |

The Home Assistant birth topic must not equal `<topic_prefix>/commands` when both
Discovery and generic semantic commands are enabled. When Home Assistant controls
are enabled, the birth topic also must not equal any dedicated Home Assistant
control topic.

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
| `<prefix>/commands` | rejected if retained | inbound version 1 daemon API scanner-control request |
| `<prefix>/responses` | never | correlated version 1 daemon API response |
| `<prefix>/home_assistant/control/hold/<scope>` | never | dedicated Home Assistant `ON` or `OFF` desired-state hold command |
| `<prefix>/home_assistant/control/previous/channel` | never | dedicated Home Assistant `PRESS` command for the current navigable channel |
| `<prefix>/home_assistant/control/next/channel` | never | dedicated Home Assistant `PRESS` command for the current navigable channel |
| `<prefix>/home_assistant/control/reconnect` | never | dedicated Home Assistant `PRESS` reconnect command |

Destination IDs are percent-encoded into one safe MQTT topic segment.

When Home Assistant Discovery is enabled, the worker also publishes one
non-retained device-discovery document at:

```text
<discovery_prefix>/device/sds200_<identity>/config
```

The identity is derived deterministically from `topic_prefix`, so it is stable
across daemon and broker reconnects while that MQTT namespace stays the same.
Changing `topic_prefix` intentionally creates a different Home Assistant device
identity. Two scanners must not share the same broker and exact topic prefix
because their generic MQTT state topics would already collide.

`retain = false` disables retention for canonical semantic state only.
Availability is always retained so consumers can recover daemon availability
without waiting for another state change. The Paho session also configures a
retained `offline` last will at the configured QoS.

## Initial state and ordered updates

After a broker connection succeeds the worker:

1. publishes retained `online`;
2. subscribes to the existing daemon event stream;
3. receives that stream's authoritative `stream.snapshot`;
4. expands the snapshot into canonical state topics;
5. when Home Assistant Discovery is enabled, subscribes to the configured birth
   topic at QoS 0 and publishes the device-discovery document;
6. when Home Assistant controls are enabled, subscribes at QoS 0 to the seven
   exact dedicated Home Assistant control topics;
7. when generic semantic commands are enabled, subscribes to
   `<prefix>/commands` at the configured QoS; and
8. only then resets its reconnect attempt counter and treats the session as
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

## Semantic scanner commands

Commands remain disabled unless the manifest explicitly sets
`commands_enabled = true`. After the worker has published the authoritative
initial state for a broker session, it subscribes to `<prefix>/commands` at the
configured QoS. The Paho callback thread only validates the transport shape and
enqueues the message; scanner-control execution occurs on the MQTT worker thread.

Command payloads use the same strict version 1 `sdsctl.daemon` request envelope
documented by the [local daemon API](daemon-api.md). MQTT admits only these
semantic scanner operations:

- `scanner.hold`
- `scanner.hold_state`
- `scanner.next`
- `scanner.previous`
- `scanner.reconnect`

Read-only API operations, recording operations, unknown operations, and arbitrary
raw scanner keys are not available through the MQTT command input. Parameter
validation, bounded control deadlines, scanner capability checks, authoritative
control results, and stable redacted error codes are shared with `daemon.sock`
through the same `DaemonReadOnlyApi` instance.

Each accepted or rejected command publishes one non-retained
`<prefix>/responses` object using the normal daemon API response envelope. The
MQTT payload omits the JSON-Lines trailing newline used by the Unix-socket API.
The worker rejects command payloads larger than 64 KiB before JSON decoding and
rejects retained command messages without dispatching scanner control.

The worker keeps a bounded process-local cache of the 64 most recent valid
request IDs. A redelivery with the same request ID and identical payload bytes
replays the cached response without executing the scanner control again. Reusing
the same request ID with different payload bytes is rejected. The response is
cached before broker publication and acknowledgement, so a publication or
acknowledgement failure cannot make an immediate QoS redelivery repeat a
non-idempotent `next`, `previous`, or reconnect operation. Cache entries do not
survive daemon restart, and an evicted request ID can be executed again; callers
must therefore generate unique request IDs.

When commands are enabled, the Paho adapter uses manual acknowledgement. QoS 1
and QoS 2 messages are acknowledged only after the response has been published.
The Paho session uses a clean session, so the daemon does not intentionally
accumulate an offline command backlog.

If the daemon event sequence has a gap, the worker closes that subscription,
opens a new one, and waits for a fresh authoritative snapshot before continuing.
This avoids trying to reconstruct missing semantic state from partial events.
A replacement authoritative snapshot also republishes Home Assistant Discovery
when enabled.

## Home Assistant MQTT Discovery

Discovery is disabled by default and remains an adapter over the generic daemon
MQTT state contract rather than a second state system. Enable it with:

```toml
[home_assistant]
enabled = true
```

Without Home Assistant controls, the device document contains the original ten
state/diagnostic components:

| Component | Platform | Source |
| --- | --- | --- |
| Daemon state | sensor | `<prefix>/state/daemon` |
| Scanner connected | binary sensor | `<prefix>/state/scanner/connection` |
| System | sensor | `<prefix>/state/radio` |
| Department | sensor | `<prefix>/state/radio` |
| Channel | sensor | `<prefix>/state/radio` |
| Signal | sensor | `<prefix>/state/radio` |
| RSSI | sensor | `<prefix>/state/radio` |
| Audio running | binary sensor | `<prefix>/state/audio` |
| Recording active | binary sensor | `<prefix>/state/recording` |
| Recording status | sensor | `<prefix>/state/recording` |

The shared device metadata uses Uniden as manufacturer, scanner model and
firmware when available in the authoritative snapshot, and daemon availability.
The discovery document remains non-retained and is republished after an
authoritative snapshot, broker reconnect, event-stream resynchronization, or an
exact configured Home Assistant birth message.

### Home Assistant control adapter

Set:

```toml
[home_assistant]
enabled = true
controls_enabled = true
```

to add seven standard Home Assistant control components to the same discovered
device:

| Component | Platform | Semantic daemon operation |
| --- | --- | --- |
| System Hold | switch | `scanner.hold_state(scope=system)` |
| Department Hold | switch | `scanner.hold_state(scope=department)` |
| Site Hold | switch | `scanner.hold_state(scope=site)` |
| Channel Hold | switch | `scanner.hold_state(scope=channel)` |
| Previous Channel | button | `scanner.previous` |
| Next Channel | button | `scanner.next` |
| Reconnect Scanner | button | `scanner.reconnect` |

These controls do **not** publish client-shaped requests to
`<prefix>/commands`. Each Home Assistant action arrives on one exact dedicated
QoS 0, non-retained topic under `<prefix>/home_assistant/control/`. The worker
rejects retained, duplicate, non-QoS-0, unknown-topic, and invalid-payload
deliveries without dispatching scanner control. For every accepted action, the
adapter generates a fresh internal daemon request identifier and invokes the
existing typed control dispatcher directly. It publishes no Home
Assistant-specific response topic.

The four Hold switches are non-optimistic. Their displayed state comes from the
authoritative retained radio state, and each switch is available only while the
daemon is online, the scanner is connected, and that scope's hold field is a
known `On` or `Off` value. The daemon still performs the authoritative `GSI`
read, desired-state comparison, bounded control operation, and convergence
verification.

Previous Channel and Next Channel are available only while the current ordered
radio state represents a documented navigable channel: `TGID` or
`ConvFrequency` with a valid SDS200 channel index below the unsigned-32
no-current-selection sentinel. Translation reuses the same current-channel
resolver as the TUI and web dashboard, producing `TGID` or `CFREQ` indexed
`scanner.previous`/`scanner.next` requests. The MQTT worker caches only the
latest ordered daemon radio state. Scanner disconnect, broker-session restart,
or an event-sequence gap clears that navigation context until a fresh
authoritative snapshot or ordered state restores it.

Reconnect Scanner is a stateless button and inherits daemon availability. The
daemon API remains authoritative about whether reconnect is supported by the
owned scanner transport.

A semantic control rejection is logged using the daemon's stable error code and
does not fail the MQTT worker. A failed Hold command does not optimistically
change Home Assistant state; later authoritative state remains the source of
truth.

The generic Milestone 20.9 command transport remains independent. It may still
be enabled explicitly with `commands_enabled = true` for daemon-API-shaped MQTT
clients, but the Home Assistant App deliberately keeps that setting false while
enabling the dedicated Home Assistant adapter.

The bundled Lovelace card remains transport-free and read-only. The standard
Home Assistant switch and button entities are the control surface; the card does
not bypass Home Assistant to call MQTT, the daemon, or scanner directly.

With the default `retain = true`, Home Assistant can consume retained canonical
state immediately after Discovery. If `retain = false`, Discovery still works,
but a state-backed entity may remain unknown until its corresponding semantic
state is published again.

## Broker lifecycle and failure isolation

The worker owns broker connectivity in a separate daemon thread. Paho automatic
reconnect is disabled; retry policy belongs to `DaemonMqttWorker`.

The Paho adapter uses callback API version 2 and MQTT 3.1.1. A connection is not
considered established until the broker CONNACK callback succeeds. Publications
wait for Paho publication completion. While the daemon event stream is idle, the
worker checks broker health every event-poll iteration so an asynchronous
disconnect does not require a later scanner event to be detected.

Connection, publication, subscription, inbound-queue, acknowledgement, and
broker-health failures are recorded in the MQTT worker snapshot and enter the
configured reconnect policy. The inbound Paho queue is bounded to 32 messages;
overflow fails the broker session instead of silently dropping an unacknowledged
command. Broker failures do not raise through daemon event callbacks and do not
stop scanner control, PSI, audio, recording, local API/event/PCMU services, or
decoded-PCM destinations.

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

MQTT command subscriptions materially increase the broker trust boundary. Leave
`commands_enabled = false` unless broker publishers are authorized to operate
the scanner. The Home Assistant control adapter is a separate control input:
leave `controls_enabled = false` unless publishers authorized by the broker for
the dedicated Home Assistant control topics are also authorized to operate the
scanner. Both paths remain limited to existing semantic daemon operations and
never expose unrestricted raw scanner-key passthrough.

Home Assistant Discovery also subscribes to its configured birth topic. Keep the
broker within the same trusted boundary. Because the current adapter does not
configure TLS, scanner state, credentials, and Home Assistant control traffic
must not be treated as encrypted in transit and neither control path should be
enabled across an untrusted network.

See [Daemon ownership runtime](daemon-runtime.md),
[Daemon deployment and upgrade guide](daemon-deployment.md), and
[Project Vision](project-vision.md) for the surrounding lifecycle, service, and
Home Assistant direction.
