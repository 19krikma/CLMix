import tkinter as tk
from tkinter import ttk
import ipaddress
import threading
import queue
import re
import socket
import json
import time
from pathlib import Path

import sv_ttk
from pythonosc.osc_message import OscMessage, ParseError
from pythonosc.osc_message_builder import OscMessageBuilder

from services import updater
from services.backup_store import BackupStore
from services.log_store import log
from services.network_info import get_ethernet_ip
from services.preset_store import PresetStore
from services.remote_server import RemoteServer
from services.update_checker import check_for_update
from services.user_store import UserStore
from ui.app_icon import ICON_PNG_BASE64
from ui.about_window import AboutWindow
from ui.access_window import AccessPanel
from ui.aux_window import AuxPanel
from ui.backup_window import BackupWindow
from ui.logs_window import LogsWindow
from ui.presets_window import PresetsWindow
from version import VERSION

SETTINGS_PATH = Path.home() / ".clmix.json"

# Passed to tk.Tk(className=...) - see the comment where it is used. Tk
# capitalizes only the first letter, so the WM_CLASS this actually produces
# is "Clmix", which is the spelling packaging/linux/clmix.desktop matches.
WM_CLASS_NAME = "CLMix"

# How long after launch the update check fires. Late enough to stay out of
# the way of connecting to the mixer, which is what the operator actually
# opened the app to do.
STARTUP_UPDATE_CHECK_MS = 5000


