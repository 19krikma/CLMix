# PyInstaller spec for the CLMix desktop app.
#
# Must be run with a Windows Python (PyInstaller does not cross-compile -
# it bundles the interpreter of whatever OS it runs on). See build.ps1 in
# this directory for the full, ready-to-run build.
#
# Usage: pyinstaller packaging/windows/clmix.spec
# Paths below are resolved via SPECPATH (the spec file's own directory,
# injected automatically by PyInstaller) rather than the current working
# directory, so this works no matter where it's invoked from.

import os

from PyInstaller.utils.hooks import collect_data_files

SPEC_DIR = os.path.abspath(SPECPATH)  # SPECPATH is the spec's directory, not the file itself
PROJECT_ROOT = os.path.abspath(os.path.join(SPEC_DIR, "..", ".."))

# sv_ttk ships its Tcl theme assets (sv.tcl + theme/light.tcl,
# theme/dark.tcl) as package data, loaded at runtime by file path rather
# than imported - PyInstaller's default analysis only follows Python
# imports, so these have to be pulled in explicitly or the packaged app
# fails to find the theme at startup.
datas = collect_data_files("sv_ttk")

a = Analysis(
    [os.path.join(PROJECT_ROOT, "main.py")],
    pathex=[PROJECT_ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

# onedir (not onefile): produces dist/CLMix/ as a folder of loose files
# rather than a single self-extracting exe - starts faster (no per-launch
# extraction to a temp dir) and is what installer.iss expects to copy
# wholesale into Program Files.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CLMix",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(SPEC_DIR, "clmix.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="CLMix",
)
