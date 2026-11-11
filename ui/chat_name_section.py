import copy
import tkinter as tk
from tkinter import messagebox, ttk

from services.chat_log import (
    MAX_TECH_NAME, TECH_NAME, clean_tech_name, normalize_tech_names,
)

# The muted gray the rest of the desktop UI uses for secondary text.
MUTED_FG = "#888888"

# How long typing in the default name box pauses before it is saved.
SAVE_DELAY_MS = 500


class ChatNameSection:
    """Settings > General's Chat Name: what phones see the desktop's chat
    messages signed as. One default, and a name of its own for any
    snapshot that wants one.

    `get_config` returns the normalized config (services/chat_log.py's
    normalize_tech_names); `set_config(config)` stores and saves a new
    one. `get_snapshots` returns the snapshot names the console has told
    us of.
    """

    def __init__(self, parent, get_config, set_config, get_snapshots):
        self.get_config = get_config
        self.set_config = set_config
        self.get_snapshots = get_snapshots
        self._save_job = None
        # Set while refresh() writes the box, so that is not taken for
        # typing.
        self._syncing = False

        self.frame = ttk.Frame(parent, padding=15)
        self.frame.pack(fill="x")

        ttk.Label(
            self.frame, text="Chat Name", font=("TkDefaultFont", 10, "bold")
        ).pack(anchor="w")

        ttk.Label(
            self.frame,
            text="The name phones see on your chat messages. A snapshot can "
                 "have a name of its own.",
            foreground=MUTED_FG, wraplength=600, justify="left"
        ).pack(anchor="w", pady=(2, 8))

        default_row = ttk.Frame(self.frame)
        default_row.pack(fill="x")

        ttk.Label(default_row, text="Default name").pack(side="left")
        self.default_var = tk.StringVar()
        entry = ttk.Entry(default_row, width=28, textvariable=self.default_var)
        entry.pack(side="left", padx=(8, 0))
        self.default_var.trace_add("write", self._on_default_typed)
        entry.bind("<FocusOut>", lambda event: self._save_default(done=True))
        entry.bind("<Return>", lambda event: self._save_default(done=True))

        body = ttk.Frame(self.frame)
        body.pack(fill="x", pady=(10, 0))
        body.columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            body, columns=("snapshot", "name"), show="headings", height=4,
            selectmode="browse"
        )
        self.tree.heading("snapshot", text="Snapshot", anchor="w")
        self.tree.heading("name", text="Chat name", anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<Double-1>", lambda event: self._edit())
        self.tree.bind("<<TreeviewSelect>>",
                       lambda event: self._update_buttons())

        buttons = ttk.Frame(body)
        buttons.grid(row=0, column=1, sticky="n", padx=(10, 0))

        def button(text, command):
            widget = ttk.Button(buttons, text=text, command=command, width=16)
            widget.pack(fill="x", pady=(0, 4))
            return widget

        button("Add", self._add)
        self.edit_button = button("Edit", self._edit)
        self.remove_button = button("Remove", self._remove)

        self.refresh()

    def refresh(self):
        """Redraws from the saved config - after an edit, or a restored
        backup."""
        config = self.get_config()

        self._syncing = True
        self.default_var.set(config["default"])
        self._syncing = False

        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())

        for snapshot in sorted(config["snapshots"], key=str.lower):
            self.tree.insert("", "end", iid=snapshot,
                             values=(snapshot, config["snapshots"][snapshot]))

        keep = [row for row in selected if self.tree.exists(row)]
        if keep:
            self.tree.selection_set(keep)

        self._update_buttons()

    def _update_buttons(self):
        state = ["!disabled"] if self.tree.selection() else ["disabled"]
        self.edit_button.state(state)
        self.remove_button.state(state)

    def _change(self, edit):
        """Applies edit(config) to a copy, saves it and redraws - swapped
        whole, since the phone server reads it from its own thread."""
        config = copy.deepcopy(self.get_config())
        edit(config)
        self.set_config(normalize_tech_names(config))
        self.refresh()

    # --- The default name ---

    def _on_default_typed(self, *args):
        if self._syncing:
            return

        text = self.default_var.get()
        if len(text) > MAX_TECH_NAME:
            self.default_var.set(text[:MAX_TECH_NAME])
            return

        if self._save_job is not None:
            self.frame.after_cancel(self._save_job)
        self._save_job = self.frame.after(SAVE_DELAY_MS, self._save_default)

    def _save_default(self, done=False):
        """Saves the default name. A blank box means the built-in name;
        the box is only filled back in with it once the operator is done
        (done), not while they are clearing it to retype."""
        if self._save_job is not None:
            self.frame.after_cancel(self._save_job)
            self._save_job = None

        name = clean_tech_name(self.default_var.get()) or TECH_NAME
        config = copy.deepcopy(self.get_config())

        if name != config["default"]:
            config["default"] = name
            self.set_config(normalize_tech_names(config))

        if done and self.default_var.get() != name:
            self._syncing = True
            self.default_var.set(name)
            self._syncing = False

    # --- Per-snapshot names ---

    def _add(self):
        taken = set(self.get_config()["snapshots"])
        offered = [s for s in self.get_snapshots() if s not in taken]

        def save(snapshot, name):
            if snapshot in taken:
                messagebox.showerror(
                    "Chat Name", f"{snapshot} already has a chat name - "
                    "edit that one instead.", parent=self.frame
                )
                return False

            self._change(lambda config: config["snapshots"].update(
                {snapshot: name}))
            self.tree.selection_set(snapshot)
            return True

        SnapshotNameDialog(self.frame, "Add Chat Name", offered, "", "",
                           on_saved=save)

    def _edit(self):
        selected = self.tree.selection()
        if not selected:
            return

        old = selected[0]
        config = self.get_config()
        taken = set(config["snapshots"]) - {old}
        offered = [s for s in self.get_snapshots() if s not in taken]

        def save(snapshot, name):
            if snapshot in taken:
                messagebox.showerror(
                    "Chat Name", f"{snapshot} already has a chat name.",
                    parent=self.frame
                )
                return False

            def edit(config):
                config["snapshots"].pop(old, None)
                config["snapshots"][snapshot] = name

            self._change(edit)
            self.tree.selection_set(snapshot)
            return True

        SnapshotNameDialog(self.frame, "Edit Chat Name", offered, old,
                           config["snapshots"][old], on_saved=save)

    def _remove(self):
        selected = self.tree.selection()
        if not selected:
            return

        self._change(lambda config: config["snapshots"].pop(selected[0], None))