# Addresses worth keeping in the in-memory cache, mirroring the
# webmixer Node server's maybeCacheResponse() address list.
CACHEABLE_ADDRESSES = [
    re.compile(r"^/Console/Input_Channels$"),
    re.compile(r"^/Console/Aux_Outputs/modes$"),
    re.compile(r"^/Aux_Outputs/\d+/Buss_Trim/name$"),
    re.compile(r"^/Input_Channels/\d+/Channel_Input/name$"),
    re.compile(r"^/Input_Channels/\d+/Aux_Send/\d+/send_level$"),
    re.compile(r"^/Input_Channels/\d+/Aux_Send/\d+/send_pan$"),
    re.compile(r"^/Input_Channels/\d+/Aux_Send/\d+/send_on$"),
    re.compile(r"^/Input_Channels/\d+/mute$"),
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

    def __init__(self, mixer_ip, send_port, recv_port,
                 command_queue, message_queue):
        super().__init__(daemon=True)

        self.mixer_ip = mixer_ip
        self.send_port = send_port
        self.recv_port = recv_port

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

        self._last_received_at = None
        self._last_heartbeat_sent_at = 0.0

    def run(self):
        try:
            log("info", f"Connecting to {self.mixer_ip}:{self.send_port} "
                f"(recv on port {self.recv_port})")
            self.message_queue.put(("status", "Connecting"))

            self.send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            log("debug", "Send socket created")

            self.recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.recv_sock.bind(("", self.recv_port))
            self.recv_sock.settimeout(0.1)
            log("debug", f"Recv socket bound on port {self.recv_port}")

            self.message_queue.put(("status", "Connected"))
            log("info", "Connected to mixer, loading parameters...")

            self._last_received_at = time.monotonic()
            self.request_next_parameter()

            while self.running:
                self.receive_osc()
                self._check_heartbeat()
                self._drain_commands()

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
            data, _ = self.recv_sock.recvfrom(65535)
        except socket.timeout:
            return

        try:
            message = OscMessage(data)
        except ParseError:
            return

        self._last_received_at = time.monotonic()

        address = message.address
        args = list(message.params)

        self.message_queue.put(
            ("message", f"Received: {address} {args}")
        )

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

        if not self.loaded:
            self.request_next_parameter()

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

    def request_next_parameter(self):
        if "/Console/Input_Channels" not in self.cache:
            self.send_osc("/Console/Channels/?", [])
            return

        if "/Console/Aux_Outputs/modes" not in self.cache:
            self.send_osc("/Console/Aux_Outputs/modes/?", [])
            return

        aux_modes = self.cache["/Console/Aux_Outputs/modes"]
        for i in range(1, len(aux_modes) + 1):
            address = f"/Aux_Outputs/{i}/Buss_Trim/name"
            if address not in self.cache:
                self.send_osc(f"{address}/?", [])
                return

        channel_count = int(self.cache["/Console/Input_Channels"][0])
        for i in range(1, channel_count + 1):
            address = f"/Input_Channels/{i}/Channel_Input/name"
            if address not in self.cache:
                self.send_osc(f"{address}/?", [])
                return

        if "/Snapshots/Current_Snapshot" not in self.cache:
            self.send_osc("/Snapshots/Current_Snapshot/?", [])
            return

        if self.snapshot_name is None:
            self._request_snapshot_name()
            return

        self.loaded = True
        log("info", "Mixer fully loaded and ready")
        self.message_queue.put(("status", "Loaded"))
        self.send_osc("/Layout/Layout/Banks/?", [])

    def send_osc(self, address, args):
        log("debug", f"send_osc: {address} {args}")

        builder = OscMessageBuilder(address=address)

        for arg in args:
            builder.add_arg(arg)

        self.send_sock.sendto(
            builder.build().dgram,
            (self.mixer_ip, self.send_port)
        )

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

            address = command.split(maxsplit=1)[0]
            key = (address, address.endswith("/?"))

            if key not in pending:
                order.append(key)

            pending[key] = command

        for key in order:
            self.send_command(pending[key])

    def send_command(self, command):
        parts = command.split()
        address = parts[0]
        args = [self.parse_arg(part) for part in parts[1:]]

        self.send_osc(address, args)

        self.message_queue.put(
            ("message", f"Sent: {command}")
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
    already used for tk.Scale, so call sites only needed the constructor
    swapped, plus value_at() for the press/drag handlers to map a click
    coordinate to a value directly (replacing the old cget-based
    _scale_value_at, which assumed tk.Scale's own trough geometry).
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


class AuxLevelsPanel:
    REFRESH_MS = 150
    LEVEL_EPSILON = 0.005

    # How long after the user releases a slider we keep ignoring
    # mixer-reported levels for it, so a stale/in-flight echo of an
    # earlier drag position can't snap it back and make it feel jumpy.
    DRAG_GRACE_SECONDS = 0.3

    TOP_DB = 10.0
    BOTTOM_DB = -150.0

    # Pixel length of the level slider's trough - shared with the ruler
    # drawn beside it.
    LEVEL_LENGTH = 220

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
        self.pan_sliders = {}
        self.mute_buttons = {}
        self.channel_columns = {}
        self.channel_fader_rows = {}
        self.channel_name_labels = {}
        self.channel_parity = {}
        self.suppress_send = False
        self.dragging = set()
        self.drag_released_at = {}
        self.pan_dragging = set()
        self.pan_drag_released_at = {}
        self.bank_names_shown = None

        self.build_ui()

        self.master.after(self.REFRESH_MS, self.refresh_levels)

    def build_ui(self):
        self.top_bar = ttk.Frame(self.master, padding=10)
        self.top_bar.pack(fill="x")

        ttk.Label(self.top_bar, text="Aux Bus").pack(side="left")

        self.aux_combo = ttk.Combobox(self.top_bar, values=[], state="readonly")
        self.aux_combo.pack(side="left", padx=10)
        self.aux_combo.bind("<<ComboboxSelected>>", self.on_aux_selected)

        ttk.Separator(self.top_bar, orient="vertical").pack(
            side="left", fill="y", padx=10
        )

        ttk.Label(self.top_bar, text="Bank").pack(side="left")

        self.banks_frame = ttk.Frame(self.top_bar)
        self.banks_frame.pack(side="left", padx=10)

        canvas_container = ttk.Frame(self.master)
        canvas_container.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(
            canvas_container, highlightthickness=0, bg=panel_bg(self.master)
        )
        h_scroll = ttk.Scrollbar(
            canvas_container, orient="horizontal", command=self.canvas.xview
        )
        self.canvas.configure(xscrollcommand=h_scroll.set)

        self.canvas.pack(side="top", fill="both", expand=True)
        h_scroll.pack(side="bottom", fill="x")

        self.channels_frame = ttk.Frame(self.canvas, padding=10)
        self.canvas.create_window(
            (0, 0), window=self.channels_frame, anchor="nw"
        )
        self.channels_frame.bind(
            "<Configure>",
            lambda event: self.canvas.configure(
                scrollregion=self.canvas.bbox("all")
            )
        )

    def apply_theme(self):
        self.canvas.configure(bg=panel_bg(self.master))

        fg = panel_fg(self.master)
        accent = accent_color(self.master)

        for channel, column in self.channel_columns.items():
            column_bg = self._channel_bg(self.channel_parity.get(channel, False))
            column.configure(bg=column_bg)

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
                muted = button.cget("text") == "Muted"
                bg, btn_fg = self._mute_button_colors(channel, muted)
                button.configure(
                    bg=bg, fg=btn_fg, activebackground=bg, activeforeground=btn_fg
                )

    def on_mixer_loaded(self, worker):
        self.worker = worker

        channel_count = int(worker.cache["/Console/Input_Channels"][0])
        self.all_channels = list(range(1, channel_count + 1))
        self.channels = self.all_channels
        self.aux_list = build_aux_list(worker, hidden=self.get_hidden_auxes())
        self.bank_names_shown = None

        self.aux_combo.configure(values=[name for _, name in self.aux_list])

        self.build_bank_buttons()
        self.build_channel_widgets()
        self.request_mute_states()

        if self.aux_list:
            self.aux_combo.current(0)
            self.on_aux_selected()

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

        self.aux_combo.configure(values=[])
        self.aux_combo.set("")

        for child in self.channels_frame.winfo_children():
            child.destroy()

        for child in self.banks_frame.winfo_children():
            child.destroy()

        self.sliders = {}
        self.level_rulers = {}
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

        ttk.Button(
            self.banks_frame,
            text="All",
            command=self.show_all_channels
        ).pack(side="left", padx=2)

        for name in bank_names:
            ttk.Button(
                self.banks_frame,
                text=name,
                command=lambda n=name: self.select_bank(n)
            ).pack(side="left", padx=2)

    def show_all_channels(self):
        self.set_channels(self.all_channels)

    def select_bank(self, bank_name):
        channels = self.worker.banks.get(bank_name)

        if not channels:
            return

        self.set_channels(channels)

    def set_channels(self, channels):
        if channels == self.channels:
            return

        self.channels = channels

        for child in self.channels_frame.winfo_children():
            child.destroy()

        self.sliders = {}
        self.level_rulers = {}
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
        self.request_mute_states()

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
            column = tk.Frame(self.channels_frame, bg=column_bg)
            column.pack(side="left", padx=4, fill="y")
            self.channel_columns[i] = column

            name_label = tk.Label(column, text=name, bg=column_bg, fg=fg)
            name_label.pack()
            self.channel_name_labels[i] = name_label

            # A row (not the slider alone) so the ruler and the slider
            # share the same top edge - and therefore line up - regardless
            # of the name label's height above them.
            fader_row = tk.Frame(column, bg=column_bg)
            fader_row.pack()
            self.channel_fader_rows[i] = fader_row

            ruler = tk.Canvas(
                fader_row, width=self.LEVEL_RULER_WIDTH, height=self.LEVEL_LENGTH,
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
                length=self.LEVEL_LENGTH,
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

            pan_slider = RoundSlider(
                column,
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
            pan_slider.set(0.0)
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
            pan_slider.pack(pady=(6, 0))

            self.pan_sliders[i] = pan_slider

            mute_bg, mute_fg = self._mute_button_colors(i, muted=False)
            mute_btn = tk.Button(
                column,
                text="Mute",
                width=6,
                bd=0,
                highlightthickness=0,
                bg=mute_bg,
                fg=mute_fg,
                activebackground=mute_bg,
                activeforeground=mute_fg,
                command=lambda channel=i: self.on_mute_toggle(channel)
            )
            mute_btn.pack(pady=(4, 0))

            self.mute_buttons[i] = mute_btn

    def _channel_bg(self, is_alt):
        return stripe_bg(self.master) if is_alt else panel_bg(self.master)

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
        span = self.LEVEL_LENGTH - 2 * self.LEVEL_TICK_INSET

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

    def current_aux(self):
        index = self.aux_combo.current()

        if index < 0:
            return None

        return self.aux_list[index][0]

    def on_aux_selected(self, event=None):
        aux = self.current_aux()

        if aux is None or self.worker is None or not self.worker.is_alive():
            return

        for channel in self.channels:
            self.command_queue.put(
                f"/Input_Channels/{channel}/Aux_Send/{aux}/send_level/?"
            )
            self.command_queue.put(
                f"/Input_Channels/{channel}/Aux_Send/{aux}/send_pan/?"
            )

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

    def request_mute_states(self):
        if self.worker is None or not self.worker.is_alive():
            return

        for channel in self.channels:
            self.command_queue.put(f"/Input_Channels/{channel}/mute/?")

    def on_mute_toggle(self, channel):
        if self.worker is None or not self.worker.is_alive():
            return

        key = f"/Input_Channels/{channel}/mute"
        currently_muted = bool(self.worker.cache.get(key, [0.0])[0])
        new_state = 0.0 if currently_muted else 1.0

        self.command_queue.put(f"{key} {new_state}")

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
                key = f"/Input_Channels/{channel}/mute"

                if key not in self.worker.cache:
                    continue

                muted = bool(self.worker.cache[key][0])
                bg, fg = self._mute_button_colors(channel, muted)
                button.config(
                    text="Muted" if muted else "Mute",
                    bg=bg, fg=fg, activebackground=bg, activeforeground=fg
                )

            self.build_bank_buttons()

        self.master.after(self.REFRESH_MS, self.refresh_levels)


class MainWindow:

    # How long to wait before automatically retrying a connection that
    # dropped on its own (mixer power loss, network blip, ...) rather
    # than one the user explicitly disconnected.
    RECONNECT_DELAY_SECONDS = 5

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
        self.root.geometry("800x500")

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
        self.logs_window = None
        self.presets_window = None
        self.remote_server = None

        self._user_disconnected = True
        self._reconnect_job = None

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

        self.apply_theme(self.settings["theme"], persist=False)
        self.build_ui()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # Now, not during the update itself: this is the one moment the
        # installer downloaded last time is guaranteed not to be running,
        # since the app it installed is the one doing the clearing.
        updater.sweep_download_dir()

        self.root.after(100, self.process_messages)
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
        self.help_menu.add_command(label="Backup", command=self.open_backup_window)
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

        self.status_label = ttk.Label(
            mixer_row, text="Disconnected", font=("TkDefaultFont", 10, "bold")
        )
        self.status_label.pack(side="left", padx=(6, 0))

        snapshot_row = ttk.Frame(status_frame)
        snapshot_row.pack(side="top", anchor="w", pady=(4, 0))

        self.snapshot_label = ttk.Label(snapshot_row, text="Snapshot: --")
        self.snapshot_label.pack(side="left")

        ttk.Separator(self.root, orient="horizontal").pack(fill="x")

        frame = ttk.Frame(self.root, padding=15)
        frame.pack(fill="both", expand=True)

        self.aux_panel = AuxLevelsPanel(
            frame, self.command_queue, get_hidden_auxes=self.get_hidden_auxes
        )

        self.build_setup_window()

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
        self.setup_window.geometry("720x470")
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
        frame.pack(fill="both", expand=True)

        port_vcmd = (self.setup_window.register(self._validate_port_input), "%P")

        ttk.Label(frame, text="Mixer IP Address").grid(
            row=0, column=0, sticky="w"
        )

        self.ip_entry = ttk.Entry(frame, width=25)
        self.ip_entry.insert(0, self.settings["mixer_ip"])
        self.ip_entry.grid(row=0, column=1, padx=5, pady=5)

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
            "theme": "dark",
            "hidden_auxes": [],
            "backup_dir": None,
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

        bg = panel_bg(self.root)
        self.root.configure(bg=bg)

        if getattr(self, "indicator", None) is not None:
            self.indicator.configure(bg=bg)

        if getattr(self, "aux_panel", None) is not None:
            self.aux_panel.apply_theme()

        if getattr(self, "aux_visibility_panel", None) is not None:
            self.aux_visibility_panel.rebuild()

        if getattr(self, "about_window", None) is not None and \
                self.about_window.window.winfo_exists():
            self.about_window.apply_theme()

        if persist:
            self.settings["theme"] = theme
            self.save_settings()

    def on_connect_button(self):
        if self.worker is not None:
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

        self.settings.update({
            "mixer_ip": mixer_ip,
            "send_port": send_port,
            "recv_port": recv_port,
            "remote_port": remote_port,
        })
        self.save_settings()

        self._user_disconnected = False

        if self._reconnect_job is not None:
            self.root.after_cancel(self._reconnect_job)
            self._reconnect_job = None

        self.connect_btn.config(text="Disconnect")

        self.worker = MixerWorker(
            mixer_ip,
            int(send_port),
            int(recv_port),
            self.command_queue,
            self.message_queue
        )

        self.worker.start()
        log("debug", "Worker thread started")

        self.remote_server = RemoteServer(
            lambda: self.worker, self.command_queue, int(remote_port),
            self.user_store, self.preset_store,
            get_hidden_auxes=self.get_hidden_auxes
        )
        self.remote_server.start()

    def disconnect(self, user_initiated=True):

        self._user_disconnected = user_initiated

        if self._reconnect_job is not None:
            self.root.after_cancel(self._reconnect_job)
            self._reconnect_job = None

        self.connect_btn.config(text="Connect")

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

        self._reconnect_job = self.root.after(
            self.RECONNECT_DELAY_SECONDS * 1000, self._attempt_reconnect
        )

    def _attempt_reconnect(self):
        self._reconnect_job = None
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

        self.theme_var.set(self.settings["theme"])
        self.apply_theme(self.settings["theme"], persist=False)

        # The Aux tab picks up the restored hidden_auxes via the rebuild
        # apply_theme() above already performs.
        self.aux_panel.refresh_aux_list()

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

        self.root.after(100, self.process_messages)

    def _apply_mixer_status(self, value):
        if value in ("Connecting", "Connected"):
            text, color = "Loading", "orange"
        elif value == "Loaded":
            text, color = "Connected", "green"
        elif value == "Disconnected":
            text, color = "Disconnected", "red"
        else:
            text, color = value, "red"

        self.status_label.config(text=text)
        self.indicator.itemconfig(self.light, fill=color)

        if value == "Disconnected" or value.startswith("Error"):
            self.connect_btn.config(text="Connect")
            self.snapshot_label.config(text="Snapshot: --")
            self.aux_panel.on_mixer_disconnected()

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

            if not self._user_disconnected:
                self._schedule_reconnect()
        else:
            self.connect_btn.config(text="Disconnect")

        if value == "Loaded":
            self.aux_panel.on_mixer_loaded(self.worker)

    def run(self):
        self.root.mainloop()

    def on_close(self):
        self.disconnect()
        self.root.destroy()


if __name__ == "__main__":
    app = MainWindow()
    app.run()
