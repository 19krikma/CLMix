import asyncio
import json
import secrets
import socket
import threading
import time

import ifaddr
import websockets
from zeroconf import ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

from services.chat_log import KIND_SOS, KIND_USER, clean_text
from services.log_store import log
from services.user_store import ALL_AUX, ALL_SNAPSHOTS

PUSH_INTERVAL_SECONDS = 0.15

# Longest channel name a phone may write. The console's own name fields
# are short - the desk shows a handful of characters per strip - and this
# is a guard against a client sending something absurd rather than a
# limit the console itself states.
MAX_CHANNEL_NAME = 32

# Channel_Input/phase is an enum over 0..3, not the 0/1 flag its sample
# values suggested - see docs/mixer_protocol/PROTOCOL.md, "The iPad app's
# parameter dictionary". Four states is what a stereo channel needs (none,
# left, right, both, in some order), and which number means which has
# never been observed: every value ever captured read 0.0.
#
# So nothing here maps a number to a meaning. All this code knows is that
# 0 is normal, anything else is inverted somehow, and the exact value is
# the console's to keep - which is enough to stop CLMix overwriting a
# stereo channel's polarity with 1.0, as it used to.
PHASE_NORMAL = 0
PHASE_INVERTED = 1
PHASE_MAX = 3

# Head-amp dial ranges, per parameter rather than the union of the two
# they used to share (PROTOCOL.md, "Head-amp ranges"). Gain is the range
# the desk stores and the app's dictionary agree on; trim is the range the
# desk stores, which is narrower at the top than the official app's dial.
# Sent to phones so a dial is drawn from what the console actually has
# rather than from a constant compiled into each app.
HEAD_AMP_RANGES = {
    "gain": (-20.0, 60.0),
    "trim": (-40.0, 40.0),
}

# The channel EQ Full Mixer Control's EQ screen edits: four parametric
# bands plus the high- and low-pass filters ahead of them. Ranges are the
# official app's parameter dictionary (docs/mixer_protocol/PROTOCOL.md,
# "The iPad app's parameter dictionary"); writes are clamped to them here
# so a phone can never put a value on the desk its own dial could not.
# Frequencies are Hz on the wire even where the desk's dial reads kHz.
EQ_BANDS = 4
EQ_RANGES = {
    "freq": (20.0, 20000.0),
    "gain": (-18.0, 18.0),
    "q": (0.1, 20.0),
}

# The channel dynamics the Dynamics screen edits: Dynamics 1 - the
# compressor, in each of its three modes - and the gate. Each entry is
# (wire leaf, range); ranges are the official app's dictionary, times in
# seconds (PROTOCOL.md, "Units"). Writes are clamped to them here, as the
# EQ's are.
#
# comp-multiband-desser picks the mode: 0 compressor, 1 multiband, 2
# de-esser (captured on channel 43, 2026-10-24). The compressor is band
# 1's settings; multiband is bands 1-3 (low, mid, high) split at the two
# crossovers, with an "all" threshold and gain over the lot; the de-esser
# runs band 3 with bands 1 and 2 switched out, plus a centre frequency and
# width the desk sends as 0-255 steps of a scale not yet mapped to Hz.
#
# Dynamics 2 - the gate slot - is gate, duck or compressor by
# gate-duck-comp: 0 gate, 1 duck, 2 compressor (captured on channel 22,
# 2026-10-25). Gate and duck both use the gate's thresh, range, attack,
# hold and release. The compressor keeps the gate's thresh, attack and
# release and adds the fourth compressor instance's ratio and gain
# (comp_ratio_4, comp_gain_4): the official app's parameter table files
# those, with comp_knee_4, under the same instance as the gate leaves.
#
# Deliberately absent: the knees and both modes are reported but never
# written - changing the mode is left to the desk.
DYN_PARAMS = {
    "comp_thresh": ("comp_thresh_1", (-60.0, 0.0)),
    "comp_ratio": ("comp_ratio_1", (1.0, 50.0)),
    "comp_attack": ("comp_attack_1", (0.00002, 0.1)),
    "comp_release": ("comp_release_1", (0.001, 10.0)),
    "comp_gain": ("comp_gain_1", (0.0, 40.0)),
    **{
        f"comp_{name}_{band}": (f"comp_{name}_{band}", bounds)
        for band in (2, 3)
        for name, bounds in (
            ("thresh", (-60.0, 0.0)),
            ("ratio", (1.0, 50.0)),
            ("attack", (0.00002, 0.1)),
            ("release", (0.001, 10.0)),
            ("gain", (0.0, 40.0)),
        )
    },
    "comp_all_thresh": ("comp_all_thresh", (-60.0, 0.0)),
    "comp_all_gain": ("comp_all_gain", (0.0, 40.0)),
    "comp_lp_xover": ("comp_LP_crossover", (20.0, 20000.0)),
    "comp_hp_xover": ("comp_HP_crossover", (20.0, 20000.0)),
    "comp_desser_freq": ("desser_centre_freq", (0.0, 255.0)),
    "comp_desser_width": ("desser_freq_width", (0.0, 255.0)),
    "gate_thresh": ("gate_thresh", (-60.0, 0.0)),
    "gate_range": ("gate_range", (0.0, 90.0)),
    "gate_attack": ("gate_attack", (0.00005, 0.1)),
    "gate_hold": ("gate_hold", (0.002, 2.0)),
    "gate_release": ("gate_release", (0.005, 10.0)),
    "gate_ratio": ("comp_ratio_4", (1.0, 50.0)),
    "gate_gain": ("comp_gain_4", (0.0, 40.0)),
}

# Multiband's per-band switches, written as flags like comp_in - see
# _set_dyn. Reported in the comp section as band_in_1..3.
DYN_BAND_FLAGS = ("comp_band_in_1", "comp_band_in_2", "comp_band_in_3")

# The dynamics a strip's COMP and GATE thumbnails draw, in the order
# _dyn_summary sends them: each section in or out, and the settings that
# shape its static curve. Attack, release and hold shape time rather
# than level, so a thumbnail has no use for them. Dynamics 2's mode and
# compressor ratio and gain come last, so an app that reads only the
# first seven still works.
DYN_SUMMARY_LEAVES = (
    "comp_in", "comp_thresh_1", "comp_ratio_1", "comp_gain_1",
    "gate_in", "gate_thresh", "gate_range",
    "gate-duck-comp", "comp_ratio_4", "comp_gain_4",
)

# Meters get their own, faster loop. The console streams them at ~29Hz
# and they are the one thing on the strip that has to look continuous -
# at the 150ms of the levels push a meter reads as a row of steps rather
# than a moving bar. Values are sent compactly (see _meter_states) so the
# extra rate costs a few KB/s, not a multiple of the existing traffic.
METER_PUSH_INTERVAL_SECONDS = 0.05

# How long a session token stays redeemable after it was last used. This
# is a *sliding* window, refreshed on every successful token login, so an
# app in active use through a long show day never expires mid-session -
# only one left unused (a phone sitting in a drawer, or a lost/stolen one)
# does. Tokens are bearer credentials with no password re-check behind
# them, so they shouldn't stay valid indefinitely the way they did before.
SESSION_TTL_SECONDS = 12 * 60 * 60

# What a phone is told when the operator kicks it from the Phone List.
# Phrased as something a person did, not as a fault: the phone shows this
# verbatim, and "connection lost" would send its user chasing the Wi-Fi.
KICK_MESSAGE = "Disconnected by the sound engineer"

# Everything gated by an account's presets permission - see
# RemoteServer._handle_preset_action.
PRESET_ACTIONS = frozenset({
    "list_presets", "list_share_targets", "save_preset", "load_preset",
    "delete_preset", "set_preset_public", "share_preset",
})

# What a phone's SOS button can ask for, by the "reason" it sends, mapped
# to what the sound engineer reads in the desktop's SOS window.
SOS_REASONS = {
    "dead_battery": "DEAD BATTERY",
    "aux_not_working": "AUX NOT WORKING",
    "need_assistance": "NEED ASSISTANCE",
}

# Advertised over mDNS/DNS-SD so phone apps can find this server on the
# local network instead of the user typing in an IP - Android's NsdManager
# and iOS's NWBrowser both browse for this exact service type.
MDNS_SERVICE_TYPE = "_clmix._tcp.local."


