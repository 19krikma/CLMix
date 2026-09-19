import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
import ipaddress
import threading
import queue
import re
import socket
import json
import math
import time
from pathlib import Path

import sv_ttk
from pythonosc.osc_bundle import OscBundle
from pythonosc.osc_bundle import ParseError as BundleParseError
from pythonosc.osc_message import OscMessage, ParseError
from pythonosc.osc_message_builder import OscMessageBuilder
from pythonosc.parsing import osc_types

from services import updater
from services.backup_store import BackupStore
from services.digico_bridge import CAPTURE_DIR, DigicoAppBridge
from services.log_store import capture, log
from services.network_info import get_ethernet_ip, list_ipv4_interfaces
from services.preset_store import PresetStore
from services.remote_server import RemoteServer
from services.update_checker import check_for_update
from services.user_store import UserStore
from ui.app_icon import ICON_PNG_BASE64
from ui.about_window import AboutWindow
from ui.access_window import AccessPanel
from ui.aux_window import AuxPanel
from ui.backup_window import BackupWindow
from ui.logs_window import LogsWindow, open_folder
from ui.presets_window import PresetsWindow
from ui.show_backup_window import ShowBackupWindow
from version import VERSION

SETTINGS_PATH = Path.home() / ".clmix.json"

# Passed to tk.Tk(className=...) - see the comment where it is used. Tk
# capitalizes only the first letter, so the WM_CLASS this actually produces
# is "Clmix", which is the spelling packaging/linux/clmix.desktop matches.
WM_CLASS_NAME = "CLMix"

# Startup window size, and also its minimum - see MainWindow.__init__.
WINDOW_WIDTH = 800
WINDOW_HEIGHT = 500

# How long after launch the update check fires. Late enough to stay out of
# the way of connecting to the mixer, which is what the operator actually
# opened the app to do.
STARTUP_UPDATE_CHECK_MS = 5000


# Addresses worth keeping in the in-memory cache, mirroring the
# webmixer Node server's maybeCacheResponse() address list.
CACHEABLE_ADDRESSES = [
    re.compile(r"^/Console/Input_Channels$"),
    re.compile(r"^/Console/Input_Channels/modes$"),
    re.compile(r"^/Console/Aux_Outputs/modes$"),
    re.compile(r"^/Aux_Outputs/\d+/Buss_Trim/name$"),
    re.compile(r"^/Input_Channels/\d+/Channel_Input/name$"),
    re.compile(r"^/Input_Channels/\d+/Aux_Send/\d+/send_level$"),
    re.compile(r"^/Input_Channels/\d+/Aux_Send/\d+/send_pan$"),
    re.compile(r"^/Input_Channels/\d+/Aux_Send/\d+/send_on$"),

    # The channel's own fader, mute and pan - the main mix rather than
    # any one send. Nothing on the desktop reads these; they are here for
    # the phone's Full Mixer Control mode, which rides them the way the
    # aux screens ride the sends above.
    re.compile(r"^/Input_Channels/\d+/fader$"),
    re.compile(r"^/Input_Channels/\d+/mute$"),
    re.compile(r"^/Input_Channels/\d+/Panner/pan$"),

    # Head-amp gain, digital trim and 48V, for Full Mixer Control's
    # channel input sheet.
    re.compile(r"^/Input_Channels/\d+/Channel_Input/analog_gain$"),
    re.compile(r"^/Input_Channels/\d+/Channel_Input/trim$"),
    re.compile(r"^/Input_Channels/\d+/Channel_Input/phantom$"),
    re.compile(r"^/Input_Channels/\d+/Channel_Input/phase$"),
]

RENAME_SNAPSHOT_PATTERN = re.compile(r"^/Snapshots/Rename_Snapshot/(\d+)$")

# The console broadcasts these immediately when a snapshot is recalled
# (from the surface or the recall list), with the new snapshot number
# embedded in the address itself rather than as an argument - and they
# arrive faster than the console re-broadcasting /Snapshots/Current_Snapshot.
SNAPSHOT_CHANGED_PATTERN = re.compile(
    r"^/Snapshots/(?:Change_Surface_Snapshot|Recall_Snapshot)/(\d+)$"
)


