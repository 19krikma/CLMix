import tkinter as tk
from tkinter import ttk
import threading
import queue
import re
import socket

from pythonosc.osc_message import OscMessage, ParseError
from pythonosc.osc_message_builder import OscMessageBuilder


# Addresses worth keeping in the in-memory cache, mirroring the
# webmixer Node server's maybeCacheResponse() address list.
CACHEABLE_ADDRESSES = [
    re.compile(r"^/Console/Input_Channels$"),
    re.compile(r"^/Console/Aux_Outputs/modes$"),
    re.compile(r"^/Aux_Outputs/\d+/Buss_Trim/name$"),
    re.compile(r"^/Input_Channels/\d+/Channel_Input/name$"),
    re.compile(r"^/Input_Channels/\d+/Aux_Send/\d+/send_level$"),
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
        self.loaded = False

    def run(self):
        try:
            self.message_queue.put(("status", "Connecting"))

            self.send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

            self.recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.recv_sock.bind(("", self.recv_port))
            self.recv_sock.settimeout(0.1)

            self.message_queue.put(("status", "Connected"))

            self.request_next_parameter()

            while self.running:
                self.receive_osc()

                try:
                    command = self.command_queue.get_nowait()

                    if command == "STOP":
                        break

                    self.send_command(command)

                except queue.Empty:
                    pass

        except Exception as ex:
            self.message_queue.put(("status", f"Error: {ex}"))

        finally:
            if self.send_sock:
                self.send_sock.close()

            if self.recv_sock:
                self.recv_sock.close()

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

        if any(pattern.match(address) for pattern in CACHEABLE_ADDRESSES):
            self.cache[address] = args

        if not self.loaded:
            self.request_next_parameter()

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

    def send_osc(self, address, args):
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

    def __init__(self, master, worker, command_queue):
        self.worker = worker
        self.command_queue = command_queue

        self.channel_count = int(worker.cache["/Console/Input_Channels"][0])
        self.aux_list = self._build_aux_list()

        self.sliders = {}
        self.suppress_send = False
        self.refresh_job = None

        self.window = tk.Toplevel(master)
        self.window.title("Aux Send Levels")
        self.window.geometry("600x400")
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        self.build_ui()

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
        top = ttk.Frame(self.window, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="Aux Bus").pack(side="left")

        self.aux_combo = ttk.Combobox(
            top,
            values=[name for _, name in self.aux_list],
            state="readonly"
        )
        self.aux_combo.pack(side="left", padx=10)
        self.aux_combo.bind("<<ComboboxSelected>>", self.on_aux_selected)

        channels_frame = ttk.Frame(self.window, padding=10)
        channels_frame.pack(fill="both", expand=True)

        for i in range(1, self.channel_count + 1):
            name_key = f"/Input_Channels/{i}/Channel_Input/name"
            name = self.worker.cache[name_key][0] \
                if name_key in self.worker.cache else f"Ch {i}"

            column = ttk.Frame(channels_frame)
            column.pack(side="left", padx=4, fill="y")

            ttk.Label(column, text=name).pack()

            slider = tk.Scale(
                column,
                from_=1.0,
                to=0.0,
                resolution=0.01,
                orient="vertical",
                length=220,
                showvalue=False,
                command=lambda value, channel=i:
                    self.on_slider_change(channel, value)
            )
            slider.pack()

            self.sliders[i] = slider

        if self.aux_list:
            self.aux_combo.current(0)
            self.on_aux_selected()

    def current_aux(self):
        index = self.aux_combo.current()

        if index < 0:
            return None

        return self.aux_list[index][0]

    def on_aux_selected(self, event=None):
        aux = self.current_aux()

        if aux is None or not self.worker.is_alive():
            return

        for channel in range(1, self.channel_count + 1):
            self.command_queue.put(
                f"/Input_Channels/{channel}/Aux_Send/{aux}/send_level/?"
            )

    def on_slider_change(self, channel, value):
        if self.suppress_send:
            return

        aux = self.current_aux()

        if aux is None or not self.worker.is_alive():
            return

        self.command_queue.put(
            f"/Input_Channels/{channel}/Aux_Send/{aux}/send_level {value}"
        )

    def refresh_levels(self):
        aux = self.current_aux()

        if aux is not None:
            for channel, slider in self.sliders.items():
                key = f"/Input_Channels/{channel}/Aux_Send/{aux}/send_level"

                if key not in self.worker.cache:
                    continue

                level = self.worker.cache[key][0]

                if abs(slider.get() - level) > self.LEVEL_EPSILON:
                    self.suppress_send = True
                    slider.set(level)
                    self.suppress_send = False

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
        self.ip_entry.insert(0, "192.168.1.100")
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
        self.send_port_entry.insert(0, "10023")
        self.send_port_entry.grid(row=1, column=1, padx=5, pady=5)

        ttk.Label(frame, text="Rec Port").grid(
            row=2, column=0, sticky="w"
        )

        self.recv_port_entry = ttk.Entry(frame, width=15)
        self.recv_port_entry.insert(0, "10024")
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

    def connect(self):

        if self.worker and self.worker.is_alive():
            return

        send_port = self.send_port_entry.get()

        if len(send_port) < 3:
            self.status_label.config(text="Invalid send port")
            return

        self.worker = MixerWorker(
            self.ip_entry.get(),
            int(send_port),
            int(self.recv_port_entry.get()),
            self.command_queue,
            self.message_queue
        )

        self.worker.start()

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
