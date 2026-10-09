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
                 preset_store, get_hidden_auxes=None, bind_ip=None):
        self.get_worker = get_worker
        self.command_queue = command_queue
        self.port = port
        self.user_store = user_store
        self.preset_store = preset_store
        self.get_hidden_auxes = get_hidden_auxes or (lambda: set())

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

        # Channels currently hard-muted from a phone, and what each aux
        # send was carrying when that happened, so unmuting can put them
        # back rather than simply switching everything on. Server-wide
        # rather than per-client: two phones looking at the same channel
        # have to agree about what a hard mute is hiding.
        self._hard_muted = {}

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

            self._set_mute(
                state, msg.get("channel"), msg.get("muted"),
                hard=bool(msg.get("hard", False)), worker=worker
            )

        elif action == "list_presets":
            if not entry.get("presets", False):
                await self._send(
                    websocket, {"type": "error", "message": "Not permitted for presets"}
                )
                return

            await self._send(websocket, {
                "type": "presets",
                "presets": [name for name, _ in self.preset_store.list_presets()],
            })

        elif action == "save_preset":
            if not entry.get("presets", False):
                await self._send(
                    websocket, {"type": "error", "message": "Not permitted for presets"}
                )
                return

            await self._save_preset(websocket, worker, state, msg.get("name"))

        elif action == "load_preset":
            if not entry.get("presets", False):
                await self._send(
                    websocket, {"type": "error", "message": "Not permitted for presets"}
                )
                return

            if not self._aux_allowed(worker, entry, state.get("aux")):
                await self._send(
                    websocket, {"type": "error", "message": "Not permitted for this aux"}
                )
                return

            await self._load_preset(websocket, state, msg.get("name"))

        else:
            await self._send(
                websocket, {"type": "error", "message": f"unknown action {action!r}"}
            )

    async def _handle_login(self, websocket, state, msg):
        token = msg.get("token")

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
            aux = state.get("aux")
            entry = state.get("permission")

            if not worker or not worker.is_alive() or entry is None:
                continue

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
            except websockets.ConnectionClosed:
                return

    # Primes the cache for everything _channel_states reports. The
    # console only broadcasts a parameter when it changes, so without an
    # explicit query first a channel nobody has touched this session has
    # no cached value at all - which is what used to leave every strip
    # reading as unmuted until something happened to move.
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
                # The alternate input slot, and which of the two is live.
                self.command_queue.put(
                    f"/Input_Channels/{channel}/Channel_Input/alt_analog_gain/?"
                )
                self.command_queue.put(
                    f"/Input_Channels/{channel}/Channel_Input/alt_phantom/?"
                )
                self.command_queue.put(
                    f"/Input_Channels/{channel}/Channel_Input/main/alt_in/?"
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

        No address says so outright - input_type tracks the main slot only
        (PROTOCOL.md, "Input patching"). An empty slot reports 0 dB and 48V
        off, the same as an empty main does, so anything else - or the
        channel already running on alt - is taken as a route being there.
        A patched alt sitting at exactly 0 dB with 48V off reads as absent;
        this wants confirming against a live capture of an alt patch.
        """
        if worker is None:
            return False

        prefix = f"/Input_Channels/{channel}/Channel_Input"

        def cached(suffix):
            args = worker.cache.get(f"{prefix}/{suffix}")
            try:
                return float(args[0]) if args else None
            except (TypeError, ValueError):
                return None

        alt_in = cached("main/alt_in")
        alt_gain = cached("alt_analog_gain")
        alt_phantom = cached("alt_phantom")

        return bool(alt_in) or bool(alt_phantom) or \
            (alt_gain is not None and alt_gain != 0.0)

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
    def _set_mute(self, state, channel, muted, hard=False, worker=None):
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

            # The console has no hard_mute on an input channel - it only
            # carries one on aux/group/matrix *outputs* (see
            # docs/mixer_protocol). So a hard mute here is assembled: the
            # channel mute above, plus every aux send dropped, which is
            # what takes the channel out of the monitors as well as the
            # room.
            if hard and worker is not None:
                self._apply_hard_mute(worker, channel, muted)

            return

        aux = state.get("aux")

        if aux is None:
            return

        send_on = 0.0 if muted else 1.0
        self.command_queue.put(
            f"/Input_Channels/{channel}/Aux_Send/{aux}/send_on {send_on}"
        )

    def _apply_hard_mute(self, worker, channel, muted):
        """Drops (or restores) every aux send for a hard-muted channel.

        What each send was carrying is remembered at mute time and put
        back on unmute, so a channel deliberately left out of one
        performer's wedge does not reappear there when the hard mute
        lifts.

        The values come from the desktop's cache, which holds a send only
        once the console has reported it - and in mixer mode nothing
        primes the sends, since the phone is riding channel faders. An
        unreported send is taken as on, the same reading _channel_states
        gives it, so the failure mode is a send coming back on rather than
        one silently staying off. The queries fired below fix that for the
        next time this channel is hard-muted.
        """
        aux_count = len(worker.cache.get("/Console/Aux_Outputs/modes", []))

        if aux_count == 0:
            return

        if muted:
            previous = {}

            for aux in range(1, aux_count + 1):
                key = f"/Input_Channels/{channel}/Aux_Send/{aux}/send_on"
                previous[aux] = (
                    float(worker.cache[key][0]) if key in worker.cache else 1.0
                )
                self.command_queue.put(
                    f"/Input_Channels/{channel}/Aux_Send/{aux}/send_on 0.0"
                )
                self.command_queue.put(f"{key}/?")

            self._hard_muted[channel] = previous
            log("info", f"Hard mute on channel {channel}: "
                f"{aux_count} aux sends dropped")
            return

        previous = self._hard_muted.pop(channel, None)

        for aux in range(1, aux_count + 1):
            # Nothing remembered (this server did not place the mute, or
            # it restarted since) means every send goes back on, which is
            # the console's own default for a send nobody has touched.
            value = 1.0 if previous is None else previous.get(aux, 1.0)
            self.command_queue.put(
                f"/Input_Channels/{channel}/Aux_Send/{aux}/send_on {value}"
            )

        log("info", f"Hard mute lifted on channel {channel}")

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

        self.preset_store.save_preset(name, channels)
        await self._send(websocket, {"type": "preset_saved", "name": name})

    async def _load_preset(self, websocket, state, name):
        preset = self.preset_store.get(name)

        if preset is None:
            await self._send(
                websocket, {"type": "error", "message": "Preset not found"}
            )
            return

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
            })

        return states

    @staticmethod
    async def _send(websocket, payload):
        await websocket.send(json.dumps(payload))
