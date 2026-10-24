"""The Analyzer page: what the console said, tied to what the operator did.

A live list of the traffic services/traffic_analyzer.py keeps - which is
always recording, so the thing can be done on the desk first and found
here afterwards. Tick the rows that belong to it (click the box, or
select rows and press Space), say what was done, and Save observation.
Export report writes every observation as one Markdown file, for working
the findings into docs/mixer_protocol/PROTOCOL.md and the app.

Mark drops a timestamped line into the stream before doing something, so
"what came after I pressed it" is easy to find. Send puts one message of
the operator's own on the wire, to see how the console answers - best on
a scratch session, never mid-show.
"""

import shlex
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk

from services.traffic_analyzer import IN, NOTE, OUT, REPORTS_DIR, analyzer
from ui.logs_window import open_folder
from version import VERSION

REFRESH_MS = 300
FILTER_DEBOUNCE_MS = 200

# Rows the list keeps before trimming its oldest. The analyzer holds more;
# changing the filter rebuilds from all of it.
MAX_ROWS = 5000

TICKED = "☑"
UNTICKED = "☐"

# Mid-tone, so they read on both themes - as the capture view's colors.
DIRECTION_COLORS = {IN: "#5bb85b", OUT: "#4a9eda", NOTE: "#d9a441"}

HINT_COLOR = "#888888"


