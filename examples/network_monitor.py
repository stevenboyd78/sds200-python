from sds200 import SDS200

with SDS200.network("192.168.1.50", trace_path="network.trace") as radio:
    radio.on_state_change(
        lambda change: print(change.fields, change.current.channel)
    )
    with radio.scanner_info_push(500):
        radio.wait()
