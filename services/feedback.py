"""Bug reports, feature requests and error logs, forwarded to the
collector (a separate service - github.com/19krikma/CLMix-Collector,
whose API.md is the contract this module is written against).

Phones never talk to the collector themselves. They hand a report to
this server over the websocket (RemoteServer's "submit_report"), the
desktop app's Feedback page hands one over directly, and either way it
lands here: written to disk first, then sent. A venue often has no
internet, so the disk copy is the point - an outbox that survives
restarts and is emptied whenever the collector can be reached.

Error logs only flow while the operator has opted in (the
"share_error_logs" setting, off by default). Reports need no switch:
writing one and pressing Send is the consent.
"""

import json
import os
import platform
import re
import sys
import threading
import time
import traceback as traceback_module
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

from services import log_store as log_store_module
from services.log_store import log
from services.update_checker import SSL_CONTEXT
from version import VERSION

# Where reports go. Must be https:// - the privacy policy promises reports
# and error logs travel encrypted. Left empty until the collector is
# deployed; with no URL the outbox still fills (nothing is lost), it just
# never empties.
DEFAULT_COLLECTOR_URL = ""
# Not a secret in any real sense: it ships inside every copy of the app.
# It only keeps the collector's endpoints from being trivially spammed by
# someone who stumbled on the URL.
DEFAULT_COLLECTOR_API_KEY = ""

# Both can be overridden from the environment, which is how a development
# build points at a collector on the LAN - over plain http if need be,
# which the built-in default above must never be.
COLLECTOR_URL = os.environ.get("CLMIX_COLLECTOR_URL",
                               DEFAULT_COLLECTOR_URL).rstrip("/")
COLLECTOR_API_KEY = os.environ.get("CLMIX_COLLECTOR_KEY",
                                   DEFAULT_COLLECTOR_API_KEY)

OUTBOX_DIR = Path.home() / ".clmix_outbox"

REQUEST_TIMEOUT_SECONDS = 10
# How long submit_report waits for the collector before telling the phone
# "saved, will be sent" instead. Short: someone is looking at a spinner.
SEND_NOW_TIMEOUT_SECONDS = 5

# Retry pacing while the collector can't be reached: doubles from the
# first to the last after each failed attempt, and resets on a success.
RETRY_MIN_SECONDS = 30
RETRY_MAX_SECONDS = 15 * 60

# An unsent error is worth less the older it gets, and a machine that is
# never online shouldn't fill its disk with them. Past this many the
# oldest are dropped. Reports are never dropped this way.
MAX_QUEUED_ERRORS = 1000
ERRORS_PER_REQUEST = 200

# The same error over and over - "no response from mixer" every few
# seconds while the console is off - is one problem, not a thousand. At
# most this many of one kind are queued per hour; the rest are counted
# and mentioned in the next one that does get through.
ERROR_REPEATS_PER_HOUR = 5

# Log lines sent along with an error, for what led up to it. Debug lines
# are left out: they are mostly console traffic, and would push
# everything useful out of the window.
CONTEXT_LINES = 20

# Field limits, the same as the collector's (API.md). Checked here as
# well so a phone hears about an over-long title straight away, rather
# than the report being queued and then refused by the collector.
TITLE_MAX = 200
DESCRIPTION_MAX = 10000
CONTACT_MAX = 200
REPORT_TYPES = ("bug", "feature")
PLATFORMS = ("android", "ios", "desktop")


def _now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _os_description():
    system = platform.system()

    if system == "Windows":
        return f"Windows {platform.release()} ({platform.version()})"

    if system == "Darwin":
        return f"macOS {platform.mac_ver()[0]}"

    return platform.platform(terse=True)


def validate_report(fields):
    """Tidy up a report's text fields, or explain what is wrong with them.

    Returns (report, None) or (None, message) - the message is shown to
    whoever wrote the report, so it is phrased for them.
    """
    report_type = fields.get("type")

    if report_type not in REPORT_TYPES:
        return None, "Choose whether this is a bug or a feature request"

    title = str(fields.get("title") or "").strip()
    description = str(fields.get("description") or "").strip()
    contact = str(fields.get("contact") or "").strip()

    if not title:
        return None, "Give the report a title"

    if len(title) > TITLE_MAX:
        return None, f"The title is too long (at most {TITLE_MAX} characters)"

    if len(description) > DESCRIPTION_MAX:
        return None, (f"The description is too long "
                      f"(at most {DESCRIPTION_MAX} characters)")

    if len(contact) > CONTACT_MAX:
        return None, f"The contact is too long (at most {CONTACT_MAX} characters)"

    return {
        "type": report_type,
        "title": title,
        "description": description,
        "contact": contact or None,
    }, None


