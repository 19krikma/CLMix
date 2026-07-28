# App version - bump this on every release that ships a fix or feature.
#
#   PATCH += 1   -> bug fix
#   MINOR += 1, PATCH = 0   -> new feature
#   MAJOR += 1, MINOR = 0, PATCH = 0   -> breaking change
#
# Read by ui/main_window.py (shown in the title bar and Setup window) and
# by packaging/windows/build.ps1 (stamped into the installer).
VERSION = "1.0.0"
