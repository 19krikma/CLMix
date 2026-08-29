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
                    the random banks add up to)

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
import random
import socket

from pythonosc.osc_message import OscMessage, ParseError
from pythonosc.osc_message_builder import OscMessageBuilder

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
SNAPSHOT_NAME = "Show 1"

# Populated by build_banks() in main(), after any --seed is applied.
BANKS = {}
CHANNEL_NAMES = []


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

    return banks, channel_names


class MockMixer:
    def __init__(self, listen_port, client_host, client_port):
        self.client_host = client_host
        self.client_port = client_port

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

    def run(self):
        print(f"Mock mixer listening on :{self.sock.getsockname()[1]}, "
              f"replying to {self.client_host}:{self.client_port}")

        while True:
            data, _ = self.sock.recvfrom(65535)

            try:
                message = OscMessage(data)
            except ParseError:
                continue

            self.handle(message.address, list(message.params))

    def handle(self, address, args):
        print(f"< {address} {args}")

        if address.endswith("/?"):
            self.handle_query(address[:-2])
        else:
            self.handle_set(address, args)

    def handle_query(self, address):
        if address == "/Console/Channels":
            self.send("/Console/Input_Channels", [len(CHANNEL_NAMES)])

        elif address == "/Console/Aux_Outputs/modes":
            self.send("/Console/Aux_Outputs/modes", [0] * len(AUX_NAMES))

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
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    global BANKS, CHANNEL_NAMES
    BANKS, CHANNEL_NAMES = build_banks()

    print(f"Simulated console: {len(CHANNEL_NAMES)} channels, "
          f"{len(AUX_NAMES)} aux sends")
    for bank_name, channels in BANKS.items():
        print(f"  {bank_name}: {len(channels)} channels {channels}")

    MockMixer(args.listen_port, args.client_host, args.client_port).run()


if __name__ == "__main__":
    main()