class MixerWorker(threading.Thread):

    # How often to poll the mixer for a sign of life, and how long to go
    # without any reply before treating the connection as dead - UDP has
    # no built-in "connection lost" notification, so this is the only way
    # to notice the mixer went away (power loss, cable pull, network
    # drop) rather than just sitting there silently disconnected.
    HEARTBEAT_INTERVAL_SECONDS = 3.0
    HEARTBEAT_TIMEOUT_SECONDS = 10.0

    # How long a boot query goes unanswered before it is sent again. UDP
    # promises no delivery, so a dropped request has to be retried or the
    # load stalls at that parameter forever - but retrying on every
    # inbound message (which is what having no in-flight tracking
    # amounted to) turned each surplus message into a duplicate of every
    # remaining query. Long enough that a LAN round trip never trips it,
    # short enough that a real loss is not noticeable.
    BOOT_RETRY_SECONDS = 0.5

    # Ceiling on how many meter slots to ask the console for at once.
    # The only hard evidence is the official client using 12; no maximum
    # is documented, so this is a deliberately generous guess that still
    # refuses to ask for something absurd when several phones each pick a
    # different bank. Sources are filled desktop-first, so overflow costs
    # a phone its meters, never the operator at the desk.
    MAX_METER_SLOTS = 64

    # Meter wire format, reverse-engineered from captures of the official
    # DiGiCo client - see docs/mixer_protocol/PROTOCOL.md "Metering".
    # Each /Meters/values int packs two 8-bit fields (the middle byte is
    # always zero): peak in the high byte, RMS in the low byte. A field is
    # simply dB below zero - 24 means -24 dB - quantised to 3 dB steps,
    # which is why every observed value is a multiple of 3. Across both
    # reference captures the fields take every multiple of 3 from 6 to 60
    # and then nothing until 126, so -60 dB is the bottom of the scale
    # (matching the console's own printed meter scale) and 126 is a
    # no-signal sentinel rather than a measurement.
    METER_FLOOR_FIELD = 126
    METER_FLOOR_DB = -60.0

    # /Console/Input_Channels/modes and /Console/Aux_Outputs/modes carry
    # one entry per channel/bus: 1 is mono, 2 is stereo.
    #
    # For an input channel this decides metering: a stereo channel meters
    # as two independent legs, each on its own subscription slot, while a
    # mono one has only a left leg. The console exposes
    # .../post_meter/right on every channel regardless, so the modes list
    # - not the address space - is what says whether the right leg
    # carries anything.
    #
    # For an aux bus it decides whether send_pan means anything at all:
    # a mono bus sums to one leg, so panning a send into it is a no-op.
    # The console accepts and echoes the write either way, which is
    # exactly why the mode has to be consulted rather than inferred from
    # how the bus responds.
    MODE_STEREO = 2
    METER_LEGS_MONO = ("left",)
    METER_LEGS_STEREO = ("left", "right")

    # How often the meter stream gets one summary line, for when the
    # Logs window has turned meter capture off. Meters are the only
    # traffic voluminous enough (~30Hz) to be worth turning down, and
    # even then they are counted rather than ignored, so the log can
    # still answer whether the console is metering at all and whether it
    # is answering with slots nobody subscribed.
    METER_LOG_INTERVAL_SECONDS = 5.0

    # How far into nested bundles to keep unpacking. The console has
    # never been seen to send a bundle at all, let alone a nested one,
    # so this is only here to keep a malformed datagram from recursing
    # without end - the log says when it stops rather than pretending
    # the contents were read.
    MAX_BUNDLE_DEPTH = 8

    # How many of a datagram's own bytes to put in the log beside the
    # decoded form. Enough to read an address, its type tags and the
    # first arguments off the wire, short enough that one long name list
    # cannot dump kilobytes of hex into the file.
    RAW_PREVIEW_BYTES = 96

    # Receive buffer for the console's replies. The OS default (~208 KB on
    # Linux, less on Windows) holds only a couple of hundred small
    # datagrams, and the console answers a whole-strip query with a burst
    # of about that many - so a Show Backup reading several strips at
    # once lost the tail of each burst before this loop could drain it.
    # The OS may cap what it grants (Linux: net.core.rmem_max); less is
    # still better than the default, and failing to set it is harmless.
    RECV_BUFFER_BYTES = 4 * 1024 * 1024

    def __init__(self, mixer_ip, send_port, recv_port,
                 command_queue, message_queue, bind_ip=None):
        super().__init__(daemon=True)

        self.mixer_ip = mixer_ip
        self.send_port = send_port
        self.recv_port = recv_port

        # Which local address to talk to the console from, or None to let
        # the routing table choose. Only matters on a machine with more
        # than one network card, where the console is reachable down one
        # of them and the route is ambiguous or simply wrong.
        self.bind_ip = bind_ip or None

        self.command_queue = command_queue
        self.message_queue = message_queue

        self.running = True
        self.send_sock = None
        self.recv_sock = None

        self.cache = {}
        self.banks = {}
        self.loaded = False

        self.snapshot_name = None
        self._snapshot_name_requested = False
        self.snapshot_names = {}

        # Bumped every time the console moves to a different snapshot.
        # A recall rewrites levels, pans and mutes across the whole desk
        # at once, and the console does not broadcast the thousands of
        # individual parameter changes that implies - so cached values
        # are stale from that moment until something asks again. Both the
        # desktop panel and each connected phone watch this and re-query
        # what they are actually showing; a plain counter rather than a
        # queued message because there is more than one such watcher and
        # they live on different threads.
        self.snapshot_epoch = 0

        self._last_received_at = None
        self._last_heartbeat_sent_at = 0.0

        # (address, sent_at) for the one outstanding boot query, so a
        # reply that arrives while it is still in flight does not cause
        # it to be asked for a second time. See request_next_parameter().
        self._pending_request = (None, 0.0)

        # What loading is currently waiting on, as a phrase to show the
        # operator ("Channels 12/72"). Read from the UI thread every
        # frame rather than pushed through message_queue: the stage
        # changes on nearly every reply, and that queue is drained at
        # 100ms and logs every entry it carries.
        self.loading_stage = None

        # Who wants meters, by source name -> channels. The console has a
        # single global slot table, so every surface that wants metering
        # has to share it: the desktop panel registers its visible bank,
        # each connected phone registers its own, and the subscription
        # sent to the console is the union. Without this a phone on a
        # different bank than the desktop would meter nothing at all.
        self._meter_sources = {}

        # DiGiCo App mode: CLMix is only a bridge for the official app
        # (see services/digico_bridge.py) and sends the console nothing
        # of its own beyond the heartbeat that keeps this connection
        # honest. Everything else CLMix queues is dropped in
        # _drain_commands, which is what makes the desktop and phones
        # unable to move anything whatever state their screens are in.
        #
        # Meters especially: the console's slot table is global and
        # /Meters/values names slots, not channels, so CLMix and the app
        # cannot both meter at once. While this is set the app has the
        # table to itself and anything on /Meters/values is its, meaning
        # nothing here. Surfaces still register what they want, so
        # leaving the mode picks up exactly where the screen is.
        self.bridge_only = False

        # The DigicoAppBridge relaying this connection, when capture is
        # on; handed every datagram sent and received. See
        # services/digico_bridge.py.
        self.bridge = None

        # Called with (address, args, type_tags) for every message the
        # console sends bar /Meters/values, whether CLMix keeps it or not
        # - how a Show Backup reads whole strips, which arrive as bursts
        # of hundreds of addresses nothing else here caches. Runs on this
        # thread, so it must only collect. Returning True means the sink
        # has recorded the message itself and it is left out of the debug
        # log: a full backup is hundreds of thousands of strip replies,
        # every one of which lands in the backup's own files anyway. See
        # services/show_backup.py.
        self.message_sink = None

        # slot number -> (channel index, leg), mirroring whatever the UI
        # last subscribed via subscribe_meters(). /Meters/values reports
        # slots, not addresses, so this is the only way back to a channel.
        # A stereo channel occupies two slots, one per leg.
        self.meter_slots = {}
        # (channel index, leg) -> (peak_db, rms_db); None means no signal.
        self.meter_levels = {}
        # (channel index, leg) -> how many samples have landed for it. The
        # console only sends a slot when its value changed, so a bump here
        # is the only way the UI can tell "a fresh sample arrived" from
        # "nothing was sent because the level is unchanged" - which the
        # meter's release ballistics depend on (see
        # MainWindow.refresh_meters).
        self.meter_seq = {}

        # Meter traffic counted while meter capture is off - see
        # METER_LOG_INTERVAL_SECONDS and CaptureSettings. Untouched
        # while capture is on, when every packet is logged instead.
        self._meter_packets = 0
        self._meter_pairs = 0
        self._meter_unmapped = 0
        self._meter_window_started_at = 0.0

    def run(self):
        try:
            log("info", f"Connecting to {self.mixer_ip}:{self.send_port} "
                f"(recv on port {self.recv_port})")
            self.message_queue.put(("status", "Connecting"))

            self.send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

            if self.bind_ip:
                # Port 0 - the source port does not matter, only which
                # card the packets leave by. Without this the routing
                # table picks, which on a two-card machine may not be the
                # card the console is on.
                self.send_sock.bind((self.bind_ip, 0))

            log("debug", f"Send socket created (from {self.bind_ip or 'any'})")

            self.recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                self.recv_sock.setsockopt(
                    socket.SOL_SOCKET, socket.SO_RCVBUF, self.RECV_BUFFER_BYTES
                )
            except OSError as ex:
                log("warning", f"Could not enlarge the receive buffer: {ex}")
            self.recv_sock.bind((self.bind_ip or "", self.recv_port))
            self.recv_sock.settimeout(0.1)
            log("debug", f"Recv socket bound on "
                f"{self.bind_ip or 'all adapters'}:{self.recv_port}")

            self.message_queue.put(("status", "Connected"))
            log("info", "Connected to mixer, loading parameters...")

            self._last_received_at = time.monotonic()

            # Start the heartbeat clock here rather than leaving it at 0,
            # which made the very first loop iteration fire one - its
            # reply then landed in the middle of loading as a message the
            # boot sequence had not asked for, and (before the in-flight
            # check in request_next_parameter) that alone was enough to
            # start duplicating every remaining query. Nothing needs a
            # liveness probe during load anyway: the replies are the
            # liveness.
            self._last_heartbeat_sent_at = time.monotonic()

            self.request_next_parameter()

            while self.running:
                self.receive_osc()
                self._flush_meter_log()
                self._check_heartbeat()
                self._drain_commands()

                # Loading is otherwise driven entirely by inbound replies,
                # so a request that never arrives (or whose reply does
                # not) would stall it with nothing left to restart it.
                # The in-flight check makes this a no-op until the
                # outstanding query has actually timed out.
                if not self.loaded:
                    self.request_next_parameter()

        except Exception as ex:
            log("error", f"Worker error: {ex!r}")
            self.message_queue.put(("status", f"Error: {ex}"))

        finally:
            if self.send_sock:
                self.send_sock.close()

            if self.recv_sock:
                self.recv_sock.close()

            log("info", "Disconnected from mixer")
            self.message_queue.put(("status", "Disconnected"))

    def receive_osc(self):
        try:
            data, sender = self.recv_sock.recvfrom(65535)
        except socket.timeout:
            return

        self._last_received_at = time.monotonic()

        # Relayed before CLMix reads it, so the app is never kept
        # waiting on this side's own handling.
        bridge = self.bridge
        if bridge is not None:
            bridge.from_console(data, sender)

        self._handle_datagram(data, sender)

    def _handle_datagram(self, data, sender, depth=0):
        """Log one inbound datagram, then act on whatever it carries.

        Everything that lands on the receive socket comes through here
        and reaches the debug log whether or not this app has any use
        for it: every message, every message nested inside a bundle, and
        the bytes of anything that decodes as neither. The only traffic
        that can be turned down is metering, and only to a count (see
        CaptureSettings) - nothing is ever dropped unrecorded.
        """
        if depth > self.MAX_BUNDLE_DEPTH:
            self._log_received(
                sender,
                f"bundle nested past {self.MAX_BUNDLE_DEPTH} levels, "
                f"{len(data)} bytes left unread",
                depth
            )
            return

        if OscBundle.dgram_is_bundle(data):
            try:
                bundle = OscBundle(data)
            except (BundleParseError, ParseError):
                self._log_unparsed(data, sender, depth)
                return

            self._log_received(
                sender,
                f"#bundle timestamp {bundle.timestamp} "
                f"({bundle.num_contents} elements, {len(data)} bytes)",
                depth
            )

            for index in range(bundle.num_contents):
                self._handle_datagram(
                    bundle.content(index).dgram, sender, depth + 1
                )

            return

        try:
            message = OscMessage(data)
        except ParseError:
            # A truncated datagram, or traffic from something else that
            # found this port. Dropping these silently (which is what
            # this used to do) made "the console is sending nothing" and
            # "the console is sending something we cannot read" look
            # identical in the log, so they get reported as bytes.
            self._log_unparsed(data, sender, depth)
            return

        self._dispatch_message(
            message.address, list(message.params), data, sender, depth
        )

    def _dispatch_message(self, address, args, data, sender, depth=0):
        # Meters are stored here rather than pushed through
        # message_queue as state, but they are logged like any other
        # message unless the Logs window has turned that down - at which
        # point they are counted instead, never simply discarded.
        if address == "/Meters/values":
            self._handle_meter_values(args)

            if capture.meters:
                self._log_received(
                    sender, self._describe(address, args, data), depth
                )
            else:
                self._count_meter_packet(args)

            return

        sink = self.message_sink
        recorded = sink is not None and sink(address, args, self._type_tags(data))

        snapshot_changed = SNAPSHOT_CHANGED_PATTERN.match(address)

        if address == "/Layout/Layout/Banks":
            self._store_bank(args)
        elif address == "/Snapshots/Current_Snapshot":
            self._handle_current_snapshot(args)
        elif snapshot_changed:
            self._handle_current_snapshot([int(snapshot_changed.group(1))])
        elif address == "/Snapshots/name":
            self._handle_snapshot_name(args)
        elif RENAME_SNAPSHOT_PATTERN.match(address):
            self._handle_snapshot_renamed(address, args)
        elif any(pattern.match(address) for pattern in CACHEABLE_ADDRESSES):
            self.cache[address] = args

        if not recorded:
            self._log_received(sender, self._describe(address, args, data), depth)

        if not self.loaded:
            self.request_next_parameter()

    def _describe(self, address, args, data):
        """One message written out in full: what it says it is, what it
        decoded to, and the bytes it arrived as.

        The type tags and the raw bytes are there because the decoded
        form alone hides the things worth knowing about an undocumented
        parameter - an int that arrives as a float, a flag that is
        really an enum, a trailing argument pythonosc drops - and this
        log is what the protocol notes get written from.
        """
        return (
            f"{address} {self._type_tags(data)} {args} | "
            f"{len(data)} bytes | {self._hex_preview(data)}"
        )

    @staticmethod
    def _type_tags(data):
        """The console's own type tag string for a message, e.g. ",iiis".

        Read back off the wire rather than inferred from the decoded
        arguments, so it says what the console claimed to send even when
        that is not what came out the other side. "," is a message with
        no arguments at all; "?" is one whose tags could not be read.
        """
        try:
            _address, index = osc_types.get_string(data, 0)

            if not data[index:]:
                return ","

            tags, _index = osc_types.get_string(data, index)
        except osc_types.ParseError:
            return "?"

        return tags

    def _hex_preview(self, data):
        preview = data[:self.RAW_PREVIEW_BYTES]
        truncated = "..." if len(data) > self.RAW_PREVIEW_BYTES else ""

        return f"{preview.hex(' ')}{truncated}"

    def _log_received(self, sender, text, depth=0):
        """One inbound line for the debug log, via the UI message pump.

        depth indents a bundle's contents under the bundle header so a
        grouped update reads as one thing rather than as a run of
        unrelated messages that happen to share a timestamp.
        """
        self.message_queue.put((
            "message",
            f"Received from {sender[0]}:{sender[1]}: {'  ' * depth}{text}"
        ))

    def _log_unparsed(self, data, sender, depth=0):
        """Report a datagram no decoder accepted, rather than dropping it."""
        preview = data[:self.RAW_PREVIEW_BYTES]
        printable = "".join(
            chr(byte) if 32 <= byte < 127 else "." for byte in preview
        )
        truncated = "..." if len(data) > self.RAW_PREVIEW_BYTES else ""

        self._log_received(
            sender,
            f"undecodable datagram, {len(data)} bytes | "
            f"{printable}{truncated} | {self._hex_preview(data)}",
            depth
        )

    def _count_meter_packet(self, args):
        """Tally one /Meters/values packet for the periodic summary line."""
        if not self._meter_packets:
            # Start of a fresh reporting window. Timed from the first
            # packet in it rather than from the last summary, so a
            # summary that follows a quiet spell reports the length of
            # the burst and not the length of the silence.
            self._meter_window_started_at = time.monotonic()

        self._meter_packets += 1

        for i in range(0, len(args) - 1, 2):
            self._meter_pairs += 1

            if int(args[i]) not in self.meter_slots:
                self._meter_unmapped += 1

    def _flush_meter_log(self):
        """Report meters counted while meter capture was off.

        A no-op with capture on, where the counters stay empty because
        every packet is logged as it lands. Driven from the run loop
        rather than from the packets themselves so the last burst before
        metering stops is still reported instead of sitting in the
        counters until the next packet arrives.
        """
        if not self._meter_packets:
            return

        elapsed = time.monotonic() - self._meter_window_started_at

        # Capture coming back on closes the window early, so packets
        # counted while it was off are reported then rather than waiting
        # out an interval that no longer applies.
        if not capture.meters and elapsed < self.METER_LOG_INTERVAL_SECONDS:
            return

        unmapped = (
            f", {self._meter_unmapped} for unsubscribed slots"
            if self._meter_unmapped else ""
        )
        self.message_queue.put((
            "message",
            f"Received: /Meters/values x{self._meter_packets} "
            f"({self._meter_pairs} slot values{unmapped}) in {elapsed:.1f}s "
            f"across {len(self.meter_slots)} subscribed slots"
        ))

        self._meter_packets = 0
        self._meter_pairs = 0
        self._meter_unmapped = 0

    def _check_heartbeat(self):
        now = time.monotonic()

        if now - self._last_received_at > self.HEARTBEAT_TIMEOUT_SECONDS:
            log("error", f"No response from mixer for "
                f"{self.HEARTBEAT_TIMEOUT_SECONDS:.0f}s - connection lost")
            self.running = False
            return

        if now - self._last_heartbeat_sent_at > self.HEARTBEAT_INTERVAL_SECONDS:
            self._last_heartbeat_sent_at = now
            self.send_osc("/Snapshots/Current_Snapshot/?", [])

    def _handle_current_snapshot(self, args):
        changed = self.cache.get("/Snapshots/Current_Snapshot") != args
        self.cache["/Snapshots/Current_Snapshot"] = args

        if changed:
            self.snapshot_epoch += 1
            self.snapshot_name = None
            self._snapshot_name_requested = False
            self._request_snapshot_name()

    def _handle_snapshot_name(self, args):
        if not args:
            return

        # The console broadcasts one of these per snapshot in response to
        # a single "/Snapshots/names/?" query, so this also builds up the
        # full index -> name catalog used for name-based access control.
        self.snapshot_names[int(args[0])] = args[-1]

        current = self.cache.get("/Snapshots/Current_Snapshot")

        if current is None or args[0] != current[0]:
            return

        self.snapshot_name = args[-1]
        self.message_queue.put(("snapshot", (current[0], self.snapshot_name)))

    def _handle_snapshot_renamed(self, address, args):
        match = RENAME_SNAPSHOT_PATTERN.match(address)

        if not args:
            return

        self.snapshot_names[int(match.group(1))] = args[0]

        current = self.cache.get("/Snapshots/Current_Snapshot")

        if current is None or int(match.group(1)) != current[0]:
            return

        self.snapshot_name = args[0]
        self.message_queue.put(("snapshot", (current[0], self.snapshot_name)))

    def _request_snapshot_name(self):
        if self.bridge_only and self.loaded:
            # Not asked for in DiGiCo App mode - the catalog loading built
            # usually already knows it. If not, leave_bridge_only asks.
            # Only once loaded: loading itself waits on this name, and it
            # all happens before the bridge opens, so none of it can land
            # in the middle of the app's session.
            current = self.cache.get("/Snapshots/Current_Snapshot")
            name = self.snapshot_names.get(int(current[0])) if current else None

            if name is not None:
                self.snapshot_name = name
                self.message_queue.put(("snapshot", (current[0], name)))

            return

        if not self._snapshot_name_requested:
            self.send_osc("/Snapshots/names/?", [])
            self._snapshot_name_requested = True

    def _store_bank(self, args):
        if len(args) < 6:
            return

        name = args[0]
        channels = []

        for i in range(4, len(args) - 1, 2):
            kind = args[i]
            index = args[i + 1]

            if kind == "Input_Channels":
                channels.append(int(index))

        if channels:
            self.banks[name] = channels

    def _next_boot_query(self):
        """(query, stage) for the next boot parameter still missing.

        (None, None) once everything below is cached. Pure lookup - it
        decides what to ask for without asking, so
        request_next_parameter() can compare it against what is already
        in flight before sending anything. The stage travels with the
        query because the counts that make it useful ("Channels 12/72")
        are only known here.
        """
        if "/Console/Input_Channels" not in self.cache:
            return "/Console/Channels/?", "Console"

        if "/Console/Aux_Outputs/modes" not in self.cache:
            return "/Console/Aux_Outputs/modes/?", "Aux layout"

        # Needed before the first meter subscription, since it decides how
        # many slots each channel takes - see subscribe_meters().
        if "/Console/Input_Channels/modes" not in self.cache:
            return "/Console/Input_Channels/modes/?", "Channel layout"

        aux_modes = self.cache["/Console/Aux_Outputs/modes"]
        total = len(aux_modes)
        for i in range(1, total + 1):
            address = f"/Aux_Outputs/{i}/Buss_Trim/name"
            if address not in self.cache:
                return f"{address}/?", f"Auxes {i}/{total}"

        channel_count = int(self.cache["/Console/Input_Channels"][0])
        for i in range(1, channel_count + 1):
            address = f"/Input_Channels/{i}/Channel_Input/name"
            if address not in self.cache:
                return f"{address}/?", f"Channels {i}/{channel_count}"

        if "/Snapshots/Current_Snapshot" not in self.cache:
            return "/Snapshots/Current_Snapshot/?", "Snapshots"

        return None, None

    def request_next_parameter(self):
        query, stage = self._next_boot_query()
        self.loading_stage = stage

        if query is None:
            if self.snapshot_name is None:
                # Self-guarding on _snapshot_name_requested, so unlike the
                # address queries above it never needed in-flight tracking.
                self.loading_stage = "Snapshot names"
                self._request_snapshot_name()
                return

            # Named rather than cleared: "Loaded" reaches the UI through
            # message_queue, which is drained at 100ms, and a stage of
            # None in that gap renders as a bare "Loading". The layout
            # query below is genuinely what is outstanding there.
            self.loading_stage = "Layout"
            self.loaded = True
            log("info", "Mixer fully loaded and ready")
            self.message_queue.put(("status", "Loaded"))
            self.send_osc("/Layout/Layout/Banks/?", [])
            return

        # Ask once, then wait for the answer. This runs on every inbound
        # message during load, and without the check below it re-sent
        # whatever was still uncached each time - so any message beyond
        # the one reply being waited for (the console's nine-message
        # topology burst, a heartbeat reply, an unsolicited broadcast)
        # duplicated every remaining boot query, permanently.
        now = time.monotonic()
        pending, sent_at = self._pending_request

        if query == pending and now - sent_at < self.BOOT_RETRY_SECONDS:
            return

        self._pending_request = (query, now)
        self.send_osc(query, [])

    @classmethod
    def decode_meter(cls, value):
        """Unpack one /Meters/values int into (peak_db, rms_db).

        Either field may be None, meaning no signal - the console sends
        the floor field (126) as a sentinel rather than as a measurement.
        """
        value = int(value)
        peak = (value >> 16) & 0xFF
        rms = value & 0xFF

        return (
            None if peak >= cls.METER_FLOOR_FIELD else float(-peak),
            None if rms >= cls.METER_FLOOR_FIELD else float(-rms),
        )

    def _handle_meter_values(self, args):
        if self.bridge_only:
            return

        # Flat [slot, value, slot, value, ...] pairs, carrying only the
        # slots that actually changed - so the length varies per packet
        # and slot n is NOT at index 2n. Always walk it as pairs.
        for i in range(0, len(args) - 1, 2):
            key = self.meter_slots.get(int(args[i]))

            if key is not None:
                self.meter_levels[key] = self.decode_meter(args[i + 1])
                self.meter_seq[key] = self.meter_seq.get(key, 0) + 1

    def channel_is_stereo(self, channel):
        """Whether channel is a stereo pair, per /Console/Input_Channels/modes.

        Falls back to mono when the modes list is missing or too short:
        metering one leg of a stereo channel under-reads, while metering
        a mono channel's absent right leg would show a dead bar, so mono
        is the safer guess with incomplete information.
        """
        return self._mode_is_stereo("/Console/Input_Channels/modes", channel)

    def aux_is_stereo(self, aux):
        """Whether aux bus is stereo, per /Console/Aux_Outputs/modes.

        Falls back to mono, i.e. no pan, when the modes list is missing
        or too short. Showing a pan control that silently does nothing is
        worse than omitting one that would have worked, and the list is
        fetched during boot so a missing entry means something is wrong
        rather than merely slow.
        """
        return self._mode_is_stereo("/Console/Aux_Outputs/modes", aux)

    def _mode_is_stereo(self, address, index):
        modes = self.cache.get(address, [])

        if not 1 <= index <= len(modes):
            return False

        return int(modes[index - 1]) == self.MODE_STEREO

    def channel_legs(self, channel):
        """The meter legs to subscribe and draw for channel."""
        return self.METER_LEGS_STEREO if self.channel_is_stereo(channel) \
            else self.METER_LEGS_MONO

    def subscribe_meters(self, channels, source="desktop"):
        """Register one surface's channels and re-bind the console's slots.

        One slot per leg: mono channels take a single slot, stereo
        channels two. Slot numbers are ours to assign and are handed back
        verbatim in /Meters/values, so nothing but this mapping needs to
        know that a strip's two bars are adjacent slots.

        Safe to call from any thread: the actual sends go out through
        command_queue on the worker thread. Only register what is on
        screen - this is a continuous ~30Hz stream, not a poll.
        """
        channels = list(channels)

        if self._meter_sources.get(source) == channels:
            # Re-registering the same set would otherwise clear and
            # rebuild the console's whole slot table for nothing, which
            # blanks every other surface's meters for a moment.
            return

        self._meter_sources[source] = channels
        self._rebuild_meter_subscription()

    def release_meters(self, source):
        """Drop a surface's claim - a phone disconnecting, say."""
        if self._meter_sources.pop(source, None) is not None:
            self._rebuild_meter_subscription()

    def _rebuild_meter_subscription(self):
        # Ordered union: the desktop's own strips claim slots first, so
        # if the cap below ever bites it is a phone that loses metering
        # rather than the operator at the console.
        wanted = []
        for source in sorted(self._meter_sources, key=lambda s: s != "desktop"):
            for channel in self._meter_sources[source]:
                if channel not in wanted:
                    wanted.append(channel)

        self.meter_slots = {}
        self.meter_levels = {}
        self.meter_seq = {}

        if self.bridge_only:
            return

        for channel in wanted:
            for leg in self.channel_legs(channel):
                if len(self.meter_slots) >= self.MAX_METER_SLOTS:
                    break
                self.meter_slots[len(self.meter_slots)] = (channel, leg)

        self.command_queue.put("/Meters/clear")

        for slot, (channel, leg) in self.meter_slots.items():
            self.command_queue.put(
                f"/Meters/request/{slot} "
                f"/Input_Channels/{channel}/Channel_Input/post_meter/{leg}"
            )

    # What CLMix may still send in DiGiCo App mode, besides the heartbeat
    # (which _check_heartbeat sends directly, not through the queue).
    # Only the one clear that hands the meter table over on the way in.
    BRIDGE_ONLY_ALLOWED = frozenset({"/Meters/clear"})

    def enter_bridge_only(self):
        """Switch to DiGiCo App mode. Safe from any thread.

        Clears the meter table once, so the app starts on an empty one
        rather than inheriting CLMix's slots, and blanks every meter here
        - desktop and phones - rather than leaving them frozen on the
        last reading.
        """
        if self.bridge_only:
            return

        self.bridge_only = True
        self.meter_slots = {}
        self.meter_levels = {}
        self.meter_seq = {}
        self.command_queue.put("/Meters/clear")

    def leave_bridge_only(self):
        """Back to normal: take the meter table back for whatever is on
        screen now, and fetch anything the mode kept CLMix from asking.

        Levels, pans and mutes are the panel's to re-read (it knows what
        is on screen); the worker's own cache stayed current throughout,
        since it went on reading every reply the console sent - the
        app's included.
        """
        if not self.bridge_only:
            return

        self.bridge_only = False
        self._rebuild_meter_subscription()

        if self.snapshot_name is None:
            self._snapshot_name_requested = False
            self.command_queue.put("/Snapshots/names/?")

    def send_osc(self, address, args, types=None):
        """Send one message. types, when given, is its OSC type tag
        string ("f", "s", "ff"...) - for a restore, which must write a
        value back exactly as the console reported it rather than as
        whatever python-osc would infer from the Python value."""
        log("debug", f"send_osc: {address} {args}")

        builder = OscMessageBuilder(address=address)

        for index, arg in enumerate(args):
            if types and index < len(types):
                builder.add_arg(arg, types[index])
            else:
                builder.add_arg(arg)

        dgram = builder.build().dgram
        self.send_sock.sendto(dgram, (self.mixer_ip, self.send_port))

        bridge = self.bridge
        if bridge is not None:
            bridge.from_clmix(dgram)

    def _drain_commands(self):
        # Pulling only one queued command per loop iteration (the old
        # behavior) capped outgoing throughput at roughly one command per
        # receive_osc() timeout (~0.1s) whenever the console was quiet -
        # nothing about the wire or the console actually limits this, it
        # was purely this loop's own pacing. Draining everything that's
        # piled up and coalescing by address means a burst of fader-drag
        # ticks for the same channel only sends its final value instead
        # of working through every stale intermediate one - queries
        # ("/address/?") are kept in their own bucket per address so one
        # landing between two drag ticks can never cause a set to that
        # same address to be silently dropped.
        pending = {}
        order = []

        while True:
            try:
                command = self.command_queue.get_nowait()
            except queue.Empty:
                break

            address = self.command_address(command)
            key = (address, address.endswith("/?"))

            if key not in pending:
                order.append(key)

            pending[key] = command

        for key in order:
            if self.bridge_only and key[0] not in self.BRIDGE_ONLY_ALLOWED:
                continue

            self.send_command(pending[key])

    @staticmethod
    def command_address(command):
        if isinstance(command, tuple):
            return command[0]

        return command.split(maxsplit=1)[0]

    def send_command(self, command):
        # A queued command is either the plain "address arg arg" string
        # that nearly all traffic uses, or an (address, [args]) tuple for
        # anything splitting on whitespace would mangle - a channel name
        # with a space in it being the case that forced the second form.
        # A third element, the type tags, pins the OSC types exactly.
        types = None

        if isinstance(command, tuple):
            address, args = command[0], command[1]
            if len(command) > 2:
                types = command[2]
        else:
            parts = command.split()
            address = parts[0]
            args = [self.parse_arg(part) for part in parts[1:]]

        self.send_osc(address, args, types)

        self.message_queue.put(
            ("message", f"Sent: {address} {args}")
        )

    @staticmethod
    def parse_arg(value):
        try:
            return float(value)
        except ValueError:
            return value

    def stop(self):
        self.running = False


