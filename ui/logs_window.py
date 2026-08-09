import os
import platform
import subprocess
import tkinter as tk
from tkinter import ttk

from services.log_store import log_store


def open_folder(path):
    system = platform.system()

    try:
        if system == "Windows":
            os.startfile(path)  # noqa: S606 - user-initiated, local path only
        elif system == "Darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except OSError:
        pass


class LogsWindow:
    REFRESH_MS = 500
    SEARCH_DEBOUNCE_MS = 200

    def __init__(self, master):
        self.window = tk.Toplevel(master)
        self.window.title("Logs")
        self.window.geometry("720x460")
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        self.filter_var = tk.StringVar(value="Info")
        self.search_var = tk.StringVar(value="")
        self._last_seq_rendered = 0
        self.refresh_job = None
        self._search_job = None

        self.build_ui()
        self.render_full()
        self.refresh()

    def build_ui(self):
        bar = ttk.Frame(self.window, padding=10)
        bar.pack(fill="x")

        for level in ("Info", "Warning", "Error", "Debug"):
            ttk.Radiobutton(
                bar, text=level, value=level, variable=self.filter_var,
                command=self.render_full
            ).pack(side="left", padx=(0, 12))

        ttk.Label(bar, text="Search:").pack(side="left", padx=(12, 4))
        search_entry = ttk.Entry(bar, textvariable=self.search_var, width=20)
        search_entry.pack(side="left")
        self.search_var.trace_add("write", self._on_search_changed)

        ttk.Button(bar, text="Clear", command=self.clear).pack(side="right")
        ttk.Button(
            bar, text="Open Logs Folder",
            command=lambda: open_folder(log_store.logs_dir)
        ).pack(side="right", padx=(0, 8))

        file_bar = ttk.Frame(self.window, padding=(10, 0))
        file_bar.pack(fill="x")

        self.file_label = ttk.Label(file_bar, foreground="#888888")
        self.file_label.pack(side="left")

        self.write_error_label = ttk.Label(file_bar, foreground="#e5473f")
        self.write_error_label.pack(side="right")

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
        self.text.tag_configure("warning", foreground="#d9a441")

    def _on_search_changed(self, *_args):
        if self._search_job is not None:
            self.window.after_cancel(self._search_job)

        self._search_job = self.window.after(
            self.SEARCH_DEBOUNCE_MS, self.render_full
        )

    def clear(self):
        log_store.clear()
        self.render_full()

    def refresh(self):
        entries = log_store.snapshot()

        if entries and entries[-1][0] != self._last_seq_rendered:
            self._append_new(entries)

        self.file_label.config(text=f"Log file: {log_store.current_log_path()}")

        write_error = log_store.get_write_error()
        self.write_error_label.config(
            text=f"Failed to write log file: {write_error}" if write_error else ""
        )

        self.refresh_job = self.window.after(self.REFRESH_MS, self.refresh)

    def _matches(self, level, message):
        level_filter = self.filter_var.get()

        # Debug is the firehose - it already includes everything info,
        # warning and error show, so it's the only filter that isn't an
        # exact level match.
        if level_filter != "Debug" and level != level_filter.lower():
            return False

        search = self.search_var.get().strip().lower()
        return not search or search in message.lower()

    def render_full(self):
        self._search_job = None
        self._last_seq_rendered = 0

        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

        self._append_new(log_store.snapshot())

    def _append_new(self, entries):
        at_bottom = self.text.yview()[1] >= 0.999

        self.text.configure(state="normal")

        for seq, timestamp, level, message in entries:
            if seq <= self._last_seq_rendered:
                continue

            self._last_seq_rendered = seq

            if not self._matches(level, message):
                continue

            line = f"[{timestamp}] {level.upper():7} {message}\n"
            self.text.insert(
                "end", line, level if level in ("error", "warning") else ""
            )

        if at_bottom:
            self.text.see("end")

        self.text.configure(state="disabled")

    def close(self):
        if self.refresh_job:
            self.window.after_cancel(self.refresh_job)

        if self._search_job:
            self.window.after_cancel(self._search_job)

        self.window.destroy()
