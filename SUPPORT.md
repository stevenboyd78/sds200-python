# Support

## Where to ask

Use a GitHub issue for reproducible bugs and feature requests. Choose the
appropriate issue form so the report includes the information needed to
investigate it.

Before opening an issue:

1. Install the latest code from the default branch.
2. Run `sds200 health` for the affected connection.
3. Run the project checks if you are developing locally.
4. Search existing issues for the same behavior.

## Information to include

- `sds200` version
- Python version
- Operating system and version
- Scanner model and firmware version
- USB serial or SDS200 Ethernet transport
- Exact command used
- Complete error message or traceback
- Minimal steps to reproduce
- Whether the same operation works over the other transport

For network problems, include:

```bash
sds200 --host SCANNER_IP health
sds200 -vv --host SCANNER_IP scanner-info
```

A traffic trace can be created with:

```bash
sds200 --trace scanner.trace --host SCANNER_IP monitor
```

Review and sanitize traces before attaching them. They can contain scanner
system names, channel names, unit identifiers, and network addresses.

## Scope

The project can help with:

- Installing and using the Python library
- SDS-series USB and SDS200 Ethernet control behavior
- Protocol parsing and typed API issues
- LAN discovery, profiles, monitoring, and diagnostics
- Reproducible compatibility reports

The project cannot provide:

- Emergency communications or dispatch support
- Scanner programming database support
- General radio-system authorization or legal advice
- Uniden warranty or hardware repair service
- A guarantee that every firmware revision behaves identically

For vulnerabilities, use the process in [SECURITY.md](SECURITY.md).
