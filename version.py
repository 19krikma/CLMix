# App version - bump this on every release that ships a fix or feature.
#
#   PATCH += 1   -> bug fix
#   MINOR += 1, PATCH = 0   -> new feature
#   MAJOR += 1, MINOR = 0, PATCH = 0   -> breaking change
#
# Read by ui/about_window.py (shown in Help > About, and checked against
# the latest GitHub release) and by packaging/windows/build.ps1 (stamped
# into the installer). Deliberately the only place a release number is
# written down - docs/index.html links at the Releases page rather than
# naming a version, so it cannot fall out of step with this.
#
# This is the version of the desktop app *and* the remote server the phone
# apps talk to (services/remote_server.py), so it needs bumping for a
# protocol change just as much as for a visible UI one - it sat at 1.0.0
# through several server releases while android/version.properties moved
# four times. The two version numbers are independent (the phone apps ship
# on their own schedule), but a change spanning both should bump both.
VERSION = "1.2.0"
