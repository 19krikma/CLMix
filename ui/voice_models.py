import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from services import voice_to_text

# The muted gray the rest of the desktop UI uses for secondary text.
MUTED_FG = "#888888"

TITLE = "Voice to Text"

# Above this a model is slow to load, needs gigabytes of memory, and may
# not keep up with several channels at once - worth a word before it is
# downloaded.
LARGE_BYTES = 500 * 1000 * 1000


def size_text(size):
    if size >= 1e9:
        return f"{size / 1e9:.1f} GB"
    return f"{max(1, round(size / 1e6))} MB"


class ModelPanel:
    """Settings > Voice to Text's Speech Model: the models on this
    computer, which is in use, and Vosk's catalog to search by language
    and download from.

    `get_config` returns the voice config; `change(edit)` applies
    edit(config) to a copy and saves it.
    """

    # The catalog, fetched once per run of CLMix - shared by every panel,
    # since the section is built once but a restored backup redraws it.
    _catalog = None
    _catalog_error = None

    def __init__(self, parent, get_config, change, get_snapshots=lambda: [],
                 get_live_snapshot=lambda: None, engine=None):
        """get_snapshots returns the snapshot names the console has told
        of; get_live_snapshot the one last recalled; engine is the
        running VoiceEngine, asked which model it has loaded."""
        self.get_config = get_config
        self.change = change
        self.get_snapshots = get_snapshots
        self.get_live_snapshot = get_live_snapshot
        self.engine = engine
        self._download = None
        self._shown_catalog = []

        self.frame = ttk.LabelFrame(parent, text="Speech Model", padding=10)

        self.in_use_label = ttk.Label(self.frame, text="", wraplength=620,
                                      justify="left")
        self.in_use_label.pack(anchor="w")

        # --- On this computer ---
        ttk.Label(self.frame, text="On this computer",
                  font=("TkDefaultFont", 9, "bold")).pack(anchor="w",
                                                          pady=(10, 4))
        installed = ttk.Frame(self.frame)
        installed.pack(fill="x")
        installed.columnconfigure(0, weight=1)

        self.installed_tree = ttk.Treeview(
            installed, columns=("language", "size", "use"),
            show="tree headings", height=4, selectmode="browse"
        )
        tree = self.installed_tree
        tree.heading("#0", text="Model", anchor="w")
        tree.column("#0", width=280)
        tree.heading("language", text="Language", anchor="w")
        tree.column("language", width=140)
        tree.heading("size", text="Size", anchor="w")
        tree.column("size", width=80, stretch=False)
        tree.heading("use", text="Default", anchor="w")
        tree.column("use", width=70, stretch=False)
        tree.grid(row=0, column=0, sticky="nsew")
        tree.bind("<Double-1>", lambda event: self._use())
        self._installed_tree_help = ttk.Label(
            self.frame, foreground=MUTED_FG, wraplength=620, justify="left",
            text="The default model is used on every snapshot without one "
                 "of its own.")
        tree.bind("<<TreeviewSelect>>", lambda event: self._update_buttons())

        buttons = ttk.Frame(installed)
        buttons.grid(row=0, column=1, sticky="n", padx=(10, 0))

        def button(master, text, command):
            widget = ttk.Button(master, text=text, command=command, width=16)
            widget.pack(fill="x", pady=(0, 4))
            return widget

        self.use_button = button(buttons, "Make Default", self._use)
        self.delete_button = button(buttons, "Delete", self._delete)
        button(buttons, "Add Folder...", self._add_folder)

        self._installed_tree_help.pack(anchor="w", pady=(4, 0))

        # --- Per snapshot ---
        ttk.Label(self.frame, text="Per snapshot",
                  font=("TkDefaultFont", 9, "bold")).pack(anchor="w",
                                                          pady=(12, 4))
        ttk.Label(
            self.frame,
            text="Tie a model to a snapshot - a service in another "
                 "language, say. It loads when the console recalls that "
                 "snapshot, and nothing reloads if it is the model already "
                 "running.",
            foreground=MUTED_FG, wraplength=620, justify="left"
        ).pack(anchor="w", pady=(0, 6))

        tied = ttk.Frame(self.frame)
        tied.pack(fill="x")
        tied.columnconfigure(0, weight=1)
        self.tied_tree = ttk.Treeview(
            tied, columns=("snapshot", "model", "language"), show="headings",
            height=4, selectmode="browse"
        )
        tree = self.tied_tree
        tree.heading("snapshot", text="Snapshot", anchor="w")
        tree.column("snapshot", width=200)
        tree.heading("model", text="Model", anchor="w")
        tree.column("model", width=280)
        tree.heading("language", text="Language", anchor="w")
        tree.column("language", width=140)
        tree.grid(row=0, column=0, sticky="nsew")
        tree.bind("<Double-1>", lambda event: self._edit_tie())
        tree.bind("<<TreeviewSelect>>", lambda event: self._update_buttons())

        buttons = ttk.Frame(tied)
        buttons.grid(row=0, column=1, sticky="n", padx=(10, 0))
        self.add_tie_button = button(buttons, "Add", self._add_tie)
        self.edit_tie_button = button(buttons, "Edit", self._edit_tie)
        self.remove_tie_button = button(buttons, "Remove", self._remove_tie)

        # --- Download ---
        ttk.Label(self.frame, text="Download a model",
                  font=("TkDefaultFont", 9, "bold")).pack(anchor="w",
                                                          pady=(12, 4))
        search_row = ttk.Frame(self.frame)
        search_row.pack(fill="x")
        ttk.Label(search_row, text="Search").pack(side="left")
        self.search_var = tk.StringVar()
        search = ttk.Entry(search_row, textvariable=self.search_var, width=30)
        search.pack(side="left", padx=(8, 0))
        self.search_var.trace_add("write", lambda *args: self._show_catalog())
        self.large_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(search_row, text="Include large models",
                        variable=self.large_var,
                        command=self._show_catalog).pack(side="left",
                                                         padx=(12, 0))

        ttk.Label(
            self.frame,
            text="Small models are quick and light enough to run many "
                 "channels at once. Large ones are more accurate but need "
                 "several GB of memory and a fast computer.",
            foreground=MUTED_FG, wraplength=620, justify="left"
        ).pack(anchor="w", pady=(6, 6))

        catalog = ttk.Frame(self.frame)
        catalog.pack(fill="x")
        catalog.columnconfigure(0, weight=1)

        self.catalog_tree = ttk.Treeview(
            catalog, columns=("language", "kind", "size", "status"),
            show="tree headings", height=8, selectmode="browse"
        )
        tree = self.catalog_tree
        tree.heading("#0", text="Model", anchor="w")
        tree.column("#0", width=280)
        tree.heading("language", text="Language", anchor="w")
        tree.column("language", width=140)
        tree.heading("kind", text="Type", anchor="w")
        tree.column("kind", width=70, stretch=False)
        tree.heading("size", text="Size", anchor="w")
        tree.column("size", width=80, stretch=False)
        tree.heading("status", text="", anchor="w")
        tree.column("status", width=110, stretch=False)
        tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(catalog, orient="vertical", command=tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=scroll.set)
        tree.bind("<Double-1>", lambda event: self._start_download())
        tree.bind("<<TreeviewSelect>>", lambda event: self._update_buttons())

        buttons = ttk.Frame(catalog)
        buttons.grid(row=0, column=2, sticky="n", padx=(10, 0))
        self.download_button = button(buttons, "Download", self._start_download)
        self.cancel_button = button(buttons, "Cancel Download",
                                    self._cancel_download)
        self.reload_button = button(buttons, "Refresh List",
                                    lambda: self._load_catalog(force=True))

        self.catalog_label = ttk.Label(self.frame, text="", foreground=MUTED_FG,
                                       wraplength=620, justify="left")
        self.catalog_label.pack(anchor="w", pady=(6, 0))

        self.refresh()

    # --- Showing ---

    def refresh(self):
        """Redraws both lists from what is on disk and in the config."""
        config = self.get_config()
        in_use = voice_to_text.model_path(config)
        selected = self.installed_tree.selection()
        self._installed = voice_to_text.installed_models(config)

        tree = self.installed_tree
        tree.delete(*tree.get_children())
        for model in self._installed:
            used = in_use is not None and Path(model["path"]) == Path(in_use)
            tree.insert("", "end", iid=model["path"], text=model["name"],
                        values=(self._language_of(model["name"]),
                                size_text(model["size"]),
                                "✔" if used else ""))
        keep = [row for row in selected if tree.exists(row)]
        if keep:
            tree.selection_set(keep)

        self._show_ties(config)

        live = self.get_live_snapshot()
        running = self.engine.model_in_use() if self.engine is not None \
            else None
        wanted = voice_to_text.model_path(config, live)
        current = running or wanted
        if current:
            name = Path(current).name
            if live and live in config["snapshot_models"] and \
                    wanted and Path(wanted) == Path(current):
                why = f"tied to snapshot {live}"
            else:
                why = "the default"
            verb = "Running" if running else "Will use"
            self.in_use_label.configure(
                text=f"{verb}: {name} ({self._language_of(name)}) - {why}")
        elif config["model_dir"]:
            self.in_use_label.configure(
                text=f"{config['model_dir']} is not a speech model folder - "
                     "pick another below.")
        else:
            self.in_use_label.configure(
                text="No speech model yet - search for your language below "
                     "and download one.")

        if self._catalog is None and self._catalog_error is None:
            self._load_catalog()
        else:
            self._show_catalog()
        self._update_buttons()

    def _show_ties(self, config):
        tree = self.tied_tree
        selected = tree.selection()
        tree.delete(*tree.get_children())
        live = self.get_live_snapshot()
        for snapshot in sorted(config["snapshot_models"], key=str.lower):
            path = config["snapshot_models"][snapshot]
            name = Path(path).name
            missing = not voice_to_text.is_model(path)
            tree.insert("", "end", iid=snapshot, values=(
                snapshot + ("  (live)" if snapshot == live else ""),
                name + ("  (missing - uses default)" if missing else ""),
                self._language_of(name),
            ))
        keep = [row for row in selected if tree.exists(row)]
        if keep:
            tree.selection_set(keep)

    def _language_of(self, name):
        for model in self._catalog or ():
            if model["name"] == name:
                return model["language"]
        return voice_to_text.guess_lang(name) or "—"

    def _show_catalog(self):
        tree = self.catalog_tree
        selected = tree.selection()
        tree.delete(*tree.get_children())

        if self._catalog is None:
            self._shown_catalog = []
            self._update_buttons()
            return

        words = self.search_var.get().lower().split()
        large = self.large_var.get()
        installed = {m["name"] for m in getattr(self, "_installed", ())}
        downloading = self._download["name"] if self._download else None

        self._shown_catalog = []
        for model in self._catalog:
            if model["kind"] != "small" and not large:
                continue
            haystack = f"{model['language']} {model['lang']} {model['name']}" \
                .lower()
            if not all(word in haystack for word in words):
                continue

            if model["name"] == downloading:
                status = self._download["status"]
            elif model["name"] in installed:
                status = "Installed"
            else:
                status = ""
            tree.insert("", "end", iid=model["name"], text=model["name"],
                        values=(model["language"],
                                model["kind"].replace("big", "large").title(),
                                model["size_text"] or size_text(model["size"]),
                                status))
            self._shown_catalog.append(model)

        keep = [row for row in selected if tree.exists(row)]
        if keep:
            tree.selection_set(keep)

        if not self._shown_catalog:
            self.catalog_label.configure(
                text="No models match that search." if words
                else "The catalog is empty.")
        elif not self._download:
            self.catalog_label.configure(
                text=f"{len(self._shown_catalog)} models. Models come from "
                     "alphacephei.com/vosk and run on this computer.")
        self._update_buttons()

    def _update_buttons(self):
        def enable(widget, on):
            widget.state(["!disabled"] if on else ["disabled"])

        picked = self.installed_tree.selection()
        model = self._installed_by_path(picked[0]) if picked else None
        enable(self.use_button, model is not None)
        enable(self.delete_button, model is not None and not model["external"])

        tie = bool(self.tied_tree.selection())
        enable(self.add_tie_button, bool(self._installed))
        enable(self.edit_tie_button, tie)
        enable(self.remove_tie_button, tie)

        busy = self._download is not None
        enable(self.download_button,
               not busy and bool(self.catalog_tree.selection()))
        enable(self.cancel_button, busy)
        enable(self.reload_button, not busy)

    def _installed_by_path(self, path):
        return next((m for m in getattr(self, "_installed", ())
                     if m["path"] == path), None)

    # --- On this computer ---

    def _use(self):
        picked = self.installed_tree.selection()
        if not picked:
            return
        path = picked[0]
        self.change(lambda config: config.update(model_dir=path))
        self.refresh()

    def _delete(self):
        picked = self.installed_tree.selection()
        model = self._installed_by_path(picked[0]) if picked else None
        if model is None or model["external"]:
            return
        config = self.get_config()
        tied = sorted(s for s, path in config["snapshot_models"].items()
                      if Path(path) == Path(model["path"]))
        also = (f"\n\n{', '.join(tied)} will go back to the default model."
                if tied else "")
        if not messagebox.askyesno(
                TITLE, f"Delete {model['name']} ({size_text(model['size'])}) "
                       f"from this computer?{also}", parent=self.frame):
            return

        in_use = voice_to_text.model_path(self.get_config())
        try:
            voice_to_text.delete_model(model["path"])
        except (OSError, ValueError) as ex:
            messagebox.showerror(TITLE, f"Could not delete it: {ex}",
                                 parent=self.frame)
            return

        def edit(config):
            if in_use and Path(in_use) == Path(model["path"]):
                # Back to the newest one left, if any.
                config["model_dir"] = ""
            for snapshot in tied:
                config["snapshot_models"].pop(snapshot, None)

        self.change(edit)
        self.refresh()

    def _add_folder(self):
        chosen = filedialog.askdirectory(
            parent=self.frame, title="Choose a Vosk model folder",
        )
        if not chosen:
            return
        if not voice_to_text.is_model(chosen):
            messagebox.showerror(
                TITLE,
                "That folder is not a Vosk speech model - pick the folder "
                "that holds its “am” and “conf” folders.",
                parent=self.frame
            )
            return
        self.change(lambda config: config.update(model_dir=chosen))
        self.refresh()

    # --- Per snapshot ---

    def _add_tie(self):
        config = self.get_config()
        taken = set(config["snapshot_models"])
        offered = [s for s in self.get_snapshots() if s not in taken]

        def save(snapshot, path):
            if snapshot in taken:
                messagebox.showerror(
                    TITLE, f"{snapshot} already has a model - edit that one "
                           "instead.", parent=self.frame)
                return False
            self.change(lambda config: config["snapshot_models"].update(
                {snapshot: path}))
            self.refresh()
            self.tied_tree.selection_set(snapshot)
            return True

        SnapshotModelDialog(self.frame, "Tie a Model to a Snapshot", offered,
                            "", None, self._installed, self._language_of,
                            on_saved=save)

    def _edit_tie(self):
        picked = self.tied_tree.selection()
        if not picked:
            return
        old = picked[0]
        config = self.get_config()
        taken = set(config["snapshot_models"]) - {old}
        offered = [s for s in self.get_snapshots() if s not in taken]

        def save(snapshot, path):
            if snapshot in taken:
                messagebox.showerror(
                    TITLE, f"{snapshot} already has a model.",
                    parent=self.frame)
                return False

            def edit(config):
                config["snapshot_models"].pop(old, None)
                config["snapshot_models"][snapshot] = path

            self.change(edit)
            self.refresh()
            self.tied_tree.selection_set(snapshot)
            return True

        SnapshotModelDialog(self.frame, "Edit Snapshot Model", offered, old,
                            config["snapshot_models"][old], self._installed,
                            self._language_of, on_saved=save)

    def _remove_tie(self):
        picked = self.tied_tree.selection()
        if not picked:
            return
        self.change(lambda config: config["snapshot_models"].pop(picked[0],
                                                                 None))
        self.refresh()

    # --- The catalog ---

    def _load_catalog(self, force=False):
        if force:
            ModelPanel._catalog = None
            ModelPanel._catalog_error = None
        self.catalog_label.configure(text="Fetching the list of models...")
        self.reload_button.state(["disabled"])
        result = {}

        def run():
            try:
                result["models"] = voice_to_text.fetch_catalog()
            except Exception as ex:
                result["error"] = ex

        worker = threading.Thread(target=run, name="VoiceCatalog", daemon=True)
        worker.start()

        def check():
            if not self.frame.winfo_exists():
                return
            if worker.is_alive():
                self.frame.after(200, check)
                return
            if "error" in result:
                ModelPanel._catalog_error = result["error"]
                self.catalog_label.configure(
                    text="Could not fetch the list of models - is this "
                         f"computer online? ({result['error']})")
                self._update_buttons()
                return
            ModelPanel._catalog = result["models"]
            # Installed models can now be named by language.
            self.refresh()

        check()

    def _selected_catalog_model(self):
        picked = self.catalog_tree.selection()
        if not picked:
            return None
        return next((m for m in self._catalog or () if m["name"] == picked[0]),
                    None)

    def _start_download(self):
        model = self._selected_catalog_model()
        if model is None or self._download is not None:
            return

        installed = {m["name"] for m in self._installed}
        size = model["size_text"] or size_text(model["size"])
        if model["name"] in installed:
            if not messagebox.askyesno(
                    TITLE, f"{model['name']} is already on this computer. "
                           "Download it again?", parent=self.frame):
                return
        elif model["size"] > LARGE_BYTES:
            if not messagebox.askyesno(
                    TITLE,
                    f"{model['name']} is {size}. Large models are more "
                    "accurate, but take a while to load, need several GB of "
                    "memory and may not keep up with many channels at once."
                    "\n\nDownload it?", parent=self.frame):
                return

        cancel = threading.Event()
        state = {"name": model["name"], "status": "Starting...",
                 "cancel": cancel}
        self._download = state
        result = {}

        def report(done, total):
            percent = f"{done * 100 // total}%" if total else size_text(done)
            state["status"] = f"Downloading {percent}"
            state["detail"] = (f"Downloading {model['name']} - "
                               f"{size_text(done)} of {size}")

        def run():
            try:
                result["path"] = voice_to_text.download_model(
                    report, url=model["url"], md5=model["md5"], cancel=cancel)
            except voice_to_text.DownloadCancelled:
                result["cancelled"] = True
            except Exception as ex:
                result["error"] = ex

        worker = threading.Thread(target=run, name="VoiceModelDownload",
                                  daemon=True)
        worker.start()
        self._show_catalog()

        def check():
            if not self.frame.winfo_exists():
                cancel.set()
                return
            if worker.is_alive():
                if self.catalog_tree.exists(model["name"]):
                    self.catalog_tree.set(model["name"], "status",
                                          "Unpacking..." if state["status"]
                                          == "Downloading 100%"
                                          else state["status"])
                self.catalog_label.configure(
                    text=state.get("detail", f"Downloading {model['name']}..."))
                self.frame.after(300, check)
                return

            self._download = None
            message = None
            if "cancelled" in result:
                message = "Download cancelled."
            elif "path" in result:
                path = result["path"]
                # Just picked for this: put it to use straight away.
                self.change(lambda config: config.update(model_dir=path))
                message = f"Downloaded {model['name']} - now in use."
            self.refresh()
            if message:
                self.catalog_label.configure(text=message)
            if "error" in result:
                messagebox.showerror(
                    TITLE, f"{model['name']} could not be downloaded: "
                           f"{result['error']}", parent=self.frame)

        check()

    def _cancel_download(self):
        if self._download is not None:
            self._download["cancel"].set()
            self._download["status"] = "Cancelling..."


