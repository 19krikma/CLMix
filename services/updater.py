"""Downloading a release installer and handing it to Windows to install.

Everything here blocks and none of it touches Tkinter, matching
services/update_checker.py - the UI runs these on a worker thread and
polls for the results, the same way it already runs the version check.

The install itself is done by the ordinary Inno Setup installer from the
release (packaging/windows/installer.iss), run silently over the top of
the current installation. There is no bespoke "replace these files"
routine here, deliberately: the installer already knows what CLMix
consists of, rolls back a failed install, and keeps the uninstall entry in
Add/Remove Programs correct.
"""

import ctypes
import hashlib
import platform
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from services.log_store import log
from services.update_checker import REQUEST_TIMEOUT_SECONDS, SSL_CONTEXT

# Where the installer is downloaded to. Cleared and recreated at the start
# of every download, and swept once at app startup - which is the only
# moment the previous update's installer is guaranteed not to still be
# running, since the app it was installing is the one doing the sweeping.
DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "clmix-update"

# The read timeout can't be the 8 seconds the API check uses: this is tens
# of megabytes over whatever network a venue happens to have. It is a
# per-read timeout rather than a budget for the whole download, so a slow
# connection is fine and only a genuinely stalled one gives up.
DOWNLOAD_READ_TIMEOUT_SECONDS = 60

CHUNK_BYTES = 256 * 1024

# Inno Setup's own switches:
#   /SILENT               progress window, no wizard pages or prompts
#   /CLOSEAPPLICATIONS    close CLMix (via Restart Manager) to free its files
#   /RESTARTAPPLICATIONS  start it again afterwards
#   /NORESTART            never reboot the machine, whatever it finds
#   /SUPPRESSMSGBOXES     no modal error boxes behind our back
#
# The app deliberately does *not* exit before running this. Restart Manager
# only relaunches processes it closed itself, so quitting first would
# install the update and leave the operator staring at nothing.
INSTALLER_ARGS = (
    "/SILENT /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS /NORESTART /SUPPRESSMSGBOXES"
)

# ShellExecuteW returns a value <= 32 to mean failure, and it is the same
# value whether the user clicked "No" on the UAC prompt or the file was
# missing. SE_ERR_ACCESSDENIED is the one worth naming - it is what a
# declined elevation looks like.
_SE_ERR_ACCESSDENIED = 5
_SW_SHOWNORMAL = 1


class UpdateError(Exception):
    """A download or verification step that failed for a reportable reason."""


def can_self_install():
    """Whether this installation is one the app can replace on its own.

    True only for the frozen Windows build, which is the only thing the
    release installer knows how to install over. Run from source - on any
    OS, including Windows - there is nothing for it to update, so the UI
    falls back to opening the release page.
    """
    return bool(getattr(sys, "frozen", False)) and platform.system() == "Windows"


def sweep_download_dir():
    """Removes any installer left behind by a previous update."""
    if not DOWNLOAD_DIR.exists():
        return

    try:
        shutil.rmtree(DOWNLOAD_DIR)
    except OSError as ex:
        # Not worth surfacing: a leftover file in the temp directory costs
        # nothing, and Windows clears it eventually anyway.
        log("debug", f"Could not clear update download directory: {ex!r}")


