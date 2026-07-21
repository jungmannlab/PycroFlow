""" """

import threading
import time
from functools import wraps

import numpy as np
import yaml

# import logging
from loguru import logger

import PycroFlow.pyHamilton as ham
from PycroFlow.hamilton_components import (
    Pump,
    Reservoir,
    ReservoirDict,
    TubingConfig,
    Valve,
)
from PycroFlow.orchestration import AbstractSystem

# logger = logging.getLogger(__name__)
# logger.level = logging.DEBUG
# logger.addHandler(logging.StreamHandler(sys.stdout))

is_connected = False


def connect(port, baudrate):
    assert isinstance(port, str)
    ham.connect(port, baudrate)
    global is_connected
    is_connected = True


def disconnect():
    ham.disconnect()
    global is_connected
    is_connected = False


# Legacy system architecture:
#   * N valves for N*6+1 reservoirs ('valve_a')
#   * 1 syringe pump ('pump_a')
#   * sample
#   * 1 syringe pump ('pump_out')
#   * waste
#
# The instrument-topology dicts now live in YAML under PycroFlow/configs/
# so the wiring can be branched/diffed without editing source. They are
# re-exported here at import time as ``legacy_system_config`` and
# ``legacy_tubing_config`` to preserve back-compat with existing call
# sites (frontend_cli.py, example_experiment/*.py, prep_legacy_wettest()).
from PycroFlow.configs import (  # noqa: E402
    load_legacy_system,
    load_legacy_tubing,
)

legacy_system_config = load_legacy_system("legacy_system")
legacy_tubing_config = load_legacy_tubing("legacy_tubing")


def prep_legacy_wettest(port="4", baudrate=9600):
    """Prepare a LegacyArchitecture instance with a wettest protocol.
    This is a convenience function for easy setup in the command
    line
    """
    la = LegacyArchitecture(
        legacy_system_config, legacy_tubing_config, port, baudrate
    )
    la._create_wettest_protocol(400)
    return la


def do_legacy_wettest(la):
    print('****************   testing "_set_valves"')
    la._set_valves(1)
    la._set_valves(14)
    print('****************   Test of "_set_valves" was successful.')
    print('****************   testing "_set_flush_valve"')
    la._set_flush_valve()
    print('****************   Test of "_set_flush_valve" was successful.')
    print('****************   testing "_pump"')
    la._pump(la.pump_a, 100)
    la._pump(la.pump_a, 100, pickup_dir="out", dispense_dir="in")
    la._pump(
        la.pump_a,
        100,
        pickup_dir="in",
        dispense_dir="in",
        pickup_res=la.special_names["flushbuffer_a"],
        dispense_res=la.special_names["flushbuffer_a"],
    )
    print('****************   Test of "_pump" was successful.')


def do_test_caltube(la):
    print('****************   testing "_calibrate_tubing"')
    la._calibrate_tubing(400)
    print('****************   Test of "_calibrate_tubing" was successful.')


def do_test_protocol(la):
    print("****************   testing protocol entry execution")
    la.execute_protocol_entry(0)
    la.execute_single_protocol_entry(0)
    for i in len(la.protocol["protocol_entries"]):
        print("********************   protocol entry ", i)
        la.execute_protocol_entry(i)
    print('****************   Test of "_calibrate_tubing" was successful.')


def find_reservoirs(la):
    la._set_flush_valve(to_flush=True)
    la._set_valves(la.special_names["flushbuffer_a"])
    print(
        "****************   pumping  air (from outlet) into "
        "flushbuffer_a reservoir"
    )
    repeat = True
    while repeat:
        la._pump(
            la.pump_a,
            la.pump_a.syringe_volume,
            pickup_dir="out",
            dispense_dir="in",
        )
        result = input("repeat? [Y / N] | quit [Q]")
        repeat = "y" in result.lower()
        if "q" in result.lower():
            return

    for resid, res in la.reservoir_a.items():
        la._set_valves(resid)
        print(
            "****************   pumping  air (from outlet) into "
            "reservoir {:s}".format(str(resid))
        )
        repeat = True
        while repeat:
            la._pump(
                la.pump_a,
                la.pump_a.syringe_volume,
                pickup_dir="out",
                dispense_dir="in",
            )
            result = input("repeat? [Y / N] | quit [Q]")
            repeat = "y" in result.lower()
            if "q" in result.lower():
                return


