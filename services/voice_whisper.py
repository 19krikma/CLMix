"""Voice to Text's Whisper engine: faster-whisper
(https://github.com/SYSTRAN/faster-whisper) for the words, Silero VAD -
the copy faster-whisper ships - for when someone is speaking.

Built for a worship leader's mic: what matters is that the projection
team sees "Chorus in 2" or "Matthew 4:5" while it is still useful, so the
sentence so far is re-read every PARTIAL_SECONDS as it grows, and the
final text is sent the moment the leader pauses rather than at the end of
a sentence.

    audio callback -> ChannelWorker (one per channel: level, gate, VAD,
                      cutting speech into utterances)
                   -> Decoder (one for all channels: Whisper, finished
                      utterances first, then the latest partial of each)

The model runs on an NVIDIA GPU through CUDA where there is one (medium
in float16 needs about 1.5 GB of video memory), else on the CPU in int8 -
which works, but a medium model is then seconds behind, so small or base
suits a CPU better.

ctranslate2, faster_whisper and onnxruntime are optional, like the rest
of Voice to Text: without them this engine reports itself unavailable.
"""

import glob
import math
import os
import queue
import sys
import threading
import time
from collections import deque
from pathlib import Path

from services.log_store import log

# The sizes offered, smallest first: (name, download size, what it suits).
MODELS = (
    ("tiny", "75 MB", "fastest, least accurate"),
    ("base", "145 MB", "a slow CPU"),
    ("small", "485 MB", "a CPU, or a small GPU"),
    ("medium", "1.5 GB", "an NVIDIA GPU - recommended"),
)
MODEL_NAMES = tuple(name for name, _size, _note in MODELS)
DEFAULT_MODEL = "medium"

DEVICES = ("auto", "cuda", "cpu")
DEFAULT_DEVICE = "auto"

# Languages Whisper may pick between for each utterance - most churches
# need one or two, and narrowing it stops a short English phrase being
# taken for Welsh. Empty means any language Whisper knows.
LANGUAGES = (("en", "English"), ("ru", "Russian"), ("uk", "Ukrainian"),
             ("es", "Spanish"), ("pt", "Portuguese"), ("de", "German"),
             ("fr", "French"), ("ro", "Romanian"), ("ko", "Korean"),
             ("zh", "Chinese"))
DEFAULT_LANGUAGES = ("en", "ru")

MODELS_ROOT = Path.home() / ".clmix_voice" / "whisper"

RATE = 16000
# Silero's frame at 16 kHz: 32 ms.
FRAME = 512
FRAME_SECONDS = FRAME / RATE
# Silero is given the end of the frame before as context.
CONTEXT = 64

# Speech starts above START_PROB and continues until the probability has
# been under END_PROB for END_SILENCE.
START_PROB = 0.5
END_PROB = 0.35
END_SILENCE = 0.5
# Audio kept from before speech was heard, so the first word is whole.
PREROLL_SECONDS = 0.3
# Utterances with less speech than this are a cough, not words.
MIN_SPEECH_SECONDS = 0.25
# A leader talking over the band may never pause long enough: past
# SOFT_CUT_SECONDS a short pause closes the utterance, and at
# MAX_UTTERANCE_SECONDS it is cut regardless.
SOFT_CUT_SECONDS = 7.0
SOFT_CUT_SILENCE = 0.2
MAX_UTTERANCE_SECONDS = 14.0

# How much new speech between two readings of the sentence so far.
PARTIAL_SECONDS = 0.4
# And how much speech there must be for the first.
FIRST_PARTIAL_SECONDS = 0.5
# Language is only judged from this much speech on.
DETECT_SECONDS = 1.0

# Blocks from the audio callback a channel may hold before new ones are
# dropped - only reached if a channel's VAD thread is starved.
MAX_BACKLOG_BLOCKS = 100

# Whisper's well-known inventions on noise and music, which slip past the
# VAD now and then - a result that is only one of these is dropped.
_HALLUCINATIONS = (
    "thank you for watching", "thanks for watching", "please subscribe",
    "subscribe to", "like and subscribe", "see you in the next video",
    "thank you.", "thank you", "bye.", "you", "amara.org",
    "продолжение следует", "спасибо за просмотр", "подписывайтесь",
    "субтитры", "редактор субтитров", "корректор", "спасибо.",
    "dimatorzok",
)

