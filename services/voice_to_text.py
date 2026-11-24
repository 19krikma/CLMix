"""Voice to Text: listens to chosen input channels of an audio interface -
ASIO on Windows, whatever PortAudio offers elsewhere - and turns what is
said on each into text, handed to on_text one utterance at a time. The
main window puts that in the shared chat.

Speech is recognised on this machine by Vosk (https://alphacephei.com/vosk),
offline, one recogniser per channel sharing one model. Nothing is sent
anywhere; the only network use is the one-off model download Settings
offers.

Settings keep the config under "voice" - see normalize. Channels are
numbered from 1, as the interface and the console label them.

Bleed is the enemy here: a talkback or vocal mic hears the band too. So
each channel has a gate - audio below gate_db never reaches the
recogniser, and an utterance is closed off (and its text sent) as soon
as the channel has been quiet for HOLD_SECONDS.

sounddevice, numpy and vosk are optional: without them, or without
PortAudio on Linux (libportaudio2), the feature reports itself
unavailable and the rest of CLMix is unaffected.
"""

import hashlib
import json
import os
import queue
import re
import shutil
import ssl
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from collections import deque
from pathlib import Path

from services import voice_highlights
from services.log_store import log

# How many channels the interface is opened with - the most CLMix offers
# first, down to a stereo pair.
CHANNEL_COUNTS = (64, 32, 16, 2)
DEFAULT_CHANNEL_COUNT = 64

DEFAULT_GATE_DB = -45
MIN_GATE_DB = -80
MAX_GATE_DB = -10

# Quiet this long closes an utterance and sends it.
HOLD_SECONDS = 0.8
# Audio kept from before the gate opened, so the first syllable is heard.
PREROLL_SECONDS = 0.3
# One block of audio, as the interface hands it over.
BLOCK_SECONDS = 0.1
# Blocks a channel may fall behind by before new ones are dropped -
# better to lose a moment than to drift further and further behind.
MAX_BACKLOG_BLOCKS = 50

# Longest label a channel can have.
MAX_LABEL = 24

# Vosk's small models put out a stray word for a cough or a thump; a
# result that is only one of these is dropped.
FILLER = {"the", "a", "uh", "um", "huh", "oh", "hmm", "ah", "eh", "i", "and"}
# Words the recogniser is less sure of than this, on average, are dropped.
MIN_CONFIDENCE = 0.5

MODELS_ROOT = Path.home() / ".clmix_voice" / "models"
MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
# Every model Vosk publishes, with its language and size.
CATALOG_URL = "https://alphacephei.com/vosk/models/model-list.json"

_SSL_CONTEXT = None


def default_config():
    return {
        "enabled": False,
        # The input device as PortAudio names it, and its host API
        # ("ASIO", "ALSA", ...) - names rather than indexes, which move
        # when an interface is plugged in.
        "device": "",
        "host_api": "",
        "channel_count": DEFAULT_CHANNEL_COUNT,
        "gate_db": DEFAULT_GATE_DB,
        # A Vosk model folder; empty for the newest one under MODELS_ROOT.
        "model_dir": "",
        # {snapshot name: model folder} - a model of its own for a
        # snapshot, loaded when the console recalls it. See model_path.
        "snapshot_models": {},
        # {"<n>": {"listen": bool, "user": username or None, "label": str}}
        # - only channels with something set are kept.
        "channels": {},
        # {snapshot name: channels as above} - a setup of its own for a
        # snapshot, used in place of "channels" while it is live. See
        # for_snapshot.
        "snapshot_channels": {},
        "highlights": voice_highlights.default_config(),
    }


