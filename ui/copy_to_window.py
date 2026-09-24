"""The Copy To window: one snapshot's channel settings onto other snapshots.

Four choices, left to right as they are made: which snapshot to copy
from, which channels, which of their settings, and which snapshots to
write them to. Then CopySettingsJob (services/copy_settings.py) walks the
console, and this window shows its progress and puts its prompts - press
Update on the desk, then Continue - in front of the operator, the same
way the Mixer Backup window does for a restore.

Every list here has an "All" row at the top, and picking it beats
whatever else is picked in that list: "All channels" plus a handful of
channels means all of them, not the handful. The settings list is ticked
rather than selected - what will be copied is a longer-lived choice than
which row was last clicked, and a tick still reads as chosen once the
list has lost focus. Ticking "All settings" there takes the individual
groups over: they show ticked, greyed and unclickable, and go back to
whatever they were when it is unticked.

Lists are built from what the console told CLMix at connect time, so
Refresh is there for a desk whose snapshots were renamed since.
"""

import tkinter as tk
from tkinter import messagebox, ttk

from services.copy_settings import DEFAULT_GROUPS, GROUP_LABELS, CopySettingsJob

POLL_MS = 150

# How long to let the console's answers to "/Snapshots/names/?" land
# before the list is rebuilt from them.
NAMES_SETTLE_MS = 700

ALL_ROW = "all"

# The settings list draws its own tick boxes. A glyph, not an image: it
# needs no asset, no per-theme variant, and it stays put when the row is
# greyed out.
TICKED = "\u2611"
UNTICKED = "\u2610"

# Rows that "All settings" has taken over. The same grey the hint text
# uses, which reads as secondary in both themes.
DIM_TAG = "dim"
DIM_COLOR = "#888888"


