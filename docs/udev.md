# Optional Linux udev rule

Some immutable or desktop-oriented Linux distributions do not grant the active
user access to a newly attached scanner serial port. The project includes an
optional local udev rule at `contrib/udev/70-uniden-sds.rules`.

The rule matches Uniden USB vendor `1965`, product `001a`, and an
`ID_MODEL` value shaped like `SDS*_Serial_Port`. It applies the systemd-logind
`uaccess` tag, retains a `dialout`/`0660` fallback, and asks ModemManager not to
probe the scanner. It does not use globally writable mode `0666`.

Install it manually:

```bash
sudo install -Dm0644 \
  contrib/udev/70-uniden-sds.rules \
  /etc/udev/rules.d/70-uniden-sds.rules

sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=tty
```

Unplug and reconnect the scanner, then verify the active user can open the
resolved device:

```bash
test -r /dev/ttyACM0 && test -w /dev/ttyACM0 \
  && echo "Scanner is accessible" \
  || echo "Scanner is not accessible"
```

The rule is opt-in and is not installed by the Python package. Remove it with:

```bash
sudo rm /etc/udev/rules.d/70-uniden-sds.rules
sudo udevadm control --reload-rules
```
