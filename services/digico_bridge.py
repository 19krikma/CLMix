"""DiGiCo App Capture: stand in for the console and record the official app.

The official DiGiCo app does things CLMix has never found a way to ask
the console for. The quickest way to learn them is to watch the app do
them, and this makes that possible without a packet sniffer: CLMix
announces itself as a console, the app connects to it believing it is
the desk, and every datagram is relayed untouched in both directions
and written to a capture file along the way.

    DiGiCo app  --OSC-->  DigicoAppBridge  --same bytes-->  console
                <--------  (records both)   <---------------

Nothing is rewritten, reordered, merged or dropped in either direction.
The console has no sessions and no idea a second client exists - it
answers whatever address its External Control panel names, which is
already CLMix - so the app's traffic simply rides CLMix's existing
connection. One consequence the operator should know about: the console
sends everything to CLMix, so the app also hears the replies to CLMix's
own queries, and CLMix hears the replies to the app's. Both are ordinary
self-describing state messages; the capture tags who sent what so the
log stays readable.

Metering is the one thing that cannot be shared. The console keeps a
single global meter slot table and reports slot numbers only, so two
clients subscribing at once would each decode the other's meters as
garbage. While capture is on CLMix stays off the table entirely (see
MixerWorker.suspend_meters) and the app owns it, exactly as it would on
a desk with nothing else connected.

See docs/mixer_protocol/PROTOCOL.md for the beacon layout and for what
the official client was already seen to do on connect.
"""

import ipaddress
import socket
import threading
import time
from datetime import datetime

from pythonosc.osc_bundle import OscBundle
from pythonosc.osc_message import OscMessage
from pythonosc.parsing import osc_types

from services.log_store import LOGS_DIR, log
from services.network_info import ipv4_network_of, list_ipv4_interfaces
from version import VERSION

# Deliberately outside LOGS_DIR's own glob: LogStore prunes *.log files
# there after RETENTION_DAYS, and a capture is research material that
# should outlive any retention policy for day-to-day logs.
CAPTURE_DIR = LOGS_DIR / "digico_captures"

BEACON_PORT = 2029

# Measured off the real console: one beacon every ~2.02 seconds.
BEACON_INTERVAL_SECONDS = 2.02

# The console's beacon, byte for byte, minus the 4 bytes of its own IP
# at offset 16 - see "Console discovery" in PROTOCOL.md. The trailing
# 2a 02 has never been identified, so it goes out exactly as observed.
BEACON_HEAD = bytes.fromhex("16000000" "ff000000" "0000000000000000")
BEACON_TAIL = bytes.fromhex("2a02")

# The app polls /Console/Session/Filename/? every 2 seconds for as long
# as it is connected, so five missed polls means it has gone - closed,
# asleep, or off the network - and the console's traffic stops being
# sent its way.
APP_TIMEOUT_SECONDS = 10.0

# The console's meter stream: ~30 packets a second of values the app
# subscribed to itself. Relayed like everything else but not written to
# the capture, which would otherwise be almost nothing but these.
# Matched on the address's own padded bytes, so it costs nothing to
# check and cannot catch /Meters/request or /Meters/clear, which ARE
# recorded - they show what the app chose to meter.
METER_VALUES_PREFIX = b"/Meters/values\x00"

RECV_TIMEOUT_SECONDS = 0.2


def type_tags(data):
    """A message's type tag string as sent (",ifs"), or "?" if unreadable.

    Taken off the wire rather than from the decoded arguments, because
    the difference between them is often the interesting part.
    """
    try:
        _address, index = osc_types.get_string(data, 0)

        if not data[index:]:
            return ","

        tags, _index = osc_types.get_string(data, index)
    except osc_types.ParseError:
        return "?"

    return tags


def describe(data):
    """A datagram decoded for reading: address, type tags, arguments.

    Never trusted over the raw bytes it is logged beside - anything that
    does not decode is said to be so, and the hex still carries it.
    """
    try:
        if OscBundle.dgram_is_bundle(data):
            bundle = OscBundle(data)
            parts = [
                describe(bundle.content(index).dgram)
                for index in range(bundle.num_contents)
            ]
            return (f"#bundle t={bundle.timestamp} "
                    f"[{bundle.num_contents}] {{ {' ; '.join(parts)} }}")

        message = OscMessage(data)
    except Exception:
        # Deliberately broad: a malformed datagram can fail anywhere in
        # python-osc (ParseError, IndexError, struct.error, or recursion
        # on a pathological bundle), and a capture that dies on the one
        # packet worth studying is no use to anyone.
        return "<undecodable>"

    return f"{message.address} {type_tags(data)} {list(message.params)}"


