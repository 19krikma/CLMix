import tkinter as tk
from tkinter import messagebox, ttk

from services.user_store import ALL_AUX


class PresetsWindow:
    """The Presets page: every preset saved from the phones, whoever owns
    it, and the operator's controls over each one.

    Presets are only ever created and loaded from a phone. A preset is
    private to the account that saved it until it is made public or
    shared with named accounts - by its owner from the phone, or by the
    operator here, who can do that, or delete it, for any preset (see
    PresetStore). Each preset belongs to the aux it was saved from and
    only loads back onto that aux.

    The page has sections in the sidebar (see MainWindow.open_presets_
    window); List is the only one so far.
    """

    SECTIONS = ("List",)

    # Shown for presets saved before owners existed.
    NO_OWNER = "—"

    def __init__(self, master, preset_store, user_store, aux_name_for=None):
        self.preset_store = preset_store
        self.user_store = user_store
        # The aux's name on the console now, or None with no console to
        # ask - the name stored with the preset is shown instead.
        self.aux_name_for = aux_name_for or (lambda index: None)

        # A page inside the main window, not a window of its own -
        # MainWindow packs this frame into its page area.
        self.window = ttk.Frame(master)

        # Which store revision the list was last drawn from - MainWindow
        # compares it against the store's own to pick up a phone's change.
        self.seen_revision = None

        self.sections = {"List": self.build_list_section()}
        self.refresh_list()

    def show_section(self, name):
        for section_name, frame in self.sections.items():
            if section_name == name:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()

    def build_list_section(self):
        frame = ttk.Frame(self.window)

        columns = ("name", "aux", "owner", "visibility", "shared", "channels")
        self.tree = ttk.Treeview(
            frame, columns=columns, show="headings", height=10,
            selectmode="browse"
        )
        self.tree.heading("name", text="Name")
        self.tree.heading("aux", text="Aux")
        self.tree.heading("owner", text="Owner")
        self.tree.heading("visibility", text="Visibility")
        self.tree.heading("shared", text="Shared With")
        self.tree.heading("channels", text="Channels")
        self.tree.column("name", width=180)
        self.tree.column("aux", width=140)
        self.tree.column("owner", width=120)
        self.tree.column("visibility", width=80, anchor="center")
        self.tree.column("shared", width=180)
        self.tree.column("channels", width=80, anchor="center")
        self.tree.pack(fill="both", expand=True, padx=10, pady=10)
        self.tree.bind("<<TreeviewSelect>>", lambda event: self.update_buttons())
        self.tree.bind("<Double-1>", lambda event: self.share_preset())

        btn_bar = ttk.Frame(frame, padding=(10, 0, 10, 10))
        btn_bar.pack(fill="x")

        # Width fixed to the longer of its two labels, so the row does not
        # shuffle as the selection moves between public and private.
        self.public_button = ttk.Button(
            btn_bar, text="Make Public", width=13, command=self.toggle_public
        )
        self.public_button.pack(side="left")
        self.share_button = ttk.Button(
            btn_bar, text="Share…", command=self.share_preset
        )
        self.share_button.pack(side="left", padx=5)
        self.delete_button = ttk.Button(
            btn_bar, text="Delete", command=self.delete_preset
        )
        self.delete_button.pack(side="left")
        ttk.Button(
            btn_bar, text="Refresh", command=self.refresh_list
        ).pack(side="right")

        return frame

    def _owner_label(self, owner):
        if owner is None:
            return self.NO_OWNER
        if self.user_store.get(owner) is None:
            return f"{owner} (deleted)"
        return owner

    def aux_name(self, preset):
        """The name of the aux a preset was saved from - as the console has
        it now if it is there to say, else as it was then - or None for a
        preset from before auxes were recorded."""
        aux = preset["aux"]

        if aux is None:
            return None

        return self.aux_name_for(aux) or preset["aux_name"] or f"Aux {aux}"

    def aux_label(self, preset):
        name = self.aux_name(preset)

        if name is None:
            return "Any (older preset)"

        number = f"Aux {preset['aux']}"
        return name if name == number else f"{name} ({number})"

    def refresh_list(self):
        selected = self.selected_id()

        for row in self.tree.get_children():
            self.tree.delete(row)

        self.seen_revision = self.preset_store.revision

        for preset in self.preset_store.list_presets():
            self.tree.insert("", "end", iid=preset["id"], values=(
                preset["name"],
                self.aux_label(preset),
                self._owner_label(preset["owner"]),
                "Public" if preset["public"] else "Private",
                ", ".join(preset["shared_with"]),
                len(preset.get("channels", [])),
            ))

        if selected and self.tree.exists(selected):
            self.tree.selection_set(selected)

        self.update_buttons()

    def selected_id(self):
        selected = self.tree.selection()
        return selected[0] if selected else None

    def selected_preset(self):
        preset_id = self.selected_id()
        return self.preset_store.get(preset_id) if preset_id else None

    def update_buttons(self):
        preset = self.selected_preset()
        state = "normal" if preset else "disabled"

        for button in (self.public_button, self.share_button, self.delete_button):
            button.config(state=state)

        self.public_button.config(
            text="Make Private" if preset and preset["public"] else "Make Public"
        )

    def toggle_public(self):
        preset = self.selected_preset()

        if preset is None:
            return

        self.preset_store.set_public(preset["id"], not preset["public"])
        self.refresh_list()

    def share_preset(self):
        preset = self.selected_preset()

        if preset is None:
            return

        ShareDialog(
            self.window, preset, self.user_store, self.aux_name(preset),
            on_saved=lambda users: self._save_shares(preset["id"], users)
        )

    def _save_shares(self, preset_id, users):
        self.preset_store.set_shared_with(preset_id, users)
        self.refresh_list()

    def delete_preset(self):
        preset = self.selected_preset()

        if preset is None:
            return

        owner = preset["owner"]
        whose = f" (owned by {owner})" if owner else ""

        if messagebox.askyesno(
            "Delete preset", f"Delete preset '{preset['name']}'{whose}?",
            parent=self.window
        ):
            self.preset_store.delete_preset(preset["id"])
            self.refresh_list()


