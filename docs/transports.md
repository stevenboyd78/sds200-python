# Control transports

The radio protocol is separated from the connection mechanism. `SDSScanner`
depends on the `ControlTransport` protocol, while `SerialTransport` provides
USB control for the SDS100, SDS150, and SDS200 and `UdpTransport` provides
SDS200 Ethernet control.

## USB serial

USB serial is supported by all three scanner models. Pass `model` to narrow
automatic discovery and verify the `MDL` response:

```python
from sds200 import SDSScanner

with SDSScanner.auto(model="SDS150") as radio:
    print(radio.get_model())
```

An explicit path can also be used:

```python
from sds200 import SDSScanner

radio = SDSScanner("/dev/serial/by-id/usb-UNIDEN_...")
```

## SDS200 Ethernet control

The scanner's virtual-serial network protocol sends ordinary remote commands
as CR-terminated UDP datagrams. It uses scanner port `50536` by default and
does not require negotiation or a protocol header.

```python
from sds200 import SDSScanner

with SDSScanner.network("192.168.1.50") as radio:
    print(radio.get_model())
    print(radio.get_firmware())
```

The same high-level API works over either transport:

```python
with SDSScanner.network("192.168.1.50") as radio:
    radio.on_state_change(
        lambda change: print(change.fields, change.current.channel)
    )
    with radio.scanner_info_push(500):
        radio.wait()
```

Advanced socket options are available when a specific local interface or port
is required:

```python
radio = SDSScanner.network(
    "scanner.local",
    remote_port=50536,
    local_host="192.168.1.10",
    local_port=42000,
)
```

The UDP transport reassembles numbered XML datagrams using the network
`Footer` node's `No` and `EOT` attributes. An incomplete sequence is discarded
rather than being passed to the protocol parser. One-shot commands then time
out normally, while the next periodic `PSI` update can synchronize state again.

UDP is connectionless. `radio.connected` means the local UDP socket is open; it
does not prove that the scanner is powered on or reachable. A command timeout
is the authoritative indication that no response arrived.

The SDS200 network-control protocol has no authentication or encryption layer.
Keep it on a trusted LAN or access it through a VPN. Do not forward UDP port
50536 directly from the public Internet.

Network audio is a separate protocol and is not part of `UdpTransport`.

SDS100 and SDS150 do not use this native UDP control transport.

## Custom transports

A custom transport must expose an endpoint, connection state, CR-delimited
incoming lines, command writes, and lifecycle methods:

```python
from sds200 import SDSScanner

radio = SDSScanner.from_transport(my_transport)
```

This contract allows future connection types to reuse commands, parsing, state,
events, tracing, and monitoring without duplicating radio logic.

## UDP resilience and statistics

Numbered XML fragments are validated using their `Footer` sequence number.
When a fragment is missing or invalid, the transport emits a
`TransportDiagnostic`, discards the incomplete XML, and retries the most recent
`GSI` or `PSI` request. The default retry limit is two:

```python
radio = SDSScanner.network("192.168.0.251", max_xml_retries=3)
```

Transport counters are available through a radio health check:

```python
with SDSScanner.network("192.168.0.251") as radio:
    health = radio.health_check()
    print(health.latency_ms)
    print(health.statistics)
```

A UDP socket being open does not establish remote liveness. The health check's
successful command round trip is the meaningful reachability test.


## Fallback transport

`FallbackTransport` composes ordered serial and UDP candidates while preserving
the `ControlTransport` contract. Candidate reconnect loops are disabled because
the coordinator owns activation, retry, and switching. Transport diagnostics and
active-transport statistics are forwarded through the existing radio events.

Audio does not use `ControlTransport`; see [Audio subsystem architecture](audio.md).
