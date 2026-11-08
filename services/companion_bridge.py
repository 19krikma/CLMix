"""Bitfocus Companion bridge: CLMix stands in for the console for Companion.

Companion's DiGiCo module (github.com/bitfocus/companion-module-digico-osc)
talks to a desk as a "DiGiCo Pad", the same iPad dialect CLMix speaks. The
console only answers one external device per entry in its External
Control list, and it is already answering CLMix, so rather than give
Companion a connection of its own this lets Companion ride CLMix's:

    Companion  --OSC-->  CompanionBridge  --command_queue-->  console
               <--------  (translates)    <-- worker -------

In the DiGiCo module, "Mixer IP Address" is this computer, "Transmit Port"
is listen_port here and "Receive Port" is companion_port. The module only
accepts replies from the IP it calls the mixer, which is ours.

Two kinds of traffic go through:

  Pass-through. Whatever Companion sends is forwarded to the console
  unchanged, and whatever the console sends CLMix is copied to Companion,
  bar the meter stream (the module never subscribes to meters, and the
  values would mean nothing to it - the slot numbers are CLMix's). That is
  all the DiGiCo module needs to work as though it were on the desk.

  Translation rules. "When Companion sends X, send the console Y", and
  the other way round, "when the console sends X, send Companion Y". The
  first is for Companion's Generic OSC module, whose buttons send any
  address you like (/clmix/mute/5 -> /Input_Channels/5/mute). The second
  is for Companion's own OSC API, which takes button styles and custom
  variables over OSC (/custom-variable/ch5_mute/value), so console state
  can colour buttons with no DiGiCo module at all. See Rule.

Everything for the console goes through MixerWorker's command_queue,
never a socket of our own, so it is logged, analysed, coalesced and - in
DiGiCo App mode - blocked exactly like any other CLMix command. Nothing
is queued while the worker is not loaded: a press made while the desk is
away would otherwise fire on the next connect, long after anyone wanted
it.
"""

import re
import socket
import threading
import time
from collections import deque

from pythonosc.osc_bundle import OscBundle
from pythonosc.osc_message import OscMessage
from pythonosc.osc_message_builder import OscMessageBuilder

from services.digico_bridge import type_tags
from services.log_store import log

DEFAULT_LISTEN_PORT = 7001
DEFAULT_COMPANION_PORT = 7000

TO_CONSOLE = "to_console"
TO_COMPANION = "to_companion"
DIRECTIONS = (TO_CONSOLE, TO_COMPANION)

# What a rule sends as the value - see Rule.
MODE_PASS = "pass"
MODE_FIXED = "fixed"
MODE_TOGGLE = "toggle"
MODE_SCALE = "scale"
MODE_NONE = "none"
MODE_STEP = "step"
MODES = (MODE_PASS, MODE_FIXED, MODE_TOGGLE, MODE_STEP, MODE_SCALE, MODE_NONE)

ARG_AUTO = "auto"
ARG_TYPES = (ARG_AUTO, "float", "int", "string")

# The console's meter stream, matched on the padded address bytes so the
# ~30 packets a second cost nothing to skip.
METER_VALUES_PREFIX = b"/Meters/values\x00"

RECV_TIMEOUT_SECONDS = 0.2

# How long since Companion was last heard before the status stops calling
# it connected. The DiGiCo module polls /Console/Session/Filename every
# few seconds while it is up, so this is several missed polls.
COMPANION_TIMEOUT_SECONDS = 15.0

# Last value seen per console address, for toggles. Bounded only so a
# runaway stream cannot grow it forever; a real desk has far fewer.
MAX_VALUE_CACHE = 50000

# How long after a step or toggle the console's own reports of that
# address are ignored. While an encoder is turning, the echo of one tick
# lands after the next has been sent; taken as news it would wind the
# value back a tick and the encoder would stall. After this the console
# is believed again, so a change made on the desk is picked up.
OWN_WRITE_HOLD_SECONDS = 0.5

# A toggle whose target's state is not known yet asks the console, and
# flips it when the answer comes in - unless that takes longer than this,
# by when the press is stale and is dropped.
PENDING_TOGGLE_SECONDS = 1.0

