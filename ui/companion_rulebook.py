"""The Rule Book: reference for writing Companion translation rules.

Opened from Settings > BitFocus. Four tabs - how rules work, the default
rules and what Companion should send for each, the console addresses
worth targeting (from docs/mixer_protocol/PROTOCOL.md), and how to set
Companion up. A console address can be turned straight into a new rule.
"""

import tkinter as tk
from tkinter import ttk

from services.companion_bridge import (
    MODE_FIXED, MODE_PASS, MODE_STEP, MODE_TOGGLE, TO_CONSOLE, default_rule,
    default_rules,
)

# (address, what it is, values). {n} is the strip number, 1-based as on
# the surface; {a} an aux, {b} an EQ band (1-4). Every one of these was
# read or written on the desk unless it says otherwise.
CONSOLE_ADDRESSES = (
    ("Input channels", (
        ("/Input_Channels/{n}/mute", "Mute", "0 off, 1 on"),
        ("/Input_Channels/{n}/solo", "Solo", "0 off, 1 on"),
        ("/Input_Channels/{n}/fader", "Fader level", "dB, -150 (off) to 10"),
        ("/Input_Channels/{n}/Panner/pan", "Pan", "0 left, 0.5 centre, 1 right"),
        ("/Input_Channels/{n}/Channel_Input/name", "Channel name", "text"),
        ("/Input_Channels/{n}/Channel_Input/analog_gain", "Head-amp gain",
         "dB, -20 to 60"),
        ("/Input_Channels/{n}/Channel_Input/trim", "Digital trim", "dB, -40 to 60"),
        ("/Input_Channels/{n}/Channel_Input/phantom", "48V phantom", "0 off, 1 on"),
        ("/Input_Channels/{n}/Channel_Input/phase", "Polarity",
         "0 normal, 1 inverted (stereo: 0-3)"),
        ("/Input_Channels/{n}/Channel_Input/main/alt_in", "Main / ALT input",
         "0 main, 1 alt"),
        ("/Input_Channels/{n}/Channel_Delay/delay", "Delay", "seconds, 0 to 1.3"),
        ("/Input_Channels/{n}/EQ/eq_in", "EQ in", "0 out, 1 in"),
        ("/Input_Channels/{n}/EQ/eq_gain_{b}", "EQ band gain", "dB, -18 to 18"),
        ("/Input_Channels/{n}/EQ/eq_freq_{b}", "EQ band frequency",
         "Hz, 20 to 20000"),
        ("/Input_Channels/{n}/Filters/lo_filter_in", "High-pass filter in",
         "0 out, 1 in"),
        ("/Input_Channels/{n}/Filters/lo_filter_freq", "High-pass frequency",
         "Hz, 20 to 20000"),
        ("/Input_Channels/{n}/Dynamics/comp_in", "Compressor in", "0 out, 1 in"),
        ("/Input_Channels/{n}/Dynamics/comp_thresh_1", "Compressor threshold",
         "dB, -60 to 0"),
        ("/Input_Channels/{n}/Dynamics/gate_in", "Gate in", "0 out, 1 in"),
        ("/Input_Channels/{n}/Aux_Send/{a}/send_on", "In aux mix", "0 out, 1 in"),
        ("/Input_Channels/{n}/Aux_Send/{a}/send_level", "Aux send level",
         "dB, -150 (off) to 10"),
        ("/Input_Channels/{n}/Aux_Send/{a}/send_pan", "Aux send pan (stereo aux)",
         "0 left, 0.5 centre, 1 right"),
    )),
    ("Aux outputs", (
        ("/Aux_Outputs/{n}/mute", "Mute", "0 off, 1 on"),
        ("/Aux_Outputs/{n}/solo", "Solo", "0 off, 1 on"),
        ("/Aux_Outputs/{n}/fader", "Master level", "dB, -150 (off) to 10"),
        ("/Aux_Outputs/{n}/Buss_Trim/name", "Name", "text"),
    )),
    ("Group outputs", (
        ("/Group_Outputs/{n}/mute", "Mute", "0 off, 1 on"),
        ("/Group_Outputs/{n}/solo", "Solo", "0 off, 1 on"),
        ("/Group_Outputs/{n}/fader", "Level", "dB, -150 (off) to 10"),
        ("/Group_Outputs/{n}/Buss_Trim/name", "Name", "text"),
    )),
    ("Matrix outputs", (
        ("/Matrix_Outputs/{n}/mute", "Mute", "0 off, 1 on"),
        ("/Matrix_Outputs/{n}/solo", "Solo", "0 off, 1 on"),
        ("/Matrix_Outputs/{n}/fader", "Level", "dB, -150 (off) to 10"),
        ("/Matrix_Outputs/{n}/Buss_Trim/name", "Name", "text"),
    )),
    ("DCAs (Control Groups)", (
        ("/Control_Groups/{n}/mute", "Mute", "0 off, 1 on"),
        ("/Control_Groups/{n}/solo", "Solo", "0 off, 1 on"),
        ("/Control_Groups/{n}/fader", "Level", "dB, -150 (off) to 10"),
        ("/Control_Groups/{n}/name", "Name", "text"),
    )),
    ("Snapshots", (
        ("/Snapshots/Recall_Snapshot/{n}", "Recall a snapshot (untested)",
         "send 0 as an integer; {n} is 0-based"),
        ("/Snapshots/Current_Snapshot", "Current snapshot (reported)",
         "integer, 0-based"),
        ("/Snapshots/Fire_Next_Snapshot", "Next snapshot (untested guess)", "none"),
        ("/Snapshots/Fire_Prev_Snapshot", "Previous snapshot (untested guess)",
         "none"),
    )),
    ("Console", (
        ("/Console/Name", "Console name (reported)", "text"),
        ("/Console/Session/Filename", "Loaded session (reported)", "text"),
    )),
)

