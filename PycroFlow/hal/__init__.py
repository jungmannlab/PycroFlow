"""Hardware Abstraction Layer for PycroFlow.

These ABCs describe the minimum surface area high-level fluid /
illumination / sensor code uses, so that swapping in a different vendor
(Tecan instead of Hamilton, a different spill sensor, etc.) only requires
a new ``hal`` implementation instead of editing the orchestration or
protocol layer.

The existing concrete classes in :mod:`PycroFlow.hamilton_components` and
:mod:`PycroFlow.spill_sensor_arduino` already match these duck-typed
interfaces; the ABCs document the contract and allow ``isinstance``
checks in tests.

Stage 3 introduces the abstraction; later stages may move high-level
fluid code (``hamilton_architecture``) to call HAL methods instead of
talking to ``pyHamilton`` directly.
"""
from PycroFlow.hal.pumps import Pump
from PycroFlow.hal.valves import Valve
from PycroFlow.hal.sensors import SpillSensor


def _register_existing_implementations():
    """Mark the existing concrete classes as HAL implementations.

    Uses ABC.register so the classes stay structurally unchanged but
    ``isinstance(x, Pump)`` etc. return True. Imports are guarded because
    hamilton_components / spill_sensor_arduino transitively import vendor
    SDKs that may not be present in all environments.
    """
    try:
        from PycroFlow.hamilton_components import Pump as _HamiltonPump
        Pump.register(_HamiltonPump)
    except Exception:
        pass
    try:
        from PycroFlow.hamilton_components import Valve as _HamiltonValve
        Valve.register(_HamiltonValve)
    except Exception:
        pass
    try:
        from PycroFlow.peristaltic_drifton import DriftonPump as _DriftonPump
        Pump.register(_DriftonPump)
    except Exception:
        pass
    try:
        from PycroFlow.spill_sensor_arduino import ArduinoSensorInterface
        SpillSensor.register(ArduinoSensorInterface)
    except Exception:
        pass


_register_existing_implementations()


__all__ = ["Pump", "Valve", "SpillSensor"]
