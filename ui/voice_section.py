import copy
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from services import voice_highlights, voice_to_text
from ui.sos_section import SosSection

# The muted gray the rest of the desktop UI uses for secondary text.
MUTED_FG = "#888888"
OK_FG = "#66bb6a"
ERROR_FG = "#ef5350"

NOBODY = "(nobody)"

# How often the status line and level column are read back, while shown.
POLL_MS = 250

HIGHLIGHT_TEXTS = {
    "title": "Highlight Words",
    "blurb": "Words picked out of the transcript in their colour - in the "
             "desktop's chat and on the phones. A custom list replaces the "
             "default while the console is on one of its snapshots. A "
             "number said after a word goes with it: “verse two” "
             "shows as Verse 2.",
    "add": "Add Highlight Word",
    "edit": "Edit Highlight Word",
    "label": "Word",
    "color_label": "Highlight colour",
    "preview": "Chorus",
    "restore": "Put the default list back to Intro, Verse, Pre-Chorus, "
               "Chorus, Bridge and the rest?",
    "default_used": "Used on every snapshot without a custom list.",
    "unused": "Not tied to any snapshot yet, so it is never used.",
}


class VoiceSection:
    """Settings' Voice to Text: which audio interface and channels to
    listen to, who is on each channel, the speech model, and the words
    to highlight. Every change is saved and applied as it is made.

    `get_config` returns the normalized config (services/voice_to_text.py);
    `set_config(config)` stores, saves and applies a new one. `get_users`
    returns [(username, display name)]. `engine` is the running
    VoiceEngine, read for its status and levels.
    """

    def __init__(self, parent, get_config, set_config, get_snapshots,
                 get_users, engine):
        self.get_config = get_config
        self.set_config = set_config
        self.get_users = get_users
        self.engine = engine
        self._devices = []
        self._poll_job = None
        self._downloading = False

        outer = ScrollFrame(parent)
        outer.pack(fill="both", expand=True)
        self.frame = ttk.Frame(outer.inner, padding=15)
        self.frame.pack(fill="x")
        self.root = parent

        ttk.Label(self.frame, text="Voice to Text",
                  font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        ttk.Label(
            self.frame,
            text="Listens to input channels of your audio interface"
                 + (" over ASIO" if sys.platform == "win32" else "")
                 + " and puts what is said into the chat, under the name of "
                 "the account using that channel. Speech is recognised on "
                 "this computer - nothing is sent online.",
            foreground=MUTED_FG, wraplength=640, justify="left"
        ).pack(anchor="w", pady=(2, 8))

        top = ttk.Frame(self.frame)
        top.pack(fill="x")
        self.enabled_var = tk.BooleanVar()
        ttk.Checkbutton(top, text="Turn on Voice to Text", style="Switch.TCheckbutton",
                        variable=self.enabled_var,
                        command=self._toggle_enabled).pack(side="left")
        self.status_label = ttk.Label(top, text="", foreground=MUTED_FG)
        self.status_label.pack(side="left", padx=(14, 0))

        # --- Audio ---
        audio = ttk.LabelFrame(self.frame, text="Audio Input", padding=10)
        audio.pack(fill="x", pady=(12, 0))
        audio.columnconfigure(1, weight=1)

        ttk.Label(audio, text="Device").grid(row=0, column=0, sticky="w")
        self.device_combo = ttk.Combobox(audio, state="readonly", width=48)
        self.device_combo.grid(row=0, column=1, sticky="ew", padx=(10, 6))
        self.device_combo.bind("<<ComboboxSelected>>",
                               lambda event: self._device_chosen())
        ttk.Button(audio, text="Refresh",
                   command=self.refresh_devices).grid(row=0, column=2)

        ttk.Label(audio, text="Channels").grid(row=1, column=0, sticky="w",
                                               pady=(8, 0))
        counts = ttk.Frame(audio)
        counts.grid(row=1, column=1, columnspan=2, sticky="w", padx=(10, 0),
                    pady=(8, 0))
        self.count_combo = ttk.Combobox(
            counts, state="readonly", width=6,
            values=[str(n) for n in voice_to_text.CHANNEL_COUNTS]
        )
        self.count_combo.pack(side="left")
        self.count_combo.bind("<<ComboboxSelected>>",
                              lambda event: self._count_chosen())
        self.device_note = ttk.Label(counts, text="", foreground=MUTED_FG)
        self.device_note.pack(side="left", padx=(10, 0))

        ttk.Label(audio, text="Gate").grid(row=2, column=0, sticky="w",
                                           pady=(8, 0))
        gate_row = ttk.Frame(audio)
        gate_row.grid(row=2, column=1, columnspan=2, sticky="w", padx=(10, 0),
                      pady=(8, 0))
        self.gate_var = tk.StringVar()
        gate = ttk.Spinbox(
            gate_row, from_=voice_to_text.MIN_GATE_DB,
            to=voice_to_text.MAX_GATE_DB, increment=1, width=6,
            textvariable=self.gate_var, command=self._gate_changed
        )
        gate.pack(side="left")
        gate.bind("<FocusOut>", lambda event: self._gate_changed())
        gate.bind("<Return>", lambda event: self._gate_changed())
        ttk.Label(gate_row, text="dBFS - quieter than this is not listened "
                                 "to, which keeps band bleed out of the text.",
                  foreground=MUTED_FG).pack(side="left", padx=(8, 0))

        # --- Model ---
        model = ttk.LabelFrame(self.frame, text="Speech Model", padding=10)
        model.pack(fill="x", pady=(12, 0))
        self.model_label = ttk.Label(model, text="", wraplength=600,
                                     justify="left")
        self.model_label.pack(anchor="w")
        model_buttons = ttk.Frame(model)
        model_buttons.pack(fill="x", pady=(8, 0))
        self.download_button = ttk.Button(
            model_buttons,
            text=f"Download English Model ({voice_to_text.MODEL_SIZE_MB} MB)",
            command=self._download
        )
        self.download_button.pack(side="left")
        ttk.Button(model_buttons, text="Choose Folder...",
                   command=self._choose_model).pack(side="left", padx=(6, 0))
        self.use_newest_button = ttk.Button(
            model_buttons, text="Use Downloaded", command=self._use_downloaded
        )
        self.use_newest_button.pack(side="left", padx=(6, 0))
        self.download_label = ttk.Label(model_buttons, text="",
                                        foreground=MUTED_FG)
        self.download_label.pack(side="left", padx=(10, 0))

        # --- Channels ---
        channels = ttk.LabelFrame(self.frame, text="Channels", padding=10)
        channels.pack(fill="x", pady=(12, 0))
        ttk.Label(
            channels,
            text="Tick the channels to listen to and tie each to the account "
                 "using it - their messages go out under that name. Double-"
                 "click a channel to edit it.",
            foreground=MUTED_FG, wraplength=600, justify="left"
        ).pack(anchor="w", pady=(0, 8))

        body = ttk.Frame(channels)
        body.pack(fill="x")
        body.columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            body, columns=("listen", "label", "user", "level"),
            show="tree headings", height=10, selectmode="extended"
        )
        self.tree.heading("#0", text="Ch", anchor="w")
        self.tree.column("#0", width=60, stretch=False)
        self.tree.heading("listen", text="Listen", anchor="w")
        self.tree.column("listen", width=70, stretch=False)
        self.tree.heading("label", text="Label", anchor="w")
        self.tree.column("label", width=160)
        self.tree.heading("user", text="Account", anchor="w")
        self.tree.column("user", width=200)
        self.tree.heading("level", text="Level", anchor="w")
        self.tree.column("level", width=150, stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<Double-1>", lambda event: self._edit_channel())
        self.tree.bind("<space>", lambda event: self._toggle_listen())
        self.tree.bind("<<TreeviewSelect>>", lambda event: self._update_buttons())

        buttons = ttk.Frame(body)
        buttons.grid(row=0, column=2, sticky="n", padx=(10, 0))

        def button(text, command):
            widget = ttk.Button(buttons, text=text, command=command, width=16)
            widget.pack(fill="x", pady=(0, 4))
            return widget

        self.listen_button = button("Listen On/Off", self._toggle_listen)
        self.edit_button = button("Edit...", self._edit_channel)
        self.clear_button = button("Clear", self._clear_channels)

        # --- Highlights ---
        self.highlights = SosSection(
            self.frame,
            get_config=lambda: self.get_config()["highlights"],
            set_config=self._set_highlights,
            get_snapshots=get_snapshots,
            model=voice_highlights, texts=HIGHLIGHT_TEXTS,
            color=voice_highlights.DEFAULT_COLOR,
        )
        # SosSection pads itself as a section of its own; here it sits
        # under the others.
        self.highlights.frame.configure(padding=(0, 15, 0, 0))

        self.refresh_devices()
        self.refresh()
        self.frame.bind("<Map>", lambda event: self._start_polling())

    # --- Reading and saving ---

    def _change(self, edit):
        """Applies edit(config) to a copy and saves it - swapped whole,
        since the voice threads read it."""
        config = copy.deepcopy(self.get_config())
        edit(config)
        self.set_config(voice_to_text.normalize(config))

    def _set_highlights(self, highlights):
        self._change(lambda config: config.update(highlights=highlights))

    def refresh(self):
        """Redraws from the saved config - after an edit, or a restored
        backup."""
        config = self.get_config()
        self.enabled_var.set(config["enabled"])
        self.count_combo.set(str(config["channel_count"]))
        self.gate_var.set(str(config["gate_db"]))
        self._show_device(config)
        self._show_model(config)
        self._show_channels(config)
        self.highlights.refresh()
        self._show_status()

    def _toggle_enabled(self):
        enabled = self.enabled_var.get()
        self._change(lambda config: config.update(enabled=enabled))
        self._show_status()

    # --- Audio ---

    @staticmethod
    def _device_label(device):
        return f"{device['name']}  —  {device['host_api']}, " \
               f"{device['channels']} in"

    def refresh_devices(self):
        self._devices = voice_to_text.input_devices()
        labels = [self._device_label(d) for d in self._devices]
        self.device_combo.configure(values=labels)
        self._show_device(self.get_config())

    def _show_device(self, config):
        problem = voice_to_text.unavailable_reason()
        device = next((d for d in self._devices
                       if d["name"] == config["device"]
                       and (not config["host_api"]
                            or d["host_api"] == config["host_api"])), None)

        if device is not None:
            self.device_combo.set(self._device_label(device))
        elif config["device"]:
            self.device_combo.set(f"{config['device']}  (not connected)")
        else:
            self.device_combo.set("")

        if problem:
            note = problem
        elif not self._devices:
            note = "No recording devices found."
        elif device is not None and device["channels"] < config["channel_count"]:
            note = f"This device has {device['channels']} inputs."
        elif sys.platform == "win32" and not any(
                d["host_api"] == "ASIO" for d in self._devices):
            note = "No ASIO driver found - is the interface's driver installed?"
        else:
            note = ""
        self.device_note.configure(text=note)

    def _device_chosen(self):
        index = self.device_combo.current()
        if index < 0 or index >= len(self._devices):
            return
        device = self._devices[index]

        def edit(config):
            config["device"] = device["name"]
            config["host_api"] = device["host_api"]
            # Open no more channels than the device has: the largest
            # count that fits it.
            fitting = [n for n in voice_to_text.CHANNEL_COUNTS
                       if n <= device["channels"]]
            if fitting and config["channel_count"] > device["channels"]:
                config["channel_count"] = fitting[0]

        self._change(edit)
        self.refresh()

    def _count_chosen(self):
        try:
            count = int(self.count_combo.get())
        except ValueError:
            return
        self._change(lambda config: config.update(channel_count=count))
        self.refresh()

    def _gate_changed(self):
        try:
            gate = round(float(self.gate_var.get()))
        except ValueError:
            self.gate_var.set(str(self.get_config()["gate_db"]))
            return
        gate = min(max(gate, voice_to_text.MIN_GATE_DB), voice_to_text.MAX_GATE_DB)
        self.gate_var.set(str(gate))
        if gate != self.get_config()["gate_db"]:
            self._change(lambda config: config.update(gate_db=gate))

    # --- Model ---

    def _show_model(self, config):
        path = voice_to_text.model_path(config)
        if path:
            text = f"Using {path}"
        elif config["model_dir"]:
            text = f"{config['model_dir']} is not a speech model folder."
        else:
            text = ("No speech model yet. Download the English one, or "
                    "choose a Vosk model folder - larger models and other "
                    "languages are at alphacephei.com/vosk/models.")
        self.model_label.configure(text=text)
        # Back from a chosen folder to the newest downloaded model.
        self.use_newest_button.state(["!disabled"] if config["model_dir"]
                                     else ["disabled"])

    def _choose_model(self):
        chosen = filedialog.askdirectory(
            parent=self.frame, title="Choose a Vosk model folder",
            initialdir=str(voice_to_text.MODELS_ROOT)
            if voice_to_text.MODELS_ROOT.is_dir() else None,
        )
        if not chosen:
            return
        if not voice_to_text.is_model(chosen):
            messagebox.showerror(
                "Voice to Text",
                "That folder is not a Vosk speech model - pick the folder "
                "that holds its “am” and “conf” folders.",
                parent=self.frame
            )
            return
        self._change(lambda config: config.update(model_dir=chosen))
        self.refresh()

    def _use_downloaded(self):
        self._change(lambda config: config.update(model_dir=""))
        self.refresh()

    def _download(self):
        if self._downloading:
            return
        self._downloading = True
        self.download_button.state(["disabled"])
        self.download_label.configure(text="Downloading...")
        progress = {"text": "Downloading..."}
        result = {}

        def report(done, total):
            mb = done / 1e6
            progress["text"] = (f"Downloading... {mb:.0f} of {total / 1e6:.0f} MB"
                                if total else f"Downloading... {mb:.0f} MB")

        def run():
            try:
                result["path"] = voice_to_text.download_model(report)
            except Exception as ex:
                result["error"] = ex

        worker = threading.Thread(target=run, name="VoiceModelDownload",
                                  daemon=True)
        worker.start()

        def check():
            if worker.is_alive():
                self.download_label.configure(text=progress["text"])
                self.frame.after(300, check)
                return
            self._downloading = False
            self.download_button.state(["!disabled"])
            if "error" in result:
                self.download_label.configure(text="")
                messagebox.showerror(
                    "Voice to Text",
                    f"The model could not be downloaded: {result['error']}",
                    parent=self.frame
                )
                return
            self.download_label.configure(text="Downloaded.")
            # The newest download, so the engine restarts with it.
            self._change(lambda config: config.update(model_dir=""))
            self.refresh()

        check()

    # --- Channels ---

    def _user_names(self):
        return dict(self.get_users())

    def _show_channels(self, config):
        names = self._user_names()
        selected = self.tree.selection()
        count = config["channel_count"]

        if len(self.tree.get_children()) != count:
            self.tree.delete(*self.tree.get_children())
            for number in range(1, count + 1):
                self.tree.insert("", "end", iid=str(number), text=str(number))

        for number in range(1, count + 1):
            item = voice_to_text.channel(config, number)
            user = item["user"]
            if user is None:
                shown_user = ""
            elif user in names:
                shown_user = names[user] if names[user] == user \
                    else f"{names[user]} ({user})"
            else:
                shown_user = f"{user} (account gone)"
            self.tree.item(str(number), values=(
                "✔" if item["listen"] else "", item["label"], shown_user,
                self.tree.set(str(number), "level") if item["listen"] else "",
            ))

        keep = [row for row in selected if self.tree.exists(row)]
        if keep:
            self.tree.selection_set(keep)
        self._update_buttons()

    def _selected_numbers(self):
        return [int(row) for row in self.tree.selection()]

    def _update_buttons(self):
        selected = self._selected_numbers()
        for widget in (self.listen_button, self.clear_button):
            widget.state(["!disabled"] if selected else ["disabled"])
        self.edit_button.state(["!disabled"] if len(selected) == 1
                               else ["disabled"])

    def _toggle_listen(self):
        numbers = self._selected_numbers()
        if not numbers:
            return "break"
        config = self.get_config()
        # All on, unless every one picked is already on.
        turn_on = not all(voice_to_text.channel(config, n)["listen"]
                          for n in numbers)

        def edit(config):
            for number in numbers:
                item = dict(voice_to_text.channel(config, number))
                item["listen"] = turn_on
                config["channels"][str(number)] = item

        self._change(edit)
        self._show_channels(self.get_config())
        return "break"

    def _clear_channels(self):
        numbers = self._selected_numbers()
        if not numbers:
            return

        def edit(config):
            for number in numbers:
                config["channels"].pop(str(number), None)

        self._change(edit)
        self._show_channels(self.get_config())

    def _edit_channel(self):
        numbers = self._selected_numbers()
        if len(numbers) != 1:
            return
        number = numbers[0]
        item = voice_to_text.channel(self.get_config(), number)

        def save(listen, label, user):
            def edit(config):
                config["channels"][str(number)] = {
                    "listen": listen, "label": label, "user": user,
                }
            self._change(edit)
            self._show_channels(self.get_config())

        ChannelDialog(self.frame, number, item, self.get_users(), on_saved=save)

    # --- Live status ---

    def _start_polling(self):
        if self._poll_job is None:
            self._poll()

    def _poll(self):
        self._poll_job = None
        if not self.frame.winfo_ismapped():
            return
        self._show_status()
        self._show_levels()
        self._poll_job = self.frame.after(POLL_MS, self._poll)

    def _show_status(self):
        engine = self.engine
        state = engine.state if engine is not None else "off"
        message = engine.message if engine is not None else "Off"
        color = {"running": OK_FG, "error": ERROR_FG}.get(state, MUTED_FG)
        self.status_label.configure(text=message, foreground=color)

    def _show_levels(self):
        levels = self.engine.levels() if self.engine is not None else {}
        gate = self.get_config()["gate_db"]
        for number, level in levels.items():
            row = str(number)
            if not self.tree.exists(row):
                continue
            # A bar of 12 steps over -60..0 dBFS, open when over the gate.
            steps = max(0, min(12, round((level + 60) / 5)))
            bar = "█" * steps + "░" * (12 - steps)
            mark = " ●" if level >= gate else ""
            self.tree.set(row, "level", bar + mark)


class ChannelDialog:
    """One channel's settings: listened to or not, a label, and the
    account using it. on_saved(listen, label, user)."""

    def __init__(self, master, number, item, users, on_saved):
        self.on_saved = on_saved
        self.users = users

        self.window = tk.Toplevel(master)
        self.window.title(f"Channel {number}")
        self.window.transient(master.winfo_toplevel())
        self.window.resizable(False, False)

        frame = ttk.Frame(self.window, padding=15)
        frame.pack(fill="both", expand=True)

        self.listen_var = tk.BooleanVar(value=item["listen"])
        ttk.Checkbutton(frame, text="Listen to this channel",
                        variable=self.listen_var).grid(
            row=0, column=0, columnspan=2, sticky="w")

        ttk.Label(frame, text="Label").grid(row=1, column=0, sticky="w",
                                            pady=(10, 0))
        self.label_entry = ttk.Entry(frame, width=30)
        self.label_entry.insert(0, item["label"])
        self.label_entry.grid(row=1, column=1, sticky="ew", padx=(10, 0),
                              pady=(10, 0))

        ttk.Label(frame, text="Account").grid(row=2, column=0, sticky="w",
                                              pady=(10, 0))
        self.choices = [None] + [username for username, _name in users]
        labels = [NOBODY] + [
            name if name == username else f"{name} ({username})"
            for username, name in users
        ]
        self.user_combo = ttk.Combobox(frame, state="readonly", width=28,
                                       values=labels)
        current = item["user"]
        if current in self.choices:
            self.user_combo.current(self.choices.index(current))
        elif current:
            # Tied to an account since deleted: kept until changed.
            self.choices.append(current)
            self.user_combo.configure(values=labels + [f"{current} (account gone)"])
            self.user_combo.current(len(self.choices) - 1)
        else:
            self.user_combo.current(0)
        self.user_combo.grid(row=2, column=1, sticky="ew", padx=(10, 0),
                             pady=(10, 0))

        ttk.Label(
            frame,
            text="Messages go out under the account's name, or the label, "
                 f"or “Ch {number}”.",
            foreground=MUTED_FG, wraplength=320, justify="left"
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))

        btn_bar = ttk.Frame(frame)
        btn_bar.grid(row=4, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(btn_bar, text="Cancel",
                   command=self.window.destroy).pack(side="right")
        ttk.Button(btn_bar, text="Save", style="Accent.TButton",
                   command=self.save).pack(side="right", padx=(0, 5))

        self.label_entry.focus_set()
        self.window.bind("<Return>", lambda event: self.save())
        self.window.bind("<Escape>", lambda event: self.window.destroy())
        self.window.grab_set()

    def save(self):
        label = " ".join(self.label_entry.get().split())[:voice_to_text.MAX_LABEL]
        index = self.user_combo.current()
        user = self.choices[index] if 0 <= index < len(self.choices) else None
        self.on_saved(self.listen_var.get(), label, user)
        self.window.destroy()


class ScrollFrame(ttk.Frame):
    """A frame whose contents scroll up and down when taller than it.
    Widgets go in `inner`. The wheel scrolls it anywhere over it but a
    list, which scrolls itself."""

    def __init__(self, parent):
        super().__init__(parent)
        background = ttk.Style().lookup("TFrame", "background") or None
        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0,
                                bg=background)
        self.bar = ttk.Scrollbar(self, orient="vertical",
                                 command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bar.pack(side="right", fill="y")

        self.inner = ttk.Frame(self.canvas)
        self._window = self.canvas.create_window((0, 0), window=self.inner,
                                                 anchor="nw")
        self.inner.bind("<Configure>", lambda event: self._resize())
        self.canvas.bind("<Configure>", lambda event: self._resize())

        self.bind_all("<MouseWheel>", self._wheel, add="+")
        self.bind_all("<Button-4>", self._wheel, add="+")
        self.bind_all("<Button-5>", self._wheel, add="+")

    def _resize(self):
        self.canvas.itemconfigure(self._window, width=self.canvas.winfo_width())
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _wheel(self, event):
        if not self.winfo_ismapped():
            return
        widget = event.widget
        if not isinstance(widget, tk.Misc):
            return
        # Only for wheels over this frame, and not over a list.
        path = str(widget)
        if not path.startswith(str(self)) or widget.winfo_class() in (
                "Treeview", "Text", "Listbox", "TCombobox", "TSpinbox"):
            return
        if self.canvas.yview() == (0.0, 1.0):
            return
        if getattr(event, "num", None) == 4:
            step = -1
        elif getattr(event, "num", None) == 5:
            step = 1
        else:
            step = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(step * 3, "units")
