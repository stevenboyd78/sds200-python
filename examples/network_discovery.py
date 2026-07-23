from sds200 import discover_network_scanners

for scanner in discover_network_scanners():
    print(scanner.endpoint, scanner.model, f"{scanner.latency_ms:.1f} ms")
