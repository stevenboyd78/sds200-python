from sds200 import SDS200

with SDS200.auto() as radio:
    print("Port:", radio.port)
    print("Model:", radio.get_model())
    print("Firmware:", radio.get_firmware())
    print("Volume:", radio.get_volume())
    print("Squelch:", radio.get_squelch())
