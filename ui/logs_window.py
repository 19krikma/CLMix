import tkinter as tk
from tkinter import ttk

from services.log_store import log_store


class LogsWindow:
    REFRESH_MS = 500

    def __init__(self, master):
        self.window = tk.Toplevel(master)
        self.window.title("Logs")
        self.window.geometry("720x460")
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        self.filter_var = tk.StringVar(value="Info")
        self._shown_count = None
        self.refresh_job = None

        self.build_ui()
        self.refresh()

    def build_ui(self):
        bar = ttk.Frame(self.window, padding=10)
        bar.pack(fill="x")

        for level in ("Info", "Error", "Debug"):
            ttk.Radiobutton(
                bar, text=level, value=level, variable=self.filter_var,
                command=self.render
            ).pack(side="left", padx=(0, 12))

        ttk.Button(bar, text="Clear", command=self.clear).pack(side="right")

        text_frame = ttk.Frame(self.window, padding=(10, 0, 10, 10))
        text_frame.pack(fill="both", expand=True)
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)

        self.text = tk.Text(
            text_frame, wrap="none", state="disabled",
            font=("TkFixedFont", 9)
        )
        y_scroll = ttk.Scrollbar(
            text_frame, orient="vertical", command=self.text.yview
        )
        x_scroll = ttk.Scrollbar(
            text_frame, orient="horizontal", command=self.text.xview
        )
        self.text.configure(
            yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set
        )

        self.text.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")

        self.text.tag_configure("error", foreground="#e5473f")

    def clear(self):
        log_store.clear()
        self._shown_count = None
        self.render()

    def refresh(self):
        entries = log_store.snapshot()

        if len(entries) != self._shown_count:
            self._shown_count = len(entries)
            self.render(entries)

        self.refresh_job = self.window.after(self.REFRESH_MS, self.refresh)

    def render(self, entries=None):
        if entries is None:
            entries = log_store.snapshot()

        level_filter = self.filter_var.get()

        at_bottom = self.text.yview()[1] >= 0.999

        self.text.configure(state="normal")
        self.text.delete("1.0", "end")

        for timestamp, level, message in entries:
            # Debug is the firehose - it already includes everything info
            # and error show, so it's the only filter that isn't an exact
            # level match.
            if level_filter != "Debug" and level != level_filter.lower():
                continue

            line = f"[{timestamp}] {level.upper():5} {message}\n"
            self.text.insert("end", line, level if level == "error" else "")

        if at_bottom:
            self.text.see("end")

        self.text.configure(state="disabled")

    def close(self):
        if self.refresh_job:
            self.window.after_cancel(self.refresh_job)

        self.window.destroy()
