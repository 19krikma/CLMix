import json
from pathlib import Path

from services.log_store import log

PRESETS_PATH = Path.home() / ".clmix_presets.json"


class PresetStore:
    """Named snapshots of every channel's level+pan for a single aux.

    Captured/recalled from the phone apps via RemoteServer's save_preset/
    load_preset actions; the desktop UI (PresetsWindow) only manages the
    list - view names and delete - mirroring how AccessWindow manages
    UserStore's accounts.
    """

    def __init__(self, path=PRESETS_PATH):
        self.path = path
        self.presets = self._load()

    def _load(self):
        try:
            with open(self.path) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self):
        try:
            with open(self.path, "w") as f:
                json.dump(self.presets, f, indent=2)
        except OSError as ex:
            log("error", f"Failed to save presets: {ex!r}")

    def list_presets(self):
        return sorted(self.presets.items())

    def get(self, name):
        return self.presets.get(name)

    def save_preset(self, name, channels):
        # channels: list of {"channel": int, "level": float|None, "pan": float|None}
        self.presets[name] = {"channels": channels}
        self._save()

    def delete_preset(self, name):
        if name in self.presets:
            del self.presets[name]
            self._save()