# Console categories as default_rules() spells them on the Companion side.
SHORT_NAMES = {
    "Input_Channels": "ch",
    "Aux_Outputs": "aux",
    "Group_Outputs": "group",
    "Matrix_Outputs": "matrix",
    "Control_Groups": "dca",
    "Snapshots": "snapshot",
}

HOW_RULES_WORK = """\
A rule says: when this address arrives, send that one instead.

DIRECTION
  Companion → Console   Companion sends something; CLMix sends the console a command.
  Console → Companion   The console reports a change; CLMix sends Companion a message
                        (feedback - button colours, text, variables).

ADDRESSES
  *        matches any one part of an address. /clmix/ch/*/mute/on matches
           /clmix/ch/5/mute/on, but not /clmix/ch/5/mute.
  {1} {2}  put what the first, second * matched into the address you send.
           /clmix/ch/*/aux/*/on  →  /Input_Channels/{1}/Aux_Send/{2}/send_on
  {value}  the first value that came in, for a Fixed Value.

VALUE
  Pass value through   send on whatever value came in (a fader level, a pan).
  Fixed value          always send this value. Several values: separate with spaces.
  Toggle on/off        send 1 if the target is off now, 0 if it is on. CLMix reads the
                       console's current state; if it does not know it yet it asks the
                       console first, so even the first press is right.
  Step up/down         add Step to the target's current value on every message, kept
                       between Min and Max - for Companion's rotate left/right.
                       With "× incoming value" ticked the step is multiplied by the
                       value that arrives: send -1 to go down and 1 to go up, to the
                       same address. Unticked, Step is added whatever value arrives,
                       so you need one rule per direction (negative step for down).
                       The first tick only asks the console for the current level.
  Scale value          map the incoming value from one range onto another
                       (e.g. a 0-127 MIDI fader onto -150..10 dB).
  No value             send the address with nothing - a trigger, or a query (/?).

TYPE
  Auto sends numbers as floats, which is what the console uses for nearly
  everything. Companion's custom variables want String. Snapshot recall wants Integer.

ORDER AND OVERLAP
  Every enabled rule that matches runs, top to bottom - one Companion button
  can do several things. A matched message is not passed through as well,
  unless "Also pass the original message on" is ticked.

  Messages no rule matches are passed through unchanged if the pass-through
  switches are on. That is what lets Companion's DiGiCo module work as if it
  were on the desk, with no rules at all.

NUMBERING
  Channels, auxes, groups, matrices and DCAs count from 1, as on the surface.
  Snapshots count from 0. -150 dB is a fader or send at off.

THE ACTIVITY LIST
  Shows every message from Companion and what each rule did with it. Double-
  click a line to start a rule with that address filled in.
"""