def normalize(raw):
    """A config as saved settings may hold it, made safe to use."""
    config = default_config()
    if not isinstance(raw, dict):
        return config

    config["enabled"] = bool(raw.get("enabled"))
    for key in ("device", "host_api", "model_dir"):
        if isinstance(raw.get(key), str):
            config[key] = raw[key]

    if raw.get("channel_count") in CHANNEL_COUNTS:
        config["channel_count"] = raw["channel_count"]

    gate = raw.get("gate_db")
    if isinstance(gate, (int, float)) and not isinstance(gate, bool):
        config["gate_db"] = round(min(max(gate, MIN_GATE_DB), MAX_GATE_DB))

    config["channels"] = _normalize_channels(raw.get("channels"))
    setups = raw.get("snapshot_channels")
    config["snapshot_channels"] = {
        snapshot: _normalize_channels(channels)
        for snapshot, channels in (setups.items() if isinstance(setups, dict)
                                   else ())
        if isinstance(snapshot, str) and snapshot and isinstance(channels, dict)
    }

    tied = raw.get("snapshot_models")
    config["snapshot_models"] = {
        snapshot: path
        for snapshot, path in (tied.items() if isinstance(tied, dict) else ())
        if isinstance(snapshot, str) and snapshot
        and isinstance(path, str) and path
    }

    config["highlights"] = voice_highlights.normalize(raw.get("highlights"))
    return config


def _normalize_channels(raw):
    channels = {}
    for key, item in (raw.items() if isinstance(raw, dict) else ()):
        try:
            number = int(key)
        except (TypeError, ValueError):
            continue
        if not 1 <= number <= max(CHANNEL_COUNTS) or not isinstance(item, dict):
            continue

        user = item.get("user")
        label = item.get("label")
        entry = {
            "listen": bool(item.get("listen")),
            "user": user if isinstance(user, str) and user else None,
            "label": " ".join(label.split())[:MAX_LABEL]
            if isinstance(label, str) else "",
        }
        if entry["listen"] or entry["user"] or entry["label"]:
            channels[str(number)] = entry
    return channels


def for_snapshot(config, snapshot):
    """config with the channel setup used on `snapshot`: its own if it
    has one, else the global one. The rest - interface, channel count,
    gate - is the same on every snapshot."""
    own = config.get("snapshot_channels", {}).get(snapshot) if snapshot else None
    if own is None:
        return config
    config = dict(config)
    config["channels"] = own
    return config


def channel(config, number):
    """One channel's settings, its defaults if it has none of its own."""
    return config["channels"].get(
        str(number), {"listen": False, "user": None, "label": ""}
    )


def listened(config):
    """The channel numbers listened to, within the channel count."""
    return sorted(
        int(key) for key, item in config["channels"].items()
        if item["listen"] and int(key) <= config["channel_count"]
    )


# --- The libraries ---

_sd = None
_np = None
_vosk = None
_import_error = None


def _load():
    """Imports what the feature needs, once. Returns None when all is
    well, else a sentence saying what is missing."""
    global _sd, _np, _vosk, _import_error

    if _sd is not None or _import_error is not None:
        return _import_error

    # sounddevice ships two PortAudio builds for Windows and loads the one
    # with ASIO only when asked, before it is first imported.
    if sys.platform == "win32":
        os.environ.setdefault("SD_ENABLE_ASIO", "1")

    try:
        import numpy
        import sounddevice
        import vosk
    except ImportError as ex:
        _import_error = (f"Voice to Text needs the {ex.name or 'audio'} "
                         "package, which this install of CLMix is missing.")
        return _import_error
    except OSError as ex:
        # sounddevice without the PortAudio library underneath it.
        _import_error = ("No audio library found"
                         + (" - install libportaudio2." if sys.platform
                            .startswith("linux") else f": {ex}"))
        return _import_error

    vosk.SetLogLevel(-1)
    _sd, _np, _vosk = sounddevice, numpy, vosk
    return None


def unavailable_reason():
    return _load()


def _init_com():
    """PortAudio's ASIO host loads the driver through COM, which it sets
    up only on the thread that first imported sounddevice. Opening an
    ASIO stream from any other thread fails with "Failed to load ASIO
    driver" (-9999) unless that thread has COM of its own."""
    if sys.platform != "win32":
        return
    import ctypes
    # COINIT_APARTMENTTHREADED, as PortAudio itself uses. Never undone -
    # the driver lives on after this thread ends.
    ctypes.windll.ole32.CoInitializeEx(None, 0x2)


# ALSA's virtual devices that go through the desktop's sound server,
# and what to call them.
_SOUND_SERVER_DEVICES = {
    "default": "System default microphone",
    "pipewire": "PipeWire (system sound)",
    "pulse": "PulseAudio (system sound)",
}


