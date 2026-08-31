# PyInstaller spec for the CLMix desktop app on Linux.
#
# The Linux twin of packaging/windows/clmix.spec. The two are kept apart
# rather than shared because only the Windows one has an icon to stamp into
# the executable (a Linux binary carries none - the icon comes from the
# .desktop file instead), but their `datas` lists have to stay identical:
# a data file added for one platform and forgotten for the other produces a
# build that only breaks at runtime, on the platform nobody tested.
#
# Usage: pyinstaller packaging/linux/clmix.spec
# See build.sh in this directory for the full, ready-to-run build.

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

# The About window's feature graphic, resolved at runtime through
# ui/resources.py (which reads sys._MEIPASS in a frozen build and the repo
# root from source). Listed file by file rather than by folder so a build
# doesn't quietly balloon with the 10800x10800 master artwork also living
# in images/.
datas += [
    (os.path.join(PROJECT_ROOT, "images", name), "images")
    for name in ("clmix-feature-graphic.png", "clmix-feature-graphic-light.png")
]

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

# onedir, matching the Windows build: the .deb drops the whole folder into
# /opt/clmix and puts a launcher on PATH, so there is nothing to gain from
# a single self-extracting file that would only be slower to start.
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
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="CLMix",
)
