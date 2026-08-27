import json
from collections import namedtuple
from datetime import datetime
from pathlib import Path

from services.log_store import log

USERS_PATH = Path.home() / ".clmix_users.json"
PRESETS_PATH = Path.home() / ".clmix_presets.json"
SETTINGS_PATH = Path.home() / ".clmix.json"
BACKUPS_DIR = Path.home() / ".clmix_backups"

BACKUP_FILENAME_FORMAT = "%Y%m%d_%H%M%S_%f"

BackupInfo = namedtuple("BackupInfo", ["path", "created_at", "included"])


class BackupStore:
    """Point-in-time snapshots of accounts, presets, and app settings.

    Bundles the JSON files UserStore/PresetStore/MainWindow persist to
    the home directory into one timestamped file under BACKUPS_DIR, so
    an operator can capture known-good state (e.g. before updating the
    app) without needing to know where those files live or hand-copy
    them individually. Which of the three are captured is selectable
    per backup (see backup_now's include argument) rather than always
    all-or-nothing.
    """

    SOURCES = {
        "users": USERS_PATH,
        "presets": PRESETS_PATH,
        "settings": SETTINGS_PATH,
    }

    LABELS = {
        "users": "Accounts",
        "presets": "Presets",
        "settings": "Settings",
    }

    DEFAULT_BACKUPS_DIR = BACKUPS_DIR

    def __init__(self, backups_dir=None):
        self.backups_dir = Path(backups_dir) if backups_dir else self.DEFAULT_BACKUPS_DIR

    def set_backups_dir(self, path):
        self.backups_dir = Path(path) if path else self.DEFAULT_BACKUPS_DIR

    def resolve_dir(self):
        """The directory backups actually get written to/read from.

        Normally the configured backups_dir, but falls back to
        DEFAULT_BACKUPS_DIR if that's no longer reachable (e.g. a
        removable drive or network share the operator picked isn't
        currently mounted) - the operator's chosen path is left
        untouched in settings so it resumes being used once it's
        reachable again.
        """
        try:
            self.backups_dir.mkdir(parents=True, exist_ok=True)
            return self.backups_dir
        except OSError:
            self.DEFAULT_BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
            return self.DEFAULT_BACKUPS_DIR

    def backup_now(self, include=None):
        include = list(self.SOURCES) if include is None else list(include)

        directory = self.resolve_dir()

        now = datetime.now()
        data = {"created_at": now.isoformat(), "included": include}

        for key in include:
            data[key] = self._read_json(self.SOURCES[key])

        filename = f"clmix_backup_{now.strftime(BACKUP_FILENAME_FORMAT)}.json"
        path = directory / filename

        try:
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
        except OSError as ex:
            log("error", f"Failed to write backup: {ex!r}")
            return None

        log("info", f"Backup created ({', '.join(include)}): {path}")
        return path

    def restore(self, path):
        """Writes the sources captured in the backup at path back to
        their live locations (UserStore/PresetStore/MainWindow reload
        from disk separately - see their reload() methods - since this
        only touches the files).

        A source whose value was None at backup time (the source file
        didn't exist, or wasn't valid JSON) is restored by removing the
        live file, matching the state actually captured.

        Returns the set of keys successfully restored.
        """
        data = self._read_json(path)

        if data is None:
            log("error", f"Failed to read backup for restore: {path}")
            return set()

        included = set(data["included"]) if "included" in data else set(self.SOURCES)
        restored = set()

        for key in included:
            target = self.SOURCES[key]
            value = data.get(key)

            try:
                if value is None:
                    if target.exists():
                        target.unlink()
                else:
                    with open(target, "w") as f:
                        json.dump(value, f, indent=2)
            except OSError as ex:
                log("error", f"Failed to restore {key} from backup: {ex!r}")
                continue

            restored.add(key)

        log("info", f"Restored from backup ({', '.join(sorted(restored))}): {path}")
        return restored

    def remove_backup(self, path):
        try:
            path.unlink()
        except OSError as ex:
            log("error", f"Failed to remove backup: {ex!r}")
            return False

        log("info", f"Backup removed: {path}")
        return True

    def list_backups(self):
        try:
            paths = list(self.resolve_dir().glob("clmix_backup_*.json"))
        except OSError:
            return []

        backups = [self._describe(path) for path in paths]
        backups.sort(key=lambda backup: backup.created_at, reverse=True)

        return backups

    def _describe(self, path):
        data = self._read_json(path)

        if data and "included" in data:
            included = set(data["included"])
        elif data:
            # Backups written before selective backup existed always
            # captured all three sources.
            included = set(self.SOURCES)
        else:
            included = set()

        if data and "created_at" in data:
            try:
                created_at = datetime.fromisoformat(data["created_at"])
            except ValueError:
                created_at = datetime.fromtimestamp(path.stat().st_mtime)
        else:
            created_at = datetime.fromtimestamp(path.stat().st_mtime)

        return BackupInfo(path=path, created_at=created_at, included=included)

    @staticmethod
    def _read_json(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None
