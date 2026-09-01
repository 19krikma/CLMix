import fnmatch
import json
import re
import ssl
import urllib.error
import urllib.request

import certifi

REPO = "19krikma/CLMix"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
REQUEST_TIMEOUT_SECONDS = 8

# The contract between packaging/windows/build.ps1 (which produces and
# uploads these) and services/updater.py (which downloads them): a release
# carries exactly one asset named like the installer pattern, plus a
# sidecar of the same name with the checksum suffix appended. A release
# missing either is still perfectly installable by hand from its page - it
# just can't be installed by the app itself.
INSTALLER_PATTERN = "CLMixSetup-*.exe"
CHECKSUM_SUFFIX = ".sha256"

# Release assets are always served from this prefix. Worth asserting
# because it is what stops a malformed (or tampered) API response from
# pointing the downloader at some other host entirely - the response
# itself arrived over the pinned TLS context below, so this is the check
# with something to say. Deliberately not applied to the *redirect* that
# URL leads to: GitHub moves asset storage between CDN hostnames, and
# pinning those would break the updater on their schedule, not ours.
DOWNLOAD_URL_PREFIX = f"https://github.com/{REPO}/releases/download/"

# Built explicitly from certifi's bundled CA file rather than relying on
# urllib's default (the OS trust store) - on some Windows Python installs,
# and especially in a PyInstaller-frozen build, that lookup fails with
# "CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate"
# even though the same code works fine on Linux/macOS. Shared with
# services/updater.py, which downloads over the same connections and needs
# the same trust store for the same reason.
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


def check_for_update(current_version):
    """Checks the GitHub repo's latest release against current_version.

    Returns a dict:
        {"available": bool, "latest_version": str|None, "url": str|None,
         "installer": {"name": str, "url": str, "size": int}|None,
         "checksum_url": str|None, "error": str|None}

    "installer" and "checksum_url" are what services/updater.py needs to
    install the update in place; either being None means this release can
    only be installed by hand from "url", its release page.

    "error" is set for network/API failures. A repo with no releases
    published yet is not treated as an error - available is just False.
    """
    try:
        request = urllib.request.Request(
            LATEST_RELEASE_URL,
            headers={
                "Accept": "application/vnd.github+json",
                # GitHub's API rejects requests with no User-Agent.
                "User-Agent": "CLMix-UpdateChecker",
            },
        )

        with urllib.request.urlopen(
            request, timeout=REQUEST_TIMEOUT_SECONDS, context=SSL_CONTEXT
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))

    except urllib.error.HTTPError as ex:
        # A repo with no releases at all answers 404 here, which is a
        # perfectly ordinary state and not worth reporting as a failure.
        return _result() if ex.code == 404 \
            else _result(error=f"GitHub API error ({ex.code})")

    except (urllib.error.URLError, TimeoutError, OSError) as ex:
        return _result(error=f"Network error: {ex}")

    except (json.JSONDecodeError, KeyError) as ex:
        return _result(error=f"Unexpected response: {ex}")

    latest_version = payload.get("tag_name", "").lstrip("vV")
    installer, checksum_url = _release_files(payload)

    return _result(
        available=_is_newer(latest_version, current_version),
        latest_version=latest_version or None,
        url=payload.get("html_url"),
        installer=installer,
        checksum_url=checksum_url,
    )


def _result(available=False, latest_version=None, url=None,
            installer=None, checksum_url=None, error=None):
    return {
        "available": available,
        "latest_version": latest_version,
        "url": url,
        "installer": installer,
        "checksum_url": checksum_url,
        "error": error,
    }


def _release_files(payload):
    """Picks the installer asset and its checksum sidecar out of a release.

    Returns (installer|None, checksum_url|None). Both are reported
    independently of each other so a release that has the installer but no
    checksum still reads as "found the installer, can't verify it" rather
    than as no installer at all.
    """
    assets = {
        asset.get("name", ""): asset
        for asset in payload.get("assets", [])
        if asset.get("browser_download_url", "").startswith(DOWNLOAD_URL_PREFIX)
    }

    match = next(
        (name for name in sorted(assets)
         if fnmatch.fnmatch(name.lower(), INSTALLER_PATTERN.lower())),
        None,
    )

    if match is None:
        return None, None

    checksum = assets.get(match + CHECKSUM_SUFFIX)

    return (
        {
            "name": match,
            "url": assets[match]["browser_download_url"],
            "size": assets[match].get("size"),
        },
        checksum["browser_download_url"] if checksum else None,
    )


def _parse_version(version):
    return tuple(int(part) for part in re.findall(r"\d+", version))


def _is_newer(latest, current):
    # Prerelease suffixes are ignored by _parse_version, so "1.2.0-beta"
    # ties with "1.2.0" rather than sorting below it. Harmless here:
    # /releases/latest already skips drafts and prereleases entirely,
    # which is also how a release can be staged for testing without every
    # installation out there offering it.
    latest_parts = _parse_version(latest)
    current_parts = _parse_version(current)

    if not latest_parts:
        return False

    return latest_parts > current_parts
