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

    def connect_fluid(self, hamilton_config, tubing_config):
        """Build and connect the Hamilton (legacy) fluid system.

        Parameters
        ----------
        hamilton_config : str or dict
            Hamilton system config (path to YAML or parsed dict). Must
            carry an ``interface`` (COM/baud) and ``system_type == 'legacy'``.
        tubing_config : str or dict
            Tubing config (path to YAML or parsed dict).

        Returns
        -------
        object
            The constructed fluid system.
        """
        import PycroFlow.hamilton_architecture as ha

        hcfg = _load_yaml_or_dict(hamilton_config)
        tcfg = _load_yaml_or_dict(tubing_config)
        if hcfg.get('system_type') != 'legacy':
            raise NotImplementedError(
                "system_type {!r} is not implemented".format(
                    hcfg.get('system_type')))
        interface = hcfg['interface']
        ha.connect(interface['COM'], interface['baud'])
        self.fluid_system = ha.LegacyArchitecture(hcfg, tcfg)
        return self.fluid_system

    def connect_imaging(self, imaging_config):
        """Build and connect the imaging system.

        Parameters
        ----------
        imaging_config : str or dict
            Imaging config (path to YAML or parsed dict).

        Returns
        -------
        object
            The constructed imaging system.
        """
        import PycroFlow.imaging as im

        self.imaging_system = im.ImagingSystem(
            _load_yaml_or_dict(imaging_config))
        return self.imaging_system

    def connect_illumination(self):
        """Build the illumination system.

        No config is needed at construction; the monet laser control loads
        when a protocol carrying an illumination ``setup`` is assigned.

        Returns
        -------
        object
            The constructed illumination system.
        """
        import PycroFlow.illumination as il

        self.illumination_system = il.IlluminationSystem()
        return self.illumination_system

    # --- Fluid ---------------------------------------------------------

    def fill_tubings(self) -> None:
        self._require('fluid_system')
        self.fluid_system.fill_tubings()

    def clean_tubings(self) -> None:
        self._require('fluid_system')
        self.fluid_system.clean_tubings()

    def deliver_fluid(self, reservoir_id: int, volume: float) -> None:
        self._require('fluid_system')
        self.fluid_system.deliver_fluid(reservoir_id, volume)

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