# Words Whisper is nudged towards, by language - a prompt written the
# way the projection team wants to read them, digits included.
_PROMPTS = {
    "en": "Verse 2, Chorus, Pre-Chorus, Bridge, Tag, Outro, Interlude. "
          "Matthew 4:5, John 3:16, 1 Corinthians 13, Psalm 23.",
    "ru": "Куплет 2, припев, предприпев, бридж, проигрыш, концовка. "
          "От Матфея 4:5, От Иоанна 3:16, 1 Коринфянам 13, Псалом 22.",
    "uk": "Куплет 2, приспів, передприспів, бридж, програш, кінцівка. "
          "Від Матвія 5:5, Від Івана 3:16, 1 до коринтян 13, Псалом 23.",
}

# --- The libraries ---

_fw = None
_ct2 = None
_ort = None
_np = None
_import_error = None


def _load():
    """Imports what the engine needs, once. None when all is well, else
    a sentence saying what is missing."""
    global _fw, _ct2, _ort, _np, _import_error
    if _fw is not None or _import_error is not None:
        return _import_error

    # The Hub's progress bars write to stderr, which a windowed build
    # does not have.
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    _prepare_cuda()
    try:
        import ctranslate2
        import faster_whisper
        import numpy
        import onnxruntime
    except ImportError as ex:
        _import_error = (f"Whisper needs the {ex.name or 'faster-whisper'} "
                         "package, which this install of CLMix is missing - "
                         "install faster-whisper, or pick Vosk.")
        return _import_error
    except OSError as ex:
        _import_error = f"Whisper could not load its libraries: {ex}"
        return _import_error

    _fw, _ct2, _ort, _np = faster_whisper, ctranslate2, onnxruntime, numpy
    return None


def unavailable_reason():
    return _load()


def _prepare_cuda():
    """Points the loader at NVIDIA's cuBLAS and cuDNN where they came as
    pip packages (nvidia-cublas-cu12, nvidia-cudnn-cu12) rather than
    with the CUDA toolkit - CTranslate2 only finds them on the system's
    search path otherwise."""
    roots = [Path(p) / "nvidia" for p in sys.path if p]
    if getattr(sys, "frozen", False):
        roots.append(Path(getattr(sys, "_MEIPASS", "")) / "nvidia")
    if sys.platform == "win32":
        for root in roots:
            for folder in glob.glob(str(root / "*" / "bin")):
                try:
                    os.add_dll_directory(folder)
                except OSError:
                    continue
                os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")
    elif sys.platform.startswith("linux"):
        import ctypes
        for root in roots:
            for pattern in ("cublas/lib/libcublasLt.so*", "cublas/lib/libcublas.so*",
                            "cudnn/lib/libcudnn*.so*"):
                for library in sorted(glob.glob(str(root / pattern))):
                    try:
                        ctypes.CDLL(library, mode=ctypes.RTLD_GLOBAL)
                    except OSError:
                        pass


# What CTranslate2 needs on the GPU besides the driver. A missing one is
# not an error it raises but, on first use, a message and the process
# ending - so each is tried here before CUDA is.
if sys.platform == "win32":
    _CUDA_LIBRARIES = (("cublas64_12.dll", "cuBLAS 12"),
                       ("cudnn_ops64_9.dll", "cuDNN 9"))
else:
    _CUDA_LIBRARIES = (("libcublas.so.12", "cuBLAS 12"),
                       ("libcudnn_ops.so.9", "cuDNN 9"))


def missing_cuda_library():
    """The name of the first GPU library that will not load, or None."""
    import ctypes
    for library, name in _CUDA_LIBRARIES:
        try:
            ctypes.CDLL(library)
        except OSError:
            return name
    return None


def cuda_devices():
    """How many CUDA GPUs CTranslate2 can see - 0 without the library."""
    if _load() is not None:
        return 0
    try:
        return _ct2.get_cuda_device_count()
    except Exception:
        return 0


# --- Models ---

def is_downloaded(name):
    if _load() is not None:
        return False
    try:
        _fw.download_model(name, cache_dir=str(MODELS_ROOT),
                           local_files_only=True)
        return True
    except Exception:
        return False


