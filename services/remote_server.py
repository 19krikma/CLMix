import asyncio
import json
import threading

import websockets

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
    """

    def __init__(self, get_worker, command_queue, port, user_store, message_queue=None):
        self.get_worker = get_worker
        self.command_queue = command_queue
        self.port = port
        self.user_store = user_store
        self.message_queue = message_queue

        self._thread = None
        self._loop = None
        self._stop_event = None

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
            print(f"[remote] Server error: {ex!r}")
        finally:
            self._loop.close()
            self._report_status("Stopped")

    async def _serve(self):
        async with websockets.serve(self._handle_client, "0.0.0.0", self.port):
            print(f"[remote] Listening on port {self.port}")
            self._report_status("Ready")
            await self._stop_event.wait()

        print("[remote] Stopped")

    def _report_status(self, status):
        if self.message_queue:
            self.message_queue.put(("server_status", status))

    async def _handle_client(self, websocket):
        print(f"[remote] Client connected: {websocket.remote_address}")
        state = {"aux": None, "bank": None, "user": None, "permission": None}

        push_task = asyncio.create_task(self._push_loop(websocket, state))

        try:
            async for message in websocket:
                await self._handle_message(websocket, state, message)
        except websockets.ConnectionClosed:
            pass
        finally:
            push_task.cancel()
            print(f"[remote] Client disconnected: {websocket.remote_address}")

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
            self._request_levels(worker, state)

        elif action == "select_bank":
            state["bank"] = msg.get("bank")
            self._request_levels(worker, state)
            self._request_mutes(worker, state)

        elif action == "set_level":
            if not self._aux_allowed(worker, entry, state.get("aux")):
                await self._send(
                    websocket, {"type": "error", "message": "Not permitted for this aux"}
                )
                return

            self._set_level(state, msg.get("channel"), msg.get("level"))

        elif action == "set_mute":
            self._set_mute(msg.get("channel"), msg.get("muted"))

        else:
            await self._send(
                websocket, {"type": "error", "message": f"unknown action {action!r}"}
            )

    async def _handle_login(self, websocket, state, msg):
        username = msg.get("username")
        password = msg.get("password")

        entry = self.user_store.authenticate(username, password) \
            if username and password else None

        if entry is None:
            await self._send(
                websocket,
                {"type": "login_result", "ok": False, "message": "Invalid username or password"}
            )
            return

        state["user"] = username
        state["permission"] = entry

        await self._send(websocket, {
            "type": "login_result",
            "ok": True,
            "snapshot": entry["snapshot"],
            "aux": entry["aux"],
        })

    @staticmethod
    def _snapshot_allowed(worker, entry):
        return entry["snapshot"] == ALL_SNAPSHOTS or entry["snapshot"] == worker.snapshot_name

    @classmethod
    def _aux_allowed(cls, worker, entry, aux_index):
        if entry["aux"] == ALL_AUX:
            return True

        return aux_index is not None and entry["aux"] == cls._aux_name(worker, aux_index)

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

    def _request_levels(self, worker, state):
        aux = state.get("aux")

        if aux is None:
            return

        for channel in self._channels_for(worker, state.get("bank")):
            self.command_queue.put(
                f"/Input_Channels/{channel}/Aux_Send/{aux}/send_level/?"
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

    def _set_mute(self, channel, muted):
        if channel is None or muted is None:
            return

        self.command_queue.put(
            f"/Input_Channels/{channel}/mute {1.0 if muted else 0.0}"
        )

    @staticmethod
    def _aux_list(worker, entry=None):
        aux_modes = worker.cache.get("/Console/Aux_Outputs/modes", [])
        auxes = []

        for i in range(1, len(aux_modes) + 1):
            name_key = f"/Aux_Outputs/{i}/Buss_Trim/name"
            name = worker.cache[name_key][0] \
                if name_key in worker.cache else f"Aux {i}"

            if entry and entry["aux"] != ALL_AUX and entry["aux"] != name:
                continue

            auxes.append({"index": i, "name": name})

        return auxes

    @staticmethod
    def _channels_for(worker, bank):
        if bank:
            return worker.banks.get(bank, [])

        channel_count = int(worker.cache["/Console/Input_Channels"][0])
        return list(range(1, channel_count + 1))

    @staticmethod
    def _channel_states(worker, channels, aux):
        states = []

        for channel in channels:
            name_key = f"/Input_Channels/{channel}/Channel_Input/name"
            name = worker.cache[name_key][0] \
                if name_key in worker.cache else f"Ch {channel}"

            level_key = f"/Input_Channels/{channel}/Aux_Send/{aux}/send_level"
            level = round(worker.cache[level_key][0], 2) \
                if level_key in worker.cache else None

            mute_key = f"/Input_Channels/{channel}/mute"
            muted = bool(worker.cache[mute_key][0]) \
                if mute_key in worker.cache else False

            states.append({
                "channel": channel,
                "name": name,
                "level": level,
                "muted": muted,
            })

        return states

    @staticmethod
    async def _send(websocket, payload):
        await websocket.send(json.dumps(payload))
