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

import json
import os
import queue
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
MODEL_SIZE_MB = 40

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
        # {"<n>": {"listen": bool, "user": username or None, "label": str}}
        # - only channels with something set are kept.
        "channels": {},
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

    channels = raw.get("channels")
    for key, item in (channels.items() if isinstance(channels, dict) else ()):
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
            config["channels"][str(number)] = entry

    config["highlights"] = voice_highlights.normalize(raw.get("highlights"))
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


def input_devices():
    """[{"name", "host_api", "channels", "index"}] for every device that
    can record, ASIO ones first. Empty if the libraries are missing."""
    if _load() is not None:
        return []

    try:
        apis = _sd.query_hostapis()
        devices = _sd.query_devices()
    except Exception as ex:
        log("error", f"Voice to Text: could not list audio devices: {ex!r}")
        return []

    found = []
    for index, device in enumerate(devices):
        if device["max_input_channels"] <= 0:
            continue
        found.append({
            "name": device["name"],
            "host_api": apis[device["hostapi"]]["name"],
            "channels": device["max_input_channels"],
            "index": index,
        })

    found.sort(key=lambda d: (d["host_api"] != "ASIO", d["host_api"], d["name"]))
    return found


def find_device(config):
    for device in input_devices():
        if device["name"] == config["device"] and \
                (not config["host_api"] or device["host_api"] == config["host_api"]):
            return device
    return None


# --- Models ---

def is_model(path):
    path = Path(path)
    return path.is_dir() and ((path / "conf").is_dir() or (path / "am").is_dir())


def model_path(config):
    """The model folder to use, or None if there is none."""
    if config["model_dir"]:
        return config["model_dir"] if is_model(config["model_dir"]) else None

    try:
        found = [p for p in MODELS_ROOT.iterdir() if is_model(p)]
    except OSError:
        return None
    if not found:
        return None
    return str(max(found, key=lambda p: p.stat().st_mtime))


def download_model(progress=None, url=MODEL_URL):
    """Fetches and unpacks a Vosk model under MODELS_ROOT, returning its
    folder. progress(done_bytes, total_bytes) is called as it comes in.
    Blocking - run it off the Tkinter thread."""
    global _SSL_CONTEXT
    if _SSL_CONTEXT is None:
        import certifi
        _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

    MODELS_ROOT.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "CLMix"})

    with tempfile.TemporaryDirectory(dir=MODELS_ROOT) as scratch:
        archive = Path(scratch) / "model.zip"
        with urllib.request.urlopen(request, timeout=30,
                                    context=_SSL_CONTEXT) as response, \
                open(archive, "wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = response.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)

        unpacked = Path(scratch) / "unpacked"
        with zipfile.ZipFile(archive) as bundle:
            root = unpacked.resolve()
            for member in bundle.namelist():
                # Refuse anything that would land outside the folder.
                if not (unpacked / member).resolve().is_relative_to(root):
                    raise ValueError(f"Unsafe path in model archive: {member}")
            bundle.extractall(unpacked)

        candidates = [unpacked, *unpacked.iterdir()]
        model = next((p for p in candidates if is_model(p)), None)
        if model is None:
            raise ValueError("The download did not contain a speech model")

        target = MODELS_ROOT / (model.name if model != unpacked else "model")
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

    def levels(self):
        """{channel_number: dBFS} of the last block each listened channel
        heard, for Settings' level column."""
        return {number: worker.level for number, worker in
                list(self._channels.items())}

    def apply(self, config):
        # The gate moves without a restart - it is only compared against.
        self._gate_db = config["gate_db"]
        for worker in list(self._channels.values()):
            worker.gate_db = config["gate_db"]

        channels = listened(config)
        key = (config["device"], config["host_api"], config["channel_count"],
               tuple(channels), model_path(config))

        if not config["enabled"]:
            self.stop()
            return
        if key == self._running_key and self.state in ("starting", "running"):
            return

        self.stop()
        self._running_key = key
        self._set("starting", "Starting...")
        generation = self._generation
        threading.Thread(target=self._start, args=(config, channels, generation),
                         name="VoiceStart", daemon=True).start()

    def stop(self):
        with self._lock:
            self._generation += 1
            stream, self._stream = self._stream, None
            channels, self._channels = self._channels, {}
            self._running_key = None

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

    def _start(self, config, channels, generation):
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

        path = model_path(config)
        if path is None:
            return self._fail(generation,
                              "No speech model - download one below.")

        try:
            model = _vosk.Model(path)
        except Exception as ex:
            return self._fail(generation, f"Could not load the model: {ex}")

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
                              self._emit)
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
        self._set("running", f"Listening on {device['name']} - "
                             f"channel{'s' if len(channels) > 1 else ''} {names}")
        log("info", f"Voice to Text listening on {device['name']} "
                    f"({device['host_api']}, {rate} Hz), channels {names}")

    def _emit(self, number, text):
        try:
            self.on_text(number, text)
        except Exception as ex:
            log("error", f"Voice to Text: sending channel {number}: {ex!r}")


class _ChannelWorker(threading.Thread):
    """One channel's recogniser, fed from the audio callback through a
    queue so the callback never waits on speech recognition. Vosk lets go
    of the GIL while it works, so channels run side by side."""

    def __init__(self, number, model, rate, factor, gate_db, emit):
        super().__init__(name=f"Voice{number}", daemon=True)
        self.number = number
        self.factor = factor
        self.gate_db = gate_db
        self.emit = emit
        self.level = -120.0
        self._queue = queue.Queue(maxsize=MAX_BACKLOG_BLOCKS)
        self._recognizer = _vosk.KaldiRecognizer(model, rate)
        self._recognizer.SetWords(True)
        self._preroll = deque(maxlen=max(1, round(PREROLL_SECONDS / BLOCK_SECONDS)))
        self._hold_blocks = max(1, round(HOLD_SECONDS / BLOCK_SECONDS))
        self._dropped = 0

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

        if speaking:
            self._send(self._recognizer.FinalResult())

    def _accept(self, data):
        if self._recognizer.AcceptWaveform(data):
            self._send(self._recognizer.Result())

    def _send(self, result):
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