def _alsa_cards():
    """{card number: its long name} from /proc/asound/cards - "Focusrite
    Scarlett Solo USB" where PortAudio says "USB: Audio (hw:3,0)"."""
    cards = {}
    try:
        lines = Path("/proc/asound/cards").read_text().splitlines()
    except OSError:
        return cards
    for line, detail in zip(lines, lines[1:]):
        head = line.split("]:", 1)
        number = head[0].split("[", 1)[0].strip()
        if number.isdigit() and len(head) == 2:
            name = detail.strip().split(" at ", 1)[0].strip()
            cards[int(number)] = name or head[1].split(" - ", 1)[-1].strip()
    return cards


def _label(name, host_api, cards):
    """What to show for a device: PortAudio's name, made readable where
    it is ALSA's."""
    if host_api != "ALSA":
        return name
    if name in _SOUND_SERVER_DEVICES:
        return _SOUND_SERVER_DEVICES[name]
    match = re.search(r"\(hw:(\d+),(\d+)\)", name)
    if match and int(match.group(1)) in cards:
        label = cards[int(match.group(1))]
        if match.group(2) != "0":
            label += f" #{match.group(2)}"
        return f"{label} (hw:{match.group(1)},{match.group(2)})"
    return name


def input_devices():
    """[{"name", "label", "host_api", "channels", "index"}] for every
    device that can record, ASIO ones first. `name` is PortAudio's, what
    settings keep; `label` is what to show. Empty if the libraries are
    missing."""
    if _load() is not None:
        return []

    try:
        apis = _sd.query_hostapis()
        devices = _sd.query_devices()
    except Exception as ex:
        log("error", f"Voice to Text: could not list audio devices: {ex!r}")
        return []

    cards = _alsa_cards() if sys.platform.startswith("linux") else {}
    found = []
    for index, device in enumerate(devices):
        if device["max_input_channels"] <= 0:
            continue
        host_api = apis[device["hostapi"]]["name"]
        found.append({
            "name": device["name"],
            "label": _label(device["name"], host_api, cards),
            "host_api": host_api,
            "channels": device["max_input_channels"],
            "index": index,
        })

    # ASIO first; then the system default, which follows whatever the
    # desktop's sound settings pick; then the rest by name.
    found.sort(key=lambda d: (d["host_api"] != "ASIO", d["name"] != "default",
                              d["host_api"], d["label"].lower()))
    return found


def find_device(config):
    for device in input_devices():
        if device["name"] == config["device"] and \
                (not config["host_api"] or device["host_api"] == config["host_api"]):
            return device
    return None


def default_input_device():
    """The computer's own default recording device - its built-in or
    headset mic - or None."""
    if _load() is not None:
        return None
    try:
        index = _sd.default.device[0]
        if index is None or index < 0:
            index = _sd.query_devices(kind="input")["index"]
    except Exception:
        return None
    return next((d for d in input_devices() if d["index"] == index), None)


# Label on a mic test's chat messages.
MIC_TEST_LABEL = "Mic Test"


def mic_test_config(config, device):
    """config for trying Voice to Text on one microphone - its first
    channel, whatever the interface setup says. The model and highlight
    words are the operator's own, so the test shows what a show would."""
    test = dict(config)
    test.update(
        enabled=True, device=device["name"], host_api=device["host_api"],
        channel_count=min(CHANNEL_COUNTS), snapshot_channels={},
        channels={"1": {"listen": True, "user": None,
                        "label": MIC_TEST_LABEL}},
    )
    return test


# --- Models ---

def is_model(path):
    path = Path(path)
    return path.is_dir() and ((path / "conf").is_dir() or (path / "am").is_dir())


def model_path(config, snapshot=None):
    """The model folder to use on `snapshot`, or None if there is none:
    the snapshot's own if it has one that is still there, else the
    default - the chosen folder, or the newest one downloaded."""
    tied = config.get("snapshot_models", {}).get(snapshot) if snapshot else None
    if tied and is_model(tied):
        return tied

    if config["model_dir"]:
        return config["model_dir"] if is_model(config["model_dir"]) else None

    try:
        found = [p for p in MODELS_ROOT.iterdir()
                 if is_model(p) and not p.name.startswith(".")]
    except OSError:
        return None
    if not found:
        return None
    return str(max(found, key=lambda p: p.stat().st_mtime))


