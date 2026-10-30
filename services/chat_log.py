import threading
import time

# Who a message is from, as phones and the desktop's Chat window tell
# them apart: a logged-in account, the sound engineer at the desktop, or
# an SOS a phone sent.
KIND_USER = "user"
KIND_TECH = "tech"
KIND_SOS = "sos"

# The name the desktop's own messages go out under.
TECH_NAME = "Sound Tech"

# Longest message accepted, in characters - a chat line, not a document.
MAX_TEXT = 500

# How many messages are kept. Older ones drop off the top; nothing is
# written to disk, so a restart of CLMix starts the chat empty.
MAX_MESSAGES = 300


class ChatLog:
    """The one chat everyone shares: every logged-in phone and the sound
    engineer at the desktop.

    Owned by the main window rather than RemoteServer, so the desktop's
    Chat window keeps its history across a disconnect and reconnect of
    the console. Written from both the server's event loop and the
    Tkinter thread, hence the lock. `revision` moves on every add, so the
    Chat window can tell from the Tkinter thread that it has catching up
    to do (the same pattern as PresetStore.revision).
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._messages = []
        self._next_id = 1
        self.revision = 0

    def add(self, kind, text, user=None, name=None):
        """Stores one message and returns it. `text` must already be
        cleaned - see clean_text."""
        with self._lock:
            message = {
                "id": self._next_id,
                "kind": kind,
                "user": user,
                "name": TECH_NAME if kind == KIND_TECH else (name or user or ""),
                "text": text,
                "time": time.time(),
            }
            self._next_id += 1
            self._messages.append(message)
            del self._messages[:-MAX_MESSAGES]
            self.revision += 1
            return dict(message)

    def messages(self, after_id=0):
        """Every message kept, oldest first - or only those newer than
        after_id."""
        with self._lock:
            return [dict(m) for m in self._messages if m["id"] > after_id]


def clean_text(text):
    """A message as it may be stored, or None if there is nothing to
    send: whitespace trimmed and capped at MAX_TEXT."""
    if not isinstance(text, str):
        return None

    text = text.strip()

    if not text:
        return None

    return text[:MAX_TEXT]
