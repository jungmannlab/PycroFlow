# 001 — Hardware Abstraction Layer (HAL)

- Status: accepted
- Stage: 3

## Context

High-level orchestration and fluid code called directly into the Hamilton
driver (`pyHamilton`) and the Arduino spill sensor. Swapping in a different
pump, valve, or sensor vendor would have meant editing `LegacyArchitecture`
and the orchestration layer. There was no interface describing what a "pump"
or "valve" must do.

## Decision

Introduce `PycroFlow/hal/` with ABCs `Pump`, `Valve`, `SpillSensor` capturing
the minimum surface the high-level code uses. The existing concrete classes
(`hamilton_components.Pump`/`Valve`, `peristaltic_drifton.DriftonPump`,
`spill_sensor_arduino.ArduinoSensorInterface`) are registered as virtual
subclasses with `ABC.register`, so they satisfy `isinstance` checks without
being modified.

## Consequences

- A new vendor implementation only needs to satisfy the HAL ABC.
- No behavior change today — registration is non-invasive; the concrete
  classes are untouched.
- The ABCs document the contract and enable `isinstance` checks in tests.
- Follow-up: route `fluid/legacy.py` through the HAL instead of calling
  `pyHamilton` directly (deferred — rig-risk).