class AnalyzerWindow:
    def __init__(self, master, get_worker, command_queue):
        self.get_worker = get_worker
        self.command_queue = command_queue

        self.window = ttk.Frame(master)

        # Sequence numbers, not tree items: a ticked row stays ticked
        # when a filter change rebuilds the list without it and back.
        self.ticked = set()
        self._last_seq = 0
        self._marker_count = 0
        self._refresh_job = None
        self._filter_job = None

        self.filter_var = tk.StringVar()
        self.hide_heartbeat_var = tk.BooleanVar(value=True)
        self.meters_var = tk.BooleanVar(value=analyzer.include_meters)
        self.paused_var = tk.BooleanVar(value=False)
        self.send_address_var = tk.StringVar()
        self.send_args_var = tk.StringVar()

        self.build_ui()
        self.render_full()
        self._render_observations()
        self.refresh()

    # --- layout --------------------------------------------------------

    def build_ui(self):
        bar = ttk.Frame(self.window, padding=(10, 10, 10, 6))
        bar.pack(fill="x")

        ttk.Button(bar, text="Mark", command=self.mark).pack(side="left")

        ttk.Label(bar, text="Filter:").pack(side="left", padx=(14, 4))
        ttk.Entry(bar, textvariable=self.filter_var, width=22).pack(side="left")
        self.filter_var.trace_add("write", self._on_filter_changed)

        ttk.Button(bar, text="Clear", command=self.clear).pack(side="right")
        ttk.Button(
            bar, text="Untick all", command=self.untick_all
        ).pack(side="right", padx=(0, 8))

        self.count_label = ttk.Label(bar, foreground=HINT_COLOR)
        self.count_label.pack(side="right", padx=(0, 12))

        # A second row, so the page is no wider than the others - the
        # main window is sized to its widest page.
        bar = ttk.Frame(self.window, padding=(10, 0, 10, 6))
        bar.pack(fill="x")

        ttk.Checkbutton(
            bar, text="Hide heartbeat", variable=self.hide_heartbeat_var,
            command=self.render_full
        ).pack(side="left")

        # Not a view filter: meters are only recorded at all while this
        # is on, the stream being ~30 packets a second.
        ttk.Checkbutton(
            bar, text="Record meters", variable=self.meters_var,
            command=self._toggle_meters
        ).pack(side="left", padx=(14, 0))

        ttk.Checkbutton(
            bar, text="Pause view", variable=self.paused_var,
            command=self._on_pause_toggled
        ).pack(side="left", padx=(14, 0))

        self._build_list()
        self._build_send_row()
        self._build_observation_panel()

    def _build_list(self):
        frame = ttk.Frame(self.window, padding=(10, 0))
        frame.pack(fill="both", expand=True)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        columns = ("tick", "time", "dir", "address", "types", "args")
        tree = ttk.Treeview(frame, columns=columns, show="headings",
                            selectmode="extended", height=10)
        self.tree = tree

        for column, heading, width, stretch in (
            ("tick", "", 32, False),
            ("time", "Time", 92, False),
            ("dir", "Dir", 44, False),
            ("address", "Address", 330, True),
            ("types", "Types", 50, False),
            ("args", "Args", 180, True),
        ):
            tree.heading(column, text=heading, anchor="w")
            tree.column(column, width=width, minwidth=width if not stretch
                        else 80, stretch=stretch, anchor="w")

        y_scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=y_scroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")

        for direction, color in DIRECTION_COLORS.items():
            tree.tag_configure(direction, foreground=color)

        bold = tkfont.nametofont("TkDefaultFont").copy()
        bold.configure(weight="bold")
        self._bold = bold
        tree.tag_configure("ticked", font=bold)

        tree.bind("<Button-1>", self._on_click, add=True)
        tree.bind("<space>", self._toggle_selected)

    def _build_send_row(self):
        row = ttk.Frame(self.window, padding=(10, 8, 10, 0))
        row.pack(fill="x")

        ttk.Label(row, text="Send:").pack(side="left")
        address = ttk.Entry(row, textvariable=self.send_address_var, width=40)
        address.pack(side="left", padx=(4, 6))
        ttk.Label(row, text="Args:").pack(side="left")
        args = ttk.Entry(row, textvariable=self.send_args_var, width=18)
        args.pack(side="left", padx=(4, 6))
        ttk.Button(row, text="Send", command=self.send).pack(side="left")

        for entry in (address, args):
            entry.bind("<Return>", lambda _e: self.send())

        ttk.Label(
            self.window, padding=(10, 2, 10, 0), foreground=HINT_COLOR,
            wraplength=760, justify="left",
            text="Args: 2.0 float · i:2 int · s:x or \"a b\" string · none "
                 "for a query ending /?. Try writes on a scratch session."
        ).pack(fill="x")

    def _build_observation_panel(self):
        panel = ttk.Frame(self.window, padding=10)
        panel.pack(fill="x")
        panel.columnconfigure(0, weight=1)

        ttk.Label(
            panel, text="What did you do? Tick the rows it caused, describe "
                        "it, then save it as an observation.",
        ).grid(row=0, column=0, columnspan=2, sticky="w")

        self.note_text = tk.Text(panel, height=2, wrap="word",
                                 font=("TkDefaultFont", 10))
        self.note_text.grid(row=1, column=0, sticky="ew", pady=(4, 0))

        ttk.Button(
            panel, text="Save observation", style="Accent.TButton",
            command=self.save_observation
        ).grid(row=1, column=1, sticky="new", padx=(10, 0), pady=(4, 0))

        self.observation_list = tk.Listbox(panel, height=3,
                                           activestyle="none")
        self.observation_list.grid(row=2, column=0, sticky="ew",
                                   pady=(8, 0))

        files = ttk.Frame(panel)
        files.grid(row=2, column=1, sticky="new", padx=(10, 0), pady=(8, 0))
        ttk.Button(files, text="Export report", style="Accent.TButton",
                   command=self.export).pack(fill="x")
        ttk.Button(files, text="Open folder",
                   command=self._open_reports_folder).pack(fill="x",
                                                           pady=(4, 0))

        bottom = ttk.Frame(panel)
        bottom.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        ttk.Button(bottom, text="Remove all",
                   command=self.clear_observations).pack(side="right")
        ttk.Button(bottom, text="Remove",
                   command=self.remove_observation).pack(side="right",
                                                         padx=(0, 8))

        # width=1: a long report path is cut off rather than widening the
        # page, which the main window is sized to - as the Logs page.
        self.status_label = ttk.Label(bottom, foreground=HINT_COLOR, width=1)
        self.status_label.pack(side="left", fill="x", expand=True)

    # --- the list ------------------------------------------------------

    def _shown(self, record):
        if self.hide_heartbeat_var.get() and record.heartbeat:
            return False

        text = self.filter_var.get().strip().lower()
        return not text or text in record.address.lower() \
            or text in record.args_text().lower()

    def _values(self, record):
        return (
            TICKED if record.seq in self.ticked else UNTICKED,
            record.stamp, record.direction, record.address, record.tags,
            record.args_text(),
        )

    def _tags(self, record):
        tags = [record.direction]
        if record.seq in self.ticked:
            tags.append("ticked")
        return tags

    def _append(self, records):
        tree = self.tree
        children = tree.get_children()
        at_bottom = not children or tree.yview()[1] >= 0.999

        for record in records:
            self._last_seq = record.seq

            if not self._shown(record):
                continue

            tree.insert("", "end", iid=str(record.seq),
                        values=self._values(record),
                        tags=self._tags(record))

        children = tree.get_children()
        if len(children) > MAX_ROWS:
            tree.delete(*children[:len(children) - MAX_ROWS])

        if at_bottom and tree.get_children():
            tree.see(tree.get_children()[-1])

        self._update_count()

    def render_full(self):
        self._filter_job = None
        self.tree.delete(*self.tree.get_children())
        self._last_seq = 0
        self._append(analyzer.since(0))

    def refresh(self):
        if not self.paused_var.get():
            records = analyzer.since(self._last_seq)
            if records:
                self._append(records)

        self._refresh_job = self.window.after(REFRESH_MS, self.refresh)

    def _redraw_row(self, seq):
        item = str(seq)
        if not self.tree.exists(item):
            return

        values = list(self.tree.item(item, "values"))
        values[0] = TICKED if seq in self.ticked else UNTICKED
        tags = [t for t in self.tree.item(item, "tags") if t != "ticked"]
        if seq in self.ticked:
            tags.append("ticked")

        self.tree.item(item, values=values, tags=tags)

    def _toggle(self, seqs):
        seqs = list(seqs)
        # Ticking a mixed selection ticks all of it, rather than flipping
        # each row into the opposite of its neighbour.
        tick = any(seq not in self.ticked for seq in seqs)

        for seq in seqs:
            if tick:
                self.ticked.add(seq)
            else:
                self.ticked.discard(seq)
            self._redraw_row(seq)

        self._update_count()

    def _on_click(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return None
        if self.tree.identify_column(event.x) != "#1":
            return None

        row = self.tree.identify_row(event.y)
        if row:
            self._toggle([int(row)])

        # The box only ticks; it does not also move the selection.
        return "break"

    def _toggle_selected(self, _event=None):
        self._toggle(int(item) for item in self.tree.selection())
        return "break"

    def untick_all(self):
        ticked, self.ticked = self.ticked, set()
        for seq in ticked:
            self._redraw_row(seq)
        self._update_count()

    def _update_count(self):
        self.count_label.config(
            text=f"{len(self.tree.get_children())} shown · "
                 f"{len(self.ticked)} ticked"
        )

    def _on_filter_changed(self, *_args):
        if self._filter_job is not None:
            self.window.after_cancel(self._filter_job)
        self._filter_job = self.window.after(FILTER_DEBOUNCE_MS,
                                             self.render_full)

    def _toggle_meters(self):
        analyzer.include_meters = self.meters_var.get()

    def _on_pause_toggled(self):
        if not self.paused_var.get():
            self.refresh_now()

    def refresh_now(self):
        records = analyzer.since(self._last_seq)
        if records:
            self._append(records)

    def clear(self):
        analyzer.clear()
        self.ticked.clear()
        self.render_full()

    # --- marking and sending -------------------------------------------

    def mark(self):
        self._marker_count += 1
        analyzer.note(f"--- Marker {self._marker_count} ---")
        self.refresh_now()

    @staticmethod
    def parse_args(text):
        """(args, type tags) from the Args box - see the hint beside it."""
        args, types = [], ""

        for token in shlex.split(text):
            prefix, _, rest = token.partition(":")

            if prefix in ("i", "f", "s") and _:
                kind, value = prefix, rest
            else:
                try:
                    float(token)
                    kind, value = "f", token
                except ValueError:
                    kind, value = "s", token

            if kind == "i":
                args.append(int(value))
            elif kind == "f":
                args.append(float(value))
            else:
                args.append(value)
            types += kind

        return args, types

    def send(self):
        address = self.send_address_var.get().strip()

        if not address.startswith("/"):
            self._status("An address starts with /, e.g. "
                         "/Input_Channels/1/Channel_Input/input_type/?")
            return

        try:
            args, types = self.parse_args(self.send_args_var.get())
        except ValueError as ex:
            self._status(f"Could not read the args: {ex}")
            return

        worker = self.get_worker()
        if worker is None or not worker.is_alive():
            self._status("Not connected to the mixer.")
            return

        if worker.bridge_only:
            # _drain_commands would drop it without a word.
            self._status("DiGiCo App Capture is locking CLMix's own sends - "
                         "turn on Keep CLMix usable to send from here.")
            return

        analyzer.note(f"Sent by hand: {address} {args}")
        self.command_queue.put((address, args, types))
        self._status(f"Sent {address} {args}")

    # --- observations --------------------------------------------------

    def _context(self):
        """Where the desk was when an observation was saved."""
        worker = self.get_worker()
        if worker is None:
            return {"Mixer": "not connected"}

        context = {"Mixer": f"{worker.mixer_ip}:{worker.send_port}"}

        current = worker.cache.get("/Snapshots/Current_Snapshot")
        if current:
            name = worker.snapshot_name
            context["Snapshot"] = f"{current[0]}" + \
                (f' "{name}"' if name else "")

        return context

    def save_observation(self):
        note = self.note_text.get("1.0", "end").strip()

        if not self.ticked:
            self._status("Tick the rows that belong to what you did first.")
            return

        if not note:
            self._status("Say what you did - that is what the report is for.")
            return

        observation = analyzer.save_observation(note, self.ticked,
                                                self._context())
        if observation is None:
            self._status("Those rows have rolled out of the record - "
                         "tick newer ones.")
            return

        self.note_text.delete("1.0", "end")
        self.untick_all()
        self._render_observations()
        self._status(f"Saved: {len(observation.ticked)} ticked rows, "
                     f"{len(observation.records)} with their context.")

    def _render_observations(self):
        self.observation_list.delete(0, "end")

        for number, obs in enumerate(analyzer.observations(), 1):
            title = obs.note.splitlines()[0]
            self.observation_list.insert(
                "end",
                f"{number}. {obs.saved_at.strftime('%H:%M:%S')}  {title}  "
                f"({len(obs.ticked)} ticked)"
            )

    def remove_observation(self):
        for index in reversed(self.observation_list.curselection()):
            analyzer.remove_observation(index)
        self._render_observations()

    def clear_observations(self):
        if not analyzer.observations():
            return
        if not messagebox.askyesno(
            "Remove all", "Remove every saved observation?",
            parent=self.window
        ):
            return

        analyzer.clear_observations()
        self._render_observations()

    def export(self):
        if not analyzer.observations():
            self._status("Nothing to export - save an observation first.")
            return

        header = {"CLMix": VERSION}
        header.update(self._context())

        try:
            path = analyzer.export(header)
        except OSError as ex:
            self._status(f"Could not write the report: {ex}")
            return

        self._status(f"Report written (path copied): {path}")

        # So the path can be pasted straight into a chat.
        self.window.clipboard_clear()
        self.window.clipboard_append(str(path))

    def _open_reports_folder(self):
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        open_folder(REPORTS_DIR)

    def _status(self, text):
        self.status_label.config(text=text)

    def close(self):
        for job in (self._refresh_job, self._filter_job):
            if job is not None:
                self.window.after_cancel(job)

        self.window.destroy()