def download(name):
    """Fetches a model from Hugging Face into MODELS_ROOT, returning its
    folder. Blocking - run it off the Tkinter thread."""
    problem = _load()
    if problem:
        raise RuntimeError(problem)
    MODELS_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        # Already here: no need to ask the Hub, which a church without
        # internet could not.
        return _fw.download_model(name, cache_dir=str(MODELS_ROOT),
                                  local_files_only=True)
    except Exception:
        return _fw.download_model(name, cache_dir=str(MODELS_ROOT))


def model_size(name):
    return next((size for model, size, _note in MODELS if model == name), "")


class Transcriber:
    """One loaded Whisper model and where it runs. Used from the Decoder
    thread only - one call at a time."""

    def __init__(self, name, device_wanted):
        self.name = name
        self.note = ""
        path = download(name)

        tries = []
        gpus = cuda_devices() if device_wanted in ("auto", "cuda") else 0
        missing = missing_cuda_library() if gpus else None
        if missing:
            self.note = f"{missing} not found, so not on the GPU"
            log("warning", f"Voice to Text: an NVIDIA GPU is here but {missing} "
                           "is not - Whisper runs on the CPU. Install "
                           "nvidia-cublas-cu12 and nvidia-cudnn-cu12 (pip), "
                           "or the CUDA 12 toolkit and cuDNN 9.")
            gpus = 0
        if gpus:
            supported = _ct2.get_supported_compute_types("cuda")
            compute = next((c for c in ("float16", "int8_float16", "int8")
                            if c in supported), "float32")
            tries.append(("cuda", compute))
        elif device_wanted == "cuda":
            self.note = "no NVIDIA GPU found"
        tries.append(("cpu", "int8"))

        error = None
        for device, compute in tries:
            try:
                model = _fw.WhisperModel(
                    path, device=device, compute_type=compute,
                    cpu_threads=max(1, min(8, (os.cpu_count() or 4) - 1)))
                self.model, self.device, self.compute = model, device, compute
                # cuDNN is only loaded on first use - fail now, while the
                # CPU is still there to fall back on, not mid-service.
                self.transcribe(_np.zeros(RATE, dtype=_np.float32), "en", final=False)
                return
            except Exception as ex:
                error = ex
                if device == "cuda":
                    self.note = f"GPU failed: {ex}"
                    log("warning", f"Voice to Text: Whisper on the GPU failed, "
                                   f"using the CPU: {ex}")
        raise error

    @property
    def where(self):
        return "GPU" if self.device == "cuda" else "CPU"

    def detect(self, audio, allowed):
        """(language, probability) for audio, picked from `allowed`
        (any language when empty)."""
        model = self.model
        extractor = model.feature_extractor
        features = extractor(audio[:extractor.n_samples])
        from faster_whisper.audio import pad_or_trim
        encoded = model.encode(pad_or_trim(features[..., :extractor.nb_max_frames]))
        results = model.model.detect_language(encoded)[0]
        probabilities = {token[2:-2]: p for token, p in results}
        if allowed:
            best = max(allowed, key=lambda lang: probabilities.get(lang, 0.0))
        else:
            best = max(probabilities, key=probabilities.get)
        return best, probabilities.get(best, 0.0)

    def transcribe(self, audio, language, prompt=None, final=True):
        """(text, confidence 0-1) for one utterance."""
        segments, _info = self.model.transcribe(
            audio, language=language, task="transcribe",
            beam_size=3 if final else 1,
            temperature=(0.0, 0.2, 0.4) if final else 0.0,
            condition_on_previous_text=False, initial_prompt=prompt,
            without_timestamps=True, vad_filter=False,
            no_speech_threshold=0.6, log_prob_threshold=-1.0,
            compression_ratio_threshold=2.4,
        )
        texts = []
        scores = []
        for segment in segments:
            if segment.no_speech_prob > 0.6 and segment.avg_logprob < -0.8:
                continue
            text = segment.text.strip()
            if text:
                texts.append(text)
                scores.append(segment.avg_logprob)
        text = " ".join(" ".join(texts).split())
        confidence = math.exp(sum(scores) / len(scores)) if scores else 0.0
        return text, confidence


# One model kept loaded, reused by a restart that wants the same one -
# a channel change, a mic test - and dropped when Voice to Text turns
# off, freeing the GPU.
_LOAD_LOCK = threading.Lock()
_loaded = {"key": None, "transcriber": None}


