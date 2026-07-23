from sds200 import SDS200

with SDS200.auto() as radio:
    radio.on_packet(lambda packet: print(packet.raw, flush=True))
    radio.wait()
