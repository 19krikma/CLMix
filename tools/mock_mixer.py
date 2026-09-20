"""Mock OSC mixer console for testing CLMix without real hardware.

Answers just enough of the OSC protocol MixerWorker (ui/main_window.py)
speaks during its boot sequence and while connected - console/channel/aux
discovery, snapshot info, get/set of the channel's own fader/mute/pan and
its input gain/trim/48V/phase and channel naming (what Full Mixer Control
rides) plus per-aux-send
level/pan/on -
to let the desktop app (and, through it, phone clients via RemoteServer)
be exercised end-to-end. Every strip carries the real console's full
parameter set (built from docs/mixer_protocol/commands.csv), so a
whole-strip dump ("/Input_Channels/3/?") answers the way the desk does -
including leaving three gate parameters out of it, which the real desk
also does and which a backup has to ask for by name (see DUMP_OMITTED).
Each snapshot stores its own copy of the lot. Simulates:

    - 5 aux buses  ("Reverb", "Monitor 1", "Monitor 2", "Delay", "FX Send")
    - 5 banks      (see BANK_NAMES), each with a random channel count
    - N channels   (see CHANNEL_NAMES, built from however many channels
                    the random banks add up to), a random share of them
                    stereo (see STEREO_CHANCE) so mono and stereo
                    metering can both be exercised
    - snapshots    see SNAPSHOT_NAMES; recallable by clients via
                   /Snapshots/Recall_Snapshot/{n} (unless
                   --no-remote-recall), announced with the desk's real
                   four-message recall burst ending in
                   /Snapshots/End_Recall_Snapshot, and readable either
                   as the whole list (/Snapshots/names/?) or one at a
                   time (/Snapshots/name/? with an index). Test-only
                   surface actions live under /Mock/ - see
                   MockMixer.handle_mock_control
    - macros       names only, via /Macros/names/? - as on the desk,
                   nothing here fires one
    - meters       a ~29Hz /Meters/values stream for whatever slots the
                   client subscribed, in the console's packed peak/RMS
                   wire format

Replies are sent to a fixed --client-host/--client-port rather than to
each query's source port, matching how CLMix expects a mixer to behave
(it queries from an ephemeral socket but only ever listens on the port
configured as "Rec Port").

Console shape is randomized on every run - pass --seed for a reproducible
layout across runs.

Usage:
    python tools/mock_mixer.py [--seed N]

Then in CLMix's Setup window:
    Mixer IP Address: 127.0.0.1
    Send Port:        10023  (default - matches --listen-port default)
    Rec Port:         10024  (default - matches --client-port default)
"""
import argparse
import array
import copy
import csv
import json
import math
import random
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

from pythonosc.osc_message import OscMessage, ParseError
from pythonosc.osc_message_builder import OscMessageBuilder

# Mono/stereo, as /Console/*/modes reports it, for both input channels
# and aux buses. Roughly a quarter of the simulated channels come up
# stereo (see STEREO_CHANCE) so a typical bank exercises both meter
# layouts at once.
MODE_MONO = 1
MODE_STEREO = 2

BANK_NAMES = ["Band", "Drums", "Vocals", "Horns", "Percussion"]
MIN_CHANNELS_PER_BANK = 3
MAX_CHANNELS_PER_BANK = 10

# The first bank is always this wide, rather than randomized like the
# rest. Random counts topped out at ten, so nothing ever exercised a bank
# too wide to fit on screen at once - which is the case that decides
# whether a client scrolls its strips sensibly, and the one worth having
# on hand every run rather than on a lucky seed.
FIRST_BANK_CHANNELS = 12

# Cycled (and repeated, once exhausted) to name however many channels
# each randomized bank ends up with.
# A few of these are deliberately long. Every name here used to fit a
# strip on one line, which meant nothing ever exercised what a client
# does when a name does not - and real desks are full of "Lead Vocal L"
# and "Talkback Mic".
INSTRUMENT_POOL = [
    "Kick", "Snare", "Hi-Hat", "Toms", "Overheads", "Guitar 1", "Guitar 2",
    "Bass", "Keys 1", "Keys 2", "Lead Vocal Left", "Vocal 2", "Vocal 3", "BGV 1",
    "BGV 2", "Sax", "Trumpet", "Trombone", "Talkback Mic", "Click", "DI 1",
    "DI 2", "Synth", "Ambient Left", "Strings", "Loop",
]

# The first few are fixed so the mono/stereo mix below is predictable;
# past those, IEM sends are generated to whatever --auxes asks for. A
# real Q225 reports 30, so five is a small console, not a typical one.
AUX_NAME_SEED = ["Reverb", "Monitor 1", "Monitor 2", "Delay", "FX Send"]
AUX_NAMES = list(AUX_NAME_SEED)

