from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from services.backup_store import BackupStore
from ui.logs_window import open_folder

CHECK_MARK = "✓"

# Only the path column stretches - everything else is fixed-width, so
# shrinking the window eats into the path column instead of a checkmark
# column.
PATH_COLUMN_MIN_WIDTH = 150
DATETIME_COLUMN_WIDTH = 190
SOURCE_COLUMN_WIDTH = 70

# Padding/border/scrollbar allowance beyond the columns' own widths, so
# the window opens exactly wide enough to show every column in full.
WINDOW_CHROME_MARGIN = 60


class BackupWindow:
    """Lets the operator choose which settings to snapshot, capture one,
    and see what's already been captured, restore from one, or remove
    one - mirroring PresetsWindow's relationship to PresetStore.
    """

    SOURCE_KEYS = list(BackupStore.SOURCES)

    def __init__(self, master, backup_store, user_store, preset_store,
                 settings, save_settings, on_restored=None):
        self.backup_store = backup_store
        self.user_store = user_store
        self.preset_store = preset_store
        self.settings = settings
        self.save_settings = save_settings
        self.on_restored = on_restored

        self.window = tk.Toplevel(master)
        self.window.title("Backup")

        min_width = (
            PATH_COLUMN_MIN_WIDTH + DATETIME_COLUMN_WIDTH +
            len(self.SOURCE_KEYS) * SOURCE_COLUMN_WIDTH +
            WINDOW_CHROME_MARGIN
        )
        self.window.minsize(min_width, 300)
        self.window.geometry(f"{min_width}x400")

        self.include_vars = {
            key: tk.BooleanVar(value=True) for key in self.SOURCE_KEYS
        }
        self._path_entry = None

        self.build_ui()
        self.refresh_list()

    def build_ui(self):
        top_bar = ttk.Frame(self.window, padding=10)
        top_bar.pack(fill="x")

        checks_row = ttk.Frame(top_bar)
        checks_row.pack(anchor="w", pady=(8, 0))

        ttk.Label(checks_row, text="Include:").pack(side="left", padx=(0, 8))

        for key in self.SOURCE_KEYS:
            ttk.Checkbutton(
                checks_row,
                text=BackupStore.LABELS[key],
                variable=self.include_vars[key]
            ).pack(side="left", padx=(0, 10))

        dir_row = ttk.Frame(top_bar)
        dir_row.pack(anchor="w", pady=(8, 0))

        ttk.Button(
            dir_row, text="Change", command=self.change_backup_dir
        ).pack(side="left")

        ttk.Button(
            dir_row, text="Open Folder", command=self.open_backup_dir
        ).pack(side="left", padx=(6, 0))

        self.backup_dir_label = ttk.Label(dir_row, text="", foreground="#888888")
        self.backup_dir_label.pack(side="left", padx=(10, 0))

        list_header = ttk.Frame(self.window, padding=(10, 0))
        list_header.pack(fill="x")

        ttk.Button(
            list_header, text="Backup Now", command=self.backup_now
        ).pack(side="left")

        self.last_backup_label = ttk.Label(list_header, text="", foreground="#888888")
        self.last_backup_label.pack(side="left", padx=(10, 0))

        self.remove_btn = ttk.Button(
            list_header, text="Remove", command=self.remove_selected, state="disabled"
        )
        self.remove_btn.pack(side="right")

        self.restore_btn = ttk.Button(
            list_header, text="Restore", command=self.restore_selected, state="disabled"
        )
        self.restore_btn.pack(side="right", padx=(0, 6))

        columns = ("path", "datetime", *self.SOURCE_KEYS)
        self.tree = ttk.Treeview(
            self.window, columns=columns, show="headings", height=10
        )
        self.tree.heading("path", text="Path")
        self.tree.column("path", width=PATH_COLUMN_MIN_WIDTH, minwidth=PATH_COLUMN_MIN_WIDTH)

        self.tree.heading("datetime", text="Date and Time")
        self.tree.column("datetime", width=DATETIME_COLUMN_WIDTH, stretch=False)

        for key in self.SOURCE_KEYS:
            self.tree.heading(key, text=BackupStore.LABELS[key])
            self.tree.column(key, width=SOURCE_COLUMN_WIDTH, anchor="center", stretch=False)

        self.tree.pack(fill="both", expand=True, padx=10, pady=(4, 10))

        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Motion>", self._on_tree_motion)
        self.tree.bind("<<TreeviewSelect>>", lambda event: self._update_action_buttons())

    def backup_now(self):
        include = [key for key in self.SOURCE_KEYS if self.include_vars[key].get()]

        if not include:
            messagebox.showwarning(
                "Nothing selected", "Choose at least one setting to back up.",
                parent=self.window
            )
            return

        self.backup_store.backup_now(include=include)
        self.refresh_list()

    def change_backup_dir(self):
        chosen = filedialog.askdirectory(
            title="Choose Backup Folder",
            initialdir=str(self.backup_store.resolve_dir()),
            parent=self.window
        )

        if not chosen:
            return

        self.backup_store.set_backups_dir(chosen)
        self.settings["backup_dir"] = chosen
        self.save_settings()

        self.refresh_list()

    def open_backup_dir(self):
        open_folder(self.backup_store.resolve_dir())

    def refresh_list(self):
        self._hide_path_entry()

        self.backup_dir_label.config(text=str(self.backup_store.resolve_dir()))

        for row in self.tree.get_children():
            self.tree.delete(row)

        backups = self.backup_store.list_backups()

        for backup in backups:
            marks = [
                CHECK_MARK if key in backup.included else ""
                for key in self.SOURCE_KEYS
            ]
            self.tree.insert(
                "", "end", iid=str(backup.path),
                values=(str(backup.path), self._format(backup.created_at), *marks)
            )

        self.last_backup_label.config(
            text=self._format(backups[0].created_at) if backups else "No backups yet"
        )

        self._update_action_buttons()

    def _update_action_buttons(self):
        state = "normal" if self.tree.selection() else "disabled"
        self.restore_btn.config(state=state)
        self.remove_btn.config(state=state)

    def selected_backup_path(self):
        selection = self.tree.selection()
        return Path(selection[0]) if selection else None

    def restore_selected(self):
        path = self.selected_backup_path()

        if path is not None:
            self.restore_backup(path)

    def remove_selected(self):
        path = self.selected_backup_path()

        if path is not None:
            self.remove_backup(path)

    def _column_at(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return None, None

        row_id = self.tree.identify_row(event.y)

        if not row_id:
            return None, None

        column_index = int(self.tree.identify_column(event.x).lstrip("#")) - 1
        columns = self.tree["columns"]

        if not (0 <= column_index < len(columns)):
            return None, None

        return row_id, columns[column_index]

    def _on_tree_motion(self, event):
        _row_id, column = self._column_at(event)
        self.tree.configure(cursor="xterm" if column == "path" else "")

    def _on_tree_click(self, event):
        row_id, column = self._column_at(event)

        if column == "path":
            self._show_path_entry(row_id)
        else:
            self._hide_path_entry()

    def _show_path_entry(self, row_id):
        # Treeview cell text isn't selectable/copyable on its own - an
        # Entry placed exactly over the cell, pre-selected, gives a
        # normal text field the operator can Ctrl+C from (or right-click
        # to copy) without being able to edit the path itself.
        self._hide_path_entry()

        bbox = self.tree.bbox(row_id, column="path")

        if not bbox:
            return

        x, y, width, height = bbox

        entry = ttk.Entry(self.tree)
        entry.insert(0, self.tree.set(row_id, "path"))
        entry.configure(state="readonly")
        entry.place(x=x, y=y, width=width, height=height)

        entry.selection_range(0, tk.END)
        entry.focus_set()

        entry.bind("<FocusOut>", lambda event: self._hide_path_entry())
        entry.bind("<Escape>", lambda event: self._hide_path_entry())
        entry.bind("<Return>", lambda event: self._hide_path_entry())

        self._path_entry = entry

    def _hide_path_entry(self):
        if self._path_entry is not None:
            self._path_entry.destroy()
            self._path_entry = None

    def restore_backup(self, path):
        info = next(
            (b for b in self.backup_store.list_backups() if b.path == path), None
        )

        if info is None or not info.included:
            messagebox.showinfo(
                "Nothing to restore", "This backup doesn't contain any settings.",
                parent=self.window
            )
            return

        names = ", ".join(sorted(BackupStore.LABELS[key] for key in info.included))

        if not messagebox.askyesno(
            "Restore backup",
            f"This will overwrite the current {names} with the backup from "
            f"{self._format(info.created_at)}.\n\nThis cannot be undone. Continue?",
            parent=self.window
        ):
            return

        restored = self.backup_store.restore(path)

        if "users" in restored:
            self.user_store.reload()

        if "presets" in restored:
            self.preset_store.reload()

        if self.on_restored:
            self.on_restored(restored)

        restored_names = ", ".join(sorted(BackupStore.LABELS[key] for key in restored))
        messagebox.showinfo(
            "Restore complete", f"Restored: {restored_names}", parent=self.window
        )

    def remove_backup(self, path):
        if not messagebox.askyesno(
            "Remove backup", "Remove this backup? This cannot be undone.",
            parent=self.window
        ):
            return

        self.backup_store.remove_backup(path)
        self.refresh_list()

    @staticmethod
    def _format(dt):
        return dt.strftime("%Y-%m-%d %H:%M:%S")
