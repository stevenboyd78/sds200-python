# Control transports

The radio protocol is intentionally separated from the connection mechanism.
`SDS200` depends on the `ControlTransport` protocol, while `SerialTransport`
implements the current USB serial connection.

```python
from sds200 import SDS200, SerialTransport

transport = SerialTransport("/dev/serial/by-id/usb-UNIDEN_...")
radio = SDS200.from_transport(transport)
```

A transport must expose an endpoint, connection state, CR-delimited incoming
lines, command writes, and lifecycle methods. This contract is also the basis
for a future SDS200 UDP network transport.

The official SDS200 virtual-serial network protocol uses UDP port 50536 with
no negotiation or protocol header for ordinary remote commands. XML responses
can span multiple UDP datagrams and use numbered footer nodes for loss and
end-of-transmission detection. The network implementation will therefore keep
packet reassembly inside the transport and present the same line-oriented
interface to the radio protocol layer.
