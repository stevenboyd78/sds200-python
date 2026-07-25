import time

import serial

port = "/dev/ttyACM0"

with serial.Serial(
    port=port,
    baudrate=115200,
    timeout=0.25,
    write_timeout=0.25,
) as scanner:
    scanner.reset_input_buffer()
    scanner.write(b"GCS\r")
    scanner.flush()

    deadline = time.monotonic() + 3
    received = bytearray()

    while time.monotonic() < deadline:
        chunk = scanner.read(512)
        if chunk:
            received.extend(chunk)
            print("chunk:", repr(chunk))

    print("complete:", repr(bytes(received)))