class DigicoAppBridge:
    """Poses as the console to the DiGiCo app and relays it to the real one.

    Runs beside a MixerWorker, which hands it every datagram it sends
    and receives (see MixerWorker.bridge). The app is found rather than
    configured: whatever address sends to the console port is the app,
    and replies go back to it on the same port the real console would
    use, CLMix's own receive port - which is what the app is set up to
    listen on, since it was set up for the real desk.

    Owns its own sockets rather than borrowing the worker's, so it can
    be switched on and off mid-session without touching CLMix's
    connection.
    """

    def __init__(self, mixer_ip, send_port, recv_port,
                 mixer_bind_ip=None, listen_ip=None, capture_dir=CAPTURE_DIR):
        self.mixer_ip = mixer_ip
        self.send_port = send_port
        self.recv_port = recv_port
        self.mixer_bind_ip = mixer_bind_ip or None
        # The adapter the iPad is on (Setup's Server adapter), or None for
        # every adapter except the console's own network.
        self.listen_ip = listen_ip or None
        self.capture_dir = capture_dir

        self.capture_path = None
        self.beacon_targets = []

        self._running = False
        self._threads = []
        self._listen_sock = None
        self._egress_sock = None
        self._beacon_socks = []

        self._file = None
        self._file_lock = threading.Lock()

        # app ip -> monotonic time it was last heard from.
        self._apps = {}
        self._apps_lock = threading.Lock()

        self.packets_from_app = 0
        self.packets_to_app = 0

    # ----------------------------------------------------------- lifecycle

    def start(self):
        """Open the capture file and sockets, then start relaying.

        Raises OSError if the console port cannot be bound here - most
        likely something else on this machine is already using it - with
        everything opened so far closed again.
        """
        try:
            self._open_capture()

            self._listen_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._listen_sock.bind((self.listen_ip or "", self.send_port))
            self._listen_sock.settimeout(RECV_TIMEOUT_SECONDS)

            # Its own socket towards the console, on the same card CLMix
            # uses. The console ignores source ports entirely, so this is
            # indistinguishable from the app talking to it directly.
            self._egress_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            if self.mixer_bind_ip:
                self._egress_sock.bind((self.mixer_bind_ip, 0))

            self._open_beacons()
        except OSError:
            self._close_sockets()
            self._close_capture("failed to start")
            raise

        self._running = True
        self._threads = [
            threading.Thread(target=self._relay_from_app, daemon=True,
                             name="digico-bridge-relay"),
            threading.Thread(target=self._send_beacons, daemon=True,
                             name="digico-bridge-beacon"),
        ]
        for thread in self._threads:
            thread.start()

        advertised = ", ".join(ip for ip, _ in self.beacon_targets) or "nowhere"
        log("info", f"DiGiCo App Capture on {self.listen_ip or 'all adapters'}"
            f":{self.send_port}, announcing as a console on {advertised}")
        log("info", f"DiGiCo App Capture recording to {self.capture_path}")

    def stop(self):
        if not self._running and self._file is None:
            return

        self._running = False

        for thread in self._threads:
            thread.join(timeout=1.0)

        self._threads = []
        self._close_sockets()

        with self._apps_lock:
            self._apps.clear()

        self._close_capture("stopped")
        log("info", "DiGiCo App Capture stopped")

    @property
    def running(self):
        return self._running

    def connected_apps(self):
        """Addresses of the apps currently being relayed to."""
        now = time.monotonic()

        with self._apps_lock:
            return [ip for ip, seen in self._apps.items()
                    if now - seen < APP_TIMEOUT_SECONDS]

    # ------------------------------------------------- called by the worker

    def from_console(self, data, sender):
        """A datagram the console sent CLMix. Runs on the worker thread.

        Relayed first and recorded second, so writing the file can never
        add latency to what the app sees.
        """
        # Read once: stop() can close and clear the socket between the
        # worker picking this bridge up and calling in.
        sock = self._listen_sock
        apps = self.connected_apps() if sock is not None else []

        for ip in apps:
            try:
                sock.sendto(data, (ip, self.recv_port))
                self.packets_to_app += 1
            except OSError as ex:
                # One unreachable iPad must not stop the console's traffic
                # reaching CLMix, which is the thread this runs on.
                self._note(f"relay to app {ip} failed: {ex}")

        if data.startswith(METER_VALUES_PREFIX):
            return

        direction = "MIXER->APP" if apps else "MIXER->CLMIX"
        self._record(direction, sender, data)

    def from_clmix(self, data):
        """A datagram CLMix itself sent the console, for the record only."""
        self._record("CLMIX->MIXER", (self.mixer_ip, self.send_port), data)

    # -------------------------------------------------------------- relay

    def _relay_from_app(self):
        while self._running:
            self._expire_apps()

            try:
                data, sender = self._listen_sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                # Socket closed under us by stop().
                break

            self._note_app(sender[0])

            try:
                self._egress_sock.sendto(data, (self.mixer_ip, self.send_port))
                self.packets_from_app += 1
            except OSError as ex:
                self._note(f"relay to console failed: {ex}")

            self._record("APP->MIXER", sender, data)

    def _note_app(self, ip):
        with self._apps_lock:
            is_new = ip not in self._apps
            self._apps[ip] = time.monotonic()

        if is_new:
            self._note(f"app connected from {ip}")
            log("info", f"DiGiCo App Capture: app connected from {ip}")

    def _expire_apps(self):
        now = time.monotonic()

        with self._apps_lock:
            gone = [ip for ip, seen in self._apps.items()
                    if now - seen >= APP_TIMEOUT_SECONDS]
            for ip in gone:
                del self._apps[ip]

        for ip in gone:
            self._note(f"app {ip} silent for {APP_TIMEOUT_SECONDS:.0f}s, "
                       "no longer relaying to it")
            log("info", f"DiGiCo App Capture: app {ip} went away")

    # ------------------------------------------------------------- beacon

    def _beacon_addresses(self):
        """(our ip, its subnet's broadcast address) to announce on.

        Never onto the console's own subnet: a second "console" appearing
        on the desk's network is not something any other client there
        should have to cope with, and the app is never on that side.
        """
        if self.listen_ip:
            candidates = [self.listen_ip]
        else:
            candidates = [ip for _label, ip in list_ipv4_interfaces()]

        targets = []

        for ip in candidates:
            network = ipv4_network_of(ip)

            if network is None or network.prefixlen >= 31:
                # A point-to-point link has no broadcast address to aim at.
                continue

            if self._on_network(self.mixer_ip, network):
                continue

            targets.append((ip, str(network.broadcast_address)))

        return targets

    @staticmethod
    def _on_network(address, network):
        try:
            return ipaddress.IPv4Address(address) in network
        except ValueError:
            return False

    def _open_beacons(self):
        self.beacon_targets = self._beacon_addresses()

        for ip, broadcast in self.beacon_targets:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

            try:
                # The real console beacons from port 2029 as well as to it.
                sock.bind((ip, BEACON_PORT))
            except OSError as ex:
                sock.bind((ip, 0))
                self._note(f"beacon on {ip} could not use source port "
                           f"{BEACON_PORT} ({ex}), sending from "
                           f"{sock.getsockname()[1]} instead")

            payload = BEACON_HEAD + socket.inet_aton(ip) + BEACON_TAIL
            self._beacon_socks.append((sock, payload, (broadcast, BEACON_PORT)))

        if not self.beacon_targets:
            self._note("no adapter to announce on - the app will have to be "
                       "given this computer's address by hand")

    def _send_beacons(self):
        while self._running:
            for sock, payload, target in self._beacon_socks:
                try:
                    sock.sendto(payload, target)
                except OSError as ex:
                    self._note(f"beacon to {target[0]} failed: {ex}")

            # Sliced so stop() is not held up for a whole interval.
            deadline = time.monotonic() + BEACON_INTERVAL_SECONDS
            while self._running and time.monotonic() < deadline:
                time.sleep(0.1)

    def _close_sockets(self):
        for sock, _payload, _target in self._beacon_socks:
            sock.close()
        self._beacon_socks = []

        for sock in (self._listen_sock, self._egress_sock):
            if sock is not None:
                sock.close()

        self._listen_sock = None
        self._egress_sock = None

    # ------------------------------------------------------------ capture

    def _open_capture(self):
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.capture_path = self.capture_dir / f"digico-capture_{stamp}.log"

        # Line-buffered, so a crash loses at most the line being written.
        self._file = open(self.capture_path, "w", encoding="utf-8", buffering=1)
        self._file.write(
            f"# CLMix {VERSION} - DiGiCo App Capture\n"
            f"# started   {datetime.now().isoformat(sep=' ')}\n"
            f"# console   {self.mixer_ip}:{self.send_port} "
            f"(replies to port {self.recv_port}), "
            f"via {self.mixer_bind_ip or 'any adapter'}\n"
            f"# app side  {self.listen_ip or 'all adapters'}:{self.send_port}\n"
            "#\n"
            "# One line per datagram, tab-separated:\n"
            "#   time  direction  peer  length  decoded  raw-hex\n"
            "# APP->MIXER    the app sent this; relayed to the console as-is\n"
            "# CLMIX->MIXER  CLMix's own traffic, recorded for context only\n"
            "# MIXER->APP    the console sent this; relayed to the app as-is\n"
            "#               (and, as always, read by CLMix too)\n"
            "# MIXER->CLMIX  the console sent this while no app was connected\n"
            "# raw-hex is the complete datagram, never truncated - it is the\n"
            "# record; 'decoded' is python-osc's reading of it, for convenience.\n"
            "# /Meters/values is relayed but not recorded.\n"
            "# Lines starting with # are CLMix's own notes.\n"
            "#\n"
        )

    def _record(self, direction, peer, data):
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
        line = (f"{stamp}\t{direction}\t{peer[0]}:{peer[1]}\t{len(data)}\t"
                f"{describe(data)}\t{data.hex()}\n")

        with self._file_lock:
            if self._file is not None:
                self._file.write(line)

    def _note(self, text):
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")

        with self._file_lock:
            if self._file is not None:
                self._file.write(f"# {stamp} {text}\n")

    def _close_capture(self, reason):
        with self._file_lock:
            if self._file is None:
                return

            self._file.write(
                f"# {reason} {datetime.now().isoformat(sep=' ')} - "
                f"{self.packets_from_app} datagrams from the app, "
                f"{self.packets_to_app} relayed to it\n"
            )
            self._file.close()
            self._file = None
