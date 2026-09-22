"""Registering CLMix with the desktop's own "start these at login" list.

One helper per platform, all behind is_enabled()/set_enabled(): Windows
gets a Run key value, Linux an XDG autostart entry, macOS a LaunchAgent.
Nothing here is written at import time - the state on disk is only ever
touched by set_enabled(), so an operator who has never touched the toggle
keeps whatever their desktop was already doing.

The command registered is however this copy is being run: the frozen
executable itself for a packaged build, or "python main.py" from source.
That is deliberately re-derived on every set_enabled() rather than stored,
so moving or reinstalling the app and re-ticking the box fixes a stale
entry.
"""

import os
import platform
import plistlib
import shlex
import sys
from pathlib import Path

from services.log_store import log

# The name the entry is filed under. Stable across versions - it is what
# a later set_enabled(False) looks up to remove.
ENTRY_NAME = "CLMix"
BUNDLE_ID = "com.clmix.app"

_WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


class AutostartError(Exception):
    """A registration that failed for a reason worth showing the operator."""


def is_supported():
    """Whether this platform has an implementation below."""
    return platform.system() in ("Windows", "Linux", "Darwin")


def launch_command():
    """The argv that starts this copy of CLMix, as a list.

    A PyInstaller build is its own executable; from source it takes the
    interpreter plus main.py, resolved from this file rather than from the
    working directory (login sessions start somewhere else entirely).
    """
    if getattr(sys, "frozen", False):
        return [sys.executable]

    main_py = Path(__file__).resolve().parent.parent / "main.py"
    return [sys.executable, str(main_py)]


def is_enabled():
    """Whether a login entry for CLMix currently exists.

    Never raises: a platform without an implementation, or a registry or
    home directory that cannot be read, simply reports off.
    """
    try:
        system = platform.system()

        if system == "Windows":
            return _windows_read() is not None

        if system == "Linux":
            return _linux_entry_path().exists()

        if system == "Darwin":
            return _macos_plist_path().exists()
    except OSError as ex:
        log("debug", f"Could not read autostart state: {ex!r}")

    return False


def set_enabled(enabled):
    """Adds or removes the login entry. Raises AutostartError on failure.

    Removal of an entry that is not there is a no-op, not an error - the
    toggle can be switched off safely whatever state the desktop is in.
    """
    system = platform.system()

    if not is_supported():
        raise AutostartError(f"Launch on startup is not supported on {system}")

    try:
        if system == "Windows":
            _windows_write(enabled)
        elif system == "Linux":
            _linux_write(enabled)
        else:
            _macos_write(enabled)
    except OSError as ex:
        log("error", f"Could not update autostart entry: {ex!r}")
        raise AutostartError(str(ex)) from ex

    log("info", f"Launch on startup {'enabled' if enabled else 'disabled'}")


# ---------------------------------------------------------------- Windows

def _windows_read():
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WINDOWS_RUN_KEY) as key:
            value, _type = winreg.QueryValueEx(key, ENTRY_NAME)
            return value
    except FileNotFoundError:
        return None


def _windows_write(enabled):
    import winreg

    # CreateKey rather than OpenKey: the Run key exists on every Windows
    # install, but creating it is free and opening a missing one is not
    # recoverable here.
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _WINDOWS_RUN_KEY) as key:
        if not enabled:
            try:
                winreg.DeleteValue(key, ENTRY_NAME)
            except FileNotFoundError:
                pass
            return

        # Each argument quoted separately: the install path contains a
        # space ("C:\Program Files\CLMix\...") and Windows would otherwise
        # read it as a command plus arguments.
        command = " ".join(f'"{part}"' for part in launch_command())
        winreg.SetValueEx(key, ENTRY_NAME, 0, winreg.REG_SZ, command)


# ------------------------------------------------------------------ Linux

def _linux_autostart_dir():
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "autostart"


def _linux_entry_path():
    return _linux_autostart_dir() / "clmix.desktop"


def _linux_write(enabled):
    path = _linux_entry_path()

    if not enabled:
        path.unlink(missing_ok=True)
        return

    path.parent.mkdir(parents=True, exist_ok=True)

    # StartupWMClass matches packaging/linux/clmix.desktop for the same
    # reason it is set there: without it the dock shows a second, iconless
    # entry for the window this launches.
    path.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.0\n"
        f"Name={ENTRY_NAME}\n"
        "Comment=Aux-send mixing for the DiGiCo Q225 Quantum\n"
        f"Exec={shlex.join(launch_command())}\n"
        "Icon=clmix\n"
        "Terminal=false\n"
        "StartupWMClass=Clmix\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


# ------------------------------------------------------------------ macOS

def _macos_plist_path():
    return Path.home() / "Library" / "LaunchAgents" / f"{BUNDLE_ID}.plist"


def _macos_write(enabled):
    path = _macos_plist_path()

    if not enabled:
        path.unlink(missing_ok=True)
        return

    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "wb") as f:
        plistlib.dump({
            "Label": BUNDLE_ID,
            "ProgramArguments": launch_command(),
            "RunAtLoad": True,
            # Login only. Without this launchd treats an app the operator
            # quits as a crash and starts it straight back up.
            "KeepAlive": False,
        }, f)