# --- Loading models ---
#
# A large model takes 6-7 GB once loaded and some 3 GB more while it
# loads, so: one load at a time, the loaded model kept and reused when a
# restart (a mic test, a channel change) asks for it again, and let go of
# when Voice to Text turns off.

_LOAD_LOCK = threading.Lock()
_loaded = {"path": None, "model": None}

# Peak memory while loading, as a multiple of the model folder's size -
# measured on vosk-model-ru-0.42 and vosk-model-en-us-0.42-gigaspeech.
LOAD_MEMORY_FACTOR = 2.7


def _get_model(path, wanted=lambda: True):
    """The Vosk model at `path` - the one already loaded if it is that
    one. None if `wanted()` turns false while waiting for another load."""
    with _LOAD_LOCK:
        if _loaded["path"] == path:
            return _loaded["model"]
        if not wanted():
            return None
        model = _vosk.Model(path)
        _loaded.update(path=path, model=model)
        return model


def _release_model():
    """Drops the kept model; its memory goes once no recogniser holds it."""
    with _LOAD_LOCK:
        _loaded.update(path=None, model=None)
    _trim()


def _trim():
    """Hands freed memory back to the system - glibc otherwise keeps a
    good part of a dropped model's."""
    import gc
    gc.collect()
    if sys.platform.startswith("linux"):
        try:
            import ctypes
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except (OSError, AttributeError):
            pass


def available_memory():
    """Bytes of memory free for use, or None if unknown."""
    try:
        if sys.platform == "win32":
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong),
                            ("load", ctypes.c_ulong),
                            ("total_phys", ctypes.c_ulonglong),
                            ("avail_phys", ctypes.c_ulonglong),
                            ("total_page", ctypes.c_ulonglong),
                            ("avail_page", ctypes.c_ulonglong),
                            ("total_virtual", ctypes.c_ulonglong),
                            ("avail_virtual", ctypes.c_ulonglong),
                            ("avail_extended", ctypes.c_ulonglong)]

            status = Status()
            status.length = ctypes.sizeof(Status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return status.avail_phys
            return None
        if sys.platform == "darwin":
            return None
        with open("/proc/meminfo") as meminfo:
            for line in meminfo:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, AttributeError):
        pass
    return None


def load_memory(path):
    """Roughly the memory loading the model at `path` takes at its peak."""
    return int(_folder_size(path) * LOAD_MEMORY_FACTOR)


class DownloadCancelled(Exception):
    pass


def _ssl_context():
    global _SSL_CONTEXT
    if _SSL_CONTEXT is None:
        import certifi
        _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
    return _SSL_CONTEXT


def _open(url, timeout=30):
    request = urllib.request.Request(url, headers={"User-Agent": "CLMix"})
    return urllib.request.urlopen(request, timeout=timeout,
                                  context=_ssl_context())


def fetch_catalog():
    """Every speech model Vosk offers for download that is still current,
    as [{"name", "lang", "language", "size", "size_text", "kind", "url",
    "md5"}], by language, small ones first. kind is "small", "medium" or
    "big".
    Blocking - run it off the Tkinter thread."""
    with _open(CATALOG_URL) as response:
        raw = json.load(response)

    models = []
    for item in raw if isinstance(raw, list) else ():
        if not isinstance(item, dict) or str(item.get("obsolete")) == "true":
            continue
        kind = item.get("type")
        url = item.get("url")
        if kind not in ("small", "big", "big-lgraph") or not isinstance(url, str) \
                or not url.startswith("https://"):
            continue
        models.append({
            "name": str(item.get("name") or ""),
            "lang": str(item.get("lang") or ""),
            "language": str(item.get("lang_text") or item.get("lang") or ""),
            "size": int(item.get("size") or 0),
            "size_text": str(item.get("size_text") or ""),
            # lgraph models sit between: a big model's acoustics with a
            # lighter, fixed vocabulary - around 130 MB.
            "kind": {"small": "small", "big-lgraph": "medium"}.get(kind, "big"),
            "url": url,
            "md5": str(item.get("md5") or ""),
        })

    models.sort(key=lambda m: (m["language"].lower(), m["kind"] != "small",
                               m["size"]))
    return models


