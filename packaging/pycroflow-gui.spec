# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the standalone Windows PycroFlow GUI
(``pycroflow-gui.exe``).

Build (on Windows, with the package + PyInstaller installed):

    pip install ".[gui,hardware,monitoring]" pyinstaller
    pyinstaller --clean --noconfirm packaging/pycroflow-gui.spec

Produces ``dist/pycroflow-gui.exe`` (onefile, windowed). The CI release
workflow (.github/workflows/release-windows.yml) runs exactly this on a ``v*``
tag and attaches the exe + installer to the GitHub Release.

Notes
-----
* ``collect_submodules('PycroFlow')`` is essential: subsystems resolve drivers
  dynamically (the pyHamilton serial stack, the back-compat import shims, the
  typed ``$type`` dispatch), which PyInstaller's static analysis cannot see.
* The embedded Monet tab needs monet bundled (``monet[hardware]`` is a declared
  ``[hardware]`` dep); its server/dashboard/schema modules are skipped exactly
  as monet's own spec does (they import the fastapi/pydantic server stack the
  GUI never loads).
* ``configs/`` (setup + tubing YAML), ``configs/setups/``, and ``examples/`` are
  bundled at the same relative path the package resolves them from
  (``Path(__file__).parent`` -> ``sys._MEIPASS`` when frozen).
* The vendor SDK *Python* packages (``[hardware]``) are bundled so a real rig
  can connect; the vendor DLLs/runtimes (Thorlabs Kinesis, NI-DAQmx,
  Micro-Manager) still have to be installed on the rig PC.
"""

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_submodules,
    copy_metadata,
)

# SPECPATH is injected by PyInstaller = the directory holding this spec, so the
# build works regardless of the invocation CWD.
import os

_launcher = os.path.join(SPECPATH, "pycroflow_gui.py")

# PycroFlow's own submodules (minus the test package — dead weight, and it
# imports unittest/mock + the fake-hardware shims).
hiddenimports = [
    m
    for m in collect_submodules("PycroFlow")
    if not (m == "PycroFlow.tests" or m.startswith("PycroFlow.tests."))
]

# The embedded Monet tab. Skip the server-only modules (same rationale as
# monet's own spec): the GUI never imports server/dashboard/schemas/
# serviceauth/migrate, and they import fastapi / pydantic / picasso_registry at
# module level, so collecting them would bundle dead, unimportable modules.
_MONET_SKIP = (
    "monet.tests",
    "monet.server",
    "monet.dashboard",
    "monet.schemas",
    "monet.serviceauth",
    "monet.migrate",
)
try:
    hiddenimports += [
        m
        for m in collect_submodules("monet")
        if not any(m == s or m.startswith(s + ".") for s in _MONET_SKIP)
    ]
except Exception:
    pass

# Package data: PycroFlow's YAML configs/setups/examples (resolved by
# Path(__file__).parent at runtime) and monet's bundled examples. matplotlib
# data is pulled because the [hardware] stack imports it.
datas = collect_data_files("PycroFlow")
for _pkg in ("monet", "matplotlib"):
    try:
        datas += collect_data_files(_pkg)
    except Exception:
        pass

# Hardware SDK packages (the [hardware] extra). The drivers lazy-import these by
# name *inside* connect methods, and several resolve submodules dynamically
# (msl-equipment's resource backends, pyvisa/pyvisa-py backends, nidaqmx), so
# PyInstaller's static analysis misses them and a real rig would fail at connect
# with ModuleNotFoundError. Pull each in explicitly; best-effort so a package
# absent from a given build just contributes nothing.
_HARDWARE_PKGS = (
    "msl",
    "pyvisa",
    "pyvisa_py",
    "nidaqmx",
    "pycobolt",
    "microscope",
    "pycromanager",
    "ndtiff",
    "ThorlabsPM100",
    "cv2",
    "serial",  # pyserial — base dep, imported dynamically by several drivers
)
for _pkg in _HARDWARE_PKGS:
    try:
        hiddenimports += collect_submodules(_pkg)
    except Exception:
        pass
    try:
        datas += collect_data_files(_pkg)
    except Exception:
        pass

# Some packages read their own (or a dependency's) distribution metadata at
# import via importlib.metadata and die with "No package metadata was found" if
# the .dist-info isn't bundled: nidaqmx -> nitypes calls version("nitypes");
# PycroFlow.__version__ falls back to importlib.metadata.version("PycroFlow")
# when the generated _version.py is absent; picasso-registry[client] ships the
# live-analysis client. copy_metadata(..., recursive=True) ships each package's
# metadata and that of its dependencies. Distribution names (not import names)
# are required; best-effort so a missing package is skipped.
_METADATA_DISTS = (
    "PycroFlow",
    "monet",
    "picasso-registry",
    "python-ulid",
    "nidaqmx",
    "nitypes",
    "PyVISA",
    "PyVISA-py",
    "pycromanager",
    "microscope",
    "ThorlabsPM100",
    "msl-equipment",
    "pycobolt",
)
for _dist in _METADATA_DISTS:
    try:
        datas += copy_metadata(_dist, recursive=True)
    except Exception:
        pass

a = Analysis(
    [_launcher],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Trim libs not part of this build: the fastapi/uvicorn server stack (pulled
    # transitively by monet/picasso-registry but never used by the GUI) and
    # tkinter (the Qt backend is used, not Tk). The PycroFlow/monet test
    # packages are already excluded from the submodule collection above.
    excludes=["tkinter", "fastapi", "uvicorn"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="pycroflow-gui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX is known to corrupt PyQt6's Qt6 DLLs on Windows (crash on launch), and
    # the size win isn't worth that risk for a lab tool.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # windowed GUI app — no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
