"""Mock OSC mixer console for testing CLMix without real hardware.

Answers just enough of the OSC protocol MixerWorker (ui/main_window.py)
speaks during its boot sequence and while connected - console/channel/aux
discovery, snapshot info, and get/set of mute/level/pan per aux send -
to let the desktop app (and, through it, phone clients via RemoteServer)
be exercised end-to-end. Simulates:

    - 5 aux buses  ("Reverb", "Monitor 1", "Monitor 2", "Delay", "FX Send")
    - 4 banks      ("Band", "Drums", "Vocals", "Horns"), 5 channels each
    - 20 channels  (see CHANNEL_NAMES)

Replies are sent to a fixed --client-host/--client-port rather than to
each query's source port, matching how CLMix expects a mixer to behave
(it queries from an ephemeral socket but only ever listens on the port
configured as "Rec Port").

Usage:
    python tools/mock_mixer.py

Then in CLMix's Setup window:
    Mixer IP Address: 127.0.0.1
    Send Port:        10023  (default - matches --listen-port default)
    Rec Port:         10024  (default - matches --client-port default)
"""
import argparse
import socket

from pythonosc.osc_message import OscMessage, ParseError
from pythonosc.osc_message_builder import OscMessageBuilder

CHANNEL_NAMES = [
    "Guitar 1", "Guitar 2", "Bass", "Keys 1", "Keys 2",
    "Kick", "Snare", "Hi-Hat", "Toms", "Overheads",
    "Vocal 1", "Vocal 2", "Vocal 3", "BGV 1", "BGV 2",
    "Sax", "Trumpet", "Trombone", "Perc", "Click",
]
AUX_NAMES = ["Reverb", "Monitor 1", "Monitor 2", "Delay", "FX Send"]
BANKS = {
    "Band": [1, 2, 3, 4, 5],
    "Drums": [6, 7, 8, 9, 10],
    "Vocals": [11, 12, 13, 14, 15],
    "Horns": [16, 17, 18, 19, 20],
}
SNAPSHOT_NAME = "Show 1"


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
    args = parser.parse_args()

    MockMixer(args.listen_port, args.client_host, args.client_port).run()


if __name__ == "__main__":
    main()
