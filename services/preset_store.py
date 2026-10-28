import json
import threading
import uuid
from pathlib import Path

from services.log_store import log

PRESETS_PATH = Path.home() / ".clmix_presets.json"

# On-disk layout. Version 1 was a bare {name: {"channels": [...]}} dict
# with no owners - see _migrate.
FILE_VERSION = 2


class PresetStore:
    """Named snapshots of every channel's level+pan for a single aux.

    Captured/recalled from the phone apps via RemoteServer's save_preset/
    load_preset actions; the desktop UI (PresetsWindow) manages the list.

    Every preset belongs to the account that saved it and is private to
    that account until its owner - or the desktop - makes it public or
    shares it with named accounts. Anyone who can see a preset may load
    it; only its owner or the desktop may overwrite, delete, or change who
    sees it.

    A preset belongs to the aux it was saved from and only loads back onto
    that aux. aux is the console's aux number, which stays put when the
    bus is renamed; aux_name is what it was called at the time, for
    showing when the console is not there to ask. Names are unique per
    owner and aux, so two accounts - or one account on two auxes - can
    each keep a "Vocals" of their own.

    A record is {"id", "name", "owner", "public", "shared_with", "aux",
    "aux_name", "channels"}. owner and aux are None only for presets saved
    before either existed: those are public, so nobody lost access to
    them, and load onto any aux, there being no record of where they came
    from.

    Used from the server's event loop and the Tk thread both, so every
    read and write is under one lock and callers get copies, never the
    live records.
    """

    def __init__(self, path=PRESETS_PATH):
        self.path = path
        self._lock = threading.RLock()
        # Bumped on every change, from any thread, so the desktop page
        # can notice a phone's save without the server having to call
        # into Tk.
        self.revision = 0
        self.presets = self._load()

    def _load(self):
        try:
            with open(self.path) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return []

        if isinstance(data, dict) and data.get("version") == FILE_VERSION:
            return [
                self._clean(record) for record in data.get("presets", [])
                if isinstance(record, dict)
            ]

        if isinstance(data, dict):
            presets = self._migrate(data)
            # Written straight back so the ids handed out now are the
            # ones the next launch reads.
            self._write(presets)
            return presets

        return []

    @staticmethod
    def _migrate(legacy):
        """Version 1 had no owners: everything in it was open to every
        account with preset access, so it stays public and ownerless."""
        return [
            {
                "id": uuid.uuid4().hex,
                "name": name,
                "owner": None,
                "public": True,
                "shared_with": [],
                "aux": None,
                "aux_name": None,
                "channels": (data or {}).get("channels", []),
            }
            for name, data in sorted(legacy.items())
        ]

    @staticmethod
    def _clean(record):
        return {
            "id": record.get("id") or uuid.uuid4().hex,
            "name": record.get("name", ""),
            "owner": record.get("owner"),
            "public": bool(record.get("public", False)),
            "shared_with": sorted(set(record.get("shared_with") or [])),
            "aux": record.get("aux"),
            "aux_name": record.get("aux_name"),
            "channels": record.get("channels", []),
        }

    def _write(self, presets):
        try:
            with open(self.path, "w") as f:
                json.dump(
                    {"version": FILE_VERSION, "presets": presets}, f, indent=2
                )
        except OSError as ex:
            log("error", f"Failed to save presets: {ex!r}")

    def _save(self):
        self._write(self.presets)
        self.revision += 1

    @staticmethod
    def _copy(record):
        return dict(record, shared_with=list(record["shared_with"]))

    def _find(self, preset_id):
        for record in self.presets:
            if record["id"] == preset_id:
                return record
        return None

    @staticmethod
    def _sort_key(record):
        return (record["name"].lower(), record["owner"] or "", record["aux"] or 0)

    @staticmethod
    def fits_aux(record, aux):
        """Whether a preset may be loaded onto this aux."""
        return record["aux"] is None or record["aux"] == aux

    def reload(self):
        with self._lock:
            self.presets = self._load()
            self.revision += 1

    def list_presets(self):
        """Every preset, whoever owns it - the desktop's view."""
        with self._lock:
            return [self._copy(r) for r in sorted(self.presets, key=self._sort_key)]

    @staticmethod
    def can_see(record, username):
        return record["owner"] == username or record["public"] \
            or username in record["shared_with"]

    def visible_to(self, username, aux, any_aux=False):
        """The presets an account can see: its own, ones shared with it,
        and public ones. Only those saved from this aux, unless any_aux -
        then every one, this aux's first."""
        with self._lock:
            records = [
                r for r in sorted(self.presets, key=self._sort_key)
                if self.can_see(r, username)
                and (any_aux or self.fits_aux(r, aux))
            ]
        if any_aux:
            records.sort(key=lambda r: not self.fits_aux(r, aux))
        return [self._copy(r) for r in records]

    def get(self, preset_id):
        with self._lock:
            record = self._find(preset_id)
            return self._copy(record) if record else None

    def find_visible(self, username, name, aux):
        """A preset by name, for a phone that only knows names (builds from
        before owners). The account's own wins, then one shared with it,
        then a public one."""
        with self._lock:
            matches = [
                r for r in self.presets
                if r["name"] == name and self.can_see(r, username)
                and self.fits_aux(r, aux)
            ]

        for rank in (
            lambda r: r["owner"] == username,
            lambda r: username in r["shared_with"],
            lambda r: True,
        ):
            for record in matches:
                if rank(record):
                    return self._copy(record)

        return None

    def save_preset(self, owner, name, aux, aux_name, channels):
        """Saves over the owner's own preset of that name on that aux,
        keeping who it is shared with, or starts a new private one."""
        # channels: list of {"channel": int, "level": float|None, "pan": float|None}
        with self._lock:
            for record in self.presets:
                if record["owner"] == owner and record["name"] == name \
                        and record["aux"] == aux:
                    record["channels"] = channels
                    record["aux_name"] = aux_name
                    break
            else:
                record = {
                    "id": uuid.uuid4().hex,
                    "name": name,
                    "owner": owner,
                    "public": False,
                    "shared_with": [],
                    "aux": aux,
                    "aux_name": aux_name,
                    "channels": channels,
                }
                self.presets.append(record)

            self._save()
            return self._copy(record)

    def delete_preset(self, preset_id):
        with self._lock:
            record = self._find(preset_id)

            if record is not None:
                self.presets.remove(record)
                self._save()

    def set_public(self, preset_id, public):
        with self._lock:
            record = self._find(preset_id)

            if record is not None:
                record["public"] = bool(public)
                self._save()

    def set_shared_with(self, preset_id, usernames):
        with self._lock:
            record = self._find(preset_id)

            if record is not None:
                record["shared_with"] = sorted(
                    set(usernames) - {record["owner"]}
                )
                self._save()

    def forget_user(self, username):
        """Takes a deleted account out of every preset's share list. The
        presets it owned are kept - whoever they were shared with, or
        everyone if they were public, still has them - and the desktop
        can delete them."""
        with self._lock:
            changed = False

            for record in self.presets:
                if username in record["shared_with"]:
                    record["shared_with"].remove(username)
                    changed = True

            if changed:
                self._save()