def guess_lang(name):
    """A model's language code from its folder name, for one the catalog
    does not list: vosk-model-small-en-us-0.15 -> "en-us"."""
    parts = name.split("-")
    if parts[:2] == ["vosk", "model"]:
        parts = parts[2:]
    if parts[:1] == ["small"]:
        parts = parts[1:]
    code = []
    for part in parts:
        if not part or part[0].isdigit():
            break
        code.append(part)
    return "-".join(code)


def _folder_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def installed_models(config=None):
    """[{"name", "path", "size", "external"}] - every model under
    MODELS_ROOT, and the chosen folder if it is somewhere else."""
    found = []
    try:
        folders = sorted(MODELS_ROOT.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        folders = []
    for folder in folders:
        if is_model(folder) and not folder.name.startswith("."):
            found.append({"name": folder.name, "path": str(folder),
                          "size": _folder_size(folder), "external": False})

    chosen = (config or {}).get("model_dir")
    if chosen and is_model(chosen) and \
            not any(Path(m["path"]) == Path(chosen) for m in found):
        found.append({"name": Path(chosen).name, "path": chosen,
                      "size": _folder_size(chosen), "external": True})
    return found


def delete_model(path):
    """Removes a downloaded model. Only ever one under MODELS_ROOT -
    a folder chosen from elsewhere is the operator's own."""
    target = Path(path).resolve()
    if target.parent != MODELS_ROOT.resolve() or not is_model(target):
        raise ValueError(f"Not a downloaded model: {path}")
    shutil.rmtree(target)


def download_model(progress=None, url=MODEL_URL, md5=None, cancel=None):
    """Fetches and unpacks a Vosk model under MODELS_ROOT, returning its
    folder. progress(done_bytes, total_bytes) is called as it comes in;
    setting the `cancel` Event stops it (DownloadCancelled). Checked
    against md5 when the catalog gives one. Blocking - run it off the
    Tkinter thread."""
    MODELS_ROOT.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=MODELS_ROOT,
                                     prefix=".download-") as scratch:
        archive = Path(scratch) / "model.zip"
        digest = hashlib.md5()
        with _open(url) as response, open(archive, "wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while True:
                if cancel is not None and cancel.is_set():
                    raise DownloadCancelled()
                chunk = response.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)

        if md5 and digest.hexdigest() != md5.lower():
            raise ValueError("The download was damaged on the way - try again.")

        unpacked = Path(scratch) / "unpacked"
        with zipfile.ZipFile(archive) as bundle:
            root = unpacked.resolve()
            for member in bundle.namelist():
                # Refuse anything that would land outside the folder.
                if not (unpacked / member).resolve().is_relative_to(root):
                    raise ValueError(f"Unsafe path in model archive: {member}")
            bundle.extractall(unpacked)
        archive.unlink()

        candidates = [unpacked, *unpacked.iterdir()]
        model = next((p for p in candidates if is_model(p)), None)
        if model is None:
            raise ValueError("The download did not contain a speech model")

        name = model.name if model != unpacked else \
            url.rsplit("/", 1)[-1].removesuffix(".zip")
        target = MODELS_ROOT / name
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(model), target)
        # Newest wins in model_path.
        os.utime(target)
        return str(target)


# --- Listening ---