# Rows the activity list in Settings can catch up from.
RECENT_RECORDS = 300

_PLACEHOLDER = re.compile(r"\{(\d+|value)\}")


def default_settings():
    return {
        "enabled": False,
        "listen_port": DEFAULT_LISTEN_PORT,
        # Blank: send to whichever address Companion last spoke from.
        # Set: send there, and ignore everything from anywhere else.
        "companion_ip": "",
        "companion_port": DEFAULT_COMPANION_PORT,
        "pass_to_console": True,
        "pass_to_companion": True,
        "rules": [],
    }


def default_rule(direction=TO_CONSOLE):
    return {
        "enabled": True,
        "name": "",
        "direction": direction,
        "match": "",
        "target": "",
        "mode": MODE_PASS,
        "value": "",
        "in_min": "0",
        "in_max": "1",
        "out_min": "0",
        "out_max": "1",
        # MODE_STEP: added to the current value on every message
        # (negative goes down), kept within step_min..step_max where set.
        "step": "1",
        "step_min": "",
        "step_max": "",
        "arg_type": ARG_AUTO,
        # TO_COMPANION only: a port other than companion_port, for
        # Companion's own OSC API (12321 by default) rather than the
        # DiGiCo module.
        "port": "",
        # Whether the original message still goes on through, as
        # pass-through would have sent it. Off means the rule replaces it.
        "keep_original": direction == TO_COMPANION,
        # Which of default_rules() this came from, if any - so "Add
        # Default Rules" can put back only the ones that are missing.
        "default_id": "",
    }


# The strips default_rules() covers: (Companion prefix, console prefix,
# what to call one). Addresses from docs/mixer_protocol/PROTOCOL.md.
DEFAULT_STRIPS = (
    ("/clmix/ch/*", "/Input_Channels/{1}", "Channel"),
    ("/clmix/aux/*", "/Aux_Outputs/{1}", "Aux"),
    ("/clmix/group/*", "/Group_Outputs/{1}", "Group"),
    ("/clmix/matrix/*", "/Matrix_Outputs/{1}", "Matrix"),
    ("/clmix/dca/*", "/Control_Groups/{1}", "DCA"),
)


