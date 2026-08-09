import tkinter as tk
from tkinter import messagebox, ttk


class PresetsWindow:
    """Lists presets saved from the phone apps (name + channel count) and
    lets the operator delete them. Presets are only ever created/loaded
    from a phone - this window just manages the saved list, mirroring
    AccessWindow's relationship to UserStore.
    """

    def __init__(self, master, preset_store):
        self.preset_store = preset_store

        self.window = tk.Toplevel(master)
        self.window.title("Presets")
        self.window.geometry("360x360")

        self.build_ui()
        self.refresh_list()

    def build_ui(self):
        columns = ("name", "channels")
        self.tree = ttk.Treeview(
            self.window, columns=columns, show="headings", height=10
        )
        self.tree.heading("name", text="Name")
        self.tree.heading("channels", text="Channels")
        self.tree.column("name", width=220)
        self.tree.column("channels", width=90, anchor="center")
        self.tree.pack(fill="both", expand=True, padx=10, pady=10)

        btn_bar = ttk.Frame(self.window, padding=(10, 0, 10, 10))
        btn_bar.pack(fill="x")

        ttk.Button(btn_bar, text="Delete", command=self.delete_preset).pack(side="left")
        ttk.Button(
            btn_bar, text="Refresh", command=self.refresh_list
        ).pack(side="left", padx=5)

    def refresh_list(self):
        for row in self.tree.get_children():
            self.tree.delete(row)

        for name, data in self.preset_store.list_presets():
            channel_count = len(data.get("channels", []))
            self.tree.insert("", "end", iid=name, values=(name, channel_count))

    def delete_preset(self):
        selected = self.tree.selection()

        if not selected:
            return

        name = selected[0]

        if messagebox.askyesno(
            "Delete preset", f"Delete preset '{name}'?", parent=self.window
        ):
            self.preset_store.delete_preset(name)
            self.refresh_list()
