from sds200 import SDSScanner

with SDSScanner.auto(model="SDS150") as radio:
    status = radio.get_charge_status()
    print("Model:", radio.model)
    print("Status:", status.status)
    print("Capacity:", f"{status.capacity_percent}%")
    print("Voltage:", f"{status.voltage_mv} mV")
    print("Current:", f"{status.current_ma} mA")
    print("Temperature:", f"{status.temperature_c:.2f} C")
