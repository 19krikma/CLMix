import threading
from collections import deque
from datetime import datetime

MAX_ENTRIES = 5000


class LogStore:
    """Thread-safe, bounded in-memory log buffer feeding the Logs window.

    Entries come from background threads (MixerWorker, RemoteServer) as
    well as the Tkinter main thread, so access is lock-protected. The
    UI polls snapshot() rather than being pushed to, keeping this module
    free of any Tkinter dependency.
    """

    def __init__(self, max_entries=MAX_ENTRIES):
        self._entries = deque(maxlen=max_entries)
        self._lock = threading.Lock()

    def add(self, level, message):
        entry = (datetime.now().strftime("%H:%M:%S"), level, message)

        with self._lock:
            self._entries.append(entry)

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
