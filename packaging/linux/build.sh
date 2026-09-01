#!/usr/bin/env bash
#
# Builds a .deb of the CLMix desktop app for Debian/Ubuntu/Pop!_OS.
#
# Run this ON LINUX, on the oldest distribution you intend to support:
# PyInstaller bundles the interpreter and libraries of whatever machine it
# runs on, and glibc is only forward compatible - a package built on 24.04
# installs on 24.04 and later, not on 22.04.
#
# Prerequisites:
#   - python3, python3-venv, python3-tk
#   - dpkg-deb and fakeroot (both in dpkg-dev / fakeroot)
#
# Usage (from anywhere):
#   packaging/linux/build.sh
#
# Produces packaging/linux/dist_deb/clmix_<version>_<arch>.deb

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

MAINTAINER="Mark Krikunov <markkrikunov2000@gmail.com>"

echo "==> Reading version from version.py"
VERSION="$(sed -n 's/^VERSION = "\(.*\)"$/\1/p' version.py)"
[ -n "$VERSION" ] || { echo "Could not find VERSION in version.py" >&2; exit 1; }
ARCH="$(dpkg --print-architecture)"
echo "    Version: $VERSION  Architecture: $ARCH"

for tool in dpkg-deb fakeroot; do
    command -v "$tool" >/dev/null || {
        echo "$tool not found. Install it with: sudo apt install dpkg-dev fakeroot" >&2
        exit 1
    }
done

echo "==> Setting up build virtual environment (.venv-build)"
[ -d .venv-build ] || python3 -m venv .venv-build
VENV_PY="$REPO_ROOT/.venv-build/bin/python"

"$VENV_PY" -m pip install --upgrade pip
"$VENV_PY" -m pip install -r requirements.txt pyinstaller

echo "==> Running PyInstaller"
"$VENV_PY" -m PyInstaller "packaging/linux/clmix.spec" \
    --distpath dist --workpath build --noconfirm

[ -x "dist/CLMix/CLMix" ] || { echo "PyInstaller did not produce dist/CLMix/CLMix" >&2; exit 1; }

echo "==> Staging the package tree"
STAGE="$SCRIPT_DIR/dist_deb/stage"
rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN" \
         "$STAGE/opt/clmix" \
         "$STAGE/usr/bin" \
         "$STAGE/usr/share/applications" \
         "$STAGE/usr/share/doc/clmix"

cp -a dist/CLMix/. "$STAGE/opt/clmix/"
cp "$SCRIPT_DIR/clmix.desktop" "$STAGE/usr/share/applications/clmix.desktop"

# The launcher on PATH. A wrapper rather than a symlink because
# PyInstaller's onedir bootloader resolves its bundle relative to the real
# executable, and a symlinked entry point is a well-known way to confuse it.
cat > "$STAGE/usr/bin/clmix" <<'LAUNCHER'
#!/bin/sh
exec /opt/clmix/CLMix "$@"
LAUNCHER
chmod 755 "$STAGE/usr/bin/clmix"

# Icons at every size a dock, switcher or launcher is likely to ask for.
# hicolor is the fallback theme every desktop searches, so one install
# covers GNOME, KDE, COSMIC and the rest.
echo "==> Rendering icons"
for size in 16 24 32 48 64 128 256; do
    dir="$STAGE/usr/share/icons/hicolor/${size}x${size}/apps"
    mkdir -p "$dir"
    if command -v convert >/dev/null; then
        convert images/clmix-logo-round.png -resize "${size}x${size}" "$dir/clmix.png"
    else
        cp images/clmix-logo-round.png "$dir/clmix.png"
    fi
done

cp legal/PRIVACY_POLICY.md "$STAGE/usr/share/doc/clmix/"

INSTALLED_KB="$(du -sk "$STAGE" | cut -f1)"

cat > "$STAGE/DEBIAN/control" <<CONTROL
Package: clmix
Version: $VERSION
Section: sound
Priority: optional
Architecture: $ARCH
Maintainer: $MAINTAINER
Installed-Size: $INSTALLED_KB
Depends: libc6, libx11-6, libxext6, libxft2, libfontconfig1
Description: Aux-send mixing for the DiGiCo Q225 Quantum
 CLMix hands each performer their own monitor mix on their own phone. This
 desktop app talks to the console over OSC and re-serves the parts each
 person is allowed to touch to the Android and iOS clients over your own
 local network.
 .
 Python and its dependencies are bundled, so nothing else is required.
CONTROL

# Both caches are indexes: without refreshing them the launcher can take
# minutes to notice a new app, or show it with no icon.
cat > "$STAGE/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
set -e
if [ -x /usr/bin/gtk-update-icon-cache ]; then
    gtk-update-icon-cache -qtf /usr/share/icons/hicolor 2>/dev/null || true
fi
if [ -x /usr/bin/update-desktop-database ]; then
    update-desktop-database -q /usr/share/applications 2>/dev/null || true
fi
exit 0
POSTINST

cat > "$STAGE/DEBIAN/postrm" <<'POSTRM'
#!/bin/sh
set -e
if [ -x /usr/bin/gtk-update-icon-cache ]; then
    gtk-update-icon-cache -qtf /usr/share/icons/hicolor 2>/dev/null || true
fi
if [ -x /usr/bin/update-desktop-database ]; then
    update-desktop-database -q /usr/share/applications 2>/dev/null || true
fi
exit 0
POSTRM

chmod 755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/postrm"

echo "==> Building the package"
OUTPUT="$SCRIPT_DIR/dist_deb/clmix_${VERSION}_${ARCH}.deb"
# fakeroot so everything inside the package is owned by root, as dpkg
# expects, without this script needing to be run as root itself.
fakeroot dpkg-deb --build --root-owner-group "$STAGE" "$OUTPUT" >/dev/null

rm -rf "$STAGE"

echo
echo "==> Done: $OUTPUT"
echo
echo "    Install with:    sudo apt install $OUTPUT"
echo "    Remove with:     sudo apt remove clmix"
