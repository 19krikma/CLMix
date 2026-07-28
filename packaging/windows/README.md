# Windows packaging

Turns the app into a double-click installer (`DigicoMonitorMixSetup-<version>.exe`)
that installs to Program Files, adds a Start Menu entry, offers an optional
Desktop shortcut, and registers a normal uninstaller in
"Add or remove programs".

**This must be run on Windows.** PyInstaller bundles the Python interpreter
of whatever OS it's running on - it does not cross-compile, so a Windows
`.exe` can't be produced from Linux or macOS.

## One-time setup on the Windows build machine

1. Install Python 3.10+ from [python.org](https://www.python.org/downloads/)
   (not the Microsoft Store package - it's missing pieces Tkinter needs).
   Check "Add python.exe to PATH" during install.
2. Install [Inno Setup 6](https://jrsoftware.org/isdl.php) (default options
   are fine).
3. Clone/copy this repository onto the Windows machine.

## Build

From the repository root (or anywhere - the script finds its own location):

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
```

This will:
1. Read the version from `version.py`.
2. Create a build-only virtual environment (`.venv-build`) and install
   `requirements.txt` + `pyinstaller` into it.
3. Run PyInstaller against `digico_monitor_mix.spec`, producing
   `dist\DigicoMonitorMix\`.
4. Run Inno Setup against `installer.iss`, producing
   `packaging\windows\dist_installer\DigicoMonitorMixSetup-<version>.exe`.

Hand that one `.exe` to anyone - running it installs the app and offers to
create a desktop shortcut, no Python required on the target machine.

## Releasing a new version

Bump `VERSION` in `version.py` (see the comment there for the convention),
then re-run `build.ps1`. The installer filename and the app's own title
bar both pick up the new version automatically.

## Notes

- `.venv-build`, `build\`, `dist\`, and `packaging\windows\dist_installer\`
  are build output - safe to delete and regenerate, not meant to be
  committed.
- No app icon is wired up yet (`digico_monitor_mix.spec` uses PyInstaller's
  default). Drop an `.ico` file in this directory and pass `icon=` to the
  `EXE(...)` call in the spec if you want one.
- The installer isn't code-signed. Unsigned installers will trigger a
  Windows SmartScreen warning ("Windows protected your PC") until it builds
  enough reputation, or you buy a code-signing certificate.
