# Layered application configuration

`sdsctl` loads optional application-wide operational settings from versioned
TOML files, environment variables, and explicit command-line options.

This application configuration is separate from saved scanner connection
profiles. The existing global `--config PATH` option still selects the legacy
connection-profile document; it does not select an application configuration
file.

## Precedence

Values are resolved in this fixed order, from lowest to highest precedence:

1. built-in defaults;
2. system configuration;
3. user configuration;
4. `SDSCTL_*` environment variables;
5. explicit command-line arguments.

Absent values do not override lower-precedence sources. An explicitly supplied
value remains an override even when it equals the built-in default.

The verbosity shortcuts are explicit command-line logging overrides:

- `-v` selects `INFO`;
- `-vv` selects `DEBUG`;
- `--log-level LEVEL` takes priority when supplied with a verbosity shortcut.

## Paths

The optional application configuration files are:

- system: `/etc/sdsctl/config.toml`;
- user: `${XDG_CONFIG_HOME:-~/.config}/sdsctl/config.toml`.

The default daemon destination manifest is:

- `${XDG_CONFIG_HOME:-~/.config}/sdsctl/daemon-destinations.toml`.

The default optional daemon MQTT manifest is:

- `${XDG_CONFIG_HOME:-~/.config}/sdsctl/daemon-mqtt.toml`.

The destination and MQTT manifests are separate versioned daemon documents; they
are not fields in the flat application configuration schema.

Path resolution also defines persistent service locations:

- state: `${XDG_STATE_HOME:-~/.local/state}/sdsctl/`;
- cache: `${XDG_CACHE_HOME:-~/.cache}/sdsctl/`.

Resolving or loading configuration does not create directories or files.
`XDG_CONFIG_HOME`, `XDG_STATE_HOME`, and `XDG_CACHE_HOME` must be absolute when
set.

Known legacy files remain in the `sds200` configuration root:

- `${XDG_CONFIG_HOME:-~/.config}/sds200/profiles.toml`;
- `${XDG_CONFIG_HOME:-~/.config}/sds200/remote-audio-profiles.toml`.

Milestone 19.1 provides read-only legacy discovery. It does not move, replace,
rewrite, or delete legacy data.

## TOML document

Application configuration documents use schema version 1. The `[application]`
table is optional, so a document containing only the version is valid.

```toml
version = 1

[application]
max_xml_retries = 4
reconnect_attempts = 8
reconnect_initial_delay = 1.0
reconnect_multiplier = 2.0
reconnect_max_delay = 30.0
health_history_limit = 250
color = "auto"
theme = "dark"
log_level = "INFO"
log_file = "/var/log/sdsctl.log"
```

Unknown top-level fields, unsupported application fields, malformed TOML,
unsupported versions, invalid types, invalid ranges, and non-finite numeric
values are rejected before scanner startup. Diagnostics identify the source and
path without including unsupported field values.

## Supported settings

| TOML field | Built-in default | Environment variable | CLI option |
| --- | --- | --- | --- |
| `max_xml_retries` | `2` | `SDSCTL_MAX_XML_RETRIES` | `--max-xml-retries` |
| `reconnect_attempts` | `0` | `SDSCTL_RECONNECT_ATTEMPTS` | `--reconnect-attempts` |
| `reconnect_initial_delay` | `1.0` | `SDSCTL_RECONNECT_INITIAL_DELAY` | `--reconnect-initial-delay` |
| `reconnect_multiplier` | `2.0` | `SDSCTL_RECONNECT_MULTIPLIER` | `--reconnect-multiplier` |
| `reconnect_max_delay` | `30.0` | `SDSCTL_RECONNECT_MAX_DELAY` | `--reconnect-max-delay` |
| `health_history_limit` | `100` | `SDSCTL_HEALTH_HISTORY_LIMIT` | `--health-history-limit` |
| `color` | `auto` | `SDSCTL_COLOR` | `--color`, `--no-color` |
| `theme` | `dark` | `SDSCTL_THEME` | `--theme` |
| `log_level` | unset | `SDSCTL_LOG_LEVEL` | `--log-level`, `-v`, `-vv` |
| `log_file` | unset | `SDSCTL_LOG_FILE` | `--log-file` |

`reconnect_attempts = 0` means retry indefinitely. Delay values must be positive,
the multiplier must be at least `1`, and the maximum delay must be at least the
initial delay.

`color` accepts `auto`, `always`, or `never`. When the resolved value is `auto`,
the presentation layer continues to honor `NO_COLOR` and `FORCE_COLOR` as
documented in [Presentation and accessibility](presentation.md).

`theme` accepts `dark` or `light`. Logging levels are `CRITICAL`, `ERROR`,
`WARNING`, `INFO`, or `DEBUG`.

## Environment examples

```bash
export SDSCTL_RECONNECT_ATTEMPTS=8
export SDSCTL_HEALTH_HISTORY_LIMIT=250
export SDSCTL_THEME=light
export SDSCTL_LOG_LEVEL=INFO

sdsctl --profile home monitor
```

Environment parsing reports the variable name and expected type without echoing
the rejected value.

## Explicit CLI overrides

Command-line settings have the highest precedence:

```bash
SDSCTL_THEME=light \
  sdsctl --theme dark --reconnect-attempts 0 --profile home monitor
```

Here, the explicit dark theme and unlimited reconnect policy override the
environment and both TOML files.

## Daemon MQTT configuration

Milestone 20.8 adds a separate strict version 1
`daemon-mqtt.toml` document for the optional daemon-owned broker integration.
Its `[broker]` table configures host, port, client ID, username, password
environment-variable reference, topic prefix, QoS, semantic-state retention,
keepalive, and reconnect policy. An absent file means MQTT is disabled and does
not require the optional Paho dependency.

The daemon accepts `--mqtt-config PATH` as an explicit manifest override and
loads and validates that document before constructing scanner hardware. Resolved
password values are never serialized back into configuration; only the
environment-variable name is stored.

See [Daemon MQTT publication](daemon-mqtt.md) for the complete manifest and
topic contracts.

## Python API

The public API exposes immutable values and per-field provenance:

```python
from sds200 import load_application_configuration

resolved = load_application_configuration()
configuration = resolved.configuration

print(configuration.reconnect_policy)
print(resolved.source_for("reconnect_attempts"))
print(resolved.origin_for("log_level"))
```

Use `resolve_configuration_paths()` with injected environment and home values
when applications or tests need deterministic path resolution without touching
the host user's directories.

## Secrets

The version 1 application schema contains no credential-bearing fields. Unknown
fields are reported by name and source, not by value.

Saved remote-audio destinations continue to store environment-variable secret
references rather than resolved passwords. The daemon destination manifest
selects those profiles by name and never stores resolved credentials. The daemon
MQTT manifest follows the same rule: `password_environment_variable` stores only
the environment-variable name, and a password reference requires an MQTT
username. Resolved credentials must not be written to application configuration,
destination configuration, MQTT configuration, logs, exceptions, traces, or
serialized output.

See [Daemon deployment and upgrade guide](daemon-deployment.md) for systemd,
destination-manifest, service-account, migration, and upgrade examples.