class SnapshotNameDialog:
    """A snapshot - picked from the console's, or typed while it is not
    connected - and the chat name to use on it. on_saved(snapshot, name)
    may return False to keep the dialog open."""

    def __init__(self, master, title, snapshots, snapshot, name, on_saved):
        self.on_saved = on_saved

        self.window = tk.Toplevel(master)
        self.window.title(title)
        self.window.transient(master.winfo_toplevel())
        self.window.resizable(False, False)

        frame = ttk.Frame(self.window, padding=15)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Snapshot").grid(row=0, column=0, sticky="w")
        self.snapshot_combo = ttk.Combobox(frame, width=28, values=snapshots)
        self.snapshot_combo.set(snapshot)
        self.snapshot_combo.grid(row=0, column=1, sticky="ew", padx=(10, 0))

        ttk.Label(frame, text="Chat name").grid(row=1, column=0, sticky="w",
                                                pady=(10, 0))
        self.name_entry = ttk.Entry(frame, width=30)
        self.name_entry.insert(0, name)
        self.name_entry.grid(row=1, column=1, sticky="ew", padx=(10, 0),
                             pady=(10, 0))

        (self.name_entry if snapshot else self.snapshot_combo).focus_set()

        btn_bar = ttk.Frame(frame)
        btn_bar.grid(row=2, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(btn_bar, text="Cancel",
                   command=self.window.destroy).pack(side="right")
        ttk.Button(btn_bar, text="Save", style="Accent.TButton",
                   command=self.save).pack(side="right", padx=(0, 5))

        self.window.bind("<Return>", lambda event: self.save())
        self.window.bind("<Escape>", lambda event: self.window.destroy())
        self.window.grab_set()

    def save(self):
        snapshot = self.snapshot_combo.get().strip()
        name = clean_tech_name(self.name_entry.get())

        if not snapshot:
            self.snapshot_combo.focus_set()
            return
        if name is None:
            self.name_entry.focus_set()
            return

        if self.on_saved(snapshot, name) is not False:
            self.window.destroy()
