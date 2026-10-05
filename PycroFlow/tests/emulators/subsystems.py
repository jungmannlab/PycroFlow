"""Emulated orchestration subsystems (:class:`AbstractSystem` implementations).

These stand in for the real ``FluidSystem`` / ``ImagingSystem`` /
``IlluminationSystem`` when driving the :class:`ProtocolOrchestrator` in a
test. Unlike a bare ``MagicMock`` they:

* implement the full :class:`~PycroFlow.orchestration.AbstractSystem` contract,
* honour the pause / resume / abort flags so pause-resume and abort paths can
  be exercised deterministically,
* record every executed entry in ``executed`` for assertions.

``EmulatedFluidSystem`` optionally drives an :class:`EmulatedPump` /
:class:`EmulatedValve` so an orchestration test can also assert hardware-level
effects (e.g. that an ``inject`` entry moved fluid).
"""

from __future__ import annotations

from PycroFlow.orchestration import AbstractSystem
from PycroFlow.tests.emulators.hal_devices import EmulatedPump, EmulatedValve


class _BaseEmulatedSystem(AbstractSystem):
    def __init__(self):
        self.protocol = None
        self.executed = []  # list of (index, entry) actually run
        self.paused = False
        self.aborted = False

    def _assign_protocol(self, protocol):
        self.protocol = protocol

    def _assign_multiprocess_events(self, *flags):
        # Accept whatever the handler passes (pause/abort/abort_protocol).
        self._flags = flags

    def execute_protocol_entry(self, i):
        entry = self.protocol["protocol_entries"][i]
        self.executed.append((i, entry))
        self._on_entry(entry)

    def _on_entry(self, entry):
        """Subclass hook for entry-specific side effects."""

    def pause_execution(self):
        self.paused = True

    def resume_execution(self):
        self.paused = False
        return True

    def abort_execution(self):
        self.aborted = True


class EmulatedImagingSystem(_BaseEmulatedSystem):
    """Records 'acquire' entries instead of talking to pycromanager.

    WP-LIVE-INT: when the live-run coordinator attaches a ``frame_tap``, each
    'acquire' entry also synthesizes a short burst of MockFrameSource frames
    through it (via ``FrameTap.feed_frames``, the same bracketing protocol
    ``ImagingSystem.record_movie`` implements), so the Emulator setup
    exercises the full orchestrated live-analysis path — including the
    end-this-FOV early-abort — with no instrument. ``self.config`` mirrors
    the real ImagingSystem's config dict, so the coordinator reads the SAME
    ``config['camera_info']`` / ``config['live_analysis']`` spelling for both
    system flavors (thread-pool compute + a low gradient threshold keep the
    emulated pipeline fast and the synthetic spots localizable).
    """

    # Cap synthetic frames per acquire so emulated runs stay near-instant
    # (the emulator's acquisitions are instantaneous anyway).
    live_max_frames = 60

    def __init__(self):
        super().__init__()
        self.acquisitions = []
        self.frame_tap = None
        self.last_dataset_path = None
        self.config = {
            # Full picasso photon-conversion info (the fit refuses partial
            # info).
            "camera_info": {
                "Baseline": 100.0,
                "Sensitivity": 0.46,
                "Gain": 1,
                "Qe": 0.82,
                "Pixelsize": 130.0,
            },
            "live_analysis": {
                "use_processes": False,
                "n_workers": 2,
                "batch_size": 20,
                "localize_params": {"Box Size": 7, "Min. Net Gradient": 200},
                "first_frame_timeout_s": 10.0,
            },
        }

    def _on_entry(self, entry):
        if entry.get("$type") == "acquire":
            self.acquisitions.append(entry)
            self._feed_tap(entry)

    def _feed_tap(self, entry):
        tap = self.frame_tap
        if tap is None:
            return
        from PycroFlow.live_analysis.frame_source import MockFrameSource

        n = int(min(entry.get("frames") or 30, self.live_max_frames))
        name = "emulated_{}".format(entry.get("message", "acquire"))
        source = MockFrameSource(n_frames=n, seed=0)
        frames = (
            frame for batch in source.batches(8) for frame in batch.frames
        )
        tap.feed_frames(
            name, {"frames": n, "t_exp": entry.get("t_exp")}, frames
        )

    def close(self):
        """No-op cleanup (matches ImagingSystem.close for SystemService)."""


class EmulatedIlluminationSystem(_BaseEmulatedSystem):
    """Records laser power / shutter changes instead of talking to monet."""

    def __init__(self):
        super().__init__()
        self.power = None
        self.laser = None
        self.enabled = {}
        self.shutter_open = False

    def _on_entry(self, entry):
        t = entry.get("$type")
        if t in ("set power", "power"):
            self.power = entry.get("power", entry.get("value"))
            self.laser = entry.get("laser", self.laser)
        elif t == "set shutter":
            self.shutter_open = bool(entry.get("state"))

    # Manual-control surface (matches IlluminationSystem) so the GUI/CLI
    # SystemService laser controls work against the emulator.
    def set_laser(self, laser):
        self.laser = laser

    def set_laser_enabled(self, laser, enabled=True):
        self.enabled[laser] = enabled

    def set_sample_power(self, power, warmup_delay=0):
        self.power = power


class EmulatedFluidSystem(_BaseEmulatedSystem):
    """Drives an :class:`EmulatedPump`/:class:`EmulatedValve` on fluid entries.

    Handles the common ``inject`` / ``flush`` entry types so the bound pump's
    volume and valve position reflect the protocol; unknown entry types are
    simply recorded.
    """

    def __init__(self, pump=None, valve=None):
        super().__init__()
        self.pump = pump if pump is not None else EmulatedPump()
        self.valve = valve if valve is not None else EmulatedValve()
        self.injections = []

    def _on_entry(self, entry):
        t = entry.get("$type")
        if t == "inject":
            vol = entry.get("volume", 0)
            velocity = entry.get("velocity")
            res = entry.get("reservoir_id")
            self.injections.append((res, vol))
            if res is not None:
                self.valve.set_valve(res)
            self.pump.set_valve("in")
            self.pump.pickup(vol, velocity=velocity, waitForPump=True)
            self.pump.set_valve("out")
            self.pump.dispense(vol, velocity=velocity, waitForPump=True)
        elif t == "flush":
            factor = entry.get("flushfactor", 1)
            self.pump.set_valve("out")
            self.pump.dispense(
                self.pump.syringe_volume * factor, waitForPump=True
            )
