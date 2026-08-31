# Linux packaging

Turns the app into a `.deb` (`clmix_<version>_<arch>.deb`) that installs
CLMix to `/opt/clmix`, puts a `clmix` launcher on `PATH`, and registers a
desktop entry and icons so it appears in the applications menu with the
right icon in the dock and the app switcher.

Python and every dependency are bundled by PyInstaller, so the target
machine needs no Python, no `pip`, and no virtualenv.

**Build on the oldest distribution you intend to support.** PyInstaller
bundles the interpreter and libraries of whatever machine it runs on, and
glibc is only forward compatible - a package built on Ubuntu 24.04 installs
on 24.04 and later, not on 22.04.

## One-time setup on the build machine

```bash
sudo apt install python3 python3-venv python3-tk dpkg-dev fakeroot imagemagick
```

`imagemagick` is only used to scale the icon down to each size; without it
the build still succeeds, but every icon size gets the full-resolution PNG.

## Build

From anywhere - the script finds its own location:

```bash
packaging/linux/build.sh
```

This will:

1. Read the version from `version.py`.
2. Create a build-only virtualenv (`.venv-build`) and install
   `requirements.txt` + `pyinstaller` into it.
3. Run PyInstaller against `clmix.spec`, producing `dist/CLMix/`.
4. Stage the package tree, render the icons, and build
   `packaging/linux/dist_deb/clmix_<version>_<arch>.deb`.

## Install

```bash
sudo apt install ./packaging/linux/dist_deb/clmix_<version>_amd64.deb
sudo apt remove clmix          # to uninstall
```

`apt` is used rather than `dpkg -i` because it resolves the handful of X11
libraries listed in `Depends` if any are missing.

## What lands where

| Path | What |
|---|---|
| `/opt/clmix/` | The PyInstaller bundle |
| `/usr/bin/clmix` | Launcher on `PATH` |
| `/usr/share/applications/clmix.desktop` | Menu entry |
| `/usr/share/icons/hicolor/*/apps/clmix.png` | Icons, 16px to 256px |
| `/usr/share/doc/clmix/` | Privacy policy |

## The icon, and why `StartupWMClass` matters

A Linux desktop does not read the icon out of a running window. It takes the
window's `WM_CLASS` and looks for an installed `.desktop` file whose
`StartupWMClass` matches, then draws *that* file's `Icon=`. This is
especially true on Wayland, where an XWayland window's `_NET_WM_ICON` is
ignored outright.

So three things have to agree, or the app gets a blank placeholder:

- `ui/main_window.py` sets `tk.Tk(className=WM_CLASS_NAME)`. Without it the
  window announces itself as `Tk` and matches nothing.
- Tk normalizes that name: `"CLMix"` in the source arrives as the class
  **`Clmix`** on the wire. Check with `xprop WM_CLASS` and click the window.
- `clmix.desktop`'s `StartupWMClass=Clmix` has to match that exact spelling.

Running from a source checkout there is no `.desktop` file at all, so the
icon will still be a placeholder in the dock - that is expected, and not a
bug in the build.

## Notes

- `.venv-build/`, `build/`, `dist/` and `dist_deb/` are build output - safe
  to delete and regenerate, and all gitignored.
- `clmix.spec` is deliberately a near-copy of `packaging/windows/clmix.spec`
  rather than a shared file, because only the Windows one has an icon to
  stamp into the executable. Their `datas` lists have to stay in step: a
  data file added to one and forgotten in the other produces a build that
  only breaks at runtime, on the platform nobody tested.
- The in-app updater does not apply here. `services/updater.py` installs
  Windows `.exe` releases only, and `can_self_install()` returns False on
  Linux, so Help > About falls back to opening the release page.
- The package is not signed and is not in any apt repository, so `apt` will
  warn about installing a local file. Distributing it through a PPA or a
  signed repo would remove that.
