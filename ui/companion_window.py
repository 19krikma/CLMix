"""Settings > BitFocus: the Bitfocus Companion bridge and its rules.

See services/companion_bridge.py for what the bridge does. This is only
its controls: the on/off switch, where Companion is, whether traffic is
passed through untouched, the translation rules, and a running list of
what Companion has been sending - which is the easiest way to find out
what address to write a rule for.
"""

import copy
import tkinter as tk
from tkinter import messagebox, ttk

from services.companion_bridge import (
    ARG_TYPES, COMPANION_TIMEOUT_SECONDS, MODE_FIXED, MODE_NONE, MODE_PASS,
    MODE_SCALE, MODE_STEP, MODE_TOGGLE, TO_COMPANION, TO_CONSOLE, compile_pattern,
    default_rule, missing_default_rules, normalize_rule, normalize_settings,
)
from services.network_info import list_ipv4_interfaces
from ui.companion_rulebook import RuleBookWindow

DIRECTION_LABELS = {
    TO_CONSOLE: "Companion → Console",
    TO_COMPANION: "Console → Companion",
}

MODE_LABELS = {
    MODE_PASS: "Pass value through",
    MODE_FIXED: "Fixed value",
    MODE_TOGGLE: "Toggle on/off",
    MODE_STEP: "Step up/down",
    MODE_SCALE: "Scale value",
    MODE_NONE: "No value",
}

ARG_LABELS = {
    "auto": "Auto",
    "float": "Float",
    "int": "Integer",
    "string": "String",
}

# Rows the activity list keeps before trimming its oldest.
ACTIVITY_MAX_ROWS = 200

WRAP = 780


def _label_to_key(labels, label):
    for key, text in labels.items():
        if text == label:
            return key
    return None


def _value_summary(rule):
    mode = rule["mode"]

    if mode == MODE_FIXED:
        return rule["value"] or "(empty)"
    if mode == MODE_STEP:
        step = rule["step"]
        if rule["step_by_incoming"]:
            text = "value" if step in ("1", "1.0") else f"{step} × value"
        else:
            text = step if step.startswith("-") else f"+{step}"
        limits = [f"{word} {rule[key]}" for word, key in
                  (("min", "step_min"), ("max", "step_max")) if rule[key]]
        return f"{text} ({', '.join(limits)})" if limits else text
    if mode == MODE_SCALE:
        return (f"{rule['in_min']}..{rule['in_max']} → "
                f"{rule['out_min']}..{rule['out_max']}")
    return MODE_LABELS[mode]


