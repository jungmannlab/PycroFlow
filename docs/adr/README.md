# Architecture Decision Records

Each ADR captures one significant decision made during the PycroFlow
restructuring (Stages 0–4) — the context, the choice, and the consequences —
so future contributors understand *why* the code is shaped the way it is.

Format: lightweight [MADR](https://adr.github.io/madr/). Status is one of
`accepted`, `superseded`, or `proposed`.

| #   | Title                                          | Status   |
| --- | ---------------------------------------------- | -------- |
| 001 | Hardware Abstraction Layer (HAL)               | accepted |
| 002 | Event-backed SignalRegistry                    | accepted |
| 003 | Typed protocol entries via pydantic            | accepted |
| 004 | monet as an external sibling dependency        | accepted |
| 005 | Frontend-agnostic service layer                | accepted |
| 006 | Single-process Micro-Manager Core sharing      | accepted |
| 007 | Qt GUI frontend (PyQt6, in-process monet embed) | accepted (amended: PyQt5→PyQt6) |
