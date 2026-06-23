"""Hardware emulators for PycroFlow unit tests.

Three layers of fidelity are provided so a test can pick the cheapest one that
still covers the code under test:

1. **Serial-level** (:mod:`hamilton_serial`, :mod:`arduino_serial`) — present a
   ``serial.Serial`` surface so the *real* drivers (``SerialBus``,
   ``Pump``/``Valve``, ``ArduinoSensorInterface``) run end to end against an
   emulated wire protocol. Highest fidelity; use to cover the encode/decode path.

2. **HAL-level** (:mod:`hal_devices`) — ``EmulatedPump`` / ``EmulatedValve`` /
   ``EmulatedSpillSensor`` implement the :mod:`PycroFlow.hal` ABCs with plain
   Python state and a command log. Use when the test needs a working device but
   not vendor bytes.

3. **Subsystem-level** (:mod:`subsystems`) — ``EmulatedFluidSystem`` /
   ``EmulatedImagingSystem`` / ``EmulatedIlluminationSystem`` implement
   :class:`AbstractSystem` for driving the orchestrator with deterministic
   pause/resume/abort behavior.

Unlike the ``sys.modules`` ``MagicMock`` shims in :mod:`tests._mock_hardware`
(which only let imports succeed), these emulators model device *behavior* and so
support real behavioral assertions.
"""
from PycroFlow.tests.emulators.hal_devices import (
    EmulatedPump,
    EmulatedValve,
    EmulatedSpillSensor,
)
from PycroFlow.tests.emulators.hamilton_serial import (
    FakeHamiltonSerial,
    EmulatedHamiltonDevice,
    patch_serial,
    make_fake_bus,
)
from PycroFlow.tests.emulators.arduino_serial import (
    FakeArduinoSerial,
    connect_interface,
)
from PycroFlow.tests.emulators.ibidi_serial import (
    FakeIbidiSerial,
    patch_ibidi_serial,
    connect_multiplexer,
)
from PycroFlow.tests.emulators.subsystems import (
    EmulatedFluidSystem,
    EmulatedImagingSystem,
    EmulatedIlluminationSystem,
)

__all__ = [
    'EmulatedPump',
    'EmulatedValve',
    'EmulatedSpillSensor',
    'FakeHamiltonSerial',
    'EmulatedHamiltonDevice',
    'patch_serial',
    'make_fake_bus',
    'FakeArduinoSerial',
    'connect_interface',
    'FakeIbidiSerial',
    'patch_ibidi_serial',
    'connect_multiplexer',
    'EmulatedFluidSystem',
    'EmulatedImagingSystem',
    'EmulatedIlluminationSystem',
]