# Fixed rather than randomized: an aux's width decides whether the pan
# control appears at all, so having a known mono bus and a known stereo
# one next to each other in the list is what makes that switchable by
# hand. Reverb and FX Send are stereo; the wedge/IEM monitors are mono,
# which is also how they usually are on a real desk.
AUX_MODE_SEED = [MODE_STEREO, MODE_MONO, MODE_MONO, MODE_MONO, MODE_STEREO]
AUX_MODES = list(AUX_MODE_SEED)


def build_auxes(count):
    """Aux names/modes for `count` buses, keeping the seeded ones first."""
    names = list(AUX_NAME_SEED)
    modes = list(AUX_MODE_SEED)

    while len(names) < count:
        names.append(f"IEM {len(names) - len(AUX_NAME_SEED) + 1}")
        # IEMs are usually stereo; alternate anyway so both paths stay
        # exercised however many are asked for.
        modes.append(MODE_STEREO if len(modes) % 2 else MODE_MONO)

    return names[:count], modes[:count]

SNAPSHOT_NAMES = ["Show 1", "Show 2", "Soundcheck", "Support Band"]

# What the console reports about itself and the loaded show.
CONSOLE_NAME = "MOCK-Q225"
SESSION_FILENAME = "Mock Show.ses"

# Bus counts for everything except inputs (built from the banks) and
# auxes (--auxes). Smaller than a real Q225 so a full scan stays quick.
# Talkback_Outputs is reported, but - exactly as on the real desk -
# nothing under it answers.
MOCK_BUS_COUNTS = {
    "Group_Outputs": 3,
    "Talkback_Outputs": 2,
    "Control_Groups": 4,
    "Matrix_Inputs": 4,
    "Matrix_Outputs": 4,
    "Graphic_EQ": 4,
    "Multis": 1,
}

# /Console/Channels/? answers one count per category, in this order.
CATEGORY_ORDER = [
    "Input_Channels", "Aux_Outputs", "Group_Outputs", "Talkback_Outputs",
    "Control_Groups", "Matrix_Inputs", "Matrix_Outputs", "Graphic_EQ",
    "Multis",
]
SILENT_CATEGORIES = {"Talkback_Outputs"}

# Every parameter of one strip per category, as the real console dumped
# it - addresses, types and sample values. Each simulated strip is built
# from this, so a scan of the mock returns the real console's parameter
# set rather than an invented one.
COMMANDS_CSV = Path(__file__).resolve().parent.parent / "docs" / \
    "mixer_protocol" / "commands.csv"

# Buses nested inside a strip (a channel's sends), and the category whose
# count bounds them - the template carries all 30 of a Q225's aux sends,
# which a console with five auxes must not report.
NESTED_BUS_CATEGORY = {
    "Aux_Send": "Aux_Outputs",
    "Group_Send": "Group_Outputs",
    "Matrix_Send": "Matrix_Outputs",
}
NESTED_BUS_RE = re.compile(r"(Aux_Send|Group_Send|Matrix_Send)/(\d+)/")
STRIP_RE = re.compile(r"^/([A-Za-z_]+)/(\d+)$")

# Parameters the real console answers by name but leaves out of its own
# whole-strip dump (PROTOCOL.md, "Parameters a strip dump leaves out").
# Reproduced here, gap and all, because a mock whose dump is complete
# cannot test the code that exists to cope with one that is not - see
# ShowBackupJob._fill_dump_gaps. Added to any strip that has a gate.
DUMP_TRIGGER_LEAF = "Dynamics/gate_thresh"
DUMP_OMITTED = {
    "Dynamics/gate_hold": [0.08],
    "Dynamics/gate_range": [15.0],
    "Dynamics/gate-duck-comp": [0.0],
}

# The console's macro names, as /Macros/names/? reports them - 0-based,
# and console-wide rather than per-snapshot.
MACRO_NAMES = ["Snapshots Panel", "Talkback panel", "AutoTune PANIC",
               "PC TO MASTER", "Save current Session"]

# The fader layout's arg shape: [name, side, layer, bank] then one
# (category, index) pair per fader, an empty fader being ("", 0). The
# real desk reports 12 faders per bank and both sides of each bank
# carrying the same strips - see PROTOCOL.md, "Layout".
LAYOUT_KEY_ARGS = 4
LAYOUT_SIDES = ("L", "R")
LAYOUT_FADERS = 12
LAYOUT_BANKS_PER_LAYER = 4

STEREO_CHANCE = 0.25

