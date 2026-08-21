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

## Rootless container access

Host udev policy and container device policy are separate layers. Before a
rootless container can use the scanner, the invoking host user must already be
able to read and write the resolved character device. Do not use a container to
bypass a host permission failure.

For `compose.usb.yaml`, derive the current device GID rather than assuming
`dialout` or GID 20:

```bash
export SDSCTL_USB_DEVICE=/dev/serial/by-id/usb-UNIDEN_AMERICA_CORP._SDS200_Serial_Port-if00
export SDSCTL_USB_GID="$(stat -Lc '%g' "$(readlink -f "$SDSCTL_USB_DEVICE")")"
```

The Compose model maps only that selected character device. Native Docker keeps
the numeric supplemental-group contract, while the validated rootless
Podman/crun path additionally uses the OCI annotation
`run.oci.keep_original_groups: "1"` to retain the invoking operator's legitimate
supplementary host groups.

Do not use privileged mode, map all of `/dev`, or change the scanner device to
mode `0666` as a permission workaround.

### SELinux and AppArmor

The 2026-08-21 physical rootless Podman acceptance host did not have SELinux
enabled, so physical SELinux device-policy acceptance is not claimed. Podman
documents the `container_use_devices` SELinux boolean for container access to
host devices. An administrator who accepts that host-wide policy may enable it
explicitly:

```bash
sudo setsebool -P container_use_devices=true
```

The package, image, and Compose files never modify that policy automatically. A
site that cannot enable the broader boolean should use an administrator-maintained
local SELinux policy rather than widening container privileges.

AppArmor was effectively inactive on the same validation host, so no physical
AppArmor acceptance claim is made either. Distribution-specific mandatory
access control remains a host-administration responsibility.
