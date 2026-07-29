"""Regression fixture: a basic Exchange-PAINT input config.

Extracted from example_experiment/start_experiment_240301.py. Used by
tests/test_regression_protocols.py to pin ProtocolBuilder.create_steps()
output — any change to that output is a wire-format change that needs
explicit review.

Keep this fixture frozen. Add new variants as separate fixture files.
"""

WASH_VOLUME = 2000
IMAGER_VOLUME = 950
VOLUME_REDUCTION_FOR_XCHG = 50
WASH_BUFFER = "PBS"

RESERVOIR_NAMES = {
    1: "EGFR",
    2: "5T4",
    3: "AXL",
    4: "Her2",
    5: "PDL1",
    6: WASH_BUFFER,
}

TARGET_SEQUENCE = ["EGFR", "5T4", "AXL", "Her2", "PDL1"]
INITIAL_TARGET = "Her3"

CONFIG = {
    "save_dir": ".",
    "base_name": "regression_exchange_basic",
    "fluid": {
        "parameters": {
            "start_velocity": 500,
            "max_velocity": 1000,
            "stop_velocity": 500,
            "pumpout_dispense_velocity": 20000,
            "clean_velocity": 3000,
            "clean_delay": 10,
            "mode": "tubing_ignore",
            "extractionfactor": 6,
            "inject_pickup_extravol": 1500,
            "inject_in_to_out_delay": 15,
            "inject_out_to_in_delay": 5,
            "inject_precreate_underpressure": False,
        },
        "settings": {
            "vol_wash_pre": int(0.1 * WASH_VOLUME),
            "vol_wash": int(0.9 * WASH_VOLUME),
            "vol_imager_pre": int(0.9 * IMAGER_VOLUME),
            "vol_imager_post": int(0.1 * IMAGER_VOLUME),
            "vol_remove_before_wash": VOLUME_REDUCTION_FOR_XCHG,
            "wait_after_pickup": 5,
            "reservoir_names": RESERVOIR_NAMES,
            "experiment": {
                "type": "Exchange",
                "wash_buffer": WASH_BUFFER,
                "imagers": TARGET_SEQUENCE,
                "initial_imager": INITIAL_TARGET,
            },
        },
    },
    "img": {
        "parameters": {
            "show_progress": True,
            "show_display": True,
            "close_display_after_acquisition": True,
        },
        "settings": {
            "frames": 15,
            "darkframes": 50,
            "t_exp": 75,
        },
    },
    "illu": {
        "parameters": {
            "setup": "Crick",
        },
        "settings": {
            "laser": 560,
            "power_acq": 30,
            "power_nonacq": 1,
            "warmup_delay": 5,
            "shutter_off_nonacq": True,
            "lasers_off_finally": True,
        },
    },
}
