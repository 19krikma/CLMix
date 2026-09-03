import tkinter as tk
from tkinter import messagebox, ttk

from services.user_store import ALL_AUX, ALL_SNAPSHOTS
from ui.password_entry import PasswordEntry

AUX_CHECKBOXES_PER_ROW = 5

# First entry of the snapshot filter, meaning "don't filter". Deliberately
# not ALL_SNAPSHOTS: that is a real value an account can hold, and picking
# it should show those accounts specifically rather than everything. The
# two are told apart by position, not by text, so a snapshot named "(any)"
# on the console still can't be confused for this.
FILTER_ANY = "(any)"


def format_aux(aux):
    if aux == ALL_AUX:
        return ALL_AUX

    if not aux:
        return "(none)"

    return ", ".join(aux)


class AccessPanel:
    """User accounts and their per-user snapshot/aux/preset/mute permissions.

    Lives as a tab inside the Setup window rather than owning a window of
    its own. The permission options offered by UserEditDialog come from
    the connected mixer, but they are read at dialog-open time rather than
    build time, so this panel does not need rebuilding when the connection
    changes - refresh_list() is enough.

    The snapshot filter above the list is built from the accounts
    themselves rather than from the mixer: its options are the snapshot
    values actually in use, so it never offers a choice that would show an
    empty list, and it shrinks as accounts using a snapshot are edited
    away or deleted.
    """

    def __init__(self, parent, user_store, get_worker, get_hidden_auxes=None):
        self.user_store = user_store
        self.get_worker = get_worker
        self.get_hidden_auxes = get_hidden_auxes or (lambda: set())

        self.container = parent

        # None means "show every account"; otherwise the snapshot name
        # rows must match. Held here rather than read back off the
        # combobox so refresh_list can rebuild that widget's options
        # without the selection being part of the round trip.
        self.snapshot_filter = None

        # UserEditDialog grabs/transients onto this and the messageboxes
        # parent to it - a tab frame can't serve as either.
        self.window = parent.winfo_toplevel()

        self.build_ui()
        self.refresh_list()

    def build_ui(self):
        # Packed first because pack() stacks in call order - this has to
        # come before the tree to sit above it.
        filter_bar = ttk.Frame(self.container, padding=(10, 10, 10, 0))
        filter_bar.pack(fill="x")

        ttk.Label(filter_bar, text="Snapshot").pack(side="left")

        self.filter_combo = ttk.Combobox(filter_bar, width=22, state="readonly")
        self.filter_combo.pack(side="left", padx=(6, 0))
        self.filter_combo.bind("<<ComboboxSelected>>", self.on_filter_changed)

        columns = ("username", "snapshot", "aux", "presets", "mute")
        self.tree = ttk.Treeview(
            self.container, columns=columns, show="headings", height=10
        )
        self.tree.heading("username", text="Username")
        self.tree.heading("snapshot", text="Snapshot Access")
        self.tree.heading("aux", text="Aux Access")
        self.tree.heading("presets", text="Preset Access")
        self.tree.heading("mute", text="Mute Access")
        self.tree.column("username", width=130)
        self.tree.column("snapshot", width=140)
        self.tree.column("aux", width=105)
        self.tree.column("presets", width=85, anchor="center")
        self.tree.column("mute", width=80, anchor="center")
        self.tree.pack(fill="both", expand=True, padx=10, pady=10)
        self.tree.bind("<Double-1>", lambda event: self.edit_user())

        btn_bar = ttk.Frame(self.container, padding=(10, 0, 10, 10))
        btn_bar.pack(fill="x")

        ttk.Button(btn_bar, text="New", command=self.new_user).pack(side="left")
        ttk.Button(
            btn_bar, text="Edit", command=self.edit_user
        ).pack(side="left", padx=5)
        ttk.Button(btn_bar, text="Delete", command=self.delete_user).pack(side="left")

    def on_filter_changed(self, _event=None):
        # Index 0 is FILTER_ANY; anything else is a snapshot name.
        self.snapshot_filter = (
            None if self.filter_combo.current() <= 0 else self.filter_combo.get()
        )
        self.refresh_list()

    def sync_filter_options(self, users):
        """Rebuild the filter's choices from the snapshots the accounts use."""
        options = [FILTER_ANY] + sorted({record["snapshot"] for _, record in users})
        self.filter_combo.config(values=options)

        # The last account using the filtered snapshot may have just been
        # deleted or edited onto another one, which would leave the list
        # filtered by something no longer on offer - and looking empty for
        # no visible reason.
        if self.snapshot_filter not in options:
            self.snapshot_filter = None

        self.filter_combo.set(self.snapshot_filter or FILTER_ANY)

    def refresh_list(self):
        for row in self.tree.get_children():
            self.tree.delete(row)

        users = self.user_store.list_users()
        self.sync_filter_options(users)

        for username, record in users:
            if self.snapshot_filter is not None \
                    and record["snapshot"] != self.snapshot_filter:
                continue

            self.tree.insert(
                "", "end", iid=username,
                values=(
                    username, record["snapshot"], format_aux(record["aux"]),
                    "Yes" if record.get("presets", False) else "No",
                    "Yes" if record.get("mute", True) else "No",
                )
            )

    def _known_snapshots(self):
        worker = self.get_worker()
        names = set(worker.snapshot_names.values()) if worker else set()
        return [ALL_SNAPSHOTS] + sorted(names)

    def _known_auxes(self):
        worker = self.get_worker()

        if not worker or not worker.loaded:
            return []

        from ui.main_window import build_aux_list
        hidden = self.get_hidden_auxes()
        return [name for _, name in build_aux_list(worker, hidden=hidden)]

    def new_user(self):
        UserEditDialog(
            self.window, self.user_store,
            self._known_snapshots(), self._known_auxes(),
            on_saved=self.after_save
        )

    def edit_user(self):
        selected = self.tree.selection()

        if not selected:
            return

        UserEditDialog(
            self.window, self.user_store,
            self._known_snapshots(), self._known_auxes(),
            username=selected[0], on_saved=self.after_save
        )

    def after_save(self, username):
        """Refresh after an edit, keeping the saved account in view.

        Saving an account onto a snapshot the active filter excludes would
        otherwise drop it straight back out of the list, which reads as
        the save having failed. Showing everything again is the honest
        outcome: the account exists, it just isn't what was filtered for.
        """
        record = self.user_store.get(username)

        if record is not None and self.snapshot_filter is not None \
                and record["snapshot"] != self.snapshot_filter:
            self.snapshot_filter = None

        self.refresh_list()

        if self.tree.exists(username):
            self.tree.selection_set(username)
            self.tree.see(username)

    def delete_user(self):
        selected = self.tree.selection()

        if not selected:
            return

        username = selected[0]

        if messagebox.askyesno(
            "Delete user", f"Delete user '{username}'?", parent=self.window
        ):
            self.user_store.delete_user(username)
            self.refresh_list()


