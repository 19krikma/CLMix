import tkinter as tk
from tkinter import ttk
import threading
import queue
import re
import socket
import json
import time
from pathlib import Path

from pythonosc.osc_message import OscMessage, ParseError
from pythonosc.osc_message_builder import OscMessageBuilder

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
    re.compile(r"^/Snapshots/Current_Snapshot$"),
    re.compile(r"^/Snapshots/name$"),
]


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

    def run(self):
        try:
            print(f"[worker] Connecting to {self.mixer_ip}:{self.send_port} "
                  f"(recv on port {self.recv_port})")
            self.message_queue.put(("status", "Connecting"))

            self.send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            print("[worker] Send socket created")

            self.recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.recv_sock.bind(("", self.recv_port))
            self.recv_sock.settimeout(0.1)
            print(f"[worker] Recv socket bound on port {self.recv_port}")

            self.message_queue.put(("status", "Connected"))
            print("[worker] Status: Connected")

            self.request_next_parameter()

            while self.running:
                self.receive_osc()

                try:
                    command = self.command_queue.get_nowait()

                    if command == "STOP":
                        print("[worker] STOP command received")
                        break

                    self.send_command(command)

                except queue.Empty:
                    pass

        except Exception as ex:
            print(f"[worker] Error: {ex!r}")
            self.message_queue.put(("status", f"Error: {ex}"))

        finally:
            if self.send_sock:
                self.send_sock.close()

            if self.recv_sock:
                self.recv_sock.close()

            print("[worker] Status: Disconnected")
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

        if address == "/Layout/Layout/Banks":
            self._store_bank(args)
        elif any(pattern.match(address) for pattern in CACHEABLE_ADDRESSES):
            self.cache[address] = args

        if not self.loaded:
            self.request_next_parameter()

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

        self.loaded = True
        self.message_queue.put(("status", "Loaded"))
        self.send_osc("/Layout/Layout/Banks/?", [])

    def send_osc(self, address, args):
        print(f"[worker] send_osc: {address} {args}")

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


