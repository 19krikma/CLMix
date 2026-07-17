import tkinter as tk
from tkinter import ttk
import threading
import queue
import socket
import time


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
        self.sock = None

    def run(self):
        try:
            self.message_queue.put(("status", "Connecting"))

            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

            self.message_queue.put(("status", "Connected"))

            while self.running:
                try:
                    command = self.command_queue.get(timeout=0.1)

                    if command == "STOP":
                        break

                    self.send_command(command)

                except queue.Empty:
                    pass

        except Exception as ex:
            self.message_queue.put(("status", f"Error: {ex}"))

        finally:
            if self.sock:
                self.sock.close()

            self.message_queue.put(("status", "Disconnected"))

    def send_command(self, command):
        data = command.encode("utf-8")

        self.sock.sendto(
            data,
            (self.mixer_ip, self.send_port)
        )

        self.message_queue.put(
            ("message", f"Sent: {command}")
        )

    def stop(self):
        self.running = False


class MainWindow:

    def __init__(self):

        self.root = tk.Tk()
        self.root.title("Mixer Controller")
        self.root.geometry("550x250")

        self.worker = None

        self.command_queue = queue.Queue()
        self.message_queue = queue.Queue()

        self.build_ui()

        self.root.after(100, self.process_messages)

    def build_ui(self):

        frame = ttk.Frame(self.root, padding=15)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Mixer IP Address").grid(
            row=0, column=0, sticky="w"
        )

        self.ip_entry = ttk.Entry(frame, width=25)
        self.ip_entry.insert(0, "192.168.1.100")
        self.ip_entry.grid(row=0, column=1, padx=5, pady=5)

        ttk.Label(frame, text="Send Port").grid(
            row=1, column=0, sticky="w"
        )

        self.send_port_entry = ttk.Entry(frame, width=15)
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

        self.cmd_btn = ttk.Button(
            frame,
            text="Send Test Command",
            command=self.send_test_command
        )

        self.cmd_btn.grid(
            row=4,
            column=0,
            columnspan=2,
            pady=10
        )

    def connect(self):

        if self.worker and self.worker.is_alive():
            return

        self.worker = MixerWorker(
            self.ip_entry.get(),
            int(self.send_port_entry.get()),
            int(self.recv_port_entry.get()),
            self.command_queue,
            self.message_queue
        )

        self.worker.start()

    def disconnect(self):

        if self.worker:

            self.command_queue.put("STOP")
            self.worker.stop()

    def send_test_command(self):

        if self.worker and self.worker.is_alive():

            self.command_queue.put(
                "/ch/01/mix/fader 0.75"
            )

    def process_messages(self):

        while not self.message_queue.empty():

            msg_type, value = self.message_queue.get()

            if msg_type == "status":

                self.status_label.config(text=value)

                if value == "Connected":
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


if __name__ == "__main__":
    app = MainWindow()
    app.run()
