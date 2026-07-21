import tkinter as tk
from tkinter import ttk
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

from services.log_store import log
from services.remote_server import RemoteServer
from services.user_store import UserStore
from ui.access_window import AccessWindow
from ui.logs_window import LogsWindow

SETTINGS_PATH = Path.home() / ".digico_monitor_mix.json"


# Addresses worth keeping in the in-memory cache, mirroring the
# webmixer Node server's maybeCacheResponse() address list.
CACHEABLE_ADDRESSES = [
    re.compile(r"^/Console/Input_Channels$"),
    re.compile(r"^/Console/Aux_Outputs/modes$"),
    re.compile(r"^/Aux_Outputs/\d+/Buss_Trim/name$"),
    re.compile(r"^/Input_Channels/\d+/Channel_Input/name$"),
    re.compile(r"^/Input_Channels/\d+/Aux_Send/\d+/send_level$"),
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

            self.request_next_parameter()

            while self.running:
                self.receive_osc()

                try:
                    command = self.command_queue.get_nowait()

                    if command == "STOP":
                        log("debug", "STOP command received")
                        break

                    self.send_command(command)

                except queue.Empty:
                    pass

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


def build_aux_list(worker):
    aux_modes = worker.cache.get("/Console/Aux_Outputs/modes", [])
    aux_list = []

    for i in range(1, len(aux_modes) + 1):
        name_key = f"/Aux_Outputs/{i}/Buss_Trim/name"
        name = worker.cache[name_key][0] \
            if name_key in worker.cache else f"Aux {i}"
        aux_list.append((i, name))

    return aux_list


def panel_bg(widget):
    # sv_ttk themes ttk widgets automatically, but plain tk widgets
    # (Canvas, Scale) need their background matched by hand so they
    # don't show up as a mismatched gray/white square against the theme.
    # sv_ttk exposes its palette only as Tcl array variables, not
    # through the standard ttk::style lookup mechanism.
    name = "sv_dark" if sv_ttk.get_theme(widget.winfo_toplevel()) == "dark" else "sv_light"
    return widget.tk.eval(f"set ttk::theme::{name}::colors(-bg)")


class AuxLevelsPanel:
    REFRESH_MS = 150
    LEVEL_EPSILON = 0.005

    # How long after the user releases a slider we keep ignoring
    # mixer-reported levels for it, so a stale/in-flight echo of an
    # earlier drag position can't snap it back and make it feel jumpy.
    DRAG_GRACE_SECONDS = 0.3

    # The physical fader only travels smoothly between TOP_DB and MID_DB;
    # below that it drops almost immediately to BOTTOM_DB. FRACTION_MID
    # is the fraction (from the bottom) where that transition happens:
    # above it is the TOP_DB..MID_DB range (90% of the travel), below it
    # is the steep MID_DB..BOTTOM_DB tail near the very bottom (10%).
    TOP_DB = 10.0
    MID_DB = -60.0
    BOTTOM_DB = -150.0
    FRACTION_MID = 0.1

    @classmethod
    def _fraction_to_db(cls, fraction):
        if fraction >= cls.FRACTION_MID:
            t = (fraction - cls.FRACTION_MID) / (1 - cls.FRACTION_MID)
            return cls.MID_DB + t * (cls.TOP_DB - cls.MID_DB)

        t = fraction / cls.FRACTION_MID
        return cls.BOTTOM_DB + t * (cls.MID_DB - cls.BOTTOM_DB)

    @classmethod
    def _db_to_fraction(cls, db):
        if db >= cls.MID_DB:
            t = (db - cls.MID_DB) / (cls.TOP_DB - cls.MID_DB)
            return cls.FRACTION_MID + t * (1 - cls.FRACTION_MID)

        t = (db - cls.BOTTOM_DB) / (cls.MID_DB - cls.BOTTOM_DB)
        return max(0.0, t * cls.FRACTION_MID)

    def __init__(self, master, command_queue):
        self.master = master
        self.command_queue = command_queue

        self.worker = None
        self.all_channels = []
        self.channels = []
        self.aux_list = []

        self.sliders = {}
        self.mute_buttons = {}
        self.mute_btn_default_bg = None
        self.suppress_send = False
        self.dragging = set()
        self.drag_released_at = {}
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

    def on_mixer_loaded(self, worker):
        self.worker = worker

        channel_count = int(worker.cache["/Console/Input_Channels"][0])
        self.all_channels = list(range(1, channel_count + 1))
        self.channels = self.all_channels
        self.aux_list = build_aux_list(worker)
        self.bank_names_shown = None

        self.aux_combo.configure(values=[name for _, name in self.aux_list])

        self.build_bank_buttons()
        self.build_channel_widgets()
        self.request_mute_states()

        if self.aux_list:
            self.aux_combo.current(0)
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
        self.mute_buttons = {}
        self.dragging = set()
        self.drag_released_at = {}

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
        self.mute_buttons = {}
        self.dragging = set()
        self.drag_released_at = {}

        self.build_channel_widgets()
        self.on_aux_selected()
        self.request_mute_states()

    def build_channel_widgets(self):
        for i in self.channels:
            name_key = f"/Input_Channels/{i}/Channel_Input/name"
            name = self.worker.cache[name_key][0] \
                if name_key in self.worker.cache else f"Ch {i}"

            column = ttk.Frame(self.channels_frame)
            column.pack(side="left", padx=4, fill="y")

            ttk.Label(column, text=name).pack()

            slider = ttk.Scale(
                column,
                from_=1.0,
                to=0.0,
                orient="vertical",
                length=220,
                command=lambda value, channel=i:
                    self.on_slider_change(channel, value)
            )
            slider.bind(
                "<ButtonPress-1>",
                lambda event, channel=i: self.dragging.add(channel)
            )
            slider.bind(
                "<ButtonRelease-1>",
                lambda event, channel=i: self.on_slider_release(channel)
            )
            slider.pack()

            self.sliders[i] = slider

            mute_btn = ttk.Button(
                column,
                text="Mute",
                width=6,
                command=lambda channel=i: self.on_mute_toggle(channel)
            )
            mute_btn.pack(pady=(4, 0))

            self.mute_buttons[i] = mute_btn

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

            for channel, button in self.mute_buttons.items():
                key = f"/Input_Channels/{channel}/mute"

                if key not in self.worker.cache:
                    continue

                muted = bool(self.worker.cache[key][0])
                button.config(
                    text="Muted" if muted else "Mute",
                    style="Muted.TButton" if muted else "TButton"
                )

            self.build_bank_buttons()

        self.master.after(self.REFRESH_MS, self.refresh_levels)


class MainWindow:

    def __init__(self):

        self.root = tk.Tk()
        self.root.title("Mixer Controller")
        self.root.geometry("800x500")

        self.worker = None
        self.access_window = None
        self.logs_window = None
        self.remote_server = None

        self.command_queue = queue.Queue()
        self.message_queue = queue.Queue()

        self.settings = self.load_settings()
        self.user_store = UserStore()

        self.apply_theme(self.settings["theme"], persist=False)
        self.build_ui()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.root.after(100, self.process_messages)

    def build_ui(self):

        top_bar = ttk.Frame(self.root, padding=(15, 10))
        top_bar.pack(fill="x")

        self.connect_btn = ttk.Button(
            top_bar,
            text="Connect",
            command=self.on_connect_button
        )
        self.connect_btn.pack(side="left")

        self.setup_btn = ttk.Button(
            top_bar,
            text="Setup",
            command=self.open_setup_window
        )
        self.setup_btn.pack(side="left", padx=(10, 0))

        self.access_btn = ttk.Button(
            top_bar,
            text="Access",
            command=self.open_access_window
        )
        self.access_btn.pack(side="left", padx=(10, 0))

        self.logs_btn = ttk.Button(
            top_bar,
            text="Logs",
            command=self.open_logs_window
        )
        self.logs_btn.pack(side="left", padx=(10, 0))

        ttk.Separator(top_bar, orient="vertical").pack(
            side="left", fill="y", padx=15
        )

        ttk.Label(top_bar, text="Mixer:").pack(side="left", padx=(0, 6))

        self.indicator = tk.Canvas(
            top_bar, width=16, height=16, highlightthickness=0,
            bg=panel_bg(top_bar)
        )
        self.indicator.pack(side="left")
        self.light = self.indicator.create_oval(2, 2, 14, 14, fill="red")

        self.status_label = ttk.Label(
            top_bar, text="Disconnected", font=("TkDefaultFont", 10, "bold")
        )
        self.status_label.pack(side="left", padx=(6, 24))

        self.server_indicator = tk.Canvas(
            top_bar, width=16, height=16, highlightthickness=0,
            bg=panel_bg(top_bar)
        )
        self.server_indicator.pack(side="left")
        self.server_light = self.server_indicator.create_oval(
            2, 2, 14, 14, fill="red"
        )

        self.server_status_label = ttk.Label(top_bar, text="Server: Stopped")
        self.server_status_label.pack(side="left", padx=6)

        self.snapshot_label = ttk.Label(top_bar, text="Snapshot: --")
        self.snapshot_label.pack(side="right")

        ttk.Separator(self.root, orient="horizontal").pack(fill="x")

        frame = ttk.Frame(self.root, padding=15)
        frame.pack(fill="both", expand=True)

        self.aux_panel = AuxLevelsPanel(frame, self.command_queue)

        self.build_setup_window()

    def build_setup_window(self):
        self.setup_window = tk.Toplevel(self.root)
        self.setup_window.title("Setup")
        self.setup_window.protocol("WM_DELETE_WINDOW", self.close_setup_window)

        frame = ttk.Frame(self.setup_window, padding=15)
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

        self.recv_port_entry = ttk.Entry(frame, width=15)
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

        ttk.Label(frame, text="Theme").grid(
            row=4, column=0, sticky="w", pady=(10, 0)
        )

        self.theme_combo = ttk.Combobox(
            frame, width=13, state="readonly", values=["Light", "Dark"]
        )
        self.theme_combo.set(self.settings["theme"].capitalize())
        self.theme_combo.grid(row=4, column=1, padx=5, pady=(10, 5), sticky="w")
        self.theme_combo.bind("<<ComboboxSelected>>", self.on_theme_selected)

        self.setup_window.withdraw()

    def on_theme_selected(self, event=None):
        self.apply_theme(self.theme_combo.get().lower())

    def open_setup_window(self):
        self.setup_window.deiconify()
        self.setup_window.lift()

    def close_setup_window(self):
        self.setup_window.withdraw()

    def _validate_port_input(self, proposed):
        return proposed == "" or proposed.isdigit()

    @staticmethod
    def load_settings():
        defaults = {
            "mixer_ip": "192.168.1.100",
            "send_port": "10023",
            "recv_port": "10024",
            "remote_port": "8765",
            "theme": "dark",
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

    def apply_theme(self, theme, persist=True):
        sv_ttk.set_theme(theme)

        style = ttk.Style()
        style.configure("Muted.TButton", background="#c0392b", foreground="white")
        style.map(
            "Muted.TButton",
            background=[("active", "#e74c3c")],
            foreground=[("active", "white")]
        )

        bg = panel_bg(self.root)
        self.root.configure(bg=bg)

        for canvas in (getattr(self, "indicator", None), getattr(self, "server_indicator", None)):
            if canvas is not None:
                canvas.configure(bg=bg)

        if getattr(self, "aux_panel", None) is not None:
            self.aux_panel.apply_theme()

        if persist:
            self.settings["theme"] = theme
            self.save_settings()

    def on_connect_button(self):
        if self.worker and self.worker.is_alive():
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        log("debug", "Connect button pressed")

        if self.worker and self.worker.is_alive():
            log("debug", "Worker already running, ignoring")
            return

        send_port = self.send_port_entry.get()

        if len(send_port) < 3:
            log("error", f"Invalid send port: {send_port!r}")
            self.status_label.config(text="Invalid send port")
            return

        remote_port = self.remote_port_entry.get()

        if len(remote_port) < 2:
            log("error", f"Invalid remote port: {remote_port!r}")
            self.status_label.config(text="Invalid remote port")
            return

        mixer_ip = self.ip_entry.get()
        recv_port = self.recv_port_entry.get()
        log("debug", f"mixer_ip={mixer_ip!r} send_port={send_port!r} "
            f"recv_port={recv_port!r} remote_port={remote_port!r}")

        self.settings.update({
            "mixer_ip": mixer_ip,
            "send_port": send_port,
            "recv_port": recv_port,
            "remote_port": remote_port,
        })
        self.save_settings()

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
            self.user_store, self.message_queue
        )
        self.remote_server.start()

    def disconnect(self):

        self.connect_btn.config(text="Connect")

        if self.worker:

            self.command_queue.put("STOP")
            self.worker.stop()

        if self.remote_server:
            self.remote_server.stop()
            self.remote_server = None

    def open_access_window(self):

        if self.access_window and self.access_window.window.winfo_exists():
            self.access_window.window.lift()
            return

        self.access_window = AccessWindow(
            self.root, self.user_store, lambda: self.worker
        )

    def open_logs_window(self):

        if self.logs_window and self.logs_window.window.winfo_exists():
            self.logs_window.window.lift()
            return

        self.logs_window = LogsWindow(self.root)

    def process_messages(self):

        while not self.message_queue.empty():

            msg_type, value = self.message_queue.get()

            if msg_type == "status":
                self._apply_mixer_status(value)

            elif msg_type == "server_status":
                self._apply_server_status(value)

            elif msg_type == "snapshot":
                number, name = value
                self.snapshot_label.config(text=f"Snapshot: {name or f'#{number}'}")

            elif msg_type == "message":
                log("debug", value)

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
        else:
            self.connect_btn.config(text="Disconnect")

        if value == "Loaded":
            self.aux_panel.on_mixer_loaded(self.worker)

    def _apply_server_status(self, value):
        color = "green" if value == "Ready" else "red"
        self.server_status_label.config(text=f"Server: {value}")
        self.server_indicator.itemconfig(self.server_light, fill=color)

    def run(self):
        self.root.mainloop()

    def on_close(self):
        self.disconnect()
        self.root.destroy()


if __name__ == "__main__":
    app = MainWindow()
    app.run()