def run_in_thread(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        thread = threading.Thread(target=fn, args=args, kwargs=kwargs)
        thread.start()
        return thread  # Optionally return the thread object

    return wrapper


class LegacyArchitecture(AbstractSystem):
    """Represents the Legacy Architecture, with many valves and
    reservoirs, connected to an input syringe pump, connected to
    the sample, connected to an output/waste syringe pump.
    """

    valve_a = {}
    valve_flush = None
    pump_a = None
    pump_out = None
    tubing_config = {}
    reservoir_a = ReservoirDict()
    spill_sensor = None
    # maps reservoir ids to the fluid paths (legacy only has 'a')
    reservoir_paths = {}
    protocol = []
    last_protocol_entry = -1

    extractionfactor = 1

    def __init__(
        self, system_config, tubing_config=None, port="4", baudrate=9600
    ):
        self._assign_system_config(system_config)

        if not is_connected:
            if "COM" in port:
                port = port[3:]
            connect(port, baudrate)
        self._test_communication()

        if tubing_config:
            self._assign_tubing_config(tubing_config)
        else:
            self._calibrate_tubing()

        self.handler_ref = None
        # Estimated-duration tracking for the within-step progress bar:
        # (start_time, est_seconds, label) for the running inject/pump_out,
        # else None. See _estimate_entry_duration / get_step_progress.
        self._step_estimate = None
        # self.fluid_lock = threading.Lock()
        # self.fluid_pause = threading.Event()
        # self.fluid_abort = threading.Event()

        if "spill_sensor" in system_config.keys():
            self._start_spill_sensor(system_config["spill_sensor"])
        else:
            self.spill_sensor = None

    def _start_spill_sensor(self, sensor_config):
        """Connect and start monitoring the Arduino spill sensor.

        Parameters
        ----------
        sensor_config : dict
            Keyword arguments for :class:`ArduinoSensorInterface`, e.g.
            ``{"port": None, "baud_rate": 115200, "timeout": 2}``.
        """
        from PycroFlow.spill_sensor_arduino import ArduinoSensorInterface

        self.spill_sensor = ArduinoSensorInterface(**sensor_config)
        if not self.spill_sensor.connect():
            logger.debug(
                "Failed to establish connection to spill sensor. Exiting."
            )
            return
        # avoid interference between broadcasting and polling
        self.spill_sensor.stop_broadcast()
        # self.spill_sensor_wet_flag = self.spill_sensor.sensor_wet_flag
        self.spill_sensor.monitor_sensor(fn_on_wet=self.pause_execution)

    def _stop_spill_sensor(self):
        if self.spill_sensor is not None:
            self.spill_sensor.stop_monitoring()
            self.spill_sensor.disconnect()

    def __del__(self):
        self._stop_spill_sensor()
        mux = getattr(self, "multiplexer", None)
        if mux is not None:
            mux.close()

    def _assign_system_config(self, config):
        """Assign a system configuration.

        Parameters
        ----------
        config : dict
            The system configuration.
        """
        assert config["system_type"] == "legacy"
        for vconfig in config["valve_a"]:
            self.valve_a[vconfig["address"]] = Valve(**vconfig)
        # Optional ibidi MultiFlOW multiplexer: an alternative to the Hamilton
        # MVP rotary valves for reservoir multiplexing. It registers in the
        # valve map under its address (default 'ibidi') so _set_valves() drives
        # it via reservoir valve_pos entries like {ibidi: <channel>, 1: in}.
        self.multiplexer = None
        if config.get("ibidi"):
            from PycroFlow.ibidi_multiplexer import IbidiMultiplexer

            mux_cfg = dict(config["ibidi"])
            address = mux_cfg.pop("address", "ibidi")
            self.multiplexer = IbidiMultiplexer(address=address, **mux_cfg)
            self.valve_a[address] = self.multiplexer
        for rconfig in config["reservoir_a"]:
            self.reservoir_a.add(Reservoir(**rconfig))
            self.reservoir_paths[rconfig["id"]] = "a"
        if config.get("valve_flush"):
            self.valve_flush = Valve(**config["valve_flush"])
        else:
            self.valve_flush = None

        # pump_a_config = config['pump_a'].copy()
        # pump_a_config["pause_flag"] = self.handler_ref.txchange[
        #     "pause_protocol_flag"]
        # pump_a_config["abort_flag"] = self.handler_ref.txchange[
        #     "abort_protocol_flag"]
        # self.pump_a = Pump(**pump_a_config)
        self.pump_a = Pump(**config["pump_a"])
        self.valve_a[config["pump_a"]["address"]] = (
            self.pump_a
        )  # for setting valve positions
        # pump_out_config = config['pump_out'].copy()
        # pump_out_config["pause_flag"] = self.handler_ref.txchange[
        #     "pause_protocol_flag"]
        # pump_out_config["abort_flag"] = self.handler_ref.txchange[
        #     "abort_protocol_flag"]
        # self.pump_out = Pump(**pump_out_config)
        self.pump_out = Pump(**config["pump_out"])

        self.special_names = config["special_names"]
        self.flush_pos = config["flush_pos"]
        self.cleaning_reservoirs = config.get("cleaning_reservoirs", {})

    def _assign_protocol(self, protocol):
        self.protocol = protocol["protocol_entries"]
        self.parameters = protocol["parameters"]

    def _assign_tubing_config(self, config):
        self.tubing_config = TubingConfig(config)
        self.tubing_config.set_special_names(self.special_names)

    def _assign_multiprocess_events(
        self, pause_flag, abort_flag, abort_hamilton_wait_response_flag
    ):
        self.pause_flag = pause_flag
        self.abort_flag = abort_flag
        self.pump_a.pause_flag = pause_flag
        self.pump_a.abort_flag = abort_flag
        self.pump_out.pause_flag = pause_flag
        self.pump_out.abort_flag = abort_flag
        for k, vlv in self.valve_a.items():
            vlv.pause_flag = pause_flag
            vlv.abort_flag = abort_flag
        # valve_flush is optional; assign events too so its set_valve() does
        # not dereference a None pause_flag during injection.
        if self.valve_flush is not None:
            self.valve_flush.pause_flag = pause_flag
            self.valve_flush.abort_flag = abort_flag
        # creat the flag in hamilton communication for aborting the wait loop
        self.abort_hamilton_wait_response_flag = (
            abort_hamilton_wait_response_flag
        )
        ham.communication.abort_wait_response_flag = (
            abort_hamilton_wait_response_flag
        )

    def _test_communication(self):
        """Ask all devices for status to check whether they are connected."""
        conn_dev = {}
        result = self.pump_a.get_status()
        conn_dev["pump_a"] = result != ""
        result = self.pump_out.get_status()
        conn_dev["pump_out"] = result != ""
        for v_id, valve in self.valve_a.items():
            result = valve.get_status()
            conn_dev["valve_a-" + str(v_id)] = result != ""
        all_connected = all(conn_dev.values())
        if not all_connected:
            logger.warning("Not all devices are connected: " + str(conn_dev))
        else:
            logger.info("All Hamilton devices are connected.")

    def _create_wettest_protocol(self, vol):
        """Create a test protocol, pumping fluid from all reservoirs.

        The fluid is pumped from all reservoirs to the sample.

        Parameters
        ----------
        vol : float
            The amount of fluid to pump.
        """
        protocol = {
            "parameters": {
                "start_velocity": 50,
                "max_velocity": 3000,
                "stop_velocity": 500,
                "mode": "tubing_stack",  # or 'tubing_flush'
                "extractionfactor": 1,
            },
            "protocol_entries": [],
        }
        for rid, res in self.reservoir_a.items():
            protocol["protocol_entries"].append(
                {"$type": "inject", "reservoir_id": rid, "volume": vol}
            )
        self._assign_protocol(protocol)

    def _calibrate_tubing(self, max_vol):
        """Calibrate the tubings of the system.

        1. Fill all reservoir tubings.
        2. Ask user to insert empty and weighed tubes, also to flush and
           outlet.
        3. Empty flush and outlet tubings.
        4. Fill flushbuffer tubing with its tubing volume plus all the pump
           connecting volumes.
        5. Empty into all reservoirs (this is only reservoir to nearest pump
           volume).
        6. Ask user to weigh all reservoirs and enter values, and fill
           flushbuffer reservoir.
        7. Fill flushbuffer path.
        8. Empty outlet tubing.
        9. Empty the flushbuffer path into.

        Parameters
        ----------
        max_vol : float
            All relevant tubing volumes are definitely below this.
        """
        logger.debug("calibrating tubings")
        velocity = self.parameters.get("clean_velocity")
        if not velocity:
            velocity = self.parameters["max_velocity"]
        # print(self.reservoir_a.reservoirs)
        measured_volumes = {}
        # find reservoirs for each valve to measure the inter-valve volumes
        # this is assuming a linear arrangement

        # assuming a linear arrangement of valves, find the valve order
        # according to the config: valve IDs in order of appearance
        # from the pump, and one reservoir each that is connected to them
        nvalves = len(self.valve_a.keys())
        # a reseroir ID for each valve, sorted by number of valves from pump
        nvalves_res = [0] * nvalves
        # all valves present, sorted by number of valves from pump
        nvalves_valves = [[]] * nvalves
        # the additional valve, sorted by number of valves from pump
        valve_order = [0] * nvalves
        for resid, res in self.reservoir_a.items():
            nvalves = len(res.valve_positions.keys())
            if resid not in self.special_names.values():
                nvalves_res[nvalves - 1] = resid
            nvalves_valves[nvalves - 1] = list(res.valve_positions.keys())
        for nv in range(nvalves):
            if nv == 0:
                valve_order[nv] = nvalves_valves[nv][0]
                continue
            # find the additional valve
            valve_order[nv] = (
                list(set(nvalves_valves[nv]) - set(nvalves_valves[nv - 1]))
            )[0]
        logger.debug("nvalves_res: {:s}".format(str(nvalves_res)))
        logger.debug("nvalves_valves: {:s}".format(str(nvalves_valves)))
        logger.debug("valve_order: {:s}".format(str(valve_order)))

        # 1. fill tubings
        totalvol = (
            self.reservoir_a.len * max_vol + 3 * self.pump_a.syringe_volume
        )
        input(
            "Please fill flushbuffer reservoir with at least "
            + "{:.1f} ml flush buffer, ".format(totalvol / 1000)
            + "and insert weighed tubes in the other reservoirs."
            + " Press enter to proceed.\r\n"
        )
        # pre-fill tubing from Flushbuffer to syringe, including the syringe
        for i in range(3):
            self._pump(
                self.pump_a,
                self.pump_a.syringe_volume,
                velocity=velocity,
                pickup_dir="in",
                dispense_dir="out",
                pickup_res=self.special_names["flushbuffer_a"],
            )
        for resid, res in self.reservoir_a.items():
            # fill reservoir tubings
            self._pump(
                self.pump_a,
                max_vol,
                velocity=velocity,
                pickup_dir="in",
                dispense_dir="in",
                pickup_res=self.special_names["flushbuffer_a"],
                dispense_res=resid,
            )
        # fill outlet tubings
        if isinstance(self.valve_flush, Valve):
            self._set_flush_valve(to_flush=False)
            self._pump(
                self.pump_a,
                max_vol,
                velocity=velocity,
                pickup_dir="in",
                dispense_dir="out",
                pickup_res=self.special_names["flushbuffer_a"],
            )
            self._set_flush_valve(to_flush=True)
            self._pump(
                self.pump_a,
                max_vol,
                velocity=velocity,
                pickup_dir="in",
                dispense_dir="out",
                pickup_res=self.special_names["flushbuffer_a"],
            )
        elif isinstance(self.valve_flush, int):
            self._pump(
                self.pump_a,
                max_vol,
                velocity=velocity,
                pickup_dir="in",
                dispense_dir="out",
                pickup_res=self.special_names["flushbuffer_a"],
            )
            self._pump(
                self.pump_a,
                max_vol,
                velocity=velocity,
                pickup_dir="in",
                dispense_dir=self.valve_flush,
                pickup_res=self.special_names["flushbuffer_a"],
            )
        # empty syringe through the flush valve outlet
        input(
            "Please replace all tubes with empty and weighed tubes."
            + " Make sure dispensed fluids are not picked up in"
            + " the flush tube."
            + " Press enter to proceed.\r\n"
        )

        # empty flush and sample tubing
        dispensevol_per_stroke = self.pump_a.syringe_volume - max_vol
        if dispensevol_per_stroke <= 0:
            raise ValueError("Cannot continue this calibration procedure.")
        # add 4 to n_strokes for safety
        n_strokes = int(np.ceil(max_vol / dispensevol_per_stroke)) + 4
        for i in range(n_strokes):
            self._pump(
                self.pump_a,
                self.pump_a.syringe_volume,
                velocity=velocity,
                pickup_dir="out",
                dispense_dir="out",
                pickup_flushvalve=True,
                dispense_flushvalve=True,
            )
        # self._set_flush_valve(to_flush=False)
        for i in range(2):
            self._pump(
                self.pump_a,
                self.pump_a.syringe_volume,
                velocity=velocity,
                pickup_dir="out",
                dispense_dir="out",
                pickup_flushvalve=True,
                dispense_flushvalve=False,
            )
        result = input(
            "Please replace sample and flush tube with an empty "
            + "tube and weigh the dispensed volume. Enter the results in "
            + 'the format "sample: xxx.xx; flush: xxx.xx" with xxx.xx being'
            + " the measured net weight in mg. Press enter to proceed.\r\n"
        )
        # print(result)
        result = result.split(";")
        for sglres in result:
            destination, tubvol = sglres.split(":")
            tubvol = float(tubvol.strip())
            destination = destination.strip()
            if destination == "flush":
                measured_volumes[("pump_a", "flush_waste")] = tubvol
            elif destination == "sample":
                measured_volumes[("valve_flush", "sample")] = tubvol
            else:
                raise KeyError(
                    "Cannot process catoegory {:s}.".format(destination)
                    + ' Please only provide "sample" and "flush"'
                )

        # empty flushbuffer tubing
        self._set_valves(self.special_names["flushbuffer_a"])
        for i in range(2):  # 2 to make sure
            self._pump(
                self.pump_a,
                self.pump_a.syringe_volume,
                velocity=velocity,
                pickup_dir="out",
                dispense_dir="in",
            )
        for resid, res in self.reservoir_a.items():
            # fill reservoir tubings
            for i in range(2):
                self._pump(
                    self.pump_a,
                    self.pump_a.syringe_volume,
                    velocity=velocity,
                    pickup_dir="out",
                    dispense_dir="in",
                    dispense_res=resid,
                )
        vol_used = 2 * self.pump_a.syringe_volume + nvalves * max_vol
        result = input(
            "Please replace reservoir tubes with an empty tube and weigh "
            + "the dispensed volume. Fill flushbuffer reservoir with at "
            + "least {:.1f} ml flush buffer. ".format(vol_used / 1000)
            + 'Enter the measurement results in the format "flushbuffer: '
            + 'xxx.xx; ID1: xxx.xx; ID2: xxx.xx" with IDn being the reseroir '
            + "IDs and xxx.xx the measured net weight in mg. Press enter to "
            + "proceed.\r\n"
        )
        result = result.split(";")
        for sglres in result:
            destination, tubvol = sglres.split(":")
            tubvol = float(tubvol.strip())
            destination = destination.strip()
            if destination == "flushbuffer":
                continue
                # key = ('R' + str(self.special_names['flushbuffer_a']),
                #        'V' + str(valve_order[-1]))
            else:
                nv = self.reservoir_a.get_reservoir_nvalves(destination)
                key = ("R" + destination, "V" + str(valve_order[nv - 1]))
            measured_volumes[key] = tubvol

            # parts = result.split(':')
            # measured_volumes[parts[0].strip()] = float(parts[1].strip())

        # fill flushbuffer and thus main path
        for i in range(3):
            self._pump(
                self.pump_a,
                self.pump_a.syringe_volume,
                velocity=velocity,
                pickup_dir="in",
                dispense_dir="out",
                pickup_res=self.special_names["flushbuffer_a"],
                dispense_flushvalve=True,
            )
        result = input(
            "Please replace flushbuffer and sample tube with an empty tube."
            + " Make sure sample tubing does not pick up liquid. "
            + "Press enter to proceed.\r\n"
        )
        # empty the outlet tube
        for i in range(n_strokes):
            self._pump(
                self.pump_a,
                self.pump_a.syringe_volume,
                velocity=velocity,
                pickup_dir="out",
                dispense_dir="out",
                pickup_flushvalve=False,
                dispense_flushvalve=False,
                delay=1,
            )
        # empty into the reservoirs
        for resid in nvalves_res:
            for i in range(1):
                self._pump(
                    self.pump_a,
                    max_vol,
                    velocity=velocity,
                    pickup_dir="out",
                    dispense_dir="in",
                    dispense_res=resid,
                )
        for i in range(1):
            self._pump(
                self.pump_a,
                max_vol,
                pickup_dir="out",
                dispense_dir="in",
                velocity=velocity,
                dispense_res=self.special_names["flushbuffer_a"],
            )
        result = input(
            "Please weigh the flushbuffer and sample tube and reservoirs "
            + str(nvalves_res)
            + "."
            + 'Enter the measurement results in the format "flushbuffer: '
            + "xxx.xx; sample: "
            + 'xxx.xx; ID1: xxx.xx; ID2: xxx.xx" with IDn being the reseroir '
            + "IDs and xxx.xx the measured net weight in mg. Press enter to "
            + "proceed.\r\n"
        )
        result = result.split(";")
        for sglres in result:
            destination, tubvol = sglres.split(":")
            tubvol = float(tubvol.strip())
            destination = destination.strip()
            if destination == "flushbuffer":
                key = (
                    "R" + str(self.special_names["flushbuffer_a"]),
                    "V" + str(valve_order[-1]),
                )
            elif destination == "sample":
                key = ("pump_a", "valve_flush")
            else:
                nv = self.reservoir_a.get_reservoir_nvalves(destination)
                if nv == 1:
                    key = ("V" + str(valve_order[nv - 1]), "pump_a")
                else:
                    key = (
                        "V" + str(valve_order[nv - 1]),
                        "V" + str(valve_order[nv - 2]),
                    )
            measured_volumes[key] = tubvol
        logger.debug("measured volumes: {:s}".format(str(measured_volumes)))

        # Now, concatenate resID-pump volumes, instead of using
        # only the single steps via the valves
        for resid, res in self.reservoir_a.items():
            steps = ["R" + str(resid)]
            nv = self.reservoir_a.get_reservoir_nvalves(resid)
            for n in range(nv - 1, -1, -1):
                steps.append("V" + str(valve_order[n]))
            steps.append("pump_a")
            totvol = 0
            for sta, sto in zip(steps[:-1], steps[1:]):
                totvol += measured_volumes[(sta, sto)]
            measured_volumes[(steps[0], steps[-1])] = totvol
        measured_volumes[("valve_flush", "flush_waste")] = (
            measured_volumes[("pump_a", "flush_waste")]
            - measured_volumes[("pump_a", "valve_flush")]
        )

        logger.debug("measured volumes: {:s}".format(str(measured_volumes)))

        self._assign_tubing_config(measured_volumes)

        with open("measured_volumes.yaml", "w") as f:
            yaml.dump(measured_volumes, f)

    def _calibrate_tubing_dry(self, max_vol):
        """Calibrate the tubings of the system.

        1. Fill all reservoir tubings.
        2. Ask user to insert empty and weighed tubes, also to flush and
           outlet.
        3. Empty flush and outlet tubings.
        4. Fill flushbuffer tubing with its tubing volume plus all the pump
           connecting volumes.
        5. Empty into all reservoirs (this is only reservoir to nearest pump
           volume).
        6. Ask user to weigh all reservoirs and enter values, and fill
           flushbuffer reservoir.
        7. Fill flushbuffer path.
        8. Empty outlet tubing.
        9. Empty the flushbuffer path into.

        Parameters
        ----------
        max_vol : float
            All relevant tubing volumes are definitely below this.
        """
        print(self.reservoir_a.reservoirs)
        measured_volumes = {}
        # find reservoirs for each valve to measure the inter-valve volumes
        # this is assuming a linear arrangement

        # assuming a linear arrangement of valves, find the valve order
        # according to the config: valve IDs in order of appearance
        # from the pump, and one reservoir each that is connected to them
        nvalves = len(self.valve_a.keys())
        # a reseroir ID for each valve, sorted by number of valves from pump
        nvalves_res = [0] * nvalves
        # all valves present, sorted by number of valves from pump
        nvalves_valves = [[]] * nvalves
        # the additional valve, sorted by number of valves from pump
        valve_order = [0] * nvalves
        for resid, res in self.reservoir_a.items():
            nvalves = len(res.valve_positions.keys())
            nvalves_res[nvalves - 1] = resid
            nvalves_valves[nvalves - 1] = list(res.valve_positions.keys())
        for nv in range(nvalves):
            if nv == 0:
                valve_order[nv] = nvalves_valves[nv][0]
                continue
            # find the additional valve
            valve_order[nv] = (
                list(set(nvalves_valves[nv]) - set(nvalves_valves[nv - 1]))
            )[0]
        print("nvalves-res", nvalves_res)
        print("nvalves_valves", nvalves_valves)
        print("valve_order", valve_order)

        # 1. fill tubings
        totalvol = (
            self.reservoir_a.len * max_vol + 3 * self.pump_a.syringe_volume
        )
        print(
            "Please fill flushbuffer reservoir with at least "
            + "{:.1f} ml flush buffer, ".format(totalvol / 1000)
            + "and insert weighed tubes in the other reservoirs."
            + " Press enter to proceed.\r\n"
        )

        # empty syringe through the flush valve outlet
        print(
            "Please replace all tubes with empty and weighed tubes."
            + " Make sure dispensed fluids are not picked up in"
            + " sample and flush tubes."
            + " Press enter to proceed.\r\n"
        )

        # empty flush and sample tubing
        dispensevol_per_stroke = self.pump_a.syringe_volume - max_vol
        if dispensevol_per_stroke < 0:
            raise ValueError("Cannot continue this calibration procedure.")
        # add 2 to n_strokes for safety
        # n_strokes = int(np.ceil(max_vol / dispensevol_per_stroke)) + 2
        print(
            "Please replace sample and flush tube with an empty "
            + "tube and weigh the dispensed volume. Enter the results in "
            + 'the format "sample: xxx.xx; flush: xxx.xx" with xxx.xx being'
            + " the measured net weight in mg. Press enter to proceed.\r\n"
        )
        result = "sample: 250; flush: 300"
        # print(result)
        result = result.split(";")
        for sglres in result:
            destination, tubvol = sglres.split(":")
            tubvol = float(tubvol.strip())
            destination = destination.strip()
            if destination == "flush":
                measured_volumes[("pump_a", "flush_waste")] = tubvol
            elif destination == "sample":
                measured_volumes[("valve_flush", "sample")] = tubvol
            else:
                raise KeyError(
                    "Cannot process catoegory {:s}.".format(destination)
                    + ' Please only provide "sample" and "flush"'
                )

        # empty flushbuffer tubing
        vol_used = 2 * self.pump_a.syringe_volume + nvalves * max_vol
        print(
            "Please replace reservoir tubes with an empty tube and weigh "
            + "the dispensed volume. Fill flushbuffer reservoir with at "
            + "least {:.1f} ml flush buffer. ".format(vol_used / 1000)
            + 'Enter the measurement results in the format "flushbuffer: '
            + 'xxx.xx; ID1: xxx.xx; ID2: xxx.xx" with IDn being the reseroir '
            + "IDs and xxx.xx the measured net weight in mg. Press enter to "
            + "proceed.\r\n"
        )
        result = (
            "flushbuffer: 800; 0: 150; 1: 150; 7: 200; 8: 150; "
            + "15: 200; 16: 200"
        )
        result = result.split(";")
        for sglres in result:
            destination, tubvol = sglres.split(":")
            tubvol = float(tubvol.strip())
            destination = destination.strip()
            if destination == "flushbuffer":
                continue
                # key = ('R' + str(self.special_names['flushbuffer_a']),
                #        'V' + str(valve_order[-1]))
            else:
                nv = self.reservoir_a.get_reservoir_nvalves(destination)
                key = ("R" + destination, "V" + str(valve_order[nv - 1]))
            measured_volumes[key] = tubvol

            # parts = result.split(':')
            # measured_volumes[parts[0].strip()] = float(parts[1].strip())

        # fill flushbuffer and thus main path
        print(
            "Please replace flushbuffer tube with an empty tube."
            + "Press enter to proceed.\r\n"
        )
        # empty the outlet tube
        # empty into the reservoirs
        print(
            "Please weigh the flushbuffer tube and reservoirs "
            + str(nvalves_res)
            + "."
            + 'Enter the measurement results in the format "flushbuffer: '
            + 'xxx.xx; ID1: xxx.xx; ID2: xxx.xx" with IDn being the reseroir '
            + "IDs and xxx.xx the measured net weight in mg. Press enter to "
            + "proceed.\r\n"
        )
        result = "flushbuffer: 800; 1: 200; 8: 150; 16: 175"
        result = result.split(";")
        print("measured volumes from first measurements")
        print(measured_volumes)
        for sglres in result:
            destination, tubvol = sglres.split(":")
            tubvol = float(tubvol.strip())
            destination = destination.strip()
            if destination == "flushbuffer":
                key = (
                    "R" + str(self.special_names["flushbuffer_a"]),
                    "V" + str(valve_order[-1]),
                )
            else:
                nv = self.reservoir_a.get_reservoir_nvalves(destination)
                if nv == 1:
                    key = ("V" + str(valve_order[nv - 1]), "pump_a")
                else:
                    key = (
                        "V" + str(valve_order[nv - 1]),
                        "V" + str(valve_order[nv - 2]),
                    )
            print("adding mesurement of ", sglres, " central column: ", key)
            print(measured_volumes)
            measured_volumes[key] = tubvol

        # Now, concatenate resID-pump volumes, instead of using
        # only the single steps via the valves
        for resid, res in self.reservoir_a.items():
            steps = ["R" + str(resid)]
            nv = self.reservoir_a.get_reservoir_nvalves(resid)
            for n in range(nv - 1, -1, -1):
                steps.append("V" + str(valve_order[n]))
            steps.append("pump_a")
            print(steps)
            totvol = 0
            for sta, sto in zip(steps[:-1], steps[1:]):
                totvol += measured_volumes[(sta, sto)]
            measured_volumes[(steps[0], steps[-1])] = totvol

        print(measured_volumes)

    def execute_protocol_entry(self, i):
        """Execute a protocol entry.

        When not executing the next protocol entry but jumping to one out of
        order, the tubing-'stack' needs to be re-assembled.

        Parameters
        ----------
        i : int
            Index of the protocol entry to execute.

        Notes
        -----
        A protocol entry consists of a type and parameters, e.g.::

            type='inject', reservoirID, volume, speed, extractionfactor
            type='wait_image'
            type='wait_time', duration
        """
        delay = self.protocol[i].get("delay", 0)
        extractionfactor = self.protocol[i].get("extractionfactor")
        # Publish an estimated duration so the GUI can show a within-step
        # progress bar (cleared in `finally` so it never lingers past the
        # step, even on error/abort).
        est = self._estimate_entry_duration(self.protocol[i])
        if est:
            self._step_estimate = (
                time.time(),
                est,
                self.protocol[i].get("$type"),
            )
        try:
            if self.parameters["mode"] == "tubing_stack":
                if (self.last_protocol_entry != i - 1) or (i == 0):
                    self._assemble_tubing_stack(i)
                for reservoir_id, vol in self.tubing_stack[i]:
                    self._set_valves(reservoir_id)
                    self._inject(
                        vol, delay=delay, extractionfactor=extractionfactor
                    )
                self.last_protocol_entry = i
            elif self.parameters["mode"] == "tubing_flush":
                # # this way, we flush (1+flushfactor)
                # # and also through sample. But that shouldn't matter for now.
                # self._flush()
                # self.execute_single_protocol_entry(i)
                pass
            elif self.parameters["mode"] == "tubing_ignore":
                # this way, exactly the volumes given in the protocol are used
                self.execute_single_protocol_entry(i)
            else:
                raise NotImplementedError("Mode " + self.parameters["mode"])
        finally:
            self._step_estimate = None

    def _estimate_entry_duration(self, pentry):
        """Estimate the wall-clock duration of an inject/pump_out entry.

        The motion time is dominated by moving ``volume`` µl at ``velocity``
        µl/min: an inject (and a pump-out) both pick the volume up and then
        dispense it, so ~``2 * volume / velocity`` minutes, plus the inject
        equilibration delays and per-stroke settle waits. This is an estimate
        (valve moves, serial round-trips and the underpressure pre-stroke are
        not modelled), so the GUI caps the bar at 99 % until the step actually
        finishes. Returns seconds, or ``None`` for steps without a meaningful
        time estimate (signals, flushes, valve-only moves).

        Parameters
        ----------
        pentry : dict
            A protocol entry (needs ``$type`` and ``volume``).

        Returns
        -------
        float or None
            Estimated duration in seconds, or ``None``.
        """
        type_ = pentry.get("$type")
        if type_ not in ("inject", "pump_out"):
            return None
        vol = pentry.get("volume")
        velocity = pentry.get("velocity") or self.parameters.get(
            "max_velocity"
        )
        if not vol or not velocity:
            return None
        seconds = 120.0 * float(vol) / float(velocity)  # pickup + dispense
        if type_ == "inject":
            seconds += self.parameters.get("inject_in_to_out_delay", 0) or 0
            seconds += self.parameters.get("inject_out_to_in_delay", 0) or 0
            seconds += 2 * (pentry.get("delay", 0) or 0)
        return seconds if seconds > 0 else None

    def get_step_progress(self):
        """Within-step progress of a running inject/pump_out.

        Returns
        -------
        tuple or None
            ``(elapsed, estimate, label)`` in seconds while an inject/pump_out
            runs (elapsed capped at the estimate), else ``None``. Computed
            from the timestamp/estimate published by
            :meth:`execute_protocol_entry`, so it is safe to call from the GUI
            thread (no serial I/O, unlike polling the pump position).
        """
        est = self._step_estimate
        if est is None:
            return None
        start, total, label = est
        elapsed = min(time.time() - start, total)
        return (elapsed, total, label)

    # @run_in_thread
    def execute_single_protocol_entry(self, i):
        """Execute only one single entry of the protocol; do not fill the
        tubing with the (potentially precious) later protocol entry fluids,
        but with buffer.
        """
        pentry = self.protocol[i]
        if pentry.get("velocity"):
            velocity = pentry["velocity"]
        else:
            velocity = self.parameters["max_velocity"]
        if pentry["$type"] == "inject":
            # flush_volume = self._calc_vol_to_inlet(pentry['reservoir_id'])
            injection_volume = pentry["volume"]
            delay = pentry.get("delay", 0)
            extractionfactor = pentry.get("extractionfactor")
            # first, set up the volume required
            self._set_valves(pentry["reservoir_id"])
            self._inject(
                injection_volume,
                velocity,
                delay=delay,
                extractionfactor=extractionfactor,
            )
            # afterwards, flush in buffer to get the pentry
            # volume to the sample
            # self._set_valves(self.special_names['flushbuffer_a'])
            # self._inject(flush_volume, velocity)
        if pentry["$type"] == "pump_out":
            self._pump_out(
                pentry["volume"],
                extractionfactor=pentry.get("extractionfactor"),
            )

        # tubing full of buffer, cannot simply proceed
        self.last_protocol_entry = -1

    def deliver_fluid(self, reservoir_id, volume):
        """Deliver fluid to the sample without regard to the protocol."""
        flush_volume = self._calc_vol_to_inlet(reservoir_id)
        # first, set up the volume required
        self._set_valves(reservoir_id)
        self._inject(volume)
        # afterwards, flush in buffer to get the pentry volume to the sample
        self._set_valves(self.special_names["flushbuffer_a"])
        self._inject(flush_volume)

    def clean_tubings(
        self,
        cleaning_reservoirs=None,
        extra_vol=100,
        velocity=None,
        delay=None,
        mode="chain",
        max_reservoir_vol=None,
        confirm=True,
    ):
        """Clean all tubings by pumping cleaning liquids through paths.

        Parameters
        ----------
        cleaning_reservoirs : list of int
            Reservoir IDs connected to cleaning liquid tanks, in the order
            they should be used for cleaning.
        confirm : bool, default: True
            Block on a terminal ``input()`` confirmation before starting.
            Pass ``False`` from a GUI (which must confirm via its own dialog),
            so cleaning never blocks on / crashes for missing stdin.
        extra_vol : int, default: 100
            Volume in ul beyond the tubing volume to push through each path.
        velocity : int or None
            Pumping velocity in ul/min. If None, uses ``clean_velocity`` or
            ``max_velocity`` from parameters.
        delay : float or None
            Seconds to wait between pickup and dispense strokes. If None,
            uses ``clean_delay`` from parameters (default 0).
        mode : str
            ``'chain'`` (default) pumps cleaning liquid from tank into the
            first reservoir, then chains it through all reservoirs
            (res_0 -> res_1 -> ... -> res_N) before pushing out the needle;
            uses one fill volume per cleaning liquid. ``'individual'`` pumps
            fresh cleaning liquid from tank into each reservoir individually,
            pushes it out the needle, and extracts; more thorough but uses
            more liquid.
        max_reservoir_vol : int or None
            Maximum volume in ul that a reservoir may contain from a previous
            protocol. If set, each reservoir is emptied (up to this volume)
            through the needle before cleaning begins. If None, the
            pre-emptying step is skipped.

        Notes
        -----
        Prerequisites:

        - Input and output needles are placed in the same container so the
          output needle can suck up what the input needle dispenses.
        - Each cleaning reservoir is connected to a large tank of cleaning
          liquid.
        """
        if not velocity:
            velocity = self.parameters.get("clean_velocity")
        if not velocity:
            velocity = self.parameters["max_velocity"]
        if delay is None:
            delay = self.parameters.get("clean_delay", 0)
        logger.debug(f"Cleaning Reservoirs initial: {cleaning_reservoirs}")
        if cleaning_reservoirs is None:
            cleaning_reservoirs = self.cleaning_reservoirs
        logger.debug(
            f"Cleaning Reservoirs after none check: {cleaning_reservoirs}"
        )
        if not isinstance(cleaning_reservoirs, list):
            if isinstance(cleaning_reservoirs, dict):
                cleaning_reservoirs = list(cleaning_reservoirs.values())
            else:
                logger.debug(
                    f"Cleaning reservoirs is not a list or dict: "
                    f"{cleaning_reservoirs}. Setting empty list."
                )
                cleaning_reservoirs = []
            logger.debug(
                f"Cleaning Reservoirs was no list, now: {cleaning_reservoirs}"
            )
        else:
            # may be list of int (reservoir ids, used this way
            # here), or of special names
            cleaning_reservoirs_new = []
            for res in cleaning_reservoirs:
                if isinstance(res, int):
                    cleaning_reservoirs_new.append(res)
                elif isinstance(res, str):
                    res_id = self.special_names.get(res)
                    logger.debug(
                        f"Converting cleaning reservoir {res} via "
                        f"special names to {res_id}. All special "
                        f"names: {self.special_names}"
                    )
                    if res_id is None:
                        logger.debug(
                            f"Trying to set cleaning reservoir "
                            f"'{res}', but this is not defined in "
                            f"special names: {self.special_names}"
                        )
                    else:
                        cleaning_reservoirs_new.append(res_id)
                else:
                    logger.debug(
                        f"Reservoir '{res}' in cleaning "
                        f"reservoirs has type {type(res)}. str or "
                        f"int is needed. Ignoring."
                    )
            cleaning_reservoirs = cleaning_reservoirs_new
            logger.debug(
                f"Cleaning Reservoirs was a list, now: {cleaning_reservoirs}"
            )

        logger.debug(f"Cleaning Reservoirs: {cleaning_reservoirs}")
        logger.debug(f"reservoir_a: {self.reservoir_a.items()}")
        # Without a cleaning-liquid source the chain/individual passes have
        # nothing to pump, so the whole procedure would silently do nothing.
        # Surface that as an error (the GUI shows it in a dialog) rather than
        # printing "complete" with no movement. A pre-empty-only run
        # (max_reservoir_vol set) is still allowed.
        if not cleaning_reservoirs and max_reservoir_vol is None:
            raise ValueError(
                "No cleaning reservoirs available — nothing would be pumped. "
                "Set 'cleaning_reservoirs' in the experiment design's fluid "
                "settings to reservoir ids (e.g. [12]) or special names "
                "(e.g. ['h2o']) that are defined in special_names."
            )
        res_to_clean = [
            rid
            for rid in self.reservoir_a.keys()
            if rid not in cleaning_reservoirs
        ]

        logger.debug(
            f"Starting clean_tubings (mode={mode}). "
            f"Cleaning reservoirs: {cleaning_reservoirs}, "
            f"Reservoirs to clean: {res_to_clean}"
        )
        print("Starting tubing cleaning procedure.")
        print(
            f"Make sure no reservoirs have more than {max_reservoir_vol} ul"
            "volume left. Replace with empty reservoirs otherwise."
        )
        print(
            "Make sure input and output needles are in the same "
            "container (fluidly connected)."
        )
        if confirm:
            input("Press Enter to confirm and continue...")

        # Pre-empty: push leftover protocol liquid out of all reservoirs
        if max_reservoir_vol is not None:
            self._clean_tubings_pre_empty(
                res_to_clean, max_reservoir_vol, velocity, delay
            )

        if mode == "chain":
            self._clean_tubings_chain(
                cleaning_reservoirs, res_to_clean, extra_vol, velocity, delay
            )
        elif mode == "individual":
            self._clean_tubings_individual(
                cleaning_reservoirs, res_to_clean, extra_vol, velocity, delay
            )
        else:
            raise ValueError(
                f"Unknown clean_tubings mode: {mode}. "
                f'Use "chain" or "individual".'
            )

        print("Tubing cleaning procedure complete.")

    def _clean_tubings_pre_empty(
        self, res_to_clean, max_reservoir_vol, velocity, delay
    ):
        """Empty leftover protocol liquid from all reservoirs before
        cleaning. For each reservoir, pump up to max_reservoir_vol
        from the reservoir out through the input needle, then extract
        with the output needle.
        """
        logger.debug(f"Pre-emptying reservoirs (max {max_reservoir_vol} ul)")
        print(
            f"Emptying leftover liquid from reservoirs "
            f"(up to {max_reservoir_vol} ul each)"
        )

        for res_id in res_to_clean:
            vol = self._calc_vol_to_inlet(res_id) + max_reservoir_vol
            logger.debug(f"  Emptying reservoir {res_id} ({vol} ul)")

            # Push from reservoir out through the input needle
            self._pump(
                self.pump_a,
                vol,
                velocity=velocity,
                pickup_dir="in",
                dispense_dir="out",
                pickup_res=res_id,
                dispense_flushvalve=False,
                delay=delay,
            )

            # Extract with output needle into waste
            self.flush_pump_out(vol, only_forward=True, delay=delay)

    def _clean_tubings_chain(
        self, cleaning_reservoirs, res_to_clean, extra_vol, velocity, delay
    ):
        """Chain mode: pump cleaning liquid from tank into the first
        reservoir, shuttle it through all reservoirs, then push out.
        Each transfer cleans both the source branch (on draw) and the
        destination branch (on push). Uses one fill volume per
        cleaning liquid.
        """
        fill_vol = (
            max(self._calc_vol_to_inlet(rid) for rid in res_to_clean)
            + extra_vol
        )
        logger.debug(f"Chain mode fill volume: {fill_vol}")

        for clean_res_id in cleaning_reservoirs:
            logger.debug(
                f"Cleaning with liquid from reservoir " f"{clean_res_id}"
            )
            print(f"Cleaning with liquid from reservoir {clean_res_id}")

            # 1. Pump cleaning liquid from tank into first reservoir
            first_res = res_to_clean[0]
            logger.debug(f"  Filling reservoir {first_res} from tank")
            self._pump(
                self.pump_a,
                fill_vol,
                velocity=velocity,
                pickup_dir="in",
                dispense_dir="in",
                pickup_res=clean_res_id,
                dispense_res=first_res,
                delay=delay,
            )

            # 2. Chain through all reservoirs: res[i] -> res[i+1]
            for i in range(len(res_to_clean) - 1):
                src = res_to_clean[i]
                dst = res_to_clean[i + 1]
                logger.debug(f"  Chaining reservoir {src} -> {dst}")
                self._pump(
                    self.pump_a,
                    fill_vol,
                    velocity=velocity,
                    pickup_dir="in",
                    dispense_dir="in",
                    pickup_res=src,
                    dispense_res=dst,
                    delay=delay,
                )

            # 3. Push from last reservoir out through the input needle
            last_res = res_to_clean[-1]
            logger.debug(f"  Pushing from reservoir {last_res} to needle")
            self._pump(
                self.pump_a,
                fill_vol,
                velocity=velocity,
                pickup_dir="in",
                dispense_dir="out",
                pickup_res=last_res,
                dispense_flushvalve=False,
                delay=delay,
            )

            # 4. Extract with output needle into waste
            self.flush_pump_out(fill_vol, only_forward=True, delay=delay)

    def _clean_tubings_individual(
        self, cleaning_reservoirs, res_to_clean, extra_vol, velocity, delay
    ):
        """Individual mode: for each reservoir, pump fresh cleaning
        liquid from tank, push it through that reservoir's full path
        out the needle, and extract. More thorough but uses more liquid.
        """
        for clean_res_id in cleaning_reservoirs:
            logger.debug(
                f"Cleaning with liquid from reservoir " f"{clean_res_id}"
            )
            print(f"Cleaning with liquid from reservoir {clean_res_id}")

            for res_id in res_to_clean:
                logger.debug(f"  Cleaning path of reservoir {res_id}")

                fill_vol = self._calc_vol_to_inlet(res_id) + extra_vol

                # 1. Pump cleaning liquid from tank into this reservoir
                self._pump(
                    self.pump_a,
                    fill_vol,
                    velocity=velocity,
                    pickup_dir="in",
                    dispense_dir="in",
                    pickup_res=clean_res_id,
                    dispense_res=res_id,
                    delay=delay,
                )

                # 2. Push from reservoir out through the input needle
                self._pump(
                    self.pump_a,
                    fill_vol,
                    velocity=velocity,
                    pickup_dir="in",
                    dispense_dir="out",
                    pickup_res=res_id,
                    dispense_flushvalve=False,
                    delay=delay,
                )

                # 3. Extract with output needle into waste
                self.flush_pump_out(fill_vol, only_forward=True, delay=delay)

    def flush_pump_out(
        self, vol=None, only_forward=False, velocity=None, delay=0
    ):
        if not velocity:
            velocity = self.parameters.get("clean_velocity")
        if not velocity:
            velocity = self.parameters["max_velocity"]
        if not vol:
            vol = int(self.pump_out.syringe_volume / 2)
        dispense_velocity = self.parameters.get("pumpout_dispense_velocity")
        if dispense_velocity is None:
            dispense_velocity = velocity

        for i in range(1):
            self._pump(
                self.pump_out,
                vol,
                velocity=int(velocity),
                pickup_dir="in",
                dispense_dir="out",
                delay=delay,
            )
        if not only_forward:
            for i in range(1):
                self._pump(
                    self.pump_out,
                    vol,
                    velocity=int(dispense_velocity),
                    pickup_dir="out",
                    dispense_dir="in",
                    delay=delay,
                )

    def fill_and_shake_tubings(self, input_res, do_shake=True):
        velocity = self.parameters.get("clean_velocity")
        if not velocity:
            velocity = self.parameters["max_velocity"]
        for ires, (resid, res) in enumerate(self.reservoir_a.items()):
            # fill reservoir tubings
            nstrokes = 2
            if ires == 0:
                nstrokes_fill = 3
            else:
                nstrokes_fill = 0
            if ires == len(self.reservoir_a.items()) - 1:
                nstrokes_empty = 4
            else:
                nstrokes_empty = 0
            for i in range(nstrokes + nstrokes_fill):
                self._pump(
                    self.pump_a,
                    self.pump_a.syringe_volume,
                    velocity=velocity,
                    pickup_dir="in",
                    dispense_dir="in",
                    pickup_res=input_res,
                    dispense_res=resid,
                )
            # shake
            if do_shake:
                for i in range(2):
                    self._pump(
                        self.pump_a,
                        self.pump_a.syringe_volume,
                        velocity=velocity,
                        pickup_dir="in",
                        dispense_dir="in",
                        pickup_res=resid,
                        dispense_res=resid,
                    )
            # put liquid back
            for i in range(nstrokes + nstrokes_empty):
                self._pump(
                    self.pump_a,
                    self.pump_a.syringe_volume,
                    velocity=velocity,
                    pickup_dir="in",
                    dispense_dir="in",
                    pickup_res=resid,
                    dispense_res=input_res,
                )
        # flush and sample tubing
        for do_flushvalve in [True, False]:
            # fill
            for i in range(2):
                self._pump(
                    self.pump_a,
                    self.pump_a.syringe_volume,
                    velocity=velocity,
                    pickup_dir="in",
                    dispense_dir="out",
                    pickup_res=input_res,
                    dispense_flushvalve=do_flushvalve,
                )
            # shake
            if do_shake:
                for i in range(2):
                    self._pump(
                        self.pump_a,
                        self.pump_a.syringe_volume,
                        velocity=velocity,
                        pickup_dir="out",
                        dispense_dir="out",
                        pickup_flushvalve=do_flushvalve,
                        dispense_flushvalve=do_flushvalve,
                    )
            # put back
            for i in range(2):
                self._pump(
                    self.pump_a,
                    self.pump_a.syringe_volume,
                    velocity=velocity,
                    pickup_dir="out",
                    dispense_dir="in",
                    dispense_res=input_res,
                    pickup_flushvalve=do_flushvalve,
                )
        # sample_out
        for i in range(2):
            self._pump(
                self.pump_out,
                self.pump_out.syringe_volume,
                velocity=velocity / 10,
                pickup_dir="in",
                dispense_dir="out",
            )
            self._pump(
                self.pump_out,
                self.pump_out.syringe_volume,
                velocity=velocity / 10,
                pickup_dir="out",
                dispense_dir="in",
            )

    def pause_execution(self, msg=""):
        """Pause the execution of a protocol step. Specifically,
        stop the syringes
        """
        # print("Fluid system stops the current move")
        if msg != "":
            print(f"Pausing execution: {msg}")
            logger.debug(f"Pausing execution: {msg}")
        else:
            logger.debug("pausing execution")
        logger.debug("setting pause protocol flag")
        self.pause_flag.set()
        logger.debug("setting hamilton wait flag")
        ham.communication.abort_wait_response_flag.set()
        self.stop_all_moves()

    def stop_all_moves(self):
        logger.debug("stopping all moves")
        # self.abort_hamilton_wait_response_flag.set()
        self.pump_a.stop_current_move()
        self.pump_out.stop_current_move()
        # time.sleep(1)

    def resume_execution(self):
        """Resume the execution of a paused protocol step.

        Specifically, move the syringes again.

        Returns
        -------
        bool
            Whether the step was successfully resumed.
        """
        # print("Fluid system resumes the current move")
        # self.pause_flag.clear()
        logger.debug("resuming spill sensor monitoring")
        if self.spill_sensor is not None:
            # self.spill_sensor.sensor_wet_flag.clear()
            if hasattr(self.spill_sensor, "_monitor_thread"):
                logger.debug(
                    "there is already a monitor thread in the spill sensor"
                )
                logger.debug(self.spill_sensor._monitor_thread)
                logger.debug(self.spill_sensor._monitor_thread.__dict__)
                self.spill_sensor.stop_monitoring()
                logger.debug("stopped 'old' monitoring Thread")
            logger.debug("starting new spill sensor monitoring")
            self.spill_sensor.monitor_sensor(fn_on_wet=self.pause_execution)
        logger.debug("resuming paused moves of the syringes.")
        self.pump_a.resume_current_move()
        self.pump_out.resume_current_move()
        self.pump_a.wait_until_done()
        self.pump_out.wait_until_done()
        # may have dropped out of wait until done because of
        # another pausing event!
        if ham.communication.abort_wait_response_flag.is_set():
            logger.debug(
                "did not finish previously paused moves because "
                "another pause occurred."
            )
            return False
        else:
            logger.debug("previously paused moves are now completed.")
            return True

    def abort_execution(self):
        """Abort the execution of a protocol step. Specifically,
        stop the syringes
        """
        logger.debug("setting abort flag")
        self.abort_flag.set()
        self.pump_a.stop_current_move()
        self.pump_out.stop_current_move()

    def _assemble_tubing_stack(self, i):
        """Assemble the 'column' of different fluids stacked into the tubing.

        In an efficient delivery, when delivering fluid of step ``i`` into the
        sample, the tubing already needs to be switched to fluids of later
        steps. Tubing stack is only used in flow parameter mode
        ``'tubing_stack'``, not in ``'tubing_flush'``.

        Parameters
        ----------
        i : int
            The protocol step to start with.

        Returns
        -------
        column : dict
            Keys are the protocol step; values are lists of injection tuples
            with ``(reservoir_a_id, volume)``.
        """
        column = {}
        nsteps = len(self.protocol[i:])
        reservoirs = np.zeros(nsteps + 1, dtype=np.int32)
        volumes = np.zeros(nsteps + 1, dtype=np.float64)

        for idx, pentry in enumerate(self.protocol[i:]):
            if pentry["$type"] == "inject":
                reservoirs[idx] = pentry["reservoir_id"]
                volumes[idx] = pentry["volume"]
            elif pentry["$type"] == "flush":
                # flush step is meant only for mode 'tubing_flush', this
                # _assemble_tubing_stack si meant for mode 'tubing_stack'
                # however, let's add this; will go through sample and not
                # through the flush valve, though.
                reservoirs[idx] = self.special_names["flushbuffer_a"]
                flushfactor = pentry.get("flushfactor", 1)
                if self.valve_flush:
                    tubing_vol = self.tubing_config.get_reservoir_to_pump(
                        "flushbuffer_a", "a"
                    ) + self.tubing_config.get("pump_a", "valve_flush")
                else:
                    tubing_vol = self.tubing_config.get_reservoir_to_pump(
                        "flushbuffer_a", "a"
                    )
                volumes[idx] = flushfactor * tubing_vol
        reservoirs[-1] = self.special_names["flushbuffer_a"]
        volumes[-1] = self._calc_vol_to_inlet(reservoirs[-1])

        volumes_cum = np.cumsum(volumes)

        for idx, step in enumerate(range(i, i + nsteps)):
            injection_tuples = []
            if idx == 0:
                vol = volumes[idx] + self._calc_vol_to_inlet(reservoirs[idx])
            else:
                vol = (
                    volumes[idx]
                    + self._calc_vol_to_inlet(reservoirs[idx])
                    - self._calc_vol_to_inlet(reservoirs[idx - 1])
                )
            vol_rest = vol
            cum_start = np.argwhere(volumes_cum > 0).flatten()[0]
            try:
                cum_stop = np.argwhere(volumes_cum > vol).flatten()[0] + 1
            except IndexError:
                cum_stop = len(volumes_cum)
            for cum_idx in range(cum_start, cum_stop):
                vol_step = min([vol_rest, volumes_cum[cum_idx]])
                if vol_step == 0:
                    continue
                injection_tuples.append(tuple([reservoirs[cum_idx], vol_step]))
                volumes_cum -= vol_step
                vol_rest -= vol_step
            column[step] = injection_tuples
        self.tubing_stack = column
        logger.debug("generated tubing stack")
        logger.debug(str(self.tubing_stack))

    def _calc_vol_to_inlet(self, reservoir_id):
        """Calculate the tubing volume between reservoir and inlet needle.

        Computed from the tubing configuration. This is legacy-system
        specific.
        """
        vol_res_pump_a = self.tubing_config.get_reservoir_to_pump(
            reservoir_id, "a"
        )
        if self.valve_flush:
            vol_pump_a_valveflush = self.tubing_config.get(
                "pump_a", "valve_flush"
            )
            vol_valveflush_inlet = self.tubing_config.get(
                "valve_flush", "sample"
            )
            vol_pump_a_inlet = vol_pump_a_valveflush + vol_valveflush_inlet
        else:
            vol_pump_a_inlet = self.tubing_config.get("pump_a", "sample")
        return vol_res_pump_a + vol_pump_a_inlet

    def _set_valves(self, reservoir_id):
        """Set the valves to access the specified reservoir."""
        if self.reservoir_paths[reservoir_id] == "a":
            valve_positions = self.reservoir_a.get_reservoir_valve_positions(
                reservoir_id
            )
            self.set_valve_positions(valve_positions)
        else:
            raise NotImplementedError('Legacy system only has fluid path "a".')

    def set_valve_positions(self, valve_positions):
        """Drive the valves to an explicit ``{valve address: position}`` map.

        The routing step of :meth:`_set_valves`, split out so a caller that
        already knows the positions can use it — manual hardware control
        reaches *any* reservoir wired in the setup's manifold, not only the
        subset the loaded experiment design happens to name.

        Parameters
        ----------
        valve_positions : dict
            Maps a valve address (as used in a reservoir's ``valve_pos``) to
            the position for it. An ibidi multiplexer position may be a list
            of channels.

        Raises
        ------
        KeyError
            A valve address is not wired in this system.
        """
        for valve, pos in valve_positions.items():
            if valve not in self.valve_a:
                raise KeyError(
                    "valve {!r} is not wired in this system (have {})".format(
                        valve, sorted(self.valve_a, key=str)))
            self.valve_a[valve].set_valve(pos)

    def _flush(self, flushfactor=1):
        """Flush the tubing up to the flush valve with the flush buffer.

        This makes sure there are no residual molecules of one injection fluid
        in tubing or syringe for the next injection step.

        Parameters
        ----------
        flushfactor : float
            Fold of tubing volume to flush.
        """
        if isinstance(self.valve_flush, Valve):
            self._set_flush_valve(to_flush=True)
            self._set_valves(self.special_names["flushbuffer_a"])
            tubing_vol = self.tubing_config.get_reservoir_to_pump(
                "flushbuffer_a", "a"
            ) + self.tubing_config.get("pump_a", "valve_flush")
            self._pump(self.pump_a, tubing_vol * flushfactor)
        else:
            tubing_vol = self.tubing_config.get_reservoir_to_pump(
                "flushbuffer_a", "a"
            ) + self.tubing_config.get("pump_a", "valve_flush")
            self._pump(
                self.pump_a,
                tubing_vol * flushfactor,
                dispense_dir=self.flush_pos["flush"],
            )

    def _set_flush_valve(self, to_flush=True):
        """Set the flush valve position.

        Parameters
        ----------
        to_flush : bool
            If True, set to the flush position; if False, set to the sample
            position.
        """
        if to_flush:
            pos = self.flush_pos["flush"]
        else:
            pos = self.flush_pos["inject"]
        if isinstance(self.valve_flush, Valve):
            self.valve_flush.set_valve(pos)
        else:
            self.pump_a.set_valve(pos)

    def _pump(
        self,
        pump,
        vol,
        velocity=None,
        pickup_dir="in",
        dispense_dir="out",
        pickup_res=None,
        dispense_res=None,
        pickup_flushvalve=None,
        dispense_flushvalve=None,
        delay=0,
    ):
        """Pump fluid with a given pump.

        By default, pump from the currently set reservoir valve position to
        the pump outlet. Arguments can be set to pump e.g. from one reservoir
        to another. This routine is meant for flushing or for calibration;
        for experiment steps, use :meth:`_inject`.

        Parameters
        ----------
        pump : Pump
            The pump to use.
        vol : float
            The volume to pump.
        velocity : int
            The flow velocity of injection in µl/min.
        pickup_dir : str, default: 'in'
            One of ``['in', 'out']``. Where to pump from.
        dispense_dir : str, default: 'out'
            One of ``['in', 'out', range(nvalves)]``. Where to pump to.
        pickup_res, dispense_res : int or None
            The reservoir to pickup from / dispense to.
        pickup_flushvalve, dispense_flushvalve : int or None
            The flush valve position during pickup / dispense.
        delay : float
            The number of seconds to wait between pickup and dispense.
        """
        if velocity is None:
            velocity = self.parameters["max_velocity"]
        logger.debug(
            "Pump {:s} pumps {:f} ul at velocity {:s} "
            "from {:s}({:s}) to {:s}({:s})".format(
                pump.psd.asciiAddress,
                vol,
                str(velocity),
                str(pickup_res),
                str(pickup_dir),
                str(dispense_res),
                str(dispense_dir),
            )
            + ". Flushvalve settings pickup: {:s}, dispense: {:s}".format(
                str(pickup_flushvalve), str(dispense_flushvalve)
            )
        )
        curr_pump_vol = pump.get_current_volume()
        if curr_pump_vol > 0:
            pump.set_valve(dispense_dir)
            if dispense_res is not None:
                self._set_valves(dispense_res)
            if dispense_flushvalve is not None:
                self._set_flush_valve(dispense_flushvalve)
            pump.dispense(curr_pump_vol, velocity)
            pump.wait_until_done()

        volume_quant = pump.syringe_volume
        nr_pumpings = int(vol // volume_quant)
        if vol % volume_quant > 0:
            nr_pumpings += 1
        pump_volumes = volume_quant * np.ones(nr_pumpings)
        if vol % volume_quant > 0:
            pump_volumes[-1] = vol % volume_quant
        logger.debug("pump volumes: {:s}".format(str(pump_volumes)))

        for pump_volume in pump_volumes:
            pump.set_valve(pickup_dir)
            if pickup_res is not None:
                self._set_valves(pickup_res)
            if pickup_flushvalve is not None:
                self._set_flush_valve(pickup_flushvalve)
            pump.pickup(pump_volume, velocity, waitForPump=True)
            time.sleep(delay)
            pump.set_valve(dispense_dir)
            if dispense_res is not None:
                self._set_valves(dispense_res)
            if dispense_flushvalve is not None:
                self._set_flush_valve(dispense_flushvalve)
            pump.dispense(pump_volume, velocity, waitForPump=True)

    def _inject(self, vol, velocity=None, extractionfactor=None, delay=0):
        """Inject volume from the selected reservoir into the sample.

        The injection runs at a given flow velocity. Simultaneously, the
        extraction pump extracts the same volume.

        Parameters
        ----------
        vol : float
            The volume to inject in µl.
        velocity : float
            The flow velocity of the injection in µl/min.
        extractionfactor : float
            The factor of flow speeds of extraction vs injection. A
            non-perfectly calibrated system may result in less volume being
            extracted than injected, leading to spillage. To prevent this,
            the extraction needle could be positioned higher, and extraction
            performed faster than injection.
        delay : int
            The time to wait between pickup and dispense.
        """
        logger.debug("injecting {:.1f}".format(vol))
        if velocity is None:
            velocity = self.parameters["max_velocity"]
        if extractionfactor is None:
            extractionfactor = self.parameters["extractionfactor"]
        pumpout_dispense_velocity = self.parameters[
            "pumpout_dispense_velocity"
        ]

        pumpout_extravol = self.parameters["inject_pickup_extravol"]
        delay_in_to_out = (
            self.parameters["inject_in_to_out_delay"] / 60
        )  # in minutes
        delay_out_to_in = (
            self.parameters["inject_out_to_in_delay"] / 60
        )  # in minutes

        t_dispense = vol / velocity
        t_pickup = delay_in_to_out + t_dispense + delay_out_to_in
        vol_pickup = extractionfactor * vol
        velocity_out = int(vol_pickup / t_pickup)

        # to generate underpressure, pump out with the complete syringe volume
        if extractionfactor > 0 and self.parameters.get(
            "inject_precreate_underpressure"
        ):
            self.pump_out.wait_until_done()
            self.pump_out.set_valve("in")
            self.pump_out.wait_until_done()
            self.pump_out.pickup(
                self.pump_out.syringe_volume,
                pumpout_dispense_velocity,
                waitForPump=True,
            )
            self.pump_out.wait_until_done()
            self.pump_out.set_valve("out")
            self.pump_out.wait_until_done()
            self.pump_out.dispense(
                self.pump_out.syringe_volume,
                pumpout_dispense_velocity,
                waitForPump=False,
            )

        # assuming valve of pump a is set to pickup pos initially
        pickup_pos = self.pump_a.valve_pos
        if pickup_pos is None:
            pickup_pos = self.pump_a.input_pos

        if isinstance(self.valve_flush, Valve):
            self._set_flush_valve(to_flush=False)
        curr_pumpa_vol = self.pump_a.get_current_volume()
        curr_pumpout_vol = curr_pumpa_vol * extractionfactor
        curr_pumpout_vol += pumpout_extravol
        if curr_pumpa_vol > 0:
            # t_pickup (hence velocity_out) is only meaningful when there is
            # syringe content to dispense. Computing it unconditionally caused
            # a ZeroDivisionError when the pump started empty and the
            # equilibration delays were zero (t_dispense = 0 -> t_pickup = 0).
            t_dispense = curr_pumpa_vol / velocity
            t_pickup = delay_in_to_out + t_dispense + delay_out_to_in
            velocity_out = int(curr_pumpout_vol / t_pickup)
            self.pump_a.set_valve("out")
            self.pump_out.wait_until_done()
            self.pump_out.set_valve("in")
            if curr_pumpout_vol > 0:
                self.pump_out.wait_until_done()
                self.pump_out.pickup(curr_pumpout_vol, velocity_out)
                time.sleep(delay_in_to_out * 60)
            self.pump_a.dispense(curr_pumpa_vol, velocity)
            self.pump_out.wait_until_done()
            self.pump_a.wait_until_done()

        if extractionfactor == 0:
            out_vol = self.pump_out.syringe_volume
        elif extractionfactor > 0:
            out_vol = self.pump_out.syringe_volume / extractionfactor
        else:
            raise NotImplementedError(
                "Here, extractionfactor should be positive numeric. "
                + f"Its value is {str(extractionfactor)}."
            )
        volume_quant = min([self.pump_a.syringe_volume, out_vol])
        nr_pumpings = int(vol // volume_quant)
        if vol % volume_quant > 0:
            nr_pumpings += 1
        pump_volumes = volume_quant * np.ones(nr_pumpings)
        if vol % volume_quant > 0:
            pump_volumes[-1] = vol % volume_quant
        logger.debug("pump volumes: {:s}".format(str(pump_volumes)))

        self.pump_a.wait_until_done()
        self.pump_out.wait_until_done()
        for pump_volume in pump_volumes:
            self.pump_a.set_valve(pickup_pos)
            self.pump_out.set_valve("out")
            self.pump_a.pickup(pump_volume, velocity, waitForPump=False)
            if curr_pumpout_vol > 0:
                self.pump_out.dispense(
                    curr_pumpout_vol,
                    pumpout_dispense_velocity,
                    waitForPump=False,
                )
            self.pump_out.wait_until_done()
            self.pump_a.wait_until_done()
            # wait to let the pressure equilibrate and the fluid to settle
            time.sleep(delay)

            self.pump_a.set_valve("out")
            self.pump_out.set_valve("in")
            curr_pumpout_vol = pump_volume * extractionfactor
            curr_pumpout_vol += pumpout_extravol
            t_dispense = pump_volume / velocity
            t_pickup = delay_in_to_out + t_dispense + delay_out_to_in
            velocity_out = int(curr_pumpout_vol / t_pickup)
            if curr_pumpout_vol > 0:
                self.pump_out.pickup(
                    curr_pumpout_vol, velocity_out, waitForPump=False
                )
                time.sleep(delay_in_to_out * 60)
            self.pump_a.dispense(pump_volume, velocity, waitForPump=False)
            self.pump_out.wait_until_done()
            self.pump_a.wait_until_done()
            # wait to let the pressure equilibrate and the fluid to settle
            time.sleep(delay)

        self.pump_out.set_valve("out")
        if curr_pumpout_vol > 0:
            self.pump_out.dispense(
                curr_pumpout_vol, pumpout_dispense_velocity, waitForPump=False
            )
            # self.pump_out.wait_until_done()

    def _pump_out(self, vol, velocity=None, extractionfactor=None):
        """Make a pump out extraction stroke.
        This has been introduced to introduce suction before adding liquid
        from the sample tube. With small-diameter tubing, it may tke too
        long until suction occurs without this.
        """
        logger.debug("pumping out {:.1f}".format(vol))
        if velocity is None:
            velocity = self.parameters["max_velocity"]
        if extractionfactor is None:
            extractionfactor = self.parameters["extractionfactor"]
        velocity_out = int(extractionfactor * velocity)
        pumpout_dispense_velocity = self.parameters[
            "pumpout_dispense_velocity"
        ]

        curr_pumpout_vol = min(
            [vol * extractionfactor, self.pump_out.syringe_volume]
        )
        self.pump_out.set_valve("in")
        self.pump_out.pickup(curr_pumpout_vol, velocity_out, waitForPump=True)
        self.pump_out.wait_until_done()
        self.pump_out.set_valve("out")
        self.pump_out.dispense(
            curr_pumpout_vol, pumpout_dispense_velocity, waitForPump=True
        )
        self.pump_out.wait_until_done()

    def fill_tubings(
        self,
        extra_vol=0,
        dest="flushwaste",
        res_exceptions=[],
        post_fill_flushbuffer=True,
        velocity=None,
        include_flushwaste=False,
        delay=0,
    ):
        """Fill the tubings with the liquids of their reservoirs.

        Parameters
        ----------
        extra_vol : int
            The extra volume to pull from each tubing.
        dest : {'flushwaste', 'sample'}
            Where to pump to.
        res_exceptions : list of int
            Reservoirs not to flush.
        post_fill_flushbuffer : bool
            Whether to fill the central tubing with flushbuffer.
        include_flushwaste : bool
            Whether to also fill tubings of the flush waste (makes sense for
            cleaning, to empty the reservoir).
        delay : int
            Seconds to wait between pickup and dispense.

        Returns
        -------
        total_vol : float
            The total volume moved.
        """
        total_vol = 0
        clean_liquids = ["rbs", "ipa", "h2o", "empty"]
        clid = [
            self.special_names[cl]
            for cl in clean_liquids
            if cl.lower() in self.special_names.keys()
        ]
        res_exceptions = res_exceptions + clid
        logger.debug(
            "Filling tubings from respective reservoirs to " + str(dest)
        )
        logger.debug("Except reservoirs: " + str(res_exceptions))
        logger.debug(
            "Filling everything with flushbuffer in the end? "
            + str(post_fill_flushbuffer)
        )

        dispense_flushvalve = dest != "sample"
        for res_id, res in self.reservoir_a.items():
            # take care of the flushbuffer last
            if res_id in res_exceptions:
                continue
            # vol = self.tubing_config.get_reservoir_to_pump(res_id, 'a')
            vol = self.tubing_config.get_reservoir_to_closest_valve(res_id)
            vol += extra_vol
            total_vol += vol
            self._pump(
                self.pump_a,
                vol,
                velocity=velocity,
                pickup_res=res_id,
                dispense_flushvalve=dispense_flushvalve,
                delay=delay,
            )
        if include_flushwaste:
            vol = self.tubing_config.get("pump_a", "valve_flush")
            vol += self.tubing_config.get("valve_flush", "flush_waste")
            vol += extra_vol
            total_vol += vol
            self._pump(
                self.pump_a,
                vol,
                velocity=velocity,
                pickup_dir="out",
                pickup_flushvalve=True,
                dispense_flushvalve=dispense_flushvalve,
                delay=delay,
            )

        if post_fill_flushbuffer:
            # now, flush everything with the flushbuffer
            vol = self.tubing_config.get_reservoir_to_pump(
                "flushbuffer_a", "a"
            )
            if isinstance(self.valve_flush, Valve):
                vol += self.tubing_config.get(
                    "pump_a", "valve_flush"
                ) + self.tubing_config.get("valve_flush", "sample")
            else:
                vol += self.tubing_config.get("pump_a", "sample")
            vol += extra_vol
            total_vol += vol
            self._pump(
                self.pump_a,
                vol,
                velocity=velocity,
                pickup_res=self.special_names["flushbuffer_a"],
                dispense_flushvalve=False,
                delay=delay,
            )
        return total_vol

    def fill_tubings_reverse(
        self,
        extra_vol,
        src_res_id=None,
        res_exceptions=[],
        velocity=None,
        delay=0,
    ):
        """Fill all reservoir tubings and the sample tubing in reverse.

        Fills the tubings of all reservoirs in the config, as well as the
        sample tubing, with the liquid of the given reservoir. This is
        typically meant for cleaning.

        Parameters
        ----------
        extra_vol : int
            The extra volume to add to each tubing.
        src_res_id : int
            The reservoir id to get liquid from.
        res_exceptions : list of int
            Reservoirs not to flush.
        delay : int
            Seconds to wait between pickup and dispense.

        Returns
        -------
        total_vol : float
            The total volume moved.
        """
        total_vol = 0
        if not velocity:
            velocity = self.parameters.get("clean_velocity")
        if src_res_id is None:
            src_res_id = self.special_names["flushbuffer_a"]
        if not velocity:
            velocity = self.parameters["max_velocity"]

        logger.debug(
            "Filling tubings backwards, from reservoir " + str(src_res_id)
        )
        logger.debug("Except reservoirs: " + str(res_exceptions))

        # fill flushbuffer-reservoir to flush outlet
        vol = self.tubing_config.get_reservoir_to_pump(src_res_id, "a")
        if isinstance(self.valve_flush, Valve):
            vol += self.tubing_config.get(
                "pump_a", "valve_flush"
            ) + self.tubing_config.get("valve_flush", "flush_waste")
        else:
            vol += self.tubing_config.get("pump_a", "flush_waste")
        vol += extra_vol
        total_vol += vol
        self._pump(
            self.pump_a,
            vol,
            velocity=velocity,
            pickup_res=src_res_id,
            dispense_flushvalve=True,
            delay=delay,
        )
        # fill sample tubing
        if isinstance(self.valve_flush, Valve):
            vol = self.tubing_config.get("valve_flush", "sample")
        else:
            vol = self.tubing_config.get("pump_a", "sample")
        vol += extra_vol
        total_vol += vol
        self._pump(
            self.pump_a,
            vol,
            velocity=velocity,
            pickup_res=src_res_id,
            dispense_flushvalve=False,
            delay=delay,
        )
        # fill reservoirs
        for res_id, res in self.reservoir_a.items():
            # take care of the flushbuffer last
            if res_id in res_exceptions:
                continue
            # vol = self.tubing_config.get_reservoir_to_pump(res_id, 'a')
            vol = self.tubing_config.get_reservoir_to_closest_valve(res_id)
            vol += extra_vol
            total_vol += vol
            self._pump(
                self.pump_a,
                vol,
                velocity=velocity,
                pickup_res=src_res_id,
                pickup_dir="in",
                dispense_dir="in",
                dispense_res=res_id,
                delay=delay,
            )
        return total_vol

    def empty_tubings_to_flushbuffer(self, extra_vol):
        """Fill the tubings with the liquid of flushbuffer.

        Parameters
        ----------
        extra_vol : int
            The extra volume to add to each tubing.
        """
        velocity = self.parameters.get("clean_velocity")
        if not velocity:
            velocity = self.parameters["max_velocity"]
        # empty reservoirs
        for res_id, res in self.reservoir_a.items():
            # take care of the flushbuffer last
            if res_id == self.special_names["flushbuffer_a"]:
                continue
            # vol = self.tubing_config.get_reservoir_to_pump(res_id, 'a')
            vol = self.tubing_config.get_reservoir_to_closest_valve(res_id)
            vol += extra_vol
            self._pump(
                self.pump_a,
                vol,
                velocity=velocity,
                pickup_dir="in",
                dispense_dir="in",
                dispense_res=self.special_names["flushbuffer_a"],
                pickup_res=res_id,
            )
        # empty sample tubing
        vol = self.tubing_config.get("valve_flush", "sample")
        vol += extra_vol
        self._pump(
            self.pump_a,
            vol,
            velocity=velocity,
            pickup_dir="out",
            dispense_dir="in",
            dispense_res=self.special_names["flushbuffer_a"],
            pickup_flushvalve=False,
        )
        # fill flushbuffer-reservoir to flush outlet
        vol = (
            self.tubing_config.get_reservoir_to_pump("flushbuffer_a", "a")
            + self.tubing_config.get("pump_a", "valve_flush")
            + self.tubing_config.get("valve_flush", "flush_waste")
        )
        vol += extra_vol
        self._pump(
            self.pump_a,
            vol,
            velocity=velocity,
            pickup_dir="out",
            dispense_dir="in",
            dispense_res=self.special_names["flushbuffer_a"],
            pickup_flushvalve=True,
        )


"""
Diluter system architecture:
* a1) N valves for N*6+1 secondary probe reservoirs ('valve_a')
* a2) 1 syringe pump for reservoirs ('pump_a')

* b) 1 syringe pump for 1 buffer ('pump_b')
* c1) 1 valve for selecting imagers ('valve_c')
* c2) 1 syringe pump for pumping imagers ('pump_b')
* bc) 1 passive connection combining a and b ('conflux_bc')

* abc) 1 valve switching between ab and c ('conflux_abc')

* sample

* 1 syringe pump ('pump_out')
* waste
"""
diluter_system_config = {
    "system_type": "diluter",
    "valve_a": [
        {"address": 0, "intrument_type": "MVP", "valve_type": "8-way"},
        {"address": 1, "intrument_type": "MVP", "valve_type": "8-way"},
    ],
    "valve_c": [
        {"address": 2, "intrument_type": "MVP", "valve_type": "8-way"}
    ],
    "reservoir_a": [
        {"id": 0, "valve_pos": {0: 1, 1: 1, 7: 1}},
        {"id": 1, "valve_pos": {0: 1, 1: 2, 7: 1}},
        {"id": 2, "valve_pos": {0: 3, 1: 1, 7: 1}},
    ],
    "reservoir_b": [{"id": 3, "valve_pos": {7: 2}}],
    "reservoir_c": [
        {"id": 4, "valve_pos": {2: 2, 7: 2}},
        {"id": 5, "valve_pos": {2: 3, 7: 2}},
    ],
    "pump_a": {
        "address": 3,
        "instrument_type": "PSD4",
        "valve_type": "Y",
        "syringe": "500µl",
    },
    "pump_b": {
        "address": 4,
        "instrument_type": "PSD4",
        "valve_type": "Y",
        "syringe": "500µl",
    },
    "pump_c": {
        "address": 5,
        "instrument_type": "PSD4",
        "valve_type": "Y",
        "syringe": "50µl",
    },
    "pump_out": {
        "address": 6,
        "instrument_type": "PSD4",
        "valve_type": "Y",
        "syringe": "500µl",
    },
    "conflux_bc": {"instrument_type": "passive"},
    "conflux_abc": {
        "address": 7,
        "instrument_type": "MVP",
        "valve_type": "4-way",
    },
}

diluter_tubing_config = {
    (0, "pump_a"): 153.2,
    (1, "pump_a"): 151.8,
    ("pump_a", "conflux_abc"): 30.7,
    (3, "pump_b"): 11.8,
    (4, "pump_c"): 15.8,
    (5, "pump_c"): 16.8,
    ("pump_b", "conflux_bc"): 12,
    ("pump_c", "conflux_bc"): 13,
    ("conflux_bc", "conflux_abc"): 31,
    ("conflux_abc", "sample"): 28,
}


class DiltuterArchitecture(LegacyArchitecture):
    """ """

    def __init__(self):
        pass

    def mix_injection(self):
        """Inject with two input syringes actuated simultaneously.

        Same as inject, but with the correct relative flow speed.
        This is more a matter of the experiment protocol specifying
        the pump(s) to use
        """
        pass


experiment_config = {
    "reservoir_names": {
        0: "Buffer B+",
        1: "Imager 50pM",
    }
}


if __name__ == "__main__":
    pass
    # unittest.main()

    # # do a test run
    # connect('18', 9600)
    # arch = LegacyArchitecture(legacy_system_config, legacy_tubing_config)
    # arch._assign_protocol(protocol)

    # input('press any key to start')
    # arch.perform_next_protocol_entry()
    # print('performing protocol entry {:d}: '
    #       .format(arch.curr_protocol_entry),
    #       protocol['protocol_entries'][arch.curr_protocol_entry])
    # input('press any key to perform next step')
    # arch.perform_next_protocol_entry()
    # print('performing protocol entry {:d}: '
    #       .format(arch.curr_protocol_entry),
    #       protocol['protocol_entries'][arch.curr_protocol_entry])
    # input('press any key to perform next step')
    # arch.perform_next_protocol_entry()
    # print('performing protocol entry {:d}: '
    #       .format(arch.curr_protocol_entry),
    #       protocol['protocol_entries'][arch.curr_protocol_entry])
    # input('press any key to perform next step')
    # arch.perform_next_protocol_entry()
    # print('performing protocol entry {:d}: '
    #       .format(arch.curr_protocol_entry),
    #       protocol['protocol_entries'][arch.curr_protocol_entry])
    # # input('press any key to perform next step')
    # # arch.perform_next_protocol_entry()
    # # print('performing protocol entry {:d}: '
    # #       .format(arch.curr_protocol_entry),
    # #       protocol['protocol_entries'][arch.curr_protocol_entry])
    # print('done')
    # disconnect()

    # patch_res = patch(__name__ + '.Reservoir', autospec=True)
    # patch_res.start()
