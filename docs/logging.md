# Operational logging

`sdsctl` writes operational warnings and errors to stderr by default. Global
logging options may be placed before any subcommand:

```bash
sdsctl --log-level INFO --host 192.168.0.251 monitor
sdsctl --log-level DEBUG --log-file /var/log/sdsctl.log \
  --host 192.168.0.251 events
```

Supported levels are `CRITICAL`, `ERROR`, `WARNING`, `INFO`, and `DEBUG`.

Logging settings can also be supplied through the system or user application
configuration files, `SDSCTL_LOG_LEVEL`, and `SDSCTL_LOG_FILE`. Explicit CLI
options and verbosity shortcuts have higher precedence. See
[Layered application configuration](configuration.md).

The existing verbosity shortcuts remain available:

- no `-v`: `WARNING`
- `-v`: `INFO`
- `-vv`: `DEBUG`
- `--log-level`: explicit override of the verbosity-derived level

`--log-file PATH` adds a persistent file handler while retaining stderr output
for non-TUI commands. During an active full-screen TUI session, package records are
redirected from stderr to the bounded in-app log panel; the file handler continues
unchanged, and stderr logging is restored when the TUI exits. The parent directory
is not created automatically. A missing directory or
insufficient permission produces a clear startup error instead of silently
discarding diagnostics.

Normal logs contain lifecycle, recovery, endpoint, timing, and reliability
metadata. Raw scanner command and response traffic is intentionally excluded;
use `--trace PATH` when protocol-level traffic is required. Treat traces and
captures as potentially sensitive scanner data and inspect them before sharing.

## TUI PSI recovery entries

The Textual TUI warns after the configured stale threshold and, by default,
queues an automatic reconnect after 10 seconds without a PSI update. Recovery
is rate-limited to one attempt per 60 seconds. Typical entries include:

```text
WARNING sds200.tui: PSI stream stale endpoint=udp://192.168.0.251:50536 age_seconds=3.1
WARNING sds200.tui: PSI recovery requested endpoint=udp://192.168.0.251:50536 age_seconds=10.1 attempt=1
INFO sds200.tui: PSI reconnect completed endpoint=udp://192.168.0.251:50536 attempt=1 waiting_for_state=true
INFO sds200.tui: PSI stream recovered endpoint=udp://192.168.0.251:50536 outage_seconds=11.4 attempt=1
```

The TUI status panel reports automatic recovery attempts, successful recoveries,
and failed attempts. The operational log panel is visible by default; press `G` to
hide or restore it without discarding buffered records. Scanner control reconnects
remain independent from the
SDS200 RTSP/RTP audio session, so an active WAV recording continues during PSI
recovery.

The behavior is configurable:

```bash
sdsctl --host 192.168.0.251 tui \
  --stale-after 3 \
  --psi-recover-after 10 \
  --psi-recovery-cooldown 60

sdsctl --host 192.168.0.251 tui --no-psi-auto-recover
```

Recovery never begins before the stale threshold. When `--psi-recover-after` is
smaller than `--stale-after`, the stale threshold becomes the effective recovery
delay.

## systemd and journald

For a systemd-managed long-running process, journald is the preferred default;
no log file option is required:

```ini
[Unit]
Description=SDS scanner event stream
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=sdsctl
Group=sdsctl
ExecStart=/opt/sdsctl/bin/sdsctl --log-level INFO --host 192.168.0.251 events
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Inspect the service log with:

```bash
journalctl -u sdsctl.service
journalctl -u sdsctl.service --since today
journalctl -u sdsctl.service -f
```

## `/var/log/sdsctl.log`

Create a dedicated service account and writable file before selecting a path
under `/var/log`:

```bash
sudo install -o sdsctl -g sdsctl -m 0640 /dev/null /var/log/sdsctl.log
```

The file handler watches for inode changes, so standard rename-and-create
logrotate policies work without restarting `sdsctl`:

```text
/var/log/sdsctl.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    create 0640 sdsctl sdsctl
}
```

Install that policy as `/etc/logrotate.d/sdsctl`, then verify it with the
distribution's normal logrotate tooling. Do not combine `copytruncate` with the
watched-file handler; rename-and-create rotation is preferred.

When a dedicated file is selected in a systemd unit, journald still receives
stderr warnings and errors while the file receives all messages allowed by the
chosen level.