COMPANION_SETUP = """\
Pick the way that suits the job. They can all be used at once.

1. COMPANION'S DIGICO MODULE  (no rules needed)
   In Companion: Connections → add "DiGiCo: OSC".
     Mixer IP Address   this computer's IP (127.0.0.1 if Companion runs here)
     Transmit Port      CLMix's Listen Port (7001 by default)
     Receive Port       CLMix's Companion Port (7000 by default)
   Leave both pass-through switches on in CLMix. The module's actions and
   feedbacks then work as if it were connected to the desk. Nothing needs
   changing on the console.

2. COMPANION'S GENERIC OSC MODULE  (with the default rules)
   In Companion: Connections → add "Generic: OSC".
     Target IP     this computer's IP
     Target Port   CLMix's Listen Port (7001)
   Then on a button: action "Send message without arguments" to e.g.
       /clmix/ch/5/mute/toggle
   or "Send float" to e.g. /clmix/ch/5/fader with -10.
   The Default Rules tab lists every address CLMix answers to out of the box.

3. ROTATE LEFT / RIGHT  (Stream Deck+ dials, encoders)
   The default rules already do this, 1 dB per tick, -150 to 10:
     /clmix/ch/*/fader/step  → /Input_Channels/{1}/fader   Step 1 × incoming value
   (and the same for aux, group, matrix and dca). In Companion, using the
   Generic OSC module pointed at CLMix's Listen Port:
     rotate right:  "Send float"  /clmix/ch/5/fader/step   value 1
     rotate left:   "Send float"  /clmix/ch/5/fader/step   value -1
   For finer steps send 0.5 / -0.5, or edit the rule's Step.

   Don't put a Step rule on the DiGiCo module's own fader address
   (/Input_Channels/*/fader): with "Relative" ticked the module works out
   the new level itself and sends an absolute value, which a Step rule
   would then step again. Either use the module's Relative on its own, or
   Generic OSC with the rule above.

4. FEEDBACK INTO COMPANION  (Console → Companion rules)
   Companion has an OSC server of its own (Settings → Protocols → OSC,
   port 12321). Useful addresses there:
     /custom-variable/<name>/value   set a custom variable (String)
     /location/<page>/<row>/<column>/style/bgcolor   button colour
     /location/<page>/<row>/<column>/style/text      button text
     /location/<page>/<row>/<column>/press           press a button
   Create the custom variables in Companion first (Variables → Custom).
   The two "Feedback" default rules are switched off until you have.

SECURITY
   CLMix listens on every network adapter. On a shared network, fill in
   Companion IP so only that machine can control the desk.
"""


def _example(address):
    """A pattern with its *s filled in, as a Companion button would send it."""
    numbers = iter(("5", "3", "1"))
    return "/".join(next(numbers, "1") if part == "*" else part
                    for part in address.split("/"))


def _default_value_hint(rule):
    if rule["mode"] in (MODE_FIXED, MODE_TOGGLE):
        return "nothing"
    if rule["mode"] == MODE_STEP:
        return "-1 down, 1 up (dB)"
    name = rule["name"].lower()
    if "pan" in name:
        return "0 to 1 (0.5 centre)"
    if "(db)" in name:
        return "dB, e.g. -10"
    return "a value"


