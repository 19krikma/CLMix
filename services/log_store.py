import threading
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

MAX_ENTRIES = 20000
RETENTION_DAYS = 30
MAX_LOG_FILE_BYTES = 10 * 1024 * 1024

LOGS_DIR = Path.home() / ".clmix_logs"


class LogStore:
    """Thread-safe, bounded in-memory log buffer feeding the Logs window,
    plus same-day .log file(s) on disk (LOGS_DIR) so activity survives past
    a single session. Files older than RETENTION_DAYS are pruned whenever
    the active log date changes (including at startup).

    A single file handle is kept open and reused across writes (reopened
    on day rollover or once it passes MAX_LOG_FILE_BYTES, at which point
    writing continues in a numbered ".2.log", ".3.log", ... file for that
    day) rather than opening/closing the file on every call.

    Entries come from background threads (MixerWorker, RemoteServer) as
    well as the Tkinter main thread, so access is lock-protected. The
    UI polls snapshot() rather than being pushed to, keeping this module
    free of any Tkinter dependency.
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
        self._active_date = None
        self._active_suffix = 1
        self.write_error = None

        self.add("info", "=== CLMix session started ===")

    def _ensure_logs_dir(self):
        try:
            self.logs_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def _prune_old_logs(self):
        cutoff = datetime.now() - timedelta(days=RETENTION_DAYS)

        try:
            paths = list(self.logs_dir.glob("*.log"))
        except OSError:
            return

        for path in paths:
            try:
                file_date = datetime.strptime(path.stem.split(".")[0], "%Y-%m-%d")
            except ValueError:
                continue

            if file_date < cutoff:
                try:
                    path.unlink()
                except OSError:
                    pass

    def current_log_path(self):
        if self._current_file_path is not None:
            return self._current_file_path

        return self._file_path_for(datetime.now().date(), 1)

    def _file_path_for(self, date_, suffix):
        stem = date_.strftime("%Y-%m-%d")

        if suffix > 1:
            stem = f"{stem}.{suffix}"

        return self.logs_dir / f"{stem}.log"

    def add(self, level, message):
        now = datetime.now()

        with self._lock:
            self._seq += 1
            entry = (self._seq, now.strftime("%H:%M:%S"), level, message)
            self._entries.append(entry)

        self._write_to_file(now, level, message)

    def _write_to_file(self, now, level, message):
        line = f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] {level.upper():7} {message}\n"

        with self._file_lock:
            self._roll_file_if_needed(now)

            if self._file_handle is None:
                return

            try:
                self._file_handle.write(line)
                self.write_error = None
            except OSError as ex:
                self.write_error = str(ex)
                self._close_file_handle()

    def _roll_file_if_needed(self, now):
        today = now.date()

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

        try:
            size = self._file_handle.tell()
        except OSError:
            size = 0

        if size >= MAX_LOG_FILE_BYTES:
            self._close_file_handle()
            self._active_suffix += 1
            self._open_file_handle()

    def _open_file_handle(self):
        self._current_file_path = self._file_path_for(
            self._active_date, self._active_suffix
        )

        try:
            self._file_handle = open(
                self._current_file_path, "a", encoding="utf-8", buffering=1
            )
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

    def snapshot(self):
        with self._lock:
            return list(self._entries)

    def clear(self):
        with self._lock:
            self._entries.clear()


log_store = LogStore()


def log(level, message):
    try:
        print(f"[{level}] {message}")
    except (AttributeError, OSError, ValueError):
        # No usable stdout - e.g. a windowed (console=False) PyInstaller
        # build, where sys.stdout is None and print() would otherwise
        # raise and take the caller down with it.
        pass

    log_store.add(level, message)