# Meter simulation. The console quantises meters to 3 dB steps over a
# 0..-60 dB scale and uses 126 as its no-signal sentinel - see
# docs/mixer_protocol/PROTOCOL.md "Metering".
METER_INTERVAL_SECONDS = 0.035
METER_STEP_DB = 3
METER_FLOOR_FIELD = 126
# The scale runs 0 dB down to -60. The reference captures only ever show
# fields of 6 and above, but that is because nothing in them got louder
# than -6 dB, not a ceiling in the protocol - so a hot mic is allowed to
# drive the meters all the way to 0.
METER_MIN_FIELD = 0
METER_MAX_FIELD = 60
METER_WALK_DB = 4.0
# Peak-hold release. A field is dB BELOW zero, so a decaying peak means a
# rising field - this walks it back towards the floor at 12 dB/sec, slow
# enough that the peak marker visibly lags the bar instead of riding it.
METER_PEAK_FALL_DB_PER_SEC = 12.0

# Populated by build_banks() in main(), after any --seed is applied.
BANKS = {}
CHANNEL_NAMES = []
CHANNEL_MODES = []


# Live capture from the system's default input. parec (PulseAudio /
# PipeWire) is used rather than a Python audio binding because it needs
# no extra dependency and no system library beyond what a desktop
# already has - this is a test tool, and a missing parec simply falls
# back to the synthetic walk.
MIC_COMMAND = "parec"
MIC_RATE = 48000
MIC_CHANNELS = 2
MIC_BLOCK_FRAMES = 480          # 10ms, comfortably under one meter tick
MIC_SAMPLE_BYTES = 2            # s16le
MIC_FULL_SCALE = 32768.0