def default_rules():
    """The rules a fresh install starts with.

    Everything a Companion button does in one press - on, off, toggle, set
    a level - for the switches every strip type has, all under /clmix/ so
    they never collide with the DiGiCo module's own addresses. Stepping
    (rotate left/right) is left to the user: how big a step is, and what
    it should stop at, is a matter of taste. The Rule Book lists them all.
    """
    rules = []

    def add(default_id, name, match, target, mode, value="", arg_type=ARG_AUTO,
            enabled=True, direction=TO_CONSOLE, port=""):
        rule = default_rule(direction)
        rule.update(default_id=default_id, name=name, match=match,
                    target=target, mode=mode, value=value, arg_type=arg_type,
                    enabled=enabled, port=port)
        rules.append(rule)

    def switch(key, label, match, target):
        add(f"{key}.on", f"{label} on", f"{match}/on", target, MODE_FIXED, "1")
        add(f"{key}.off", f"{label} off", f"{match}/off", target, MODE_FIXED, "0")
        add(f"{key}.toggle", f"{label} toggle", f"{match}/toggle", target,
            MODE_TOGGLE)

    for prefix, console, label in DEFAULT_STRIPS:
        key = prefix.split("/")[2]
        switch(f"{key}.mute", f"{label} mute", f"{prefix}/mute", f"{console}/mute")
        switch(f"{key}.solo", f"{label} solo", f"{prefix}/solo", f"{console}/solo")
        add(f"{key}.fader", f"{label} fader (dB)", f"{prefix}/fader",
            f"{console}/fader", MODE_PASS, arg_type="float")

    channel = "/Input_Channels/{1}"
    add("ch.pan", "Channel pan (0-1)", "/clmix/ch/*/pan",
        f"{channel}/Panner/pan", MODE_PASS, arg_type="float")
    switch("ch.phantom", "Channel 48V", "/clmix/ch/*/phantom",
           f"{channel}/Channel_Input/phantom")
    add("ch.phase.toggle", "Channel phase toggle", "/clmix/ch/*/phase/toggle",
        f"{channel}/Channel_Input/phase", MODE_TOGGLE)
    add("ch.gain", "Channel gain (dB)", "/clmix/ch/*/gain",
        f"{channel}/Channel_Input/analog_gain", MODE_PASS, arg_type="float")
    switch("ch.eq", "Channel EQ in", "/clmix/ch/*/eq", f"{channel}/EQ/eq_in")
    switch("ch.comp", "Channel compressor in", "/clmix/ch/*/comp",
           f"{channel}/Dynamics/comp_in")
    switch("ch.gate", "Channel gate in", "/clmix/ch/*/gate",
           f"{channel}/Dynamics/gate_in")

    send = f"{channel}/Aux_Send/{{2}}"
    switch("ch.send", "Channel in aux mix", "/clmix/ch/*/aux/*/on",
           f"{send}/send_on")
    add("ch.send.level", "Channel aux send level (dB)", "/clmix/ch/*/aux/*/level",
        f"{send}/send_level", MODE_PASS, arg_type="float")

    # Only ever seen as the console's own broadcast, never confirmed as a
    # command - see PROTOCOL.md, "Snapshot recall on the wire". Indices
    # are 0-based.
    add("snapshot.recall", "Recall snapshot (0-based, untested)",
        "/clmix/snapshot/*", "/Snapshots/Recall_Snapshot/{1}", MODE_FIXED,
        "0", arg_type="int")

    # Feedback for Companion's own OSC API, off until the custom variables
    # exist in Companion. Kept as examples to switch on.
    add("fb.ch.mute", "Feedback: channel mute → variable", "/Input_Channels/*/mute",
        "/custom-variable/ch{1}_mute/value", MODE_PASS, arg_type="string",
        enabled=False, direction=TO_COMPANION, port="12321")
    add("fb.ch.fader", "Feedback: channel fader → variable",
        "/Input_Channels/*/fader", "/custom-variable/ch{1}_fader/value",
        MODE_PASS, arg_type="string", enabled=False, direction=TO_COMPANION,
        port="12321")

    for rule in rules:
        rule["keep_original"] = rule["direction"] == TO_COMPANION

    return rules


def missing_default_rules(rules):
    """The defaults not in rules already, by default_id - deleted ones
    come back, edited ones are left as they are."""
    present = {rule.get("default_id") for rule in rules}
    return [rule for rule in default_rules() if rule["default_id"] not in present]


def _port(value, fallback):
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError):
        return fallback

    return port if 1 <= port <= 65535 else fallback


def normalize_rule(raw):
    rule = default_rule((raw or {}).get("direction", TO_CONSOLE))
    rule.update({key: value for key, value in (raw or {}).items() if key in rule})

    if rule["direction"] not in DIRECTIONS:
        rule["direction"] = TO_CONSOLE
    if rule["mode"] not in MODES:
        rule["mode"] = MODE_PASS
    if rule["arg_type"] not in ARG_TYPES:
        rule["arg_type"] = ARG_AUTO

    for key in ("name", "match", "target", "value", "in_min", "in_max", "default_id",
                "out_min", "out_max", "step", "step_min", "step_max", "port"):
        rule[key] = str(rule[key] if rule[key] is not None else "").strip()

    rule["enabled"] = bool(rule["enabled"])
    rule["keep_original"] = bool(rule["keep_original"])
    return rule


def normalize_settings(raw):
    """The saved "companion" settings with every key present and sane."""
    settings = default_settings()

    if isinstance(raw, dict):
        settings.update({key: raw[key] for key in settings if key in raw})

    # A fresh install - never saved rules, as opposed to saving none -
    # starts with the defaults.
    if not isinstance(raw, dict) or "rules" not in raw:
        settings["rules"] = default_rules()

    settings["enabled"] = bool(settings["enabled"])
    settings["pass_to_console"] = bool(settings["pass_to_console"])
    settings["pass_to_companion"] = bool(settings["pass_to_companion"])
    settings["listen_port"] = _port(settings["listen_port"], DEFAULT_LISTEN_PORT)
    settings["companion_port"] = _port(
        settings["companion_port"], DEFAULT_COMPANION_PORT
    )
    settings["companion_ip"] = str(settings["companion_ip"] or "").strip()
    settings["rules"] = [
        normalize_rule(rule) for rule in settings["rules"] or []
        if isinstance(rule, dict)
    ]
    return settings


