# 007 — Qt GUI frontend (PyQt5, in-process monet embed)

- Status: accepted (code + headless tests; on-rig behavior pending);
  **amended — binding migrated to PyQt6 (see Amendment below)**
- Stage: 5

## Context

PycroFlow had only a `cmd.Cmd` CLI. A graphical frontend was wanted, and a
hard requirement was avoiding the two-process Micro-Manager conflict with
monet (ADR 006). The service layer (ADR 005) had already made a second
frontend feasible without touching orchestration internals.

## Decision

Add a `PycroFlow/gui/` PyQt5 package exposed as the `pycroflow-gui` console
script:

- **Binding: PyQt5**, matching monet's binding so its `MonetMainWindow`
  embeds without a Qt-version shim.
- **In-process monet embed**: the Monet tab adds monet's `MonetMainWindow`
  (a `QWidget`) directly to its layout; `app` calls
  `mm_core.share_with_monet()` before constructing the window so both share
  one Core (ADR 006).
- **Thread safety via `QtBridge`**: `ExperimentService` observer callbacks
  may fire on worker threads; `QtBridge` (a `QObject`) re-emits them as Qt
  signals, which Qt auto-queues onto the GUI thread. Widgets connect to the
  bridge, never to the service directly.
- **Import-safe without PyQt5**: `import PycroFlow.gui` does not import Qt
  (the Qt-dependent modules import it themselves). `app.main` exits with an
  install hint if PyQt5 is missing. PyQt5 lives in the optional `[gui]`
  extra, so CI and the test suite don't need it.
- **Graceful monet degradation**: if monet is absent, import-fails,
  fails to construct, or is mocked (non-`QWidget`), the Monet tab shows a
  placeholder instead of crashing the GUI.

## Consequences

- Both frontends share one `services/` API; no orchestration code is
  GUI-aware.
- The MM conflict is resolved structurally for GUI users.
- Headless offscreen tests cover bridge/window/tab wiring; live acquisition
  and real monet laser control still require on-rig verification.
- Out of scope for the initial GUI (deferred): live camera preview, plot
  widgets, a graphical protocol editor.

## Amendment — migrate binding to PyQt6

The `gui/` package and its tests were migrated from PyQt5 to **PyQt6**
(`[gui]` extra now requires `PyQt6`). The changes were mechanical: `PyQt5.*`
→ `PyQt6.*` imports, `QAction` moved from `QtWidgets` to `QtGui`, and
`QApplication.exec_()` → `exec()`. No scoped-enum or other API call sites
needed changing. The `QtBridge` thread-safety design and import-safety
guarantee are unchanged (now phrased against PyQt6).

**Trade-off accepted:** this breaks the original "match monet's binding"
rationale for the in-process embed. PyQt5 and PyQt6 are separate Qt libraries
whose widgets cannot coexist in one process, so while monet remains on PyQt5
its `MonetMainWindow` is not a PyQt6 `QWidget` and the existing graceful-
degradation guard shows the placeholder (no crash). The embed re-enables once
monet itself moves to PyQt6; until then the Monet tab is non-functional and
laser control must use a standalone monet process (reintroducing the ADR 006
MM-Core conflict only if that standalone process is run concurrently).
