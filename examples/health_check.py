from sds200 import SDSScanner

with SDSScanner.network("192.168.0.251") as radio:
    health = radio.health_check()
    print(health)
