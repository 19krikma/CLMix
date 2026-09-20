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

Metering is the one thing the console cannot simply share. It keeps a
single global meter slot table and reports slot numbers only, so two
clients subscribing at once each decode the other's meters as garbage -
and the official app rewrites the whole table about once a second
(PROTOCOL.md, "Subscribing"), so they would take it from each other
continuously.

There are two ways out of that, and which one applies is the operator's
choice.

By default the bridge is all CLMix does while it runs - DiGiCo App
mode. The mixer view and every phone are locked, apart from its
heartbeat CLMix sends the console nothing of its own, and the app is
left to subscribe to the table exactly as it would on a desk with
nothing else connected. Nobody can move the desk underneath a capture
and the capture holds the app's traffic rather than a mix of both,
which is what you want when the point of the capture is to learn what
the app does.

With "Keep CLMix usable while capturing" on, none of that lockdown
applies and the meter table is shared rather than surrendered:
AppMeterSubscription below takes the app's /Meters/clear and
/Meters/request before they reach the console, CLMix subscribes to
everything either side wants, and each /Meters/values that comes back
is re-numbered into the app's own slots and handed to it. Both ends see
their own meters and neither can tell. The capture stays readable
because CLMix's traffic is tagged CLMIX->MIXER and the app's
intercepted subscription is tagged APP->CLMIX; there are simply two
clients in the log rather than one. Use it when the capture is
incidental and the desk still has to be mixed on.

Either way the worker is put into the right state by one call,
MixerWorker.apply_capture_mode.

