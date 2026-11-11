import copy
import tkinter as tk
from tkinter import colorchooser, messagebox, ttk

from services import sos_options

# The muted gray the rest of the desktop UI uses for secondary text.
MUTED_FG = "#888888"

DEFAULT_LIST = "Default"


def text_color_for(background):
    """White or black, whichever reads on this "#rrggbb"."""
    red, green, blue = (int(background[i:i + 2], 16) for i in (1, 3, 5))
    luminance = 0.299 * red + 0.587 * green + 0.114 * blue
    return "#000000" if luminance > 150 else "#ffffff"


class SosSection:
    """Settings > General's SOS Messages: what a phone's SOS button offers.

    One default list, used on any snapshot without a list of its own, and
    any number of custom lists, each tied to the snapshots it replaces the
    default on. Every change is saved as it is made and reaches connected
    phones on their next push - see RemoteServer._push_sos_options.

    `get_config` returns the normalized config (services/sos_options.py);
    `set_config(config)` stores and saves a new one. `get_snapshots`
    returns the snapshot names the console has told us of.
    """

    def __init__(self, parent, get_config, set_config, get_snapshots):
        self.get_config = get_config
        self.set_config = set_config
        self.get_snapshots = get_snapshots

        self.frame = ttk.Frame(parent, padding=15)
        self.frame.pack(fill="x")

        ttk.Label(
            self.frame, text="SOS Messages", font=("TkDefaultFont", 10, "bold")
        ).pack(anchor="w")

        ttk.Label(
            self.frame,
            text="The choices a phone's SOS button offers. A custom list "
                 "replaces the default while the console is on one of its "
                 "snapshots.",
            foreground=MUTED_FG, wraplength=600, justify="left"
        ).pack(anchor="w", pady=(2, 8))

        # Which list is being edited, and what to do with lists.
        list_row = ttk.Frame(self.frame)
        list_row.pack(fill="x")

        ttk.Label(list_row, text="List").pack(side="left")
        self.list_combo = ttk.Combobox(list_row, width=24, state="readonly")
        self.list_combo.pack(side="left", padx=(8, 8))
        self.list_combo.bind("<<ComboboxSelected>>",
                             lambda event: self._show_list())

        ttk.Button(list_row, text="New Custom List",
                   command=self._new_list).pack(side="left")
        self.rename_button = ttk.Button(list_row, text="Rename",
                                        command=self._rename_list)
        self.rename_button.pack(side="left", padx=(6, 0))
        self.delete_list_button = ttk.Button(list_row, text="Delete List",
                                             command=self._delete_list)
        self.delete_list_button.pack(side="left", padx=(6, 0))

        # Where the list applies.
        snapshot_row = ttk.Frame(self.frame)
        snapshot_row.pack(fill="x", pady=(8, 0))

        self.snapshots_label = ttk.Label(
            snapshot_row, text="", foreground=MUTED_FG,
            wraplength=480, justify="left"
        )
        self.snapshots_label.pack(side="left")
        self.snapshots_button = ttk.Button(
            snapshot_row, text="Choose Snapshots", command=self._choose_snapshots
        )
        self.snapshots_button.pack(side="right")

        # The list's messages, each row in its own button colour.
        body = ttk.Frame(self.frame)
        body.pack(fill="x", pady=(8, 0))
        body.columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(body, show="tree", height=6,
                                 selectmode="browse")
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<Double-1>", lambda event: self._edit_option())
        self.tree.bind("<<TreeviewSelect>>",
                       lambda event: self._update_buttons())

        buttons = ttk.Frame(body)
        buttons.grid(row=0, column=1, sticky="n", padx=(10, 0))

        def button(text, command):
            # A fixed width, so the column keeps its size when Restore
            # Defaults is hidden for a custom list.
            widget = ttk.Button(buttons, text=text, command=command, width=16)
            widget.pack(fill="x", pady=(0, 4))
            return widget

        self.add_button = button("Add", self._add_option)
        self.edit_button = button("Edit", self._edit_option)
        self.remove_button = button("Remove", self._remove_option)
        self.up_button = button("Move Up", lambda: self._move_option(-1))
        self.down_button = button("Move Down", lambda: self._move_option(1))
        self.reset_button = button("Restore Defaults", self._restore_defaults)

        self.refresh()

    # --- Reading the config ---

    def _list_names(self):
        return [DEFAULT_LIST] + [c["name"] for c in self.get_config()["custom"]]

    def _selected_index(self):
        """None for the default list, else the custom list's index."""
        names = self._list_names()
        name = self.list_combo.get()

        if name not in names or name == DEFAULT_LIST:
            return None
        return names.index(name) - 1

    def _options(self, config):
        index = self._selected_index()
        return config["default"] if index is None \
            else config["custom"][index]["options"]

    def _selected_option(self):
        selected = self.tree.selection()
        return int(selected[0]) if selected else None

    # --- Drawing ---

    def refresh(self, select_list=None):
        """Redraws from the saved config - after an edit, or a restored
        backup. Stays on the list being edited unless told otherwise."""
        names = self._list_names()
        wanted = select_list or self.list_combo.get()

        self.list_combo.configure(values=names)
        self.list_combo.set(wanted if wanted in names else DEFAULT_LIST)
        self._show_list()

    def _show_list(self, select_option=None):
        config = self.get_config()
        index = self._selected_index()
        options = self._options(config)

        self.tree.delete(*self.tree.get_children())

        # A swatch of the button colour beside each message, rather than
        # the whole row in it: the selection highlight would hide that.
        # Kept on self, or Tk drops the images as soon as this returns.
        self._swatches = []

        for row, option in enumerate(options):
            swatch = tk.PhotoImage(width=28, height=16)
            swatch.put(option["color"], to=(0, 0, 28, 16))
            self._swatches.append(swatch)
            self.tree.insert("", "end", iid=str(row),
                             text="  " + option["text"], image=swatch)

        if select_option is not None and 0 <= select_option < len(options):
            self.tree.selection_set(str(select_option))
            self.tree.see(str(select_option))

        if index is None:
            self.snapshots_label.configure(
                text="Used on every snapshot without a custom list."
            )
            self.snapshots_button.state(["disabled"])
        else:
            snapshots = config["custom"][index]["snapshots"]
            self.snapshots_label.configure(
                text="Used on: " + ", ".join(snapshots) if snapshots
                else "Not tied to any snapshot yet, so phones never see it."
            )
            self.snapshots_button.state(["!disabled"])

        custom = index is not None
        for widget in (self.rename_button, self.delete_list_button):
            widget.state(["!disabled"] if custom else ["disabled"])

        if custom:
            self.reset_button.pack_forget()
        elif not self.reset_button.winfo_ismapped():
            self.reset_button.pack(fill="x", pady=(0, 4))

        self._update_buttons()

    def _update_buttons(self):
        row = self._selected_option()
        count = len(self.tree.get_children())

        def enable(widget, on):
            widget.state(["!disabled"] if on else ["disabled"])

        enable(self.add_button, count < sos_options.MAX_OPTIONS)
        enable(self.edit_button, row is not None)
        enable(self.remove_button, row is not None)
        enable(self.up_button, row is not None and row > 0)
        enable(self.down_button, row is not None and row < count - 1)

    # --- Changing it ---

    def _change(self, edit, select_list=None, select_option=None):
        """Applies edit(config) to a copy of the config, saves it and
        redraws. The copy is what keeps the server's view whole: it only
        ever sees the old config or the new one, never one mid-edit."""
        config = copy.deepcopy(self.get_config())
        edit(config)
        self.set_config(sos_options.normalize(config))

        if select_list is not None:
            self.refresh(select_list=select_list)
        else:
            self._show_list(select_option=select_option)

    def _add_option(self):
        def save(text, color):
            options_count = len(self.tree.get_children())
            self._change(
                lambda config: self._options(config).append({
                    "id": sos_options.new_option_id(),
                    "text": text, "color": color,
                }),
                select_option=options_count
            )

        OptionDialog(self.frame, "Add SOS Message", "", sos_options.SOS_RED,
                     on_saved=save)

    def _edit_option(self):
        row = self._selected_option()
        if row is None:
            return

        option = self._options(self.get_config())[row]

        def save(text, color):
            def edit(config):
                self._options(config)[row].update(text=text, color=color)
            self._change(edit, select_option=row)

        OptionDialog(self.frame, "Edit SOS Message", option["text"],
                     option["color"], on_saved=save)

    def _remove_option(self):
        row = self._selected_option()
        if row is None:
            return

        self._change(lambda config: self._options(config).pop(row),
                     select_option=row - 1 if row > 0 else 0)

    def _move_option(self, step):
        row = self._selected_option()
        if row is None:
            return

        def edit(config):
            options = self._options(config)
            options.insert(row + step, options.pop(row))

        self._change(edit, select_option=row + step)

    def _restore_defaults(self):
        if not messagebox.askyesno(
            "SOS Messages",
            "Put the default list back to DEAD BATTERY, AUX NOT WORKING "
            "and NEED ASSISTANCE?", parent=self.frame
        ):
            return

        def edit(config):
            config["default"] = sos_options.default_options()

        self._change(edit)

    def _new_list(self):
        taken = set(self._list_names())

        def save(name, _color):
            if name in taken:
                messagebox.showerror(
                    "SOS Messages", f"There is already a list called {name}.",
                    parent=self.frame
                )
                return False

            def edit(config):
                # Starts as a copy of the default list, under new ids so
                # editing one never changes what the other's SOS says.
                config["custom"].append({
                    "name": name, "snapshots": [],
                    "options": [
                        dict(option, id=sos_options.new_option_id())
                        for option in config["default"]
                    ],
                })

            self._change(edit, select_list=name)
            return True

        OptionDialog(self.frame, "New Custom List", "", None,
                     on_saved=save, label="List name")

    def _rename_list(self):
        index = self._selected_index()
        if index is None:
            return

        old = self.get_config()["custom"][index]["name"]
        taken = set(self._list_names()) - {old}

        def save(name, _color):
            if name in taken:
                messagebox.showerror(
                    "SOS Messages", f"There is already a list called {name}.",
                    parent=self.frame
                )
                return False

            def edit(config):
                config["custom"][index]["name"] = name

            self._change(edit, select_list=name)
            return True

        OptionDialog(self.frame, "Rename List", old, None,
                     on_saved=save, label="List name")

    def _delete_list(self):
        index = self._selected_index()
        if index is None:
            return

        name = self.get_config()["custom"][index]["name"]
        if not messagebox.askyesno(
            "SOS Messages",
            f"Delete the {name} list? Its snapshots go back to the default "
            "list.", parent=self.frame
        ):
            return

        self._change(lambda config: config["custom"].pop(index),
                     select_list=DEFAULT_LIST)

    def _choose_snapshots(self):
        index = self._selected_index()
        if index is None:
            return

        config = self.get_config()
        mine = config["custom"][index]
        # Which other list has each snapshot, so ticking it here can say
        # it is being moved over.
        owners = {
            snapshot: custom["name"]
            for i, custom in enumerate(config["custom"]) if i != index
            for snapshot in custom["snapshots"]
        }

        def save(chosen):
            def edit(config):
                for i, custom in enumerate(config["custom"]):
                    if i == index:
                        custom["snapshots"] = chosen
                    else:
                        custom["snapshots"] = [
                            s for s in custom["snapshots"] if s not in chosen
                        ]

            self._change(edit)

        SnapshotDialog(
            self.frame, mine["name"], self.get_snapshots(),
            mine["snapshots"], owners, on_saved=save
        )


