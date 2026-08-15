import asyncio
import json
import secrets
import threading

import websockets

from services.log_store import log
from services.user_store import ALL_AUX, ALL_SNAPSHOTS

PUSH_INTERVAL_SECONDS = 0.15


class RemoteServer:
    """WebSocket bridge letting phone apps read/control Aux Send Levels.

    Clients must log in with a username/password (checked against
    user_store) before any other action is honored. Each account is
    scoped to one snapshot and one aux bus (or "all" of either) - the
    server rejects actions that fall outside that scope for the mixer's
    currently active snapshot.

    Once logged in, a client picks an aux bus (and optionally a bank to
    narrow the channel list) and from then on receives periodic level/
    mute updates for that selection, and can push level/mute changes
    back - both translated to/from the same OSC commands the desktop
    UI uses via MixerWorker's cache and command_queue.

    A successful username/password login also mints an opaque session
    token, so a phone app that was killed and relaunched can resend just
    that token instead of asking the user to retype their password. The
    token table lives only in memory - it's intentionally wiped on every
    RemoteServer restart (desktop app reconnect/relaunch), at which point
    a resuming client just falls back to a normal password login.
    """

    def __init__(self, get_worker, command_queue, port, user_store,
                 preset_store, get_hidden_auxes=None):
        self.get_worker = get_worker
        self.command_queue = command_queue
        self.port = port
        self.user_store = user_store
        self.preset_store = preset_store
        self.get_hidden_auxes = get_hidden_auxes or (lambda: set())

        self._thread = None
        self._loop = None
        self._stop_event = None
        self._sessions = {}

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
        async with websockets.serve(self._handle_client, "0.0.0.0", self.port):
            log("info", f"Remote server listening on port {self.port}")
            await self._stop_event.wait()

    async def _handle_client(self, websocket):
        log("info", f"Client connected: {websocket.remote_address}")
        state = {
            "aux": None, "bank": None, "user": None, "permission": None, "token": None
        }

        push_task = asyncio.create_task(self._push_loop(websocket, state))

        try:
            async for message in websocket:
                await self._handle_message(websocket, state, message)
        except websockets.ConnectionClosed:
            pass
        finally:
            push_task.cancel()
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
                websocket, {"type": "banks", "banks": list(worker.banks.keys())}
            )

        elif action == "select_aux":
            aux = msg.get("aux")

            if not self._aux_allowed(worker, entry, aux):
                await self._send(
                    websocket, {"type": "error", "message": "Not permitted for this aux"}
                )
                return

            state["aux"] = aux
            self._request_levels_and_pans(worker, state)

        elif action == "select_bank":
            state["bank"] = msg.get("bank")
            self._request_levels_and_pans(worker, state)
            self._request_mutes(worker, state)

        elif action == "set_level":
            if not self._aux_allowed(worker, entry, state.get("aux")):
                await self._send(
                    websocket, {"type": "error", "message": "Not permitted for this aux"}
                )
                return

            self._set_level(state, msg.get("channel"), msg.get("level"))

        elif action == "set_pan":
            if not self._aux_allowed(worker, entry, state.get("aux")):
                await self._send(
                    websocket, {"type": "error", "message": "Not permitted for this aux"}
                )
                return

            self._set_pan(state, msg.get("channel"), msg.get("pan"))

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

        new_token = secrets.token_urlsafe(32)
        self._sessions[new_token] = {"username": username, "entry": entry}

        state["user"] = username
        state["permission"] = entry
        state["token"] = new_token

        log("info", f"User {username!r} logged in "
            f"(snapshot={entry['snapshot']!r}, aux={entry['aux']!r})")

        await self._send(websocket, {
            "type": "login_result",
            "ok": True,
            "snapshot": entry["snapshot"],
            "aux": entry["aux"],
            "presets": entry.get("presets", False),
            "token": new_token,
        })

    async def _handle_token_login(self, websocket, state, token):
        session = self._sessions.get(token)

        if session is None:
            # Most commonly: the desktop app (and with it, RemoteServer's
            # whole in-memory session table) restarted since this token
            # was issued. Not a wrong-password error - the client should
            # fall back to its normal login form, not show one.
            await self._send(
                websocket,
                {"type": "login_result", "ok": False, "message": "Session expired"}
            )
            return

        username = session["username"]
        entry = session["entry"]

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
            "token": token,
        })

    @staticmethod
    def _snapshot_allowed(worker, entry):
        return entry["snapshot"] == ALL_SNAPSHOTS or entry["snapshot"] == worker.snapshot_name

    @classmethod
    def _aux_allowed(cls, worker, entry, aux_index):
        if entry["aux"] == ALL_AUX:
            return True

        if aux_index is None:
            return False

        return cls._aux_name(worker, aux_index) in entry["aux"]

    @staticmethod
    def _aux_name(worker, aux_index):
        key = f"/Aux_Outputs/{aux_index}/Buss_Trim/name"
        return worker.cache[key][0] if key in worker.cache else f"Aux {aux_index}"

    async def _push_loop(self, websocket, state):
        while True:
            await asyncio.sleep(PUSH_INTERVAL_SECONDS)

            worker = self.get_worker()
            aux = state.get("aux")
            entry = state.get("permission")

            if not worker or not worker.is_alive() or aux is None or entry is None:
                continue

            if not self._snapshot_allowed(worker, entry):
                continue

            channels = self._channels_for(worker, state.get("bank"))
            payload = {
                "type": "levels",
                "aux": aux,
                "channels": self._channel_states(worker, channels, aux),
            }

            try:
                await self._send(websocket, payload)
            except websockets.ConnectionClosed:
                return

    def _request_levels_and_pans(self, worker, state):
        aux = state.get("aux")

        if aux is None:
            return

        for channel in self._channels_for(worker, state.get("bank")):
            self.command_queue.put(
                f"/Input_Channels/{channel}/Aux_Send/{aux}/send_level/?"
            )
            self.command_queue.put(
                f"/Input_Channels/{channel}/Aux_Send/{aux}/send_pan/?"
            )

    def _request_mutes(self, worker, state):
        for channel in self._channels_for(worker, state.get("bank")):
            self.command_queue.put(f"/Input_Channels/{channel}/mute/?")

    def _set_level(self, state, channel, level):
        aux = state.get("aux")

        if aux is None or channel is None or level is None:
            return

        db = round(float(level), 2)
        self.command_queue.put(
            f"/Input_Channels/{channel}/Aux_Send/{aux}/send_level {db}"
        )

    def _set_pan(self, state, channel, pan):
        aux = state.get("aux")

        if aux is None or channel is None or pan is None:
            return

        wire_pan = self._ui_pan_to_wire(float(pan))
        self.command_queue.put(
            f"/Input_Channels/{channel}/Aux_Send/{aux}/send_pan {wire_pan}"
        )

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

            auxes.append({"index": i, "name": name})

        return auxes

    @staticmethod
    def _channels_for(worker, bank):
        if bank:
            return worker.banks.get(bank, [])

        channel_count = int(worker.cache["/Console/Input_Channels"][0])
        return list(range(1, channel_count + 1))

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

    @classmethod
    def _channel_states(cls, worker, channels, aux):
        states = []

        for channel in channels:
            name_key = f"/Input_Channels/{channel}/Channel_Input/name"
            name = worker.cache[name_key][0] \
                if name_key in worker.cache else f"Ch {channel}"

            level_key = f"/Input_Channels/{channel}/Aux_Send/{aux}/send_level"
            level = round(worker.cache[level_key][0], 2) \
                if level_key in worker.cache else None

            pan_key = f"/Input_Channels/{channel}/Aux_Send/{aux}/send_pan"
            pan = cls._wire_pan_to_ui(worker.cache[pan_key][0]) \
                if pan_key in worker.cache else None

            mute_key = f"/Input_Channels/{channel}/mute"
            muted = bool(worker.cache[mute_key][0]) \
                if mute_key in worker.cache else False

            states.append({
                "channel": channel,
                "name": name,
                "level": level,
                "pan": pan,
                "muted": muted,
            })

        return states

    @staticmethod
    async def _send(websocket, payload):
        await websocket.send(json.dumps(payload))