def get_transcriber(name, device, wanted=lambda: True):
    """The Transcriber for (name, device), loading it if it is not the
    one already loaded. None if `wanted()` turns false while waiting."""
    with _LOAD_LOCK:
        key = (name, device)
        if _loaded["key"] == key:
            return _loaded["transcriber"]
        if not wanted():
            return None
        _loaded.update(key=None, transcriber=None)
        transcriber = Transcriber(name, device)
        _loaded.update(key=key, transcriber=transcriber)
        return transcriber


def release():
    with _LOAD_LOCK:
        _loaded.update(key=None, transcriber=None)
    import gc
    gc.collect()


# --- Voice activity ---

_vad_session = None
_vad_lock = threading.Lock()


def _vad():
    """Silero VAD's ONNX session, shared by every channel - each keeps
    its own state. faster-whisper's own wrapper starts afresh on every
    call, which streaming cannot use."""
    global _vad_session
    with _vad_lock:
        if _vad_session is None:
            options = _ort.SessionOptions()
            options.inter_op_num_threads = 1
            options.intra_op_num_threads = 1
            options.log_severity_level = 4
            path = os.path.join(_fw.utils.get_assets_path(), "silero_vad_v6.onnx")
            _vad_session = _ort.InferenceSession(
                path, providers=["CPUExecutionProvider"], sess_options=options)
        return _vad_session


class _StreamingVad:
    def __init__(self):
        self.session = _vad()
        self.h = _np.zeros((1, 1, 128), dtype=_np.float32)
        self.c = _np.zeros((1, 1, 128), dtype=_np.float32)
        self.context = _np.zeros(CONTEXT, dtype=_np.float32)

    def __call__(self, frame):
        """Speech probability of one FRAME-sample frame."""
        window = _np.concatenate([self.context, frame])[None, :]
        prob, self.h, self.c = self.session.run(
            None, {"input": window, "h": self.h, "c": self.c})
        self.context = frame[-CONTEXT:]
        return float(prob.reshape(-1)[0])


def _resample(samples, rate):
    """float32 samples at `rate` as 16 kHz."""
    if rate == RATE:
        return samples
    if rate % RATE == 0:
        factor = rate // RATE
        usable = len(samples) - len(samples) % factor
        return samples[:usable].reshape(-1, factor).mean(axis=1)
    count = int(round(len(samples) * RATE / rate))
    if count <= 0:
        return samples[:0]
    positions = _np.linspace(0, len(samples) - 1, count)
    return _np.interp(positions, _np.arange(len(samples)), samples) \
        .astype(_np.float32)


def _hallucination(text):
    folded = " ".join(text.lower().split()).strip(" .!?…")
    return not folded or any(folded == h.strip(" .") or
                             (len(h) > 12 and h in folded)
                             for h in _HALLUCINATIONS)


# --- Decoding ---