class SnapshotModelDialog:
    """A snapshot - picked from the console's, or typed while it is not
    connected - and the installed model to load on it. on_saved(snapshot,
    path) may return False to keep the dialog open."""

    def __init__(self, master, title, snapshots, snapshot, path, installed,
                 language_of, on_saved):
        self.on_saved = on_saved
        self.paths = [m["path"] for m in installed]

        self.window = tk.Toplevel(master)
        self.window.title(title)
        self.window.transient(master.winfo_toplevel())
        self.window.resizable(False, False)

        frame = ttk.Frame(self.window, padding=15)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Snapshot").grid(row=0, column=0, sticky="w")
        self.snapshot_combo = ttk.Combobox(frame, width=36, values=snapshots)
        self.snapshot_combo.set(snapshot)
        self.snapshot_combo.grid(row=0, column=1, sticky="ew", padx=(10, 0))

        ttk.Label(frame, text="Model").grid(row=1, column=0, sticky="w",
                                            pady=(10, 0))
        labels = [f"{m['name']}  ({language_of(m['name'])})"
                  for m in installed]
        self.model_combo = ttk.Combobox(frame, width=36, state="readonly",
                                        values=labels)
        if path is not None and any(Path(p) == Path(path) for p in self.paths):
            self.model_combo.current(
                next(i for i, p in enumerate(self.paths)
                     if Path(p) == Path(path)))
        self.model_combo.grid(row=1, column=1, sticky="ew", padx=(10, 0),
                              pady=(10, 0))

        (self.model_combo if snapshot else self.snapshot_combo).focus_set()

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
        index = self.model_combo.current()
        if not snapshot:
            self.snapshot_combo.focus_set()
            return
        if index < 0:
            self.model_combo.focus_set()
            return
        if self.on_saved(snapshot, self.paths[index]) is not False:
            self.window.destroy()
