from sds200 import SDS200

with SDS200.network("192.168.0.251") as radio:
    health = radio.health_check()
    print(health)