def compile_pattern(pattern):
    """An address pattern as a regex, or None if it is not one.

    "*" stands for one whole path segment and is captured, so its text can
    be put back into the target as {1}, {2}... in order. Anything else
    matches literally:

        /clmix/mute/*        matches /clmix/mute/12, {1} = "12"
        /Input_Channels/*/mute
    """
    if not pattern.startswith("/"):
        return None

    parts = [r"([^/]+)" if segment == "*" else re.escape(segment)
             for segment in pattern.split("/")]
    return re.compile("^" + "/".join(parts) + "$")


def fill_template(template, captures, value):
    def replace(match):
        key = match.group(1)

        if key == "value":
            return "" if value is None else _format_number(value)

        index = int(key) - 1
        return captures[index] if 0 <= index < len(captures) else match.group(0)

    return _PLACEHOLDER.sub(replace, template)


def _format_number(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _number(value):
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)

    try:
        return float(str(value).strip())
    except ValueError:
        return None


def _typed(value, arg_type):
    """(value, OSC type tag) for one outgoing argument."""
    if arg_type == "string":
        return _format_number(value) if isinstance(value, float) else str(value), "s"

    number = _number(value)

    if arg_type == "int":
        return (int(round(number)), "i") if number is not None else (0, "i")

    if arg_type == "float":
        return (number, "f") if number is not None else (0.0, "f")

    # Auto: the console stores nearly everything as a float, so a number
    # goes as one; anything else is a string.
    if number is not None:
        return number, "f"
    return str(value), "s"


class Rule:
    """One translation, compiled from its saved form.

    match is the incoming address (see compile_pattern); target is the
    address to send, with {1}.. for what match's "*"s caught. What goes
    with it depends on mode:

        pass    the incoming arguments unchanged
        fixed   value, with {1}.. and {value} (the incoming first argument)
                filled in; several space-separated values send several
                arguments
        toggle  1 if the target is currently off, otherwise 0 - read from
                the last value the console reported for the target
        step    the target's current value plus step, clamped to
                step_min..step_max - for Companion's rotate left/right,
                which send the same message on every tick. One rule per
                direction, with a negative step for down
        scale   the incoming first argument mapped from in_min..in_max to
                out_min..out_max, clamped (a 0..127 MIDI fader onto 0..1)
        none    no arguments - a trigger, or a query ending in /?

    arg_type forces every argument to one OSC type; auto leaves pass as it
    came and sends numbers as floats.
    """

    def __init__(self, raw):
        self.raw = raw
        self.name = raw["name"] or raw["match"]
        self.direction = raw["direction"]
        self.pattern = compile_pattern(raw["match"])
        self.target = raw["target"]
        self.mode = raw["mode"]
        self.arg_type = raw["arg_type"]
        self.keep_original = raw["keep_original"]
        self.port = _port(raw["port"], None) if raw["port"] else None
        self.scale = tuple(
            _number(raw[key]) for key in ("in_min", "in_max", "out_min", "out_max")
        )
        self.step = _number(raw["step"]) or 0.0
        self.step_min = _number(raw["step_min"]) if raw["step_min"] else None
        self.step_max = _number(raw["step_max"]) if raw["step_max"] else None

    @property
    def usable(self):
        return (self.raw["enabled"] and self.pattern is not None
                and self.target.startswith("/"))

    def match(self, address):
        found = self.pattern.match(address)
        return list(found.groups()) if found else None

    def build(self, captures, args, types, current_value):
        """(address, args, type tags) to send, or None to send nothing."""
        address = fill_template(self.target, captures, args[0] if args else None)
        first = args[0] if args else None

        if self.mode == MODE_NONE:
            return address, [], ""

        if self.mode == MODE_PASS:
            if self.arg_type == ARG_AUTO:
                return address, list(args), types

            out = [_typed(arg, self.arg_type) for arg in args]
            return address, [v for v, _ in out], "".join(t for _, t in out)

        if self.mode == MODE_FIXED:
            text = fill_template(self.raw["value"], captures, first)
            out = [_typed(part, self.arg_type) for part in text.split()]
            return address, [v for v, _ in out], "".join(t for _, t in out)

        if self.mode == MODE_TOGGLE:
            current = _number(current_value) if current_value is not None else None
            new = 0.0 if current is not None and current >= 0.5 else 1.0
            value, tag = _typed(new, self.arg_type)
            return address, [value], tag

        if self.mode == MODE_STEP:
            current = _number(current_value) if current_value is not None else None
            if current is None:
                return None

            new = current + self.step
            if self.step_min is not None:
                new = max(self.step_min, new)
            if self.step_max is not None:
                new = min(self.step_max, new)

            # Rounded so a run of 0.1 steps does not drift to -9.999999.
            value, tag = _typed(round(new, 6), self.arg_type
                                if self.arg_type != ARG_AUTO else "float")
            return address, [value], tag

        if self.mode == MODE_SCALE:
            number = _number(first) if first is not None else None
            in_min, in_max, out_min, out_max = self.scale

            if number is None or None in self.scale or in_max == in_min:
                return None

            fraction = (number - in_min) / (in_max - in_min)
            fraction = min(1.0, max(0.0, fraction))
            value, tag = _typed(out_min + fraction * (out_max - out_min),
                                self.arg_type if self.arg_type != ARG_AUTO else "float")
            return address, [value], tag

        return None


