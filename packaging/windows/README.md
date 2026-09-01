# Windows packaging

Turns the app into a double-click installer (`CLMixSetup-<version>.exe`)
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
3. Run PyInstaller against `clmix.spec`, producing
   `dist\CLMix\`.
4. Run Inno Setup against `installer.iss`, producing
   `packaging\windows\dist_installer\CLMixSetup-<version>.exe`.

Hand that one `.exe` to anyone - running it installs the app and offers to
create a desktop shortcut, no Python required on the target machine.

## Releasing a new version

1. Bump `VERSION` in `version.py` (see the comment there for the
   convention). The installer filename and the version shown in the app's
   About window both follow it automatically.
2. Re-run `build.ps1`. It produces two files in `dist_installer\`:
   `CLMixSetup-<version>.exe` and `CLMixSetup-<version>.exe.sha256`.
3. **Attach both of them to a GitHub release tagged `<version>`.** The
   script prints the `gh release create` / `gh release upload` command to
   copy.

Step 3 is not optional. Installed copies of CLMix find updates by asking
the releases API for those two asset names (`services/update_checker.py`),
download the installer and refuse to run it unless it matches the published
hash (`services/updater.py`). A release with no assets is invisible to
every installation out there, and one with the installer but no `.sha256`
falls back to opening the release page for a manual install.

Publish as a **draft or prerelease** to stage a build without offering it to
anyone: `/releases/latest` skips both, so nothing will see it until it is
promoted to a full release.

## Notes

- `.venv-build`, `build\`, `dist\`, and `packaging\windows\dist_installer\`
  are build output - safe to delete and regenerate, not meant to be
  committed.
- The installer isn't code-signed. Unsigned installers will trigger a
  Windows SmartScreen warning ("Windows protected your PC") until it builds
  enough reputation, or you buy a code-signing certificate. In-app updating
  means users meet that prompt more often, not less, since every update
  elevates a freshly downloaded installer.
- The app installs to Program Files, so the installer is admin-manifested
  and each in-app update shows a UAC prompt. An operator without admin
  rights on that machine can't self-update.
