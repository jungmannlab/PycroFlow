"""Hardware-control commands the frontends expose.

Wraps the fluid / imaging / illumination systems so the CLI (and future
GUI) can drive them without reaching into private attributes. Previous
``frontend_cli`` poked ``self.fluid_system._pump`` directly — that's the
sort of leakage this service eliminates.
"""
from __future__ import annotations

from loguru import logger


from PycroFlow.configs import IBIDI_MULTIFLOW


def _as_wavelength(value):
    """Return ``value`` as an int wavelength if it parses, else unchanged.

    monet config keys may be YAML ints (``640``) or strings (``'640'``); the
    experiment design's ``laser`` field is an int.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


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
        self._monet_config = None

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
        from PycroFlow.configs import load_setup, monet_config

        self._setup = load_setup(name)
        self._setup_name = self._setup.get('setup', name)
        self._monet_config = monet_config(self._setup)
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

    def setup_name(self):
        """Return the loaded setup's own name (as in the setup selector)."""
        return self._setup_name

    def get_monet_setup(self):
        """Return the monet ``CONFIGS`` key the loaded setup illuminates with.

        This names the *microscope* whose lasers are driven, which need not be
        the setup's own name (e.g. an ``Ibidi`` fluidics setup running on the
        ``Mercury`` microscope declares ``illumination.config: Mercury``).
        """
        return self._monet_config

    def is_emulated(self) -> bool:
        """Whether the loaded setup runs against emulated hardware."""
        return bool(self._setup and self._setup.get('emulated'))

    def reservoir_ids(self) -> list:
        """Reservoir ids wired in the current setup's manifold (sorted).

        Empty when no setup is loaded. Used by the GUI to restrict the
        experiment design's reservoir-id inputs to the setup's hardware.
        """
        from PycroFlow.configs import setup_reservoirs

        ids = [e['id'] for e in setup_reservoirs(self._setup)
               if isinstance(e, dict) and 'id' in e]
        return sorted(ids)

    def laser_options(self) -> list:
        """Laser lines (wavelengths) defined in the setup's monet config.

        monet configs come in two shapes: a multi-laser one with a ``lasers``
        mapping keyed by wavelength, and a single-laser one that names its
        line in ``index[monet.LASER_TAG]`` only. Both are read here, so the
        GUI's laser dropdown is populated either way.

        Returns
        -------
        list
            Wavelengths (ints where they parse as such), sorted. Empty when
            no setup is loaded, monet/the config is unavailable (not
            installed, or mocked in tests), or the config names no laser —
            the design's ``laser`` field stays typeable in that case.
        """
        name = self.get_monet_setup()
        if not name:
            return []
        try:
            import monet
        except Exception as exc:
            logger.debug("no laser options: monet import failed: {!r}".format(
                exc))
            return []
        configs = getattr(monet, 'CONFIGS', None)
        if not isinstance(configs, dict):
            logger.debug("no laser options: monet.CONFIGS is unavailable")
            return []
        mconfig = configs.get(name)
        if not isinstance(mconfig, dict):
            logger.warning(
                "no laser options: monet config {!r} not found (have: "
                "{})".format(name, sorted(configs)))
            return []

        lasers = mconfig.get('lasers')
        if isinstance(lasers, dict) and lasers:
            return sorted(_as_wavelength(k) for k in lasers)
        # Single-laser config: the line lives in the database index instead.
        index = mconfig.get('index')
        tag = getattr(monet, 'LASER_TAG', 'wavelength [nm]')
        if isinstance(index, dict) and index.get(tag) is not None:
            return [_as_wavelength(index[tag])]
        logger.warning(
            "monet config {!r} declares no lasers (neither a 'lasers' "
            "mapping nor index[{!r}]); the design's laser field stays "
            "free-text".format(name, tag))
        return []

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
        Otherwise the setup's monet config name is validated against
        ``monet.CONFIGS`` and a real
        :class:`PycroFlow.illumination.IlluminationSystem` is built with it.
        The lasers themselves still open lazily on first use, so connecting
        does not claim the laser COM port (the Monet tab stays usable) — but a
        misconfigured setup fails here instead of mid-run.

        Returns
        -------
        object
            The constructed illumination system.

        Raises
        ------
        RuntimeError
            The setup declares no monet config name.
        KeyError
            The monet config name is not among ``monet.CONFIGS``.
        """
        if self.is_emulated():
            from PycroFlow.tests.emulators import EmulatedIlluminationSystem
            self.illumination_system = EmulatedIlluminationSystem()
        else:
            import PycroFlow.illumination as il
            name = self.get_monet_setup()
            self._check_monet_config(name)
            self.illumination_system = il.IlluminationSystem(setup=name)
        return self.illumination_system

    @staticmethod
    def _check_monet_config(name) -> None:
        """Verify ``name`` is a usable monet config key. Raise if it is not.

        When monet is absent or mocked (``CONFIGS`` is not a dict, as in the
        test suite) the check is skipped with a warning rather than failing —
        the same defensiveness as :meth:`laser_options`.
        """
        if not name:
            raise RuntimeError(
                "the loaded setup declares no monet config; set "
                "'illumination: {config: <monet CONFIGS key>}' in its "
                "setup YAML")
        try:
            import monet
        except Exception as exc:
            logger.warning(
                "monet is not importable ({!r}); connecting illumination "
                "without validating config {!r}".format(exc, name))
            return
        configs = getattr(monet, 'CONFIGS', None)
        if not isinstance(configs, dict):
            logger.warning(
                "monet.CONFIGS is unavailable; connecting illumination "
                "without validating config {!r}".format(name))
            return
        if name not in configs:
            raise KeyError(
                "monet config {!r} does not exist. The setup's "
                "illumination.config must name the microscope's monet "
                "config; available: {}".format(name, sorted(configs)))

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

    def sync_fluid_reservoirs(self, fluid) -> bool:
        """Re-apply an experiment design's reservoirs to the live system.

        The fluid system is built from the design at connect time, but the
        design keeps being edited afterwards. Adding a reservoir and
        re-translating would otherwise leave the connected system routing by
        the old list, and the run would die on the first step touching the
        new one. Call this whenever the design may have changed (the GUI does
        it on translate); it rebuilds the reservoir bookkeeping only, with no
        serial reconnect.

        Parameters
        ----------
        fluid : dict
            The design's ``fluid`` section (or a bare settings dict).

        Returns
        -------
        bool
            True if the live system was updated, False if there was nothing
            to update (not connected, no setup, or an emulated/simple system
            that does not take a reservoir config).
        """
        if self.fluid_system is None or self._setup is None:
            return False
        update = getattr(self.fluid_system, 'update_reservoirs', None)
        if not callable(update):
            return False
        settings = fluid.get('settings', fluid) if fluid else {}
        if not settings:
            return False
        from PycroFlow.configs import assemble_hamilton_config

        hamilton, _ = assemble_hamilton_config(self._setup, settings)
        update(hamilton)
        logger.debug("fluid reservoirs re-synced from the design: {}".format(
            sorted(getattr(self.fluid_system, 'reservoir_paths', {}))))
        return True

    def routable_reservoirs(self) -> list:
        """Reservoir ids the connected fluid system can currently route to."""
        if self.fluid_system is None:
            return []
        return sorted(getattr(self.fluid_system, 'reservoir_paths', {}) or {})

    def reservoir_route(self, reservoir_id) -> dict:
        """Return the ``{valve address: position}`` map for a reservoir.

        Read from the setup's manifold, so it answers "what would setting
        the valves to this reservoir actually do?" whether or not the loaded
        design uses it. Empty dict if the id is not wired.
        """
        from PycroFlow.configs import setup_reservoirs

        for entry in setup_reservoirs(self._setup):
            if entry.get('id') == reservoir_id:
                return dict(entry.get('valve_pos') or {})
        return {}

    def _valve_labels(self) -> dict:
        """Map each valve address in the setup to a human-readable name."""
        fluid = (self._setup or {}).get('fluid') or {}
        labels = {}
        pumps = fluid.get('pumps') or {}
        for name in ('pump_a', 'pump_out'):
            address = (pumps.get(name) or {}).get('address')
            if address is not None:
                labels[address] = name
        mux = fluid.get('multiplexer') or {}
        if mux.get('driver') == IBIDI_MULTIFLOW:
            labels[mux.get('address', 'ibidi')] = 'ibidi multiplexer'
        for valve in mux.get('valves') or []:
            address = valve.get('address')
            if address is not None:
                labels[address] = 'MVP valve {}'.format(address)
        return labels

    def describe_reservoir_route(self, reservoir_id) -> str:
        """Describe, in words, how a reservoir is reached.

        Spells out which valve goes where — including that the ibidi
        multiplexer opens several channels at once and closes the rest — so
        the manual controls say what they are about to do to the hardware.

        Returns
        -------
        str
            A one-line description, or an explanatory line when the
            reservoir is not wired / no setup is loaded.
        """
        if self._setup is None:
            return "No setup loaded."
        route = self.reservoir_route(reservoir_id)
        if not route:
            return "Reservoir {} is not wired in setup {!r}.".format(
                reservoir_id, self._setup_name)
        labels = self._valve_labels()
        parts = []
        for address, position in route.items():
            name = labels.get(address, "valve {}".format(address))
            if isinstance(position, (list, tuple, set)):
                channels = ', '.join(str(c) for c in position)
                parts.append(
                    "{} opens channels {} (all others closed)".format(
                        name, channels))
            else:
                parts.append("{} → {}".format(name, position))
        in_design = False
        if self.fluid_system is not None:
            in_design = reservoir_id in getattr(
                self.fluid_system, 'reservoir_paths', {})
        return "Reservoir {}: {}. {}".format(
            reservoir_id, '; '.join(parts),
            "Used by the loaded experiment design."
            if in_design else "Wired in the setup, not used by the design.")

    def set_valves(self, reservoir_id: int) -> None:
        """Route the manifold valves to access ``reservoir_id`` (manual).

        Manual control is not limited to the reservoirs the loaded experiment
        design names: any reservoir wired in the *setup's* manifold can be
        reached, which is what testing the plumbing needs. Design-selected
        reservoirs still go through the fluid system's own routing.

        Raises
        ------
        KeyError
            ``reservoir_id`` is not wired in the setup's manifold.
        """
        self._require('fluid_system')
        try:
            self.fluid_system._set_valves(reservoir_id)
            return
        except KeyError:
            pass   # not part of the design; fall back to the setup manifold
        from PycroFlow.configs import setup_reservoirs

        for entry in setup_reservoirs(self._setup):
            if entry.get('id') == reservoir_id:
                self.fluid_system.set_valve_positions(entry['valve_pos'])
                return
        raise KeyError(
            "reservoir {!r} is not wired in setup {!r}'s fluid.reservoirs "
            "(which wires {})".format(
                reservoir_id, self._setup_name, self.reservoir_ids()))

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