def _messages(data):
    """Every (address, args, type tags) in one datagram, bundles unpacked.

    Unreadable datagrams give nothing - they are counted by the caller.
    """
    try:
        if OscBundle.dgram_is_bundle(data):
            bundle = OscBundle(data)
            found = []
            for index in range(bundle.num_contents):
                found.extend(_messages(bundle.content(index).dgram))
            return found

        message = OscMessage(data)
    except Exception:
        # python-osc fails in several ways on a bad datagram (ParseError,
        # IndexError, struct.error); none of them should end the relay.
        return []

    tags = _type_tag_string(data)
    return [(message.address, list(message.params), tags)]


def _type_tag_string(data):
    tags = type_tags(data)
    return "" if tags == "?" else tags.lstrip(",")


def _build_dgram(address, args, types):
    builder = OscMessageBuilder(address=address)

    for index, arg in enumerate(args):
        if types and index < len(types):
            builder.add_arg(arg, types[index])
        else:
            builder.add_arg(arg)

    return builder.build().dgram


def _describe(address, args):
    def one(arg):
        if isinstance(arg, float):
            return _format_number(arg)
        if isinstance(arg, str):
            return repr(arg)
        return str(arg)

    return " ".join([address, *(one(arg) for arg in args)])


class CompanionBridge:
    """The listening socket, the relay and the rules - see the module doc.

    Runs for as long as the switch in Settings is on, connected to the
    console or not, so Companion sees CLMix come and go the way it would
    see the desk. get_worker() returns the MixerWorker in use, if any.
    """

    def __init__(self, settings, command_queue, get_worker, listen_ip=None):
        self.command_queue = command_queue
        self.get_worker = get_worker
        self.listen_ip = listen_ip or None

        self.listen_port = settings["listen_port"]
        self.configure(settings)

        self._running = False
        self._thread = None
        self._sock = None

        self._lock = threading.Lock()
        self._learned = None
        self._last_heard = None
        self._values = {}
        # address -> monotonic time until which the console's reports of
        # it are ignored - see OWN_WRITE_HOLD_SECONDS.
        self._own_until = {}
        # address -> (rule, deadline) for toggles waiting on the console.
        self._pending_toggles = {}

        self.packets_from_companion = 0
        self.packets_to_companion = 0
        self.dropped_not_connected = 0
        self.translated = 0

        self._recent = deque(maxlen=RECENT_RECORDS)
        self._recent_seq = 0

    # ------------------------------------------------------------ settings

    def configure(self, settings):
        """Take new rules and options without closing the socket.

        The listening port is fixed for the life of the bridge; changing
        it means a new bridge.
        """
        rules = [Rule(raw) for raw in settings["rules"]]

        # Swapped in whole, so the relay thread never sees half of it.
        self._config = (
            settings["companion_ip"] or None,
            settings["companion_port"],
            settings["pass_to_console"],
            settings["pass_to_companion"],
            tuple(r for r in rules if r.usable and r.direction == TO_CONSOLE),
            tuple(r for r in rules if r.usable and r.direction == TO_COMPANION),
        )

    # ----------------------------------------------------------- lifecycle

    def start(self):
        """Bind the listening port. Raises OSError if it is taken."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((self.listen_ip or "", self.listen_port))
        except OSError:
            sock.close()
            raise

        sock.settimeout(RECV_TIMEOUT_SECONDS)
        self._sock = sock
        self._running = True
        self._thread = threading.Thread(
            target=self._relay_from_companion, daemon=True,
            name="companion-bridge"
        )
        self._thread.start()
        log("info", f"Companion bridge listening on "
            f"{self.listen_ip or 'all adapters'}:{self.listen_port}")

    def stop(self):
        if not self._running:
            return

        self._running = False

        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

        if self._sock is not None:
            self._sock.close()
            self._sock = None

        log("info", "Companion bridge stopped")

    @property
    def running(self):
        return self._running

    # -------------------------------------------------------------- status

    def companion_address(self):
        """Where Companion is being sent to: (ip, port), or None."""
        ip, port = self._config[0], self._config[1]
        ip = ip or self._learned
        return (ip, port) if ip else None

    def seconds_since_heard(self):
        heard = self._last_heard
        return None if heard is None else time.monotonic() - heard

    def recent(self, after_seq=0):
        """(seq, (time, direction, text)) newer than after_seq."""
        with self._lock:
            return [item for item in self._recent if item[0] > after_seq]

    def _remember(self, direction, text):
        with self._lock:
            self._recent_seq += 1
            self._recent.append(
                (self._recent_seq, (time.strftime("%H:%M:%S"), direction, text))
            )

    # --------------------------------------------- Companion -> the console

    def _relay_from_companion(self):
        while self._running:
            try:
                data, sender = self._sock.recvfrom(65535)
            except socket.timeout:
                continue
            except ConnectionResetError:
                # Windows: an earlier send to a Companion that has gone
                # away, reported on this read. The socket is still fine.
                continue
            except OSError:
                break

            companion_ip = self._config[0]
            if companion_ip and sender[0] != companion_ip:
                continue

            self._learned = sender[0]
            self._last_heard = time.monotonic()
            self.packets_from_companion += 1

            for address, args, types in _messages(data):
                try:
                    self._from_companion(address, args, types)
                except Exception as ex:
                    # A rule that cannot be applied costs that message,
                    # never the relay.
                    log("warning", f"Companion: could not handle {address}: {ex!r}")

    def _from_companion(self, address, args, types):
        _ip, _port, pass_to_console, _pass_back, rules, _back = self._config
        text = _describe(address, args)

        worker = self.get_worker()
        connected = worker is not None and worker.is_alive() and worker.loaded

        matched = [(rule, captures) for rule in rules
                   for captures in [rule.match(address)] if captures is not None]

        if not connected:
            self.dropped_not_connected += 1
            self._remember("IN", f"{text}   (not connected to the console)")
            return

        if not matched:
            if pass_to_console:
                self._to_console(address, args, types)
                self._remember("IN", text)
            else:
                self._remember("IN", f"{text}   (no rule, not passed on)")
            return

        keep = False

        for rule, captures in matched:
            target = fill_template(rule.target, captures, args[0] if args else None)
            current = self._current_value(target, worker)
            out = rule.build(captures, args, types, current)

            if rule.mode == MODE_TOGGLE and current is None:
                with self._lock:
                    self._pending_toggles[target] = (
                        rule, time.monotonic() + PENDING_TOGGLE_SECONDS)
                self._to_console(target + "/?", [], None)
                self._remember("IN", f"{text}   -> [{rule.name}] state not "
                                     f"known yet, asked the console")
                continue

            if out is None and rule.mode == MODE_STEP and current is None:
                # Nothing to step from yet. Asking now means the next
                # tick has it; this one is lost, which a turning encoder
                # never notices.
                self._to_console(target + "/?", [], None)
                self._remember("IN", f"{text}   -> [{rule.name}] level not "
                                     f"known yet, asked the console")
                continue

            if out is None:
                self._remember("IN", f"{text}   -> [{rule.name}] nothing to send")
                continue

            self._to_console(*out)
            self.translated += 1
            keep = keep or rule.keep_original
            self._remember("IN", f"{text}   -> [{rule.name}] {_describe(out[0], out[1])}")

            # So a second press straight after toggles back, rather than
            # reading the old value before the console's echo is in.
            if out[1]:
                self._note_value(out[0], out[1][0], own=rule.mode in
                                 (MODE_STEP, MODE_TOGGLE))

        if keep and pass_to_console:
            self._to_console(address, args, types)

    def _to_console(self, address, args, types):
        self.command_queue.put((address, list(args), types or None))

    def _current_value(self, address, worker):
        with self._lock:
            if address in self._values:
                return self._values[address]

        cached = worker.cache.get(address) if worker is not None else None
        return cached[0] if cached else None

    def _finish_pending_toggle(self, address, value):
        with self._lock:
            pending = self._pending_toggles.pop(address, None)

        if pending is None:
            return

        rule, deadline = pending
        if time.monotonic() > deadline:
            return

        out = rule.build([], [], "", value)
        out = (address,) + out[1:]
        self._to_console(*out)
        self._note_value(address, out[1][0], own=True)
        self.translated += 1
        self._remember("IN", f"[{rule.name}] {_describe(out[0], out[1])}")

    def _note_value(self, address, value, own=False):
        """Remember a value. own: we just sent it, so hold it against the
        console's late echoes for a moment - see OWN_WRITE_HOLD_SECONDS."""
        now = time.monotonic()

        with self._lock:
            if own:
                self._own_until[address] = now + OWN_WRITE_HOLD_SECONDS
            elif self._own_until.get(address, 0.0) > now:
                return

            if len(self._values) >= MAX_VALUE_CACHE and address not in self._values:
                return
            self._values[address] = value

    # --------------------------------------------- the console -> Companion

    def from_console(self, data):
        """A datagram the console sent CLMix. Runs on the worker thread,
        so it only ever sends - nothing here can block on Companion."""
        if data.startswith(METER_VALUES_PREFIX) or not self._running:
            return

        sock = self._sock
        if sock is None:
            return

        ip, port, _to_console, pass_to_companion, _rules, rules = self._config
        destination_ip = ip or self._learned

        # Read even with no rules this way: toggles going the other way
        # need the console's latest value for their target.
        consumed = False

        for address, args, types in _messages(data):
            if args:
                self._note_value(address, args[0])
                self._finish_pending_toggle(address, args[0])

            for rule in rules:
                captures = rule.match(address)
                if captures is None:
                    continue

                if not rule.keep_original:
                    consumed = True

                out = rule.build(captures, args, types, None)
                if out is None or destination_ip is None:
                    continue

                self._send(sock, _build_dgram(*out),
                           (destination_ip, rule.port or port))
                self.translated += 1
                self._remember("OUT", f"{_describe(address, args)}   -> "
                                      f"[{rule.name}] {_describe(out[0], out[1])}")

        if pass_to_companion and destination_ip and not consumed:
            self._send(sock, data, (destination_ip, port))

    def _send(self, sock, data, destination):
        try:
            sock.sendto(data, destination)
            self.packets_to_companion += 1
        except OSError:
            # Companion closed or unreachable. UDP: nothing to repair,
            # and a log line per console message would bury the log.
            pass