class CompanionPanel:
    """The BitFocus section of Settings. Built once and kept, like the
    other sections; refresh() is called from the main window's message
    loop to keep the status line and the activity list current.

    on_apply() is called whenever something the running bridge needs to
    know has changed - MainWindow starts, reconfigures or stops it from
    settings["companion"].
    """

    def __init__(self, parent, settings, save_settings, on_apply,
                 get_bridge, get_error, get_worker):
        self.settings = settings
        self.save_settings = save_settings
        self.on_apply = on_apply
        self.get_bridge = get_bridge
        self.get_error = get_error
        self.get_worker = get_worker

        self.container = parent
        self.window = parent.winfo_toplevel()

        self._status_shown = None
        self._activity_seq = 0
        self._activity_bridge = None
        self.rule_book = None

        self.build_ui()

    # ------------------------------------------------------------- settings

    @property
    def config(self):
        config = normalize_settings(self.settings.get("companion"))
        self.settings["companion"] = config
        return config

    def _save(self, restart=False):
        self.save_settings()
        self.on_apply(restart)

    def reload(self):
        """Settings were restored from a backup - show what they say now."""
        config = self.config
        self.enabled_var.set(config["enabled"])
        self.pass_console_var.set(config["pass_to_console"])
        self.pass_companion_var.set(config["pass_to_companion"])

        for entry, value in ((self.listen_entry, config["listen_port"]),
                             (self.ip_entry, config["companion_ip"]),
                             (self.port_entry, config["companion_port"])):
            entry.delete(0, tk.END)
            entry.insert(0, str(value))

        self.refresh_rules()
        self._refresh_hint()

    # ------------------------------------------------------------------ UI

    def build_ui(self):
        config = self.config

        frame = ttk.Frame(self.container, padding=15)
        frame.pack(fill="both", expand=True)

        heading = ttk.Frame(frame)
        heading.pack(fill="x")

        ttk.Label(heading, text="Bitfocus Companion",
                  font=("TkDefaultFont", 10, "bold")).pack(side="left")

        self.enabled_var = tk.BooleanVar(value=config["enabled"])
        ttk.Checkbutton(
            heading, style="Switch.TCheckbutton", variable=self.enabled_var,
            command=self._on_enabled_toggled
        ).pack(side="left", padx=(12, 0))

        ttk.Button(heading, text="Rule Book", command=self.open_rule_book) \
            .pack(side="right")

        ttk.Label(
            frame,
            text="CLMix stands in for the console so Companion can control "
                 "it through CLMix's own connection. Companion's DiGiCo "
                 "module works as if it were on the desk, and translation "
                 "rules turn any other OSC Companion sends into console "
                 "commands - or console changes into messages for "
                 "Companion. The console's External Control needs no "
                 "change.",
            wraplength=WRAP, justify="left"
        ).pack(fill="x", pady=(6, 0))

        self._build_connection(frame)

        self.status_label = ttk.Label(frame, text="", wraplength=WRAP,
                                      justify="left")
        self.status_label.pack(fill="x", pady=(8, 0))

        ttk.Separator(frame).pack(fill="x", pady=(10, 8))

        lower = ttk.Frame(frame)
        lower.pack(fill="both", expand=True)
        lower.columnconfigure(0, weight=3)
        lower.columnconfigure(1, weight=2)
        lower.rowconfigure(1, weight=1)

        self._build_rules(lower)
        self._build_activity(lower)

        self.refresh_rules()
        self.refresh()

    def _build_connection(self, parent):
        config = self.config
        port_vcmd = (parent.register(
            lambda p: p == "" or (p.isdigit() and len(p) <= 5)), "%P")

        grid = ttk.Frame(parent)
        grid.pack(fill="x", pady=(10, 0))

        ttk.Label(grid, text="Listen Port").grid(row=0, column=0, sticky="w")
        self.listen_entry = ttk.Entry(grid, width=8, validate="key",
                                      validatecommand=port_vcmd)
        self.listen_entry.insert(0, str(config["listen_port"]))
        self.listen_entry.grid(row=0, column=1, sticky="w", padx=5, pady=3)

        ttk.Label(grid, text="Companion IP").grid(row=0, column=2, sticky="w",
                                                  padx=(18, 0))
        self.ip_entry = ttk.Entry(grid, width=16)
        self.ip_entry.insert(0, config["companion_ip"])
        self.ip_entry.grid(row=0, column=3, sticky="w", padx=5, pady=3)

        ttk.Label(grid, text="Companion Port").grid(row=0, column=4, sticky="w",
                                                    padx=(18, 0))
        self.port_entry = ttk.Entry(grid, width=8, validate="key",
                                    validatecommand=port_vcmd)
        self.port_entry.insert(0, str(config["companion_port"]))
        self.port_entry.grid(row=0, column=5, sticky="w", padx=5, pady=3)

        ttk.Button(grid, text="Apply", command=self._on_apply_connection) \
            .grid(row=0, column=6, sticky="w", padx=(12, 0))

        self.pass_console_var = tk.BooleanVar(value=config["pass_to_console"])
        ttk.Checkbutton(
            grid, text="Send Companion's DiGiCo commands to the console",
            variable=self.pass_console_var, command=self._on_pass_toggled
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 0))

        self.pass_companion_var = tk.BooleanVar(value=config["pass_to_companion"])
        ttk.Checkbutton(
            grid, text="Send console replies to Companion",
            variable=self.pass_companion_var, command=self._on_pass_toggled
        ).grid(row=1, column=4, columnspan=3, sticky="w", pady=(4, 0))

        self.hint_label = ttk.Label(parent, text="", wraplength=WRAP,
                                    justify="left", foreground="#888888")
        self.hint_label.pack(fill="x", pady=(6, 0))
        self._refresh_hint()

    def _refresh_hint(self):
        config = self.config
        addresses = [ip for _label, ip in list_ipv4_interfaces()]
        here = " or ".join(addresses) if addresses else "this computer's IP"

        self.hint_label.config(
            text=f"In Companion, add a DiGiCo OSC connection with Mixer IP "
                 f"Address {here} (127.0.0.1 if Companion runs on this "
                 f"computer), Transmit Port {config['listen_port']} and "
                 f"Receive Port {config['companion_port']}. Leave Companion "
                 f"IP blank to answer whoever sends; set it to accept "
                 f"commands from that address only."
        )

    def _build_rules(self, parent):
        ttk.Label(parent, text="Translation Rules",
                  font=("TkDefaultFont", 10, "bold")) \
            .grid(row=0, column=0, sticky="w")

        box = ttk.Frame(parent)
        box.grid(row=1, column=0, sticky="nsew", padx=(0, 12), pady=(4, 0))

        columns = (
            ("on", "On", 34, "center", False),
            ("direction", "Direction", 140, "w", False),
            ("match", "When", 160, "w", True),
            ("target", "Send", 160, "w", True),
            ("value", "Value", 110, "w", True),
        )
        self.rules_tree = ttk.Treeview(
            box, columns=[c[0] for c in columns], show="headings", height=7,
            selectmode="browse"
        )
        for key, text, width, anchor, stretch in columns:
            self.rules_tree.heading(key, text=text)
            self.rules_tree.column(key, width=width, minwidth=30,
                                   anchor=anchor, stretch=stretch)

        scrollbar = ttk.Scrollbar(box, orient="vertical",
                                  command=self.rules_tree.yview)
        self.rules_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.rules_tree.pack(side="left", fill="both", expand=True)

        self.rules_tree.bind("<Double-1>", lambda e: self._edit_rule())
        self.rules_tree.bind("<<TreeviewSelect>>",
                             lambda e: self._update_rule_buttons())

        bar = ttk.Frame(parent)
        bar.grid(row=2, column=0, sticky="w", pady=(6, 0))

        ttk.Button(bar, text="Add", command=self._add_rule).pack(side="left")
        self.rule_buttons = [
            ttk.Button(bar, text="Edit", command=self._edit_rule),
            ttk.Button(bar, text="On/Off", command=self._toggle_rule),
            ttk.Button(bar, text="Up", width=4, command=lambda: self._move_rule(-1)),
            ttk.Button(bar, text="Down", width=5, command=lambda: self._move_rule(1)),
            ttk.Button(bar, text="Delete", command=self._delete_rule),
        ]
        for button in self.rule_buttons:
            button.pack(side="left", padx=(5, 0))

        ttk.Button(bar, text="Add Default Rules", command=self._add_defaults) \
            .pack(side="left", padx=(15, 0))

    def _build_activity(self, parent):
        ttk.Label(parent, text="Activity",
                  font=("TkDefaultFont", 10, "bold")) \
            .grid(row=0, column=1, sticky="w")

        box = ttk.Frame(parent)
        box.grid(row=1, column=1, sticky="nsew", pady=(4, 0))

        self.activity = tk.Listbox(box, height=7, activestyle="none",
                                   exportselection=False, borderwidth=0,
                                   highlightthickness=0)
        scrollbar = ttk.Scrollbar(box, orient="vertical",
                                  command=self.activity.yview)
        self.activity.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.activity.pack(side="left", fill="both", expand=True)
        self.activity.bind("<Double-1>", lambda e: self._rule_from_activity())

        bar = ttk.Frame(parent)
        bar.grid(row=2, column=1, sticky="w", pady=(6, 0))

        ttk.Button(bar, text="Make Rule", command=self._rule_from_activity) \
            .pack(side="left")
        ttk.Button(bar, text="Clear",
                   command=lambda: self.activity.delete(0, tk.END)) \
            .pack(side="left", padx=(5, 0))

        # What each row came from, so Make Rule can prefill the address.
        self._activity_rows = []

    # ------------------------------------------------------------ handlers

    def _on_enabled_toggled(self):
        self.settings["companion"] = dict(self.config,
                                          enabled=self.enabled_var.get())
        self._save()

    def _on_pass_toggled(self):
        self.settings["companion"] = dict(
            self.config,
            pass_to_console=self.pass_console_var.get(),
            pass_to_companion=self.pass_companion_var.get(),
        )
        self._save()

    def _on_apply_connection(self):
        ip = self.ip_entry.get().strip()

        for name, entry in (("Listen Port", self.listen_entry),
                            ("Companion Port", self.port_entry)):
            value = entry.get().strip()
            if not value.isdigit() or not 1 <= int(value) <= 65535:
                messagebox.showerror("BitFocus", f"{name} must be 1-65535.",
                                     parent=self.window)
                return

        if ip:
            import ipaddress
            try:
                ipaddress.IPv4Address(ip)
            except ValueError:
                messagebox.showerror(
                    "BitFocus", "Companion IP must be an IPv4 address, or "
                                "blank to answer whoever sends.",
                    parent=self.window)
                return

        if self.listen_entry.get().strip() == self.settings.get("recv_port"):
            messagebox.showerror(
                "BitFocus", "Listen Port is already CLMix's Rec Port for the "
                            "console. Pick another.", parent=self.window)
            return

        self.settings["companion"] = dict(
            self.config,
            listen_port=int(self.listen_entry.get()),
            companion_ip=ip,
            companion_port=int(self.port_entry.get()),
        )
        self._save(restart=True)
        self._refresh_hint()

    # --------------------------------------------------------------- rules

    def refresh_rules(self, select=None):
        self.rules_tree.delete(*self.rules_tree.get_children())

        for index, rule in enumerate(self.config["rules"]):
            label = rule["name"]
            match = f"{label}: {rule['match']}" if label else rule["match"]
            self.rules_tree.insert(
                "", "end", iid=str(index),
                values=("●" if rule["enabled"] else "○",
                        DIRECTION_LABELS[rule["direction"]], match,
                        rule["target"], _value_summary(rule))
            )

        if select is not None and self.rules_tree.exists(str(select)):
            self.rules_tree.selection_set(str(select))
            self.rules_tree.see(str(select))

        self._update_rule_buttons()

    def _selected_index(self):
        selection = self.rules_tree.selection()
        return int(selection[0]) if selection else None

    def _update_rule_buttons(self):
        state = "normal" if self._selected_index() is not None else "disabled"
        for button in self.rule_buttons:
            button.config(state=state)

    def _set_rules(self, rules, select=None):
        self.settings["companion"] = dict(self.config, rules=rules)
        self._save()
        self.refresh_rules(select)

    def _add_rule(self, prefill=None):
        RuleDialog(self.window, prefill or default_rule(),
                   on_saved=lambda rule: self._set_rules(
                       self.config["rules"] + [rule],
                       select=len(self.config["rules"])))

    def _edit_rule(self):
        index = self._selected_index()
        if index is None:
            return

        rules = self.config["rules"]

        def saved(rule):
            rules[index] = rule
            self._set_rules(rules, select=index)

        RuleDialog(self.window, copy.deepcopy(rules[index]), on_saved=saved,
                   title="Edit Rule")

    def _toggle_rule(self):
        index = self._selected_index()
        if index is None:
            return

        rules = self.config["rules"]
        rules[index]["enabled"] = not rules[index]["enabled"]
        self._set_rules(rules, select=index)

    def _move_rule(self, step):
        index = self._selected_index()
        if index is None:
            return

        rules = self.config["rules"]
        other = index + step
        if not 0 <= other < len(rules):
            return

        rules[index], rules[other] = rules[other], rules[index]
        self._set_rules(rules, select=other)

    def _delete_rule(self):
        index = self._selected_index()
        if index is None:
            return

        rules = self.config["rules"]
        if not messagebox.askyesno(
                "Delete Rule", f"Delete the rule for {rules[index]['match']}?",
                parent=self.window):
            return

        del rules[index]
        self._set_rules(rules)

    def _add_defaults(self):
        rules = self.config["rules"]
        missing = missing_default_rules(rules)

        if not missing:
            messagebox.showinfo("Default Rules",
                                "All the default rules are already here.",
                                parent=self.window)
            return

        if not messagebox.askyesno(
                "Default Rules",
                f"Add {len(missing)} default rule(s)? Rules already here, "
                f"edited or not, are left as they are.", parent=self.window):
            return

        self._set_rules(rules + missing)

    def open_rule_book(self):
        if self.rule_book is not None and self.rule_book.window.winfo_exists():
            self.rule_book.lift()
            return

        self.rule_book = RuleBookWindow(
            self.window, on_new_rule=lambda rule: self._add_rule(prefill=rule))

    def _rule_from_activity(self):
        selection = self.activity.curselection()
        if not selection:
            return

        direction, address = self._activity_rows[selection[0]]
        rule = default_rule(TO_CONSOLE if direction == "IN" else TO_COMPANION)
        rule["match"] = address
        self._add_rule(prefill=rule)

    # -------------------------------------------------------------- status

    def _status_text(self):
        config = self.config

        if not config["enabled"]:
            return "Off."

        error = self.get_error()
        if error:
            return f"Could not start: {error}"

        bridge = self.get_bridge()
        if bridge is None:
            return "Starting..."

        listening = f"Listening on port {bridge.listen_port}."
        worker = self.get_worker()
        if worker is None or not worker.is_alive() or not worker.loaded:
            console = (" Not connected to the console - Companion's "
                       "commands are dropped until CLMix connects.")
        else:
            console = ""

        heard = bridge.seconds_since_heard()
        destination = bridge.companion_address()

        if heard is None or heard > COMPANION_TIMEOUT_SECONDS:
            waiting = "Waiting for Companion." if heard is None else \
                f"Companion last heard {int(heard)}s ago."
            return f"{listening} {waiting}{console}"

        where = f"{destination[0]}:{destination[1]}" if destination else "?"
        return (f"Companion connected at {where}. "
                f"{bridge.packets_from_companion} in, "
                f"{bridge.packets_to_companion} out, "
                f"{bridge.translated} translated.{console}")

    def refresh(self):
        text = self._status_text()
        if text != self._status_shown:
            self._status_shown = text
            self.status_label.config(text=text)

        bridge = self.get_bridge()
        if bridge is not self._activity_bridge:
            # A fresh bridge counts from 1 again.
            self._activity_bridge = bridge
            self._activity_seq = 0

        if bridge is None:
            return

        records = bridge.recent(self._activity_seq)
        if not records:
            return

        at_bottom = self.activity.yview()[1] >= 0.999

        for seq, (stamp, direction, text) in records:
            self._activity_seq = seq
            arrow = "→" if direction == "IN" else "←"
            self.activity.insert(tk.END, f"{stamp} {arrow} {text}")
            self._activity_rows.append((direction, text.split()[0]))

        overflow = self.activity.size() - ACTIVITY_MAX_ROWS
        if overflow > 0:
            self.activity.delete(0, overflow - 1)
            del self._activity_rows[:overflow]

        if at_bottom:
            self.activity.see(tk.END)


