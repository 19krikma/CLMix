import hashlib
import json
import secrets
import threading
from pathlib import Path

from services.log_store import log

USERS_PATH = Path.home() / ".clmix_users.json"

ALL_SNAPSHOTS = "All Snapshots"
ALL_AUX = "All Aux"


class UserStore:
    """Remote-access accounts, keyed by username.

    Each account is scoped to one snapshot (by name) or ALL_SNAPSHOTS, and
    either ALL_AUX or a list of specific aux bus names. RemoteServer checks
    these scopes against the mixer's current snapshot/aux before honoring
    a client's requests.

    Alongside those scopes - which say *where* an account may act - three
    flags say *what* it may do there: "presets" gates the preset actions,
    and "mute" gates the per-send on/off write. An account denied mute can
    still ride levels and pan on its permitted auxes, and still sees which
    channels are muted; it just can't drop one out of the mix.

    The third, "mixer_control", is a different order of thing: it grants
    the console's own channel faders, mutes and pans - the main mix, which
    every listener hears - rather than one performer's send. Nothing else
    here is console-wide, so unlike "mute" it defaults to *off*, for
    existing accounts and new ones alike; it has to be granted
    deliberately.

    A fourth, "personalization", is the odd one out: it grants nothing on
    the console at all. It lets an account relabel channels for its own
    aux screens only - see personal_names() below - so a performer can
    read "My Vox" where the desk says "CH12". Off by default like
    mixer_control, though for the opposite reason: it changes nothing an
    account could already do, so nobody should acquire it unasked.

    Records are mutated from two threads - the Tkinter one through the
    Accounts tab, and RemoteServer's event loop through
    set_personal_name() - so every write goes through _lock. Reads are
    left unlocked: they only ever touch whole dict values, and a phone
    reading a name mid-rename either gets the old one or the new one.
    """

    def __init__(self, path=USERS_PATH):
        self.path = path
        self._lock = threading.Lock()
        self.users = self._load()

    def _load(self):
        try:
            with open(self.path) as f:
                users = json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

        # Older accounts stored "aux" as a single name string rather than
        # a list - normalize on load so every caller can assume the
        # current shape (ALL_AUX or a list) regardless of when the
        # account was created.
        for record in users.values():
            aux = record.get("aux")
            if aux is not None and aux != ALL_AUX and not isinstance(aux, list):
                record["aux"] = [aux]

            # "mute" was added after accounts were already in the wild,
            # and every one of those accounts could mute. Defaulting a
            # missing key to True keeps an upgrade (or a restored older
            # backup) from quietly taking a capability away from a
            # performer mid-show - only an explicit False denies it.
            record.setdefault("mute", True)

            # The opposite default to "mute" above, and deliberately so:
            # this one reaches the main mix, so an account created before
            # it existed must not silently acquire it on upgrade.
            record.setdefault("mixer_control", False)

            # Not setdefault'd to a shared literal: each record needs its
            # own dict, or every account would rename channels into the
            # same one.
            if not isinstance(record.get("personal_names"), dict):
                record["personal_names"] = {}

            record.setdefault("personalization", False)

        return users

    def _save(self):
        try:
            with open(self.path, "w") as f:
                json.dump(self.users, f, indent=2)
        except OSError as ex:
            log("error", f"Failed to save users: {ex!r}")

    def reload(self):
        # Under the lock so a rename landing from the event loop at the
        # same moment cannot save itself into the dict this is replacing.
        with self._lock:
            self.users = self._load()

    def list_users(self):
        return sorted(self.users.items())

    def get(self, username):
        return self.users.get(username)

    def save_user(self, username, password, snapshot, aux, presets=False,
                  mute=True, mixer_control=False, personalization=False):
        with self._lock:
            record = dict(self.users.get(username, {}))
            salt = record.get("salt") or secrets.token_hex(16)

            if password:
                record["salt"] = salt
                record["password_hash"] = self._hash(password, salt)
            elif "password_hash" not in record:
                raise ValueError("Password is required for a new user")

            record["snapshot"] = snapshot
            record["aux"] = aux
            record["presets"] = presets
            record["mute"] = mute
            record["mixer_control"] = mixer_control
            record["personalization"] = personalization

            # Copied forward rather than rewritten: revoking the
            # permission is not the same as discarding what the performer
            # named their channels, and an operator who turns it off by
            # mistake should be able to turn it back on and find the
            # labels still there.
            record.setdefault("personal_names", {})

            self.users[username] = record
            self._save()

    def delete_user(self, username):
        with self._lock:
            if username in self.users:
                del self.users[username]
                self._save()

    def authenticate(self, username, password):
        record = self.users.get(username)

        if not record or self._hash(password, record["salt"]) != record["password_hash"]:
            return None

        return {
            "snapshot": record["snapshot"],
            "aux": record["aux"],
            "presets": record.get("presets", False),
            "mute": record.get("mute", True),
            "mixer_control": record.get("mixer_control", False),
            "personalization": record.get("personalization", False),
        }

    def personal_names(self, username, snapshot):
        """This account's own channel labels for one snapshot.

        Keyed by snapshot name because an account scoped to
        ALL_SNAPSHOTS follows the desk from one show to the next, where
        the same channel number is a different instrument - a label that
        followed it across would be worse than none. Returns {channel
        number: label}, converted from the string keys JSON forces.

        Never the console's own names: these exist only on this account's
        aux screens, and nothing here is ever written back to the desk.
        """
        record = self.users.get(username)

        if not record or not snapshot:
            return {}

        names = record.get("personal_names", {}).get(snapshot, {})
        resolved = {}

        for channel, name in names.items():
            try:
                resolved[int(channel)] = name
            except (TypeError, ValueError):
                continue

        return resolved

    def set_personal_name(self, username, snapshot, channel, name):
        """Label one channel for one account on one snapshot.

        A falsy name clears the label rather than storing an empty one -
        that is how a phone asks for the console's own name back. Empty
        snapshot entries are pruned with it, so an account that renames
        and then undoes it leaves nothing behind.

        Called from RemoteServer's event loop while the Accounts tab may
        be saving from the Tkinter thread, hence the lock.
        """
        if not snapshot:
            return

        with self._lock:
            record = self.users.get(username)

            if record is None:
                return

            names = record.setdefault("personal_names", {})
            per_snapshot = names.setdefault(snapshot, {})
            key = str(channel)

            if name:
                per_snapshot[key] = name
            else:
                per_snapshot.pop(key, None)

            if not per_snapshot:
                del names[snapshot]

            self._save()

    @staticmethod
    def _hash(password, salt):
        return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
