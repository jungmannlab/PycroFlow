"""
packaging/pycroflow_gui.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

PyInstaller entry point for the standalone Windows PycroFlow GUI
(``pycroflow-gui.exe``).

A frozen onefile build unpacks the package into an ephemeral temp dir
(``sys._MEIPASS``) that is wiped on exit, so PycroFlow's per-machine state —
the ``.env`` (registry URL/token, monet paths, spill port) and the rotating
``pycroflow.log`` / ``hamilton.log`` — must live somewhere persistent and
writable. This launcher establishes that location (a per-user dir) and moves
the process there **before** the GUI starts, so:

* ``PycroFlow.setup_logging`` writes its CWD-relative log files into the
  per-user dir instead of the read-only unpack dir, and
* ``PycroFlow.envfile.load_env_file`` (which searches the CWD for ``.env``)
  finds the operator's ``.env`` there.

Then it delegates to the real entry point, :func:`PycroFlow.gui.app.main`, so
the frozen build and a ``pip``-installed ``pycroflow-gui`` run identical code.

``--selfcheck`` imports the GUI and every dynamically-loaded driver headlessly
(no QApplication) and exits non-zero on any missing module, so CI can verify
the frozen bundle is complete without a display or any instruments.
"""

import os
import sys

# Modules PycroFlow / its embedded monet tab load dynamically (by classpath or
# lazily inside connect methods), so PyInstaller's static analysis cannot see
# them. --selfcheck imports them to prove the bundle is complete.
_SELFCHECK_MODULES = (
    "PycroFlow",
    "PycroFlow.gui",
    "PycroFlow.gui.app",
    "PycroFlow.gui.main_window",
    "PycroFlow.services",
    "PycroFlow.orchestration.core",
    "PycroFlow.imaging",
    "PycroFlow.illumination",
    "PycroFlow.fluid.legacy",
    "PycroFlow.pyHamilton",
    "PycroFlow.ibidi_multiplexer",
    "PycroFlow.spill_sensor_arduino",
    "PycroFlow.monitoring.capture_service",
    "PycroFlow.live_analysis.service",
)

# SDKs that import cleanly with no instrument/DLL present — a failure here
# means the bundle is genuinely broken, so it fails the release. monet is the
# embedded Monet tab; pycromanager is the MM bridge; serial is pyserial.
_SELFCHECK_FATAL = ("monet", "pycromanager", "serial")

# SDKs that may touch a vendor runtime at import on some machines; a failure on
# the headless CI runner is reported but does NOT fail the build (the .spec
# still bundles them, so a real rig with the vendor runtime can import them).
_SELFCHECK_BEST_EFFORT = (
    "nidaqmx",
    "msl.equipment",
    "pyvisa",
    "pycobolt",
    "microscope",
    "ThorlabsPM100",
    "cv2",
)


def _user_dir():
    """The persistent per-user PycroFlow dir for ``.env`` + logs."""
    root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(root, "PycroFlow" if os.name == "nt" else ".pycroflow")


def _selfcheck():
    """Import the GUI + all dynamically-loaded drivers; print OK and exit(0).

    Headless (no QApplication) so CI can run
    ``pycroflow-gui.exe --selfcheck`` to verify the frozen bundle actually
    contains PycroFlow, the embedded monet tab, and the hardware drivers.
    """
    import importlib

    try:
        for name in _SELFCHECK_MODULES + _SELFCHECK_FATAL:
            importlib.import_module(name)
        import PycroFlow

        print("PycroFlow selfcheck OK — version", PycroFlow.__version__)
    except Exception as exc:
        # stderr + non-zero exit so a CI run shows which module is missing from
        # the frozen bundle and fails the release.
        print("PycroFlow selfcheck FAILED: {}".format(exc), file=sys.stderr)
        sys.exit(1)

    for name in _SELFCHECK_BEST_EFFORT:
        try:
            importlib.import_module(name)
        except Exception as exc:
            print(
                "PycroFlow selfcheck note: {} not import-verifiable here "
                "({})".format(name, exc),
                file=sys.stderr,
            )
    sys.exit(0)


def main():
    home = _user_dir()
    os.makedirs(home, exist_ok=True)
    # Resolve .env (setup_core.env_path), user setups (configs.user_setups_dir)
    # and the CWD-relative logs to this persistent dir — NOT the wiped onefile
    # unpack dir that PycroFlow.__file__ points at when frozen.
    os.environ.setdefault("PYCROFLOW_HOME", home)
    # A windowed (console=False) frozen exe double-clicked with no console has
    # sys.stdout/stderr == None, so logging and print() would crash on the None
    # stream. Point them at a file in home before anything imports/logs.
    for _name in ("stdout", "stderr"):
        if getattr(sys, _name, None) is None:
            try:
                setattr(
                    sys,
                    _name,
                    open(
                        os.path.join(home, "pycroflow-{}.log".format(_name)),
                        "a",
                        buffering=1,
                        encoding="utf-8",
                    ),
                )
            except OSError:
                setattr(sys, _name, open(os.devnull, "w"))
    try:
        os.chdir(home)
    except OSError:
        pass

    if "--selfcheck" in sys.argv:
        _selfcheck()

    from PycroFlow.gui.app import main as gui_main

    sys.exit(gui_main())


if __name__ == "__main__":
    main()