class RuleDialog:
    """Add or edit one translation rule."""

    def __init__(self, master, rule, on_saved, title="Add Rule"):
        self.rule = normalize_rule(rule)
        self.on_saved = on_saved

        self.window = tk.Toplevel(master)
        self.window.title(title)
        self.window.transient(master)
        self.window.resizable(False, False)

        frame = ttk.Frame(self.window, padding=15)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        row = 0

        def label(text, at_row):
            ttk.Label(frame, text=text).grid(row=at_row, column=0, sticky="w",
                                             pady=3, padx=(0, 10))

        label("Name", row)
        self.name_entry = ttk.Entry(frame, width=44)
        self.name_entry.insert(0, self.rule["name"])
        self.name_entry.grid(row=row, column=1, columnspan=3, sticky="ew", pady=3)
        row += 1

        label("Direction", row)
        self.direction_combo = ttk.Combobox(
            frame, state="readonly", width=24,
            values=list(DIRECTION_LABELS.values()))
        self.direction_combo.set(DIRECTION_LABELS[self.rule["direction"]])
        self.direction_combo.grid(row=row, column=1, sticky="w", pady=3)
        self.direction_combo.bind("<<ComboboxSelected>>",
                                  lambda e: self._sync_fields())
        row += 1

        self.match_label = ttk.Label(frame, text="")
        self.match_label.grid(row=row, column=0, sticky="w", pady=3, padx=(0, 10))
        self.match_entry = ttk.Entry(frame, width=44)
        self.match_entry.insert(0, self.rule["match"])
        self.match_entry.grid(row=row, column=1, columnspan=3, sticky="ew", pady=3)
        row += 1

        self.target_label = ttk.Label(frame, text="")
        self.target_label.grid(row=row, column=0, sticky="w", pady=3, padx=(0, 10))
        self.target_entry = ttk.Entry(frame, width=44)
        self.target_entry.insert(0, self.rule["target"])
        self.target_entry.grid(row=row, column=1, columnspan=3, sticky="ew", pady=3)
        row += 1

        label("Value", row)
        self.mode_combo = ttk.Combobox(frame, state="readonly", width=24,
                                       values=list(MODE_LABELS.values()))
        self.mode_combo.set(MODE_LABELS[self.rule["mode"]])
        self.mode_combo.grid(row=row, column=1, sticky="w", pady=3)
        self.mode_combo.bind("<<ComboboxSelected>>", lambda e: self._sync_fields())

        ttk.Label(frame, text="Type").grid(row=row, column=2, sticky="e",
                                           padx=(10, 5))
        self.type_combo = ttk.Combobox(frame, state="readonly", width=9,
                                       values=[ARG_LABELS[t] for t in ARG_TYPES])
        self.type_combo.set(ARG_LABELS[self.rule["arg_type"]])
        self.type_combo.grid(row=row, column=3, sticky="w", pady=3)
        row += 1

        self.fixed_label = ttk.Label(frame, text="Fixed Value")
        self.fixed_label.grid(row=row, column=0, sticky="w", pady=3)
        self.fixed_entry = ttk.Entry(frame, width=24)
        self.fixed_entry.insert(0, self.rule["value"])
        self.fixed_entry.grid(row=row, column=1, sticky="w", pady=3)
        self.fixed_row = (self.fixed_label, self.fixed_entry)
        row += 1

        self.scale_label = ttk.Label(frame, text="Scale")
        self.scale_label.grid(row=row, column=0, sticky="w", pady=3)
        self.scale_frame = ttk.Frame(frame)
        self.scale_frame.grid(row=row, column=1, columnspan=3, sticky="w", pady=3)
        self.scale_entries = {}
        for key, before in (("in_min", "From"), ("in_max", "to"),
                            ("out_min", "  →  "), ("out_max", "to")):
            ttk.Label(self.scale_frame, text=before).pack(side="left", padx=(0, 4))
            entry = ttk.Entry(self.scale_frame, width=7)
            entry.insert(0, self.rule[key])
            entry.pack(side="left", padx=(0, 4))
            self.scale_entries[key] = entry
        self.scale_row = (self.scale_label, self.scale_frame)
        row += 1

        self.step_label = ttk.Label(frame, text="Step")
        self.step_label.grid(row=row, column=0, sticky="w", pady=3)
        self.step_frame = ttk.Frame(frame)
        self.step_frame.grid(row=row, column=1, columnspan=3, sticky="w", pady=3)
        self.step_entries = {}
        for key, before in (("step", "By"), ("step_min", "  Min"),
                            ("step_max", "Max")):
            ttk.Label(self.step_frame, text=before).pack(side="left", padx=(0, 4))
            entry = ttk.Entry(self.step_frame, width=7)
            entry.insert(0, self.rule[key])
            entry.pack(side="left", padx=(0, 4))
            self.step_entries[key] = entry
        self.step_incoming_var = tk.BooleanVar(value=self.rule["step_by_incoming"])
        ttk.Checkbutton(
            self.step_frame, text="× incoming value",
            variable=self.step_incoming_var
        ).pack(side="left", padx=(8, 0))
        self.step_row = (self.step_label, self.step_frame)
        row += 1

        self.port_label = ttk.Label(frame, text="Send to Port")
        self.port_label.grid(row=row, column=0, sticky="w", pady=3)
        self.port_entry = ttk.Entry(frame, width=8)
        self.port_entry.insert(0, self.rule["port"])
        self.port_entry.grid(row=row, column=1, sticky="w", pady=3)
        self.port_note = ttk.Label(
            frame, text="Blank = Companion Port. 12321 = Companion's own OSC API.",
            foreground="#888888")
        self.port_note.grid(row=row, column=2, columnspan=2, sticky="w")
        self.port_row = (self.port_label, self.port_entry, self.port_note)
        row += 1

        self.keep_var = tk.BooleanVar(value=self.rule["keep_original"])
        ttk.Checkbutton(frame, text="Also pass the original message on",
                        variable=self.keep_var) \
            .grid(row=row, column=1, columnspan=3, sticky="w", pady=(6, 0))
        row += 1

        self.enabled_var = tk.BooleanVar(value=self.rule["enabled"])
        ttk.Checkbutton(frame, text="Enabled", variable=self.enabled_var) \
            .grid(row=row, column=1, columnspan=3, sticky="w", pady=(2, 0))
        row += 1

        self.help_label = ttk.Label(frame, text="", wraplength=440,
                                    justify="left", foreground="#888888")
        self.help_label.grid(row=row, column=0, columnspan=4, sticky="w",
                             pady=(10, 0))
        row += 1

        bar = ttk.Frame(frame)
        bar.grid(row=row, column=0, columnspan=4, sticky="e", pady=(12, 0))
        ttk.Button(bar, text="Cancel", command=self.window.destroy) \
            .pack(side="right")
        ttk.Button(bar, text="Save", style="Accent.TButton",
                   command=self.save).pack(side="right", padx=(0, 5))

        self.window.bind("<Escape>", lambda e: self.window.destroy())
        self.window.bind("<Return>", lambda e: self.save())

        self._sync_fields()
        self.match_entry.focus_set()
        self.window.wait_visibility()
        self.window.grab_set()

    def _direction(self):
        return _label_to_key(DIRECTION_LABELS, self.direction_combo.get())

    def _mode(self):
        return _label_to_key(MODE_LABELS, self.mode_combo.get())

    def _sync_fields(self):
        direction = self._direction()
        mode = self._mode()

        if direction == TO_CONSOLE:
            self.match_label.config(text="When Companion Sends")
            self.target_label.config(text="Send Console")
            examples = ("Example: when Companion sends /clmix/mute/*, send "
                        "the console /Input_Channels/{1}/mute with Toggle "
                        "on/off.")
        else:
            self.match_label.config(text="When Console Sends")
            self.target_label.config(text="Send Companion")
            examples = ("Example: when the console sends "
                        "/Input_Channels/*/mute, send Companion "
                        "/custom-variable/ch{1}_mute/value with Pass value "
                        "through and Type String, to port 12321.")

        if mode == MODE_STEP:
            examples = ("Step reads the target's current value and adds Step "
                        "every time the message arrives - for Companion's "
                        "rotate left/right. Tick × incoming value and have "
                        "Companion send -1 on rotate left and 1 on rotate "
                        "right to the same address: one rule does both. "
                        "Without it, Step is added whatever value arrives, "
                        "so make one rule per direction with a negative step "
                        "for down. Faders run -150 to 10 dB.")

        self.help_label.config(
            text="* matches any one part of the address, and {1}, {2}... "
                 "put what it matched into Send. {value} is the incoming "
                 "value. " + examples)

        for widgets, shown in ((self.fixed_row, mode == MODE_FIXED),
                               (self.scale_row, mode == MODE_SCALE),
                               (self.step_row, mode == MODE_STEP),
                               (self.port_row, direction == TO_COMPANION)):
            for widget in widgets:
                if shown:
                    widget.grid()
                else:
                    widget.grid_remove()

    def save(self):
        direction = self._direction()
        mode = self._mode()
        match = self.match_entry.get().strip()
        target = self.target_entry.get().strip()

        problem = None
        if compile_pattern(match) is None:
            problem = "The incoming address must start with /."
        elif not target.startswith("/"):
            problem = "The address to send must start with /."
        elif mode in (MODE_TOGGLE, MODE_STEP) and direction == TO_COMPANION:
            problem = (f"{MODE_LABELS[mode]} only works for Companion → "
                       f"Console rules.")
        elif mode == MODE_STEP:
            texts = {key: e.get().strip() for key, e in self.step_entries.items()}
            try:
                step = float(texts["step"])
                limits = [float(texts[k]) for k in ("step_min", "step_max")
                          if texts[k]]
            except ValueError:
                problem = "Step must be a number, and Min and Max numbers or blank."
            else:
                if step == 0:
                    problem = "Step can't be 0. Use a negative step to go down."
                elif len(limits) == 2 and limits[0] > limits[1]:
                    problem = "Min must be below Max."
        elif mode == MODE_SCALE:
            try:
                values = [float(e.get()) for e in self.scale_entries.values()]
            except ValueError:
                problem = "All four scale values must be numbers."
            else:
                if values[0] == values[1]:
                    problem = "The incoming range can't start and end at the same value."

        port = self.port_entry.get().strip()
        if not problem and direction == TO_COMPANION and port and not (
                port.isdigit() and 1 <= int(port) <= 65535):
            problem = "Port must be 1-65535, or blank."

        if problem:
            messagebox.showerror("Rule", problem, parent=self.window)
            return

        rule = dict(
            self.rule,
            name=self.name_entry.get().strip(),
            direction=direction,
            match=match,
            target=target,
            mode=mode,
            value=self.fixed_entry.get().strip(),
            arg_type=_label_to_key(ARG_LABELS, self.type_combo.get()),
            port=port if direction == TO_COMPANION else "",
            keep_original=self.keep_var.get(),
            enabled=self.enabled_var.get(),
            **{key: entry.get().strip() for key, entry in self.scale_entries.items()},
            **{key: entry.get().strip() for key, entry in self.step_entries.items()},
            step_by_incoming=self.step_incoming_var.get(),
        )

        self.on_saved(normalize_rule(rule))
        self.window.destroy()
