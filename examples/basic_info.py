from sds200 import SDSScanner

with SDSScanner.auto() as radio:
    print("Port:", radio.port)
    print("Model:", radio.get_model())
    print("Firmware:", radio.get_firmware())
    print("Volume:", radio.get_volume())
    print("Squelch:", radio.get_squelch())
