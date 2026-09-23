# WP-FLUIDICS-CAM (Phase 1) — Fluidics monitoring webcams runbook

Record one short movie per Exchange round of the fluid-exchange leg (the
least-observable, most failure-prone part of a run: dry reservoir, mis-primed
pump, bubble/air-gap, leak, kinked tubing). Phase 1 is **capture +
record-per-round + registry index**. Live streaming and a GUI review/scrub panel
are Phase 1b; automatic action-verification is Phase 2.

The subsystem is **read-only** (no actuation) and **best-effort**: a slow,
saturated, or unplugged camera degrades to a logged gap and can never stall or
perturb acquisition or the fluidics orchestration. If a setup declares no
cameras, the subsystem is inert and the rig runs exactly as before.

---

## 1. Install (acquisition PC)

The camera library is an optional extra so the base install stays wheel-only:

```bash
pip install -e ".[hardware,monitoring]"      # real webcams (OpenCV)
pip install -e ".[hardware,monitoring,registry]"  # + registry indexing
```

- `[monitoring]` → `opencv-python-headless` (used **only** by the real
  `--instrument` frame source).
- `[registry]` → the `picasso-registry` client (used **only** to index clip
  URIs; omit it to record clips without a registry).

No extra is needed to run the emulator path (synthetic frames + a pure-Python
AVI writer are wheel-only).

---

## 2. Declare the rig's cameras

Add a `monitoring:` block to the microscope's setup YAML in
`PycroFlow/configs/setups/<name>.yaml`. Example (see the shipped
`EmulatorCam.yaml` for a full no-hardware setup):

```yaml
monitoring:
  output_dir: /data/fluidics_cam      # pool/archive dir clips land in (never git)
  fps: 5                              # tile frame rate written to each clip
  retention_days: 14                 # prune clips older than this (0 disables)
  # source: instrument               # optional; inferred from the setup's
                                     #   `emulated:` flag when omitted
  cameras:
    - {role: reservoir, device: 0, width: 640, height: 480}
    - {role: pump,      device: 1, width: 640, height: 480}
    - {role: sample,    device: 2, width: 640, height: 480}
```

- `role` — advisory label (`reservoir` / `pump` / `sample`); names the tile
  panel.
- `device` — the OpenCV `VideoCapture` index or device path/URL for that camera.
- One tiled clip is written per round combining all cameras into a grid; a
  downed camera renders as a black panel (a visible gap), never a stall.

Find camera indices on the acquisition PC with any UVC tool, or by trial with
the smoke test below.

---

## 3. Verify the cameras (smoke test, no orchestration)

Grab a short clip from every camera and exit — confirms the cameras open and
frames flow before wiring monitoring into a real run:

```bash
# Write a cameras config the service can read (or hand-author the JSON):
python -c "from PycroFlow.configs import load_setup; \
from PycroFlow.monitoring.config import load_monitoring_config; import json; \
print(json.dumps(load_monitoring_config(load_setup('<YourSetup>')).to_dict()))" > cams.json

pycroflow-capture --config cams.json --instrument --smoke --run-id smoke
# -> prints the path of the written .avi; open it and confirm all panels are live
```

---

## 4. Record per round during a real run

Recording binds to the fluidics round lifecycle automatically — you do **not**
launch the capture service by hand for a run. In the GUI (`pycroflow-gui`):

1. Select the camera-equipped **setup** in the toolbar. The monitoring
   controller attaches automatically (nothing visible changes; the plain
   `Emulator`/non-camera setups stay inert).
2. Load the experiment design and run the experiment as usual.
3. As each Exchange round flushes, one tiled `.avi` is written to `output_dir`.

Under the hood the controller spawns `pycroflow-capture` in a **separate OS
process** and drives its record windows from the orchestration's round signals;
the capture process is torn down when the run finishes/aborts or the setup
changes.

### Clips

One file per round in `output_dir`, named:

```
run_<run_id>_round<NNN>_<UTC-start>.avi
# e.g. run_20260923T141500Z_ab12cd_round003_20260923T141530Z.avi
```

Only the fluid-exchange leg of each round is recorded (not the long imaging
leg), bounding disk use; `retention_days` prunes old clips before each run.

---

## 5. Index clips on the registry (optional)

To index each clip's pool URI onto the matching `fluidics_round` record, set the
registry endpoint in the environment before starting the app (client side of the
stack-wide bearer-token pattern — never commit the token):

```bash
export PAINT_REGISTRY_URL="http://registry.lab:8000"
export PAINT_REGISTRY_TOKEN="<write-scope token>"   # omit on a loopback dev registry
```

The URI is written as a top-level `monitoring_video_uri` field (the registry
folds it into the row's `extra` JSON). Confirm it round-tripped:

```bash
python -c "from picasso_registry.client import RegistryClient; import os; \
r=RegistryClient(os.environ['PAINT_REGISTRY_URL'], token=os.environ.get('PAINT_REGISTRY_TOKEN')); \
rows=r.list('fluidics_round', limit=20); \
print([ (x.get('round_index'), (x.get('extra') or {}).get('monitoring_video_uri')) for x in rows ])"
```

Indexing is best-effort: with `PAINT_REGISTRY_URL` unset, or the registry down
or slow, clips are still recorded and the run is never blocked (writes are
buffered and replayed).

---

## 6. Perturbation check (the isolation guarantee)

Confirm on the instrument that a failing camera does not perturb the run:

1. Start a real (or dry) exchange with monitoring on.
2. **Physically unplug one USB camera mid-run.**
3. Confirm: the exchange and acquisition continue unaffected (no stall, no
   error); the round clips keep being written with that camera's panel now
   black; the log shows a `monitoring: camera <role> ... recording a gap`
   warning. Re-running with all cameras present records full tiles again.

This mirrors the hermetic isolation proof in
`PycroFlow/tests/test_monitoring.py::TestIsolationProof` (a slow / raising /
unplugged fake camera leaves the emulated exchange's timing within noise of the
no-camera baseline and still yields gap clips).

---

## 7. Confirmation evidence (round-trip leg)

Commit under `results/` on the branch: a couple of the per-round clips (or their
paths + sizes), the registry read-back showing `monitoring_video_uri` on the
right rounds, and a note on the unplug-a-camera observation. Claude verifies
this evidence back in the dev container and ticks the gate.

---

## Troubleshooting

- **No clips written** — the setup has no `monitoring:` block or no `cameras`;
  or every camera failed to open (check the log for `did not open`).
- **All panels black** — cameras did not open (wrong `device` index) or are all
  unplugged; the smoke test (§3) isolates this.
- **`instrument capture needs OpenCV`** — install the `[monitoring]` extra.
- **Clips not indexed** — `PAINT_REGISTRY_URL` unset, registry unreachable, or
  `[registry]` extra not installed; recording is unaffected.
- **Disk filling** — lower `fps`/`width`/`height` or `retention_days` in the
  `monitoring:` block (uncompressed AVI trades size for zero codec deps).
