import hashlib
import json
import secrets
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
    """

    def __init__(self, path=USERS_PATH):
        self.path = path
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

        return users

    def _save(self):
        try:
            with open(self.path, "w") as f:
                json.dump(self.users, f, indent=2)
        except OSError as ex:
            log("error", f"Failed to save users: {ex!r}")

    def list_users(self):
        return sorted(self.users.items())

    def get(self, username):
        return self.users.get(username)

    def save_user(self, username, password, snapshot, aux, presets=False):
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

        self.users[username] = record
        self._save()

    def delete_user(self, username):
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
        }

    @staticmethod
    def _hash(password, salt):
        return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
