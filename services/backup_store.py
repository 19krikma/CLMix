import json
from collections import namedtuple
from datetime import datetime
from pathlib import Path

from services import autostart
from services.log_store import log
from services.voice_vocabulary import CORRECTIONS_FILE

USERS_PATH = Path.home() / ".clmix_users.json"
PRESETS_PATH = Path.home() / ".clmix_presets.json"
SETTINGS_PATH = Path.home() / ".clmix.json"
BACKUPS_DIR = Path.home() / ".clmix_backups"

BACKUP_FILENAME_FORMAT = "%Y%m%d_%H%M%S_%f"

BackupInfo = namedtuple("BackupInfo", ["path", "created_at", "included"])


class BackupStore:
    """Point-in-time snapshots of everything CLMix itself keeps.

    Bundles the JSON files UserStore/PresetStore/MainWindow persist to
    the home directory into one timestamped file under BACKUPS_DIR, so
    an operator can capture known-good state (e.g. before updating the
    app) without needing to know where those files live or hand-copy
    them individually:

        users              accounts (UserStore)
        presets            presets (PresetStore)
        settings           ~/.clmix.json - every setting MainWindow keeps:
                           connections, AUX, SOS, chat names, Companion,
                           Voice to Text, Live Text on the web, ...
        voice_corrections  Voice to Text's own corrections
                           (services/voice_vocabulary.py)
        startup            Launch on Startup, which lives in the desktop's
                           login list rather than in settings (see
                           services/autostart.py) - kept as a flag

    Not kept: downloaded speech models (large, and downloaded again on
    demand), logs and captures, and Mixer Backup's console backups, which
    are their own files in their own folder.

    Every new backup captures all of the above. Older ones hold fewer -
    each records which in "included", and restore writes back only those,
    so restoring an old backup leaves anything newer as it is.
    """

    # Sources that are a JSON file each.
    SOURCES = {
        "users": USERS_PATH,
        "presets": PRESETS_PATH,
        "settings": SETTINGS_PATH,
        "voice_corrections": CORRECTIONS_FILE,
    }

    # Every key a backup can hold, file or not, in the order shown.
    KEYS = (*SOURCES, "startup")

    LABELS = {
        "users": "Accounts",
        "presets": "Presets",
        "settings": "Settings",
        "voice_corrections": "Voice corrections",
        "startup": "Launch on Startup",
    }

    # What a backup written before "included" existed always held.
    LEGACY_KEYS = ("users", "presets", "settings")

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

    def backup_now(self):
        include = list(self.KEYS)

        directory = self.resolve_dir()

        now = datetime.now()
        data = {"created_at": now.isoformat(), "included": include}

        for key in self.SOURCES:
            data[key] = self._read_json(self.SOURCES[key])

        data["startup"] = {"launch_on_startup": autostart.is_enabled()}

        filename = f"clmix_backup_{now.strftime(BACKUP_FILENAME_FORMAT)}.json"
        path = directory / filename

        try:
            with open(path, "w", encoding="utf-8") as f:
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

        included = set(data["included"]) if "included" in data \
            else set(self.LEGACY_KEYS)
        restored = set()

        for key in included:
            if key == "startup":
                if self._restore_startup(data.get(key)):
                    restored.add(key)
                continue

            target = self.SOURCES.get(key)
            if target is None:
                # Written by a newer CLMix, with something this one does
                # not know - left alone rather than guessed at.
                log("warning", f"Backup holds {key!r}, unknown here - skipped")
                continue

            value = data.get(key)

            try:
                if value is None:
                    if target.exists():
                        target.unlink()
                else:
                    # Voice corrections live in a folder of their own,
                    # which a fresh machine may not have yet.
                    target.parent.mkdir(parents=True, exist_ok=True)
                    # ASCII-escaped, as the app itself writes them: the
                    # settings file is read back with the platform's
                    # default encoding, which UTF-8 text would trip up.
                    with open(target, "w", encoding="utf-8") as f:
                        json.dump(value, f, indent=2)
            except OSError as ex:
                log("error", f"Failed to restore {key} from backup: {ex!r}")
                continue

            restored.add(key)

        log("info", f"Restored from backup ({', '.join(sorted(restored))}): {path}")
        return restored

    @staticmethod
    def _restore_startup(value):
        """Puts Launch on Startup back as it was. True if it now is."""
        if not isinstance(value, dict) or "launch_on_startup" not in value:
            return False

        wanted = bool(value["launch_on_startup"])

        if wanted == autostart.is_enabled():
            return True

        if not autostart.is_supported():
            return False

        try:
            autostart.set_enabled(wanted)
        except autostart.AutostartError as ex:
            log("error", f"Failed to restore Launch on Startup: {ex}")
            return False

        return True

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
            # captured these three.
            included = set(self.LEGACY_KEYS)
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
        # UTF-8, not the platform default: voice corrections can be
        # Ukrainian, and on Windows the default would fail to read them -
        # recording the file as missing, so a restore would delete it.
        # The other files are plain ASCII, which reads the same either way.
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None
