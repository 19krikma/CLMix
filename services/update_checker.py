import json
import re
import ssl
import urllib.error
import urllib.request

import certifi

REPO = "19krikma/CLMix"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
REQUEST_TIMEOUT_SECONDS = 8

# Built explicitly from certifi's bundled CA file rather than relying on
# urllib's default (the OS trust store) - on some Windows Python installs,
# and especially in a PyInstaller-frozen build, that lookup fails with
# "CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate"
# even though the same code works fine on Linux/macOS.
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


def check_for_update(current_version):
    """Checks the GitHub repo's latest release against current_version.

    Returns a dict:
        {"available": bool, "latest_version": str|None,
         "url": str|None, "error": str|None}

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
            request, timeout=REQUEST_TIMEOUT_SECONDS, context=_SSL_CONTEXT
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))

    except urllib.error.HTTPError as ex:
        if ex.code == 404:
            return {
                "available": False, "latest_version": None,
                "url": None, "error": None,
            }

        return {
            "available": False, "latest_version": None,
            "url": None, "error": f"GitHub API error ({ex.code})",
        }

    except (urllib.error.URLError, TimeoutError, OSError) as ex:
        return {
            "available": False, "latest_version": None,
            "url": None, "error": f"Network error: {ex}",
        }

    except (json.JSONDecodeError, KeyError) as ex:
        return {
            "available": False, "latest_version": None,
            "url": None, "error": f"Unexpected response: {ex}",
        }

    latest_version = payload.get("tag_name", "").lstrip("vV")
    url = payload.get("html_url")

    return {
        "available": _is_newer(latest_version, current_version),
        "latest_version": latest_version or None,
        "url": url,
        "error": None,
    }


def _parse_version(version):
    return tuple(int(part) for part in re.findall(r"\d+", version))


def _is_newer(latest, current):
    latest_parts = _parse_version(latest)
    current_parts = _parse_version(current)

    if not latest_parts:
        return False

    return latest_parts > current_parts