def download_installer(installer, on_progress=None, cancel=None):
    """Downloads a release installer, returning (path, sha256 hex digest).

    `installer` is check_for_update()'s "installer" dict. `on_progress` is
    called with (bytes_so_far, total_bytes) as it goes, and `cancel` is a
    threading.Event checked between chunks. Raises UpdateError.

    The hash is computed while the bytes stream past rather than by
    re-reading the file afterwards, so nothing can change on disk between
    hashing it and checking it.
    """
    url = installer["url"]
    expected_size = installer.get("size")

    sweep_download_dir()

    try:
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as ex:
        raise UpdateError(f"Could not create download folder: {ex}") from ex

    destination = DOWNLOAD_DIR / installer["name"]
    digest = hashlib.sha256()
    downloaded = 0

    log("info", f"Downloading update from {url}")

    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "CLMix-Updater"}
        )

        with urllib.request.urlopen(
            request, timeout=DOWNLOAD_READ_TIMEOUT_SECONDS, context=SSL_CONTEXT
        ) as response, open(destination, "wb") as out:

            total = expected_size or _content_length(response)

            while True:
                if cancel is not None and cancel.is_set():
                    raise UpdateError("Cancelled")

                chunk = response.read(CHUNK_BYTES)

                if not chunk:
                    break

                out.write(chunk)
                digest.update(chunk)
                downloaded += len(chunk)

                if on_progress is not None:
                    on_progress(downloaded, total)

    except UpdateError:
        _discard(destination)
        raise

    except (urllib.error.URLError, TimeoutError, OSError) as ex:
        _discard(destination)
        raise UpdateError(f"Download failed: {ex}") from ex

    log("info", f"Downloaded {downloaded} bytes to {destination}")
    return destination, digest.hexdigest()


def fetch_checksum(url):
    """Reads a .sha256 sidecar, returning the bare lower-case hex digest.

    The file is in the usual `<hash>  <filename>` shape that sha256sum and
    PowerShell's Get-FileHash both produce, so only the first field is
    taken.
    """
    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "CLMix-Updater"}
        )

        with urllib.request.urlopen(
            request, timeout=REQUEST_TIMEOUT_SECONDS, context=SSL_CONTEXT
        ) as response:
            text = response.read(4096).decode("utf-8", "replace")

    except (urllib.error.URLError, TimeoutError, OSError) as ex:
        raise UpdateError(f"Could not fetch checksum: {ex}") from ex

    fields = text.split()

    if not fields or len(fields[0]) != 64:
        raise UpdateError("Checksum file is not in the expected format")

    return fields[0].lower()


def verify_installer(path, digest, expected_digest, expected_size=None):
    """Refuses anything that isn't byte-for-byte the published installer.

    Raises UpdateError and deletes the file if it fails any check, so a
    rejected download can never be left lying around for something else to
    pick up and run.
    """
    try:
        actual_size = path.stat().st_size

        with path.open("rb") as handle:
            header = handle.read(2)
    except OSError as ex:
        raise UpdateError(f"Could not read the downloaded file: {ex}") from ex

    problem = None

    if expected_size is not None and actual_size != expected_size:
        problem = (f"size is {actual_size} bytes, expected {expected_size}")

    # Every Windows executable starts "MZ". A download that got as far as
    # an HTML error page or a truncated response fails here even if the
    # size happened to line up.
    elif header != b"MZ":
        problem = "file is not a Windows executable"

    elif digest.lower() != expected_digest.lower():
        problem = "checksum does not match the one published with the release"

    if problem is not None:
        _discard(path)
        log("error", f"Rejected downloaded update: {problem}")
        raise UpdateError(f"Update failed verification - {problem}")

    log("info", f"Update verified: {path.name} ({actual_size} bytes)")


def launch_installer(path):
    """Starts the installer elevated, leaving this app running.

    Has to go through ShellExecuteW: the installer is manifested
    requireAdministrator, and CreateProcess (what subprocess uses) refuses
    those outright with ERROR_ELEVATION_REQUIRED instead of showing a UAC
    prompt. Raises UpdateError, including when the prompt is declined.
    """
    log("info", f"Launching installer {path}")

    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", str(path), INSTALLER_ARGS, str(path.parent),
            _SW_SHOWNORMAL,
        )
    except (AttributeError, OSError) as ex:
        raise UpdateError(f"Could not start the installer: {ex}") from ex

    if result <= 32:
        if result == _SE_ERR_ACCESSDENIED:
            raise UpdateError("Update cancelled at the Windows prompt")

        raise UpdateError(f"Could not start the installer (code {result})")


def _content_length(response):
    try:
        return int(response.headers.get("Content-Length"))
    except (TypeError, ValueError):
        return None


def _discard(path):
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