def _origin(device):
    """The "origin" block for a report, from what a phone said about
    itself. Unknown or absent fields are left out rather than guessed."""
    device = device if isinstance(device, dict) else {}
    origin = {"platform": device.get("platform")}

    if origin["platform"] not in PLATFORMS:
        origin["platform"] = "android"

    for key in ("app_version", "device_model", "os_version"):
        value = device.get(key)

        if isinstance(value, str) and value.strip():
            origin[key] = value.strip()[:100]

    return origin


def _error_kind(source, message):
    """A rough grouping key for the repeat limit - the collector does the
    real grouping. Numbers are what usually differ between repeats."""
    return source, re.sub(r"\d+", "#", message)


class Outbox:
    """Reports and errors waiting to go to the collector, one JSON file
    each under OUTBOX_DIR, and the thread that sends them.

    Every item has a uid, which the collector stores it under. That makes
    sending idempotent: an item whose request timed out after the
    collector had already stored it is simply sent again, and the
    collector answers as it did the first time. So nothing here has to
    know whether an attempt "really" failed - only whether it got an
    answer.
    """

    def __init__(self, outbox_dir=OUTBOX_DIR, url=COLLECTOR_URL,
                 api_key=COLLECTOR_API_KEY):
        self.url = url
        self.api_key = api_key
        self.reports_dir = outbox_dir / "reports"
        self.errors_dir = outbox_dir / "errors"
        self._install_id_path = outbox_dir / "install_id"

        for path in (self.reports_dir, self.errors_dir):
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass

        self.install_id = self._load_install_id()
        self.console_model = None

        self.errors_enabled = False
        self._error_lock = threading.Lock()
        # kind -> [window start (monotonic), queued this hour, suppressed]
        self._error_counts = {}

        # uid -> the collector's answer, for reports delivered this
        # session. Lets send_report_now answer a phone with the ref even
        # when the sender thread got there first and the file is gone.
        self._delivered = {}

        self._wake = threading.Event()
        self._thread = None
        self._retry_seconds = RETRY_MIN_SECONDS

        # What the Feedback page shows. Written from the sender thread,
        # read from the Tkinter one; each is only ever swapped whole.
        self.last_error = None
        self.last_sent_at = None

    # --- Install identity ---------------------------------------------------

    def _load_install_id(self):
        """A random id for this install, made once and kept. It is what
        lets the collector tell installs apart, and is deliberately not
        derived from anything about the machine or its owner."""
        try:
            value = self._install_id_path.read_text().strip()

            if value:
                return value
        except OSError:
            pass

        value = str(uuid.uuid4())

        try:
            self._install_id_path.write_text(value)
        except OSError:
            # Unsaved, so a new one next launch - the collector sees a
            # new install, which is the only cost.
            pass

        return value

    def _install(self):
        install = {
            "id": self.install_id,
            "clmix_version": VERSION,
            "os": _os_description(),
        }

        if self.console_model:
            install["console_model"] = self.console_model

        return install

    # --- Queueing -----------------------------------------------------------

    @staticmethod
    def _write(directory, uid, item):
        # Written beside its final name and renamed into place, so a crash
        # or power cut mid-write can't leave a half file for the sender to
        # trip over.
        path = directory / f"{uid}.json"
        temporary = directory / f"{uid}.tmp"
        temporary.write_text(json.dumps(item))
        os.replace(temporary, path)

    def add_report(self, fields, device=None, uid=None):
        """Queue a validated report (see validate_report). Returns its uid.

        uid may come from the phone, which then gets the same answer if it
        sends the same report twice - say, resent after its connection
        dropped before the reply arrived. Raises OSError if the report
        can't be saved, which the caller reports as a failure: a report
        that is neither saved nor sent must not look received.
        """
        uid = uid if isinstance(uid, str) and re.fullmatch(
            r"[0-9A-Za-z-]{8,64}", uid) else str(uuid.uuid4())

        if (self.reports_dir / f"{uid}.json").exists():
            return uid

        device = dict(device or {})
        device.setdefault("platform", "desktop")

        item = dict(fields, uid=uid, created_at=_now_iso(),
                    origin=_origin(device))
        self._write(self.reports_dir, uid, item)
        log("info", f"Report queued: {fields['type']} {fields['title']!r}")
        return uid

    def add_error(self, source, message, context=(), traceback=None):
        """Queue one error-level log entry, if the operator has opted in.

        Called from inside log() itself, on whatever thread logged the
        error - so it must never log an error of its own (that would come
        straight back here) and never raise.
        """
        if not self.errors_enabled:
            return

        kind = _error_kind(source, message)
        now = time.monotonic()

        with self._error_lock:
            counts = self._error_counts.get(kind)

            if counts is None or now - counts[0] >= 3600:
                counts = self._error_counts[kind] = [now, 0, 0]

            if counts[1] >= ERROR_REPEATS_PER_HOUR:
                counts[2] += 1
                return

            counts[1] += 1

            if counts[2]:
                message = (f"{message} [{counts[2]} more like this in the "
                           f"last hour were not sent]")
                counts[2] = 0

        uid = str(uuid.uuid4())
        event = {
            "uid": uid,
            "occurred_at": _now_iso(),
            "source": source[:100],
            "message": message[:5000],
            "context": [line[:1000] for line in context][-50:],
        }

        if traceback:
            event["traceback"] = traceback[-20000:]

        try:
            self._write(self.errors_dir, uid, event)
        except OSError:
            return

        self._trim_errors()
        self._wake.set()

    def _trim_errors(self):
        try:
            paths = sorted(self.errors_dir.glob("*.json"),
                           key=lambda path: path.stat().st_mtime)
        except OSError:
            return

        for path in paths[:max(0, len(paths) - MAX_QUEUED_ERRORS)]:
            path.unlink(missing_ok=True)

    def set_errors_enabled(self, enabled):
        """The opt-in switch. Turning it off also deletes anything still
        waiting to be sent: no means no, including for the backlog."""
        self.errors_enabled = bool(enabled)

        if not enabled:
            for path in self._queued(self.errors_dir):
                path.unlink(missing_ok=True)

    @staticmethod
    def _queued(directory):
        try:
            return sorted(directory.glob("*.json"),
                          key=lambda path: path.stat().st_mtime)
        except OSError:
            return []

    def pending(self):
        """(reports, errors) still waiting to be sent."""
        return (len(self._queued(self.reports_dir)),
                len(self._queued(self.errors_dir)))

    # --- Sending ------------------------------------------------------------

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="feedback-outbox")
        self._thread.start()

    def wake(self):
        self._wake.set()

    def _run(self):
        while True:
            self._wake.wait(timeout=self._retry_seconds)
            self._wake.clear()

            try:
                delivered = self.flush()
            except Exception as ex:  # noqa: BLE001 - must outlive any bug
                # Warning, not error: an error would be queued for this
                # very thread to send.
                log("warning", f"Feedback outbox failed: {ex!r}")
                delivered = False

            if delivered:
                self._retry_seconds = RETRY_MIN_SECONDS
            else:
                self._retry_seconds = min(self._retry_seconds * 2,
                                          RETRY_MAX_SECONDS)

    def flush(self):
        """Send everything queued. Returns False if the collector could
        not be reached (so the caller backs off), True otherwise -
        including when there was nothing to send."""
        if not self.url:
            return True

        for path in self._queued(self.reports_dir):
            if self._send_report_file(path) is None:
                return False

        while True:
            paths = self._queued(self.errors_dir)[:ERRORS_PER_REQUEST]

            if not paths:
                return True

            if not self._send_error_files(paths):
                return False

    def send_report_now(self, uid, timeout=SEND_NOW_TIMEOUT_SECONDS):
        """Try to deliver one queued report straight away, for the
        "received" reply. Returns the collector's answer ({"ref": ...}),
        or None if it is still queued. Safe to race with the sender
        thread: both may post it, and the collector stores it once."""
        if not self.url:
            return None

        return self._send_report_file(self.reports_dir / f"{uid}.json",
                                      timeout=timeout) or \
            self._delivered.get(uid)

    def _send_report_file(self, path, timeout=REQUEST_TIMEOUT_SECONDS):
        try:
            item = json.loads(path.read_text())
        except FileNotFoundError:
            # Sent and removed by the other path a moment ago.
            return None
        except (OSError, json.JSONDecodeError):
            log("warning", f"Dropping unreadable queued report {path.name}")
            path.unlink(missing_ok=True)
            return {}

        item["install"] = self._install()
        answer = self._post("/api/v1/reports", item, timeout)

        if answer is None:
            return None

        path.unlink(missing_ok=True)

        if answer.get("ref"):
            self._delivered[item["uid"]] = answer
            log("info", f"Report {answer['ref']} delivered: {item['title']!r}")

        return answer

    def _send_error_files(self, paths):
        events = []

        for path in paths:
            try:
                events.append(json.loads(path.read_text()))
            except (OSError, json.JSONDecodeError):
                path.unlink(missing_ok=True)

        if events:
            answer = self._post("/api/v1/errors",
                                {"install": self._install(), "events": events},
                                REQUEST_TIMEOUT_SECONDS)

            if answer is None:
                return False

        for path in paths:
            path.unlink(missing_ok=True)

        return True

    def _post(self, path, body, timeout):
        """POST JSON to the collector. Returns the decoded answer, {} for a
        request the collector refused for good (so the item is dropped),
        or None for one worth retrying."""
        request = urllib.request.Request(
            self.url + path,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-API-Key": self.api_key,
                "User-Agent": f"CLMix/{VERSION}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=timeout,
                                        context=SSL_CONTEXT) as response:
                answer = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as ex:
            if ex.code == 422:
                # Malformed, and it will be malformed next time too.
                # Dropped so it can't block everything queued behind it.
                log("warning", f"Collector refused {path} item as invalid; "
                    f"dropped it")
                self.last_error = None
                return {}

            # 401 (wrong key) and 5xx: retried. A wrong key needs a new
            # build to fix, and keeping the items means they go once it
            # is.
            self.last_error = f"Collector answered {ex.code}"
            log("warning", f"Feedback upload failed: {self.last_error}")
            return None
        except (urllib.error.URLError, TimeoutError, OSError,
                json.JSONDecodeError) as ex:
            self.last_error = "Collector not reachable"
            log("debug", f"Feedback upload failed: {ex!r}")
            return None

        self.last_error = None
        self.last_sent_at = time.time()
        return answer