class RemoteServer:
    """WebSocket bridge letting phone apps read/control Aux Send Levels.

    Clients must log in with a username/password (checked against
    user_store) before any other action is honored. Each account is
    scoped to one snapshot and one aux bus (or "all" of either) - the
    server rejects actions that fall outside that scope for the mixer's
    currently active snapshot.

    Once logged in, a client picks an aux bus (and optionally a bank to
    narrow the channel list) and from then on receives periodic level/
    pan/mute updates for that selection, and can push level/pan/mute
    changes back - all translated to/from the same OSC commands the
    desktop UI uses via MixerWorker's cache and command_queue.

    "Mute" here is the console's per-send on/off flag
    (/Input_Channels/{n}/Aux_Send/{a}/send_on), never the channel mute
    (/Input_Channels/{n}/mute). Accounts are scoped to a single aux, so a
    phone muting a channel has to affect only that operator's own mix - a
    channel mute would cut the source everywhere at once, FOH and every
    other performer's wedge included. The desktop's own Mute buttons
    wrote the channel mute until 1.7.2 and now write this same flag for
    the aux on screen, so the two agree.

    Writing that flag is itself gated by the account's "mute" permission.
    An account denied it still *receives* each channel's muted state in
    the push loop - it needs to see a channel the operator has pulled
    from its mix - and can still move level and pan; only set_mute is
    refused.

    An account granted "mixer_control" can instead put its socket into
    *mixer mode* (action "select_mixer"), where the same level/pan/mute
    actions ride the console's own channel fader, panner and mute rather
    than one aux's sends - the main mix everyone hears. A socket is in one
    mode or the other, never both: select_aux returns it to aux mode.
    Nothing but this permission gates that mode, so it defaults to off
    (see UserStore).

    An account granted "personalization" can relabel channels for its
    own aux screens (action "set_personal_name"). Nothing about that
    reaches the console: the label is stored against the account in
    UserStore and swapped in by _channel_states on the way out, so only
    this one account ever sees it, and only in aux mode. Mixer mode shows
    the desk's own names untouched - a socket riding the main mix is
    looking at what everyone else is looking at, and "set_name" is there
    to rename the channel for real.

    A successful username/password login also mints an opaque session
    token, so a phone app that was killed and relaunched can resend just
    that token instead of asking the user to retype their password. The
    token table lives only in memory - it's intentionally wiped on every
    RemoteServer restart (desktop app reconnect/relaunch), at which point
    a resuming client just falls back to a normal password login.

    Tokens also age out on their own after SESSION_TTL_SECONDS of disuse
    (see that constant), so one left on a phone that stops being used
    can't be redeemed indefinitely just because the desktop app happens to
    stay up.
    """

    def __init__(self, get_worker, command_queue, port, user_store,
                 preset_store, get_hidden_auxes=None, bind_ip=None,
                 on_sos=None, chat_log=None):
        self.get_worker = get_worker
        self.command_queue = command_queue
        self.port = port
        self.user_store = user_store
        self.preset_store = preset_store
        self.get_hidden_auxes = get_hidden_auxes or (lambda: set())

        # Called on the event loop's thread with one dict per SOS a phone
        # sends (see _handle_sos); the desktop hands it to its own thread.
        self.on_sos = on_sos

        # The shared chat (see services/chat_log.py), or None for a server
        # without one, which then refuses the chat actions.
        self.chat_log = chat_log

        # Which local address to listen on, or None for every adapter.
        # On a machine with two network cards this is what decides which
        # network the phones can reach the server from - and, since the
        # advertisement has to match, what mDNS tells them to connect to.
        self.bind_ip = bind_ip or None

        self._thread = None
        self._loop = None
        self._stop_event = None
        self._sessions = {}
        self._aiozc = None
        self._service_info = None

        # Sockets open right now mapped to their per-connection state, as
        # opposed to _sessions, which is redeemable tokens - a phone that
        # has been put in a pocket still has a session but no connection.
        # Only ever added to and removed from on the event loop's own
        # thread; read from the Tkinter thread by client_count() and
        # client_list(), which is fine for a dict whose entries are only
        # ever swapped whole.
        self._clients = {}

        # Set while CLMix is in DiGiCo App mode, to the reason every phone
        # is given: connected ones are sent it and dropped by their push
        # loop, and logins - including a token resume - are refused with
        # it until this is cleared. Written from the Tkinter thread, read
        # on the event loop's; a plain attribute is enough for a value
        # that is only ever swapped whole.
        self.locked_reason = None

    def client_count(self):
        """How many phones are connected to this server right now."""
        return len(self._clients)

    def local_addresses(self):
        """The local addresses phones are connected in on right now.

        With bind_ip set that can only be bind_ip; on Automatic the server
        listens on every adapter, and this is how the info bar tells which
        of them are actually carrying phones. Called from the Tkinter
        thread, so the dict is copied first, as in client_list().
        """
        addresses = set()

        for websocket in list(self._clients):
            address = getattr(websocket, "local_address", None)

            if address:
                addresses.add(address[0])

        return addresses

    def client_list(self):
        """One row per connected phone, for the Phone List window.

        Deliberately built only from what a connection already had to tell
        us to work at all: the address it dialled in from, the account it
        logged in as, what that account is allowed to touch, and what it
        is mixing right now. Nothing is asked of the phone for this
        window's sake - there is no device name, model or OS here because
        the protocol never collects one.

        Called from the Tkinter thread. The dict is copied first so an
        event-loop connect or disconnect mid-iteration cannot fault it,
        and every value is read out into a plain snapshot rather than
        handing the live state dicts over.
        """
        worker = self.get_worker()
        now = time.monotonic()
        rows = []

        for websocket, state in list(self._clients.items()):
            address = getattr(websocket, "remote_address", None)
            entry = state.get("permission") or {}

            rows.append({
                "id": state.get("client_id"),
                "address": address[0] if address else "unknown",
                "user": state.get("user"),
                "mode": state.get("mode"),
                "aux": state.get("aux"),
                "aux_name": self._aux_name(worker, state["aux"])
                            if worker is not None and state.get("aux") is not None
                            else None,
                "snapshot": entry.get("snapshot"),
                "mixer_control": bool(entry.get("mixer_control", False)),
                "connected_seconds": max(0.0, now - state.get("connected_at", now)),
            })

        # Longest-connected first, so the list does not reshuffle under
        # the operator every time a phone drops and reconnects.
        rows.sort(key=lambda row: row["connected_seconds"], reverse=True)
        return rows

    def kick_client(self, client_id, reason=KICK_MESSAGE):
        """Drops one phone, by the "id" client_list() gave for it.

        Called from the Tkinter thread, so the close itself is handed to
        the event loop rather than attempted here. Returns whether a live
        connection matched - False means it had already gone, which is
        not worth treating as a failure.

        The account's session token is revoked with it. Without that the
        phone's own reconnect would redeem the token within seconds and
        the operator would have achieved nothing; with it, getting back
        in needs the password again.
        """
        if self._loop is None or not self._loop.is_running():
            return False

        for websocket, state in list(self._clients.items()):
            if state.get("client_id") != client_id:
                continue

            token = state.get("token")

            if token is not None:
                # Popped here on the Tkinter thread rather than inside the
                # coroutine: the point is that the token is dead the
                # moment the operator asks, not whenever the loop gets
                # round to the close.
                self._sessions.pop(token, None)

            log("info", f"Kicking {state.get('user') or 'unauthenticated client'} "
                f"at {getattr(websocket, 'remote_address', ('unknown',))[0]}")

            asyncio.run_coroutine_threadsafe(
                self._close_client(websocket, reason), self._loop
            )
            return True

        return False

    async def _close_client(self, websocket, reason):
        """Tells a phone why it is going, then closes the socket.

        _handle_client's own finally block does the cleanup - releasing
        meters and dropping the _clients entry - so there is nothing to
        undo here.
        """
        try:
            await self._send(websocket, {"type": "error", "message": reason})
            await websocket.close()
        except websockets.ConnectionClosed:
            pass

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._stop_event.set)

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._stop_event = asyncio.Event()

        try:
            self._loop.run_until_complete(self._serve())
        except Exception as ex:
            log("error", f"Remote server error: {ex!r}")
        finally:
            self._loop.close()
            log("info", "Remote server stopped")

    async def _serve(self):
        host = self.bind_ip or "0.0.0.0"

        async with websockets.serve(self._handle_client, host, self.port):
            log("info", f"Remote server listening on {host}:{self.port}")
            await self._advertise_mdns()
            try:
                await self._stop_event.wait()
            finally:
                await self._stop_mdns()

    async def _advertise_mdns(self):
        hostname = socket.gethostname()

        # Only the address actually being served. Advertising the others
        # would hand a phone an address nothing is listening on, and the
        # phone clients resolve a service to a single host - so the one
        # they picked could be the wrong one.
        addresses = [self.bind_ip] if self.bind_ip \
            else self._local_ipv4_addresses()

        if not addresses:
            log("warning", "No local IPv4 address found - skipping mDNS advertisement")
            return

        self._service_info = ServiceInfo(
            MDNS_SERVICE_TYPE,
            f"CLMix on {hostname}.{MDNS_SERVICE_TYPE}",
            port=self.port,
            parsed_addresses=addresses,
            server=f"{hostname}.local.",
        )

        self._aiozc = AsyncZeroconf()

        try:
            await self._aiozc.async_register_service(self._service_info)
            log("info", f"Advertising on local network via mDNS as "
                f"{self._service_info.name!r} ({', '.join(addresses)}:{self.port})")
        except Exception as ex:
            # Best-effort - a phone can still connect by typing in the IP
            # manually, so a broken mDNS responder (blocked multicast,
            # port conflict with another local service, ...) shouldn't
            # take down the rest of the server.
            log("warning", f"mDNS advertisement failed: {ex!r}")
            await self._aiozc.async_close()
            self._aiozc = None
            self._service_info = None

    async def _stop_mdns(self):
        if self._aiozc is None:
            return

        try:
            # async_close() unregisters (and, unlike
            # async_unregister_service() on its own, actually awaits the
            # "goodbye" broadcast that tells the network the service is
            # gone) before shutting the engine down.
            await self._aiozc.async_close()
        except Exception as ex:
            log("warning", f"mDNS shutdown failed: {ex!r}")
        finally:
            self._aiozc = None
            self._service_info = None

    # Every adapter's non-loopback, non-link-local IPv4 address - deliberately
    # not restricted to Ethernet (unlike services/network_info.py's
    # get_ethernet_ip) since phones discovering this server are just as
    # likely to be on the same Wi-Fi network as the desktop.
    @staticmethod
    def _local_ipv4_addresses():
        addresses = []

        for adapter in ifaddr.get_adapters():
            for ip in adapter.ips:
                if not ip.is_IPv4:
                    continue

                address = ip.ip

                if address == "127.0.0.1" or address.startswith("169.254."):
                    continue

                addresses.append(address)

        return addresses

    async def _handle_client(self, websocket):
        log("info", f"Client connected: {websocket.remote_address}")
        client_id = f"client:{id(websocket):x}"
        state = {
            # "aux" (this client rides one bus's sends) or "mixer" (it
            # rides the console's own channel faders). Never both - the
            # selection actions swap between them.
            "mode": "aux",
            "aux": None, "bank": None, "user": None, "permission": None,
            "token": None,
            # The channel whose EQ screen this phone has open, or None.
            # Only ever set in mixer mode; while it is, the push loop
            # follows each levels frame with that channel's EQ.
            "eq_channel": None,
            # The same for the Dynamics screen: its channel, or None.
            "dyn_channel": None,
            # Last worker.snapshot_epoch this client has been re-primed
            # for. None until it picks an aux, since there is nothing to
            # prime before that.
            "snapshot_epoch": None,
            # Identifies this connection for as long as it lives: its
            # claim on the console's shared meter slots (see
            # _claim_meters), and the handle the Phone List window kicks
            # by. Two names for one token because the two uses are
            # unrelated - a meter claim is not a person.
            "client_id": client_id,
            "meter_source": client_id,
            # Monotonic, not wall clock: this is only ever read as "how
            # long has this phone been on", which a clock change should
            # not rewrite.
            "connected_at": time.monotonic(),
            # Desktop resync - see _follow_resync. Whether this phone has
            # been told one is running, and the (worker, resync_epoch) it
            # last saw, so one that ran entirely between two push frames
            # is still announced.
            "syncing": False,
            "resync_seen": None,
            # Whether the app understands "sync" - sent at login by
            # phone apps new enough to (see _handle_login). One that does
            # not is only paused through a resync, not sent anywhere.
            "sync_aware": False,
        }

        # Registered once the state exists, so client_list() can never see
        # a socket without one.
        self._clients[websocket] = state

        push_task = asyncio.create_task(self._push_loop(websocket, state))
        meter_task = asyncio.create_task(self._meter_loop(websocket, state))

        try:
            async for message in websocket:
                await self._handle_message(websocket, state, message)
        except websockets.ConnectionClosed:
            pass
        finally:
            push_task.cancel()
            meter_task.cancel()

            # Give back this client's share of the console's meter slots,
            # so a phone that disconnects stops costing everyone else
            # bandwidth for strips nobody is looking at any more.
            worker = self.get_worker()
            if worker is not None:
                worker.release_meters(state["meter_source"])

            self._clients.pop(websocket, None)
            log("info", f"Client disconnected: {websocket.remote_address}")

    async def _handle_message(self, websocket, state, raw):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await self._send(websocket, {"type": "error", "message": "invalid JSON"})
            return

        action = msg.get("action")

        if action == "login":
            await self._handle_login(websocket, state, msg)
            return

        if action == "logout":
            self._sessions.pop(state.get("token"), None)
            state["user"] = None
            state["permission"] = None
            state["token"] = None
            return

        if state["user"] is None:
            await self._send(
                websocket, {"type": "error", "message": "Not authenticated"}
            )
            return

        # Ahead of every check below on purpose: a console that is down,
        # syncing or locked to the DiGiCo App is exactly when someone may
        # need the sound engineer, and the desktop is still there to tell.
        if action == "sos":
            await self._handle_sos(websocket, state, msg)
            return

        # Chat too, for the same reason: talking to the sound engineer
        # matters most when the console is not working.
        if action in ("chat_history", "chat_send"):
            await self._handle_chat(websocket, state, action, msg)
            return

        if self.locked_reason is not None:
            await self._send(
                websocket, {"type": "error", "message": self.locked_reason}
            )
            return

        worker = self.get_worker()

        if not worker or not worker.is_alive() or not worker.loaded:
            await self._send(
                websocket, {"type": "error", "message": "Mixer not connected"}
            )
            return

        if state["syncing"] or worker.resyncing:
            # Nothing is acted on mid-resync: the names and layout it
            # would be acted on are being replaced. A phone that knows is
            # already showing why; this says it again to whichever screen
            # asked. An older one gets it as an error it can show.
            await self._send(
                websocket,
                {"type": "sync", "syncing": True} if state["sync_aware"]
                else {"type": "error", "message": "Server syncing - try again shortly"}
            )
            return

        entry = state["permission"]

        if not self._snapshot_allowed(worker, entry):
            await self._send(
                websocket,
                {"type": "error", "message": "Not permitted for the current snapshot"}
            )
            return

        if action == "list_auxes":
            await self._send(
                websocket, {"type": "auxes", "auxes": self._aux_list(worker, entry)}
            )

        elif action == "list_banks":
            await self._send(
                websocket,
                {"type": "banks", "banks": self._bank_names(worker, state)}
            )

        elif action == "select_aux":
            aux = msg.get("aux")

            if not self._aux_allowed(worker, entry, aux):
                await self._send(
                    websocket, {"type": "error", "message": "Not permitted for this aux"}
                )
                return

            state["mode"] = "aux"
            state["aux"] = aux
            state["eq_channel"] = None
            state["dyn_channel"] = None
            self._request_channel_states(worker, state)
            self._claim_meters(worker, state)

        elif action == "select_mixer":
            if not entry.get("mixer_control", False):
                log("info", f"Denied mixer control for user {state['user']!r} "
                    "(account has no mixer_control permission)")
                await self._send(
                    websocket,
                    {"type": "error", "message": "Not permitted for mixer control"}
                )
                return

            # The aux is cleared rather than remembered: every write from
            # here on goes to the channel itself, and a stale aux left in
            # the state would be the one thing standing between a bug in
            # that dispatch and someone's monitor mix.
            state["mode"] = "mixer"
            state["aux"] = None
            state["eq_channel"] = None
            state["dyn_channel"] = None
            self._request_channel_states(worker, state)
            self._claim_meters(worker, state)

        elif action == "select_bank":
            state["bank"] = msg.get("bank")
            self._request_channel_states(worker, state)
            self._claim_meters(worker, state)

        elif action in ("list_custom_banks", "save_custom_banks",
                        "reset_custom_banks"):
            # Behind the same permission as personal names, and for the
            # same reason: both are this account's own view of a console
            # everyone else is sharing, and neither writes anything back
            # to the desk.
            if not entry.get("personalization", False):
                log("info", f"Denied {action} for user {state['user']!r} "
                    "(account has no personalization permission)")
                await self._send(websocket, {
                    "type": "error",
                    "message": "Not permitted for personalization",
                })
                return

            if action == "save_custom_banks":
                self.user_store.set_custom_banks(state["user"], msg.get("banks"))
            elif action == "reset_custom_banks":
                # Forgotten rather than overwritten, so the next read
                # seeds from whatever the console is reporting now -
                # which may not be what it was reporting when this
                # account was first seeded.
                self.user_store.clear_custom_banks(state["user"])

            banks = self._editable_banks(worker, state)

            await self._send(websocket, {
                "type": "custom_banks",
                "banks": banks if banks is not None else [],
                "channels": self._channel_catalog(worker, state),
            })

            if action == "list_custom_banks":
                return

            # The picker and the strips both follow from the set that
            # just changed. A bank the client was sitting on may have
            # been renamed or deleted out from under it, in which case
            # it falls back to the whole desk rather than to the empty
            # list _channels_for would otherwise hand it.
            names = self._bank_names(worker, state)

            if state.get("bank") not in names:
                state["bank"] = None

            await self._send(websocket, {"type": "banks", "banks": names})
            self._request_channel_states(worker, state)
            self._claim_meters(worker, state)

        elif action == "set_level":
            if await self._reject_write(websocket, worker, state, entry):
                return

            self._set_level(state, msg.get("channel"), msg.get("level"))

        elif action == "set_pan":
            if await self._reject_write(websocket, worker, state, entry):
                return

            self._set_pan(state, msg.get("channel"), msg.get("pan"))

        elif action in ("set_gain", "set_trim", "set_phantom", "set_phase",
                        "set_name", "set_alt_gain", "set_alt_phantom",
                        "set_alt_in"):
            # The head amp and its trim belong to the channel, not to any
            # one mix: turning a preamp down changes what FOH, every
            # monitor and the recording hear at once. So unlike level/pan/
            # mute there is no aux-mode equivalent - these are refused
            # outright unless this socket is in mixer mode with the
            # permission behind it.
            if not self._in_mixer_mode(state):
                await self._send(
                    websocket,
                    {"type": "error", "message": "Not permitted for mixer control"}
                )
                return

            if await self._reject_write(websocket, worker, state, entry):
                return

            if action == "set_gain":
                self._set_gain(state, msg.get("channel"), msg.get("gain"))
            elif action == "set_trim":
                self._set_trim(state, msg.get("channel"), msg.get("trim"))
            elif action == "set_phantom":
                self._set_phantom(state, msg.get("channel"), msg.get("phantom"))
            elif action == "set_phase":
                self._set_phase(state, worker, msg.get("channel"),
                                msg.get("phase"))
            elif action == "set_alt_gain":
                self._set_alt_gain(state, msg.get("channel"), msg.get("gain"))
            elif action == "set_alt_phantom":
                self._set_alt_phantom(state, msg.get("channel"),
                                      msg.get("phantom"))
            elif action == "set_alt_in":
                self._set_alt_in(state, worker, msg.get("channel"),
                                 msg.get("alt_in"))
            else:
                self._set_name(state, msg.get("channel"), msg.get("name"))

        elif action in ("select_dyn", "set_dyn"):
            # The channel's dynamics, behind the same gate as its EQ.
            if not self._in_mixer_mode(state):
                await self._send(
                    websocket,
                    {"type": "error", "message": "Not permitted for mixer control"}
                )
                return

            if action == "select_dyn":
                channel = self._valid_channel(worker, msg.get("channel"))
                state["dyn_channel"] = channel

                if channel is not None:
                    self._request_dyn_state(channel)
                return

            if await self._reject_write(websocket, worker, state, entry):
                return

            self._set_dyn(worker, msg.get("channel"), msg.get("param"),
                          msg.get("value"))

        elif action in ("select_eq", "set_eq"):
            # A channel's EQ belongs to the channel, exactly as its head
            # amp does: it shapes what FOH, every monitor and the
            # recording hear at once. So it lives behind the same gate -
            # mixer mode, with the permission checked on every write.
            if not self._in_mixer_mode(state):
                await self._send(
                    websocket,
                    {"type": "error", "message": "Not permitted for mixer control"}
                )
                return

            if action == "select_eq":
                channel = self._valid_channel(worker, msg.get("channel"))
                state["eq_channel"] = channel

                if channel is not None:
                    self._request_eq_state(channel)
                return

            if await self._reject_write(websocket, worker, state, entry):
                return

            self._set_eq(worker, msg.get("channel"), msg.get("param"),
                         msg.get("band"), msg.get("value"))

        elif action == "set_personal_name":
            # The mirror image of set_name above: that one writes the
            # console's own name field and is refused outside mixer mode,
            # this one touches nothing but this account's own record and
            # is refused *inside* it. Deliberately not behind
            # _reject_write - that guards writes to the desk, and there
            # is no write to the desk here. A phone whose account is
            # locked out of the live snapshot never reaches this at all
            # (see the snapshot check above), which is the same gate the
            # label itself is filed behind.
            if not entry.get("personalization", False):
                log("info", f"Denied set_personal_name for user "
                    f"{state['user']!r} (account has no personalization "
                    "permission)")
                await self._send(websocket, {
                    "type": "error",
                    "message": "Not permitted for personalization",
                })
                return

            if self._in_mixer_mode(state):
                await self._send(websocket, {
                    "type": "error",
                    "message": "Personal names apply to aux mixes only",
                })
                return

            await self._set_personal_name(
                websocket, worker, state, msg.get("channel"), msg.get("name")
            )

        elif action == "set_mute":
            if await self._reject_write(websocket, worker, state, entry):
                return

            # The "mute" permission is about a performer dropping a
            # channel out of their own wedge. Mixer mode's mute is the
            # console's own, and is covered by mixer_control - already
            # checked above - so this narrower flag does not apply there.
            if state.get("mode") != "mixer" and not entry.get("mute", True):
                log("info", f"Denied set_mute for user {state['user']!r} "
                    "(account has no mute permission)")
                await self._send(
                    websocket, {"type": "error", "message": "Not permitted to mute"}
                )
                return

            # A "hard" flag from a build that still offered hard mute is
            # ignored: the feature is gone, and its mute is an ordinary
            # channel mute like any other.
            self._set_mute(state, msg.get("channel"), msg.get("muted"))

        elif action in PRESET_ACTIONS:
            if not entry.get("presets", False):
                await self._send(
                    websocket, {"type": "error", "message": "Not permitted for presets"}
                )
                return

            await self._handle_preset_action(websocket, worker, state, action, msg)

        else:
            await self._send(
                websocket, {"type": "error", "message": f"unknown action {action!r}"}
            )

    async def _handle_login(self, websocket, state, msg):
        token = msg.get("token")

        features = msg.get("features")
        state["sync_aware"] = isinstance(features, list) and "sync" in features

        if self.locked_reason is not None:
            # Refused before the token path too, or a phone that was
            # dropped would resume its session straight away. The token
            # itself is left alone, so it works again once unlocked.
            await self._send(websocket, {
                "type": "login_result", "ok": False,
                "message": self.locked_reason,
            })
            return

        if token is not None:
            await self._handle_token_login(websocket, state, token)
            return

        username = msg.get("username")
        password = msg.get("password")

        entry = self.user_store.authenticate(username, password) \
            if username and password else None

        if entry is None:
            log("info", f"Failed login attempt for user {username!r}")
            await self._send(
                websocket,
                {"type": "login_result", "ok": False, "message": "Invalid username or password"}
            )
            return

        if await self._reject_if_snapshot_denied(websocket, entry):
            log("info", f"Login refused for {username!r}: outside their snapshot")
            return

        new_token = secrets.token_urlsafe(32)
        self._prune_expired_sessions()
        self._sessions[new_token] = {
            "username": username,
            "entry": entry,
            "expires_at": time.monotonic() + SESSION_TTL_SECONDS,
        }

        state["user"] = username
        state["permission"] = entry
        state["token"] = new_token

        log("info", f"User {username!r} logged in "
            f"(snapshot={entry['snapshot']!r}, aux={entry['aux']!r}, "
            f"mute={entry.get('mute', True)}, "
            f"mixer_control={entry.get('mixer_control', False)})")

        await self._send(websocket, {
            "type": "login_result",
            "ok": True,
            "snapshot": entry["snapshot"],
            "aux": entry["aux"],
            "presets": entry.get("presets", False),
            "mute": entry.get("mute", True),
            "mixer_control": entry.get("mixer_control", False),
            "personalization": entry.get("personalization", False),
            "head_amp": HEAD_AMP_RANGES,
            "token": new_token,
        })

    async def _handle_token_login(self, websocket, state, token):
        session = self._sessions.get(token)

        # An expired token is dropped here rather than left to the next
        # prune, so a token that's aged out can never be redeemed even if
        # nothing else triggers a sweep first.
        if session is not None and time.monotonic() >= session["expires_at"]:
            del self._sessions[token]
            session = None

        if session is None:
            # Most commonly: the desktop app (and with it, RemoteServer's
            # whole in-memory session table) restarted since this token
            # was issued, or the token aged past SESSION_TTL_SECONDS. Not
            # a wrong-password error - the client should fall back to its
            # normal login form, not show one.
            await self._send(
                websocket,
                {"type": "login_result", "ok": False, "message": "Session expired"}
            )
            return

        username = session["username"]
        entry = session["entry"]

        if await self._reject_if_snapshot_denied(websocket, entry):
            log("info", f"Token login refused for {username!r}: "
                f"outside their snapshot")
            return

        # Sliding window: using a token renews it, so an app in continuous
        # use never expires out from under the user mid-show. Renewed only
        # after the snapshot check, so a refused attempt cannot keep a
        # session alive indefinitely.
        session["expires_at"] = time.monotonic() + SESSION_TTL_SECONDS

        state["user"] = username
        state["permission"] = entry
        state["token"] = token

        log("info", f"User {username!r} resumed session via token")

        await self._send(websocket, {
            "type": "login_result",
            "ok": True,
            "snapshot": entry["snapshot"],
            "aux": entry["aux"],
            "presets": entry.get("presets", False),
            "mute": entry.get("mute", True),
            "mixer_control": entry.get("mixer_control", False),
            "personalization": entry.get("personalization", False),
            "head_amp": HEAD_AMP_RANGES,
            "token": token,
        })

    def _prune_expired_sessions(self):
        """Drops aged-out tokens from the session table.

        Called when minting a new token rather than on a timer - the table
        only grows at that moment, and this server sees a handful of
        logins a day at most, so there's nothing to gain from a background
        sweep.
        """
        now = time.monotonic()
        expired = [
            token for token, session in self._sessions.items()
            if now >= session["expires_at"]
        ]

        for token in expired:
            del self._sessions[token]

        if expired:
            log("info", f"Pruned {len(expired)} expired session token(s)")

    @staticmethod
    def _snapshot_allowed(worker, entry):
        return entry["snapshot"] == ALL_SNAPSHOTS or entry["snapshot"] == worker.snapshot_name

    async def _reject_if_snapshot_denied(self, websocket, entry):
        """Refuse a login when the desk is on a snapshot this account lacks.

        Without this the kick in _push_loop achieves nothing: the phone
        drops to its login screen, auto-submits the token it still holds,
        and is straight back in on a snapshot it may not touch.
        """
        worker = self.get_worker()

        if not worker or not worker.is_alive():
            # Nothing to check against yet. The push loop re-checks once
            # a console is actually there.
            return False

        if not self._snapshot_denied(worker, entry):
            return False

        await self._send(websocket, {
            "type": "login_result", "ok": False,
            "message": "Not permitted for the current snapshot",
        })
        return True

    @classmethod
    def _snapshot_denied(cls, worker, entry):
        """Whether we KNOW this account may not be on the current snapshot.

        Deliberately weaker than "not allowed". A recall clears
        worker.snapshot_name and only refills it when the console answers
        with the name, so every name-scoped account fails an allowed-check
        in that gap. Denying there would throw every scoped user off on
        every recall - including recalls onto a snapshot they are
        entitled to - so an unknown name is not a denial, just a "not
        yet".
        """
        if worker.snapshot_name is None:
            return False

        return not cls._snapshot_allowed(worker, entry)

    @classmethod
    def _aux_allowed(cls, worker, entry, aux_index):
        if entry["aux"] == ALL_AUX:
            return True

        if aux_index is None:
            return False

        return cls._aux_name(worker, aux_index) in entry["aux"]

    @staticmethod
    def _in_mixer_mode(state):
        return state.get("mode") == "mixer"

    @classmethod
    def _has_selection(cls, state):
        """Whether this client has picked something to ride yet.

        Mixer mode is a selection in itself - the channels are the
        console's own - while aux mode has nothing to report until a bus
        has been chosen.
        """
        return cls._in_mixer_mode(state) or state.get("aux") is not None

    async def _reject_write(self, websocket, worker, state, entry):
        """Refuses a level/pan/mute write the account may not make.

        The two modes are gated by different things: an aux write has to
        land on a bus this account is scoped to, while a mixer write is
        console-wide and answers only to mixer_control. Checked on every
        write rather than trusted from select_mixer, since the permission
        is what stands between a phone and the main mix.
        """
        if self._in_mixer_mode(state):
            if entry.get("mixer_control", False):
                return False

            await self._send(
                websocket,
                {"type": "error", "message": "Not permitted for mixer control"}
            )
            return True

        if self._aux_allowed(worker, entry, state.get("aux")):
            return False

        await self._send(
            websocket, {"type": "error", "message": "Not permitted for this aux"}
        )
        return True

    @staticmethod
    def _aux_name(worker, aux_index):
        key = f"/Aux_Outputs/{aux_index}/Buss_Trim/name"
        return worker.cache[key][0] if key in worker.cache else f"Aux {aux_index}"

    async def _push_loop(self, websocket, state):
        while True:
            await asyncio.sleep(PUSH_INTERVAL_SECONDS)

            if self.locked_reason is not None and state.get("user") is not None:
                # Same way out as a snapshot the account may not touch:
                # say why, then drop the connection. Logging back in is
                # refused with the same reason until the lock lifts.
                log("info", f"User {state.get('user')!r} disconnected: "
                    f"{self.locked_reason}")
                try:
                    await self._send(
                        websocket, {"type": "error", "message": self.locked_reason}
                    )
                    await websocket.close()
                except websockets.ConnectionClosed:
                    pass
                return

            worker = self.get_worker()
            entry = state.get("permission")

            if not worker or not worker.is_alive() or entry is None:
                continue

            try:
                if await self._follow_resync(websocket, state, worker):
                    continue
            except websockets.ConnectionClosed:
                return

            # Read after _follow_resync, which may have just cleared it.
            aux = state.get("aux")

            if not self._has_selection(state):
                continue

            if self._snapshot_denied(worker, entry):
                # The console has moved to a snapshot this account is not
                # scoped to. Going quiet (which is all that used to
                # happen) leaves the operator holding faders that no
                # longer do anything, with nothing on screen saying so -
                # so say it, then drop the connection.
                log("info", f"User {state.get('user')!r} disconnected: "
                    f"snapshot {worker.snapshot_name!r} is outside their access")
                try:
                    await self._send(websocket, {
                        "type": "error",
                        "message": "Not permitted for the current snapshot",
                    })
                    await websocket.close()
                except websockets.ConnectionClosed:
                    pass
                return

            if not self._snapshot_allowed(worker, entry):
                continue

            # A snapshot recall rewrites levels, pans and mutes across
            # the desk without announcing each one, so the cache this
            # push reads from is stale until something asks again. Each
            # client re-primes for its own aux and bank - the desktop's
            # refresh only covers whatever the desktop happens to be
            # showing, which is rarely the same strips.
            if state.get("snapshot_epoch") != worker.snapshot_epoch:
                state["snapshot_epoch"] = worker.snapshot_epoch
                self._request_channel_states(worker, state)

                if state.get("eq_channel") is not None:
                    self._request_eq_state(state["eq_channel"])

                if state.get("dyn_channel") is not None:
                    self._request_dyn_state(state["dyn_channel"])

            channels = self._channels_for(worker, state)

            if self._in_mixer_mode(state):
                payload = {
                    "type": "levels",
                    # No bus is being ridden here, but the field is part
                    # of the shape every existing client parses, so it
                    # carries a value that cannot be mistaken for one.
                    "aux": -1,
                    "mode": "mixer",
                    "channels": self._mixer_channel_states(worker, channels),
                }
            else:
                payload = {
                    "type": "levels",
                    "aux": aux,
                    "mode": "aux",
                    "channels": self._channel_states(
                        worker, channels, aux,
                        self._personal_names(worker, state),
                    ),
                    # The snapshot a personal rename would be filed
                    # against, so the phone can name it rather than
                    # saying "this snapshot". Sent on every frame rather
                    # than once at login because an account scoped to
                    # ALL_SNAPSHOTS stays connected across a recall, and
                    # a label offered under the previous show's name
                    # would be a lie.
                    "snapshot": worker.snapshot_name,
                }

            try:
                await self._send(websocket, payload)

                # The open EQ screen's channel, on the levels cadence.
                # Its own frame rather than a field on every row: only
                # one channel's EQ is ever on screen, and twenty-odd
                # values per strip would be most of each levels frame.
                eq_channel = state.get("eq_channel")

                if self._in_mixer_mode(state) and eq_channel is not None:
                    await self._send(websocket, {
                        "type": "eq",
                        **self._eq_state(worker, eq_channel),
                    })

                dyn_channel = state.get("dyn_channel")

                if self._in_mixer_mode(state) and dyn_channel is not None:
                    await self._send(websocket, {
                        "type": "dynamics",
                        **self._dyn_state(worker, dyn_channel),
                    })
            except websockets.ConnectionClosed:
                return

    # Primes the cache for everything _channel_states reports. The
    # console only broadcasts a parameter when it changes, so without an
    # explicit query first a channel nobody has touched this session has
    # no cached value at all - which is what used to leave every strip
    # reading as unmuted until something happened to move.
    async def _follow_resync(self, websocket, state, worker):
        """Tell this phone about a desktop resync. True while one runs.

        Starting, the phone is sent {"type": "sync", "syncing": true} and
        its selection is dropped, so nothing it has open keeps writing to
        strips whose names and layout are being replaced: it goes back to
        its aux list, or to the AUX Only / Mixer Control choice, and
        shows "Server Syncing" there. Done, it gets "syncing": false and
        asks again for whatever it needs, as after logging in.
        """
        key = (id(worker), worker.resync_epoch)
        seen = state["resync_seen"]

        # A different epoch on the same worker is a resync that began and
        # may have finished between two frames. A different worker is a
        # reconnect, which is not one.
        missed = seen is not None and seen[0] == key[0] and seen != key

        if (worker.resyncing or missed) and not state["syncing"]:
            state["syncing"] = True

            # An app from before this existed would ignore the message and
            # sit on a screen that had stopped updating, with no way to
            # tell. So it keeps its selection, is only paused, and picks
            # up where it was afterwards (the resync bumps snapshot_epoch,
            # which re-reads its strips).
            if state["sync_aware"]:
                state["mode"] = "aux"
                state["aux"] = None
                state["eq_channel"] = None
                state["dyn_channel"] = None
                worker.release_meters(state["meter_source"])
                await self._send(websocket, {"type": "sync", "syncing": True})

        if state["syncing"] and not worker.resyncing:
            state["syncing"] = False

            if state["sync_aware"]:
                await self._send(websocket, {"type": "sync", "syncing": False})

        state["resync_seen"] = key
        return state["syncing"]

    async def _meter_loop(self, websocket, state):
        last = None

        while True:
            await asyncio.sleep(METER_PUSH_INTERVAL_SECONDS)

            worker = self.get_worker()

            if not worker or not worker.is_alive():
                continue

            if not self._has_selection(state) or state.get("permission") is None:
                continue

            channels = self._channels_for(worker, state)
            meters = self._meter_states(worker, channels)

            # Silence is identical frame after frame; sending it 20 times
            # a second to a phone whose mix is idle is pure waste. Any
            # change at all goes out in full.
            if meters == last:
                continue

            last = meters

            try:
                await self._send(websocket, {"type": "meters", "meters": meters})
            except websockets.ConnectionClosed:
                return

    @staticmethod
    def _meter_states(worker, channels):
        """Compact per-channel meter rows: [channel, pL, rL, pR, rR].

        dB below zero as negative numbers, null where the console reports
        its no-signal sentinel. The right pair is null on a mono channel.
        Deliberately positional rather than named: this goes out 20 times
        a second, and the field names would be most of the payload.
        """
        rows = []

        for channel in channels:
            legs = worker.channel_legs(channel)
            values = []

            for leg in ("left", "right"):
                peak, rms = (worker.meter_levels.get((channel, leg), (None, None))
                             if leg in legs else (None, None))
                values += [peak, rms]

            rows.append([channel] + values)

        return rows

    def _claim_meters(self, worker, state):
        """Tell the worker which channels this client needs metered.

        The console keeps one global slot table, so this is a claim on a
        shared resource rather than a private subscription - the worker
        unions every claim and re-binds. Keyed by the client's own
        identity so switching bank replaces that claim instead of adding
        to it.
        """
        if not self._has_selection(state):
            return

        worker.subscribe_meters(
            self._channels_for(worker, state),
            source=state["meter_source"],
        )

    def _request_channel_states(self, worker, state):
        if self._in_mixer_mode(state):
            # Same reason as the aux branch below: the console announces
            # a parameter only when it changes, so a channel nobody has
            # touched this session would arrive with no fader, no pan and
            # no mute at all until someone moved it on the desk.
            for channel in self._channels_for(worker, state):
                self.command_queue.put(f"/Input_Channels/{channel}/fader/?")
                self.command_queue.put(f"/Input_Channels/{channel}/mute/?")
                self.command_queue.put(f"/Input_Channels/{channel}/Panner/pan/?")
                self.command_queue.put(
                    f"/Input_Channels/{channel}/Channel_Input/analog_gain/?"
                )
                self.command_queue.put(f"/Input_Channels/{channel}/Channel_Input/trim/?")
                self.command_queue.put(
                    f"/Input_Channels/{channel}/Channel_Input/phantom/?"
                )
                self.command_queue.put(
                    f"/Input_Channels/{channel}/Channel_Input/phase/?"
                )
                # The alternate input slot and which of the two is live.
                # Whether there is an alt route at all is not asked - the
                # worker tracks it from the desk's input bursts
                # (INPUT_TYPE_PATTERN in ui/main_window.py).
                self.command_queue.put(
                    f"/Input_Channels/{channel}/Channel_Input/main/alt_in/?"
                )
                self.command_queue.put(
                    f"/Input_Channels/{channel}/Channel_Input/alt_analog_gain/?"
                )
                self.command_queue.put(
                    f"/Input_Channels/{channel}/Channel_Input/alt_phantom/?"
                )
                # The whole EQ, which each strip draws in miniature as its
                # EQ button - see _eq_summary.
                self._request_eq_state(channel)
                # What the strip's COMP and GATE thumbnails draw - see
                # _dyn_summary. The rest of the dynamics is asked for only
                # when its screen opens.
                for leaf in DYN_SUMMARY_LEAVES:
                    self.command_queue.put(
                        f"/Input_Channels/{channel}/Dynamics/{leaf}/?"
                    )

            return

        aux = state.get("aux")

        if aux is None:
            return

        # Nothing to prime on a mono bus: send_pan does not apply, so the
        # value stays absent from the cache and _channel_states reports
        # pan as None for the whole bank - which is what a client that
        # honours the aux list's "stereo" flag expects anyway.
        stereo = worker.aux_is_stereo(aux)

        for channel in self._channels_for(worker, state):
            prefix = f"/Input_Channels/{channel}/Aux_Send/{aux}"
            self.command_queue.put(f"{prefix}/send_level/?")
            self.command_queue.put(f"{prefix}/send_on/?")

            if stereo:
                self.command_queue.put(f"{prefix}/send_pan/?")

    def _set_level(self, state, channel, level):
        if channel is None or level is None:
            return

        db = round(float(level), 2)

        # The channel's own fader carries the same dB scale as a send
        # level, so the phone's taper needs no special case here.
        if self._in_mixer_mode(state):
            self.command_queue.put(f"/Input_Channels/{channel}/fader {db}")
            return

        aux = state.get("aux")

        if aux is None:
            return

        self.command_queue.put(
            f"/Input_Channels/{channel}/Aux_Send/{aux}/send_level {db}"
        )

    def _set_pan(self, state, channel, pan):
        if channel is None or pan is None:
            return

        wire_pan = self._ui_pan_to_wire(float(pan))

        # The channel panner uses the same 0.0..1.0 wire range as a send
        # pan, centre at 0.5 - so the conversion above is shared.
        if self._in_mixer_mode(state):
            self.command_queue.put(
                f"/Input_Channels/{channel}/Panner/pan {wire_pan}"
            )
            return

        aux = state.get("aux")

        if aux is None:
            return

        self.command_queue.put(
            f"/Input_Channels/{channel}/Aux_Send/{aux}/send_pan {wire_pan}"
        )

    # Both are plain dB floats on the channel's input stage, written the
    # same way a fader move is - nothing is written to the cache here, the
    # console's echo is what the push loop reports back.
    def _set_gain(self, state, channel, gain):
        if channel is None or gain is None:
            return

        self.command_queue.put(
            f"/Input_Channels/{channel}/Channel_Input/analog_gain "
            f"{round(float(gain), 2)}"
        )

    def _set_trim(self, state, channel, trim):
        if channel is None or trim is None:
            return

        self.command_queue.put(
            f"/Input_Channels/{channel}/Channel_Input/trim {round(float(trim), 2)}"
        )

    def _set_phantom(self, state, channel, phantom):
        if channel is None or phantom is None:
            return

        self.command_queue.put(
            f"/Input_Channels/{channel}/Channel_Input/phantom "
            f"{1.0 if phantom else 0.0}"
        )

    # The alternate input's own head amp. There is no alt trim: the trim
    # sits after the main/alt switch, so set_trim covers both.
    def _set_alt_gain(self, state, channel, gain):
        if channel is None or gain is None:
            return

        self.command_queue.put(
            f"/Input_Channels/{channel}/Channel_Input/alt_analog_gain "
            f"{round(float(gain), 2)}"
        )

    def _set_alt_phantom(self, state, channel, phantom):
        if channel is None or phantom is None:
            return

        self.command_queue.put(
            f"/Input_Channels/{channel}/Channel_Input/alt_phantom "
            f"{1.0 if phantom else 0.0}"
        )

    # Which input feeds the channel. Switching to alt is refused unless
    # the console reports an alt route there (see _alt_available) -
    # flipping a live channel onto an empty socket silences it.
    def _set_alt_in(self, state, worker, channel, alt_in):
        if channel is None or alt_in is None:
            return

        if alt_in and not self._alt_available(worker, channel):
            log("info", f"Refused set_alt_in on channel {channel}: "
                        f"no alt route reported")
            return

        self.command_queue.put(
            f"/Input_Channels/{channel}/Channel_Input/main/alt_in "
            f"{1.0 if alt_in else 0.0}"
        )

    @staticmethod
    def _alt_available(worker, channel):
        """Whether the channel has an alt route patched on the console.

        No address answers this. The worker learns it from the burst the
        desk sends when an alt route is patched or removed (alt_routed,
        see INPUT_TYPE_PATTERN in ui/main_window.py), so a route patched
        before CLMix connected reads as absent until it is patched again.
        Neither the arrival nor the value of alt_analog_gain will do: the
        Q225 answers it 0.0 for a channel with no alt route too.

        A channel already running on alt counts too, since it plainly has
        one.
        """
        if worker is None:
            return False

        if str(channel) in worker.alt_routed:
            return True

        args = worker.cache.get(
            f"/Input_Channels/{channel}/Channel_Input/main/alt_in"
        )
        try:
            return bool(float(args[0])) if args else False
        except (TypeError, ValueError):
            return False

    # phase is an enum over 0..3, not a flag - see PHASE_MAX. Two client
    # shapes are accepted, because the wire has carried both:
    #
    #   a number  the state the phone chose, written as asked. This is
    #             what every current build sends, and it is how a stereo
    #             channel's polarity survives a round trip: the phone hands
    #             back the same value it was given.
    #   a bool    what every build before this change sent, and the reason
    #             it needed fixing. "On" no longer means 1.0 - if the desk
    #             already holds 2 or 3 the channel is inverted, so that
    #             value is written back rather than flattened to 1.0.
    def _set_phase(self, state, worker, channel, phase):
        if channel is None or phase is None:
            return

        if isinstance(phase, bool):
            current = self._cached_phase(worker, channel)
            wanted = (current or PHASE_INVERTED) if phase else PHASE_NORMAL
        else:
            try:
                wanted = round(float(phase))
            except (TypeError, ValueError):
                return

            wanted = min(PHASE_MAX, max(PHASE_NORMAL, wanted))

        self.command_queue.put(
            f"/Input_Channels/{channel}/Channel_Input/phase {float(wanted)}"
        )

    @staticmethod
    def _cached_phase(worker, channel):
        """What the console last reported for this channel's phase, or 0."""
        key = f"/Input_Channels/{channel}/Channel_Input/phase"
        args = worker.cache.get(key) if worker is not None else None

        if not args:
            return PHASE_NORMAL

        try:
            return min(PHASE_MAX, max(PHASE_NORMAL, round(float(args[0]))))
        except (TypeError, ValueError):
            return PHASE_NORMAL

    # The one write in the protocol carrying a string rather than a
    # float, and the one that can contain a space - so it goes to the
    # worker in the (address, args) form, which survives a name like
    # "DI 2" that splitting on whitespace would turn into two arguments.
    def _set_name(self, state, channel, name):
        if channel is None or name is None:
            return

        name = str(name).strip()[:MAX_CHANNEL_NAME]

        if not name:
            return

        self.command_queue.put((
            f"/Input_Channels/{channel}/Channel_Input/name", [name]
        ))

    # Nothing is sent to the console here - this is the whole of the
    # "personalization" feature's write path. The label goes into the
    # account's own record and is swapped in by _channel_states, so the
    # phone sees it on the next push the same way it sees any other
    # change, and no other account sees it at all.
    #
    # Filed against the snapshot live on the desk right now rather than
    # the account's snapshot scope: an account scoped to ALL_SNAPSHOTS
    # has one scope and many shows, and channel 12 is a different
    # instrument in each of them.
    async def _set_personal_name(self, websocket, worker, state, channel, name):
        if channel is None:
            return

        snapshot = worker.snapshot_name

        if not snapshot:
            # The console has not said which snapshot it is on yet, so
            # there is nothing to file the label against. Saying so beats
            # storing it under a guess the user would then find missing.
            await self._send(websocket, {
                "type": "error",
                "message": "Snapshot not known yet - try again shortly",
            })
            return

        # An empty name is how a phone asks for the console's own name
        # back, so unlike _set_name it is not discarded as a no-op.
        name = str(name or "").strip()[:MAX_CHANNEL_NAME]

        self.user_store.set_personal_name(
            state["user"], snapshot, channel, name
        )

        log("info", f"User {state['user']!r} "
            + (f"renamed channel {channel} to {name!r}" if name
               else f"cleared their name for channel {channel}")
            + f" on snapshot {snapshot!r}")

    # send_on is the inverse of mute: 0.0 drops the channel out of this
    # aux mix, 1.0 puts it back. Nothing is written to the cache here -
    # the console echoes the change back like any other parameter move,
    # and the push loop reports it from there, so the phone only ever
    # shows state the console has actually confirmed.
    def _set_mute(self, state, channel, muted):
        if channel is None or muted is None:
            return

        # The console's own channel mute, which cuts the source for FOH
        # and every monitor mix at once - not the per-send flag below.
        # Reads the right way round, too: 1.0 *is* muted here, where
        # send_on means the opposite.
        if self._in_mixer_mode(state):
            self.command_queue.put(
                f"/Input_Channels/{channel}/mute {1.0 if muted else 0.0}"
            )
            return

        aux = state.get("aux")

        if aux is None:
            return

        send_on = 0.0 if muted else 1.0
        self.command_queue.put(
            f"/Input_Channels/{channel}/Aux_Send/{aux}/send_on {send_on}"
        )

    @staticmethod
    def _valid_channel(worker, channel):
        """channel as an int if the console has an input by that number."""
        try:
            channel = int(channel)
        except (TypeError, ValueError):
            return None

        count_args = worker.cache.get("/Console/Input_Channels")

        try:
            count = int(count_args[0]) if count_args else None
        except (TypeError, ValueError):
            count = None

        if channel < 1 or (count is not None and channel > count):
            return None

        return channel

    def _request_eq_state(self, channel):
        """Primes the cache for everything _eq_state reports.

        The same reason as _request_channel_states: the console only
        announces a parameter when it changes, so an EQ nobody has
        touched this session would open with nothing on it. Asked for
        leaf by leaf rather than with a strip dump, which would drag the
        whole channel - dynamics, sends and all - across the link for
        one panel.
        """
        prefix = f"/Input_Channels/{channel}"
        self.command_queue.put(f"{prefix}/EQ/eq_in/?")

        for band in range(1, EQ_BANDS + 1):
            for leaf in ("eq_on", "eq_freq", "eq_gain", "eq_Q", "eq_curve"):
                self.command_queue.put(f"{prefix}/EQ/{leaf}_{band}/?")

        for side in ("lo", "hi"):
            self.command_queue.put(f"{prefix}/Filters/{side}_filter_in/?")
            self.command_queue.put(f"{prefix}/Filters/{side}_filter_freq/?")

    def _request_dyn_state(self, channel):
        """Primes the cache for everything _dyn_state reports.

        gate_hold, gate_range and gate-duck-comp are among the leaves a
        strip dump leaves out (PROTOCOL.md, "Parameters a strip dump
        leaves out"), which is one more reason to ask leaf by leaf.
        """
        prefix = f"/Input_Channels/{channel}/Dynamics"

        for leaf in ("comp_in", "gate_in", "comp-multiband-desser",
                     "gate-duck-comp", "comp_knee_1", "comp_knee_4",
                     *DYN_BAND_FLAGS):
            self.command_queue.put(f"{prefix}/{leaf}/?")

        for leaf, _ in DYN_PARAMS.values():
            self.command_queue.put(f"{prefix}/{leaf}/?")

    def _set_dyn(self, worker, channel, param, value):
        """Writes one dynamics parameter: comp_in, gate_in, a multiband
        band switch (DYN_BAND_FLAGS), or a DYN_PARAMS key."""
        channel = self._valid_channel(worker, channel)

        if channel is None or value is None:
            return

        prefix = f"/Input_Channels/{channel}/Dynamics"

        if param in ("comp_in", "gate_in", *DYN_BAND_FLAGS):
            self.command_queue.put(f"{prefix}/{param} {1.0 if value else 0.0}")
            return

        if param not in DYN_PARAMS:
            return

        leaf, bounds = DYN_PARAMS[param]
        # Times go to the microsecond, so a 20 us attack survives.
        wire = self._clamped(value, bounds, 6)

        if wire is not None:
            self.command_queue.put(f"{prefix}/{leaf} {wire}")

    # Band parameters and the address leaf each one writes. The rest of
    # set_eq's vocabulary (eq_in and the two filters) has no band.
    _EQ_BAND_LEAVES = {
        "band_on": "eq_on",
        "freq": "eq_freq",
        "gain": "eq_gain",
        "q": "eq_Q",
    }

    _EQ_FILTER_LEAVES = {
        "lo_filter_in": "lo_filter_in",
        "lo_filter_freq": "lo_filter_freq",
        "hi_filter_in": "hi_filter_in",
        "hi_filter_freq": "hi_filter_freq",
    }

    def _set_eq(self, worker, channel, param, band, value):
        """Writes one EQ or filter parameter on an input channel.

        param is one of eq_in, band_on, freq, gain, q (those four with
        band 1..EQ_BANDS), lo_filter_in, lo_filter_freq, hi_filter_in,
        hi_filter_freq. Flags go out as 0.0/1.0 and numbers are clamped to
        EQ_RANGES - the console itself clamps nothing it is sent. Nothing
        is written to the cache; the console's echo is what the next push
        reports, as with every other write here.
        """
        channel = self._valid_channel(worker, channel)

        if channel is None or value is None:
            return

        prefix = f"/Input_Channels/{channel}"

        if param == "eq_in":
            self.command_queue.put(f"{prefix}/EQ/eq_in {1.0 if value else 0.0}")
            return

        if param in self._EQ_FILTER_LEAVES:
            leaf = self._EQ_FILTER_LEAVES[param]

            if param.endswith("_in"):
                wire = 1.0 if value else 0.0
            else:
                wire = self._clamped(value, EQ_RANGES["freq"], 1)

            if wire is not None:
                self.command_queue.put(f"{prefix}/Filters/{leaf} {wire}")
            return

        leaf = self._EQ_BAND_LEAVES.get(param)

        try:
            band = int(band)
        except (TypeError, ValueError):
            return

        if leaf is None or not 1 <= band <= EQ_BANDS:
            return

        if param == "band_on":
            wire = 1.0 if value else 0.0
        elif param == "freq":
            wire = self._clamped(value, EQ_RANGES["freq"], 1)
        elif param == "gain":
            wire = self._clamped(value, EQ_RANGES["gain"], 2)
        else:
            wire = self._clamped(value, EQ_RANGES["q"], 3)

        if wire is not None:
            self.command_queue.put(f"{prefix}/EQ/{leaf}_{band} {wire}")

    @staticmethod
    def _clamped(value, bounds, digits):
        if isinstance(value, bool):
            return None

        try:
            number = float(value)
        except (TypeError, ValueError):
            return None

        if number != number:  # NaN
            return None

        low, high = bounds
        return round(min(high, max(low, number)), digits)

    async def _handle_sos(self, websocket, state, msg):
        """A performer asking the sound engineer for help: passed to the
        desktop, which pops up an SOS window saying what is needed and who
        is asking. Any logged-in account may send one - no permission gates
        it. The phone is told it arrived, so it can say so."""
        reason = SOS_REASONS.get(msg.get("reason"))

        if reason is None:
            await self._send(
                websocket, {"type": "error", "message": "Unknown SOS reason"}
            )
            return

        user = state["user"]
        worker = self.get_worker()

        if state.get("mode") == "mixer":
            where = "Mixer control"
        elif state.get("aux") is None:
            where = None
        elif worker is not None:
            where = self._aux_name(worker, state["aux"])
        else:
            where = f"Aux {state['aux']}"

        address = getattr(websocket, "remote_address", None)
        sos = {
            "user": user,
            "name": self.user_store.display_name(user),
            "reason": reason,
            "aux_name": where,
            "address": address[0] if address else None,
            "time": time.time(),
        }

        log("warning", f"SOS from {user!r} ({where or 'no aux'}): {reason}")

        if self.on_sos is not None:
            try:
                self.on_sos(sos)
            except Exception as ex:
                log("error", f"SOS could not be shown: {ex}")
                await self._send(websocket, {
                    "type": "error",
                    "message": "SOS could not be delivered - find the sound engineer",
                })
                return

        await self._send(websocket, {"type": "sos_sent", "reason": msg["reason"]})

        # Into the chat as well, so every phone sees who asked for what.
        if self.chat_log is not None:
            text = f"SOS: {reason}" + (f" ({where})" if where else "")
            await self._broadcast_chat(
                self.chat_log.add(KIND_SOS, text, user=user, name=sos["name"])
            )

    async def _handle_chat(self, websocket, state, action, msg):
        """The shared chat: any logged-in account may read it and post to
        it. Each new message goes to every logged-in phone, the sender's
        included - that echo is how its own phone shows it."""
        if self.chat_log is None:
            await self._send(
                websocket, {"type": "error", "message": "Chat is not available"}
            )
            return

        if action == "chat_history":
            await self._send(websocket, {
                "type": "chat_history",
                "messages": [self._chat_for(m, state["user"])
                             for m in self.chat_log.messages()],
            })
            return

        text = clean_text(msg.get("text"))

        if text is None:
            return

        user = state["user"]
        message = self.chat_log.add(
            KIND_USER, text, user=user, name=self.user_store.display_name(user)
        )
        log("info", f"Chat from {user!r}: {text}")
        await self._broadcast_chat(message)

    @staticmethod
    def _chat_for(message, user):
        """One message as a phone gets it: with "mine" set where that
        phone's account wrote it, so the app need not know its own
        username to put its messages on the right."""
        # The desk's own messages have no user, so are never anyone's.
        return dict(message, mine=message["user"] is not None
                    and message["user"] == user)

    async def _broadcast_chat(self, message):
        for websocket, state in list(self._clients.items()):
            if state.get("user") is None:
                continue
            try:
                await self._send(websocket, {
                    "type": "chat",
                    "message": self._chat_for(message, state["user"]),
                })
            except websockets.ConnectionClosed:
                pass

    def send_tech_chat(self, message):
        """Hands a message the desktop already stored in the chat log to
        every phone. Called from the Tkinter thread; does nothing while
        the server is not running, since no phone could hear it."""
        if self._loop is None or not self._loop.is_running():
            return

        asyncio.run_coroutine_threadsafe(
            self._broadcast_chat(message), self._loop
        )

    async def _handle_preset_action(self, websocket, worker, state, action, msg):
        """Everything behind the presets permission, already checked.

        A preset is private to the account that saved it until its owner
        makes it public or shares it with other accounts (see PresetStore),
        and belongs to the aux it was saved from. Older phones are only
        offered, and may only load, the presets of the aux they are on;
        newer ones send any_aux and are offered every preset they can see,
        warning before loading one onto a different aux.
        Anyone who can see one may load it; only its owner may delete it
        or change who sees it - the desktop can do those for any preset,
        from its own page.
        Every change answers with the caller's fresh list, so a phone
        never has to work out the result for itself.
        """
        user = state["user"]

        aux = state.get("aux")

        if action == "list_presets":
            # Remembered for the lists sent after later changes too.
            if msg.get("any_aux"):
                state["presets_any_aux"] = True
            await self._send_presets(websocket, state)

        elif action == "list_share_targets":
            # For one preset where the phone names it, so the list is
            # only accounts that can use that preset's aux.
            preset = self.preset_store.get(msg.get("id")) if msg.get("id") else None
            users = sorted(
                self._share_targets(worker, user, preset),
                key=lambda u: self.user_store.display_name(u).lower(),
            )
            await self._send(websocket, {
                "type": "share_targets",
                "id": msg.get("id"),
                "users": users,
                # Readable names alongside, so older phones that only know
                # "users" keep working. Username when no name is set.
                "names": {u: self.user_store.display_name(u) for u in users},
            })

        elif action == "save_preset":
            await self._save_preset(websocket, worker, state, msg.get("name"))

        elif action == "load_preset":
            if not self._aux_allowed(worker, state["permission"], state.get("aux")):
                await self._send(
                    websocket, {"type": "error", "message": "Not permitted for this aux"}
                )
                return

            # By id from builds that know about owners; by name from older
            # ones, which only ever saw a list of names.
            if msg.get("id"):
                preset = self.preset_store.get(msg["id"])
                if preset is not None and not self.preset_store.can_see(preset, user):
                    preset = None
            else:
                preset = self.preset_store.find_visible(user, msg.get("name"), aux)

            # A newer phone has already asked the user before loading a
            # preset onto an aux it wasn't saved from.
            if preset is not None and not msg.get("any_aux") \
                    and not self.preset_store.fits_aux(preset, aux):
                await self._send(websocket, {
                    "type": "error",
                    "message": "This preset belongs to "
                    f"{self._aux_name(worker, preset['aux'])}",
                })
                return

            await self._load_preset(websocket, state, preset)

        else:
            # delete_preset / set_preset_public / share_preset: the owner's
            # alone.
            preset = self.preset_store.get(msg.get("id"))

            if preset is None or not self.preset_store.can_see(preset, user):
                await self._send(
                    websocket, {"type": "error", "message": "Preset not found"}
                )
                return

            if preset["owner"] != user:
                await self._send(
                    websocket,
                    {"type": "error", "message": "Only the preset's owner can change it"}
                )
                return

            if action == "delete_preset":
                self.preset_store.delete_preset(preset["id"])
                log("info", f"User {user!r} deleted preset {preset['name']!r}")
            elif action == "set_preset_public":
                public = bool(msg.get("public"))
                self.preset_store.set_public(preset["id"], public)
                log("info", f"User {user!r} made preset {preset['name']!r} "
                    f"{'public' if public else 'private'}")
            else:
                allowed = set(self._share_targets(worker, user, preset))
                users = [u for u in (msg.get("users") or [])
                         if isinstance(u, str) and u in allowed]
                self.preset_store.set_shared_with(preset["id"], users)
                log("info", f"User {user!r} shared preset {preset['name']!r} "
                    f"with {users!r}")

            await self._send_presets(websocket, state)

    async def _send_presets(self, websocket, state):
        user = state["user"]
        presets = self.preset_store.visible_to(
            user, state.get("aux"), any_aux=state.get("presets_any_aux", False)
        )
        items = []

        for preset in presets:
            mine = preset["owner"] == user
            item = {
                "id": preset["id"],
                "name": preset["name"],
                "owner": preset["owner"],
                "owner_name": self.user_store.display_name(preset["owner"])
                if preset["owner"] else None,
                "mine": mine,
                "public": preset["public"],
                "shared_with_me": user in preset["shared_with"],
                # None for a preset from before auxes were recorded,
                # which loads onto any aux.
                "aux": preset["aux"],
                "aux_name": preset["aux_name"],
            }
            # Who else has it is the owner's business only.
            if mine:
                item["shared_with"] = preset["shared_with"]
                item["shared_with_names"] = [
                    self.user_store.display_name(u) for u in preset["shared_with"]
                ]
            items.append(item)

        await self._send(websocket, {
            "type": "presets",
            # Names alone for builds from before owners, which list and
            # load by name - see load_preset.
            "presets": sorted({p["name"] for p in presets}),
            "items": items,
        })

    def _share_targets(self, worker, user, preset=None):
        """Who an account can share a preset with: every other account
        that has preset access and may use the preset's aux, since nobody
        else could load it."""
        aux = preset["aux"] if preset else None
        return [
            name for name, record in self.user_store.list_users()
            if name != user and record.get("presets", False)
            and (aux is None or worker is None
                 or self._aux_allowed(worker, record, aux))
        ]

    async def _save_preset(self, websocket, worker, state, name):
        name = (name or "").strip()

        if not name:
            await self._send(
                websocket, {"type": "error", "message": "Preset name required"}
            )
            return

        aux = state.get("aux")

        if aux is None:
            await self._send(
                websocket, {"type": "error", "message": "No aux selected"}
            )
            return

        channel_count = int(worker.cache["/Console/Input_Channels"][0])
        channels = []

        for channel in range(1, channel_count + 1):
            level_key = f"/Input_Channels/{channel}/Aux_Send/{aux}/send_level"
            level = round(worker.cache[level_key][0], 2) \
                if level_key in worker.cache else None

            pan_key = f"/Input_Channels/{channel}/Aux_Send/{aux}/send_pan"
            pan = self._wire_pan_to_ui(worker.cache[pan_key][0]) \
                if pan_key in worker.cache else None

            channels.append({"channel": channel, "level": level, "pan": pan})

        self.preset_store.save_preset(
            state["user"], name, aux, self._aux_name(worker, aux), channels
        )
        await self._send(websocket, {"type": "preset_saved", "name": name})

    async def _load_preset(self, websocket, state, preset):
        if preset is None:
            await self._send(
                websocket, {"type": "error", "message": "Preset not found"}
            )
            return

        name = preset["name"]

        # Reuses _set_level/_set_pan (the same path a fader/pan drag takes)
        # so a loaded preset is enqueued as ordinary OSC set commands -
        # the console (and every other connected client) sees it exactly
        # like a live mix move, not a special bulk-apply operation.
        for entry in preset.get("channels", []):
            channel = entry.get("channel")

            if channel is None:
                continue

            if entry.get("level") is not None:
                self._set_level(state, channel, entry["level"])

            if entry.get("pan") is not None:
                self._set_pan(state, channel, entry["pan"])

        await self._send(websocket, {"type": "preset_loaded", "name": name})

    def _aux_list(self, worker, entry=None):
        aux_modes = worker.cache.get("/Console/Aux_Outputs/modes", [])
        hidden = self.get_hidden_auxes()
        auxes = []

        for i in range(1, len(aux_modes) + 1):
            name_key = f"/Aux_Outputs/{i}/Buss_Trim/name"
            name = worker.cache[name_key][0] \
                if name_key in worker.cache else f"Aux {i}"

            if name in hidden:
                continue

            if entry and entry["aux"] != ALL_AUX and name not in entry["aux"]:
                continue

            # A mono bus sums its sends to one leg, so send_pan is a
            # no-op on it - the console accepts and echoes the write
            # regardless, so a client has no way to discover this by
            # trying. Reported here so phone clients can drop their pan
            # control the way the desktop strip does.
            auxes.append({
                "index": i,
                "name": name,
                "stereo": worker.aux_is_stereo(i),
            })

        return auxes

    def _channels_for(self, worker, state):
        """Which channels this socket is currently looking at.

        No bank selected means every channel on the console - "All" is
        the absence of a filter rather than a bank of its own.

        A bank name is resolved against this account's own set first,
        where it has one, and only then against the console's. The two
        can carry the same name and mean different things, which is the
        point of the feature: an account's "Drums" is whatever it put in
        there. Mixer Control is deliberately left on the desk's own
        grouping - see _stored_banks.
        """
        bank = state.get("bank")

        if not bank:
            channel_count = int(worker.cache["/Console/Input_Channels"][0])
            return list(range(1, channel_count + 1))

        if not self._in_mixer_mode(state):
            custom = self._stored_banks(worker, state)

            if custom is not None:
                for entry in custom:
                    if entry["name"] == bank:
                        return entry["channels"]

                # A bank this client still thinks it is on, which has
                # since been renamed or deleted from another session.
                # Empty rather than every channel: silently widening a
                # filter to the whole desk is the more surprising of the
                # two, and the next list_banks puts it right.
                return []

        return worker.banks.get(bank, [])

    def _stored_banks(self, worker, state):
        """This account's own banks, or None if it is not using any.

        None covers three cases that all mean "fall back to the
        console's": an account without the personalization permission,
        one whose set has never been seeded because the console has not
        reported its banks yet, and any client in Mixer Control, which
        shows the desk's own truth the same way it shows the desk's own
        channel names.

        The seeding is the interesting part. An account that has never
        customised anything is given the console's banks as its own, on
        first use, so the phone's editor opens on the desk's grouping
        rather than on nothing - which is what makes "remove the two I
        never use" the first thing a user can do, instead of having to
        build their rig back up from an empty list.
        """
        if self._in_mixer_mode(state):
            return None

        return self._editable_banks(worker, state)

    def _editable_banks(self, worker, state):
        """The same set, without the Mixer Control gate - what the
        editor reads and writes. Kept apart from _stored_banks because
        the gate is about which grouping the *strips* are filtered by,
        not about whether the account owns a set at all."""
        entry = state.get("permission") or {}
        user = state.get("user")

        if not user or not entry.get("personalization", False):
            return None

        stored = self.user_store.custom_banks(user)

        if stored is not None:
            return stored

        console = getattr(worker, "banks", None) or {}

        if not console:
            # Nothing to seed from yet. Deliberately not stored as an
            # empty set: that would freeze this account on no banks at
            # all for a console that simply had not answered yet.
            return None

        seeded = [
            {"name": name, "channels": list(channels)}
            for name, channels in console.items()
        ]
        self.user_store.set_custom_banks(user, seeded)

        return seeded

    def _bank_names(self, worker, state):
        """What the phone's bank picker should list."""
        custom = self._stored_banks(worker, state)

        if custom is not None:
            return [entry["name"] for entry in custom]

        return list(worker.banks.keys())

    def _channel_catalog(self, worker, state):
        """Every channel on the desk, numbered and named, for the custom
        bank editor - which has to offer channels this socket is not
        currently being pushed, since the whole point of it is picking
        from the lot."""
        personal_names = self._personal_names(worker, state)
        catalog = []

        channel_count = int(worker.cache["/Console/Input_Channels"][0])

        for channel in range(1, channel_count + 1):
            name_key = f"/Input_Channels/{channel}/Channel_Input/name"
            name = worker.cache[name_key][0] \
                if name_key in worker.cache else f"Ch {channel}"

            catalog.append({
                "channel": channel,
                "name": personal_names.get(channel, name),
            })

        return catalog

    # The mixer's own send_pan values run 0.0 (hard left) to 1.0 (hard
    # right) with 0.5 as center. Phone clients use the more conventional
    # -1.0..1.0 with 0.0 as center, so every value crossing this boundary
    # needs converting.
    @staticmethod
    def _wire_pan_to_ui(value):
        return round((value - 0.5) * 2, 2)

    @staticmethod
    def _ui_pan_to_wire(value):
        return round((value / 2) + 0.5, 2)

    def _personal_names(self, worker, state):
        """This socket's own channel labels, or {} if it has none.

        Read per frame rather than cached on the connection: a rename
        has to show up on the next push, and this is a dict lookup
        against what UserStore already holds in memory.
        """
        entry = state.get("permission") or {}

        if not entry.get("personalization", False):
            return {}

        return self.user_store.personal_names(
            state.get("user"), worker.snapshot_name
        )

    @classmethod
    def _channel_states(cls, worker, channels, aux, personal_names=None):
        personal_names = personal_names or {}
        states = []

        for channel in channels:
            name_key = f"/Input_Channels/{channel}/Channel_Input/name"
            name = worker.cache[name_key][0] \
                if name_key in worker.cache else f"Ch {channel}"

            # The account's own label for this strip, where it has one.
            # Applied last so it wins over the console's name and over
            # the "Ch n" placeholder alike - a performer who named a
            # channel should see that name even on a strip the desk has
            # not reported yet.
            name = personal_names.get(channel, name)

            level_key = f"/Input_Channels/{channel}/Aux_Send/{aux}/send_level"
            level = round(worker.cache[level_key][0], 2) \
                if level_key in worker.cache else None

            pan_key = f"/Input_Channels/{channel}/Aux_Send/{aux}/send_pan"
            pan = cls._wire_pan_to_ui(worker.cache[pan_key][0]) \
                if pan_key in worker.cache else None

            # Per-aux, not the console-wide /Input_Channels/{n}/mute -
            # see the class docstring for why. Absent from the cache
            # (nothing has reported this send yet) reads as on, matching
            # a console's own default.
            send_on_key = f"/Input_Channels/{channel}/Aux_Send/{aux}/send_on"
            muted = not bool(worker.cache[send_on_key][0]) \
                if send_on_key in worker.cache else False

            states.append({
                "channel": channel,
                "name": name,
                "level": level,
                "pan": pan,
                "muted": muted,
                # Says whether this strip's meter has a second leg. Sent
                # here rather than with the meter rows because it changes
                # only with the console's configuration, while those go
                # out 20 times a second.
                "stereo": worker.channel_is_stereo(channel),
            })

        return states

    @classmethod
    def _mixer_channel_states(cls, worker, channels):
        """The same row shape as _channel_states, read off the channel itself.

        Level is the channel fader, pan the channel panner, and muted the
        console's own channel mute - so a phone in mixer mode shows what
        the desk shows, rather than one performer's send.
        """
        states = []

        for channel in channels:
            name_key = f"/Input_Channels/{channel}/Channel_Input/name"
            name = worker.cache[name_key][0] \
                if name_key in worker.cache else f"Ch {channel}"

            level_key = f"/Input_Channels/{channel}/fader"
            level = round(worker.cache[level_key][0], 2) \
                if level_key in worker.cache else None

            # A mono channel has no pan axis on the main mix, exactly as
            # a mono aux has none for its sends - None here drops the
            # control on the phone rather than offering a no-op.
            pan_key = f"/Input_Channels/{channel}/Panner/pan"
            pan = cls._wire_pan_to_ui(worker.cache[pan_key][0]) \
                if pan_key in worker.cache else None

            # 1.0 is muted, the opposite sense to send_on - see _set_mute.
            mute_key = f"/Input_Channels/{channel}/mute"
            muted = bool(worker.cache[mute_key][0]) \
                if mute_key in worker.cache else False

            # The head amp and the digital trim behind it. Both null until
            # the console has answered for this channel, which is what the
            # phone's dials read as "nothing to show yet" rather than 0 dB.
            gain_key = f"/Input_Channels/{channel}/Channel_Input/analog_gain"
            gain = round(worker.cache[gain_key][0], 2) \
                if gain_key in worker.cache else None

            trim_key = f"/Input_Channels/{channel}/Channel_Input/trim"
            trim = round(worker.cache[trim_key][0], 2) \
                if trim_key in worker.cache else None

            # 48V on the channel's main input. The alternate input's own
            # is alt_phantom, below.
            phantom_key = f"/Input_Channels/{channel}/Channel_Input/phantom"
            phantom = bool(worker.cache[phantom_key][0]) \
                if phantom_key in worker.cache else False

            # The alternate input slot: its own head amp and 48V (trim is
            # shared - it sits after the switch), which slot is live, and
            # whether there is an alt route at all to switch to.
            alt_gain_key = f"/Input_Channels/{channel}/Channel_Input/alt_analog_gain"
            alt_gain = round(worker.cache[alt_gain_key][0], 2) \
                if alt_gain_key in worker.cache else None

            alt_phantom_key = f"/Input_Channels/{channel}/Channel_Input/alt_phantom"
            alt_phantom = bool(worker.cache[alt_phantom_key][0]) \
                if alt_phantom_key in worker.cache else False

            alt_in_key = f"/Input_Channels/{channel}/Channel_Input/main/alt_in"
            alt_in = bool(worker.cache[alt_in_key][0]) \
                if alt_in_key in worker.cache else False

            # Both shapes go out: "phase_state" is the console's own value
            # (0..3, see PHASE_MAX) and is what a phone should read and
            # hand back, while "phase" stays a bool so a build from before
            # this change still lights its polarity button. A phone reading
            # only the bool cannot tell 3 from 1, which is precisely why
            # writes no longer trust it - see _set_phase.
            phase_state = cls._cached_phase(worker, channel)

            states.append({
                "channel": channel,
                "name": name,
                "level": level,
                "pan": pan,
                "muted": muted,
                "gain": gain,
                "trim": trim,
                "phantom": phantom,
                "phase": phase_state != PHASE_NORMAL,
                "phase_state": phase_state,
                "alt_gain": alt_gain,
                "alt_phantom": alt_phantom,
                "alt_in": alt_in,
                "alt_available": cls._alt_available(worker, channel),
                "stereo": worker.channel_is_stereo(channel),
                "eq_in": cls._cached_flag(
                    worker, f"/Input_Channels/{channel}/EQ/eq_in"
                ),
                "eq_summary": cls._eq_summary(worker, channel),
                "comp_in": cls._cached_flag(
                    worker, f"/Input_Channels/{channel}/Dynamics/comp_in"
                ),
                "gate_in": cls._cached_flag(
                    worker, f"/Input_Channels/{channel}/Dynamics/gate_in"
                ),
                "dyn_summary": cls._dyn_summary(worker, channel),
            })

        return states

    @staticmethod
    def _cached_number(worker, key, digits):
        args = worker.cache.get(key)

        if not args:
            return None

        try:
            return round(float(args[0]), digits)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _cached_flag(worker, key):
        """A 0/1 parameter as a bool, or None until the console reports it."""
        args = worker.cache.get(key)

        if not args:
            return None

        try:
            return bool(float(args[0]))
        except (TypeError, ValueError):
            return None

    @classmethod
    def _dyn_summary(cls, worker, channel):
        """The strip thumbnails' dynamics as one flat list, positional for
        the same reason as _eq_summary - it rides every strip of every
        levels frame:

            [comp_in, comp_thresh, comp_ratio, comp_gain,
             gate_in, gate_thresh, gate_range,
             gate_mode, gate_ratio, gate_gain]

        gate_mode is gate-duck-comp (0 gate, 1 duck, 2 compressor). Flags
        are 0/1, and anything unreported is null.
        """
        prefix = f"/Input_Channels/{channel}/Dynamics"
        row = []

        for leaf in DYN_SUMMARY_LEAVES:
            if leaf.endswith("_in"):
                value = cls._cached_flag(worker, f"{prefix}/{leaf}")
                row.append(None if value is None else int(value))
            else:
                row.append(cls._cached_number(worker, f"{prefix}/{leaf}", 2))

        return row

    @classmethod
    def _dyn_state(cls, worker, channel):
        """One input channel's compressor and gate, as the Dynamics screen
        shows them. None for anything the console has not reported.

        The two mode enums and the knee are sent as the console's own
        numbers, for the screen to name but never to write - see
        DYN_PARAMS.
        """
        prefix = f"/Input_Channels/{channel}/Dynamics"

        def enum(leaf):
            args = worker.cache.get(f"{prefix}/{leaf}")
            try:
                return round(float(args[0])) if args else None
            except (TypeError, ValueError):
                return None

        def section(name):
            values = {
                key[len(name) + 1:]: cls._cached_number(worker, f"{prefix}/{leaf}", 6)
                for key, (leaf, _) in DYN_PARAMS.items()
                if key.startswith(name + "_")
            }
            values["in"] = cls._cached_flag(worker, f"{prefix}/{name}_in")
            return values

        comp = section("comp")
        for flag in DYN_BAND_FLAGS:
            value = cls._cached_flag(worker, f"{prefix}/{flag}")
            comp[flag[len("comp_"):]] = None if value is None else int(value)
        comp["mode"] = enum("comp-multiband-desser")
        comp["knee"] = enum("comp_knee_1")

        gate = section("gate")
        gate["mode"] = enum("gate-duck-comp")
        gate["knee"] = enum("comp_knee_4")

        return {"channel": channel, "comp": comp, "gate": gate}

    @classmethod
    def _eq_summary(cls, worker, channel):
        """The channel's EQ as one flat list, for the strip's thumbnail.

        Positional rather than _eq_state's named shape because it rides
        every strip of every levels frame, where field names would be
        most of the payload:

            [eq_in, lo_in, lo_freq, hi_in, hi_freq,
             then per band 1..EQ_BAND_COUNT: on, freq, gain, q, curve]

        Flags are 0/1, and anything the console has not reported is null,
        exactly as in _eq_state.
        """
        eq = cls._eq_state(worker, channel)

        def flag(value):
            return None if value is None else int(value)

        row = [
            flag(eq["eq_in"]),
            flag(eq["lo_filter"]["in"]), eq["lo_filter"]["freq"],
            flag(eq["hi_filter"]["in"]), eq["hi_filter"]["freq"],
        ]

        for band in eq["bands"]:
            row += [flag(band["on"]), band["freq"], band["gain"],
                    band["q"], band["curve"]]

        return row

    @classmethod
    def _eq_state(cls, worker, channel):
        """One input channel's EQ and filters, as the EQ screen draws them.

        Every value is None until the console has answered for it, which
        the phone reads as "nothing to show yet" - a band with no
        frequency is left off the graph rather than drawn at some default
        the desk does not hold.

        Bands are numbered as the console numbers them. On every channel
        captured, band 1 sat highest and band 4 lowest, but that is how
        those channels were set rather than a rule, so the phone places
        each band by its frequency and never by its number.

        eq_curve is an enum over 1..4 whose values have never been seen to
        mean anything in particular - every sample ever captured read 1.
        So, as with phase, it is reported but never written (set_eq has no
        "curve"): the wrong number on a live desk would reshape a band
        nobody asked to touch.
        """
        prefix = f"/Input_Channels/{channel}"
        bands = []

        for band in range(1, EQ_BANDS + 1):
            curve_args = worker.cache.get(f"{prefix}/EQ/eq_curve_{band}")

            try:
                curve = round(float(curve_args[0])) if curve_args else None
            except (TypeError, ValueError):
                curve = None

            bands.append({
                "band": band,
                "on": cls._cached_flag(worker, f"{prefix}/EQ/eq_on_{band}"),
                "freq": cls._cached_number(worker, f"{prefix}/EQ/eq_freq_{band}", 1),
                "gain": cls._cached_number(worker, f"{prefix}/EQ/eq_gain_{band}", 2),
                "q": cls._cached_number(worker, f"{prefix}/EQ/eq_Q_{band}", 3),
                "curve": curve,
            })

        def side(name):
            return {
                "in": cls._cached_flag(worker, f"{prefix}/Filters/{name}_filter_in"),
                "freq": cls._cached_number(
                    worker, f"{prefix}/Filters/{name}_filter_freq", 1
                ),
            }

        return {
            "channel": channel,
            "eq_in": cls._cached_flag(worker, f"{prefix}/EQ/eq_in"),
            "bands": bands,
            "lo_filter": side("lo"),
            "hi_filter": side("hi"),
        }

    @staticmethod
    async def _send(websocket, payload):
        await websocket.send(json.dumps(payload))