class Decoder(threading.Thread):
    """Runs Whisper for every channel, one job at a time: finished
    utterances in the order they ended, then - when none is waiting -
    the newest job of whichever channel has waited longest: reading its
    sentence so far for Live Text, or just judging its language.

    The language is judged once per utterance, while it is still being
    spoken, so the final reading is a single pass of the model.

    on_final(channel, utterance, text, language, confidence) and
    on_partial(channel, utterance, text) are called from this thread.
    """

    def __init__(self, transcriber, languages, on_final, on_partial):
        super().__init__(name="VoiceWhisper", daemon=True)
        self.transcriber = transcriber
        self.languages = tuple(languages)
        self.on_final = on_final
        self.on_partial = on_partial
        self._cond = threading.Condition()
        self._finals = deque()
        # {channel: (utterance, audio, asked at, read)} - only the newest;
        # read is False for a job that only judges the language.
        self._partials = {}
        # {(channel, utterance): language} judged while it was spoken.
        self._languages = {}
        # {channel: language last heard} - for an utterance too short to
        # judge, speakers mostly keep to one.
        self._last_language = {}
        # {channel: last final text} - context for the next.
        self._previous = {}
        self._closing = False

    @property
    def multilingual(self):
        return len(self.languages) != 1

    def submit_final(self, channel, utterance, audio):
        with self._cond:
            self._finals.append((channel, utterance, audio))
            current = self._partials.get(channel)
            if current is not None and current[0] == utterance:
                del self._partials[channel]
            self._cond.notify()

    def submit_partial(self, channel, utterance, audio, read=True):
        """Reads the sentence so far - or with read False only judges its
        language, for the final to use."""
        with self._cond:
            if self._closing:
                return
            since = self._partials.get(channel, (None, None, time.monotonic()))[2]
            self._partials[channel] = (utterance, audio, since, read)
            self._cond.notify()

    def wants_partial(self, channel):
        """False while a job for `channel` is still waiting - no point
        queueing a newer one behind it."""
        with self._cond:
            return channel not in self._partials

    def close(self):
        """Finishes the utterances already handed in, then stops."""
        with self._cond:
            self._closing = True
            self._partials.clear()
            self._cond.notify()

    def _detect(self, audio):
        return self.transcriber.detect(audio, self.languages)[0]

    def _language_for(self, channel, utterance, audio, final):
        """The language to read audio in: the one judged for this
        utterance, judging it now if there is enough to go on, else the
        one this channel spoke last."""
        if not self.multilingual:
            return self.languages[0]
        key = (channel, utterance)
        known = self._languages.pop(key, None) if final \
            else self._languages.get(key)
        if known:
            return known
        if len(audio) >= DETECT_SECONDS * RATE:
            language = self._detect(audio)
            if not final:
                if len(self._languages) > 100:
                    # Utterances dropped as too short leave theirs behind.
                    self._languages.clear()
                self._languages[key] = language
            return language
        last = self._last_language.get(channel)
        if last:
            return last
        return self._detect(audio) if final else \
            (self.languages[0] if self.languages else None)

    def _prompt(self, channel, language):
        prompt = _PROMPTS.get(language, "")
        previous = self._previous.get(channel)
        if previous:
            prompt = f"{prompt} {previous[-160:]}".strip()
        return prompt or None

    def _next_job(self):
        """(channel, utterance, audio, kind) - kind "final", "partial"
        or "detect" - or None once closed with nothing left."""
        with self._cond:
            while not self._finals and not self._partials and not self._closing:
                self._cond.wait()
            if self._finals:
                return (*self._finals.popleft(), "final")
            if self._partials:
                channel = min(self._partials, key=lambda c: self._partials[c][2])
                utterance, audio, _since, read = self._partials.pop(channel)
                return channel, utterance, audio, "partial" if read else "detect"
            return None

    def run(self):
        while True:
            job = self._next_job()
            if job is None:
                return
            channel, utterance, audio, kind = job
            final = kind == "final"
            text, confidence, language = "", 0.0, None
            try:
                language = self._language_for(channel, utterance, audio, final)
                if kind != "detect":
                    text, confidence = self.transcriber.transcribe(
                        audio, language, self._prompt(channel, language)
                        if final else _PROMPTS.get(language), final=final)
            except Exception as ex:
                log("error", f"Voice to Text: Whisper failed on channel "
                             f"{channel}: {ex!r}")

            if _hallucination(text):
                text = ""
            if final:
                if text:
                    self._last_language[channel] = language
                    self._previous[channel] = text
                try:
                    self.on_final(channel, utterance, text, language, confidence)
                except Exception as ex:
                    log("error", f"Voice to Text: sending channel {channel}: {ex!r}")
            elif text:
                with self._cond:
                    # A final for it may have been handed in meanwhile.
                    stale = any(c == channel and u == utterance
                                for c, u, _a in self._finals)
                if not stale:
                    self.on_partial(channel, utterance, text)