class ShareDialog:
    """Picks which accounts a preset is shared with: one tick box per
    account that has preset access and may use the preset's aux, since
    nobody else could load it. An account already on the list that has
    since lost either is still offered, ticked, so saving here does not
    quietly drop it."""

    def __init__(self, master, preset, user_store, aux_name, on_saved):
        self.on_saved = on_saved

        self.window = tk.Toplevel(master)
        self.window.title("Share Preset")
        self.window.transient(master)
        self.window.grab_set()

        frame = ttk.Frame(self.window, padding=15)
        frame.pack(fill="both", expand=True)

        where = f" ({aux_name})" if aux_name else ""
        ttk.Label(
            frame, text=f"Share '{preset['name']}'{where} with:"
        ).pack(anchor="w", pady=(0, 8))

        def can_use(record):
            return record.get("presets", False) and (
                aux_name is None or record.get("aux") == ALL_AUX
                or aux_name in record.get("aux", [])
            )

        shared = set(preset["shared_with"])
        candidates = {
            name: "" for name, record in user_store.list_users()
            if name != preset["owner"] and can_use(record)
        }

        for name in shared - set(candidates):
            candidates[name] = " (can't load it)"

        self.vars = {}

        if not candidates:
            ttk.Label(
                frame, text="No other account has Preset Access to this aux.",
                foreground="gray"
            ).pack(anchor="w")

        for name in sorted(candidates):
            var = tk.BooleanVar(value=name in shared)
            self.vars[name] = var
            ttk.Checkbutton(
                frame, text=name + candidates[name], variable=var
            ).pack(anchor="w", pady=2)

        btn_bar = ttk.Frame(frame)
        btn_bar.pack(fill="x", pady=(12, 0))

        ttk.Button(btn_bar, text="Cancel", command=self.window.destroy).pack(side="right")
        ttk.Button(
            btn_bar, text="Save", style="Accent.TButton", command=self.save
        ).pack(side="right", padx=(0, 5))

        self.window.bind("<Escape>", lambda event: self.window.destroy())

    def save(self):
        self.on_saved([name for name, var in self.vars.items() if var.get()])
        self.window.destroy()