class MicCapture:
    """Peak/RMS per input channel from the default recording device.

    Levels are exposed in the console's own units - a field is dB below
    zero, so 0 is full scale and larger means quieter - leaving
    tick_meters() to do nothing but quantise and pack them.

    Every simulated console channel meters this same input: a stereo
    channel takes its two legs from the device's left and right, a mono
    channel takes left only. So with a stereo interface the two bars of
    a stereo strip move genuinely independently, which is the thing
    worth testing here.
    """

    def __init__(self):
        self.process = None
        # Per device channel, the latest (peak_field, rms_field). Written
        # by the reader thread, read by the socket loop - a plain tuple
        # swap, so no lock is needed to see a consistent pair.
        self.levels = [None] * MIC_CHANNELS

    @staticmethod
    def available():
        return shutil.which(MIC_COMMAND) is not None

    def start(self):
        self.process = subprocess.Popen(
            [
                MIC_COMMAND,
                "--format=s16le",
                f"--rate={MIC_RATE}",
                f"--channels={MIC_CHANNELS}",
                "--latency-msec=10",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

        thread = threading.Thread(target=self._read, daemon=True)
        thread.start()

    def _read(self):
        block = MIC_BLOCK_FRAMES * MIC_CHANNELS * MIC_SAMPLE_BYTES

        while True:
            data = self.process.stdout.read(block)

            if not data:
                # parec died (device unplugged, server restart) - stop
                # updating and let the levels decay to the sentinel.
                self.levels = [None] * MIC_CHANNELS
                return

            samples = array.array("h")
            samples.frombytes(data[:len(data) - len(data) % 2])

            for channel in range(MIC_CHANNELS):
                self.levels[channel] = self._measure(samples[channel::MIC_CHANNELS])

    @staticmethod
    def _measure(samples):
        if not samples:
            return None

        peak = max(abs(sample) for sample in samples)
        rms = math.sqrt(
            sum(sample * sample for sample in samples) / len(samples)
        )

        return (MicCapture._field(peak), MicCapture._field(rms))

    @staticmethod
    def _field(amplitude):
        """Linear amplitude -> dB below full scale, as a positive field."""
        if amplitude <= 0:
            return float(METER_FLOOR_FIELD)

        return -20.0 * math.log10(amplitude / MIC_FULL_SCALE)

    def level(self, channel):
        """(peak_field, rms_field) for a device channel.

        None only before the first block arrives or once parec has died -
        a silent input is a real measurement, reported as a field past
        the bottom of the scale rather than as nothing at all.
        """
        if channel >= len(self.levels):
            return None

        return self.levels[channel]


def build_layout(banks):
    """The console's fader layout for these banks, as it reports it.

    One entry per bank per side, banks filling four to a layer, each
    padded out to twelve faders the way a part-filled bank is on the
    desk.
    """
    layout = []

    for index, (bank_name, channels) in enumerate(banks.items()):
        layer, position = divmod(index, LAYOUT_BANKS_PER_LAYER)
        slots = []

        for fader in range(LAYOUT_FADERS):
            if fader < len(channels):
                slots += ["Input_Channels", channels[fader]]
            else:
                slots += ["", 0]

        for side in LAYOUT_SIDES:
            layout.append([bank_name, side, layer, position] + slots)

    return layout


def build_banks():
    banks = {}
    channel_names = []
    next_channel = 1

    for index, bank_name in enumerate(BANK_NAMES):
        count = FIRST_BANK_CHANNELS if index == 0 else \
            random.randint(MIN_CHANNELS_PER_BANK, MAX_CHANNELS_PER_BANK)
        banks[bank_name] = list(range(next_channel, next_channel + count))
        next_channel += count

        for _ in range(count):
            channel_names.append(INSTRUMENT_POOL[len(channel_names) % len(INSTRUMENT_POOL)])

    modes = [
        MODE_STEREO if random.random() < STEREO_CHANCE else MODE_MONO
        for _ in channel_names
    ]

    return banks, channel_names, modes


def load_strip_templates(path=COMMANDS_CSV):
    """{category: [(leaf, sample_args)]} from strip 1 of each category."""
    templates = {}

    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            match = re.match(r"^/([^/]+)/1/(.+)$", row["address"])
            if not match:
                continue

            category, leaf = match.groups()
            templates.setdefault(category, []).append(
                (leaf, json.loads(row["sample_value"]))
            )

    return templates


def build_console_params(counts, templates):
    """Every parameter of every strip -> its value, plus each strip's
    addresses in dump order."""
    params = {}
    strips = {}

    for category, count in counts.items():
        if category in SILENT_CATEGORIES:
            continue

        for n in range(1, count + 1):
            prefix = f"/{category}/{n}"
            order = strips.setdefault(prefix, [])

            for leaf, sample in templates.get(category, []):
                nested = NESTED_BUS_RE.search(leaf)
                if nested and int(nested.group(2)) > \
                        counts.get(NESTED_BUS_CATEGORY[nested.group(1)], 0):
                    continue

                address = f"{prefix}/{leaf}"
                params[address] = list(sample)
                order.append(address)

            if f"{prefix}/{DUMP_TRIGGER_LEAF}" in params:
                # Deliberately not appended to `order`: these answer a
                # direct query and stay out of the dump, as on the desk.
                for leaf, sample in DUMP_OMITTED.items():
                    params[f"{prefix}/{leaf}"] = list(sample)

    return params, strips


def perturb(params, rng, flip_chance=0.15, spread=6.0, rename=False):
    """Change values the way a different snapshot (or a wrecked session)
    would: flags flip, levels move, 0..1 controls stay in range."""
    for address, value in params.items():
        if not value:
            continue

        current = value[0]

        if isinstance(current, str):
            if rename:
                params[address] = [f"Name {rng.randint(100, 999)}"]
            continue

        if isinstance(current, int) and not isinstance(current, bool):
            continue

        if current in (0.0, 1.0):
            if rng.random() < flip_chance:
                params[address] = [1.0 - current]
        elif 0.0 < current < 1.0:
            params[address] = [round(min(1.0, max(0.0, current + rng.uniform(-0.3, 0.3))), 3)]
        else:
            params[address] = [round(current + rng.uniform(-spread, spread), 2)]


class MockMixer:
    def __init__(self, listen_port, client_host, client_port, mic=None,
                 recall_every=None, listen_host="0.0.0.0",
                 remote_recall=True, drop_rate=0.0, layout_write=True):
        self.client_host = client_host
        self.client_port = client_port
        self.mic = mic

        # Simulated snapshot recalls, as if someone were working the
        # surface. A recall rewrites levels, pans and mutes across the
        # whole desk and announces only the recall itself - which is the
        # behaviour a client has to cope with, so it is the behaviour
        # worth being able to reproduce here.
        self.recall_every = recall_every
        self.remote_recall = remote_recall
        self.drop_rate = drop_rate
        self.snapshot = 1
        self.last_recall_at = time.monotonic()

        # The fader layout, one entry per bank per side, in the console's
        # own arg order: [name, side, layer, bank] then (category, index)
        # per fader - see PROTOCOL.md, "Layout".
        self.layout = build_layout(BANKS)

        # Whether writing a bank back is honoured. Nothing has ever been
        # observed writing /Layout/Layout/Banks on a real desk, so this
        # is the one behaviour here that is a guess rather than a copy:
        # --no-layout-write plays the console that ignores the write, so
        # the honest-failure path in RestoreSessionJob can be exercised
        # too.
        self.layout_write = layout_write

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((listen_host, listen_port))

        self.counts = {"Input_Channels": len(CHANNEL_NAMES),
                       "Aux_Outputs": len(AUX_NAMES), **MOCK_BUS_COUNTS}

        # The whole console as one address -> [value] table: every strip
        # parameter the real desk reports (see COMMANDS_CSV), so a query,
        # a set and a whole-strip dump all read and write the same place.
        # self.strips keeps each strip's addresses in dump order.
        base, self.strips = build_console_params(
            self.counts, load_strip_templates()
        )
        self._set_starting_values(base)

        # The desk's stored snapshots, each a full copy of the table. A
        # recall loads one into the live table (bar names, which this
        # console keeps across recalls); pressing Update on the surface
        # - /Mock/Store_Snapshot here - saves the live table back into
        # the current one. Snapshot 1 is the starting values themselves,
        # the others vary from it, so every snapshot holds different data.
        seed_rng = random.Random(random.random())
        self.snapshot_state = {}
        for index in range(1, len(SNAPSHOT_NAMES) + 1):
            state = copy.deepcopy(base)
            if index > 1:
                perturb(state, random.Random(seed_rng.random()))
            self.snapshot_state[index] = state

        self.params = copy.deepcopy(self.snapshot_state[self.snapshot])

        # Meter subscriptions, as /Meters/request builds them up: slot
        # number -> the meter address bound to it. Slots are the client's
        # to assign, so this is whatever it asked for, and a stereo
        # channel simply shows up as two slots on two different legs.
        self.meter_slots = {}
        # slot -> its current level in dB (a float the walk moves around,
        # quantised to the wire's 3 dB grid only on the way out) and the
        # peak field currently being held for it.
        self.meter_levels = {}
        self.meter_peaks = {}
        # slot -> the packed int last sent, so only slots that actually
        # changed go into a packet, exactly as the console does it.
        self.meter_sent = {}
        self.meter_tick_at = 0.0

    def _set_starting_values(self, params):
        """The values this mock has always started with, over the
        template's samples - so every existing flow sees the same desk."""
        def put(address, value):
            if address not in params:
                prefix = address.rsplit("/", 1)[0]
                while prefix not in self.strips:
                    prefix = prefix.rsplit("/", 1)[0]
                self.strips[prefix].append(address)
            params[address] = [value]

        for channel in range(1, self.counts["Input_Channels"] + 1):
            prefix = f"/Input_Channels/{channel}"
            put(f"{prefix}/Channel_Input/name", CHANNEL_NAMES[channel - 1])
            # The channel's own fader and panner - the main mix Full
            # Mixer Control rides. Unity and centre, like a fresh show.
            put(f"{prefix}/fader", 0.0)
            put(f"{prefix}/mute", 0.0)
            put(f"{prefix}/Panner/pan", 0.5)
            # Input stage, spread so one channel's dial reads differently
            # from the next; 48V on roughly every third channel.
            put(f"{prefix}/Channel_Input/analog_gain", float(20 + (channel % 7) * 5))
            put(f"{prefix}/Channel_Input/trim", float((channel % 5) - 2))
            put(f"{prefix}/Channel_Input/phantom", float(channel % 3 == 0))
            put(f"{prefix}/Channel_Input/phase", 0.0)

            for aux in range(1, self.counts["Aux_Outputs"] + 1):
                put(f"{prefix}/Aux_Send/{aux}/send_level", -10.0)
                put(f"{prefix}/Aux_Send/{aux}/send_pan", 0.5)
                put(f"{prefix}/Aux_Send/{aux}/send_on", 1.0)

        for aux, name in enumerate(AUX_NAMES, start=1):
            put(f"/Aux_Outputs/{aux}/Buss_Trim/name", name)

    def recall(self, index):
        """Load stored snapshot `index` into the live desk and announce it.

        Levels, pans, mutes and everything else change; the ONLY thing
        sent is the recall burst, exactly as a console does it - the four
        messages below, in that order, with the index in the address and
        a single zero as the argument. Note that Current_Snapshot lands
        in the middle of it and End_Recall_Snapshot closes it, which is
        what tells a client the desk has stopped moving. Names survive
        the recall, as the desk keeps them per session.
        """
        if index not in self.snapshot_state:
            return

        names = {address: value for address, value in self.params.items()
                 if address.endswith("/name")}
        self.params = copy.deepcopy(self.snapshot_state[index])
        self.params.update(names)
        self.snapshot = index

        print(f"* snapshot recall -> {index} ({SNAPSHOT_NAMES[index - 1]})")
        self.send(f"/Snapshots/Recall_Snapshot/{index}", [0])
        self.send(f"/Snapshots/Change_Surface_Snapshot/{index}", [0])
        self.send("/Snapshots/Current_Snapshot", [index])
        self.send("/Snapshots/End_Recall_Snapshot", [0])

    def handle_mock_control(self, address, args):
        """Test-only stand-ins for someone at the surface, under /Mock/.

        Nothing CLMix sends lives here; a test drives these to do what an
        operator would do on the desk itself.
        """
        if address.startswith("/Mock/Surface_Recall/"):
            self.recall(int(address.rsplit("/", 1)[1]))

        elif address == "/Mock/Store_Snapshot":
            # The operator pressing Update on the current snapshot.
            self.snapshot_state[self.snapshot] = copy.deepcopy(self.params)
            print(f"* snapshot {self.snapshot} updated from the live desk")

        elif address == "/Mock/Scramble":
            # A session rebuilt from nothing: every value and name, live
            # and in every stored snapshot, no longer what it was.
            rng = random.Random()
            for state in [self.params, *self.snapshot_state.values()]:
                perturb(state, rng, flip_chance=0.5, spread=10.0, rename=True)
            print("* every snapshot and the live desk scrambled")

        elif address == "/Mock/Scramble_Layout":
            # Someone rearranging the surface's banks. Deliberately not
            # routed through handle_layout_write, so the layout can be
            # wrecked even on a console playing --no-layout-write - which
            # is the only way to test what a client does when the desk
            # reports a layout it refuses to let anyone set.
            for bank_args in self.layout:
                for i in range(LAYOUT_KEY_ARGS, len(bank_args) - 1, 2):
                    bank_args[i], bank_args[i + 1] = "", 0
            print("* fader layout emptied")

        elif address == "/Mock/Dump_State" and args:
            with open(str(args[0]), "w") as f:
                json.dump({"current": self.snapshot, "live": self.params,
                           "snapshots": self.snapshot_state}, f)
            print(f"* state written to {args[0]}")

    def run(self):
        print(f"Mock mixer listening on :{self.sock.getsockname()[1]}, "
              f"replying to {self.client_host}:{self.client_port}")

        # A timeout rather than a blocking read, so the meter stream keeps
        # ticking in the gaps between client messages.
        self.sock.settimeout(METER_INTERVAL_SECONDS)

        while True:
            try:
                data, _ = self.sock.recvfrom(65535)
            except socket.timeout:
                self.tick_meters()
                self.maybe_recall_snapshot()
                continue

            self.tick_meters()
            self.maybe_recall_snapshot()

            try:
                message = OscMessage(data)
            except ParseError:
                continue

            self.handle(message.address, list(message.params))

    def handle(self, address, args):
        print(f"< {address} {args}")

        if address.startswith("/Mock/"):
            self.handle_mock_control(address, args)
        elif address.startswith("/Snapshots/Recall_Snapshot/"):
            if self.remote_recall:
                self.recall(int(address.rsplit("/", 1)[1]))
            else:
                print("  (ignored: --no-remote-recall)")
        elif address.endswith("/?"):
            self.handle_query(address[:-2], args)
        elif address == "/Layout/Layout/Banks":
            self.handle_layout_write(args)
        elif address == "/Meters/clear" or address.startswith("/Meters/request/"):
            # Split out before handle_set, whose float() coercion would
            # choke on /Meters/request's string argument - it carries a
            # meter address, not a value.
            self.handle_meter_subscription(address, args)
        else:
            self.handle_set(address, args)

    def handle_meter_subscription(self, address, args):
        if address == "/Meters/clear":
            self.meter_slots = {}
            self.meter_levels = {}
            self.meter_peaks = {}
            self.meter_sent = {}
            return

        if not args:
            return

        slot = int(address.rsplit("/", 1)[1])
        self.meter_slots[slot] = str(args[0])
        # Without a mic each leg starts somewhere different, so a stereo
        # channel's two bars are visibly independent from the first
        # packet rather than moving in lockstep until the walks diverge.
        self.meter_levels[slot] = float(random.randint(
            METER_MIN_FIELD, METER_MAX_FIELD
        ))
        # A live meter starts with no peak held at all, and lets the
        # input itself pull the marker down; only the walk seeds one.
        self.meter_peaks[slot] = float(METER_FLOOR_FIELD) if self.mic \
            else self.meter_levels[slot]

    @staticmethod
    def leg_of(meter_address):
        """Device channel index for a meter address: left 0, right 1."""
        return 1 if meter_address.endswith("/right") else 0

    def slot_level(self, slot):
        """(peak_field, rms_field) for a slot, or None if nothing measured yet.

        Live input when there is a mic, and a random walk otherwise, so
        the tool still exercises the meter path on a machine with no
        recording device.
        """
        if self.mic is not None:
            return self.mic.level(self.leg_of(self.meter_slots[slot]))

        # Random walk in "dB below zero" - so a smaller field is a louder
        # signal - kept inside the console's own range.
        level = self.meter_levels[slot] + random.uniform(
            -METER_WALK_DB, METER_WALK_DB
        )
        level = min(float(METER_MAX_FIELD), max(float(METER_MIN_FIELD), level))
        self.meter_levels[slot] = level

        return (level, level)

    def maybe_recall_snapshot(self):
        """Every recall_every seconds, act as if the surface recalled the
        next snapshot - see recall(). A client that does not re-read
        after seeing the broadcast will sit on stale values.
        """
        if self.recall_every is None:
            return

        now = time.monotonic()

        if now - self.last_recall_at < self.recall_every:
            return

        self.last_recall_at = now
        self.recall(self.snapshot % len(SNAPSHOT_NAMES) + 1)

    def tick_meters(self):
        """Push one /Meters/values packet if a tick's worth of time passed."""
        now = time.monotonic()

        if not self.meter_slots or now - self.meter_tick_at < METER_INTERVAL_SECONDS:
            return

        self.meter_tick_at = now
        changed = []

        for slot in sorted(self.meter_slots):
            measured = self.slot_level(slot)

            # Smaller field = louder, so the peak is the MINIMUM field
            # seen recently, and holding it means letting it drift back
            # up towards the floor. It is allowed to decay past the
            # bottom of the scale and into the sentinel, reproducing the
            # console's documented "peak reads no-signal while RMS still
            # reports a level" case.
            peak = min(
                self.meter_peaks[slot]
                + METER_PEAK_FALL_DB_PER_SEC * METER_INTERVAL_SECONDS,
                float(METER_FLOOR_FIELD),
            )

            if measured is not None:
                peak = min(peak, measured[0])

            self.meter_peaks[slot] = peak

            packed = (self.wire_field(peak) << 16) | self.wire_field(
                measured[1] if measured is not None else None
            )

            # Real consoles send only what moved, which is what the app's
            # meter ballistics key off - see MixerWorker.meter_seq.
            if packed != self.meter_sent.get(slot):
                self.meter_sent[slot] = packed
                changed += [slot, packed]

        if changed:
            self.send_quiet("/Meters/values", changed)

    @staticmethod
    def wire_field(level):
        """A dB-below-zero float onto the console's 3 dB wire grid.

        Anything quieter than the bottom of the scale is reported as the
        no-signal sentinel rather than pinned to -60, which is what the
        console itself does.
        """
        if level is None or level > METER_MAX_FIELD:
            return METER_FLOOR_FIELD

        stepped = round(level / METER_STEP_DB) * METER_STEP_DB
        return min(METER_MAX_FIELD, max(METER_MIN_FIELD, int(stepped)))

    def handle_query(self, address, args=()):
        if address == "/Console/Channels":
            for category in CATEGORY_ORDER:
                self.send(f"/Console/{category}", [self.counts[category]])

        elif address == "/Console/Aux_Outputs/modes":
            self.send("/Console/Aux_Outputs/modes", list(AUX_MODES))

        elif address == "/Console/Input_Channels/modes":
            self.send("/Console/Input_Channels/modes", list(CHANNEL_MODES))

        elif address == "/Console/Group_Outputs/modes":
            self.send(address, [MODE_MONO] * self.counts["Group_Outputs"])

        elif address == "/Console/Name":
            self.send(address, [CONSOLE_NAME])

        elif address == "/Console/Session/Filename":
            self.send(address, [SESSION_FILENAME])

        elif address == "/Snapshots/Current_Snapshot":
            self.send(address, [self.snapshot])

        elif address == "/Snapshots/count":
            self.send(address, [len(SNAPSHOT_NAMES)])

        elif address == "/Snapshots/Surface_Snapshot":
            self.send(address, [self.snapshot])

        elif address == "/Snapshots/names":
            # [index, cue number, 0, name] - the real console's shape.
            for index, name in enumerate(SNAPSHOT_NAMES, start=1):
                self.send("/Snapshots/name", [index, index * 10, 0, name])

        elif address == "/Snapshots/name":
            # The same reply for one snapshot, asked for by index - what
            # the official app uses to follow the current snapshot's name
            # without pulling the whole list.
            index = int(args[0]) if args else 0
            if 1 <= index <= len(SNAPSHOT_NAMES):
                self.send(address, [index, index * 10, 0,
                                    SNAPSHOT_NAMES[index - 1]])

        elif address == "/Macros/names":
            for index, name in enumerate(MACRO_NAMES):
                self.send("/Macros/name", [index, name])

        elif address == "/Layout/Layout/Banks":
            # One message per bank per side, mirroring how a real console
            # answers a single "/Layout/Layout/Banks/?" query with a
            # broadcast per bank rather than one combined reply.
            for bank_args in self.layout:
                self.send("/Layout/Layout/Banks", list(bank_args))

        elif STRIP_RE.match(address):
            # A bare strip ("/Input_Channels/3/?") dumps every parameter
            # under it as a burst of individual replies - meters included,
            # with no arguments, exactly as the real console does.
            for parameter in self.strips.get(address, []):
                self.send(parameter, self.params[parameter])

        elif address in self.params:
            self.send(address, self.params[address])

    def handle_layout_write(self, args):
        """A bank written back, matched to the one it replaces by its
        name/side/layer/bank - the four args that identify it."""
        if len(args) < LAYOUT_KEY_ARGS:
            return

        if not self.layout_write:
            print("  (ignored: --no-layout-write)")
            return

        key = tuple(args[:LAYOUT_KEY_ARGS])

        for index, existing in enumerate(self.layout):
            if tuple(existing[:LAYOUT_KEY_ARGS]) == key:
                self.layout[index] = list(args)
                print(f"* fader bank {key} rewritten")
                return

        print(f"  (no such fader bank: {key})")

    def handle_set(self, address, args):
        if not args or address not in self.params:
            return

        current = self.params[address]

        if not current:
            # A meter: nothing to set.
            return

        # Keep the parameter's own type - a name stays a string, a count
        # an int - whatever a client sends.
        kind = type(current[0])
        value = str(args[0]) if kind is str else kind(args[0])
        self.params[address] = [value]

        # Real consoles echo every parameter change back to remote
        # listeners - CLMix relies on that echo to update its cache
        # rather than assuming its own command succeeded.
        self.send(address, [value])

    def send(self, address, args):
        if self.drop_rate and random.random() < self.drop_rate:
            # --drop-rate: lose this one on the way, as UDP may.
            print(f"x {address} (dropped)")
            return

        print(f"> {address} {args}")
        self.send_quiet(address, args)

    def send_quiet(self, address, args):
        # Unlogged: the meter stream is ~29 packets a second and would
        # bury every other line in the console.
        builder = OscMessageBuilder(address=address)
        for arg in args:
            builder.add_arg(arg)

        self.sock.sendto(builder.build().dgram, (self.client_host, self.client_port))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-port", type=int, default=10023)
    # Loopback only, say, to leave the same port free on this machine's
    # real adapters - which is what testing DiGiCo App Capture against
    # this mock needs, since that binds the console's port itself.
    parser.add_argument("--listen-host", default="0.0.0.0")
    parser.add_argument("--client-host", default="127.0.0.1")
    parser.add_argument("--client-port", type=int, default=10024)
    parser.add_argument("--seed", type=int, default=None,
                         help="Random seed for a reproducible bank/channel layout")
    parser.add_argument("--auxes", type=int, default=len(AUX_NAME_SEED),
                         metavar="N",
                         help=f"How many aux buses to report "
                              f"(default {len(AUX_NAME_SEED)}; a real Q225 has 30)")
    parser.add_argument("--recall-every", type=float, default=None,
                         metavar="SECONDS",
                         help="Periodically recall a snapshot, rewriting all "
                              "levels/pans/mutes and announcing only the recall")
    parser.add_argument("--no-layout-write", action="store_true",
                        help="Ignore fader banks written to "
                             "/Layout/Layout/Banks, playing a console that "
                             "only reports its layout")
    parser.add_argument("--no-remote-recall", action="store_true",
                         help="Ignore /Snapshots/Recall_Snapshot/{n} from "
                              "clients, as a console that only recalls from "
                              "its own surface would")
    parser.add_argument("--drop-rate", type=float, default=0.0,
                         metavar="FRACTION",
                         help="Lose this fraction of replies (not meters), "
                              "to test a client against UDP loss")
    parser.add_argument("--no-mic", action="store_true",
                         help="Drive the meters with a synthetic random walk "
                              "instead of the default recording device")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    global BANKS, CHANNEL_NAMES, CHANNEL_MODES, AUX_NAMES, AUX_MODES
    BANKS, CHANNEL_NAMES, CHANNEL_MODES = build_banks()
    AUX_NAMES, AUX_MODES = build_auxes(max(1, args.auxes))

    stereo = [
        index + 1 for index, mode in enumerate(CHANNEL_MODES)
        if mode == MODE_STEREO
    ]

    aux_widths = ", ".join(
        f"{name} ({'stereo' if mode == MODE_STEREO else 'mono'})"
        for name, mode in zip(AUX_NAMES, AUX_MODES)
    )

    print(f"Simulated console: {len(CHANNEL_NAMES)} channels, "
          f"{len(AUX_NAMES)} aux sends")
    print(f"  auxes: {aux_widths}")
    print(f"  stereo channels: {stereo or 'none'}")
    for bank_name, channels in BANKS.items():
        print(f"  {bank_name}: {len(channels)} channels {channels}")

    mic = None

    if not args.no_mic and MicCapture.available():
        mic = MicCapture()
        mic.start()
        print(f"  meters: live from the default input via {MIC_COMMAND} "
              f"(left/right feed each stereo channel's two legs)")
    elif args.no_mic:
        print("  meters: synthetic (--no-mic)")
    else:
        print(f"  meters: synthetic ({MIC_COMMAND} not found)")

    if args.recall_every:
        print(f"  snapshots: recalling one every {args.recall_every}s")

    MockMixer(args.listen_port, args.client_host, args.client_port, mic=mic,
              recall_every=args.recall_every,
              listen_host=args.listen_host,
              remote_recall=not args.no_remote_recall,
              layout_write=not args.no_layout_write,
              drop_rate=args.drop_rate).run()


if __name__ == "__main__":
    main()