class VoiceEngine:
    """Runs Voice to Text for one config at a time. apply(config) starts,
    restarts or stops it to match; on_text(channel_number, text) is
    called from a worker thread for each finished utterance.

    `state` is "off", "starting", "running" or "error", with `message`
    saying more - read from the Tkinter thread for Settings' status line.
    """

    def __init__(self, on_text):
        self.on_text = on_text
        self.state = "off"
        self.message = "Off"
        self._lock = threading.Lock()
        self._stream = None
        self._channels = {}
        self._running_key = None
        self._generation = 0
        self._gate_db = DEFAULT_GATE_DB
        # The model wanted now: running, starting, or being switched to.
        self._model_path = None
        self._listening_text = ""
        self.last_text = ""
        # Live Text (ui/voice_live_window.py): while `watchers` is above
        # zero each channel also reports what it is hearing mid-sentence -
        # extra work otherwise skipped. `recent` keeps the latest finished
        # lines as {"time", "channel", "text"}, for a window opened later.
        self.watchers = 0
        self.recent = deque(maxlen=200)

    def levels(self):
        """{channel_number: dBFS} of the last block each listened channel
        heard, for Settings' level column."""
        return {number: worker.level for number, worker in
                list(self._channels.items())}

    def apply(self, config, snapshot=None):
        """Brings the engine in line with config, on the model for
        `snapshot` (the one live on the console, or None). A different
        model on the same audio setup is swapped in without stopping -
        see _switch_model; the same one changes nothing."""
        config = for_snapshot(config, snapshot)
        # The gate moves without a restart - it is only compared against.
        self._gate_db = config["gate_db"]
        for worker in list(self._channels.values()):
            worker.gate_db = config["gate_db"]

        channels = listened(config)
        key = (config["device"], config["host_api"], config["channel_count"],
               tuple(channels))
        path = model_path(config, snapshot)

        if not config["enabled"]:
            self.stop()
            _release_model()
            return

        if key == self._running_key:
            if self.state == "running":
                if path is not None and path != self._model_path:
                    self._switch_model(path)
                return
            if self.state == "starting" and path == self._model_path:
                return

        self.stop()
        self._running_key = key
        self._model_path = path
        self._set("starting", "Starting...")
        generation = self._generation
        threading.Thread(target=self._start,
                         args=(config, channels, path, generation),
                         name="VoiceStart", daemon=True).start()

    def model_in_use(self):
        """The model folder running, or about to - None while off."""
        return self._model_path if self.state in ("starting", "running") \
            else None

    def _switch_model(self, path):
        """Loads another model beside the running one and hands each
        channel a recogniser on it, which the channel takes up between
        utterances. Until it has loaded, the old model keeps listening."""
        self._model_path = path
        generation = self._generation
        listening = self._listening_text
        name = Path(path).name
        self._set("running", f"Loading {name}... (still listening)")

        def load():
            need = load_memory(path)
            free = available_memory()
            if free is not None and free < need:
                # Not room for both: the old model goes first, and the
                # channels pause until the new one is in.
                with self._lock:
                    if generation != self._generation \
                            or self._model_path != path:
                        return
                    dropped = [worker.pause()
                               for worker in self._channels.values()]
                # A block comes every BLOCK_SECONDS, and each answers then.
                deadline = time.monotonic() + 2
                for done in dropped:
                    done.wait(max(0, deadline - time.monotonic()))
                _release_model()
                self._set("running", f"Loading {name}... (paused - not "
                                     "enough memory to keep listening)")
                log("info", f"Voice to Text: {name} needs about "
                            f"{need / 1e9:.1f} GB to load and "
                            f"{free / 1e9:.1f} GB is free - pausing "
                            "while it loads")
            try:
                model = _get_model(
                    path, lambda: generation == self._generation
                    and self._model_path == path)
            except Exception as ex:
                if generation == self._generation and self._model_path == path:
                    log("error", f"Voice to Text: could not load {path}: {ex}")
                    self._set("error", f"Could not load {name}: {ex}")
                return
            if model is None:
                return
            with self._lock:
                # Overtaken by a stop, or by yet another model.
                if generation != self._generation or self._model_path != path:
                    return
                for worker in self._channels.values():
                    worker.swap(model)
            self._listening_text = listening
            self._set("running", f"{listening} - {name}")
            log("info", f"Voice to Text switched to {name}")

        threading.Thread(target=load, name="VoiceModelSwitch",
                         daemon=True).start()

    def stop(self):
        with self._lock:
            self._generation += 1
            stream, self._stream = self._stream, None
            channels, self._channels = self._channels, {}
            self._running_key = None
            self._model_path = None

        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as ex:
                log("warning", f"Voice to Text: closing the stream: {ex!r}")

        for worker in channels.values():
            worker.finish()

        self._set("off", "Off")

    def _set(self, state, message):
        self.state = state
        self.message = message

    def _fail(self, generation, message):
        if generation == self._generation:
            log("error", f"Voice to Text: {message}")
            self._set("error", message)
            self._running_key = None

    def _start(self, config, channels, path, generation):
        problem = _load()
        if problem is not None:
            return self._fail(generation, problem)
        if not channels:
            return self._fail(generation, "No channels are set to listen.")

        device = find_device(config)
        if device is None:
            return self._fail(
                generation, f"Audio device not found: {config['device']}"
                if config["device"] else "Choose an audio device.")

        too_high = [n for n in channels if n > device["channels"]]
        if too_high:
            return self._fail(
                generation,
                f"{device['name']} has {device['channels']} inputs - "
                f"channel {too_high[0]} is beyond them.")

        if path is None:
            return self._fail(generation,
                              "No speech model - download one below.")

        try:
            # Waits for any load under way; skipped if overtaken by then.
            model = _get_model(path, lambda: generation == self._generation)
        except Exception as ex:
            return self._fail(generation, f"Could not load the model: {ex}")
        if model is None:
            return

        try:
            info = _sd.query_devices(device["index"])
            rate = int(info["default_samplerate"])
        except Exception as ex:
            return self._fail(generation, f"Could not read the device: {ex}")

        # Recognised at 16 kHz where the interface rate divides down to
        # it cleanly - a third of the work at 48 kHz, and what the model
        # was trained on. Otherwise Vosk resamples it itself.
        factor = rate // 16000 if rate % 16000 == 0 and rate > 16000 else 1

        asio = device["host_api"] == "ASIO"
        if asio:
            _init_com()
            # Only the channels listened to are opened - the driver hands
            # over nothing else.
            columns = {n: i for i, n in enumerate(channels)}
            stream_channels = len(channels)
            extra = _sd.AsioSettings(channel_selectors=[n - 1 for n in channels])
        else:
            columns = {n: n - 1 for n in channels}
            stream_channels = max(channels)
            extra = None

        workers = {
            n: _ChannelWorker(n, model, rate // factor, factor, self._gate_db,
                              self._emit, live=lambda: self.watchers > 0)
            for n in channels
        }

        def callback(indata, frames, time_info, status):
            for number, column in columns.items():
                workers[number].push(indata[:, column].copy())

        try:
            stream = _sd.InputStream(
                device=device["index"], channels=stream_channels,
                samplerate=rate, dtype="int16",
                blocksize=int(rate * BLOCK_SECONDS), callback=callback,
                extra_settings=extra,
            )
            stream.start()
        except Exception as ex:
            for worker in workers.values():
                worker.finish()
            return self._fail(generation, f"Could not open {device['name']}: {ex}")

        with self._lock:
            if generation != self._generation:
                # Stopped or reconfigured while starting.
                stream.close()
                for worker in workers.values():
                    worker.finish()
                return
            self._stream = stream
            self._channels = workers

        for worker in workers.values():
            worker.start()

        names = ", ".join(str(n) for n in channels)
        self._listening_text = (f"Listening on {device['name']} - channel"
                                f"{'s' if len(channels) > 1 else ''} {names}")
        self._set("running", f"{self._listening_text} - {Path(path).name}")
        log("info", f"Voice to Text listening on {device['name']} "
                    f"({device['host_api']}, {rate} Hz), channels {names}")

    def partials(self):
        """{channel_number: text so far} for every channel mid-sentence -
        only kept while Live Text is watching."""
        return {number: worker.partial for number, worker in
                list(self._channels.items()) if worker.partial}

    def _emit(self, number, text):
        # For Settings' mic test, which shows what was last heard.
        self.last_text = text
        self.recent.append({"time": time.time(), "channel": number,
                            "text": text})
        try:
            self.on_text(number, text)
        except Exception as ex:
            log("error", f"Voice to Text: sending channel {number}: {ex!r}")


