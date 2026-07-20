import tkinter as tk
from tkinter import messagebox, ttk

from services.user_store import ALL_AUX, ALL_SNAPSHOTS


class AccessWindow:
    def __init__(self, master, user_store, get_worker):
        self.user_store = user_store
        self.get_worker = get_worker

        self.window = tk.Toplevel(master)
        self.window.title("Access")
        self.window.geometry("440x360")

        self.build_ui()
        self.refresh_list()

    def build_ui(self):
        columns = ("username", "snapshot", "aux")
        self.tree = ttk.Treeview(
            self.window, columns=columns, show="headings", height=10
        )
        self.tree.heading("username", text="Username")
        self.tree.heading("snapshot", text="Snapshot Access")
        self.tree.heading("aux", text="Aux Access")
        self.tree.column("username", width=140)
        self.tree.column("snapshot", width=150)
        self.tree.column("aux", width=110)
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
                values=(username, record["snapshot"], record["aux"])
            )

    def _known_snapshots(self):
        worker = self.get_worker()
        names = set(worker.snapshot_names.values()) if worker else set()
        return [ALL_SNAPSHOTS] + sorted(names)

    def _known_auxes(self):
        worker = self.get_worker()

        if not worker or not worker.loaded:
            return [ALL_AUX]

        from ui.main_window import build_aux_list
        return [ALL_AUX] + [name for _, name in build_aux_list(worker)]

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

        ttk.Label(frame, text="Aux Access").grid(row=4, column=0, sticky="w")
        self.aux_combo = ttk.Combobox(frame, width=23, values=aux_options)
        self.aux_combo.grid(row=4, column=1, padx=5, pady=5)
        self.aux_combo.set(record["aux"] if record else ALL_AUX)

        btn_bar = ttk.Frame(frame)
        btn_bar.grid(row=5, column=0, columnspan=2, pady=(15, 0))

        ttk.Button(btn_bar, text="Save", command=self.save).pack(side="left", padx=5)
        ttk.Button(
            btn_bar, text="Cancel", command=self.window.destroy
        ).pack(side="left")

    def save(self):
        username = self.username or self.username_entry.get().strip()
        password = self.password_entry.get()
        snapshot = self.snapshot_combo.get().strip() or ALL_SNAPSHOTS
        aux = self.aux_combo.get().strip() or ALL_AUX

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

        self.user_store.save_user(username, password, snapshot, aux)

        if self.on_saved:
            self.on_saved()

        self.window.destroy()
