import copy
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from services import (voice_highlights, voice_to_text, voice_vocabulary,
                      voice_whisper)
from ui.sos_section import SosSection
from ui.voice_models import ModelPanel

# The muted gray the rest of the desktop UI uses for secondary text.
MUTED_FG = "#888888"
OK_FG = "#66bb6a"
ERROR_FG = "#ef5350"

NOBODY = "(nobody)"
GLOBAL_SCOPE = "Global - every snapshot"

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
                 get_users, engine, get_live_snapshot=lambda: None,
                 set_test=None):
        self.get_config = get_config
        self.set_config = set_config
        self.get_users = get_users
        self.get_snapshots = get_snapshots
        self.get_live_snapshot = get_live_snapshot
        self.engine = engine
        # The channel setup shown: None for the global one, else a
        # snapshot's name.
        self._scope = None
        self._scopes = [None]
        # set_test(device) starts a mic test, set_test(None) stops it.
        self.set_test = set_test
        self._test_device = None
        self._mics = []
        self._devices = []
        self._poll_job = None

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

        # --- Mic test ---
        test = ttk.LabelFrame(self.frame, text="Mic Test", padding=10)
        test.pack(fill="x", pady=(12, 0))
        ttk.Label(
            test,
            text="Try it on this computer's microphone - no interface "
                 "needed. Uses the speech model and highlight words below; "
                 "what it hears also goes to the chat as \u201cMic Test\u201d, and Live "
                 "Text in the sidebar shows it as it is heard. "
                 "Your interface setup is untouched and comes back when the "
                 "test stops.",
            foreground=MUTED_FG, wraplength=640, justify="left"
        ).pack(anchor="w", pady=(0, 8))

        test_row = ttk.Frame(test)
        test_row.pack(fill="x")
        ttk.Label(test_row, text="Microphone").pack(side="left")
        self.mic_combo = ttk.Combobox(test_row, state="readonly", width=44)
        self.mic_combo.pack(side="left", padx=(10, 6))
        self.test_button = ttk.Button(test_row, text="Start Mic Test",
                                      style="Accent.TButton", width=16,
                                      command=self._toggle_test)
        self.test_button.pack(side="left")

        heard_row = ttk.Frame(test)
        heard_row.pack(fill="x", pady=(8, 0))
        self.test_level = ttk.Label(heard_row, text="", font=("TkFixedFont", 9),
                                    width=16)
        self.test_level.pack(side="left")
        self.heard_label = ttk.Label(heard_row, text="", wraplength=520,
                                     justify="left")
        self.heard_label.pack(side="left", padx=(10, 0))

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

        # --- Recognition ---
        self.recognition = RecognitionPanel(
            self.frame, self.get_config, self._change, engine,
            on_engine=lambda: self._show_models(self.get_config()))
        self.recognition.frame.pack(fill="x", pady=(12, 0))

        # --- Model (Vosk's) ---
        self.models = ModelPanel(self.frame, self.get_config, self._change,
                                 get_snapshots=get_snapshots,
                                 get_live_snapshot=get_live_snapshot,
                                 engine=engine)

        # --- Channels ---
        channels = ttk.LabelFrame(self.frame, text="Channels", padding=10)
        channels.pack(fill="x", pady=(12, 0))
        self.channels_frame = channels
        ttk.Label(
            channels,
            text="Tick the channels to listen to and tie each to the account "
                 "using it - the channel is labelled with the account's name "
                 "(its username if it has none), and its messages go out "
                 "under it. Double-click a channel to edit it.",
            foreground=MUTED_FG, wraplength=600, justify="left"
        ).pack(anchor="w", pady=(0, 8))

        scope_row = ttk.Frame(channels)
        scope_row.pack(fill="x", pady=(0, 4))
        ttk.Label(scope_row, text="Setup for").pack(side="left")
        self.scope_combo = ttk.Combobox(scope_row, state="readonly", width=34)
        self.scope_combo.pack(side="left", padx=(8, 0))
        self.scope_combo.bind("<<ComboboxSelected>>",
                              lambda event: self._scope_chosen())
        self.global_button = ttk.Button(scope_row, text="Use Global Setup",
                                        command=self._use_global)
        self.global_button.pack(side="left", padx=(8, 0))
        self.scope_note = ttk.Label(channels, foreground=MUTED_FG,
                                    wraplength=600, justify="left")
        self.scope_note.pack(anchor="w", pady=(0, 8))

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
        self.recognition.refresh()
        self._show_models(config)
        self._show_channels(config)
        self.highlights.refresh()
        self._show_status()

    # --- Mic test ---

    def _show_mics(self):
        """Every recording device but ASIO ones - a test is for the
        computer's own mics - with the default one picked."""
        chosen = self._mics[self.mic_combo.current()] \
            if 0 <= self.mic_combo.current() < len(self._mics) else None
        self._mics = [d for d in self._devices if d["host_api"] != "ASIO"] \
            or list(self._devices)
        self.mic_combo.configure(values=[self._device_label(d)
                                         for d in self._mics])

        default = voice_to_text.default_input_device()
        keep = chosen or self._test_device or default
        index = next((i for i, d in enumerate(self._mics) if keep and
                      d["name"] == keep["name"]
                      and d["host_api"] == keep["host_api"]), None)
        if index is None and self._mics:
            index = 0
        if index is not None:
            self.mic_combo.current(index)
        else:
            self.mic_combo.set("")

        if self._test_device is None:
            # Say why the list is empty rather than just greying it out.
            problem = voice_to_text.unavailable_reason()
            if problem:
                why = problem
            elif not self._mics:
                why = "No microphones found - plug one in and press Refresh."
            else:
                why = ""
            self.heard_label.configure(text=why, foreground=ERROR_FG
                                       if why else "")
        self._show_test_button()

    def _show_test_button(self):
        testing = self._test_device is not None
        self.test_button.configure(
            text="Stop Mic Test" if testing else "Start Mic Test")
        self.test_button.state(
            ["!disabled"] if testing or (self._mics and self.set_test)
            else ["disabled"])
        self.mic_combo.state(["disabled"] if testing else ["!disabled"])
        if not testing:
            self.test_level.configure(text="")

    def _toggle_test(self):
        if self.set_test is None:
            return
        if self._test_device is not None:
            self._test_device = None
            self.set_test(None)
            self.heard_label.configure(text="")
        else:
            index = self.mic_combo.current()
            if not 0 <= index < len(self._mics):
                return
            self._test_device = self._mics[index]
            self.heard_label.configure(
                text="Say something - try \u201cgoing to the chorus\u201d.",
                foreground=MUTED_FG)
            self.set_test(self._test_device)
        self._show_test_button()
        self._show_status()
        self._start_polling()

    def _show_test(self):
        if self._test_device is None or self.engine is None:
            return
        level = self.engine.levels().get(1)
        if level is None:
            self.test_level.configure(text="")
        else:
            steps = max(0, min(12, round((level + 60) / 5)))
            open_ = level >= self.get_config()["gate_db"]
            self.test_level.configure(
                text="\u2588" * steps + "\u2591" * (12 - steps)
                + (" \u25cf" if open_ else "  "))
        heard = self.engine.last_text
        if heard:
            self.heard_label.configure(text=f"Heard: {heard}",
                                       foreground="")

    def refresh_model_status(self):
        """After a snapshot recall, which may have changed the model and
        the channel setup in use."""
        self._show_models(self.get_config())
        self._show_channels(self.get_config())

    def _show_models(self, config):
        """Vosk's model list, only while Vosk is the engine - Whisper's
        model is picked in Recognition."""
        vosk = config["engine"] == "vosk"
        if vosk:
            self.models.refresh()
            if not self.models.frame.winfo_ismapped():
                self.models.frame.pack(fill="x", pady=(12, 0),
                                       before=self.channels_frame)
        else:
            self.models.frame.pack_forget()

    def _toggle_enabled(self):
        enabled = self.enabled_var.get()
        self._change(lambda config: config.update(enabled=enabled))
        self._show_status()

    # --- Audio ---

    @staticmethod
    def _device_label(device):
        return f"{device.get('label') or device['name']}  —  " \
               f"{device['host_api']}, {device['channels']} in"

    def refresh_devices(self):
        self._devices = voice_to_text.input_devices()
        self._show_mics()
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

    # --- Channels ---

    def _user_names(self):
        return dict(self.get_users())

    def _show_scopes(self, config):
        live = self.get_live_snapshot()
        names = set(self.get_snapshots() or ()) | set(config["snapshot_channels"])
        if live:
            names.add(live)
        if self._scope:
            names.add(self._scope)
        self._scopes = [None] + sorted(names, key=str.lower)

        def label(scope):
            if scope is None:
                return GLOBAL_SCOPE
            text = scope
            if scope in config["snapshot_channels"]:
                text += " - own setup"
            if scope == live:
                text += " (live)"
            return text

        self.scope_combo.configure(values=[label(s) for s in self._scopes])
        self.scope_combo.current(self._scopes.index(self._scope))

        own = self._scope in config["snapshot_channels"]
        self.global_button.state(["!disabled"] if own else ["disabled"])
        live_own = live in config["snapshot_channels"]
        if self._scope is None:
            note = ("Used on every snapshot without a setup of its own.")
            if live_own:
                note += f" Not in use now: {live} has its own."
        elif own:
            note = (f"{self._scope} has a setup of its own, used while it "
                    "is live. Use Global Setup drops it.")
        else:
            note = (f"{self._scope} uses the global setup. A change here "
                    "gives it a setup of its own, starting from the global "
                    "one.")
        self.scope_note.configure(text=note)

    def _scope_chosen(self):
        index = self.scope_combo.current()
        if 0 <= index < len(self._scopes):
            self._scope = self._scopes[index]
        self._show_channels(self.get_config())

    def _use_global(self):
        scope = self._scope
        if scope is None:
            return
        self._change(lambda config: config["snapshot_channels"].pop(scope, None))
        self._show_channels(self.get_config())

    def _shown_setup(self, config):
        """The channels shown: the scope's own, else the global ones."""
        return voice_to_text.for_snapshot(config, self._scope)

    def _target(self, config):
        """The channels dict an edit goes into - on a snapshot without a
        setup of its own, a new one copied from the global setup."""
        if self._scope is None:
            return config["channels"]
        setups = config["snapshot_channels"]
        if self._scope not in setups:
            setups[self._scope] = copy.deepcopy(config["channels"])
        return setups[self._scope]

    def _viewing_live(self, config):
        """True if the setup shown is the one listening now."""
        live = self.get_live_snapshot()
        in_use = live if live in config["snapshot_channels"] else None
        return self._scope == in_use

    def _show_channels(self, config):
        self._show_scopes(config)
        names = self._user_names()
        selected = self.tree.selection()
        count = config["channel_count"]
        shown = self._shown_setup(config)
        live = self._viewing_live(config)

        if len(self.tree.get_children()) != count:
            self.tree.delete(*self.tree.get_children())
            for number in range(1, count + 1):
                self.tree.insert("", "end", iid=str(number), text=str(number))

        for number in range(1, count + 1):
            item = voice_to_text.channel(shown, number)
            user = item["user"]
            label = item["label"]
            if user is None:
                shown_user = ""
            elif user in names:
                # Labelled with the account's name - its username if it
                # has none.
                label = names[user]
                shown_user = user
            else:
                shown_user = f"{user} (account gone)"
            level = self.tree.set(str(number), "level") \
                if item["listen"] and live else ""
            self.tree.item(str(number), values=(
                "✔" if item["listen"] else "", label, shown_user, level,
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
        shown = self._shown_setup(self.get_config())
        # All on, unless every one picked is already on.
        turn_on = not all(voice_to_text.channel(shown, n)["listen"]
                          for n in numbers)

        def edit(config):
            channels = self._target(config)
            for number in numbers:
                item = dict(voice_to_text.channel({"channels": channels},
                                                  number))
                item["listen"] = turn_on
                channels[str(number)] = item

        self._change(edit)
        self._show_channels(self.get_config())
        return "break"

    def _clear_channels(self):
        numbers = self._selected_numbers()
        if not numbers:
            return

        def edit(config):
            channels = self._target(config)
            for number in numbers:
                channels.pop(str(number), None)

        self._change(edit)
        self._show_channels(self.get_config())

    def _edit_channel(self):
        numbers = self._selected_numbers()
        if len(numbers) != 1:
            return
        number = numbers[0]
        item = voice_to_text.channel(self._shown_setup(self.get_config()),
                                     number)

        def save(listen, label, user):
            def edit(config):
                self._target(config)[str(number)] = {
                    "listen": listen, "label": label, "user": user,
                }
            self._change(edit)
            self._show_channels(self.get_config())

        title = f"Channel {number}" + (f" - {self._scope}" if self._scope
                                       else "")
        ChannelDialog(self.frame, number, item, self.get_users(),
                      on_saved=save, title=title)

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
        self._show_test()
        self.recognition.poll()
        self._poll_job = self.frame.after(POLL_MS, self._poll)

    def _show_status(self):
        engine = self.engine
        state = engine.state if engine is not None else "off"
        message = engine.message if engine is not None else "Off"
        color = {"running": OK_FG, "error": ERROR_FG}.get(state, MUTED_FG)
        if self._test_device is not None:
            message = f"Mic test: {message}"
        self.status_label.configure(text=message, foreground=color)

    def _show_levels(self):
        if self._test_device is not None:
            # The test mic's level is not input 1's.
            return
        config = self.get_config()
        if not self._viewing_live(config):
            # Another snapshot's setup: the levels are not its channels'.
            return
        levels = self.engine.levels() if self.engine is not None else {}
        gate = config["gate_db"]
        for number, level in levels.items():
            row = str(number)
            if not self.tree.exists(row):
                continue
            # A bar of 12 steps over -60..0 dBFS, open when over the gate.
            steps = max(0, min(12, round((level + 60) / 5)))
            bar = "█" * steps + "░" * (12 - steps)
            mark = " ●" if level >= gate else ""
            self.tree.set(row, "level", bar + mark)


class RecognitionPanel:
    """Settings > Voice to Text's Recognition: which engine, and for
    Whisper its model, where it runs and the languages it listens for;
    then scripture detection and the corrections file, for either.

    `change(edit)` applies edit(config) to a copy and saves it, which
    restarts Voice to Text on the new setup."""

    def __init__(self, parent, get_config, change, engine,
                 on_engine=lambda: None):
        """on_engine() is called after the engine is switched."""
        self.get_config = get_config
        self.change = change
        self.engine = engine
        self.on_engine = on_engine
        self._download = None
        self._gpus = None
        self._gpu_missing = None

        self.frame = ttk.LabelFrame(parent, text="Recognition", padding=10)

        engines = ttk.Frame(self.frame)
        engines.pack(fill="x")
        self.engine_var = tk.StringVar()
        ttk.Radiobutton(
            engines, text="Whisper - accurate, English and Russian mixed, "
                          "best on an NVIDIA GPU",
            value="whisper", variable=self.engine_var,
            command=self._engine_chosen).pack(anchor="w")
        ttk.Radiobutton(
            engines, text="Vosk - light enough for any computer, one "
                          "language per model",
            value="vosk", variable=self.engine_var,
            command=self._engine_chosen).pack(anchor="w", pady=(2, 0))

        # --- Whisper ---
        self.whisper = ttk.Frame(self.frame)
        whisper = self.whisper
        whisper.columnconfigure(1, weight=1)

        ttk.Label(whisper, text="Model").grid(row=0, column=0, sticky="w")
        model_row = ttk.Frame(whisper)
        model_row.grid(row=0, column=1, sticky="w", padx=(10, 0))
        self.model_combo = ttk.Combobox(
            model_row, state="readonly", width=44,
            values=[f"{name} - {size} - {note}"
                    for name, size, note in voice_whisper.MODELS])
        self.model_combo.pack(side="left")
        self.model_combo.bind("<<ComboboxSelected>>",
                              lambda event: self._model_chosen())
        self.download_button = ttk.Button(model_row, text="Download",
                                          command=self._start_download)
        self.download_button.pack(side="left", padx=(8, 0))
        self.model_note = ttk.Label(whisper, foreground=MUTED_FG,
                                    wraplength=600, justify="left")
        self.model_note.grid(row=1, column=1, sticky="w", padx=(10, 0),
                             pady=(4, 0))

        ttk.Label(whisper, text="Run on").grid(row=2, column=0, sticky="w",
                                               pady=(8, 0))
        device_row = ttk.Frame(whisper)
        device_row.grid(row=2, column=1, sticky="w", padx=(10, 0),
                        pady=(8, 0))
        self._device_labels = {"auto": "NVIDIA GPU if there is one, else CPU",
                               "cuda": "NVIDIA GPU (CUDA)", "cpu": "CPU"}
        self.device_combo = ttk.Combobox(
            device_row, state="readonly", width=34,
            values=[self._device_labels[d] for d in voice_whisper.DEVICES])
        self.device_combo.pack(side="left")
        self.device_combo.bind("<<ComboboxSelected>>",
                               lambda event: self._device_chosen())
        self.gpu_note = ttk.Label(device_row, foreground=MUTED_FG)
        self.gpu_note.pack(side="left", padx=(10, 0))

        ttk.Label(whisper, text="Languages").grid(row=3, column=0,
                                                  sticky="nw", pady=(8, 0))
        languages = ttk.Frame(whisper)
        languages.grid(row=3, column=1, sticky="w", padx=(10, 0),
                       pady=(8, 0))
        self.language_vars = {}
        for index, (code, name) in enumerate(voice_whisper.LANGUAGES):
            var = tk.BooleanVar()
            self.language_vars[code] = var
            ttk.Checkbutton(languages, text=name, variable=var,
                            command=self._languages_chosen).grid(
                row=index // 5, column=index % 5, sticky="w", padx=(0, 12))
        ttk.Label(
            whisper,
            text="Each sentence is read in whichever of these it sounds "
                 "like, so a leader can move between English and Russian "
                 "without anyone switching anything. Tick only the ones "
                 "you need - fewer is more reliable. None ticked means any "
                 "language.",
            foreground=MUTED_FG, wraplength=600, justify="left"
        ).grid(row=4, column=1, sticky="w", padx=(10, 0), pady=(4, 0))

        # --- Either engine ---
        common = ttk.Frame(self.frame)
        common.pack(fill="x", side="bottom", pady=(10, 0))
        self.scripture_var = tk.BooleanVar()
        ttk.Checkbutton(
            common, text="Pick out scripture references - “Matthew chapter "
                         "four verse five” shows as Matthew 4:5",
            variable=self.scripture_var,
            command=self._scripture_chosen).pack(anchor="w")
        corrections = ttk.Frame(common)
        corrections.pack(fill="x", pady=(8, 0))
        ttk.Button(corrections, text="Edit Corrections...",
                   command=self._open_corrections).pack(side="left")
        ttk.Label(
            corrections,
            text="Words it keeps getting wrong, and what to write instead - "
                 "names, songs. Bible books and worship terms are built in.",
            foreground=MUTED_FG, wraplength=460, justify="left"
        ).pack(side="left", padx=(10, 0))

        self.refresh()

    # --- Showing ---

    def refresh(self):
        config = self.get_config()
        self.engine_var.set(config["engine"])
        self.scripture_var.set(config["scripture"])
        whisper = config["whisper"]
        self.model_combo.current(voice_whisper.MODEL_NAMES.index(whisper["model"]))
        self.device_combo.current(voice_whisper.DEVICES.index(whisper["device"]))
        for code, var in self.language_vars.items():
            var.set(code in whisper["languages"])

        if config["engine"] == "whisper":
            if not self.whisper.winfo_ismapped():
                self.whisper.pack(fill="x", pady=(10, 0))
        else:
            self.whisper.pack_forget()
        self._show_model_note()

    def _show_model_note(self):
        config = self.get_config()
        name = config["whisper"]["model"]
        problem = voice_whisper.unavailable_reason() \
            if config["engine"] == "whisper" else None
        busy = self._download is not None
        if problem:
            note = problem
            have = False
        elif busy:
            note = f"Downloading {self._download['name']} " \
                   f"({voice_whisper.model_size(self._download['name'])})..."
            have = False
        else:
            have = voice_whisper.is_downloaded(name)
            note = (f"{name} is on this computer." if have else
                    f"{name} is not downloaded yet - it is fetched from "
                    "Hugging Face the first time Voice to Text starts, or "
                    "now with Download.")
            if name == "medium" and self._gpu_count() == 0:
                note += (" Without an NVIDIA GPU, medium runs seconds "
                         "behind - small or base keep up better on a CPU.")
        self.model_note.configure(text=note,
                                  foreground=ERROR_FG if problem else MUTED_FG)
        self.download_button.state(
            ["disabled"] if busy or have or problem else ["!disabled"])

        gpus = self._gpu_count()
        if problem:
            gpu = ""
        elif not gpus:
            gpu = "No NVIDIA GPU found"
        elif self._gpu_missing:
            gpu = f"NVIDIA GPU found, but {self._gpu_missing} is missing - CPU only"
        else:
            gpu = f"{gpus} NVIDIA GPU{'s' if gpus > 1 else ''} found"
        self.gpu_note.configure(text=gpu)

    def _gpu_count(self):
        if self._gpus is None:
            self._gpus = voice_whisper.cuda_devices() \
                if voice_whisper.unavailable_reason() is None else 0
            self._gpu_missing = voice_whisper.missing_cuda_library() \
                if self._gpus else None
        return self._gpus

    def poll(self):
        """Called with Settings' status line, while it is shown."""
        if self._download is not None and not self._download["thread"].is_alive():
            result = self._download
            self._download = None
            if "error" in result:
                messagebox.showerror(
                    "Voice to Text", f"Whisper {result['name']} could not be "
                    f"downloaded: {result['error']}", parent=self.frame)
            self._show_model_note()

    # --- Changes ---

    def _engine_chosen(self):
        engine = self.engine_var.get()
        self.change(lambda config: config.update(engine=engine))
        self.refresh()
        self.on_engine()

    def _model_chosen(self):
        index = self.model_combo.current()
        if 0 <= index < len(voice_whisper.MODEL_NAMES):
            name = voice_whisper.MODEL_NAMES[index]
            self.change(lambda config: config["whisper"].update(model=name))
        self._show_model_note()

    def _device_chosen(self):
        index = self.device_combo.current()
        if 0 <= index < len(voice_whisper.DEVICES):
            device = voice_whisper.DEVICES[index]
            self.change(lambda config: config["whisper"].update(device=device))

    def _languages_chosen(self):
        chosen = [code for code, var in self.language_vars.items() if var.get()]
        self.change(lambda config: config["whisper"].update(languages=chosen))

    def _scripture_chosen(self):
        on = self.scripture_var.get()
        self.change(lambda config: config.update(scripture=on))

    def _start_download(self):
        if self._download is not None:
            return
        name = self.get_config()["whisper"]["model"]
        state = {"name": name}

        def run():
            try:
                voice_whisper.download(name)
            except Exception as ex:
                state["error"] = ex

        state["thread"] = threading.Thread(target=run, name="WhisperDownload",
                                           daemon=True)
        self._download = state
        state["thread"].start()
        self._show_model_note()

    def _open_corrections(self):
        try:
            path = str(voice_vocabulary.corrections_file())
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as ex:
            messagebox.showerror(
                "Voice to Text", f"Could not open the corrections file "
                f"({voice_vocabulary.CORRECTIONS_FILE}): {ex}",
                parent=self.frame)


class ChannelDialog:
    """One channel's settings: listened to or not, a label, and the
    account using it. on_saved(listen, label, user)."""

    def __init__(self, master, number, item, users, on_saved, title=None):
        self.on_saved = on_saved
        self.users = users
        self.names = dict(users)
        # The label typed in - kept while an account's name stands in.
        self._own_label = item["label"]

        self.window = tk.Toplevel(master)
        self.window.title(title or f"Channel {number}")
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
        self.user_combo.bind("<<ComboboxSelected>>",
                             lambda event: self._show_label())
        self._show_label()

        ttk.Label(
            frame,
            text="With an account, the channel is labelled with its name - "
                 "its username if it has none. Without one, the label is "
                 f"yours to type, or “Ch {number}”.",
            foreground=MUTED_FG, wraplength=320, justify="left"
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))

        btn_bar = ttk.Frame(frame)
        btn_bar.grid(row=4, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(btn_bar, text="Cancel",
                   command=self.window.destroy).pack(side="right")
        ttk.Button(btn_bar, text="Save", style="Accent.TButton",
                   command=self.save).pack(side="right", padx=(0, 5))

        self.user_combo.focus_set()
        self.window.bind("<Return>", lambda event: self.save())
        self.window.bind("<Escape>", lambda event: self.window.destroy())
        self.window.grab_set()

    def _user(self):
        index = self.user_combo.current()
        return self.choices[index] if 0 <= index < len(self.choices) else None

    def _show_label(self):
        """The account's name in the label box while one is picked."""
        user = self._user()
        if str(self.label_entry.cget("state")) != "disabled":
            self._own_label = self.label_entry.get()
        self.label_entry.configure(state="normal")
        self.label_entry.delete(0, "end")
        if user in self.names:
            self.label_entry.insert(0, self.names[user])
            self.label_entry.configure(state="disabled")
        else:
            self.label_entry.insert(0, self._own_label)

    def save(self):
        user = self._user()
        typed = self._own_label if user in self.names else self.label_entry.get()
        label = " ".join(typed.split())[:voice_to_text.MAX_LABEL]
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
