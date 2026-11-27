"""CLMix's log: one place every part of the app reports to.

    log(level, message)       from any thread - "debug", "info", "warning"
                              or "error"
    log_store                 the session's entries (for the Logs page) and
                              the daily files under LOGS_DIR
    install_crash_logging()   called once at startup: uncaught exceptions
                              (any thread, Tk callbacks), Python's own
                              `logging` from libraries, and hard crashes
                              all end up here too

File lines read

    [2026-11-26 22:38:16.204] INFO    [MixerWorker] Mixer fully loaded

- millisecond timestamps (OSC timing questions need them) and the thread
that logged, so a line from the phone server can be told from one from the
console link.
"""
import atexit
import faulthandler
import itertools
import logging
import os
import platform
import queue
import sys
import threading
import time
import traceback
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

MAX_ENTRIES = 20000
RETENTION_DAYS = 30
MAX_LOG_FILE_BYTES = 10 * 1024 * 1024
# The whole folder's .log files, past which the oldest go first. Debug
# logging every OSC message can fill a 10MB file several times a day at a
# busy show; without this, 30 days of that had no ceiling.
MAX_TOTAL_LOG_BYTES = 200 * 1024 * 1024
FLUSH_SECONDS = 1.0
FLUSH_NOW_LEVELS = ("warning", "error")
LEVELS = ("debug", "info", "warning", "error")

LOGS_DIR = Path.home() / ".clmix_logs"
# Where faulthandler writes if the process dies in native code (an audio
# driver, the speech engine) - nothing in Python gets to run then, so it
# cannot go through the normal log.
CRASH_LOG_NAME = "crash.log"
MAX_CRASH_LOG_BYTES = 1024 * 1024