class AuxLevelTestWindow:
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

    def __init__(self, master, worker, command_queue):
        self.worker = worker
        self.command_queue = command_queue

        channel_count = int(worker.cache["/Console/Input_Channels"][0])
        self.all_channels = list(range(1, channel_count + 1))
        self.channels = self.all_channels
        self.aux_list = self._build_aux_list()

        self.sliders = {}
        self.mute_buttons = {}
        self.suppress_send = False
        self.refresh_job = None
        self.dragging = set()
        self.drag_released_at = {}
        self.bank_names_shown = None

        self.window = tk.Toplevel(master)
        self.window.title("Aux Send Levels")
        self.window.geometry("600x400")
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        self.build_ui()
        self.request_mute_states()

        self.refresh_job = self.window.after(self.REFRESH_MS, self.refresh_levels)

    def _build_aux_list(self):
        aux_modes = self.worker.cache.get("/Console/Aux_Outputs/modes", [])
        aux_list = []

        for i in range(1, len(aux_modes) + 1):
            name_key = f"/Aux_Outputs/{i}/Buss_Trim/name"
            name = self.worker.cache[name_key][0] \
                if name_key in self.worker.cache else f"Aux {i}"
            aux_list.append((i, name))

        return aux_list

    def build_ui(self):
        self.top_bar = ttk.Frame(self.window, padding=10)
        self.top_bar.pack(fill="x")

        ttk.Label(self.top_bar, text="Aux Bus").pack(side="left")

        self.aux_combo = ttk.Combobox(
            self.top_bar,
            values=[name for _, name in self.aux_list],
            state="readonly"
        )
        self.aux_combo.pack(side="left", padx=10)
        self.aux_combo.bind("<<ComboboxSelected>>", self.on_aux_selected)

        ttk.Separator(self.top_bar, orient="vertical").pack(
            side="left", fill="y", padx=10
        )

        ttk.Label(self.top_bar, text="Bank").pack(side="left")

        self.banks_frame = ttk.Frame(self.top_bar)
        self.banks_frame.pack(side="left", padx=10)

        canvas_container = ttk.Frame(self.window)
        canvas_container.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(canvas_container, highlightthickness=0)
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

        self.build_bank_buttons()
        self.build_channel_widgets()
        self._fit_window_size()

        if self.aux_list:
            self.aux_combo.current(0)
            self.on_aux_selected()

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
        self._fit_window_size()
        self.on_aux_selected()
        self.request_mute_states()

    def _fit_window_size(self):
        self.window.update_idletasks()

        content_width = self.channels_frame.winfo_reqwidth() + 40
        content_height = (
            self.top_bar.winfo_reqheight()
            + self.channels_frame.winfo_reqheight()
            + 60
        )

        max_width = self.window.winfo_screenwidth() - 80
        max_height = self.window.winfo_screenheight() - 120

        width = max(400, min(content_width, max_width))
        height = max(300, min(content_height, max_height))

        self.window.geometry(f"{width}x{height}")

    def build_channel_widgets(self):
        for i in self.channels:
            name_key = f"/Input_Channels/{i}/Channel_Input/name"
            name = self.worker.cache[name_key][0] \
                if name_key in self.worker.cache else f"Ch {i}"

            column = ttk.Frame(self.channels_frame)
            column.pack(side="left", padx=4, fill="y")

            ttk.Label(column, text=name).pack()

            slider = tk.Scale(
                column,
                from_=1.0,
                to=0.0,
                resolution=0.001,
                orient="vertical",
                length=220,
                showvalue=False,
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

            mute_btn = tk.Button(
                column,
                text="Mute",
                width=6,
                command=lambda channel=i: self.on_mute_toggle(channel)
            )
            mute_btn.pack(pady=(4, 0))

            self.mute_buttons[i] = mute_btn
            self.mute_btn_default_bg = mute_btn.cget("background")

    def current_aux(self):
        index = self.aux_combo.current()

        if index < 0:
            return None

        return self.aux_list[index][0]

    def on_aux_selected(self, event=None):
        aux = self.current_aux()

        if aux is None or not self.worker.is_alive():
            return

        for channel in self.channels:
            self.command_queue.put(
                f"/Input_Channels/{channel}/Aux_Send/{aux}/send_level/?"
            )

    def on_slider_change(self, channel, value):
        if self.suppress_send:
            return

        aux = self.current_aux()

        if aux is None or not self.worker.is_alive():
            return

        db = round(self._fraction_to_db(float(value)), 2)

        self.command_queue.put(
            f"/Input_Channels/{channel}/Aux_Send/{aux}/send_level {db}"
        )

    def on_slider_release(self, channel):
        self.dragging.discard(channel)
        self.drag_released_at[channel] = time.monotonic()

    def request_mute_states(self):
        if not self.worker.is_alive():
            return

        for channel in self.channels:
            self.command_queue.put(f"/Input_Channels/{channel}/mute/?")

    def on_mute_toggle(self, channel):
        if not self.worker.is_alive():
            return

        key = f"/Input_Channels/{channel}/mute"
        currently_muted = bool(self.worker.cache.get(key, [0.0])[0])
        new_state = 0.0 if currently_muted else 1.0

        self.command_queue.put(f"{key} {new_state}")

    def refresh_levels(self):
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
                bg="red" if muted else self.mute_btn_default_bg
            )

        self.build_bank_buttons()

        self.refresh_job = self.window.after(self.REFRESH_MS, self.refresh_levels)

    def close(self):
        if self.refresh_job:
            self.window.after_cancel(self.refresh_job)

        self.window.destroy()