See docs/mixer_protocol/PROTOCOL.md for the beacon layout and for what
the official client was already seen to do on connect.
"""

import ipaddress
import socket
import threading
import time
from collections import deque
from datetime import datetime

from pythonosc.osc_bundle import OscBundle
from pythonosc.osc_message import OscMessage
from pythonosc.osc_message_builder import OscMessageBuilder
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

# The app's end of the meter subscription, matched on the padded address
# bytes the same way. These are the two the bridge answers itself when
# it is serving the app's meters rather than letting it subscribe.
METER_CLEAR_PREFIX = b"/Meters/clear\x00"
METER_REQUEST_PREFIX = b"/Meters/request/"

# The app rebuilds its whole subscription about once a second - a
# /Meters/clear, then one /Meters/request per slot a few milliseconds
# later (PROTOCOL.md, "Subscribing"). Acting on the clear as it lands
# would empty the app's half of the console's table and refill it a slot
# at a time, once a second, blanking everyone's meters each go. So the
# requests are collected and committed once they stop arriving, and a
# commit identical to the last one changes nothing.
METER_COMMIT_QUIET_SECONDS = 0.3

RECV_TIMEOUT_SECONDS = 0.2

# How many of the latest records the live view in Setup can catch up
# from. Only a buffer between this thread and the UI's 100ms poll - the
# file is the record, and a view that falls further behind than this
# simply skips ahead.
LIVE_VIEW_RECORDS = 500


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


class AppMeterSubscription:
    """The DiGiCo app's meter subscription, held here instead of on the desk.

    The console has one meter slot table for everyone, so while the app
    owns it CLMix has none. The way round that is not to share the table
    but to stop the app using it: its /Meters/clear and /Meters/request
    never reach the console, CLMix subscribes to everything either side
    wants, and the values that come back are re-numbered into the app's
    own slots and handed to it. As far as the app can tell the console
    answered.

    Slot numbers are each client's own to assign, which is what makes
    this possible: nothing in a /Meters/values packet says which channel
    a slot means, so the only thing that needs translating is the
    numbers.
    """

    def __init__(self):
        self._lock = threading.Lock()
        # app slot -> meter address, as the app last settled on it.
        self.slots = {}
        # meter address -> the app slots bound to it. Usually one, but
        # nothing stops the app pointing two slots at one address.
        self._by_address = {}
        self._pending = {}
        self._collecting = False
        self._last_request = 0.0

    def clear(self):
        """The app resetting its subscription. Starts a new collection
        rather than emptying anything - see METER_COMMIT_QUIET_SECONDS."""
        with self._lock:
            self._pending = {}
            self._collecting = True
            self._last_request = time.monotonic()

    def request(self, slot, address):
        with self._lock:
            if not self._collecting:
                # A request with no clear before it: the app adding a
                # slot to what it already has.
                self._pending = dict(self.slots)
                self._collecting = True

            self._pending[slot] = address
            self._last_request = time.monotonic()

    def commit_if_settled(self):
        """The app's addresses once its burst has stopped, or None if
        there is nothing new to apply. Called from the relay loop."""
        with self._lock:
            if not self._collecting:
                return None

            if time.monotonic() - self._last_request < METER_COMMIT_QUIET_SECONDS:
                return None

            self._collecting = False

            if self._pending == self.slots:
                return None

            self.slots = self._pending
            self._pending = {}

            self._by_address = {}
            for slot, address in sorted(self.slots.items()):
                self._by_address.setdefault(address, []).append(slot)

            return list(self._by_address)

    def renumber(self, pairs, console_addresses):
        """[app slot, value, ...] for whatever of `pairs` the app wants.

        `pairs` is the console's own [slot, value, ...] and
        `console_addresses` maps its slot numbers to addresses. Slots the
        app never asked for are dropped, which is most of them whenever
        CLMix is metering strips of its own.
        """
        out = []

        with self._lock:
            if not self._by_address:
                return out

            for slot, value in pairs:
                address = console_addresses.get(slot)
                if address is None:
                    continue

                for app_slot in self._by_address.get(address, ()):
                    out += [app_slot, value]

        return out


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
                 mixer_bind_ip=None, listen_ip=None, capture_dir=CAPTURE_DIR,
                 serve_meters=None, console_meters=None):
        self.mixer_ip = mixer_ip
        self.send_port = send_port
        self.recv_port = recv_port
        self.mixer_bind_ip = mixer_bind_ip or None
        # The adapter the iPad is on (Setup's Server adapter), or None for
        # every adapter except the console's own network.
        self.listen_ip = listen_ip or None
        self.capture_dir = capture_dir

        # Set when CLMix is to own the console's meter table and serve
        # the app from it: serve_meters(addresses) tells CLMix what the
        # app wants, console_meters() gives back {console slot: address}.
        # Both None means the old arrangement - the app subscribes to the
        # console itself and CLMix does without meters.
        self.serve_meters = serve_meters
        self.console_meters = console_meters
        self.app_meters = AppMeterSubscription() if serve_meters else None

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

        # (seq, record) for the live view - see recent_records(). Guarded
        # by _file_lock, since every record is appended while writing it.
        self._recent = deque(maxlen=LIVE_VIEW_RECORDS)
        self._recent_seq = 0

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

        if sock is not None and self._relay_meters(data, apps, sock):
            return

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

    def _relay_meters(self, data, apps, sock):
        """Send the app its own meters, built from CLMix's subscription.

        Returns False if this is not something to serve that way, leaving
        the caller to relay the datagram as it would anything else.
        """
        if self.app_meters is None or not data.startswith(METER_VALUES_PREFIX):
            return False

        try:
            message = OscMessage(data)
            args = list(message.params)
        except Exception:
            # Same reasoning as describe(): a malformed meter packet is
            # not worth losing the connection over. Nothing goes to the
            # app for it, and the next one lands 35ms later.
            return True

        pairs = [(int(args[i]), args[i + 1])
                 for i in range(0, len(args) - 1, 2)]
        renumbered = self.app_meters.renumber(pairs, self.console_meters())

        if not renumbered:
            # Nothing the app asked for changed in this packet.
            return True

        builder = OscMessageBuilder(address="/Meters/values")
        for value in renumbered:
            builder.add_arg(value, builder.ARG_TYPE_INT)

        payload = builder.build().dgram

        for ip in apps:
            try:
                sock.sendto(payload, (ip, self.recv_port))
                self.packets_to_app += 1
            except OSError as ex:
                self._note(f"meter relay to app {ip} failed: {ex}")

        return True

    def _intercept_from_app(self, data):
        """Take the app's meter subscription for CLMix to answer.

        True when the datagram was handled here and must not go to the
        console - sending it on would hand the console's one meter table
        straight back to the app, which is the thing being avoided.
        """
        if self.app_meters is None:
            return False

        if data.startswith(METER_CLEAR_PREFIX):
            self.app_meters.clear()
            return True

        if not data.startswith(METER_REQUEST_PREFIX):
            return False

        try:
            message = OscMessage(data)
            slot = int(message.address.rsplit("/", 1)[1])
            address = str(list(message.params)[0])
        except Exception:
            # Not a slot number and an address after all - let it through
            # rather than swallow something that was never a subscription.
            return False

        self.app_meters.request(slot, address)
        return True

    def from_clmix(self, data):
        """A datagram CLMix itself sent the console, for the record only."""
        self._record("CLMIX->MIXER", (self.mixer_ip, self.send_port), data)

    # -------------------------------------------------------------- relay

    def _relay_from_app(self):
        while self._running:
            self._expire_apps()
            self._commit_app_meters()

            try:
                data, sender = self._listen_sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                # Socket closed under us by stop().
                break

            self._note_app(sender[0])

            if self._intercept_from_app(data):
                self._record("APP->CLMIX", sender, data)
                continue

            try:
                self._egress_sock.sendto(data, (self.mixer_ip, self.send_port))
                self.packets_from_app += 1
            except OSError as ex:
                self._note(f"relay to console failed: {ex}")

            self._record("APP->MIXER", sender, data)

    def _commit_app_meters(self):
        if self.app_meters is None:
            return

        addresses = self.app_meters.commit_if_settled()

        if addresses is None:
            return

        self.serve_meters(addresses)
        self._note(f"serving the app {len(addresses)} meter(s) from CLMix's "
                   "own subscription")

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
            "# APP->CLMIX    the app sent this and CLMix answered it itself,\n"
            "#               without passing it on - its meter subscription,\n"
            "#               when CLMix is serving the app's meters\n"
            "# CLMIX->MIXER  CLMix's own traffic, recorded for context only\n"
            "# MIXER->APP    the console sent this; relayed to the app as-is\n"
            "#               (and, as always, read by CLMix too)\n"
            "# MIXER->CLMIX  the console sent this while no app was connected\n"
            "# raw-hex is the complete datagram, never truncated - it is the\n"
            "# record; 'decoded' is python-osc's reading of it, for convenience.\n"
            "# /Meters/values is relayed but not recorded - nor are the\n"
            "# ones CLMix builds for the app when it serves its meters.\n"
            "# Lines starting with # are CLMix's own notes.\n"
            "#\n"
        )

    def recent_records(self, after_seq=0):
        """Records written since after_seq, oldest first, for the live view.

        Each is (seq, record): record is ("packet", time, direction, peer,
        length, decoded, hex) or ("note", time, text) - the same fields
        the file line holds, unjoined, so the view can lay them out and
        color them without parsing its own text back. Still readable
        after stop(), so the view can show how a capture ended.
        """
        with self._file_lock:
            return [item for item in self._recent if item[0] > after_seq]

    def _record(self, direction, peer, data):
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
        fields = (stamp, direction, f"{peer[0]}:{peer[1]}", str(len(data)),
                  describe(data), data.hex())

        with self._file_lock:
            if self._file is not None:
                self._file.write("\t".join(fields) + "\n")
                self._remember(("packet",) + fields)

    def _note(self, text):
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")

        with self._file_lock:
            if self._file is not None:
                self._file.write(f"# {stamp} {text}\n")
                self._remember(("note", stamp, text))

    def _remember(self, record):
        # Caller holds _file_lock.
        self._recent_seq += 1
        self._recent.append((self._recent_seq, record))

    def _close_capture(self, reason):
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
        text = (f"{reason} - {self.packets_from_app} datagrams from the app, "
                f"{self.packets_to_app} relayed to it")

        with self._file_lock:
            if self._file is None:
                return

            self._file.write(f"# {stamp} {text}\n")
            self._remember(("note", stamp, text))
            self._file.close()
            self._file = None