class LogStore:
    """Thread-safe, bounded in-memory log buffer feeding the Logs window,
    plus same-day .log file(s) on disk (LOGS_DIR) so activity survives past
    a single session.

    Files are written by a background thread of their own: add() only
    appends to the buffer and hands the line over, so neither the Tk
    thread nor the console link ever waits on the disk (or on Windows
    Defender scanning it). Lines are flushed at most every FLUSH_SECONDS,
    or at once for a warning or error; flush() waits for everything
    handed over so far to reach the file.

    A single file handle is kept open and reused (reopened on day
    rollover or once it passes MAX_LOG_FILE_BYTES, at which point writing
    continues in a numbered ".2.log", ".3.log", ... file for that day).
    Files older than RETENTION_DAYS are pruned whenever the day changes,
    and the oldest go whenever the folder passes MAX_TOTAL_LOG_BYTES.

    The UI polls entries_since() rather than being pushed to, keeping this
    module free of any Tkinter dependency.
    """

    def __init__(self, max_entries=MAX_ENTRIES, logs_dir=LOGS_DIR):
        self._entries = deque(maxlen=max_entries)
        self._lock = threading.Lock()
        self._file_lock = threading.Lock()
        self._seq = 0
        self.logs_dir = logs_dir

        self._ensure_logs_dir()

        self._file_handle = None
        self._current_file_path = None
        # "YYYY-MM-DD" of the file being written.
        self._active_date = None
        # (whole second, "YYYY-MM-DD HH:MM:SS") - see add().
        self._stamp_cache = (None, "")
        self._active_suffix = 1
        self._file_size = 0
        self._flushed_at = 0.0
        self.write_error = None

        self._pending = queue.SimpleQueue()
        self._writer = threading.Thread(
            target=self._write_loop, name="LogWriter", daemon=True
        )
        self._writer.start()

        self.add("info", f"=== CLMix session started · {_describe_runtime()} ===")

    # --- Entries -------------------------------------------------------

    def add(self, level, message):
        now = time.time()
        second = int(now)

        # The date and time are formatted once a second, not per line -
        # strftime was most of what a log() call cost. One tuple, read
        # and replaced whole, so threads never see a mismatched pair.
        cache = self._stamp_cache
        if cache[0] != second:
            cache = (second, time.strftime("%Y-%m-%d %H:%M:%S",
                                           time.localtime(second)))
            self._stamp_cache = cache

        stamp = f"{cache[1]}.{int((now - second) * 1000):03d}"
        thread = threading.current_thread().name

        with self._lock:
            self._seq += 1
            self._entries.append((self._seq, stamp[11:], level, message))

        line = f"[{stamp}] {level.upper():7} [{thread}] {message}\n"
        day = stamp[:10]

        if self._writer.is_alive():
            self._pending.put((day, level, line))
        else:
            # Interpreter shutting down, or the writer died: write here.
            with self._file_lock:
                self._write_line(day, level, line)

    def entries_since(self, seq):
        """Only the entries newer than seq - for the Logs window's poll,
        which used to copy all MAX_ENTRIES every half second to find the
        handful that were new. Sequence numbers are consecutive, so the
        new ones are simply the last (latest seq - seq) of the buffer."""
        with self._lock:
            if not self._entries:
                return []

            new = min(len(self._entries), self._entries[-1][0] - seq)

            if new <= 0:
                return []

            return list(itertools.islice(
                reversed(self._entries), new
            ))[::-1]

    def snapshot(self):
        with self._lock:
            return list(self._entries)

    def count(self):
        # For the main window's info bar, which polls every second - far
        # cheaper than len(snapshot()), which copies up to MAX_ENTRIES.
        with self._lock:
            return len(self._entries)

    def clear(self):
        with self._lock:
            self._entries.clear()

    # --- Files ---------------------------------------------------------

    def flush(self, timeout=2.0):
        """Wait (up to timeout) until every line handed over so far is on
        disk - for the UI's once-a-second tick, before a crash is
        reported, and at exit."""
        if not self._writer.is_alive() or \
                threading.current_thread() is self._writer:
            with self._file_lock:
                self._flush_file()
            return

        done = threading.Event()
        self._pending.put(done)
        done.wait(timeout)

    def close(self):
        """Stop the writer once it has written everything - at exit."""
        if self._writer.is_alive():
            self._pending.put(None)
            self._writer.join(2.0)

        with self._file_lock:
            self._flush_file()
            self._close_file_handle()

    def _write_loop(self):
        while True:
            try:
                item = self._pending.get(timeout=FLUSH_SECONDS)
            except queue.Empty:
                with self._file_lock:
                    self._flush_file()
                continue

            urgent = False
            stop = False
            waiters = []

            # Everything already waiting goes in one go: under a burst of
            # console traffic that is hundreds of lines per wake-up.
            with self._file_lock:
                while True:
                    if item is None:
                        stop = True
                    elif isinstance(item, threading.Event):
                        waiters.append(item)
                    else:
                        day, level, line = item
                        self._write_line(day, level, line)
                        urgent = urgent or level in FLUSH_NOW_LEVELS

                    try:
                        item = self._pending.get_nowait()
                    except queue.Empty:
                        break

                if urgent or waiters or stop or \
                        time.monotonic() - self._flushed_at >= FLUSH_SECONDS:
                    self._flush_file()

            for waiter in waiters:
                waiter.set()

            if stop:
                return

    def _write_line(self, day, level, line):
        """Under _file_lock. day is the line's "YYYY-MM-DD"."""
        self._roll_file_if_needed(day)

        if self._file_handle is None:
            return

        try:
            self._file_handle.write(line)
            # Counted rather than asked for with tell(), which on a
            # text file is slow enough to matter at this rate.
            self._file_size += len(line.encode("utf-8"))
            self.write_error = None
        except OSError as ex:
            self.write_error = str(ex)
            self._close_file_handle()

    def _flush_file(self):
        """Under _file_lock."""
        if self._file_handle is None:
            return

        try:
            self._file_handle.flush()
            self._flushed_at = time.monotonic()
        except OSError as ex:
            self.write_error = str(ex)
            self._close_file_handle()

    def _ensure_logs_dir(self):
        try:
            self.logs_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def _log_files(self):
        try:
            return sorted(self.logs_dir.glob("*.log"))
        except OSError:
            return []

    def _prune_old_logs(self):
        cutoff = datetime.now() - timedelta(days=RETENTION_DAYS)

        for path in self._log_files():
            try:
                file_date = datetime.strptime(path.stem.split(".")[0], "%Y-%m-%d")
            except ValueError:
                continue

            if file_date < cutoff:
                try:
                    path.unlink()
                except OSError:
                    pass

        self._prune_to_size()

    def _prune_to_size(self):
        """Oldest days first until the folder is under MAX_TOTAL_LOG_BYTES.
        The file being written is never removed."""
        dated = []
        for path in self._log_files():
            try:
                datetime.strptime(path.stem.split(".")[0], "%Y-%m-%d")
                dated.append((path, path.stat().st_size))
            except (ValueError, OSError):
                continue

        # "2026-11-26.log" before "2026-11-26.2.log": oldest first.
        dated.sort(key=lambda item: (item[0].stem.split(".")[0],
                                     _suffix_of(item[0])))
        total = sum(size for _path, size in dated)

        for path, size in dated:
            if total <= MAX_TOTAL_LOG_BYTES:
                break
            if path == self._current_file_path:
                continue
            try:
                path.unlink()
                total -= size
            except OSError:
                pass

    def current_log_path(self):
        if self._current_file_path is not None:
            return self._current_file_path

        return self._file_path_for(time.strftime("%Y-%m-%d"), 1)

    def _file_path_for(self, day, suffix):
        stem = day

        if suffix > 1:
            stem = f"{stem}.{suffix}"

        return self.logs_dir / f"{stem}.log"

    def _roll_file_if_needed(self, today):
        if today != self._active_date:
            self._close_file_handle()
            self._active_date = today
            self._active_suffix = 1
            self._prune_old_logs()
            self._open_file_handle()
            return

        if self._file_handle is None:
            self._open_file_handle()
            return

        if self._file_size >= MAX_LOG_FILE_BYTES:
            self._close_file_handle()
            self._active_suffix += 1
            self._open_file_handle()
            self._prune_to_size()

    def _open_file_handle(self):
        self._current_file_path = self._file_path_for(
            self._active_date, self._active_suffix
        )

        try:
            self._file_handle = open(
                self._current_file_path, "a", encoding="utf-8"
            )
            self._file_size = self._file_handle.tell()
            self.write_error = None
        except OSError as ex:
            self._file_handle = None
            self.write_error = str(ex)

    def _close_file_handle(self):
        if self._file_handle is not None:
            try:
                self._file_handle.close()
            except OSError:
                pass

            self._file_handle = None

    def get_write_error(self):
        return self.write_error


