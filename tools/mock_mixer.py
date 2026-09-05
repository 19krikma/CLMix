"""Mock OSC mixer console for testing CLMix without real hardware.

Answers just enough of the OSC protocol MixerWorker (ui/main_window.py)
speaks during its boot sequence and while connected - console/channel/aux
discovery, snapshot info, and get/set of channel mute plus per-aux-send
level/pan/on -
to let the desktop app (and, through it, phone clients via RemoteServer)
be exercised end-to-end. Simulates:

    - 5 aux buses  ("Reverb", "Monitor 1", "Monitor 2", "Delay", "FX Send")
    - 5 banks      (see BANK_NAMES), each with a random channel count
    - N channels   (see CHANNEL_NAMES, built from however many channels
                    the random banks add up to), a random share of them
                    stereo (see STEREO_CHANCE) so mono and stereo
                    metering can both be exercised
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
import math
import random
import shutil
import socket
import subprocess
import threading
import time

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

# Cycled (and repeated, once exhausted) to name however many channels
# each randomized bank ends up with.
INSTRUMENT_POOL = [
    "Kick", "Snare", "Hi-Hat", "Toms", "Overheads", "Guitar 1", "Guitar 2",
    "Bass", "Keys 1", "Keys 2", "Vocal 1", "Vocal 2", "Vocal 3", "BGV 1",
    "BGV 2", "Sax", "Trumpet", "Trombone", "Perc", "Click", "DI 1", "DI 2",
    "Synth", "Pad", "Strings", "Loop",
]

AUX_NAMES = ["Reverb", "Monitor 1", "Monitor 2", "Delay", "FX Send"]

# Fixed rather than randomized: an aux's width decides whether the pan
# control appears at all, so having a known mono bus and a known stereo
# one next to each other in the list is what makes that switchable by
# hand. Reverb and FX Send are stereo; the wedge/IEM monitors are mono,
# which is also how they usually are on a real desk.
AUX_MODES = [MODE_STEREO, MODE_MONO, MODE_MONO, MODE_MONO, MODE_STEREO]

SNAPSHOT_NAME = "Show 1"

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


def build_banks():
    banks = {}
    channel_names = []
    next_channel = 1

    for bank_name in BANK_NAMES:
        count = random.randint(MIN_CHANNELS_PER_BANK, MAX_CHANNELS_PER_BANK)
        banks[bank_name] = list(range(next_channel, next_channel + count))
        next_channel += count

        for _ in range(count):
            channel_names.append(INSTRUMENT_POOL[len(channel_names) % len(INSTRUMENT_POOL)])

    modes = [
        MODE_STEREO if random.random() < STEREO_CHANCE else MODE_MONO
        for _ in channel_names
    ]

    return banks, channel_names, modes


class MockMixer:
    def __init__(self, listen_port, client_host, client_port, mic=None):
        self.client_host = client_host
        self.client_port = client_port
        self.mic = mic

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", listen_port))

        channels = range(1, len(CHANNEL_NAMES) + 1)
        auxes = range(1, len(AUX_NAMES) + 1)
        self.levels = {(channel, aux): -10.0 for channel in channels for aux in auxes}
        self.pans = {(channel, aux): 0.5 for channel in channels for aux in auxes}
        # Per-aux-send on/off (what the phone apps' Mute button drives),
        # distinct from self.mutes below - the console-wide channel mute
        # the desktop's own Mute buttons drive.
        self.send_ons = {(channel, aux): 1.0 for channel in channels for aux in auxes}
        self.mutes = {channel: 0.0 for channel in channels}

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
                continue

            self.tick_meters()

            try:
                message = OscMessage(data)
            except ParseError:
                continue

            self.handle(message.address, list(message.params))

    def handle(self, address, args):
        print(f"< {address} {args}")

        if address.endswith("/?"):
            self.handle_query(address[:-2])
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

    def handle_query(self, address):
        if address == "/Console/Channels":
            self.send("/Console/Input_Channels", [len(CHANNEL_NAMES)])

        elif address == "/Console/Aux_Outputs/modes":
            self.send("/Console/Aux_Outputs/modes", list(AUX_MODES))

        elif address == "/Console/Input_Channels/modes":
            self.send("/Console/Input_Channels/modes", list(CHANNEL_MODES))

        elif address.startswith("/Aux_Outputs/") and address.endswith("/Buss_Trim/name"):
            aux = int(address.split("/")[2])
            self.send(address, [AUX_NAMES[aux - 1]])

        elif address.startswith("/Input_Channels/") and address.endswith("/Channel_Input/name"):
            channel = self._channel_from(address)
            self.send(address, [CHANNEL_NAMES[channel - 1]])

        elif address == "/Snapshots/Current_Snapshot":
            self.send(address, [1])

        elif address == "/Snapshots/names":
            self.send("/Snapshots/name", [1, SNAPSHOT_NAME])

        elif address == "/Layout/Layout/Banks":
            # One message per bank, mirroring how a real console answers
            # a single "/Layout/Layout/Banks/?" query with a broadcast
            # per bank rather than one combined reply.
            for bank_name, channels in BANKS.items():
                bank_args = [bank_name, 0, 0, 0]
                for channel in channels:
                    bank_args += ["Input_Channels", channel]
                self.send("/Layout/Layout/Banks", bank_args)

        elif address.endswith("/mute"):
            channel = self._channel_from(address)
            self.send(address, [self.mutes[channel]])

        elif address.endswith("/send_level"):
            self.send(address, [self.levels[self._channel_aux_from(address)]])

        elif address.endswith("/send_pan"):
            self.send(address, [self.pans[self._channel_aux_from(address)]])

        elif address.endswith("/send_on"):
            self.send(address, [self.send_ons[self._channel_aux_from(address)]])

    def handle_set(self, address, args):
        if not args:
            return

        value = float(args[0])

        if address.endswith("/mute"):
            self.mutes[self._channel_from(address)] = value
        elif address.endswith("/send_level"):
            self.levels[self._channel_aux_from(address)] = value
        elif address.endswith("/send_pan"):
            self.pans[self._channel_aux_from(address)] = value
        elif address.endswith("/send_on"):
            self.send_ons[self._channel_aux_from(address)] = value
        else:
            return

        # Real consoles echo every parameter change back to remote
        # listeners - CLMix relies on that echo to update its cache
        # rather than assuming its own command succeeded.
        self.send(address, [value])

    @staticmethod
    def _channel_from(address):
        return int(address.split("/")[2])

    @staticmethod
    def _channel_aux_from(address):
        parts = address.split("/")
        return int(parts[2]), int(parts[4])

    def send(self, address, args):
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
    parser.add_argument("--client-host", default="127.0.0.1")
    parser.add_argument("--client-port", type=int, default=10024)
    parser.add_argument("--seed", type=int, default=None,
                         help="Random seed for a reproducible bank/channel layout")
    parser.add_argument("--no-mic", action="store_true",
                         help="Drive the meters with a synthetic random walk "
                              "instead of the default recording device")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    global BANKS, CHANNEL_NAMES, CHANNEL_MODES
    BANKS, CHANNEL_NAMES, CHANNEL_MODES = build_banks()

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

    MockMixer(args.listen_port, args.client_host, args.client_port, mic=mic).run()


if __name__ == "__main__":
    main()
