# WP-1 performance harness — output schema & go/no-go thresholds

This is the stable contract between the **run** leg (on the acquisition PC) and
the **analyze** leg (here). It is versioned by `SCHEMA_VERSION` in
`PycroFlow/perf/schema.py` (currently **1.1**); the analysis refuses run dirs
whose `schema_version` does not match. Bump the version and this doc together
on any incompatible change.

Note on sizes: the run directory holds only these three small files. The raw
NDTiff acquisition (tens of GB for a full-length full-frame run) is written to a
separate `data_dir` on a data drive and deleted after each configuration is
measured (WP-1 needs only the metrics) — it is **never** part of the run dir and
must **not** be committed. Only the run dir is git-transferred.

Each harness invocation writes **one timestamped run directory**
(`<label>_<mode>_<UTC-timestamp>/`) containing exactly three files.

---

## `run_meta.json`

Environment + provenance so a run is reproducible without the harness author
present. Required top-level keys (pinned by `RUN_META_REQUIRED_KEYS`):

| key | meaning |
|---|---|
| `schema_version` | schema version string (must equal `SCHEMA_VERSION`) |
| `mode` | `emulator` or `instrument` |
| `host` | acquisition PC hostname |
| `os` | `platform.platform()` |
| `python_version` | interpreter version |
| `pycroflow_version` | installed PycroFlow version |
| `pycromanager_version` | pycromanager version, or `not-installed` |
| `frame_rate_hz`, `n_frames`, `buffer_mb` | acquisition parameters |
| `batch_sizes` | the swept batch-size list |
| `git_commit` | HEAD commit of the branch that produced the run |
| `utc_start`, `utc_end` | ISO-8601 UTC timestamps |
| `config` | the fully-resolved config (`PerfConfig.to_dict()`) |
| `backend` | backend provenance (emulator params, or camera + MM version + raw-data dir) |

Also present (informational): `buffer_frames`, `frame_bytes`, `exposure_ms`,
`roi`, `data_dir`, `reader_mode`, `monitor_interval_s`, `thresholds`,
`status` (`running` / `complete` / `error`), `completed_configs`, and
`errors`. `status` / `completed_configs` / `errors` support the incremental
writer: results are flushed after **every** configuration, so a run dir stays
valid and self-describing even if a later acquisition fails (`status: error`).
The `backend` block for an instrument run also carries `reader_mode` and
`reader_frames_read` (frames the separate-process reader actually read).

---

## `metrics.csv`

One row per configuration. The baseline row has `reader=False`,
`batch_size=0`; each swept row has `reader=True` and its batch size.

| column | meaning |
|---|---|
| `mode` | `emulator` / `instrument` |
| `reader` | whether the concurrent incremental reader was running |
| `batch_size` | frames per contiguous read (0 for the baseline) |
| `n_frames` | frames in this run |
| `frame_rate_hz` | target frame rate |
| `buffer_mb` | circular-buffer footprint (MB) — matches MM's sequence buffer |
| `buffer_frames` | circular-buffer capacity in frames (from `buffer_mb` / frame size) |
| `frame_bytes` | bytes per frame (ROI / full-frame size x bytes per pixel) |
| `frames_produced` | frames placed in the buffer |
| `frames_written` | frames drained to disk (the write path) |
| `dropped_count` | frames lost to buffer overflow |
| `dropped_fraction` | `dropped_count / n_frames` |
| `occupancy_peak` | max circular-buffer occupancy over the run |
| `occupancy_mean` | mean occupancy over the sampled series |
| `throughput_fps` | `frames_written / duration_s` |
| `duration_s` | wall-clock duration of the run |

---

## `buffer_timeseries.csv`

Circular-buffer occupancy over the run, one row per monitor sample, tagged with
the configuration it belongs to.

| column | meaning |
|---|---|
| `mode`, `reader`, `batch_size` | which configuration this sample belongs to |
| `sample_index` | monotonically increasing sample counter |
| `t_rel_s` | seconds since that run started |
| `frame_index` | frames written at sample time (the x-axis) |
| `occupancy` | circular-buffer occupancy at sample time |

---

## Go/no-go rule and thresholds

The question is: **does the concurrent incremental reader compromise
acquisition?** For each mode, every with-reader configuration is compared to
that mode's no-reader **baseline**. A configuration **passes** when all three
hold:

| threshold | default | rationale |
|---|---|---|
| `max_dropped_fraction` | `0.001` | DNA-PAINT kinetics need every frame; dropped frames are disqualifying. A tiny tolerance absorbs a single boundary artefact. |
| `min_throughput_retention` | `0.95` | with-reader write throughput must stay within 5 % of baseline. |
| `max_occupancy_fraction` | `0.5` | peak occupancy must stay below half of buffer capacity — comfortable head-room before overflow. |

- **Mode verdict** = `GO` iff *every* with-reader config passes; else `NO-GO`
  (`PENDING` if there is no baseline or no with-reader run).
- **Overall verdict** = `NO-GO` if any mode is `NO-GO`; `GO` only once the
  **instrument** mode is `GO`; otherwise `PENDING` (e.g. an emulator-only dry
  run — the code path is proven but the real decision awaits instrument data).

Thresholds are overridable on the analysis command line
(`--max-dropped-fraction`, `--min-throughput-retention`,
`--max-occupancy-fraction`) and are echoed verbatim into every report.

**Interpretation.** `GO` → adopt live incremental NDTiff reading (option b) as
the primary live path. `NO-GO` → fall back to post-FOV batch processing (or the
watchdog, option c) and investigate the contention.
