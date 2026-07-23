# Control transports

The radio protocol is separated from the connection mechanism. `SDS200`
depends on the `ControlTransport` protocol, while `SerialTransport` and
`UdpTransport` provide USB and Ethernet implementations.

## USB serial

```python
from sds200 import SDS200

with SDS200.auto() as radio:
    print(radio.get_model())
```

An explicit path can also be used:

```python
from sds200 import SDS200

radio = SDS200("/dev/serial/by-id/usb-UNIDEN_...")
```

## SDS200 Ethernet control

The scanner's virtual-serial network protocol sends ordinary remote commands
as CR-terminated UDP datagrams. It uses scanner port `50536` by default and
does not require negotiation or a protocol header.

```python
from sds200 import SDS200

with SDS200.network("192.168.1.50") as radio:
    print(radio.get_model())
    print(radio.get_firmware())
```

The same high-level API works over either transport:

```python
with SDS200.network("192.168.1.50") as radio:
    radio.on_state_change(
        lambda change: print(change.fields, change.current.channel)
    )
    with radio.scanner_info_push(500):
        radio.wait()
```

Advanced socket options are available when a specific local interface or port
is required:

```python
radio = SDS200.network(
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

## Custom transports

A custom transport must expose an endpoint, connection state, CR-delimited
incoming lines, command writes, and lifecycle methods:

```python
from sds200 import SDS200

radio = SDS200.from_transport(my_transport)
```

This contract allows future connection types to reuse commands, parsing, state,
events, tracing, and monitoring without duplicating radio logic.
