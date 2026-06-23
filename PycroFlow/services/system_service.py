"""Hardware-control commands the frontends expose.

Wraps the fluid / imaging / illumination systems so the CLI (and future
GUI) can drive them without reaching into private attributes. Previous
``frontend_cli`` poked ``self.fluid_system._pump`` directly — that's the
sort of leakage this service eliminates.
"""
from __future__ import annotations

from loguru import logger


def _load_yaml_or_dict(config):
    """Return ``config`` as a dict: load YAML if a path, else pass through.

    Parameters
    ----------
    config : str or dict
        A path to a YAML config file, or an already-parsed config dict.

    Returns
    -------
    dict
        The configuration dictionary.
    """
    if isinstance(config, str):
        import yaml
        with open(config) as f:
            return yaml.full_load(f)
    return config


class SystemService:
    """Adapter around the three subsystem objects.

    The service does NOT instantiate the underlying systems — pass them in.
    That keeps construction (which needs hardware) separate from this
    adapter, so the GUI can build a SystemService in a unit test by
    handing in mocks.
    """

    def __init__(
        self,
        fluid_system=None,
        imaging_system=None,
        illumination_system=None,
    ):
        self.fluid_system = fluid_system
        self.imaging_system = imaging_system
        self.illumination_system = illumination_system
        self._setup = None
        self._setup_name = None

    # --- Setup (per-microscope hardware) -------------------------------

    def load_setup(self, name):
        """Load a per-microscope setup (hardware) config by name.

        Parameters
        ----------
        name : str
            Setup name (e.g. ``'Mercury'`` / ``'Emulator'``) or a path.

        Returns
        -------
        dict
            The parsed setup config.
        """
        from PycroFlow.configs import load_setup

        self._setup = load_setup(name)
        self._setup_name = self._setup.get('setup', name)
        if self._setup.get('emulated'):
            # Warm the emulator import on THIS (main) thread. Importing the
            # tests package runs install_hardware_mocks(), whose subprocess
            # imports deadlock if first triggered from a worker thread; doing
            # it here means later background connects find it cached.
            import PycroFlow.tests.emulators  # noqa: F401
        return self._setup

    @property
    def setup(self):
        return self._setup

    def get_monet_setup(self):
        """Return the setup name (a ``monet.CONFIGS`` key) for Monet."""
        return self._setup_name

    def is_emulated(self) -> bool:
        """Whether the loaded setup runs against emulated hardware."""
        return bool(self._setup and self._setup.get('emulated'))

    def reservoir_ids(self) -> list:
        """Reservoir ids wired in the current setup's manifold (sorted).

        Empty when no setup is loaded. Used by the GUI to restrict the
        experiment design's reservoir-id inputs to the setup's hardware.
        """
        if not self._setup:
            return []
        manifold = (self._setup.get('hamilton', {})
                    or {}).get('reservoir_a_manifold', [])
        ids = [e['id'] for e in manifold
               if isinstance(e, dict) and 'id' in e]
        return sorted(ids)

    def laser_options(self) -> list:
        """Laser lines (wavelengths) defined in the setup's monet config.

        Empty when no setup is loaded or monet/the config is unavailable
        (e.g. monet not installed or mocked in tests). Used by the GUI to
        offer the experiment design's ``laser`` field as a dropdown.
        """
        name = self.get_monet_setup()
        if not name:
            return []
        try:
            import monet
        except Exception:
            return []
        configs = getattr(monet, 'CONFIGS', None)
        if not isinstance(configs, dict):
            return []
        lasers = (configs.get(name) or {}).get('lasers') \
            if isinstance(configs.get(name), dict) else None
        if not isinstance(lasers, dict):
            return []
        return sorted(lasers.keys())

    # --- Connection ----------------------------------------------------

    def connection_states(self) -> dict:
        """Return which subsystems are currently connected.

        Returns
        -------
        dict
            Maps ``'fluid'`` / ``'imaging'`` / ``'illumination'`` to a bool
            (True when that subsystem object has been built/connected).
        """
        return {
            'fluid': self.fluid_system is not None,
            'imaging': self.imaging_system is not None,
            'illumination': self.illumination_system is not None,
        }

    def connect_fluid(self, fluid):
        """Build and connect the Hamilton (legacy) fluid system.

        Merges the loaded setup's hardware wiring with the experiment's
        reservoir choices (``configs.assemble_hamilton_config``). For an
        emulated setup the *real* drivers run over the fake serial wire
        emulator, so fill/clean/manual-pump all work without instruments.

        The design's fluid ``parameters`` are assigned to the system on
        connect (as an empty protocol) so manual controls (fill/clean/pump)
        work immediately, before any Run Sequence is loaded.

        Parameters
        ----------
        fluid : dict
            The experiment design's ``fluid`` section (with ``settings`` —
            needs ``reservoir_names`` — and optional ``parameters``). A bare
            settings dict is also accepted.

        Returns
        -------
        object
            The constructed fluid system.
        """
        if self._setup is None:
            raise RuntimeError("no setup loaded; call load_setup first")
        from PycroFlow.configs import assemble_hamilton_config
        import PycroFlow.hamilton_architecture as ha

        if 'settings' in fluid:
            settings = fluid['settings']
            parameters = fluid.get('parameters', {})
        else:  # a bare settings dict
            settings = fluid
            parameters = {}

        hamilton, tubing = assemble_hamilton_config(self._setup, settings)
        if hamilton.get('system_type') != 'legacy':
            raise NotImplementedError(
                "system_type {!r} is not implemented".format(
                    hamilton.get('system_type')))
        interface = hamilton['interface']

        if self.is_emulated():
            from PycroFlow.tests.emulators import (
                patch_serial, patch_ibidi_serial)
            # patch_ibidi_serial is a no-op unless the setup wires an ibidi
            # multiplexer (whose driver opens its own serial port).
            with patch_serial(), patch_ibidi_serial():
                ha.connect(interface['COM'], interface['baud'])
                self.fluid_system = ha.LegacyArchitecture(hamilton, tubing)
        else:
            ha.connect(interface['COM'], interface['baud'])
            self.fluid_system = ha.LegacyArchitecture(hamilton, tubing)

        # Seed parameters so manual fill/clean/pump work before a Run
        # Sequence is loaded; translate later re-assigns the full protocol.
        self.fluid_system._assign_protocol(
            {'parameters': dict(parameters), 'protocol_entries': []})
        return self.fluid_system

    def connect_imaging(self, imaging_config=None):
        """Build and connect the imaging system.

        For an emulated setup an :class:`EmulatedImagingSystem` is built and
        ``imaging_config`` is ignored. Otherwise a real
        :class:`PycroFlow.imaging.ImagingSystem` is built from
        ``imaging_config`` (assemble it with
        :func:`PycroFlow.configs.assemble_imaging_config`).

        Parameters
        ----------
        imaging_config : str or dict, optional
            Imaging config (path or dict). Required for a real setup.

        Returns
        -------
        object
            The constructed imaging system.
        """
        if self.is_emulated():
            from PycroFlow.tests.emulators import EmulatedImagingSystem
            self.imaging_system = EmulatedImagingSystem()
        else:
            import PycroFlow.imaging as im
            self.imaging_system = im.ImagingSystem(
                _load_yaml_or_dict(imaging_config))
        return self.imaging_system

    def connect_illumination(self):
        """Build the illumination system.

        For an emulated setup an :class:`EmulatedIlluminationSystem` is built.
        Otherwise a real :class:`PycroFlow.illumination.IlluminationSystem`
        is built, given the microscope setup's monet config name; its monet
        control loads lazily on first laser use.

        Returns
        -------
        object
            The constructed illumination system.
        """
        if self.is_emulated():
            from PycroFlow.tests.emulators import EmulatedIlluminationSystem
            self.illumination_system = EmulatedIlluminationSystem()
        else:
            import PycroFlow.illumination as il
            self.illumination_system = il.IlluminationSystem(
                setup=self.get_monet_setup())
        return self.illumination_system

    # --- Disconnection -------------------------------------------------

    def disconnect_fluid(self) -> None:
        """Release the fluid (Hamilton serial) connection. No-op if absent."""
        if self.fluid_system is None:
            return
        try:
            import PycroFlow.hamilton_architecture as ha
            if self.is_emulated():
                from PycroFlow.tests.emulators import patch_serial
                with patch_serial():
                    ha.disconnect()
            else:
                ha.disconnect()
        except Exception as exc:
            logger.warning("fluid disconnect failed: {!r}".format(exc))
        self.fluid_system = None

    def disconnect_imaging(self) -> None:
        """Release the imaging system (and its MM Core lock)."""
        if self.imaging_system is None:
            return
        try:
            if hasattr(self.imaging_system, 'close'):
                self.imaging_system.close()
        except Exception as exc:
            logger.warning("imaging disconnect failed: {!r}".format(exc))
        self.imaging_system = None

    def disconnect_illumination(self) -> None:
        """Release the illumination system (closes monet if it exposes one)."""
        if self.illumination_system is None:
            return
        try:
            for name in ('close', 'shutdown', 'disconnect'):
                fn = getattr(self.illumination_system, name, None)
                if callable(fn):
                    fn()
                    break
        except Exception as exc:
            logger.warning("illumination disconnect failed: {!r}".format(exc))
        self.illumination_system = None

    def disconnect(self, key: str) -> None:
        """Disconnect one subsystem ('fluid'/'imaging'/'illumination')."""
        {
            'fluid': self.disconnect_fluid,
            'imaging': self.disconnect_imaging,
            'illumination': self.disconnect_illumination,
        }[key]()

    def disconnect_all(self) -> None:
        """Disconnect every connected subsystem."""
        self.disconnect_fluid()
        self.disconnect_imaging()
        self.disconnect_illumination()

    # --- Fluid ---------------------------------------------------------

    def fill_tubings(self) -> None:
        self._require('fluid_system')
        self.fluid_system.fill_tubings()

    def clean_tubings(self) -> None:
        # confirm=False: no terminal prompt — GUI callers confirm via a
        # dialog before calling, and there is no stdin to block on.
        self._require('fluid_system')
        self.fluid_system.clean_tubings(confirm=False)

    def deliver_fluid(self, reservoir_id: int, volume: float) -> None:
        self._require('fluid_system')
        self.fluid_system.deliver_fluid(reservoir_id, volume)

    def set_valves(self, reservoir_id: int) -> None:
        """Route the manifold valves to access ``reservoir_id`` (manual)."""
        self._require('fluid_system')
        self.fluid_system._set_valves(reservoir_id)

    def stop_all_moves(self) -> None:
        """Emergency stop on the fluid system. Safe to call from anywhere."""
        if self.fluid_system is None:
            return
        try:
            self.fluid_system.stop_all_moves()
        except Exception as exc:
            logger.warning("stop_all_moves failed: {!r}".format(exc))

    def manual_pump(self, pump_name: str, *args, **kwargs):
        """Drive a named pump manually. Replaces the previous
        ``self.fluid_system._pump`` reach-through in the CLI.

        ``pump_name`` is one of the keys exposed by the fluid system
        (e.g. ``'pump_a'``, ``'pump_out'``). Extra args are forwarded.
        """
        self._require('fluid_system')
        pump_method = getattr(self.fluid_system, '_pump', None)
        if pump_method is None:
            raise RuntimeError("fluid_system has no _pump method")
        pump_obj = getattr(self.fluid_system, pump_name, None)
        if pump_obj is None:
            raise KeyError(
                "no such pump on fluid_system: {!r}".format(pump_name))
        return pump_method(pump_obj, *args, **kwargs)

    # --- Imaging -------------------------------------------------------

    def close_imaging(self) -> None:
        """Release the MM Core lock (Stage 1) and any other resources."""
        if self.imaging_system is None:
            return
        if hasattr(self.imaging_system, 'close'):
            self.imaging_system.close()

    # --- Illumination --------------------------------------------------

    def set_laser(self, laser: int) -> None:
        self._require('illumination_system')
        self.illumination_system.set_laser(laser)

    def set_laser_enabled(self, laser: int, enabled: bool = True) -> None:
        self._require('illumination_system')
        self.illumination_system.set_laser_enabled(laser, enabled=enabled)

    def set_sample_power(self, power: float, warmup_delay: float = 0) -> None:
        self._require('illumination_system')
        self.illumination_system.set_sample_power(power, warmup_delay)

    # --- Cleanup -------------------------------------------------------

    def close(self) -> None:
        """Release everything releasable. Idempotent."""
        self.stop_all_moves()
        self.close_imaging()

    # --- Internals -----------------------------------------------------

    def _require(self, attr: str) -> None:
        if getattr(self, attr, None) is None:
            raise RuntimeError(
                "SystemService.{} is None; no {} configured".format(
                    attr, attr.replace('_system', '')
                )
            )