class OptionDialog:
    """One line of text, and a colour unless color is None - an SOS
    message, or a list's name. on_saved(text, color) may return False to
    keep the dialog open."""

    def __init__(self, master, title, text, color, on_saved, label="Message"):
        self.on_saved = on_saved
        self.color = color

        self.window = tk.Toplevel(master)
        self.window.title(title)
        self.window.transient(master.winfo_toplevel())
        self.window.resizable(False, False)

        frame = ttk.Frame(self.window, padding=15)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text=label).grid(row=0, column=0, sticky="w")
        self.entry = ttk.Entry(frame, width=32)
        self.entry.insert(0, text)
        self.entry.grid(row=0, column=1, sticky="ew", padx=(10, 0))
        self.entry.focus_set()
        self.entry.select_range(0, "end")

        if color is not None:
            ttk.Label(frame, text="Button colour").grid(
                row=1, column=0, sticky="w", pady=(10, 0)
            )
            color_row = ttk.Frame(frame)
            color_row.grid(row=1, column=1, sticky="w", padx=(10, 0),
                           pady=(10, 0))

            # How the button will look on the phone.
            self.preview = tk.Label(
                color_row, width=18, height=1,
                font=("TkDefaultFont", 10, "bold")
            )
            self.preview.pack(side="left")
            ttk.Button(color_row, text="Choose...",
                       command=self._pick_color).pack(side="left", padx=(8, 0))

            self.entry.bind("<KeyRelease>", lambda event: self._draw_preview())
            self._draw_preview()

        btn_bar = ttk.Frame(frame)
        btn_bar.grid(row=2, column=0, columnspan=2, sticky="e", pady=(14, 0))

        ttk.Button(btn_bar, text="Cancel",
                   command=self.window.destroy).pack(side="right")
        ttk.Button(btn_bar, text="Save", style="Accent.TButton",
                   command=self.save).pack(side="right", padx=(0, 5))

        self.window.bind("<Return>", lambda event: self.save())
        self.window.bind("<Escape>", lambda event: self.window.destroy())
        self.window.grab_set()

    def _draw_preview(self):
        text = sos_options.clean_text(self.entry.get()) or "SOS"
        self.preview.configure(text=text, bg=self.color,
                               fg=text_color_for(self.color))

    def _pick_color(self):
        _rgb, chosen = colorchooser.askcolor(
            self.color, parent=self.window, title="Button colour"
        )
        if chosen:
            self.color = sos_options.clean_color(chosen)
            self._draw_preview()

    def save(self):
        text = sos_options.clean_text(self.entry.get())

        if text is None:
            self.entry.focus_set()
            return

        if self.on_saved(text, self.color) is not False:
            self.window.destroy()


