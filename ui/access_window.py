import tkinter as tk
from tkinter import messagebox, ttk

from services.user_store import ALL_AUX, ALL_SNAPSHOTS

AUX_CHECKBOXES_PER_ROW = 5


def format_aux(aux):
    if aux == ALL_AUX:
        return ALL_AUX

    if not aux:
        return "(none)"

    return ", ".join(aux)


class AccessWindow:
    def __init__(self, master, user_store, get_worker, get_hidden_auxes=None):
        self.user_store = user_store
        self.get_worker = get_worker
        self.get_hidden_auxes = get_hidden_auxes or (lambda: set())

        self.window = tk.Toplevel(master)
        self.window.title("Accounts")
        self.window.geometry("530x360")

        self.build_ui()
        self.refresh_list()

    def build_ui(self):
        columns = ("username", "snapshot", "aux", "presets")
        self.tree = ttk.Treeview(
            self.window, columns=columns, show="headings", height=10
        )
        self.tree.heading("username", text="Username")
        self.tree.heading("snapshot", text="Snapshot Access")
        self.tree.heading("aux", text="Aux Access")
        self.tree.heading("presets", text="Preset Access")
        self.tree.column("username", width=140)
        self.tree.column("snapshot", width=150)
        self.tree.column("aux", width=110)
        self.tree.column("presets", width=90, anchor="center")
        self.tree.pack(fill="both", expand=True, padx=10, pady=10)
        self.tree.bind("<Double-1>", lambda event: self.edit_user())

        btn_bar = ttk.Frame(self.window, padding=(10, 0, 10, 10))
        btn_bar.pack(fill="x")

        ttk.Button(btn_bar, text="New", command=self.new_user).pack(side="left")
        ttk.Button(
            btn_bar, text="Edit", command=self.edit_user
        ).pack(side="left", padx=5)
        ttk.Button(btn_bar, text="Delete", command=self.delete_user).pack(side="left")

    def refresh_list(self):
        for row in self.tree.get_children():
            self.tree.delete(row)

        for username, record in self.user_store.list_users():
            self.tree.insert(
                "", "end", iid=username,
                values=(
                    username, record["snapshot"], format_aux(record["aux"]),
                    "Yes" if record.get("presets", False) else "No",
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
            on_saved=self.refresh_list
        )

    def edit_user(self):
        selected = self.tree.selection()

        if not selected:
            return

        UserEditDialog(
            self.window, self.user_store,
            self._known_snapshots(), self._known_auxes(),
            username=selected[0], on_saved=self.refresh_list
        )

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
        self.username_entry.grid(row=0, column=1, padx=5, pady=5)

        if username:
            self.username_entry.insert(0, username)
            self.username_entry.config(state="disabled")

        ttk.Label(frame, text="Password").grid(row=1, column=0, sticky="w")
        self.password_entry = ttk.Entry(frame, width=25, show="*")
        self.password_entry.grid(row=1, column=1, padx=5, pady=5)

        if username:
            ttk.Label(
                frame, text="(leave blank to keep current password)",
                font=("TkDefaultFont", 8)
            ).grid(row=2, column=0, columnspan=2, sticky="w")

        ttk.Label(frame, text="Snapshot Access").grid(
            row=3, column=0, sticky="w", pady=(10, 0)
        )
        self.snapshot_combo = ttk.Combobox(frame, width=23, values=snapshot_options)
        self.snapshot_combo.grid(row=3, column=1, padx=5, pady=(10, 5))
        self.snapshot_combo.set(record["snapshot"] if record else ALL_SNAPSHOTS)

        ttk.Label(frame, text="Aux Access").grid(
            row=4, column=0, sticky="nw", pady=(10, 0)
        )

        current_aux = record["aux"] if record else ALL_AUX
        all_selected = current_aux == ALL_AUX
        selected_names = set() if all_selected else set(current_aux or [])

        aux_frame = ttk.Frame(frame)
        aux_frame.grid(row=4, column=1, sticky="w", padx=5, pady=(10, 5))

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

        self.presets_var = tk.BooleanVar(value=record.get("presets", False) if record else False)
        ttk.Checkbutton(
            frame, text="Preset Access", variable=self.presets_var
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(10, 0))

        btn_bar = ttk.Frame(frame)
        btn_bar.grid(row=6, column=0, columnspan=2, pady=(15, 0))

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
            username, password, snapshot, aux, presets=self.presets_var.get()
        )

        if self.on_saved:
            self.on_saved()

        self.window.destroy()