def _suffix_of(path):
    parts = path.stem.split(".")
    try:
        return int(parts[1]) if len(parts) > 1 else 1
    except ValueError:
        return 1


def _describe_runtime():
    """Which CLMix, on what - so a log sent in identifies itself."""
    try:
        from version import VERSION
    except ImportError:
        VERSION = "?"

    build = "installed" if getattr(sys, "frozen", False) else "from source"
    if sys.platform == "win32":
        # Not platform.version(): on Windows that is a WMI query, ~80ms
        # at every launch. Build 22000 and up is Windows 11.
        v = sys.getwindowsversion()
        name = "11" if v.major == 10 and v.build >= 22000 else str(v.major)
        system = f"Windows {name} ({v.major}.{v.minor}.{v.build})"
    else:
        system = f"{platform.system()} {platform.release()}"
    return (f"v{VERSION} {build} · {system} · Python "
            f"{platform.python_version()} · pid {os.getpid()}")


class CaptureSettings:
    """How much of the inbound mixer traffic reaches the log.

    Everything the console sends is logged, with metering the single
    exception: at ~30Hz it is the only traffic voluminous enough to bury
    every other line and roll the log file within the hour, so it starts
    off and is reported as a periodic count instead. Ticking "Meters" in
    the Logs window logs every meter packet as well.

    Off is the only default that is ever assumed - session-scoped and
    never persisted, so a capture left on in some earlier run cannot
    quietly flood a later one.
    """

    def __init__(self):
        self.meters = False


capture = CaptureSettings()

