import threading
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

MAX_ENTRIES = 5000
RETENTION_DAYS = 30

LOGS_DIR = Path.home() / ".clmix_logs"


class LogStore:
    """Thread-safe, bounded in-memory log buffer feeding the Logs window,
    plus a same-day .log file on disk (LOGS_DIR) so activity survives past
    a single session. Files older than RETENTION_DAYS are pruned once at
    startup.

    Entries come from background threads (MixerWorker, RemoteServer) as
    well as the Tkinter main thread, so access is lock-protected. The
    UI polls snapshot() rather than being pushed to, keeping this module
    free of any Tkinter dependency.
    """

    def __init__(self, max_entries=MAX_ENTRIES, logs_dir=LOGS_DIR):
        self._entries = deque(maxlen=max_entries)
        self._lock = threading.Lock()
        self._file_lock = threading.Lock()
        self.logs_dir = logs_dir

        self._ensure_logs_dir()
        self._prune_old_logs()

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
                file_date = datetime.strptime(path.stem, "%Y-%m-%d")
            except ValueError:
                continue

            if file_date < cutoff:
                try:
                    path.unlink()
                except OSError:
                    pass

    def current_log_path(self):
        return self.logs_dir / f"{datetime.now().strftime('%Y-%m-%d')}.log"

    def add(self, level, message):
        now = datetime.now()
        entry = (now.strftime("%H:%M:%S"), level, message)

        with self._lock:
            self._entries.append(entry)

        self._write_to_file(now, level, message)

    def _write_to_file(self, now, level, message):
        line = f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] {level.upper():5} {message}\n"

        with self._file_lock:
            try:
                with open(self.current_log_path(), "a", encoding="utf-8") as f:
                    f.write(line)
            except OSError:
                pass

    def snapshot(self):
        with self._lock:
            return list(self._entries)

    def clear(self):
        with self._lock:
            self._entries.clear()


log_store = LogStore()


def log(level, message):
    print(f"[{level}] {message}")
    log_store.add(level, message)
