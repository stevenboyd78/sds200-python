from sds200 import SDSScanner
from sds200.monitor import TerminalMonitor

with SDSScanner.auto(trace_path="scanner.trace") as radio:
    terminal = TerminalMonitor()
    radio.on_state(lambda state: terminal.render(state, radio.endpoint))

    with radio.scanner_info_push(interval_ms=500):
        radio.wait()