def build_aux_list(worker, hidden=None):
    hidden = hidden or set()
    aux_modes = worker.cache.get("/Console/Aux_Outputs/modes", [])
    aux_list = []

    for i in range(1, len(aux_modes) + 1):
        name_key = f"/Aux_Outputs/{i}/Buss_Trim/name"
        name = worker.cache[name_key][0] \
            if name_key in worker.cache else f"Aux {i}"

        if name in hidden:
            continue

        aux_list.append((i, name))

    return aux_list


def panel_bg(widget):
    # sv_ttk themes ttk widgets automatically, but plain tk widgets
    # (Canvas, Scale) need their background matched by hand so they
    # don't show up as a mismatched gray/white square against the theme.
    # sv_ttk exposes its palette only as Tcl array variables, not
    # through the standard ttk::style lookup mechanism.
    #
    # sv_ttk.get_theme() is called with no argument (relying on
    # Tkinter's implicit default root) rather than passing this widget's
    # toplevel - a Toplevel window is its own toplevel ancestor, so
    # widget.winfo_toplevel() would return the Toplevel itself instead
    # of the actual Tk root that sv_ttk requires.
    name = "sv_dark" if sv_ttk.get_theme() == "dark" else "sv_light"
    return widget.tk.eval(f"set ttk::theme::{name}::colors(-bg)")


def panel_fg(widget):
    name = "sv_dark" if sv_ttk.get_theme() == "dark" else "sv_light"
    return widget.tk.eval(f"set ttk::theme::{name}::colors(-fg)")


def stripe_bg(widget):
    # A subtle alternate background for banding groups of rows apart -
    # reuses the exact shades sv_ttk itself uses for its own popup menus
    # (one perceptual step off the base background), so it stays
    # visually consistent across both themes instead of a single
    # hardcoded gray that would vanish in dark mode or look muddy in light.
    return "#292929" if sv_ttk.get_theme() == "dark" else "#e7e7e7"


def section_bg(widget):
    # The ground the channel strips sit on - a step darker than the
    # window's own background in both themes, so every strip (including
    # the un-striped ones, which used to be exactly panel_bg and so
    # melted into the page) reads as its own rounded panel raised off
    # the section rather than as part of it.
    return "#0d0d0d" if sv_ttk.get_theme() == "dark" else "#dedede"


def channel_bg(widget):
    # The un-striped strip's own tone - also a step darker than the
    # window background, sitting between section_bg below it and
    # stripe_bg's alternate above it, so the two parities still band
    # apart while both stay visible against the darker section.
    return "#1a1a1a" if sv_ttk.get_theme() == "dark" else "#f0f0f0"


def configure_section_styles(widget):
    """Point the "Section.*" ttk styles at the current section color.

    ttk widgets take their background from the style, not from a
    per-instance option, so the frames that make up the mixer section
    need a style of their own to sit on section_bg instead of the theme's
    lighter window background. Called on build and again on every theme
    change, since sv_ttk re-sources its theme underneath us.

    Labels are the exception and stay plain tk.Label: sv_ttk's themes are
    clam-parented, and a clam ttk.Label paints the *root* style's
    background behind its text no matter what a derived style sets, so a
    ttk.Label here would sit in a visible lighter box.
    """
    style = ttk.Style()
    section = section_bg(widget)

    style.configure("Section.TFrame", background=section)
    style.configure("Section.TSeparator", background=section)


def accent_color(widget):
    # sv_ttk's own selection/accent blue, reused so the plain tk widgets
    # below (Scale, Button - ttk versions of these render their trough/
    # fill via fixed PNG image assets sv_ttk ships, which a ttk style's
    # "background" option cannot recolor per-instance at all) still look
    # like they belong to the theme instead of a guessed hardcoded blue.
    name = "sv_dark" if sv_ttk.get_theme() == "dark" else "sv_light"
    return widget.tk.eval(f"set ttk::theme::{name}::colors(-selbg)")


def _cumulative_weighted_fractions(gap_count):
    # Fraction 0 (bottom) .. 1 (top) at each of gap_count+1 points, with
    # gap widths growing 1, 2, 3, ... toward the top - the same "packed
    # tight near -infinity, spread out near unity" shape as a printed
    # fader scale, generalized to a continuous 0..1 range.
    total_weight = gap_count * (gap_count + 1) / 2
    fractions = [0.0]

    for weight in range(1, gap_count + 1):
        fractions.append(fractions[-1] + weight / total_weight)

    return fractions


class RoundSlider:
    """Canvas-drawn slider - a rounded pill track plus a round thumb,
    matching sv_ttk's ttk.Scale look. ttk.Scale itself can't be used for
    the per-channel colors AuxLevelsPanel needs: sv_ttk renders both the
    trough and the slider as fixed PNG image assets (see
    sv_ttk/theme/*.tcl), so a ttk style's "background" option has no
    visual effect on them at all - and plain tk.Scale, while colorable,
    defaults to whole-number resolution (breaking a 0..1-range fader)
    and pages toward a trough click instead of jumping straight to it,
    both requiring workarounds of their own.

    Exposes the same get/set/configure/bind/pack surface AuxLevelsPanel
    already used for tk.Scale - including pack_forget/winfo_manager, so a
    slider can be taken off a strip and put back - so call sites only
    needed the constructor swapped, plus value_at() for the press/drag
    handlers to map a click coordinate to a value directly (replacing the
    old cget-based _scale_value_at, which assumed tk.Scale's own trough
    geometry).

    Note it is a plain wrapper, not a Widget subclass: it forwards to the
    canvas it owns rather than inheriting Tk's API, so anything a call
    site needs has to be forwarded explicitly. Passing one where Tk
    itself expects a widget (pack's own before=/after=, for instance)
    needs .canvas.
    """

    THUMB_RADIUS = 9
    TRACK_THICKNESS = 6

    def __init__(self, parent, orient, from_, to, length,
                 bg, troughcolor, thumb_color, command=None):
        self.orient = orient
        self.from_value = from_
        self.to_value = to
        self.length = length
        self.command = command
        self._value = from_
        self._track_color = troughcolor
        self._thumb_color = thumb_color

        pad = self.THUMB_RADIUS + 2
        width, height = (pad * 2, length) if orient == "vertical" else (length, pad * 2)

        self.canvas = tk.Canvas(
            parent, width=width, height=height, bg=bg, highlightthickness=0
        )
        self.canvas.bind("<Configure>", lambda event: self._redraw())
        self._redraw()

    def pack(self, **kwargs):
        self.canvas.pack(**kwargs)

    def pack_forget(self):
        self.canvas.pack_forget()

    def winfo_manager(self):
        # "" while unpacked, "pack" once packed - the standard Tk way to
        # ask whether a widget is currently on screen.
        return self.canvas.winfo_manager()

    def bind(self, sequence, func, add=None):
        return self.canvas.bind(sequence, func, add=add)

    def configure(self, bg=None, troughcolor=None, activebackground=None, **_ignored):
        if bg is not None:
            self.canvas.configure(bg=bg)
        if troughcolor is not None:
            self._track_color = troughcolor
        if activebackground is not None:
            self._thumb_color = activebackground
        self._redraw()

    def resize(self, length):
        self.length = length

        if self.orient == "vertical":
            self.canvas.configure(height=length)
        else:
            self.canvas.configure(width=length)

        self._redraw()

    def get(self):
        return self._value

    def set(self, value):
        low, high = sorted((self.from_value, self.to_value))
        self._value = min(high, max(low, value))
        self._redraw()

        # Matches tk.Scale's own behavior: .set() fires the command
        # regardless of whether the caller is the user dragging or code
        # updating the value programmatically - AuxLevelsPanel relies on
        # this (its suppress_send flag exists specifically to no-op the
        # command during a programmatic set rather than needing a
        # separate "set without firing" method).
        if self.command is not None:
            self.command(self._value)

    def value_at(self, coordinate):
        pad = self.THUMB_RADIUS
        span = self.length - 2 * pad
        fraction = 0.0 if span <= 0 else (coordinate - pad) / span
        fraction = min(1.0, max(0.0, fraction))
        return self.from_value + fraction * (self.to_value - self.from_value)

    def _fraction(self):
        span = self.to_value - self.from_value
        return 0.0 if span == 0 else (self._value - self.from_value) / span

    def _redraw(self):
        self.canvas.delete("all")
        r = self.THUMB_RADIUS
        fraction = self._fraction()

        if self.orient == "vertical":
            cx = int(self.canvas["width"]) / 2
            y0, y1 = r, self.length - r
            self.canvas.create_line(
                cx, y0, cx, y1, width=self.TRACK_THICKNESS,
                capstyle=tk.ROUND, fill=self._track_color
            )
            cy = y0 + fraction * (y1 - y0)
            self.canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r, fill=self._thumb_color, outline=""
            )
        else:
            cy = int(self.canvas["height"]) / 2
            x0, x1 = r, self.length - r
            self.canvas.create_line(
                x0, cy, x1, cy, width=self.TRACK_THICKNESS,
                capstyle=tk.ROUND, fill=self._track_color
            )
            cx = x0 + fraction * (x1 - x0)
            self.canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r, fill=self._thumb_color, outline=""
            )