class ChannelWorker(threading.Thread):
    """One channel: its level, the gate, the VAD, and cutting what it
    hears into utterances for the Decoder. Fed from the audio callback
    through a queue, so the callback never waits."""

    def __init__(self, number, rate, gate_db, decoder, emit,
                 live=lambda: False):
        super().__init__(name=f"Voice{number}", daemon=True)
        self.number = number
        self.rate = rate
        self.gate_db = gate_db
        self.decoder = decoder
        # emit(number, text, language, confidence) for a finished line.
        self.emit = emit
        self.live = live
        self.level = -120.0
        self.partial = ""
        self._partial_utterance = None
        self._queue = queue.Queue(maxsize=MAX_BACKLOG_BLOCKS)
        self._dropped = 0
        self._vad = _StreamingVad()
        self._pending = _np.zeros(0, dtype=_np.float32)
        self._preroll = deque(maxlen=max(1, round(PREROLL_SECONDS / FRAME_SECONDS)))
        self._utterance = 0
        self._frames = []
        self._speaking = False
        self._speech = 0
        self._silence = 0
        self._partial_at = 0
        # Whether this utterance's language has been sent to be judged.
        self._judged = False

    # The engine's side.

    def push(self, block):
        try:
            self._queue.put_nowait(block)
        except queue.Full:
            self._dropped += 1

    def finish(self):
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self._queue.put_nowait(None)

    # The Decoder's side.

    def finished(self, utterance, text, language, confidence):
        if self._partial_utterance == utterance:
            self.partial = ""
        if text:
            self.emit(self.number, text, language, confidence)

    def heard_so_far(self, utterance, text):
        if utterance == self._utterance and self._speaking:
            self._partial_utterance = utterance
            self.partial = text

    # Listening.

    def run(self):
        warned_at = 0
        while True:
            block = self._queue.get()
            if block is None:
                break
            samples = block.astype(_np.float32) / 32768.0
            rms = float(_np.sqrt(_np.mean(samples * samples))) if len(samples) else 0.0
            self.level = 20 * math.log10(rms) if rms > 0 else -120.0
            gate_open = self.level >= self.gate_db

            self._pending = _np.concatenate([self._pending,
                                             _resample(samples, self.rate)])
            while len(self._pending) >= FRAME:
                frame = self._pending[:FRAME]
                self._pending = self._pending[FRAME:]
                prob = self._vad(frame)
                self._frame(frame, prob if gate_open else 0.0)

            if self._speaking:
                self._maybe_partial()

            if self._dropped and time.monotonic() - warned_at > 30:
                warned_at = time.monotonic()
                log("warning", f"Voice to Text: channel {self.number} fell "
                               f"behind and skipped {self._dropped} blocks")
                self._dropped = 0

        if self._speaking:
            self._close()

    def _frame(self, frame, prob):
        if not self._speaking:
            self._preroll.append(frame)
            if prob >= START_PROB:
                self._speaking = True
                self._utterance += 1
                self._frames = list(self._preroll)
                self._preroll.clear()
                self._speech = 1
                self._silence = 0
                self._partial_at = 0
                self._judged = False
            return

        self._frames.append(frame)
        if prob >= END_PROB:
            self._speech += 1
            self._silence = 0
        else:
            self._silence += 1

        seconds = len(self._frames) * FRAME_SECONDS
        quiet = self._silence * FRAME_SECONDS
        if quiet >= END_SILENCE or seconds >= MAX_UTTERANCE_SECONDS or \
                (seconds >= SOFT_CUT_SECONDS and quiet >= SOFT_CUT_SILENCE):
            self._close()

    def _maybe_partial(self):
        """Hands the sentence so far to the Decoder: to read for Live Text
        while it is watching, and once - either way - to judge which
        language it is in."""
        seconds = len(self._frames) * FRAME_SECONDS
        # Not while the speaker is pausing - the utterance may be about
        # to end, and a reading then would only hold up the final one.
        if self._silence > 2 or not self.decoder.wants_partial(self.number):
            return
        if self.live():
            if seconds < FIRST_PARTIAL_SECONDS or \
                    seconds - self._partial_at < PARTIAL_SECONDS:
                return
            self._partial_at = seconds
            self._judged = self._judged or seconds >= DETECT_SECONDS
            self.decoder.submit_partial(self.number, self._utterance,
                                        _np.concatenate(self._frames))
        elif not self._judged and seconds >= DETECT_SECONDS and \
                self.decoder.multilingual:
            self._judged = True
            self.decoder.submit_partial(self.number, self._utterance,
                                        _np.concatenate(self._frames),
                                        read=False)

    def _close(self):
        self._speaking = False
        frames, self._frames = self._frames, []
        # The trailing silence, bar a little, is not worth reading.
        keep = max(0, self._silence - round(0.2 / FRAME_SECONDS))
        if keep:
            frames = frames[:-keep]
        if self._speech * FRAME_SECONDS < MIN_SPEECH_SECONDS or not frames:
            if self._partial_utterance == self._utterance:
                self.partial = ""
            return
        self.decoder.submit_final(self.number, self._utterance,
                                  _np.concatenate(frames))