class MainWindow:

    def __init__(self):

        self.root = tk.Tk()
        self.root.title("Mixer Controller")
        self.root.geometry("550x250")

        self.worker = None
        self.test_window = None

        self.command_queue = queue.Queue()
        self.message_queue = queue.Queue()

        self.settings = self.load_settings()

        self.build_ui()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.root.after(100, self.process_messages)

    def build_ui(self):

        frame = ttk.Frame(self.root, padding=15)
        frame.pack(fill="both", expand=True)

        port_vcmd = (self.root.register(self._validate_port_input), "%P")

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

        self.connect_btn = ttk.Button(
            frame,
            text="Connect",
            command=self.connect
        )

        self.connect_btn.grid(
            row=3,
            column=0,
            pady=15
        )

        self.cancel_btn = ttk.Button(
            frame,
            text="Cancel",
            command=self.disconnect
        )

        self.cancel_btn.grid(
            row=3,
            column=1,
            sticky="w"
        )

        self.indicator = tk.Canvas(
            frame,
            width=20,
            height=20,
            highlightthickness=0
        )

        self.indicator.grid(
            row=3,
            column=2,
            padx=10
        )

        self.light = self.indicator.create_oval(
            2, 2, 18, 18,
            fill="red"
        )

        self.status_label = ttk.Label(
            frame,
            text="Disconnected"
        )

        self.status_label.grid(
            row=3,
            column=3,
            sticky="w"
        )

        self.test_btn = ttk.Button(
            frame,
            text="Test Aux Levels",
            command=self.open_test_window
        )

        self.test_btn.grid(
            row=4,
            column=0,
            columnspan=2,
            pady=10
        )

    def _validate_port_input(self, proposed):
        return proposed == "" or proposed.isdigit()

    @staticmethod
    def load_settings():
        defaults = {
            "mixer_ip": "192.168.1.100",
            "send_port": "10023",
            "recv_port": "10024",
        }

        try:
            with open(SETTINGS_PATH) as f:
                defaults.update(json.load(f))
        except (OSError, json.JSONDecodeError):
            pass

        return defaults

    def save_settings(self, mixer_ip, send_port, recv_port):
        try:
            with open(SETTINGS_PATH, "w") as f:
                json.dump({
                    "mixer_ip": mixer_ip,
                    "send_port": send_port,
                    "recv_port": recv_port,
                }, f)
        except OSError as ex:
            print(f"[settings] Failed to save settings: {ex!r}")

    def connect(self):
        print("[connect] Connect button pressed")

        if self.worker and self.worker.is_alive():
            print("[connect] Worker already running, ignoring")
            return

        send_port = self.send_port_entry.get()

        if len(send_port) < 3:
            print(f"[connect] Invalid send port: {send_port!r}")
            self.status_label.config(text="Invalid send port")
            return

        mixer_ip = self.ip_entry.get()
        recv_port = self.recv_port_entry.get()
        print(f"[connect] mixer_ip={mixer_ip!r} send_port={send_port!r} recv_port={recv_port!r}")

        self.save_settings(mixer_ip, send_port, recv_port)

        self.worker = MixerWorker(
            mixer_ip,
            int(send_port),
            int(recv_port),
            self.command_queue,
            self.message_queue
        )

        self.worker.start()
        print("[connect] Worker thread started")

    def disconnect(self):

        if self.worker:

            self.command_queue.put("STOP")
            self.worker.stop()

    def open_test_window(self):

        if not (self.worker and self.worker.is_alive() and self.worker.loaded):
            self.status_label.config(text="Wait for mixer to finish loading")
            return

        if self.test_window and self.test_window.window.winfo_exists():
            self.test_window.window.lift()
            return

        self.test_window = AuxLevelTestWindow(
            self.root, self.worker, self.command_queue
        )

    def process_messages(self):

        while not self.message_queue.empty():

            msg_type, value = self.message_queue.get()

            if msg_type == "status":

                self.status_label.config(text=value)

                if value in ("Connected", "Loaded"):
                    self.indicator.itemconfig(
                        self.light,
                        fill="green"
                    )
                else:
                    self.indicator.itemconfig(
                        self.light,
                        fill="red"
                    )

            elif msg_type == "message":
                print(value)

        self.root.after(100, self.process_messages)

    def run(self):
        self.root.mainloop()

    def on_close(self):
        self.disconnect()
        self.root.destroy()


if __name__ == "__main__":
    app = MainWindow()
    app.run()