class CopyToWindow:
    def __init__(self, master, get_worker, command_queue):
        self.get_worker = get_worker
        self.command_queue = command_queue

        self.job = None
        self._prompt_shown = None
        self._notes_shown = 0
        self._poll_job = None
        self._names_job = None
        # Which snapshot each row of the Copy from box means; index 0 is
        # "Current Snapshot", which is None - whatever the desk is on when
        # the job starts, not whatever it is on now.
        self._source_indices = [None]
        self._snapshot_rows = []
        # The settings ticked, and whether "All settings" is ticked over
        # the top of them. Kept apart so unticking All gives the groups
        # back exactly as they were rather than all-on or all-off.
        self._groups_ticked = set(DEFAULT_GROUPS)
        self._all_groups = False

        self.window = tk.Toplevel(master)
        self.window.title("Copy To")
        self.window.geometry("1000x700")
        self.window.minsize(860, 600)
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        self.build_ui()
        self.refresh(ask_console=True)

    # ------------------------------------------------------------------ UI

    def build_ui(self):
        top = ttk.Frame(self.window, padding=(10, 10, 10, 4))
        top.pack(fill="x")

        ttk.Label(top, text="Copy from:").pack(side="left")
        self.source_box = ttk.Combobox(top, state="readonly", width=40)
        self.source_box.pack(side="left", padx=(6, 0))
        ttk.Button(top, text="Refresh",
                   command=lambda: self.refresh(ask_console=True)
                   ).pack(side="right")

        ttk.Label(
            self.window,
            text="Pick the channels, the settings to take from them, and the "
                 "snapshots to write them to. An “All” row covers "
                 "the whole list.",
            padding=(10, 0, 10, 0), wraplength=940, justify="left",
            foreground="#888888"
        ).pack(fill="x")

        lists = ttk.Frame(self.window, padding=(10, 6))
        lists.pack(fill="both", expand=True)
        lists.rowconfigure(0, weight=1)
        for column in range(3):
            lists.columnconfigure(column, weight=1, uniform="lists")

        self.channel_tree, self.channel_count_label = self._list_pane(
            lists, 0, "Channels", "All channels"
        )
        self.group_tree, self.group_count_label = self._list_pane(
            lists, 1, "Settings", "All settings", ticked=True
        )
        self.target_tree, self.target_count_label = self._list_pane(
            lists, 2, "Copy to snapshots", "All snapshots"
        )

        for label in GROUP_LABELS:
            self.group_tree.insert("", "end", iid=label, text=label)
        self._render_groups()

        job = ttk.LabelFrame(self.window, text="Progress", padding=10)
        job.pack(fill="x", padx=10, pady=(4, 10))

        status_row = ttk.Frame(job)
        status_row.pack(fill="x")
        self.status_label = ttk.Label(
            status_row, text="Idle.", wraplength=740, justify="left"
        )
        self.status_label.pack(side="left", fill="x", expand=True)
        self.cancel_button = ttk.Button(
            status_row, text="Cancel", command=self.cancel_job,
            state="disabled"
        )
        self.cancel_button.pack(side="right")
        self.copy_button = ttk.Button(
            status_row, text="Copy", command=self.start_copy,
            style="Accent.TButton"
        )
        self.copy_button.pack(side="right", padx=(0, 8))

        self.progress = ttk.Progressbar(job, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(8, 0))

        # Filled while the job waits on the operator; empty otherwise.
        self.prompt_frame = ttk.Frame(job)
        self.prompt_frame.pack(fill="x", pady=(8, 0))
        self.prompt_label = ttk.Label(
            self.prompt_frame, text="", wraplength=620, justify="left",
            font=("TkDefaultFont", 10, "bold")
        )
        self.prompt_label.pack(side="left", fill="x", expand=True)
        self.prompt_buttons = ttk.Frame(self.prompt_frame)
        self.prompt_buttons.pack(side="right")

        self.notes = tk.Text(job, height=5, wrap="word", state="disabled")
        self.notes.pack(fill="x", pady=(8, 0))

        self._update_counts()

    def _list_pane(self, parent, column, title, all_label, ticked=False):
        """One titled list with its "All" row already in it.

        Rows are selected, unless `ticked`, in which case a click toggles
        a tick box instead and the selection highlight is turned off so
        the two cannot say different things.
        """
        frame = ttk.LabelFrame(parent, text=title, padding=(6, 4))
        frame.grid(row=0, column=column, sticky="nsew",
                   padx=(0, 8 if column < 2 else 0))
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        tree = ttk.Treeview(frame, show="tree",
                            selectmode="none" if ticked else "extended")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")

        tree.insert("", "end", iid=ALL_ROW, text=all_label)

        if ticked:
            tree.tag_configure(DIM_TAG, foreground=DIM_COLOR)
            tree.bind("<Button-1>", self._toggle_group)
        else:
            tree.bind("<<TreeviewSelect>>", lambda _e: self._update_counts())

        count = ttk.Label(frame, text="", foreground="#888888")
        count.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        return tree, count

    # -------------------------------------------------------------- lists

    def refresh(self, ask_console=False):
        """Rebuild the channel and snapshot lists from what the desk said.

        With ask_console, the snapshot names are asked for again first and
        the rebuild happens once the answers have had time to land - a
        snapshot renamed since CLMix connected is otherwise listed under
        its old name.
        """
        if self._names_job is not None:
            self.window.after_cancel(self._names_job)
            self._names_job = None

        if ask_console and self._console_connected():
            self.command_queue.put("/Snapshots/names/?")
            self._names_job = self.window.after(
                NAMES_SETTLE_MS, lambda: self.refresh(ask_console=False)
            )

        worker = self.get_worker()
        snapshots = self._snapshots(worker)

        self._fill(self.channel_tree, self._channels(worker))
        self._fill(self.target_tree,
                   [(index, f"{index}  {name}") for index, name in snapshots])
        self._fill_sources(snapshots, worker)
        self._update_counts()

    @staticmethod
    def _channels(worker):
        """(number, label) per input channel, named as the console names them."""
        if worker is None or not worker.loaded:
            return []

        count = worker.cache.get("/Console/Input_Channels")
        if not count:
            return []

        rows = []
        for i in range(1, int(count[0]) + 1):
            name = worker.cache.get(f"/Input_Channels/{i}/Channel_Input/name")
            rows.append((str(i), f"{i}  {name[0] if name else ''}".rstrip()))

        return rows

    def _snapshots(self, worker):
        """(index, name) for every snapshot the console has named.

        The worker fills snapshot_names on its own thread as the console
        answers - which is exactly what Refresh has just asked it to do -
        so a copy taken at the wrong moment can be refused. The list from
        last time is a better answer than an empty one.
        """
        if worker is None or not worker.loaded:
            return []

        try:
            names = dict(worker.snapshot_names)
        except RuntimeError:
            return self._snapshot_rows

        self._snapshot_rows = [(index, names[index]) for index in sorted(names)]
        return self._snapshot_rows

    @staticmethod
    def _fill(tree, rows):
        """Replace a list's rows, keeping the "All" row and the selection.

        Selections survive a refresh so that re-reading the snapshot names
        while someone is halfway through choosing does not undo the
        choosing. A row that is gone afterwards simply drops out.
        """
        chosen = set(tree.selection())

        for item in tree.get_children():
            if item != ALL_ROW:
                tree.delete(item)

        for key, label in rows:
            tree.insert("", "end", iid=str(key), text=label)

        keep = [item for item in tree.get_children() if item in chosen]
        if keep:
            tree.selection_set(keep)

    def _fill_sources(self, snapshots, worker):
        """The Copy from box: the desk's current snapshot, or a named one."""
        current = None
        if worker is not None and worker.loaded:
            value = worker.cache.get("/Snapshots/Current_Snapshot")
            current = int(value[0]) if value else None

        self._source_indices = [None] + [index for index, _name in snapshots]

        labels = ["Current Snapshot" + (f"  ({current})" if current is not None
                                        else "")]
        labels += [f"{index}  {name}" for index, name in snapshots]

        was = self.source_box.current()
        self.source_box.configure(values=labels)
        self.source_box.current(was if 0 <= was < len(labels) else 0)

    def _render_groups(self):
        """Draw the settings list from the ticks, and grey what All owns."""
        self.group_tree.item(
            ALL_ROW,
            text=f"{TICKED if self._all_groups else UNTICKED} All settings"
        )

        for label in GROUP_LABELS:
            ticked = self._all_groups or label in self._groups_ticked
            self.group_tree.item(
                label,
                text=f"{TICKED if ticked else UNTICKED} {label}",
                tags=(DIM_TAG,) if self._all_groups else (),
            )

        self._update_counts()

    def _toggle_group(self, event):
        """A click in the settings list ticks the row it landed on.

        Returns "break" either way: Treeview's own click handling would
        otherwise start a selection the list does not use, and while All
        is ticked the individual rows are not the operator's to change.
        """
        row = self.group_tree.identify_row(event.y)

        if not row:
            return "break"

        if row == ALL_ROW:
            self._all_groups = not self._all_groups
        elif self._all_groups:
            return "break"
        elif row in self._groups_ticked:
            self._groups_ticked.discard(row)
        else:
            self._groups_ticked.add(row)

        self._render_groups()
        return "break"

    def _groups(self):
        """The settings to copy: None for all of them, else the ticked ones."""
        if self._all_groups:
            return None

        return [label for label in GROUP_LABELS if label in self._groups_ticked]

    def _picked(self, tree):
        """What a list means: every row, or the ones selected.

        None for "all of them" - the All row, or a list nobody touched
        where every row would be the same thing anyway. Otherwise the iids
        picked, in the order the list holds them.
        """
        selection = set(tree.selection())

        if not selection or ALL_ROW in selection:
            return None

        return [item for item in tree.get_children()
                if item != ALL_ROW and item in selection]

    def _update_counts(self):
        for tree, label, noun in (
            (self.channel_tree, self.channel_count_label, "channel"),
            (self.group_tree, self.group_count_label, "setting group"),
            (self.target_tree, self.target_count_label, "snapshot"),
        ):
            total = len(tree.get_children()) - 1
            picked = self._groups() if tree is self.group_tree \
                else self._picked(tree)
            chosen = total if picked is None else len(picked)
            label.configure(text=f"{chosen} of {total} {noun}(s)"
                            + (" - all" if picked is None and total else ""))

    # ---------------------------------------------------------------- job

    def _console_connected(self):
        worker = self.get_worker()
        return worker is not None and worker.is_alive() and worker.loaded

    def _console_ready(self):
        worker = self.get_worker()

        if not self._console_connected():
            messagebox.showinfo(
                "Copy To", "Connect to the console first.", parent=self.window
            )
            return False

        if worker.bridge_only:
            messagebox.showinfo(
                "Copy To",
                "CLMix is in DiGiCo App mode. Turn it off in Setup first.",
                parent=self.window
            )
            return False

        return True

    def start_copy(self):
        if not self._console_ready():
            return

        channels = self._picked(self.channel_tree)
        groups = self._groups()
        targets = self._picked(self.target_tree)

        all_channels = [item for item in self.channel_tree.get_children()
                        if item != ALL_ROW]
        all_targets = [item for item in self.target_tree.get_children()
                       if item != ALL_ROW]

        if not all_channels or not all_targets:
            messagebox.showinfo(
                "Copy To",
                "The console's channels and snapshots are not loaded yet.",
                parent=self.window
            )
            return

        if groups is not None and not groups:
            messagebox.showinfo(
                "Copy To", "Tick at least one setting to copy.",
                parent=self.window
            )
            return

        strips = [f"/Input_Channels/{number}"
                  for number in (channels if channels is not None
                                 else all_channels)]
        target_indices = None if targets is None else [int(i) for i in targets]

        index = self.source_box.current()
        source = self._source_indices[index] if 0 <= index < \
            len(self._source_indices) else None

        count = len(all_targets) if target_indices is None \
            else len(target_indices)
        what = "every setting" if groups is None \
            else f"{len(groups)} setting group(s)"

        if not messagebox.askokcancel(
            "Copy To",
            f"This copies {what} on "
            f"{len(strips)} channel(s) from "
            f"{self.source_box.get()} to {count} snapshot(s).\n\n"
            "CLMix recalls each snapshot in turn, so the desk's output "
            "changes while this runs and anything unsaved on the current "
            "snapshot is lost - only do this when the console is not "
            "live.\n\n"
            "Each snapshot is written to and then waits for you to press "
            "Update on the console before it moves on. It goes back to the "
            "snapshot the desk started on at the end.",
            icon="warning", parent=self.window
        ):
            return

        self._run(CopySettingsJob(self.get_worker, self.command_queue, source,
                                  strips, groups, target_indices))

    def _run(self, job):
        self.job = job
        self._prompt_shown = None
        self._notes_shown = 0
        self._set_notes("")
        self.progress.configure(value=0)
        job.start()
        self.update_buttons()
        self._poll()

    def cancel_job(self):
        if self.job is not None and not self.job.finished:
            self.job.cancel()

    def update_buttons(self):
        busy = self.job is not None and not self.job.finished
        self.copy_button.configure(state="disabled" if busy else "normal")
        self.cancel_button.configure(state="normal" if busy else "disabled")

    # ------------------------------------------------------------- polling

    def _poll(self):
        self._poll_job = None
        job = self.job

        if job is None or not self.window.winfo_exists():
            return

        done, total = job.progress
        self.progress.configure(value=(100.0 * done / total) if total else 0)
        self.status_label.configure(text=job.status)

        if job.prompt is not self._prompt_shown:
            self._show_prompt(job.prompt)

        if len(job.notes) > self._notes_shown:
            self._append_notes(job.notes[self._notes_shown:])
            self._notes_shown = len(job.notes)

        if job.finished:
            self._show_prompt(None)
            self.status_label.configure(
                text=job.outcome,
                foreground="#e5473f" if job.failed else ""
            )
            if not job.failed:
                self.progress.configure(value=100)
            self.update_buttons()
            self.refresh()
            return

        self.status_label.configure(foreground="")
        self._poll_job = self.window.after(POLL_MS, self._poll)

    def _show_prompt(self, prompt):
        self._prompt_shown = prompt

        for child in self.prompt_buttons.winfo_children():
            child.destroy()

        if prompt is None:
            self.prompt_label.configure(text="")
            return

        self.prompt_label.configure(text=prompt.text)

        for choice in prompt.choices:
            ttk.Button(
                self.prompt_buttons, text=choice,
                command=lambda c=choice: self.job.respond(c),
                style="Accent.TButton" if choice == "Continue" else "TButton"
            ).pack(side="left", padx=(8, 0))

        # A prompt is the job waiting on the operator - make sure they see it.
        self.window.deiconify()
        self.window.lift()

    def _set_notes(self, text):
        self.notes.configure(state="normal")
        self.notes.delete("1.0", "end")
        self.notes.insert("end", text)
        self.notes.configure(state="disabled")

    def _append_notes(self, lines):
        self.notes.configure(state="normal")
        for line in lines:
            self.notes.insert("end", line + "\n")
        self.notes.see("end")
        self.notes.configure(state="disabled")

    # --------------------------------------------------------------- close

    def close(self):
        if self.job is not None and not self.job.finished:
            if not messagebox.askyesno(
                "Copy To",
                "A copy is still running. Stop it and close?",
                icon="warning", parent=self.window
            ):
                return
            self.job.cancel()

        for job in (self._poll_job, self._names_job):
            if job is not None:
                self.window.after_cancel(job)

        self.window.destroy()
