# 006 — Single-process Micro-Manager Core sharing

- Status: accepted (both the guard and the in-process GUI are implemented;
  the GUI's real hardware behavior is pending verification on the rig)
- Stage: 1 (guard) / 5 (in-process GUI)

## Context

PycroFlow's `ImagingSystem` and a separately-run monet GUI both connect to
Micro-Manager. Two processes attaching to the same MM Core is a documented
silent-failure mode: the second connection breaks the first, with no error.

## Decision

Two-part:

1. **Now (Stage 1):** a filesystem mutex `MmCoreLock`
   (`%LOCALAPPDATA%\PycroFlow\mm.lock` on Windows,
   `~/.cache/PycroFlow/mm.lock` on POSIX) acquired in
   `ImagingSystem.__init__`. If monet's GUI already holds it, raise
   `MmLockHeld` with a clear message instead of corrupting the connection.
2. **In-process GUI (Stage 5):** the `pycroflow-gui` Qt frontend embeds
   monet's `MonetMainWindow` as a tab. `services.mm_core.get_core()` is the
   one owner; `app.build_main_window()` calls `share_with_monet()`, which
   patches monet's `beampath.pycrocore` to the same instance so both
   packages share one Core inside one process — removing the conflict
   structurally. The lockfile then only matters for users still running the
   CLI alongside a standalone monet process.

## Consequences

- Today: no more silent breakage; conflicts fail fast and loud.
- `services.mm_core` is the single Core owner, superseding
  `util.PyMgrSingleton` (kept as a fallback).
- The eventual in-process GUI makes the guard mostly redundant but it stays
  useful for the two-process case.
