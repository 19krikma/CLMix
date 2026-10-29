import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

# Loud on purpose: this window exists to be noticed from across a stage.
SOS_RED = "#c62828"
SOS_TEXT = "#ffffff"

# How many times the system bell rings when an SOS arrives - more than
# once, since a single beep is easy to miss at a show.
BELL_COUNT = 3
BELL_GAP_MS = 350


class SosWindow:
    """The pop-up a phone's SOS button raises on the desktop: "SOS", what
    help is needed and who asked, from which aux and when.

    Always on top, and not tied to the main window, so it shows even with
    CLMix minimised. One window per account: a second SOS from the same
    person updates theirs (and says how many times they've asked) rather
    than stacking another on top - see show_sos.
    """

    def __init__(self, master, sos, on_closed):
        self.on_closed = on_closed
        self.count = 0

        self.window = tk.Toplevel(master)
        self.window.title("SOS")
        self.window.configure(bg=SOS_RED)
        self.window.resizable(False, False)
        self.window.attributes("-topmost", True)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Escape>", lambda event: self.close())

        frame = tk.Frame(self.window, bg=SOS_RED, padx=36, pady=24)
        frame.pack(fill="both", expand=True)

        family = tkfont.nametofont("TkDefaultFont").actual("family")

        def label(size, weight="normal", pady=0):
            widget = tk.Label(
                frame, bg=SOS_RED, fg=SOS_TEXT,
                font=(family, size, weight), justify="center"
            )
            widget.pack(pady=pady)
            return widget

        label(44, "bold").configure(text="SOS")
        self.reason_label = label(22, "bold", (0, 14))
        self.who_label = label(14)
        self.where_label = label(11)
        self.when_label = label(11, pady=(6, 18))

        ttk.Button(
            frame, text="Got it", style="Accent.TButton", command=self.close
        ).pack(ipadx=24, ipady=4)

        self.update(sos)

    def update(self, sos):
        """Shows this SOS - the first, or a newer one from the same
        account - and brings the window to the front again."""
        self.count += 1
        self.reason_label.configure(text=sos["reason"])

        who = sos["name"]
        if sos["name"] != sos["user"]:
            who += f"  ({sos['user']})"
        self.who_label.configure(text=f"from {who}")

        where = [part for part in (sos.get("aux_name"), sos.get("address")) if part]
        self.where_label.configure(text="  ·  ".join(where))

        at = time.strftime("%H:%M:%S", time.localtime(sos["time"]))
        self.when_label.configure(
            text=f"at {at}" if self.count == 1
            else f"asked {self.count} times, last at {at}"
        )

        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()
        self._ring(BELL_COUNT)

    def _ring(self, times):
        if times <= 0 or not self.window.winfo_exists():
            return
        self.window.bell()
        self.window.after(BELL_GAP_MS, lambda: self._ring(times - 1))

    def close(self):
        self.window.destroy()
        self.on_closed()
