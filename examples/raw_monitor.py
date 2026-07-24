from sds200 import SDSScanner

with SDSScanner.auto() as radio:
    radio.on_packet(lambda packet: print(packet.raw, flush=True))
    radio.wait()