class _ChannelWorker(threading.Thread):
    """One channel's recogniser, fed from the audio callback through a
    queue so the callback never waits on speech recognition. Vosk lets go
    of the GIL while it works, so channels run side by side."""

    def __init__(self, number, model, rate, factor, gate_db, emit,
                 live=lambda: False):
        super().__init__(name=f"Voice{number}", daemon=True)
        self.number = number
        # Whether to keep `partial` - the sentence so far - up to date.
        self.live = live
        self.partial = ""
        self.factor = factor
        self.gate_db = gate_db
        self.emit = emit
        self.level = -120.0
        self.rate = rate
        self._queue = queue.Queue(maxsize=MAX_BACKLOG_BLOCKS)
        self._recognizer = self._recognizer_for(model)
        # A recogniser on another model, waiting for a pause - see swap.
        self._next = None
        # Set by pause, for the run thread to answer.
        self._pause_done = None
        self._preroll = deque(maxlen=max(1, round(PREROLL_SECONDS / BLOCK_SECONDS)))
        self._hold_blocks = max(1, round(HOLD_SECONDS / BLOCK_SECONDS))
        self._dropped = 0

    def _recognizer_for(self, model):
        recognizer = _vosk.KaldiRecognizer(model, self.rate)
        recognizer.SetWords(True)
        return recognizer

    def swap(self, model):
        """Moves this channel onto another model at its next pause, so
        nothing said is split between the two."""
        self._next = self._recognizer_for(model)

    def pause(self):
        """Has this channel drop its recogniser, so its model can be let
        go of - what it was saying is sent first. It listens again once
        swap hands it a new one. Returns an Event set once dropped."""
        done = threading.Event()
        self._pause_done = done
        return done

    def push(self, block):
        try:
            self._queue.put_nowait(block)
        except queue.Full:
            self._dropped += 1

    def finish(self):
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            # Make room: the worker is behind, and stopping matters more.
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self._queue.put_nowait(None)

    def run(self):
        quiet = self._hold_blocks
        speaking = False
        warned_at = 0

        while True:
            block = self._queue.get()
            if block is None:
                break

            samples = block.astype(_np.float32)
            rms = float(_np.sqrt(_np.mean(samples * samples))) if len(samples) else 0.0
            self.level = 20 * _np.log10(rms / 32768) if rms > 0 else -120.0

            if self.factor > 1:
                usable = len(block) - len(block) % self.factor
                block = samples[:usable].reshape(-1, self.factor).mean(axis=1) \
                    .astype(_np.int16)
            data = block.tobytes()

            quiet = 0 if self.level >= self.gate_db else quiet + 1

            pause, self._pause_done = self._pause_done, None
            if pause is not None:
                if speaking and self._recognizer is not None:
                    self._send(self._recognizer.FinalResult())
                self._recognizer = self._next = None
                pause.set()

            if self._next is not None and (not speaking
                                           or self._recognizer is None):
                self._recognizer, self._next = self._next, None
            if self._recognizer is None:
                # Paused for a model change - see pause.
                speaking = False
                self.partial = ""
                self._preroll.clear()
                continue

            if quiet < self._hold_blocks:
                if not speaking:
                    speaking = True
                    for earlier in self._preroll:
                        self._accept(earlier)
                    self._preroll.clear()
                self._accept(data)
            else:
                if speaking:
                    speaking = False
                    self._send(self._recognizer.FinalResult())
                self._preroll.append(data)

            if self._dropped and time.monotonic() - warned_at > 30:
                warned_at = time.monotonic()
                log("warning", f"Voice to Text: channel {self.number} fell "
                               f"behind and skipped {self._dropped} blocks")
                self._dropped = 0

        if speaking and self._recognizer is not None:
            self._send(self._recognizer.FinalResult())

    def _accept(self, data):
        if self._recognizer.AcceptWaveform(data):
            self._send(self._recognizer.Result())
        elif self.live():
            try:
                partial = json.loads(self._recognizer.PartialResult())
            except (TypeError, ValueError):
                partial = {}
            self.partial = " ".join((partial.get("partial") or "").split())

    def _send(self, result):
        self.partial = ""
        text = clean_result(result)
        if text:
            self.emit(self.number, text)


def clean_result(result):
    """The text worth sending from one Vosk result, or None."""
    try:
        parsed = json.loads(result)
    except (TypeError, ValueError):
        return None

    words = parsed.get("result") or []
    if words:
        confidence = sum(w.get("conf", 1.0) for w in words) / len(words)
        if confidence < MIN_CONFIDENCE:
            return None

    text = " ".join((parsed.get("text") or "").split())
    if not text or all(word in FILLER for word in text.split()):
        return None

    return text[0].upper() + text[1:]
