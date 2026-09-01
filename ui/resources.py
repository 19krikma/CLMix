"""Locating the repo's bundled data files at runtime.

Where those files live differs between the two ways this app runs: from
source they sit in the repo next to main.py, while a PyInstaller build
unpacks them into a temporary bundle directory it points sys._MEIPASS at.
Nothing outside this module should be assembling data-file paths by hand.

ui/app_icon.py deliberately sidesteps all of this by embedding its PNG as
base64 - the right trade for one 64x64 icon that has to be there before
any window exists. The About banner is a pair of 1024x500 graphics, too
big to want inlined twice, so it ships as real files instead - listed in
packaging/windows/clmix.spec's `datas` so the frozen build carries them.
"""

import sys
from pathlib import Path

# Source runs resolve against the repo root (this file lives in ui/).
_SOURCE_ROOT = Path(__file__).resolve().parent.parent


def resource_path(*parts):
    """Absolute path to a bundled data file, e.g. resource_path("images", "x.png")."""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base).joinpath(*parts) if base else _SOURCE_ROOT.joinpath(*parts)
