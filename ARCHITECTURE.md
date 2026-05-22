# Architecture

PycroFlow coordinates microscopy acquisition, fluid handling, and
illumination across per-subsystem handler threads synchronized by a
signal/wait protocol.

- **Detailed map** — subsystems, signal protocol, threading model, wire
  format, configuration: [docs/architecture.md](docs/architecture.md)
- **Decision records** — why the code is shaped this way:
  [docs/adr/](docs/adr/README.md)
- **Getting started** — install, run, test: [docs/quickstart.md](docs/quickstart.md)

## Package layout

```
PycroFlow/
├── orchestration/        # ProtocolOrchestrator, handler threads,
│   ├── core.py           #   SignalRegistry, ThreadExchange
│   ├── signal_registry.py
│   └── threadexchange.py
├── protocols/            # ProtocolBuilder (split from the old protocols.py)
│   ├── builder.py
│   └── exchange.py / merpaint.py / flushtest.py / sph_resi.py
├── protocol_entries.py   # typed entry models + parse helpers (Stage 4)
├── schemas/              # pydantic wire-format schema (Stage 2)
├── fluid/                # Hamilton fluid stack (split from
│   └── legacy.py         #   hamilton_architecture.py)
├── hal/                  # Hardware Abstraction Layer ABCs
├── services/             # ExperimentService, SystemService, mm_core
├── imaging.py            # ImagingSystem (pycromanager)
├── illumination.py       # IlluminationSystem (monet)
├── mm_lock.py            # MM-Core single-process guard
├── frontend_cli.py       # `pycroflow` interactive CLI
├── gui/                  # `pycroflow-gui` PyQt5 frontend
│   ├── app.py            #   entry point + monet Core sharing
│   ├── main_window.py    #   PycroFlowMainWindow (tabbed)
│   ├── qt_bridge.py      #   service observer -> Qt signals
│   └── tabs/             #   experiment / fluid / imaging / monet tabs
├── configs/              # YAML instrument configs
├── examples/             # demo protocols
├── pyHamilton/           # in-house Hamilton serial driver
└── tests/                # unittest suite + fixtures + snapshots
```

Back-compat shims preserve the old import paths:
`PycroFlow.hamilton_architecture` re-exports from `fluid.legacy`;
`PycroFlow.protocols` re-exports `ProtocolBuilder` from `protocols.builder`;
`PycroFlow.orchestration` re-exports from `orchestration.core`.
