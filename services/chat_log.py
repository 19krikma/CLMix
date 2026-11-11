import threading
import time

# Who a message is from, as phones and the desktop's Chat window tell
# them apart: a logged-in account, the sound engineer at the desktop, or
# an SOS a phone sent.
KIND_USER = "user"
KIND_TECH = "tech"
KIND_SOS = "sos"

# The name the desktop's own messages go out under, unless Settings >
# General gives it another - see tech_name_for.
TECH_NAME = "Sound Tech"

# Longest name the desktop can chat under.
MAX_TECH_NAME = 32

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
        cleaned - see clean_text. A desktop message without a name goes
        out as TECH_NAME."""
        with self._lock:
            if kind == KIND_TECH:
                name = name or TECH_NAME
            message = {
                "id": self._next_id,
                "kind": kind,
                "user": user,
                "name": name or user or "",
                "text": text,
                "time": time.time(),
            }
            self._next_id += 1
            self._messages.append(message)
            del self._messages[:-MAX_MESSAGES]
            self.revision += 1
            return dict(message)

    def rename_user(self, old, new, name=None):
        """Keeps an account's messages its own after a rename, under the
        name it goes by now."""
        with self._lock:
            for message in self._messages:
                if message["user"] == old:
                    message["user"] = new
                    if name and message["kind"] == KIND_USER:
                        message["name"] = name
            self.revision += 1

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


def clean_tech_name(name):
    """A chat name as it is stored, or None if there is nothing left."""
    if not isinstance(name, str):
        return None
    name = " ".join(name.split())[:MAX_TECH_NAME]
    return name or None


def normalize_tech_names(raw):
    """The desktop's chat names as settings keep them,

        {"default": "Sound Tech", "snapshots": {"Sunday AM": "Pastor's Tech"}}

    made safe to use: a missing or blank default is TECH_NAME, and blank
    per-snapshot names are dropped."""
    raw = raw if isinstance(raw, dict) else {}
    snapshots = raw.get("snapshots")
    return {
        "default": clean_tech_name(raw.get("default")) or TECH_NAME,
        "snapshots": {
            snapshot: clean_tech_name(name)
            for snapshot, name in (snapshots.items()
                                   if isinstance(snapshots, dict) else ())
            if isinstance(snapshot, str) and snapshot
            and clean_tech_name(name)
        },
    }


def tech_name_for(config, snapshot):
    """The name the desktop chats under while snapshot is live."""
    return config["snapshots"].get(snapshot) or config["default"]
