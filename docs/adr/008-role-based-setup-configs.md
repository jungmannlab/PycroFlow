# 008 — Role-based setup configs

- Status: accepted

## Context

Per-microscope setup files (`PycroFlow/configs/setups/<name>.yaml`) grouped
hardware by *vendor*: a single `hamilton:` block held the serial interface,
the syringe pumps, the rotary valves, the reservoir manifold and `flush_pos`.
That was accurate when Hamilton made every fluidics device, but is no longer:
the ibidi MultiFlOW multiplexer had to be wired in as `hamilton.ibidi:` — an
ibidi device nested under a competitor's name.

A second conflation sat in the same file: the top-level `setup:` key doubled
as both the setup's own name (shown in the GUI selector) and the `monet.CONFIGS`
key for the lasers. Those coincide only when one setup file serves exactly one
microscope. Running the `Ibidi` fluidics setup on the `Mercury` microscope made
PycroFlow look up a nonexistent `monet.CONFIGS['Ibidi']`, which silently
emptied the experiment design's laser dropdown and mis-targeted the Monet tab.

## Decision

Group setup files by **role**, with each role naming the driver serving it:

```yaml
setup: Ibidi          # this setup's own name
fluid:
  system_type: legacy
  pumps:       {driver: hamilton-psd, interface: {...}, pump_a: {...}, ...}
  multiplexer: {driver: ibidi-multiflow, port: '7', channels: 24, ...}
  #         or {driver: hamilton-mvp, valves: [...]}
  flush_pos:   {...}
  reservoirs:  [...]          # was hamilton.reservoir_a_manifold
  tubing:      [...]          # was top-level tubing:
imaging:      {pfs_pars: {...}}
illumination: {backend: monet, config: Mercury}   # the MICROSCOPE's monet key
```

`LegacyArchitecture` keeps consuming its existing flat, vendor-shaped config
dict; `configs._flatten_fluid_config()` is the single translation point, called
from `assemble_hamilton_config()`. `configs._normalize_setup()` translates the
old `hamilton:` layout on load (with a deprecation warning), so setup files
outside the repo keep working.

`SystemService.get_monet_setup()` now returns `illumination.config` (falling
back to `setup:`), and `setup_name()` returns the setup's own name.
`connect_illumination()` validates that name against `monet.CONFIGS` before
building the system — the lasers still open lazily, so connecting does not
claim the laser COM port, but a misconfigured setup fails at Connect instead of
reporting "connected" and dying mid-run on the first laser command.

## Consequences

- Adding a non-Hamilton device (a different pump, another multiplexer) means a
  new `driver:` value under the role it fills, not a new vendor block.
- One fluidics setup file can be used on several microscopes by changing only
  `illumination.config`.
- Illumination connection status is truthful; a wrong monet config name is
  reported with the list of available ones.
- Two layouts exist transiently; the translation shim is the deprecation path.