def _rounded_rect(canvas, x0, y0, x1, y1, radius, **kwargs):
    """Draw a rounded rectangle - Tk's canvas has no such primitive, so
    it's a polygon whose corner points are doubled up and then smoothed:
    the duplicate point pins the curve to the corner while the two
    inset points either side of it become the arc's endpoints.
    """
    radius = max(0, min(radius, (x1 - x0) / 2, (y1 - y0) / 2))
    points = [
        x0 + radius, y0,
        x1 - radius, y0,
        x1, y0,
        x1, y0 + radius,
        x1, y1 - radius,
        x1, y1,
        x1 - radius, y1,
        x0 + radius, y1,
        x0, y1,
        x0, y1 - radius,
        x0, y0 + radius,
        x0, y0,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


class RoundedPanel:
    """A rounded-corner container: a Canvas that paints the panel shape,
    with a plain Frame floating on top of it holding the real content.
    tk.Frame has no corner-radius option of any kind, and canvas window
    items always stack above drawn items, so the shape underneath shows
    through only at the corners the frame's own rectangle leaves free.

    Content goes into .inner (the Frame), not the panel itself - it is a
    plain wrapper rather than a Widget subclass, so anything a call site
    needs from the canvas has to be forwarded explicitly (.canvas for
    places Tk itself wants a widget, such as pack's before=/after=).
    """

    RADIUS = 12

    # How far the painted panel extends past its content on every side -
    # what gives the strip a margin of its own color below the Mute
    # button instead of the color stopping at the button's edge.
    PADDING = 7

    def __init__(self, parent, bg, outer_bg):
        self._bg = bg
        self._shape = None

        self.canvas = tk.Canvas(parent, highlightthickness=0, bg=outer_bg)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.canvas.create_window(
            self.PADDING, self.PADDING, window=self.inner, anchor="nw"
        )

        # The frame's requested size drives the canvas size, and the
        # canvas's actual size (which pack's fill= can stretch past that)
        # drives the painted shape - so a strip packed fill="y" keeps its
        # color all the way down instead of ending where its content does.
        self.inner.bind("<Configure>", lambda event: self._resize())
        self.canvas.bind("<Configure>", lambda event: self._redraw())
        self._resize()

    def pack(self, **kwargs):
        self.canvas.pack(**kwargs)

    def pack_forget(self):
        self.canvas.pack_forget()

    def configure(self, bg=None, outer_bg=None):
        if bg is not None:
            self._bg = bg
            self.inner.configure(bg=bg)
        if outer_bg is not None:
            self.canvas.configure(bg=outer_bg)
        self._redraw()

    config = configure

    def _resize(self):
        self.canvas.configure(
            width=self.inner.winfo_reqwidth() + self.PADDING * 2,
            height=self.inner.winfo_reqheight() + self.PADDING * 2,
        )
        self._redraw()

    def _redraw(self):
        if self._shape is not None:
            self.canvas.delete(self._shape)

        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()

        # Before the first map winfo_width() reports 1; the <Configure>
        # that follows redraws at the real size.
        if width <= 1 or height <= 1:
            width = int(self.canvas["width"])
            height = int(self.canvas["height"])

        self._shape = _rounded_rect(
            self.canvas, 0, 0, width - 1, height - 1, self.RADIUS,
            fill=self._bg, outline=""
        )


class RoundButton:
    """Canvas-drawn button with rounded corners. tk.Button has no
    corner-radius option, and sv_ttk's ttk.Button paints its fill from
    fixed PNG assets a style's "background" cannot recolor - so neither
    can be both rounded and per-channel colored the way the Mute buttons
    need to be.

    Mirrors the slice of tk.Button's surface the call sites already use
    (config/configure/cget for text and colors, pack), plus .canvas for
    places Tk itself wants a widget.
    """

    RADIUS = 8

    def __init__(self, parent, text, width, height,
                 bg, fg, outer_bg, command=None):
        self._text = text
        self._bg = bg
        self._fg = fg
        self.command = command

        self.canvas = tk.Canvas(
            parent, width=width, height=height,
            highlightthickness=0, bg=outer_bg
        )
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Configure>", lambda event: self._redraw())
        self._redraw()

    def pack(self, **kwargs):
        self.canvas.pack(**kwargs)

    def pack_forget(self):
        self.canvas.pack_forget()

    def configure(self, text=None, bg=None, fg=None, outer_bg=None,
                  activebackground=None, activeforeground=None, **_ignored):
        if text is not None:
            self._text = text
        if bg is not None:
            self._bg = bg
        if fg is not None:
            self._fg = fg
        if outer_bg is not None:
            # What shows through outside the rounded shape - the strip's
            # own background, which changes with the theme.
            self.canvas.configure(bg=outer_bg)
        self._redraw()

    config = configure

    def cget(self, option):
        if option == "text":
            return self._text
        if option == "bg" or option == "background":
            return self._bg
        if option == "fg" or option == "foreground":
            return self._fg
        return self.canvas.cget(option)

    def _on_release(self, event):
        # Only a release still over the button counts, matching how a
        # real button lets you slide off it to cancel the press.
        inside = 0 <= event.x < self.canvas.winfo_width() \
            and 0 <= event.y < self.canvas.winfo_height()

        if inside and self.command is not None:
            self.command()

    def _redraw(self):
        self.canvas.delete("all")

        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()

        if width <= 1 or height <= 1:
            width = int(self.canvas["width"])
            height = int(self.canvas["height"])

        _rounded_rect(
            self.canvas, 0, 0, width - 1, height - 1, self.RADIUS,
            fill=self._bg, outline=""
        )
        self.canvas.create_text(
            width / 2, height / 2, text=self._text, fill=self._fg
        )


class AuxLevelsPanel:
    REFRESH_MS = 150

    # How long to wait for /Layout/Layout/Banks replies before giving up
    # and showing every channel instead. The opening bank comes from the
    # console's own layout, which is only asked for once loading
    # finishes, so it is never known at load time - but a console that
    # reports no banks at all must not leave the operator staring at an
    # empty window.
    BANK_WAIT_SECONDS = 2.0
    LEVEL_EPSILON = 0.005

    # How long after the user releases a slider we keep ignoring
    # mixer-reported levels for it, so a stale/in-flight echo of an
    # earlier drag position can't snap it back and make it feel jumpy.
    DRAG_GRACE_SECONDS = 0.3

    TOP_DB = 10.0
    BOTTOM_DB = -150.0

    # Pixel length of the level slider's trough - shared with the ruler
    # and the meter drawn beside it. This is the *minimum*: the strips
    # stretch to whatever height the window gives them (self.level_length
    # holds the live value), and MainWindow's minsize keeps the window
    # from ever shrinking below the height that yields this.
    LEVEL_LENGTH = 220

    # Padding inside channels_frame, subtracted when working out how much
    # height is actually left for a fader.
    CHANNELS_FRAME_PAD = 10

    # The fader's dB<->position curve (_fraction_to_db / _db_to_fraction
    # below) is a piecewise-linear interpolation through these points,
    # using LEVEL_TICK_FRACTIONS for each one's position - so dragging
    # the fader to where a ruler number sits reports that exact dB value,
    # matching a printed fader scale: packed tight near -infinity, spread
    # out near unity.
    LEVEL_TICKS = [
        (BOTTOM_DB, "∞"),
        (-60.0, "60"),
        (-50.0, "50"),
        (-40.0, "40"),
        (-30.0, "30"),
        (-20.0, "20"),
        (-10.0, "10"),
        (-5.0, "5"),
        (0.0, "0"),
        (5.0, "5"),
        (TOP_DB, "10"),
    ]
    LEVEL_TICK_FRACTIONS = _cumulative_weighted_fractions(len(LEVEL_TICKS) - 1)

    # Keeps the top/bottom tick labels (+10, ∞) from being clipped by the
    # canvas edge, since their text is vertically centered on the exact
    # top/bottom endpoints otherwise.
    LEVEL_TICK_INSET = 4

    # Total ruler width, and where each tick's connector line (pointing
    # from the number toward the fader track) starts - matching the
    # reference photo's numbers-then-line-to-the-fader look.
    LEVEL_RULER_WIDTH = 26
    LEVEL_TICK_LINE_START = 16

    # Meter bar drawn beside each fader. The console's meter feed spans
    # 0..-60 dB (see MixerWorker.decode_meter), matching the scale printed
    # on the console's own meters - deliberately NOT the fader's own
    # -150..+10 dB scale, so the two are not interchangeable and the meter
    # is not drawn against the fader's ruler.
    METER_WIDTH = 10
    METER_FLOOR_DB = -60.0

    # Mute button size in pixels (RoundButton is canvas-drawn, so it has
    # no character-width option like tk.Button did) - close to the
    # fader/ruler/meter row's own width, so hiding the wider pan slider
    # on a mono aux doesn't leave the button sticking out past the row.
    MUTE_WIDTH = 62
    MUTE_HEIGHT = 24

    # pady the pan slider and the Mute button are packed with. Counted
    # again in _strip_chrome_height, so the two have to agree.
    STRIP_PAN_PAD = 6
    STRIP_MUTE_PAD = 4

    # A stereo channel draws two bars inside that same total width rather
    # than widening its canvas, so a bank of mixed mono and stereo strips
    # still lines up as even columns - fader, ruler and meter keep
    # identical geometry either way, and the split reads as "this channel
    # is stereo" without the strip jumping width.
    #
    # Consequently every per-meter dict below (slices, peak items and all
    # the ballistics state) is keyed by (channel, leg) rather than by
    # channel: a stereo strip's two bars animate independently, exactly
    # as two mono strips would. self.channel_legs holds each channel's
    # legs, mirroring what MixerWorker actually subscribed.
    METER_STEREO_GAP = 2

    # Colour ramp sampled from the Q225's own meters: deep blue at the
    # bottom, through cyan and green, yellow-green near -14, into red for
    # the last few dB. Stops are in dB (not fractions) so the colours stay
    # tied to real levels if METER_FLOOR_DB ever changes.
    METER_GRADIENT = (
        (-60.0, (0x0A, 0x30, 0xC8)),
        (-46.0, (0x00, 0xA0, 0xE8)),
        (-34.0, (0x00, 0xD2, 0x4A)),
        (-20.0, (0x7A, 0xDA, 0x00)),
        (-14.0, (0xD8, 0xD0, 0x00)),
        (-10.0, (0xE8, 0x40, 0x1C)),
        (0.0,   (0xFF, 0x1A, 0x10)),
    )

    # Unlit slices keep their gradient hue at low brightness, which is
    # what gives the console's meters their dark-red-over-dark-blue look
    # when idle rather than a flat grey trough.
    METER_DIM_FACTOR = 0.22

    # Peak-hold marker, kept near-white so it reads against every part
    # of the gradient including the red top.
    METER_PEAK_COLOR = "#e6edf3"

    # The bar is built from fixed slices whose fill is toggled lit/unlit,
    # rather than redrawn each frame: only the few slices the level
    # actually crossed get touched, which is what makes a 30fps refresh
    # across a full bank affordable in Tk.
    METER_SLICE_H = 2

    # Ballistics, in the classic attack/release shape of a hardware
    # programme meter: the bar is ALWAYS falling, and each arriving
    # sample pushes it back up. It never parks on a level and waits.
    #
    # This is why MixerWorker keeps meter_seq. A sample is a one-shot
    # push, not a level to settle on: once the bar has risen to it the
    # sample is spent, and only the next packet lifts the bar again.
    # Re-reading the same latched value every frame would hold the bar
    # up forever, which is exactly the parking this replaces.
    #
    # Rise is fast but not instantaneous: snapping straight to the new
    # value was the one movement in the bar that wasn't animated, which
    # read as a glitch next to the smooth fall. ~0.045s settles inside
    # about five frames - still immediate to the eye, but a slide.
    #
    # METER_RISE_MIN_DB_PER_SEC has to stay small. The wire quantises to
    # 3 dB, so most real movement is a single 3 dB step; a floor big
    # enough to cross that in one frame would snap exactly the case that
    # happens most often, leaving only rare large jumps looking smooth.
    #
    # Release is a flat dB/sec, not a time constant: it decays towards
    # the floor rather than towards a target, so there is no gap for a
    # tau to be proportional to - an exponential would sag hardest at
    # exactly the loud levels where the sag is most visible. The rate is
    # the one number to turn if the meter feels too twitchy or too slow,
    # and it trades off in two directions:
    #   - too fast, and the bar visibly sags between packets (with music
    #     the RMS field changes on roughly every other frame, so the bar
    #     free-falls ~70ms at a time - 40 dB/sec is ~2.8 dB of dip, about
    #     one wire step, which reads as the meter breathing rather than
    #     as flicker);
    #   - too slow, and silence takes too long to clear. 40 dB/sec walks
    #     the full 60 dB scale in 1.5s; an earlier 32 dB/sec fall was
    #     already judged laggy against the console's own meters.
    METER_REFRESH_MS = 33
    METER_RISE_TAU_SECONDS = 0.045
    METER_RISE_MIN_DB_PER_SEC = 30.0
    METER_RELEASE_DB_PER_SEC = 40.0
    METER_PEAK_HOLD_SECONDS = 0.9
    METER_PEAK_FALL_DB_PER_SEC = 40.0

    @classmethod
    def _fraction_to_db(cls, fraction):
        fraction = min(1.0, max(0.0, fraction))
        fractions = cls.LEVEL_TICK_FRACTIONS

        for i in range(len(fractions) - 1):
            f0, f1 = fractions[i], fractions[i + 1]

            if fraction <= f1:
                db0, db1 = cls.LEVEL_TICKS[i][0], cls.LEVEL_TICKS[i + 1][0]
                t = (fraction - f0) / (f1 - f0)
                return db0 + t * (db1 - db0)

        return cls.TOP_DB

    @classmethod
    def _db_to_fraction(cls, db):
        ticks = cls.LEVEL_TICKS
        fractions = cls.LEVEL_TICK_FRACTIONS

        if db <= ticks[0][0]:
            return 0.0

        if db >= ticks[-1][0]:
            return 1.0

        for i in range(len(ticks) - 1):
            db0, db1 = ticks[i][0], ticks[i + 1][0]

            if db <= db1:
                t = (db - db0) / (db1 - db0)
                return fractions[i] + t * (fractions[i + 1] - fractions[i])

        return 1.0

    # The mixer's own send_pan values run 0.0 (hard left) to 1.0 (hard
    # right) with 0.5 as center. The UI (and double-click reset) use the
    # more conventional -1.0..1.0 with 0.0 as center, so every value
    # crossing the OSC boundary needs converting.
    @staticmethod
    def _wire_pan_to_ui(value):
        return round((value - 0.5) * 2, 2)

    @staticmethod
    def _ui_pan_to_wire(value):
        return round((value / 2) + 0.5, 2)

    def __init__(self, master, command_queue, get_hidden_auxes=None):
        self.master = master
        self.command_queue = command_queue
        self.get_hidden_auxes = get_hidden_auxes or (lambda: set())

        self.worker = None
        self.all_channels = []
        self.channels = []
        self.aux_list = []

        self.sliders = {}
        self.level_rulers = {}
        self.channel_meters = {}
        self.channel_legs = {}
        self.meter_slices = {}
        self.meter_leg_spans = {}
        self.meter_peak_items = {}
        self.meter_lit = {}
        self.meter_shown = {}
        self.meter_target = {}
        self.meter_rising = {}
        self.meter_seen_seq = {}
        self.meter_peak = {}
        self.meter_peak_at = {}
        self.pan_sliders = {}
        self.mute_buttons = {}
        self.channel_columns = {}
        self.channel_fader_rows = {}
        self.channel_name_labels = {}
        self.channel_parity = {}
        self.level_length = self.LEVEL_LENGTH
        self.suppress_send = False
        self.dragging = set()
        self.drag_released_at = {}
        self.pan_dragging = set()
        self.pan_drag_released_at = {}
        self.bank_names_shown = None
        # Which bank is on screen, and when we started waiting for the
        # console to tell us what the banks are. See _open_default_bank().
        self.current_bank = None
        self._bank_wait_started_at = None
        # Last worker.snapshot_epoch this panel has re-queried for.
        self._snapshot_epoch_seen = 0
        self.meter_ticked_at = time.monotonic()

        self.build_ui()

        self.master.after(self.REFRESH_MS, self.refresh_levels)
        self.master.after(self.METER_REFRESH_MS, self.refresh_meters)

    def build_ui(self):
        configure_section_styles(self.master)

        self.top_bar = ttk.Frame(
            self.master, padding=10, style="Section.TFrame"
        )
        self.top_bar.pack(fill="x")

        section = section_bg(self.master)
        fg = panel_fg(self.master)

        self.aux_bus_label = tk.Label(
            self.top_bar, text="Aux Bus", bg=section, fg=fg
        )
        self.aux_bus_label.pack(side="left")

        self.aux_combo = ttk.Combobox(self.top_bar, values=[], state="readonly")
        self.aux_combo.pack(side="left", padx=10)
        self.aux_combo.bind("<<ComboboxSelected>>", self.on_aux_selected)

        ttk.Separator(
            self.top_bar, orient="vertical", style="Section.TSeparator"
        ).pack(side="left", fill="y", padx=10)

        self.bank_label = tk.Label(
            self.top_bar, text="Bank", bg=section, fg=fg
        )
        self.bank_label.pack(side="left")

        self.banks_frame = ttk.Frame(self.top_bar, style="Section.TFrame")
        self.banks_frame.pack(side="left", padx=10)

        canvas_container = ttk.Frame(self.master, style="Section.TFrame")
        canvas_container.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(
            canvas_container, highlightthickness=0, bg=section_bg(self.master)
        )
        h_scroll = ttk.Scrollbar(
            canvas_container, orient="horizontal", command=self.canvas.xview
        )
        self.canvas.configure(xscrollcommand=h_scroll.set)

        self.canvas.pack(side="top", fill="both", expand=True)
        h_scroll.pack(side="bottom", fill="x")

        self.channels_frame = ttk.Frame(
            self.canvas, padding=self.CHANNELS_FRAME_PAD,
            style="Section.TFrame"
        )
        self.channels_window = self.canvas.create_window(
            (0, 0), window=self.channels_frame, anchor="nw"
        )
        self.channels_frame.bind(
            "<Configure>",
            lambda event: self.canvas.configure(
                scrollregion=self.canvas.bbox("all")
            )
        )

        # A canvas window item is only as tall as the widget inside it
        # asks to be, so without this the strip row would keep its own
        # height and leave dead section below it. Forcing the item to the
        # canvas's height instead is what gives the columns' fill="y"
        # something to fill, and so lands them on the window's bottom
        # edge; _fit_level_length then spends the new height on the
        # faders rather than on padding.
        self.canvas.bind("<Configure>", self._on_canvas_resize)

    def _on_canvas_resize(self, event):
        self.canvas.itemconfigure(self.channels_window, height=event.height)
        self._fit_level_length(event.height)

    def _strip_chrome_height(self):
        """Height a strip spends on everything that is not the fader.

        Summed from the individual widgets rather than taken as (the
        strip's requested height - the fader's): Tk recomputes a parent's
        requested height at idle, so straight after the pan slider is
        packed or unpacked the strip still reports its previous layout.
        Sizing the fader off that number overruns the strip by exactly the
        pan slider's height, which pushes the Mute button off the bottom
        of the window - the widgets summed here each keep a requested
        height of their own that is correct the moment it changes.
        """
        channel = next(iter(self.channel_columns), None)

        if channel is None:
            return None

        chrome = self.channel_name_labels[channel].winfo_reqheight()
        chrome += self.STRIP_MUTE_PAD + self.MUTE_HEIGHT

        pan_slider = self.pan_sliders.get(channel)

        # winfo_manager() is "" while unpacked - a hidden pan slider costs
        # the strip nothing, and the fader gets that height instead.
        if pan_slider is not None and pan_slider.winfo_manager():
            chrome += self.STRIP_PAN_PAD + pan_slider.canvas.winfo_reqheight()

        return chrome

    def _fit_level_length(self, canvas_height):
        chrome = self._strip_chrome_height()

        if chrome is None:
            return

        length = (
            canvas_height
            - 2 * self.CHANNELS_FRAME_PAD
            - 2 * RoundedPanel.PADDING
            - chrome
        )
        length = max(self.LEVEL_LENGTH, length)

        if length == self.level_length:
            return

        self.level_length = length

        for channel in self.channel_columns:
            ruler = self.level_rulers.get(channel)
            if ruler is not None:
                ruler.configure(height=length)
                self._draw_level_ruler(ruler)

            slider = self.sliders.get(channel)
            if slider is not None:
                slider.resize(length)

            meter = self.channel_meters.get(channel)
            if meter is not None:
                # The slice count is a function of the length, so the bar
                # is rebuilt rather than stretched; refresh_meters relights
                # it on its next tick from the ballistics state, which is
                # keyed by (channel, leg) and survives untouched.
                meter.configure(height=length)
                meter.delete("all")
                self._build_meter_slices(
                    channel, meter, self.channel_legs[channel]
                )

    def apply_theme(self):
        section = section_bg(self.master)
        self.canvas.configure(bg=section)
        configure_section_styles(self.master)

        fg = panel_fg(self.master)
        accent = accent_color(self.master)

        for label in (self.aux_bus_label, self.bank_label):
            label.configure(bg=section, fg=fg)

        for channel, column in self.channel_columns.items():
            column_bg = self._channel_bg(self.channel_parity.get(channel, False))
            column.configure(bg=column_bg, outer_bg=section)

            label = self.channel_name_labels.get(channel)
            if label is not None:
                label.configure(bg=column_bg, fg=fg)

            fader_row = self.channel_fader_rows.get(channel)
            if fader_row is not None:
                fader_row.configure(bg=column_bg)

            ruler = self.level_rulers.get(channel)
            if ruler is not None:
                ruler.configure(bg=column_bg)
                self._draw_level_ruler(ruler)

            meter = self.channel_meters.get(channel)
            if meter is not None:
                # Only the canvas backing needs recolouring - the slices
                # carry their own gradient, which is theme-independent.
                meter.configure(bg=column_bg)

            track_color = self._track_color(column_bg)

            slider = self.sliders.get(channel)
            if slider is not None:
                slider.configure(bg=column_bg, activebackground=accent, troughcolor=track_color)

            pan_slider = self.pan_sliders.get(channel)
            if pan_slider is not None:
                pan_slider.configure(
                    bg=column_bg, activebackground=accent, troughcolor=track_color
                )

            button = self.mute_buttons.get(channel)
            if button is not None:
                bg, btn_fg = self._mute_button_colors(
                    channel, self._is_muted(channel)
                )
                button.configure(bg=bg, fg=btn_fg, outer_bg=column_bg)

    def on_mixer_loaded(self, worker):
        self.worker = worker

        channel_count = int(worker.cache["/Console/Input_Channels"][0])
        self.all_channels = list(range(1, channel_count + 1))

        # Deliberately empty rather than every channel. The console's
        # banks are not known yet - /Layout/Layout/Banks/? is only sent
        # as loading completes, and its replies land over the following
        # frames - so _open_default_bank() fills this in as soon as the
        # layout arrives. Building all 72 strips here just to replace
        # them a moment later is what made connecting expensive: it cost
        # a full set of widgets, a per-channel level/pan query for the
        # selected aux, and a meter subscription covering the whole
        # console, none of which survived the first bank selection.
        self.channels = []
        self.current_bank = None
        self._bank_wait_started_at = time.monotonic()
        # Adopt the console's current epoch rather than 0: loading has
        # just fetched everything, so there is nothing stale to correct.
        self._snapshot_epoch_seen = worker.snapshot_epoch

        self.aux_list = build_aux_list(worker, hidden=self.get_hidden_auxes())
        self.bank_names_shown = None

        self.aux_combo.configure(values=[name for _, name in self.aux_list])

        self.build_bank_buttons()
        self.build_channel_widgets()
        self.subscribe_meters()

        if self.aux_list:
            self.aux_combo.current(0)
            self.on_aux_selected()

    def subscribe_meters(self):
        # Only ever the channels actually on screen: this is a continuous
        # ~30Hz stream from the console, so subscribing all 72 would cost
        # bandwidth and redraws for strips nobody is looking at.
        if self.worker is not None:
            self.worker.subscribe_meters(self.channels)

    def refresh_aux_list(self):
        # Called when the operator hides/shows an aux via the Aux window,
        # so the dropdown reflects it immediately rather than only after
        # the next reconnect.
        if self.worker is None:
            return

        previous_aux = self.current_aux()

        self.aux_list = build_aux_list(self.worker, hidden=self.get_hidden_auxes())
        self.aux_combo.configure(values=[name for _, name in self.aux_list])

        aux_indices = [index for index, _ in self.aux_list]

        if previous_aux in aux_indices:
            self.aux_combo.current(aux_indices.index(previous_aux))
        elif self.aux_list:
            self.aux_combo.current(0)
        else:
            self.aux_combo.set("")

        self.on_aux_selected()

    def on_mixer_disconnected(self):
        self.worker = None
        self.all_channels = []
        self.channels = []
        self.aux_list = []
        self.bank_names_shown = None
        # Which bank is on screen, and when we started waiting for the
        # console to tell us what the banks are. See _open_default_bank().
        self.current_bank = None
        self._bank_wait_started_at = None
        # Last worker.snapshot_epoch this panel has re-queried for.
        self._snapshot_epoch_seen = 0

        self.aux_combo.configure(values=[])
        self.aux_combo.set("")

        for child in self.channels_frame.winfo_children():
            child.destroy()

        for child in self.banks_frame.winfo_children():
            child.destroy()

        self.sliders = {}
        self.level_rulers = {}
        self.channel_meters = {}
        self.channel_legs = {}
        self.meter_slices = {}
        self.meter_leg_spans = {}
        self.meter_peak_items = {}
        self.meter_lit = {}
        self.meter_shown = {}
        self.meter_target = {}
        self.meter_rising = {}
        self.meter_seen_seq = {}
        self.meter_peak = {}
        self.meter_peak_at = {}
        self.pan_sliders = {}
        self.mute_buttons = {}
        self.channel_columns = {}
        self.channel_fader_rows = {}
        self.channel_name_labels = {}
        self.channel_parity = {}
        self.dragging = set()
        self.drag_released_at = {}
        self.pan_dragging = set()
        self.pan_drag_released_at = {}

    def build_bank_buttons(self):
        bank_names = tuple(self.worker.banks.keys())

        if bank_names == self.bank_names_shown:
            return

        self.bank_names_shown = bank_names

        for child in self.banks_frame.winfo_children():
            child.destroy()

        for name in bank_names:
            ttk.Button(
                self.banks_frame,
                text=name,
                command=lambda n=name: self.select_bank(n)
            ).pack(side="left", padx=2)

    def _resync_after_snapshot(self):
        """Re-read what a snapshot recall just changed underneath us.

        Only the values a recall rewrites - levels, pans and mutes -
        and only for the strips actually on screen. Names, modes and
        the bank layout survive a recall, so re-reading those would be
        the expensive half of a reload for nothing.
        """
        if self.worker is None or not self.worker.is_alive():
            return

        if self.worker.snapshot_epoch == self._snapshot_epoch_seen:
            return

        self._snapshot_epoch_seen = self.worker.snapshot_epoch

        if not self.channels:
            return

        log("info", "Snapshot changed - refreshing levels, pans and mutes")

        # Queries send_level and send_on, plus send_pan when the aux
        # is stereo.
        self.on_aux_selected()

    def _open_default_bank(self):
        """Open the console's first bank once its layout has arrived.

        Polled from refresh_levels because the layout is pushed, not
        awaited: one /Layout/Layout/Banks message per bank, arriving
        after loading has already finished. The first one to land is the
        console's own first bank, which is what opens.
        """
        if self.current_bank is not None or self.channels:
            return

        bank_names = tuple(self.worker.banks.keys())

        if bank_names:
            self.select_bank(bank_names[0])
        elif self._bank_wait_started_at is not None and \
                time.monotonic() - self._bank_wait_started_at > \
                self.BANK_WAIT_SECONDS:
            # No banks at all: better every channel than a blank window.
            self._bank_wait_started_at = None
            self.set_channels(self.all_channels)

    def select_bank(self, bank_name):
        channels = self.worker.banks.get(bank_name)

        if not channels:
            return

        self.current_bank = bank_name
        self.set_channels(channels)

    def set_channels(self, channels):
        if channels == self.channels:
            return

        self.channels = channels

        for child in self.channels_frame.winfo_children():
            child.destroy()

        self.sliders = {}
        self.level_rulers = {}
        self.channel_meters = {}
        self.channel_legs = {}
        self.meter_slices = {}
        self.meter_leg_spans = {}
        self.meter_peak_items = {}
        self.meter_lit = {}
        self.meter_shown = {}
        self.meter_target = {}
        self.meter_rising = {}
        self.meter_seen_seq = {}
        self.meter_peak = {}
        self.meter_peak_at = {}
        self.pan_sliders = {}
        self.mute_buttons = {}
        self.channel_columns = {}
        self.channel_fader_rows = {}
        self.channel_name_labels = {}
        self.channel_parity = {}
        self.dragging = set()
        self.drag_released_at = {}
        self.pan_dragging = set()
        self.pan_drag_released_at = {}

        self.build_channel_widgets()
        self.on_aux_selected()
        self.subscribe_meters()

    def build_channel_widgets(self):
        for index, i in enumerate(self.channels):
            name_key = f"/Input_Channels/{i}/Channel_Input/name"
            name = self.worker.cache[name_key][0] \
                if name_key in self.worker.cache else f"Ch {i}"

            # Alternating tone so adjacent channel strips read as visually
            # separate columns instead of blurring together - same idea
            # as AuxPanel's row striping and the Android app's per-item
            # background (ChannelAdapter.bindChannelState).
            is_alt = index % 2 == 1
            self.channel_parity[i] = is_alt
            column_bg = self._channel_bg(is_alt)
            track_color = self._track_color(column_bg)
            fg = panel_fg(self.master)
            accent = accent_color(self.master)

            # tk (not ttk) widgets for everything in this column: sv_ttk
            # renders Scale's trough and Button's fill as fixed PNG image
            # assets (see sv_ttk/theme/*.tcl), which a ttk style's
            # "background" option cannot recolor per-instance at all -
            # only plain tk widgets accept real background colors here.
            column = RoundedPanel(
                self.channels_frame, bg=column_bg,
                outer_bg=section_bg(self.master)
            )
            # No gap between strips: with the two parities banding them
            # apart and each one carrying its own PADDING of color, they
            # read as separate columns while touching, and a bank fits
            # that much more of itself on screen before scrolling.
            column.pack(side="left", fill="y")
            self.channel_columns[i] = column

            name_label = tk.Label(column.inner, text=name, bg=column_bg, fg=fg)
            name_label.pack()
            self.channel_name_labels[i] = name_label

            # A row (not the slider alone) so the ruler and the slider
            # share the same top edge - and therefore line up - regardless
            # of the name label's height above them.
            fader_row = tk.Frame(column.inner, bg=column_bg)
            fader_row.pack()
            self.channel_fader_rows[i] = fader_row

            ruler = tk.Canvas(
                fader_row, width=self.LEVEL_RULER_WIDTH,
                height=self.level_length,
                highlightthickness=0, bg=column_bg
            )
            ruler.pack(side="left", fill="y")
            self.level_rulers[i] = ruler
            self._draw_level_ruler(ruler)

            slider = RoundSlider(
                fader_row,
                orient="vertical",
                from_=1.0,
                to=0.0,
                length=self.level_length,
                bg=column_bg,
                troughcolor=track_color,
                thumb_color=accent,
                command=lambda value, channel=i:
                    self.on_slider_change(channel, value)
            )
            # Press and drag are handled entirely by hand (rather than
            # relying on a native widget's own click/drag handling) since
            # RoundSlider is Canvas-drawn, not an interactive widget on
            # its own - "break" stops the event from also reaching
            # anything else bound on the canvas.
            slider.bind(
                "<ButtonPress-1>",
                lambda event, channel=i, widget=slider:
                    self._on_scale_press(self.dragging, channel, widget, event.y)
            )
            slider.bind(
                "<B1-Motion>",
                lambda event, widget=slider: self._on_scale_motion(widget, event.y)
            )
            slider.bind(
                "<ButtonRelease-1>",
                lambda event, channel=i: self.on_slider_release(channel)
            )
            slider.pack(side="left")

            self.sliders[i] = slider

            meter = tk.Canvas(
                fader_row, width=self.METER_WIDTH, height=self.level_length,
                highlightthickness=0, bg=column_bg
            )
            meter.pack(side="left", padx=(3, 0))
            self.channel_meters[i] = meter
            # Legs come from the worker so the bars drawn here and the
            # slots it subscribes can never disagree about a channel.
            legs = self.worker.channel_legs(i)
            self.channel_legs[i] = legs
            self._build_meter_slices(i, meter, legs)

            pan_slider = RoundSlider(
                column.inner,
                orient="horizontal",
                from_=-1.0,
                to=1.0,
                length=90,
                bg=column_bg,
                troughcolor=track_color,
                thumb_color=accent,
                command=lambda value, channel=i:
                    self.on_pan_change(channel, value)
            )
            # Centred for display only, until the console's own value
            # arrives on the next refresh. Without suppress_send, set()
            # fires on_pan_change and writes centre to this send on the
            # desk - which on every bank opened overwrote the real pans of
            # the selected stereo aux.
            self.suppress_send = True
            pan_slider.set(0.0)
            self.suppress_send = False
            pan_slider.bind(
                "<ButtonPress-1>",
                lambda event, channel=i, widget=pan_slider:
                    self._on_scale_press(self.pan_dragging, channel, widget, event.x)
            )
            pan_slider.bind(
                "<B1-Motion>",
                lambda event, widget=pan_slider: self._on_scale_motion(widget, event.x)
            )
            pan_slider.bind(
                "<ButtonRelease-1>",
                lambda event, channel=i: self.on_pan_release(channel)
            )
            pan_slider.bind(
                "<Double-Button-1>",
                lambda event, channel=i: self.on_pan_double_click(channel)
            )
            pan_slider.pack(pady=(self.STRIP_PAN_PAD, 0))

            self.pan_sliders[i] = pan_slider

            # The label is fixed: fill color alone says whether the
            # channel is in the mix, matching the phone app's strip.
            mute_bg, mute_fg = self._mute_button_colors(i, muted=False)
            mute_btn = RoundButton(
                column.inner,
                text="MUTE",
                width=self.MUTE_WIDTH,
                height=self.MUTE_HEIGHT,
                bg=mute_bg,
                fg=mute_fg,
                outer_bg=column_bg,
                command=lambda channel=i: self.on_mute_toggle(channel)
            )
            mute_btn.pack(pady=(self.STRIP_MUTE_PAD, 0))

            self.mute_buttons[i] = mute_btn

        # Freshly built strips start at LEVEL_LENGTH; spend whatever the
        # window is already giving us before they are first drawn.
        self._fit_level_length(self.canvas.winfo_height())

    def _channel_bg(self, is_alt):
        return stripe_bg(self.master) if is_alt else channel_bg(self.master)

    def _track_color(self, column_bg):
        # A visible rail for the slider to ride on, without going back to
        # a flatly different-colored patch (the earlier complaint) - it's
        # blended from the column's own background toward the theme's fg,
        # so it's a subtle step off whatever tone that channel already
        # has rather than an unrelated fixed gray.
        r1, g1, b1 = self.master.winfo_rgb(column_bg)
        r2, g2, b2 = self.master.winfo_rgb(panel_fg(self.master))
        ratio = 0.35

        blended = tuple(
            round(c1 + (c2 - c1) * ratio) >> 8
            for c1, c2 in ((r1, r2), (g1, g2), (b1, b2))
        )
        return "#{:02x}{:02x}{:02x}".format(*blended)

    def _mute_button_colors(self, channel, muted):
        if muted:
            return "#c0392b", "white"

        # The opposite tone from its own column, so the button stands out
        # against the background instead of blending into it.
        is_alt = self.channel_parity.get(channel, False)
        return self._channel_bg(not is_alt), panel_fg(self.master)

    def _on_scale_press(self, dragging_set, channel, widget, coordinate):
        dragging_set.add(channel)
        widget.set(widget.value_at(coordinate))
        return "break"

    def _on_scale_motion(self, widget, coordinate):
        widget.set(widget.value_at(coordinate))
        return "break"

    def _draw_level_ruler(self, ruler):
        ruler.delete("all")
        fg = panel_fg(self.master)
        span = self.level_length - 2 * self.LEVEL_TICK_INSET

        # Same LEVEL_TICK_FRACTIONS the fader itself uses (_fraction_to_db
        # / _db_to_fraction), so a tick's printed position always matches
        # exactly where dragging the fader there reports that dB value.
        for (_db, label), fraction in zip(self.LEVEL_TICKS, self.LEVEL_TICK_FRACTIONS):
            y = self.LEVEL_TICK_INSET + (1 - fraction) * span
            ruler.create_line(
                self.LEVEL_TICK_LINE_START, y, self.LEVEL_RULER_WIDTH, y, fill=fg
            )
            ruler.create_text(
                2, y, text=label, anchor="w", fill=fg, font=("TkDefaultFont", 7)
            )

    def _meter_fraction(self, db):
        """dB -> 0..1 along the bar, clamped to the console's meter range."""
        span = -self.METER_FLOOR_DB
        return min(1.0, max(0.0, (db - self.METER_FLOOR_DB) / span))

    @classmethod
    def _meter_palette(cls, count):
        """(lit, dim) hex colours per slice, bottom-up.

        Cached per slice count rather than built once, since a strip that
        stretched with the window has more slices to colour - the ramp
        itself is defined by dB, so it re-derives to the same gradient at
        any resolution.
        """
        # Read out of cls.__dict__ rather than off cls, so a subclass
        # would build its own cache instead of filling the parent's.
        cache = cls.__dict__.get("_meter_palette_cache")

        if cache is None:
            cache = {}
            cls._meter_palette_cache = cache

        if count in cache:
            return cache[count]

        stops = cls.METER_GRADIENT
        lit = []
        dim = []

        for index in range(count):
            db = cls.METER_FLOOR_DB + (
                (index + 0.5) / count * -cls.METER_FLOOR_DB
            )

            # Linear interpolation between the two stops bracketing this
            # slice's dB - the ramp is defined by level, not by position.
            low, high = stops[0], stops[-1]
            for current, following in zip(stops, stops[1:]):
                if current[0] <= db <= following[0]:
                    low, high = current, following
                    break

            span = high[0] - low[0]
            ratio = 0.0 if span == 0 else (db - low[0]) / span
            rgb = [
                round(a + (b - a) * ratio) for a, b in zip(low[1], high[1])
            ]

            lit.append("#%02x%02x%02x" % tuple(rgb))
            dim.append("#%02x%02x%02x" % tuple(
                round(channel * cls.METER_DIM_FACTOR) for channel in rgb
            ))

        cache[count] = (lit, dim)
        return cache[count]

    @classmethod
    def _meter_leg_spans(cls, legs):
        """(x0, x1) for each leg, splitting one strip's meter width.

        A single leg gets the whole width; a stereo pair splits it with a
        gap between, keeping the strip the same width either way.
        """
        if len(legs) < 2:
            return {legs[0]: (0, cls.METER_WIDTH)}

        gaps = cls.METER_STEREO_GAP * (len(legs) - 1)
        bar = (cls.METER_WIDTH - gaps) // len(legs)

        return {
            leg: (index * (bar + cls.METER_STEREO_GAP),
                  index * (bar + cls.METER_STEREO_GAP) + bar)
            for index, leg in enumerate(legs)
        }

    def _build_meter_slices(self, channel, meter, legs):
        # Created once per strip, one bar per leg. refresh_meters() then
        # only recolours the slices the level actually crossed.
        count = self.level_length // self.METER_SLICE_H
        _lit, dim = self._meter_palette(count)
        spans = self._meter_leg_spans(legs)

        for leg in legs:
            x0, x1 = spans[leg]
            slices = []

            for index in range(count):
                bottom = self.level_length - index * self.METER_SLICE_H
                slices.append(meter.create_rectangle(
                    x0, bottom - self.METER_SLICE_H, x1, bottom,
                    fill=dim[index], outline=""
                ))

            self.meter_slices[(channel, leg)] = slices
            self.meter_leg_spans[(channel, leg)] = (x0, x1)
            self.meter_lit[(channel, leg)] = 0
            self.meter_peak_items[(channel, leg)] = meter.create_line(
                x0, 0, x1, 0,
                fill=self.METER_PEAK_COLOR, state="hidden"
            )

    def _apply_meter(self, channel, leg, level_db, peak_db):
        meter = self.channel_meters.get(channel)
        slices = self.meter_slices.get((channel, leg))

        if meter is None or not slices:
            return

        count = len(slices)
        lit_colors, dim_colors = self._meter_palette(count)
        lit = round(self._meter_fraction(level_db) * count)
        previous = self.meter_lit.get((channel, leg), 0)

        if lit != previous:
            # Only the crossed band changes state, so a bar that moved two
            # pixels costs two itemconfig calls rather than a full repaint.
            low, high = min(lit, previous), max(lit, previous)

            for index in range(low, high):
                meter.itemconfig(
                    slices[index],
                    fill=lit_colors[index] if index < lit else dim_colors[index]
                )

            self.meter_lit[(channel, leg)] = lit

        peak_item = self.meter_peak_items.get((channel, leg))

        if peak_item is not None:
            if peak_db is None or peak_db <= self.METER_FLOOR_DB:
                meter.itemconfig(peak_item, state="hidden")
            else:
                x0, x1 = self.meter_leg_spans[(channel, leg)]
                y = self.level_length - self._meter_fraction(peak_db) \
                    * self.level_length
                meter.coords(peak_item, x0, y, x1, y)
                meter.itemconfig(peak_item, state="normal")

    def refresh_meters(self):
        now = time.monotonic()
        elapsed = min(0.25, now - self.meter_ticked_at)
        self.meter_ticked_at = now

        if self.worker is not None:
            levels = self.worker.meter_levels
            seqs = self.worker.meter_seq
            rise_decay = math.exp(-elapsed / self.METER_RISE_TAU_SECONDS)
            rise_floor = self.METER_RISE_MIN_DB_PER_SEC * elapsed
            release = self.METER_RELEASE_DB_PER_SEC * elapsed
            peak_fall = self.METER_PEAK_FALL_DB_PER_SEC * elapsed

            # Each leg animates on its own: a stereo channel's two bars
            # are driven by two independent subscriptions and must be
            # free to sit at different levels.
            for channel, legs in self.channel_legs.items():
                for leg in legs:
                    self._advance_meter(
                        channel, leg, levels, seqs, now,
                        rise_decay, rise_floor, release, peak_fall
                    )

        self.master.after(self.METER_REFRESH_MS, self.refresh_meters)

    def _advance_meter(self, channel, leg, levels, seqs, now,
                       rise_decay, rise_floor, release, peak_fall):
        """Step one bar's ballistics by a frame and redraw it.

        Called once per leg, so a stereo strip's two bars rise, release
        and hold their peaks entirely independently of each other.
        """
        key = (channel, leg)
        shown = self.meter_shown.get(key, self.METER_FLOOR_DB)
        target = self.meter_target.get(key, self.METER_FLOOR_DB)
        seq = seqs.get(key, 0)

        # A packet only carries slots that actually changed, so a bumped
        # seq - not a changed value - is what marks a fresh sample. Arm
        # the rise on arrival; a sample at or below where the bar already
        # sits arms nothing and simply lets the release carry on through
        # it.
        #
        # The corollary: a signal steady enough to stay inside one 3 dB
        # wire step sends nothing, and the bar releases away under it.
        # Real programme material moves the RMS field on roughly every
        # other frame so it never gets far, but a held test tone will
        # visibly sag.
        if seq != self.meter_seen_seq.get(key):
            self.meter_seen_seq[key] = seq
            peak_db, rms_db = levels.get(key, (None, None))

            # The console reports peak and RMS independently and either
            # may be at its floor sentinel, so drive the bar from
            # whichever is actually present.
            target = max(
                rms_db if rms_db is not None else self.METER_FLOOR_DB,
                peak_db if peak_db is not None else self.METER_FLOOR_DB,
            )
            self.meter_target[key] = target
            self.meter_rising[key] = target > shown

        if self.meter_rising.get(key):
            # Ease up to the sample, never overshooting it. Once it is
            # reached the push is spent: the bar releases from there
            # until the next packet lifts it again.
            eased = target + (shown - target) * rise_decay
            shown = min(target, max(eased, shown + rise_floor))

            if shown >= target:
                self.meter_rising[key] = False
        else:
            shown = max(self.METER_FLOOR_DB, shown - release)

        self.meter_shown[key] = shown

        held = self.meter_peak.get(key, self.METER_FLOOR_DB)

        # Tracked against the incoming target rather than the animated
        # bar, so a brief transient still marks its true peak even when
        # the bar is still sliding up to it.
        if target >= held:
            held = target
            self.meter_peak_at[key] = now
        elif now - self.meter_peak_at.get(key, now) > \
                self.METER_PEAK_HOLD_SECONDS:
            held = max(shown, held - peak_fall)

        self.meter_peak[key] = held

        self._apply_meter(channel, leg, shown, held)

    def current_aux(self):
        index = self.aux_combo.current()

        if index < 0:
            return None

        return self.aux_list[index][0]

    def on_aux_selected(self, event=None):
        aux = self.current_aux()

        if aux is None or self.worker is None or not self.worker.is_alive():
            return

        # A mono bus sums its sends to one leg, so send_pan does nothing
        # on it - the control comes off the strip and its value is never
        # asked for, which also keeps a bank's worth of dead queries off
        # the wire on every aux change.
        stereo = self.worker.aux_is_stereo(aux)
        self._show_pan_sliders(stereo)

        for channel in self.channels:
            self.command_queue.put(
                f"/Input_Channels/{channel}/Aux_Send/{aux}/send_level/?"
            )

            # Mute is per-aux as well, so it is read here rather than in
            # a pass of its own - changing aux has to re-read it for the
            # same reason level and pan do.
            self.command_queue.put(
                f"/Input_Channels/{channel}/Aux_Send/{aux}/send_on/?"
            )

            if stereo:
                self.command_queue.put(
                    f"/Input_Channels/{channel}/Aux_Send/{aux}/send_pan/?"
                )

    def _show_pan_sliders(self, shown):
        for channel, pan_slider in self.pan_sliders.items():
            # winfo_manager() is "" while unpacked and "pack" once packed,
            # so it answers "is this on screen" without tracking a
            # parallel flag that could drift out of step with Tk.
            if bool(pan_slider.winfo_manager()) == shown:
                continue

            if shown:
                # Packing appends to the end of the column, so without an
                # explicit anchor a re-shown slider would reappear below
                # the Mute button instead of above it.
                pan_slider.pack(
                    before=self.mute_buttons[channel].canvas,
                    pady=(self.STRIP_PAN_PAD, 0)
                )
            else:
                pan_slider.pack_forget()

        # Showing or hiding the pan slider changes how much of a strip is
        # chrome, so the faders get the freed height (or give it back).
        self._fit_level_length(self.canvas.winfo_height())

    def on_slider_change(self, channel, value):
        if self.suppress_send:
            return

        aux = self.current_aux()

        if aux is None or self.worker is None or not self.worker.is_alive():
            return

        db = round(self._fraction_to_db(float(value)), 2)

        self.command_queue.put(
            f"/Input_Channels/{channel}/Aux_Send/{aux}/send_level {db}"
        )

    def on_slider_release(self, channel):
        self.dragging.discard(channel)
        self.drag_released_at[channel] = time.monotonic()

    def on_pan_change(self, channel, value):
        if self.suppress_send:
            return

        aux = self.current_aux()

        if aux is None or self.worker is None or not self.worker.is_alive():
            return

        ui_pan = round(float(value), 2)
        wire_pan = self._ui_pan_to_wire(ui_pan)

        self.command_queue.put(
            f"/Input_Channels/{channel}/Aux_Send/{aux}/send_pan {wire_pan}"
        )

    def on_pan_release(self, channel):
        self.pan_dragging.discard(channel)
        self.pan_drag_released_at[channel] = time.monotonic()

    def on_pan_double_click(self, channel):
        pan_slider = self.pan_sliders.get(channel)

        if pan_slider is None:
            return

        # .set() triggers the slider's own command callback (on_pan_change),
        # which sends the corresponding OSC command - no need to send here too.
        pan_slider.set(0.0)

    def _is_muted(self, channel):
        """Whether this channel is out of the selected aux's mix.

        Read from the cache rather than from the Mute button, which used
        to carry the state in its own label. Absent from the cache - the
        console has not reported this send yet - reads as unmuted, the
        same assumption the remote server makes.
        """
        aux = self.current_aux()

        if aux is None or self.worker is None:
            return False

        key = f"/Input_Channels/{channel}/Aux_Send/{aux}/send_on"

        if key not in self.worker.cache:
            return False

        return not bool(self.worker.cache[key][0])

    # send_on is the inverse of mute: 0.0 drops the channel out of the
    # selected aux's mix, 1.0 puts it back. Deliberately not the
    # console-wide /Input_Channels/{n}/mute this used to write, which cut
    # the source at the head - out of the engineer's mix, out of every
    # other operator's, and off the desk. The phone app has always muted
    # per-aux (see RemoteServer._set_mute); this is the desktop matching
    # it, so a Mute pressed here and one pressed on a phone watching the
    # same aux now mean the same thing.
    def on_mute_toggle(self, channel):
        if self.worker is None or not self.worker.is_alive():
            return

        aux = self.current_aux()

        if aux is None:
            return

        send_on = 1.0 if self._is_muted(channel) else 0.0
        self.command_queue.put(
            f"/Input_Channels/{channel}/Aux_Send/{aux}/send_on {send_on}"
        )

    def refresh_levels(self):
        if self.worker is not None:
            aux = self.current_aux()

            if aux is not None:
                for channel, slider in self.sliders.items():
                    if channel in self.dragging:
                        continue

                    released_at = self.drag_released_at.get(channel)
                    if released_at is not None and \
                            time.monotonic() - released_at < self.DRAG_GRACE_SECONDS:
                        continue

                    key = f"/Input_Channels/{channel}/Aux_Send/{aux}/send_level"

                    if key not in self.worker.cache:
                        continue

                    level = round(self.worker.cache[key][0], 2)
                    current_level = self._fraction_to_db(slider.get())

                    if abs(current_level - level) > self.LEVEL_EPSILON:
                        self.suppress_send = True
                        slider.set(self._db_to_fraction(level))
                        self.suppress_send = False

                for channel, pan_slider in self.pan_sliders.items():
                    if channel in self.pan_dragging:
                        continue

                    released_at = self.pan_drag_released_at.get(channel)
                    if released_at is not None and \
                            time.monotonic() - released_at < self.DRAG_GRACE_SECONDS:
                        continue

                    key = f"/Input_Channels/{channel}/Aux_Send/{aux}/send_pan"

                    if key not in self.worker.cache:
                        continue

                    wire_pan = round(self.worker.cache[key][0], 2)
                    ui_pan = self._wire_pan_to_ui(wire_pan)
                    current_pan = round(float(pan_slider.get()), 2)

                    if abs(current_pan - ui_pan) > self.LEVEL_EPSILON:
                        self.suppress_send = True
                        pan_slider.set(ui_pan)
                        self.suppress_send = False

            for channel, button in self.mute_buttons.items():
                bg, fg = self._mute_button_colors(
                    channel, self._is_muted(channel)
                )
                # No text= : the label stays "MUTE" and the fill carries
                # the whole of the state.
                button.config(bg=bg, fg=fg)

            self.build_bank_buttons()
            self._open_default_bank()
            self._resync_after_snapshot()

        self.master.after(self.REFRESH_MS, self.refresh_levels)


class MainWindow:

    # How long to wait before automatically retrying a connection that
    # dropped on its own (mixer power loss, network blip, ...) rather
    # than one the user explicitly disconnected.
    RECONNECT_DELAY_SECONDS = 5

    # Shown in both adapter dropdowns for "don't pin this to a card".
    # Stored as an empty string, which is what every binding site reads
    # as "let the OS decide" - all adapters for listening, the routing
    # table for sending.
    NIC_AUTOMATIC = "Automatic (all adapters)"

    # What phones are told while CLMix is in DiGiCo App mode - on the
    # way out if connected, and on every login attempt until it ends.
    DIGICO_MODE_MESSAGE = "CLMix is in DiGiCo App mode"

    # Phases in which something is still in progress: the spinner turns,
    # the status line repaints every tick, and the connect button offers
    # "Disconnect". One definition so those three can never disagree.
    BUSY_PHASES = ("connecting", "loading", "reconnecting")

    # Spinner/countdown repaint rate. Fast enough that the arc reads as
    # motion rather than a stutter, slow enough to be free next to the
    # 33ms meter repaint already running.
    STATUS_TICK_MS = 80

    # How far the spinner arc sweeps, and how far it advances per tick.
    SPINNER_EXTENT = 100
    SPINNER_STEP_DEGREES = 24

    def __init__(self):

        # className is what X11 reports as the window's WM_CLASS, and a
        # Linux desktop matches *that* against an installed .desktop file
        # to decide what to show in the dock and the app switcher. Left at
        # tkinter's default the window announces itself as "Tk", matches
        # nothing, and gets a placeholder icon no matter what iconphoto
        # below says - a Wayland compositor never reads _NET_WM_ICON for
        # an XWayland window.
        #
        # Tk normalizes the name it is given, so this arrives as the class
        # "Clmix" rather than "CLMix". packaging/linux/clmix.desktop's
        # StartupWMClass has to match that spelling exactly, so don't
        # change one without the other.
        self.root = tk.Tk(className=WM_CLASS_NAME)
        self.root.title("CLMix")
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")

        # The startup size is also the floor. The channel strips stretch
        # to fill whatever height the window has, and LEVEL_LENGTH is the
        # shortest fader worth showing - so rather than letting the strips
        # be squashed below that, the window itself refuses to go under
        # the size that produces it. Growing is unrestricted.
        self.root.minsize(WINDOW_WIDTH, WINDOW_HEIGHT)

        # Keep a reference on root itself - iconphoto doesn't retain the
        # PhotoImage, so a local-only reference gets garbage collected and
        # the icon silently reverts to the Tk default. Still worth setting
        # on top of the .desktop match above: it is what X11 desktops (and
        # the window's own title bar, on the ones that draw an icon there)
        # actually use.
        self.root.icon_image = tk.PhotoImage(data=ICON_PNG_BASE64)
        self.root.iconphoto(True, self.root.icon_image)

        self.worker = None
        self.about_window = None
        self.access_panel = None
        self.aux_visibility_panel = None
        self.backup_window = None
        self.show_backup_window = None
        self.logs_window = None
        self.presets_window = None
        self.remote_server = None

        # DiGiCo App Capture - see services/digico_bridge.py. Runs only
        # while connected with the setting on; _capture_error keeps the
        # reason the last start failed on screen until the next attempt.
        self.capture_bridge = None
        self._capture_error = None
        self._capture_status_shown = None

        self._user_disconnected = True
        self._reconnect_job = None

        # Everything the status line renders from. _status_phase is the
        # single source of truth for both the label and the connect
        # button, so the two can never disagree about whether something
        # is still in progress:
        #   idle         nothing running - the only phase showing "Connect"
        #   connecting   socket opening
        #   loading      pulling parameters (worker.loading_stage says which)
        #   ready        loaded
        #   reconnecting waiting out RECONNECT_DELAY_SECONDS after a drop
        self._status_phase = "idle"
        self._failure_reason = None
        self._reconnect_at = None
        self._spinner_angle = 0
        # Whether this attempt ever finished loading, which is what
        # separates "could not reach the mixer" from "the mixer went away".
        self._was_loaded = False

        # Last check_for_update() result, from the startup check below or
        # from the About window's own Check button. Held here rather than
        # in AboutWindow because that window is built and destroyed on
        # demand, and this survives it.
        self.latest_update = None

        self.command_queue = queue.Queue()
        self.message_queue = queue.Queue()

        self.settings = self.load_settings()
        self.user_store = UserStore()
        self.preset_store = PresetStore()
        self.backup_store = BackupStore(backups_dir=self.settings.get("backup_dir"))

        self._palette_recolor_hooked = False
        self.apply_theme(self.settings["theme"], persist=False)
        self.build_ui()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # Now, not during the update itself: this is the one moment the
        # installer downloaded last time is guaranteed not to be running,
        # since the app it installed is the one doing the clearing.
        updater.sweep_download_dir()

        self.root.after(100, self.process_messages)
        self.root.after(self.STATUS_TICK_MS, self._tick_status)
        self.root.after(STARTUP_UPDATE_CHECK_MS, self._start_update_check)

    def build_menu_bar(self):
        menu_bar = tk.Menu(self.root)

        self.preference_menu = tk.Menu(menu_bar, tearoff=False)
        self.preference_menu.add_command(
            label="Setup\u2026", command=self.open_setup_window
        )
        menu_bar.add_cascade(label="Setup", menu=self.preference_menu)

        self.theme_var = tk.StringVar(value=self.settings["theme"])
        self.view_menu = tk.Menu(menu_bar, tearoff=False)
        self.view_menu.add_radiobutton(
            label="Light", variable=self.theme_var, value="light",
            command=lambda: self.apply_theme(self.theme_var.get())
        )
        self.view_menu.add_radiobutton(
            label="Dark", variable=self.theme_var, value="dark",
            command=lambda: self.apply_theme(self.theme_var.get())
        )
        menu_bar.add_cascade(label="View", menu=self.view_menu)

        self.help_menu = tk.Menu(menu_bar, tearoff=False)
        self.help_menu.add_command(label="Logs", command=self.open_logs_window)
        self.help_menu.add_command(label="CLMix Backup", command=self.open_backup_window)
        self.help_menu.add_command(
            label="Mixer Backup", command=self.open_show_backup_window
        )
        self.help_menu.add_command(label="About", command=self.open_about_window)
        # Kept so the startup check can relabel this one entry - looking it
        # up by its current label would stop working the moment it changes.
        self.about_menu_index = self.help_menu.index("end")
        menu_bar.add_cascade(label="Help", menu=self.help_menu)

        self.root.config(menu=menu_bar)

    def build_ui(self):

        self.build_menu_bar()

        top_bar = ttk.Frame(self.root, padding=(15, 10))
        top_bar.pack(fill="x")

        self.connect_btn = ttk.Button(
            top_bar,
            text="Connect",
            command=self.on_connect_button
        )
        self.connect_btn.pack(side="left")

        ttk.Button(
            top_bar,
            text="Presets",
            command=self.open_presets_window
        ).pack(side="left", padx=(8, 0))

        ttk.Separator(top_bar, orient="vertical").pack(
            side="left", fill="y", padx=15
        )

        status_frame = ttk.Frame(top_bar)
        status_frame.pack(side="left")

        mixer_row = ttk.Frame(status_frame)
        mixer_row.pack(side="top", anchor="w")

        ttk.Label(mixer_row, text="Status:").pack(side="left", padx=(0, 6))

        self.indicator = tk.Canvas(
            mixer_row, width=16, height=16, highlightthickness=0,
            bg=panel_bg(top_bar)
        )
        self.indicator.pack(side="left")
        self.light = self.indicator.create_oval(2, 2, 14, 14, fill="red")

        # Same 16x16 cell as the status light, one shown at a time: a
        # solid dot when the state is settled, a rotating arc while
        # something is still in progress. Sharing the cell keeps the row
        # from reflowing every time the phase changes.
        self.spinner = self.indicator.create_arc(
            2, 2, 14, 14,
            start=0, extent=self.SPINNER_EXTENT, style="arc",
            outline="orange", width=2, state="hidden"
        )

        self.status_label = ttk.Label(
            mixer_row, text="Disconnected", font=("TkDefaultFont", 10, "bold")
        )
        self.status_label.pack(side="left", padx=(6, 0))

        snapshot_row = ttk.Frame(status_frame)
        snapshot_row.pack(side="top", anchor="w", pady=(4, 0))

        self.snapshot_label = ttk.Label(snapshot_row, text="Snapshot: --")
        self.snapshot_label.pack(side="left")

        ttk.Separator(self.root, orient="horizontal").pack(fill="x")

        # No padding, and the section's own background: everything below
        # the control bar's separator is the mixer section, edge to edge,
        # with the breathing room coming from channels_frame's own
        # padding inside it instead of a lighter margin around it.
        configure_section_styles(self.root)
        frame = ttk.Frame(self.root, style="Section.TFrame")
        frame.pack(fill="both", expand=True)

        self.aux_panel = AuxLevelsPanel(
            frame, self.command_queue, get_hidden_auxes=self.get_hidden_auxes
        )

        # After the panel, so it stacks above everything the panel built.
        self._build_digico_overlay(frame)

        self.build_setup_window()
        self._show_digico_overlay(bool(self.settings.get("digico_capture")))

    def build_setup_window(self):
        """The single Setup window: Config, Accounts and Aux as notebook tabs.

        Built once at startup and hidden rather than created on demand, so
        the Config entries below exist before the first connect(). The
        Accounts and Aux tabs read mixer state, which is not available yet
        at startup - open_setup_window() refreshes them on the way in, and
        _on_setup_tab_changed does the same when the operator switches tabs.
        """
        self.setup_window = tk.Toplevel(self.root)
        self.setup_window.title("Setup")
        # Wide enough for the Config tab's third column (the adapter
        # dropdowns) without clipping them.
        self.setup_window.geometry("860x740")
        self.setup_window.protocol("WM_DELETE_WINDOW", self.close_setup_window)

        self.setup_notebook = ttk.Notebook(self.setup_window)
        self.setup_notebook.pack(fill="both", expand=True, padx=10, pady=10)

        config_tab = ttk.Frame(self.setup_notebook)
        accounts_tab = ttk.Frame(self.setup_notebook)
        aux_tab = ttk.Frame(self.setup_notebook)

        self.setup_notebook.add(config_tab, text="Config")
        self.setup_notebook.add(accounts_tab, text="Accounts")
        self.setup_notebook.add(aux_tab, text="Aux")

        self.build_config_tab(config_tab)

        self.access_panel = AccessPanel(
            accounts_tab, self.user_store, lambda: self.worker,
            self.get_hidden_auxes
        )

        self.aux_visibility_panel = AuxPanel(
            aux_tab, self.settings, self.save_settings, lambda: self.worker,
            on_change=self.aux_panel.refresh_aux_list
        )

        self.setup_notebook.bind(
            "<<NotebookTabChanged>>", self._on_setup_tab_changed
        )

        self.setup_window.withdraw()

    def build_config_tab(self, parent):
        frame = ttk.Frame(parent, padding=15)
        frame.pack(fill="x")

        port_vcmd = (self.setup_window.register(self._validate_port_input), "%P")

        ttk.Label(frame, text="Mixer IP Address").grid(
            row=0, column=0, sticky="w"
        )

        self.ip_entry = ttk.Entry(frame, width=25)
        self.ip_entry.insert(0, self.settings["mixer_ip"])
        self.ip_entry.grid(row=0, column=1, padx=5, pady=5)

        # Which network card to reach the console from. The entry beside
        # it is the console's own address; this is our end of that
        # conversation.
        self.mixer_nic_combo = ttk.Combobox(frame, width=26, state="readonly")
        self.mixer_nic_combo.grid(row=0, column=2, padx=5, pady=5, sticky="w")

        ttk.Label(frame, text="Send Port").grid(
            row=1, column=0, sticky="w"
        )

        self.send_port_entry = ttk.Entry(
            frame,
            width=15,
            validate="key",
            validatecommand=port_vcmd
        )
        self.send_port_entry.insert(0, self.settings["send_port"])
        self.send_port_entry.grid(row=1, column=1, padx=5, pady=5)

        ttk.Label(frame, text="Rec Port").grid(
            row=2, column=0, sticky="w"
        )

        self.recv_port_entry = ttk.Entry(
            frame,
            width=15,
            validate="key",
            validatecommand=port_vcmd
        )
        self.recv_port_entry.insert(0, self.settings["recv_port"])
        self.recv_port_entry.grid(row=2, column=1, padx=5, pady=5)

        ttk.Label(frame, text="Remote Port").grid(
            row=3, column=0, sticky="w"
        )

        self.remote_port_entry = ttk.Entry(
            frame,
            width=15,
            validate="key",
            validatecommand=port_vcmd
        )
        self.remote_port_entry.insert(0, self.settings["remote_port"])
        self.remote_port_entry.grid(row=3, column=1, padx=5, pady=5)

        # Which network card the phones connect to. Pinning it also pins
        # what the server advertises over mDNS, so a phone that discovers
        # it is handed an address that is actually being listened on.
        self.remote_nic_combo = ttk.Combobox(frame, width=26, state="readonly")
        self.remote_nic_combo.grid(row=3, column=2, padx=5, pady=5, sticky="w")

        ttk.Label(
            frame,
            text="Adapter chooses which network card a connection uses. "
                 "Leave on Automatic unless this machine has more than one.",
            wraplength=780,
            justify="left"
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(12, 0))

        self._refresh_nic_choices()

        self.build_capture_section(parent)

    def build_capture_section(self, parent):
        """DiGiCo App Capture, in its own section under the connection
        settings - it changes what this machine pretends to be, which is
        a different kind of setting from where the console is."""
        ttk.Separator(parent).pack(fill="x", padx=15, pady=(0, 4))

        frame = ttk.Frame(parent, padding=(15, 8, 15, 15))
        frame.pack(fill="both", expand=True)

        heading = ttk.Frame(frame)
        heading.pack(fill="x")

        ttk.Label(
            heading, text="DiGiCo App Capture", font=("TkDefaultFont", 10, "bold")
        ).pack(side="left")

        self.capture_var = tk.BooleanVar(
            value=bool(self.settings.get("digico_capture"))
        )
        ttk.Checkbutton(
            heading,
            style="Switch.TCheckbutton",
            variable=self.capture_var,
            command=self._on_capture_toggled
        ).pack(side="left", padx=(12, 0))

        ttk.Button(
            heading, text="Open Capture Folder",
            command=self._open_capture_folder
        ).pack(side="right")

        ttk.Label(
            frame,
            text="Stands in for the console so the official DiGiCo app can "
                 "connect to this computer instead. Everything is passed "
                 "through unchanged in both directions and recorded to a "
                 "capture file. Set the app to the same ports as above; it "
                 "finds this computer on the Server adapter's network. "
                 "While on, CLMix works only as this bridge: its own "
                 "controls, meters and phone connections are locked.",
            wraplength=780,
            justify="left"
        ).pack(fill="x", pady=(8, 0))

        self.capture_status_label = ttk.Label(
            frame, text="", wraplength=780, justify="left"
        )
        self.capture_status_label.pack(fill="x", pady=(8, 0))
        self._refresh_capture_status()

        self._build_capture_view(frame)

    # Direction colors in the live view. Mid-tone on purpose, so the same
    # value reads on both the light and the dark theme's background.
    CAPTURE_VIEW_COLORS = {
        "APP->MIXER": "#4a9eda",
        "MIXER->APP": "#5bb85b",
        "CLMIX->MIXER": "#8a8a8a",
        "MIXER->CLMIX": "#8a8a8a",
        "hex": "#8a8a8a",
        "note": "#d9a441",
    }

    # Lines the live view keeps before trimming its oldest. Plenty to
    # scroll back through a connect sequence; the file keeps the rest.
    CAPTURE_VIEW_MAX_LINES = 2000

    def _build_capture_view(self, parent):
        """Live view of the capture, as it is being written.

        A reading aid, not the record: the same fields as the file, but
        with the time cut to the second's fraction and the hex dimmed, so
        the addresses stand out. The file is what to study afterwards.
        """
        view = ttk.Frame(parent)
        view.pack(fill="both", expand=True, pady=(10, 0))
        view.rowconfigure(0, weight=1)
        view.columnconfigure(0, weight=1)

        # The fixed font's real family, not ("TkFixedFont", 9): in a tuple
        # Tk reads that name as a family, finds none called it, and falls
        # back to the proportional UI font - which undoes the column
        # layout in _capture_view_chunks.
        mono = tkfont.nametofont("TkFixedFont").actual("family")

        self.capture_view = tk.Text(
            view, wrap="none", state="disabled", height=10, font=(mono, 9)
        )
        y_scroll = ttk.Scrollbar(view, orient="vertical",
                                 command=self.capture_view.yview)
        x_scroll = ttk.Scrollbar(view, orient="horizontal",
                                 command=self.capture_view.xview)
        self.capture_view.configure(
            yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set
        )

        self.capture_view.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")

        for tag, color in self.CAPTURE_VIEW_COLORS.items():
            self.capture_view.tag_configure(tag, foreground=color)

        # The bridge whose records are on screen, and the last one shown.
        # Kept after that bridge stops, so its final lines stay readable
        # until the next capture starts.
        self._capture_view_bridge = None
        self._capture_view_seq = 0

        self._reset_capture_view(
            "Traffic appears here live while DiGiCo App mode is running."
        )

    def _reset_capture_view(self, placeholder=None):
        self.capture_view.configure(state="normal")
        self.capture_view.delete("1.0", "end")

        if placeholder:
            self.capture_view.insert("end", placeholder + "\n", "hex")

        self.capture_view.configure(state="disabled")

    @staticmethod
    def _capture_view_chunks(record):
        """(text, tag) pieces for one record, laid out in fixed columns."""
        if record[0] == "note":
            _kind, stamp, text = record
            return [(f"{stamp[11:23]}  # {text}\n", "note")]

        _kind, stamp, direction, peer, length, decoded, raw = record
        return [
            (f"{stamp[11:23]}  ", ""),
            (f"{direction:<13}", direction),
            (f"{peer:<22}{length:>5}  {decoded}  ", ""),
            (f"{raw}\n", "hex"),
        ]

    def _refresh_capture_view(self):
        bridge = self.capture_bridge

        if bridge is not None and bridge is not self._capture_view_bridge:
            # A new capture: start the view over, the way the file does.
            self._capture_view_bridge = bridge
            self._capture_view_seq = 0
            self._reset_capture_view()

        bridge = self._capture_view_bridge

        if bridge is None:
            return

        records = bridge.recent_records(self._capture_view_seq)

        if not records:
            return

        self._capture_view_seq = records[-1][0]

        args = []
        for _seq, record in records:
            for text, tag in self._capture_view_chunks(record):
                args.extend((text, tag))

        view = self.capture_view
        # Only follow new lines if already at the bottom - scrolling up to
        # read something must not be yanked away by the next packet.
        at_bottom = view.yview()[1] >= 0.999

        view.configure(state="normal")
        # One insert for the whole batch rather than one per line; a
        # connect sequence lands as a hundred-odd records at once.
        view.insert("end", *args)

        excess = int(view.index("end-1c").split(".")[0]) - \
            self.CAPTURE_VIEW_MAX_LINES
        if excess > 0:
            view.delete("1.0", f"{excess + 1}.0")

        view.configure(state="disabled")

        if at_bottom:
            view.see("end")

    def _refresh_nic_choices(self):
        """Fill both adapter dropdowns from the adapters present right now.

        Keeps whatever is already selected if that adapter still exists,
        so reopening Setup (or switching tabs, which refreshes) does not
        throw away a choice that has not been connected with yet.
        Otherwise it falls back to what was saved.

        An adapter that was saved but is now gone - a cable pulled, or
        settings carried to another machine - is kept in the list marked
        unavailable rather than silently swapped for Automatic, which
        would look like the setting had been forgotten.
        """
        interfaces = list_ipv4_interfaces()
        self._nic_ip_by_label = {label: ip for label, ip in interfaces}

        for combo, setting in (
            (self.mixer_nic_combo, "mixer_bind_ip"),
            (self.remote_nic_combo, "remote_bind_ip"),
        ):
            current = combo.get()
            wanted = current if current in self._nic_ip_by_label else \
                self._nic_label_for(self.settings.get(setting, ""), interfaces)

            values = [self.NIC_AUTOMATIC] + [label for label, _ in interfaces]

            if wanted not in values:
                values.append(wanted)

            combo.configure(values=values)
            combo.set(wanted)

    def _nic_label_for(self, ip, interfaces):
        if not ip:
            return self.NIC_AUTOMATIC

        for label, address in interfaces:
            if address == ip:
                return label

        return f"{ip} (unavailable)"

    def _selected_nic_ip(self, combo):
        """The address a dropdown is pointing at - "" for Automatic."""
        label = combo.get()

        if not label or label == self.NIC_AUTOMATIC:
            return ""

        if label in self._nic_ip_by_label:
            return self._nic_ip_by_label[label]

        # "<ip> (unavailable)" - hand back the address so connect() can
        # say so plainly rather than binding to nothing.
        return label.split(" ", 1)[0]

    def open_setup_window(self):
        self.refresh_setup_tabs()
        self.setup_window.deiconify()
        self.setup_window.lift()

    def close_setup_window(self):
        self.setup_window.withdraw()

    def _on_setup_tab_changed(self, event=None):
        # Only worth refreshing what is actually on screen; the window
        # stays alive in the background between openings, so without this
        # a tab built while disconnected would keep showing stale content.
        if self.setup_window.winfo_viewable():
            self.refresh_setup_tabs()

    def refresh_setup_tabs(self):
        """Re-sync the Accounts and Aux tabs with the current mixer state.

        The Aux tab has to be rebuilt outright rather than refreshed: its
        rows are laid out from the aux list and colored with values read at
        build time, so there is nothing to update in place.
        """
        if self.access_panel is not None:
            self.access_panel.refresh_list()

        if self.aux_visibility_panel is not None:
            self.aux_visibility_panel.rebuild()

    def _validate_port_input(self, proposed):
        # Caps keystroke entry at 5 digits (max valid port is 65535) -
        # full range/validity is still checked at connect() time via
        # _is_valid_port, since a 5-digit string can still be > 65535.
        return proposed == "" or (proposed.isdigit() and len(proposed) <= 5)

    @staticmethod
    def _is_valid_ip(value):
        try:
            ipaddress.IPv4Address(value)
            return True
        except ValueError:
            return False

    @staticmethod
    def _is_valid_port(value):
        return value.isdigit() and 1 <= int(value) <= 65535

    @staticmethod
    def load_settings():
        defaults = {
            "mixer_ip": "192.168.1.100",
            "send_port": "10023",
            "recv_port": "10024",
            "remote_port": "8765",
            # Empty means "any adapter" - see MainWindow._refresh_nic_choices.
            "mixer_bind_ip": "",
            "remote_bind_ip": "",
            "theme": "dark",
            "hidden_auxes": [],
            "backup_dir": None,
            "digico_capture": False,
            # None means services.show_backup.DEFAULT_ROOT.
            "show_backup_dir": None,
        }

        try:
            with open(SETTINGS_PATH) as f:
                defaults.update(json.load(f))
        except (OSError, json.JSONDecodeError):
            pass

        return defaults

    def save_settings(self):
        try:
            with open(SETTINGS_PATH, "w") as f:
                json.dump(self.settings, f)
        except OSError as ex:
            log("error", f"Failed to save settings: {ex!r}")

    def get_hidden_auxes(self):
        return set(self.settings.get("hidden_auxes", []))

    def apply_theme(self, theme, persist=True):
        sv_ttk.set_theme(theme)
        self._hook_palette_recolor()
        self._recolor_widgets()

        if persist:
            self.settings["theme"] = theme
            self.save_settings()

    def _hook_palette_recolor(self):
        """Re-apply our own colours *after* sv_ttk's palette pass.

        sv_ttk answers <<ThemeChanged>> with tk_setPalette, and that event
        is delivered later from the event loop - so the _recolor_widgets()
        call in apply_theme() always runs *before* it. On Tk 8.6 that
        ordering never mattered: its palette only repaints widgets still
        on their default colours. Tk 9 (what python.org's Python 3.14
        ships) repaints every plain tk widget unconditionally, so every
        colour we had just set - the section-dark labels and canvas, the
        channel strips - was overwritten with the theme's generic
        background a moment later. Appending to the same binding sv_ttk
        uses runs our pass straight after its own, on every theme change
        from any source.

        Bound once, and only after the first set_theme(): sv_ttk sources
        the Tcl side that installs its binding on that call, and a "+"
        binding registered before it would run ahead of it instead.
        Bound on the root's window class because that is what sv_ttk
        binds on - and this app renames the class (see WM_CLASS_NAME), so
        the name has to be read back rather than assumed to be "Tk".
        """
        if self._palette_recolor_hooked:
            return

        self.root.bind_class(
            self.root.winfo_class(), "<<ThemeChanged>>",
            lambda event: self._recolor_widgets(), add="+"
        )
        self._palette_recolor_hooked = True

    def _recolor_widgets(self):
        bg = panel_bg(self.root)
        self.root.configure(bg=bg)

        if getattr(self, "indicator", None) is not None:
            self.indicator.configure(bg=bg)

        if getattr(self, "aux_panel", None) is not None:
            self.aux_panel.apply_theme()

        if getattr(self, "digico_overlay_labels", None) is not None:
            self._recolor_digico_overlay()

        if getattr(self, "aux_visibility_panel", None) is not None:
            self.aux_visibility_panel.rebuild()

        if getattr(self, "about_window", None) is not None and \
                self.about_window.window.winfo_exists():
            self.about_window.apply_theme()

    def _session_active(self):
        """Whether a connection is up, being made, or queued to retry.

        Deliberately not "is there a worker": between a dropped link and
        the retry firing there is no worker, yet the app is still working
        on the operator's behalf and the button has to offer a way out of
        that.
        """
        # The phase matters as well as the worker: connect() enters
        # "connecting" before the worker exists, and without this that
        # gap would show "Connect" - and a click landing in it would
        # start a second worker rather than cancelling the first.
        return (self.worker is not None
                or self._reconnect_job is not None
                or self._status_phase in self.BUSY_PHASES)

    def _update_connect_button(self):
        self.connect_btn.config(
            text="Disconnect" if self._session_active() else "Connect"
        )

    def _status_text_and_color(self):
        """What the status line should read right now."""
        if self._status_phase == "connecting":
            return "Connecting", "orange"

        if self._status_phase == "loading":
            # Published by the worker as it walks the boot sequence, so
            # this names the parameter actually outstanding rather than
            # just saying "busy".
            stage = self.worker.loading_stage if self.worker else None
            return f"Loading {stage}" if stage else "Loading", "orange"

        if self._status_phase == "ready":
            return "Connected", "green"

        if self._status_phase == "reconnecting":
            reason = self._failure_reason or "Disconnected"
            remaining = 0

            if self._reconnect_at is not None:
                remaining = max(0, math.ceil(self._reconnect_at - time.monotonic()))

            return f"{reason} - trying again in {remaining}s", "orange"

        return "Disconnected", "red"

    def _render_status(self):
        text, color = self._status_text_and_color()
        self.status_label.config(text=text)

        busy = self._status_phase in self.BUSY_PHASES

        if busy:
            self.indicator.itemconfig(self.light, state="hidden")
            self.indicator.itemconfig(
                self.spinner, state="normal",
                outline=color, start=self._spinner_angle
            )
        else:
            self.indicator.itemconfig(self.spinner, state="hidden")
            self.indicator.itemconfig(self.light, state="normal", fill=color)

    def _tick_status(self):
        if self._status_phase in self.BUSY_PHASES:
            self._spinner_angle = \
                (self._spinner_angle + self.SPINNER_STEP_DEGREES) % 360
            # Repainted every tick regardless of whether the phase moved:
            # the loading stage and the retry countdown both change
            # underneath a phase that stays put.
            self._render_status()

        self.root.after(self.STATUS_TICK_MS, self._tick_status)

    def _set_status_phase(self, phase):
        self._status_phase = phase
        self._update_connect_button()
        self._render_status()

    def on_connect_button(self):
        if self._session_active():
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        log("debug", "Connect button pressed")

        if self.worker is not None:
            log("debug", "Worker already running, ignoring")
            return

        mixer_ip = self.ip_entry.get().strip()
        send_port = self.send_port_entry.get().strip()
        recv_port = self.recv_port_entry.get().strip()
        remote_port = self.remote_port_entry.get().strip()

        if not self._is_valid_ip(mixer_ip):
            log("error", f"Invalid mixer IP address: {mixer_ip!r}")
            self.status_label.config(text="Invalid mixer IP")
            return

        if not self._is_valid_port(send_port):
            log("error", f"Invalid send port: {send_port!r}")
            self.status_label.config(text="Invalid send port")
            return

        if not self._is_valid_port(recv_port):
            log("error", f"Invalid receive port: {recv_port!r}")
            self.status_label.config(text="Invalid receive port")
            return

        if not self._is_valid_port(remote_port):
            log("error", f"Invalid remote port: {remote_port!r}")
            self.status_label.config(text="Invalid remote port")
            return

        log("debug", f"mixer_ip={mixer_ip!r} send_port={send_port!r} "
            f"recv_port={recv_port!r} remote_port={remote_port!r}")

        mixer_bind_ip = self._selected_nic_ip(self.mixer_nic_combo)
        remote_bind_ip = self._selected_nic_ip(self.remote_nic_combo)

        # Checked before anything is started: binding to an address the
        # machine no longer has fails deep inside the worker thread as a
        # bare OS error, which says nothing about which dropdown caused it.
        available = {ip for _, ip in list_ipv4_interfaces()}

        for address, name in ((mixer_bind_ip, "Mixer"), (remote_bind_ip, "Server")):
            if address and address not in available:
                log("error", f"{name} adapter {address} is not available")
                self.status_label.config(text=f"{name} adapter unavailable")
                return

        self.settings.update({
            "mixer_ip": mixer_ip,
            "send_port": send_port,
            "recv_port": recv_port,
            "remote_port": remote_port,
            "mixer_bind_ip": mixer_bind_ip,
            "remote_bind_ip": remote_bind_ip,
        })
        self.save_settings()

        self._user_disconnected = False

        if self._reconnect_job is not None:
            self.root.after_cancel(self._reconnect_job)
            self._reconnect_job = None

        self._was_loaded = False
        self._reconnect_at = None
        # Cleared per attempt, so the reason shown always belongs to the
        # attempt that just failed rather than an older one.
        self._failure_reason = None
        self._set_status_phase("connecting")

        self.worker = MixerWorker(
            mixer_ip,
            int(send_port),
            int(recv_port),
            self.command_queue,
            self.message_queue,
            bind_ip=mixer_bind_ip
        )

        if self.settings.get("digico_capture"):
            # Before the worker starts, so nothing the panel queues on
            # load - its first meter subscription included - ever reaches
            # the console.
            self.worker.enter_bridge_only()

        self.worker.start()
        log("debug", "Worker thread started")

        self.remote_server = RemoteServer(
            lambda: self.worker, self.command_queue, int(remote_port),
            self.user_store, self.preset_store,
            get_hidden_auxes=self.get_hidden_auxes,
            bind_ip=remote_bind_ip
        )

        if self.settings.get("digico_capture"):
            self.remote_server.locked_reason = self.DIGICO_MODE_MESSAGE

        self.remote_server.start()

    def disconnect(self, user_initiated=True):

        self._user_disconnected = user_initiated

        if self._reconnect_job is not None:
            self.root.after_cancel(self._reconnect_job)
            self._reconnect_job = None

        self._reconnect_at = None

        if user_initiated:
            # An explicit disconnect clears the failure notice too - the
            # operator is no longer waiting on anything, so the status
            # line should not keep explaining why the last attempt died.
            self._failure_reason = None

        self._set_status_phase("idle")

        self._stop_capture()

        if self.worker:
            # Stopping the running flag is enough - the worker's own loop
            # notices within one recv-socket timeout (<=0.1s). Nothing
            # sends "STOP" through command_queue anymore, since that queue
            # is reused across reconnects and a stray unconsumed sentinel
            # left behind by a race here would otherwise be picked up by
            # the *next* worker's loop and kill it immediately.
            self.worker.stop()
            self.worker = None

        if self.remote_server:
            self.remote_server.stop()
            self.remote_server = None

    def _schedule_reconnect(self):
        if self._reconnect_job is not None:
            return

        log("warning", f"Mixer disconnected - retrying in "
            f"{self.RECONNECT_DELAY_SECONDS}s")

        self._reconnect_at = time.monotonic() + self.RECONNECT_DELAY_SECONDS

        self._reconnect_job = self.root.after(
            self.RECONNECT_DELAY_SECONDS * 1000, self._attempt_reconnect
        )

        # Set last: _session_active() reads _reconnect_job, so the button
        # only flips to "Disconnect" once the retry is genuinely pending.
        self._set_status_phase("reconnecting")

    def _attempt_reconnect(self):
        self._reconnect_job = None
        # user_initiated=False so the failure reason survives into the
        # next attempt, ready to be shown again if that one fails too.
        self.disconnect(user_initiated=False)
        self.connect()

    def open_presets_window(self):

        if self.presets_window and self.presets_window.window.winfo_exists():
            self.presets_window.window.lift()
            return

        self.presets_window = PresetsWindow(self.root, self.preset_store)

    def open_logs_window(self):

        if self.logs_window and self.logs_window.window.winfo_exists():
            self.logs_window.window.lift()
            return

        self.logs_window = LogsWindow(self.root)

    def open_about_window(self):

        if self.about_window and self.about_window.window.winfo_exists():
            self.about_window.refresh()
            self.about_window.window.lift()
            return

        self.about_window = AboutWindow(
            self.root, self.server_details,
            initial_result=self.latest_update,
            on_result=self._apply_update_result,
        )

    def _start_update_check(self):
        """Asks GitHub once, a few seconds after launch, whether there's a
        newer release - and does nothing else with the answer but note it.

        Nothing is ever downloaded or installed without the operator
        pressing Update in the About window. This only exists so they find
        out an update exists without having to go looking.
        """
        def worker():
            self.message_queue.put(("update_check", check_for_update(VERSION)))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_update_result(self, result):
        self.latest_update = result

        if result["error"]:
            log("debug", f"Update check failed: {result['error']}")

        label = "About  •  update available" if result["available"] else "About"
        self.help_menu.entryconfigure(self.about_menu_index, label=label)

        # The About window may already be open and showing an empty status
        # line when this lands - it is opened on demand, not after the
        # check.
        if self.about_window and self.about_window.window.winfo_exists():
            self.about_window.set_result(result)

    def server_details(self):
        """Live server/mixer facts for the About window's support block.

        Handed over as a bound method (not a snapshot dict) so the window
        can re-read it whenever it's reopened or refreshed - the mixer
        connects, drops and reconnects while the app stays up.
        """
        worker = self.worker

        return {
            "remote_port": self.settings["remote_port"],
            "remote_running": self.remote_server is not None,
            "clients": self.remote_server.client_count() if self.remote_server else 0,
            "computer_ip": get_ethernet_ip(),
            "mixer_ip": self.settings["mixer_ip"],
            "mixer_connected": worker is not None and worker.is_alive() and worker.loaded,
        }

    def open_backup_window(self):

        if self.backup_window and self.backup_window.window.winfo_exists():
            self.backup_window.window.lift()
            return

        self.backup_window = BackupWindow(
            self.root, self.backup_store, self.user_store, self.preset_store,
            self.settings, self.save_settings, on_restored=self.on_backup_restored
        )

    def open_show_backup_window(self):
        if self.show_backup_window and \
                self.show_backup_window.window.winfo_exists():
            self.show_backup_window.window.lift()
            return

        self.show_backup_window = ShowBackupWindow(
            self.root, self.settings, self.save_settings,
            lambda: self.worker, self.command_queue
        )

    def on_backup_restored(self, keys):
        if "settings" in keys:
            self.reload_settings()

        if "users" in keys and self.access_panel is not None:
            self.access_panel.refresh_list()

        if "presets" in keys and \
                self.presets_window and self.presets_window.window.winfo_exists():
            self.presets_window.refresh_list()

    def reload_settings(self):
        # Mutated in place (not reassigned) so AuxPanel - which is handed
        # this same dict object rather than a getter - stays in sync too;
        # replacing self.settings outright would leave the Aux tab mutating
        # a now-orphaned dict that save_settings() no longer serializes.
        fresh = self.load_settings()
        self.settings.clear()
        self.settings.update(fresh)

        self.ip_entry.delete(0, tk.END)
        self.ip_entry.insert(0, self.settings["mixer_ip"])

        self.send_port_entry.delete(0, tk.END)
        self.send_port_entry.insert(0, self.settings["send_port"])

        self.recv_port_entry.delete(0, tk.END)
        self.recv_port_entry.insert(0, self.settings["recv_port"])

        self.remote_port_entry.delete(0, tk.END)
        self.remote_port_entry.insert(0, self.settings["remote_port"])

        # Cleared first so the restored settings win over whatever the
        # dropdowns happen to be showing.
        self.mixer_nic_combo.set("")
        self.remote_nic_combo.set("")
        self._refresh_nic_choices()

        self.theme_var.set(self.settings["theme"])
        self.apply_theme(self.settings["theme"], persist=False)

        # The Aux tab picks up the restored hidden_auxes via the rebuild
        # apply_theme() above already performs.
        self.aux_panel.refresh_aux_list()

    def _on_capture_toggled(self):
        self._set_digico_mode(self.capture_var.get())

    def _set_digico_mode(self, enabled):
        """Enter or leave DiGiCo App mode, mid-session included.

        In the mode CLMix is nothing but the bridge: the mixer view is
        covered and inert, every phone is sent away with the reason and
        refused until it ends, and the worker drops anything CLMix
        itself tries to send (see MixerWorker.bridge_only) - so nobody
        can move the desk underneath a capture, from here or a phone.
        Leaving it puts all of that back and re-reads what is on screen,
        since the app will have been changing things the whole time.
        """
        self.capture_var.set(enabled)
        self.settings["digico_capture"] = enabled
        self.save_settings()
        self._capture_error = None

        log("info", f"DiGiCo App mode {'on' if enabled else 'off'}")

        self._show_digico_overlay(enabled)

        if self.remote_server is not None:
            self.remote_server.locked_reason = \
                self.DIGICO_MODE_MESSAGE if enabled else None

        worker = self.worker
        if worker is None or not worker.is_alive():
            return

        if enabled:
            worker.enter_bridge_only()

            if worker.loaded:
                self._start_capture()
        else:
            self._stop_capture()
            worker.leave_bridge_only()

            if worker.loaded:
                # Queries level, send_on and (stereo) pan for the strips
                # on screen - the app may have moved any of them.
                self.aux_panel.on_aux_selected()

    def _build_digico_overlay(self, section):
        """The cover over the mixer view in DiGiCo App mode.

        Placed over the section rather than disabling each control: the
        strips are canvas-drawn and have no disabled state, and a cover
        cannot miss one that gets added later. Built once, shown and
        hidden with place()/place_forget().
        """
        self.digico_overlay = ttk.Frame(section, style="Section.TFrame")

        box = ttk.Frame(self.digico_overlay, style="Section.TFrame")
        box.place(relx=0.5, rely=0.45, anchor="center")

        # Plain tk.Labels, colored by hand - see configure_section_styles
        # for why a ttk.Label cannot sit on the section background.
        self.digico_overlay_labels = [
            tk.Label(box, text="DiGiCo App Mode",
                     font=("TkDefaultFont", 16, "bold")),
            tk.Label(
                box,
                text="CLMix is acting only as a bridge for the official "
                     "DiGiCo app and recording its traffic. The mixer "
                     "controls here and on phones are locked until this "
                     "mode is turned off.",
                wraplength=460, justify="center"
            ),
            tk.Label(box, text="", wraplength=460, justify="center"),
        ]
        self.digico_overlay_status = self.digico_overlay_labels[-1]

        for label, pady in zip(self.digico_overlay_labels, (0, 10, 12)):
            label.pack(pady=(pady, 0))

        self._recolor_digico_overlay()

        ttk.Button(
            box, text="Turn Off DiGiCo App Mode",
            command=lambda: self._set_digico_mode(False)
        ).pack(pady=(18, 0))

    def _recolor_digico_overlay(self):
        for label in self.digico_overlay_labels:
            label.configure(bg=section_bg(self.root), fg=panel_fg(self.root))

    def _show_digico_overlay(self, shown):
        if shown:
            self.digico_overlay.place(x=0, y=0, relwidth=1, relheight=1)
            # Above the strips, which the panel may have rebuilt since.
            self.digico_overlay.lift()
        else:
            self.digico_overlay.place_forget()

    def _start_capture(self):
        worker = self.worker

        if self.capture_bridge is not None or worker is None:
            return

        bridge = DigicoAppBridge(
            worker.mixer_ip, worker.send_port, worker.recv_port,
            mixer_bind_ip=worker.bind_ip,
            listen_ip=self.settings.get("remote_bind_ip") or None
        )

        try:
            bridge.start()
        except OSError as ex:
            log("error", f"DiGiCo App Capture could not start: {ex}")
            self._capture_error = str(ex)
            return

        self._capture_error = None
        self.capture_bridge = bridge
        worker.bridge = bridge

    def _stop_capture(self):
        # Detached from the worker first, so it stops handing datagrams
        # to a bridge that is in the middle of closing its sockets.
        if self.worker is not None:
            self.worker.bridge = None

        if self.capture_bridge is not None:
            self.capture_bridge.stop()
            self.capture_bridge = None

    def _open_capture_folder(self):
        try:
            CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as ex:
            log("error", f"Could not create {CAPTURE_DIR}: {ex}")
            return

        open_folder(CAPTURE_DIR)

    def _capture_status_text(self):
        if not self.settings.get("digico_capture"):
            return "Off."

        if self._capture_error:
            return f"Could not start: {self._capture_error}"

        bridge = self.capture_bridge

        if bridge is None:
            return "On - starts once CLMix is connected to the console."

        apps = bridge.connected_apps()
        where = f"{bridge.listen_ip or 'all adapters'}:{bridge.send_port}"
        file_name = bridge.capture_path.name

        if not apps:
            announced = ", ".join(ip for ip, _ in bridge.beacon_targets) \
                or "no adapter"
            return (f"Waiting for the DiGiCo app on {where}, announced on "
                    f"{announced}. Recording to {file_name}.")

        return (f"Relaying for {', '.join(apps)} - "
                f"{bridge.packets_from_app} datagrams from the app, "
                f"{bridge.packets_to_app} to it. Recording to {file_name}.")

    def _refresh_capture_status(self):
        text = self._capture_status_text()

        if text != self._capture_status_shown:
            self._capture_status_shown = text
            self.capture_status_label.config(text=text)
            self.digico_overlay_status.config(text=text)

    def process_messages(self):

        while not self.message_queue.empty():

            msg_type, value = self.message_queue.get()

            if msg_type == "status":
                self._apply_mixer_status(value)

            elif msg_type == "snapshot":
                number, name = value
                self.snapshot_label.config(text=f"Snapshot: {name or f'#{number}'}")

            elif msg_type == "message":
                log("debug", value)

            elif msg_type == "update_check":
                self._apply_update_result(value)

        self._refresh_capture_status()
        self._refresh_capture_view()

        self.root.after(100, self.process_messages)

    def _failure_notice(self, value):
        """Why the last attempt ended, phrased for the status line."""
        if value.startswith("Error"):
            # Carries the exception text - a bound port, a bad address -
            # which is more use than any wording of our own.
            return value

        if self._was_loaded:
            return "Connection lost"

        # Never finished loading, so the mixer never answered: either
        # nothing is at that address or it is not reachable. The worker
        # gives up on it after HEARTBEAT_TIMEOUT_SECONDS.
        return "Connection failed"

    def _apply_mixer_status(self, value):
        if value == "Connecting":
            self._set_status_phase("connecting")
        elif value == "Connected":
            # Socket is up; the boot sequence is now pulling parameters,
            # and worker.loading_stage names whichever one is in flight.
            self._set_status_phase("loading")
        elif value == "Loaded":
            self._was_loaded = True
            self._failure_reason = None
            self._set_status_phase("ready")

        if value == "Disconnected" or value.startswith("Error"):
            # First reason wins. A worker that dies on an exception
            # reports it and then still reports "Disconnected" on its way
            # out, and the generic follow-up must not overwrite the
            # specific cause the operator actually needs to see.
            if self._failure_reason is None:
                self._failure_reason = self._failure_notice(value)
            self.snapshot_label.config(text="Snapshot: --")
            self.aux_panel.on_mixer_disconnected()
            self._stop_capture()

            if self.worker is not None:
                # The worker thread exited on its own (heartbeat timeout
                # or a socket error) rather than via an explicit
                # disconnect() call - clear the stale reference so
                # connect() (guarded by "self.worker is not None") isn't
                # blocked from starting a new one.
                self.worker = None

                if self.remote_server:
                    self.remote_server.stop()
                    self.remote_server = None

            if self._user_disconnected:
                self._set_status_phase("idle")
            else:
                # Sets the phase to "reconnecting" itself, once the retry
                # is actually scheduled.
                self._schedule_reconnect()

        if value == "Loaded":
            self.aux_panel.on_mixer_loaded(self.worker)

            if self.settings.get("digico_capture"):
                self._start_capture()

    def run(self):
        self.root.mainloop()

    def on_close(self):
        self.disconnect()
        self.root.destroy()


if __name__ == "__main__":
    app = MainWindow()
    app.run()