class RuleBookWindow:
    """Non-modal, so it can sit beside the rule editor. on_new_rule(rule)
    opens the editor for a rule prefilled from an address picked here."""

    def __init__(self, master, on_new_rule):
        self.on_new_rule = on_new_rule

        self.window = tk.Toplevel(master)
        self.window.title("Rule Book")
        self.window.transient(master)
        self.window.geometry("860x560")
        self.window.minsize(600, 380)
        self.window.bind("<Escape>", lambda e: self.window.destroy())

        notebook = ttk.Notebook(self.window)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        notebook.add(self._text_tab(notebook, HOW_RULES_WORK), text="How Rules Work")
        notebook.add(self._defaults_tab(notebook), text="Default Rules")
        notebook.add(self._addresses_tab(notebook), text="Console Addresses")
        notebook.add(self._text_tab(notebook, COMPANION_SETUP), text="Companion Setup")

    def lift(self):
        self.window.deiconify()
        self.window.lift()
        self.window.focus_set()

    def _text_tab(self, parent, content):
        frame = ttk.Frame(parent)
        text = tk.Text(frame, wrap="word", borderwidth=0, highlightthickness=0,
                       padx=12, pady=10, font=("TkFixedFont", 10))
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        text.insert("1.0", content)
        # Read-only, but still selectable so an address can be copied.
        text.bind("<Key>", lambda e: None if (e.state & 0x4 and e.keysym.lower()
                                              in ("c", "a")) else "break")
        return frame

    def _tree(self, parent, columns):
        box = ttk.Frame(parent)
        tree = ttk.Treeview(box, columns=[c[0] for c in columns],
                            show="tree headings", selectmode="browse")
        tree.column("#0", width=18, stretch=False)
        for key, heading, width in columns:
            tree.heading(key, text=heading, anchor="w")
            tree.column(key, width=width, anchor="w", stretch=True)
        scrollbar = ttk.Scrollbar(box, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)
        return box, tree

    def _defaults_tab(self, parent):
        frame = ttk.Frame(parent, padding=8)

        ttk.Label(
            frame, wraplength=800, justify="left",
            text="What CLMix answers to out of the box. Send the example from a "
                 "Companion button (Generic OSC module, to CLMix's Listen Port); "
                 "change 5 to your channel, 3 to your aux. Deleted defaults come "
                 "back with Add Default Rules.",
        ).pack(fill="x", pady=(0, 6))

        box, tree = self._tree(frame, (
            ("does", "Does", 230),
            ("send", "Companion sends (example)", 260),
            ("value", "With value", 140),
            ("console", "Console address", 280),
        ))
        box.pack(fill="both", expand=True)

        for rule in default_rules():
            if rule["direction"] != TO_CONSOLE:
                continue
            tree.insert("", "end", values=(
                rule["name"], _example(rule["match"]), _default_value_hint(rule),
                rule["target"],
            ))

        return frame

    def _addresses_tab(self, parent):
        frame = ttk.Frame(parent, padding=8)

        top = ttk.Frame(frame)
        top.pack(fill="x", pady=(0, 6))
        ttk.Label(top, text="Filter").pack(side="left")
        self.filter_var = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.filter_var, width=30)
        entry.pack(side="left", padx=(6, 0))
        self.filter_var.trace_add("write", lambda *a: self._fill_addresses())

        self.new_rule_button = ttk.Button(
            top, text="New Rule With This", state="disabled",
            command=self._new_rule_from_address)
        self.new_rule_button.pack(side="right")

        ttk.Label(
            frame, wraplength=800, justify="left", foreground="#888888",
            text="{n} is the strip number (1-based, as on the surface), {a} an "
                 "aux, {b} an EQ band 1-4. Add /? to any address to ask the "
                 "console for its value. The full list is in "
                 "docs/mixer_protocol/PROTOCOL.md.",
        ).pack(fill="x", pady=(0, 6))

        box, self.address_tree = self._tree(frame, (
            ("address", "Address", 380),
            ("what", "What it is", 200),
            ("values", "Values", 220),
        ))
        box.pack(fill="both", expand=True)

        self.address_tree.bind("<<TreeviewSelect>>", lambda e: self._sync_button())
        self.address_tree.bind("<Double-1>", lambda e: self._new_rule_from_address())

        self._fill_addresses()
        return frame

    def _fill_addresses(self):
        tree = self.address_tree
        tree.delete(*tree.get_children())
        needle = self.filter_var.get().strip().lower()

        for group, rows in CONSOLE_ADDRESSES:
            shown = [row for row in rows
                     if not needle or any(needle in part.lower()
                                          for part in (group, *row))]
            if not shown:
                continue

            parent = tree.insert("", "end", text="", open=True,
                                 values=(group, "", ""), tags=("group",))
            for address, what, values in shown:
                tree.insert(parent, "end", values=(address, what, values))

        tree.tag_configure("group", font=("TkDefaultFont", 9, "bold"))
        self._sync_button()

    def _selected_address(self):
        selection = self.address_tree.selection()
        if not selection or "group" in self.address_tree.item(selection[0], "tags"):
            return None
        return self.address_tree.item(selection[0], "values")

    def _sync_button(self):
        self.new_rule_button.config(
            state="normal" if self._selected_address() else "disabled")

    def _new_rule_from_address(self):
        row = self._selected_address()
        if row is None:
            return

        address, what, values = row
        # Each placeholder becomes a * on the Companion side and {1}, {2}..
        # on the console side, in order.
        parts, match_parts, count = [], [], 0
        for part in address.split("/"):
            if part.startswith("{") and part.endswith("}"):
                count += 1
                parts.append(f"{{{count}}}")
                match_parts.append("*")
            else:
                head, sep, tail = part.partition("_{")
                if sep:
                    # eq_gain_{b}: a placeholder inside one part - left for
                    # the user to fill in, with a band number.
                    parts.append(f"{head}_1")
                    match_parts.append(f"{head}_1")
                else:
                    parts.append(part)
                    match_parts.append(part)

        toggle = values.startswith(("0 off", "0 out"))
        # The same short names the default rules use, so the two read alike.
        match_parts[1] = SHORT_NAMES.get(match_parts[1], match_parts[1].lower())

        rule = default_rule(TO_CONSOLE)
        rule["target"] = "/".join(parts)
        rule["match"] = "/clmix" + "/".join(match_parts).lower() + \
            ("/toggle" if toggle else "")
        rule["name"] = what
        rule["mode"] = MODE_TOGGLE if toggle else MODE_PASS
        self.on_new_rule(rule)