class UserEditDialog:
    def __init__(self, master, user_store, snapshot_options, aux_options,
                 username=None, on_saved=None):
        self.user_store = user_store
        self.username = username
        self.on_saved = on_saved

        record = user_store.get(username) if username else None

        self.window = tk.Toplevel(master)
        self.window.title("Edit User" if username else "New User")
        self.window.transient(master)
        self.window.grab_set()

        frame = ttk.Frame(self.window, padding=15)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Username").grid(row=0, column=0, sticky="w")
        self.username_entry = ttk.Entry(frame, width=25)
        self.username_entry.grid(row=0, column=1, sticky="w", padx=5, pady=5)

        if username:
            self.username_entry.insert(0, username)
            self.username_entry.config(state="disabled")

        ttk.Label(frame, text="Password").grid(row=1, column=0, sticky="w")
        self.password_entry = PasswordEntry(frame, width=25)
        self.password_entry.grid(row=1, column=1, sticky="w", padx=5, pady=5)

        if username:
            ttk.Label(
                frame, text="(leave blank to keep current password)",
                font=("TkDefaultFont", 8)
            ).grid(row=2, column=0, columnspan=2, sticky="w")

        ttk.Label(frame, text="Snapshot Access").grid(
            row=3, column=0, sticky="w", pady=(10, 0)
        )
        self.snapshot_combo = ttk.Combobox(frame, width=23, values=snapshot_options)
        self.snapshot_combo.grid(row=3, column=1, sticky="w", padx=5, pady=(10, 5))

        # A Combobox reserves room for its dropdown arrow on top of the
        # character width it is asked for, so the same `width` as an Entry
        # renders wider. Trim it until the right edges line up, measured
        # rather than hardcoded so it holds up if the theme's font changes.
        target = self.username_entry.winfo_reqwidth()

        while (self.snapshot_combo.winfo_reqwidth() > target
               and int(self.snapshot_combo.cget("width")) > 5):
            self.snapshot_combo.config(
                width=int(self.snapshot_combo.cget("width")) - 1
            )
        self.snapshot_combo.set(record["snapshot"] if record else ALL_SNAPSHOTS)

        ttk.Label(frame, text="Preset Access").grid(
            row=4, column=0, sticky="w", pady=(10, 0)
        )
        self.presets_var = tk.BooleanVar(value=record.get("presets", False) if record else False)
        ttk.Checkbutton(
            frame, variable=self.presets_var
        ).grid(row=4, column=1, sticky="w", padx=5, pady=(10, 5))

        # Defaults on for a new account: muting a channel out of your own
        # wedge is ordinary use of a monitor mix, and it is what every
        # account could do before this permission existed. Unchecking it
        # leaves the performer level and pan only.
        ttk.Label(frame, text="Mute Access").grid(row=5, column=0, sticky="w")
        self.mute_var = tk.BooleanVar(value=record.get("mute", True) if record else True)
        ttk.Checkbutton(
            frame, variable=self.mute_var
        ).grid(row=5, column=1, sticky="w", padx=5, pady=(0, 5))

        ttk.Label(frame, text="Aux Access").grid(
            row=6, column=0, sticky="nw", pady=(10, 0)
        )

        current_aux = record["aux"] if record else ALL_AUX
        all_selected = current_aux == ALL_AUX
        selected_names = set() if all_selected else set(current_aux or [])

        aux_frame = ttk.Frame(frame)
        aux_frame.grid(row=6, column=1, sticky="w", padx=5, pady=(10, 5))

        self.all_aux_var = tk.BooleanVar(value=all_selected)
        ttk.Checkbutton(
            aux_frame, text="All", variable=self.all_aux_var,
            command=self.on_all_aux_toggled
        ).grid(
            row=0, column=0, columnspan=AUX_CHECKBOXES_PER_ROW,
            sticky="w", pady=(0, 4)
        )

        self.aux_vars = {}
        self.aux_checkbuttons = {}
        checkbox_state = "disabled" if all_selected else "normal"

        for i, name in enumerate(aux_options):
            row = 1 + i // AUX_CHECKBOXES_PER_ROW
            column = i % AUX_CHECKBOXES_PER_ROW

            var = tk.BooleanVar(value=name in selected_names)
            self.aux_vars[name] = var

            checkbutton = ttk.Checkbutton(
                aux_frame, text=name, variable=var, state=checkbox_state
            )
            checkbutton.grid(row=row, column=column, sticky="w", padx=(0, 12), pady=2)
            self.aux_checkbuttons[name] = checkbutton

        btn_bar = ttk.Frame(frame)
        btn_bar.grid(row=7, column=0, columnspan=2, pady=(15, 0))

        ttk.Button(btn_bar, text="Save", command=self.save).pack(side="left", padx=5)
        ttk.Button(
            btn_bar, text="Cancel", command=self.window.destroy
        ).pack(side="left")

    def on_all_aux_toggled(self):
        state = "disabled" if self.all_aux_var.get() else "normal"

        for checkbutton in self.aux_checkbuttons.values():
            checkbutton.config(state=state)

    def save(self):
        username = self.username or self.username_entry.get().strip()
        password = self.password_entry.get()
        snapshot = self.snapshot_combo.get().strip() or ALL_SNAPSHOTS

        if self.all_aux_var.get():
            aux = ALL_AUX
        else:
            aux = [name for name, var in self.aux_vars.items() if var.get()]

        if not username:
            messagebox.showerror(
                "Missing username", "Username is required", parent=self.window
            )
            return

        if not self.username:
            if self.user_store.get(username):
                messagebox.showerror(
                    "Duplicate user", f"User '{username}' already exists",
                    parent=self.window
                )
                return

            if not password:
                messagebox.showerror(
                    "Missing password", "Password is required for new users",
                    parent=self.window
                )
                return

        self.user_store.save_user(
            username, password, snapshot, aux,
            presets=self.presets_var.get(), mute=self.mute_var.get()
        )

        if self.on_saved:
            self.on_saved(username)

        self.window.destroy()