class SnapshotDialog:
    """Ticks the snapshots a custom SOS list is used on. Offers every
    snapshot the console has named, plus any already on the list, and a
    box for typing one in while the console is not connected."""

    def __init__(self, master, list_name, known, chosen, owners, on_saved):
        self.on_saved = on_saved
        self.owners = owners

        self.window = tk.Toplevel(master)
        self.window.title("Choose Snapshots")
        self.window.transient(master.winfo_toplevel())

        frame = ttk.Frame(self.window, padding=15)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text=f"Use the {list_name} list on:").pack(
            anchor="w", pady=(0, 8)
        )

        self.checks = ttk.Frame(frame)
        self.checks.pack(fill="x")
        self.vars = {}

        names = sorted(set(known) | set(chosen) | set(owners),
                       key=str.lower)
        for name in names:
            self._add_check(name, name in chosen)

        self.empty_label = ttk.Label(
            frame, foreground=MUTED_FG,
            text="The console has not listed its snapshots - connect to "
                 "it, or type a snapshot's name below."
        )
        if not names:
            self.empty_label.pack(anchor="w")

        add_row = ttk.Frame(frame)
        add_row.pack(fill="x", pady=(10, 0))
        self.add_entry = ttk.Entry(add_row, width=24)
        self.add_entry.pack(side="left")
        self.add_entry.bind("<Return>", lambda event: self._add_typed())
        ttk.Button(add_row, text="Add Snapshot",
                   command=self._add_typed).pack(side="left", padx=(6, 0))

        btn_bar = ttk.Frame(frame)
        btn_bar.pack(fill="x", pady=(14, 0))
        ttk.Button(btn_bar, text="Cancel",
                   command=self.window.destroy).pack(side="right")
        ttk.Button(btn_bar, text="Save", style="Accent.TButton",
                   command=self.save).pack(side="right", padx=(0, 5))

        self.window.bind("<Escape>", lambda event: self.window.destroy())
        self.window.grab_set()

    def _add_check(self, name, ticked):
        var = tk.BooleanVar(value=ticked)
        self.vars[name] = var
        owner = self.owners.get(name)
        caption = f"{name}  (moves it from {owner})" if owner else name
        ttk.Checkbutton(self.checks, text=caption, variable=var).pack(
            anchor="w", pady=2
        )

    def _add_typed(self):
        name = self.add_entry.get().strip()
        if not name:
            return

        if name in self.vars:
            self.vars[name].set(True)
        else:
            self.empty_label.pack_forget()
            self._add_check(name, True)

        self.add_entry.delete(0, "end")

    def save(self):
        self.on_saved([name for name, var in self.vars.items() if var.get()])
        self.window.destroy()