outbox = Outbox()


# --- Feeding errors in ------------------------------------------------------

def _context_lines():
    lines = []

    for _seq, clock, level, message in log_store_module.log_store.tail(
            CONTEXT_LINES * 4):
        if level != "debug":
            lines.append(f"{clock} {level.upper():7} {message}")

    # The last line is the error itself, which is sent as the message.
    return lines[-CONTEXT_LINES - 1:-1]


def _on_error_logged(source, message, traceback):
    outbox.add_error(source, message, _context_lines(), traceback)


def install_hooks(root=None):
    """Route error-level log entries into the outbox, and turn exceptions
    nothing caught into error-level log entries.

    Before this, an exception escaping a Tk callback or a thread went to
    stderr - which a windowed build doesn't have - and was simply lost.
    Now each is logged as an error, with its traceback, whether or not
    error sharing is on.
    """
    log_store_module.set_error_hook(_on_error_logged)

    def report(where, exc_type, exc_value, exc_tb):
        text = "".join(traceback_module.format_exception(
            exc_type, exc_value, exc_tb))
        log("error", f"Unhandled exception in {where}: {exc_value!r}",
            source="unhandled", traceback=text)

    sys.excepthook = lambda *exc: report("main thread", *exc)
    threading.excepthook = lambda args: report(
        f"thread {args.thread.name if args.thread else '?'}",
        args.exc_type, args.exc_value, args.exc_traceback)

    if root is not None:
        root.report_callback_exception = \
            lambda *exc: report("the user interface", *exc)


def asyncio_exception_handler(loop, context):
    """For RemoteServer's event loop: an exception in a task nobody
    awaited would otherwise be printed and lost, like the ones above."""
    exception = context.get("exception")

    if exception is None:
        log("warning", f"Remote server: {context.get('message')}")
        return

    text = "".join(traceback_module.format_exception(exception))
    log("error", f"Unhandled exception in the remote server: {exception!r}",
        source="unhandled", traceback=text)