log_store = LogStore()
# Everything handed over is written out on a normal exit.
atexit.register(log_store.close)


# Echo debug lines to the terminal too. Off by default: every OSC
# message in and out is logged at debug, and printing each one to a
# Windows console is slow enough to cost the UI frames under traffic.
# They still reach the Logs page and the log file either way.
ECHO_DEBUG = bool(os.environ.get("CLMIX_DEBUG_CONSOLE"))


def log(level, message):
    try:
        if level != "debug" or ECHO_DEBUG:
            print(f"[{level}] {message}")
    except (AttributeError, OSError, ValueError):
        # No usable stdout - e.g. a windowed (console=False) PyInstaller
        # build, where sys.stdout is None and print() would otherwise
        # raise and take the caller down with it.
        pass

    log_store.add(level, message)


# --- Crashes and libraries -------------------------------------------------

def log_exception(where, exc_type, exc_value, exc_tb):
    """An uncaught exception, traceback and all, flushed to disk at once -
    the process may be about to go."""
    text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    log("error", f"Uncaught exception in {where}:\n{text.rstrip()}")
    log_store.flush()


class _LibraryHandler(logging.Handler):
    """Python's `logging`, from libraries CLMix uses (websockets,
    zeroconf, asyncio, faster-whisper, ...), into this log - their
    warnings and errors used to go to a stderr a windowed build does not
    have."""

    LEVEL_NAMES = ((logging.ERROR, "error"), (logging.WARNING, "warning"),
                   (logging.INFO, "info"))

    def emit(self, record):
        try:
            level = next((name for number, name in self.LEVEL_NAMES
                          if record.levelno >= number), "debug")
            message = f"{record.name}: {record.getMessage()}"
            if record.exc_info:
                message += "\n" + "".join(
                    traceback.format_exception(*record.exc_info)).rstrip()
            log(level, message)
        except Exception:  # noqa: BLE001 - logging must never raise
            self.handleError(record)


_installed = False
_crash_file = None


def install_crash_logging(tk_root=None):
    """Route everything that can go wrong into the log. Safe to call more
    than once; pass tk_root once the Tk root exists, so exceptions inside
    Tk callbacks (button commands, after() jobs) are caught too."""
    global _installed, _crash_file

    if tk_root is not None:
        def report(exc_type, exc_value, exc_tb):
            log_exception("a Tk callback", exc_type, exc_value, exc_tb)
        tk_root.report_callback_exception = report

    if _installed:
        return
    _installed = True

    previous_hook = sys.excepthook

    def excepthook(exc_type, exc_value, exc_tb):
        if not issubclass(exc_type, KeyboardInterrupt):
            log_exception("the main thread", exc_type, exc_value, exc_tb)
        previous_hook(exc_type, exc_value, exc_tb)

    sys.excepthook = excepthook

    previous_thread_hook = threading.excepthook

    def thread_excepthook(args):
        if args.exc_type is not SystemExit:
            name = args.thread.name if args.thread is not None else "a thread"
            log_exception(f"thread {name}", args.exc_type, args.exc_value,
                          args.exc_traceback)
        previous_thread_hook(args)

    threading.excepthook = thread_excepthook

    handler = _LibraryHandler(level=logging.WARNING)
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    if root_logger.level > logging.WARNING or root_logger.level == logging.NOTSET:
        root_logger.setLevel(logging.WARNING)

    # A crash in native code (audio driver, speech engine) kills the
    # process before any of the above can run; faulthandler still gets
    # the Python stack of every thread out, into a file of its own.
    try:
        path = LOGS_DIR / CRASH_LOG_NAME
        if path.exists() and path.stat().st_size > MAX_CRASH_LOG_BYTES:
            path.unlink()
        _crash_file = open(path, "a", encoding="utf-8")
        _crash_file.write(f"--- session {datetime.now():%Y-%m-%d %H:%M:%S} "
                          f"pid {os.getpid()} ---\n")
        _crash_file.flush()
        faulthandler.enable(file=_crash_file, all_threads=True)
    except (OSError, RuntimeError, ValueError) as ex:
        log("warning", f"Native crash reports not available: {ex}")
