package com.clmix

import android.content.Context
import android.os.Handler
import android.os.Looper
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

interface MixerClientListener {
    fun onConnected() {}
    fun onDisconnected() {}

    // The socket itself never opened/dropped (host unreachable, refused,
    // timed out, ...) - distinct from onError, which is a normal protocol
    // reply from a server we *did* reach telling us a specific request
    // was rejected (wrong permission, bad snapshot, ...).
    fun onConnectionFailed(message: String) {}

    fun onError(message: String) {}

    // token is set whenever ok is true (both a fresh username/password
    // login and a resumed one) - see SessionStore for what callers should
    // do with it. Null when ok is false.
    fun onLoginResult(ok: Boolean, message: String?, token: String? = null) {}
    fun onAuxes(auxes: List<AuxBus>) {}

    // Arrives ~20x a second while a mix is moving, carrying one row per
    // visible channel. sequence advances per frame so views can tell a
    // fresh sample from a redraw of the same one - the meter ballistics
    // depend on that distinction (see ChannelMeterView).
    fun onMeters(sequence: Long, meters: Map<Int, MeterLevels>) {}
    fun onBanks(banks: List<String>) {}
    fun onLevels(aux: Int, channels: List<ChannelState>) {}
    fun onPresets(names: List<String>) {}
    fun onPresetSaved(name: String) {}
    fun onPresetLoaded(name: String) {}

    /** This account's own banks, with the console's full channel list
     *  to pick from. Both arrive together because the editor needs both
     *  and neither is worth a round trip of its own. */
    fun onCustomBanks(banks: List<CustomBank>, channels: List<BankChannel>) {}
}

/**
 * Talks to the CLMix desktop app's RemoteServer
 * (services/remote_server.py) over a WebSocket, using the same JSON
 * protocol: login/logout/list_auxes/list_banks/select_aux/select_mixer/
 * select_bank/
 * set_level/set_pan/set_mute/set_personal_name/
 * list_presets/save_preset/load_preset out,
 * login_result/auxes/banks/levels/presets/preset_saved/preset_loaded/error
 * in.
 *
 * The server rejects every action until a successful "login" - callers
 * must send credentials via login() (or a saved token via
 * loginWithToken()) and wait for onLoginResult(true) before calling
 * requestAuxes() or anything else.
 */
object MixerClient {
    private val httpClient = OkHttpClient.Builder()
        .pingInterval(15, TimeUnit.SECONDS)
        .build()

    private val mainHandler = Handler(Looper.getMainLooper())

    private var webSocket: WebSocket? = null
    private var appContext: Context? = null

    // True while the demo console is standing in for a real server. It
    // is checked in exactly three places - connect, disconnect and send
    // - because the demo answers on the wire protocol rather than at
    // this object's API: every reply it makes goes through
    // handleMessage and the ordinary parse, permission and dispatch
    // path, so nothing else in the app has to know it is on.
    //
    // REMOVE WITH DEMO MODE.
    private var demoMode = false

    // Screens claim this on resume and release it on pause. It is a
    // stack rather than a single slot because a configuration change -
    // the drawer's Dark mode switch, or the phone's own switchover at
    // sunset - rebuilds every activity in the task, and the ones sitting
    // in the back stack each run onResume as they relaunch, in an order
    // Android does not guarantee. With one slot, whichever background
    // screen happened to resume last captured the push stream: pushes
    // went to ConnectActivity, which ignores them, and the mixer grid sat
    // empty until the connection was rebuilt. Releasing removes only that
    // claimant, so the foreground screen's claim is still underneath and
    // takes over again the moment the transient one lets go.
    private val listeners = mutableListOf<MixerClientListener>()

    val listener: MixerClientListener?
        get() = listeners.lastOrNull()

    fun claimListener(listener: MixerClientListener) {
        listeners.remove(listener)
        listeners.add(listener)
    }

    fun releaseListener(listener: MixerClientListener) {
        listeners.remove(listener)
    }
    var isConnected: Boolean = false
        private set

    // Set from login_result - gates whether MixerActivity shows the
    // Presets UI at all, mirroring the server's own per-user permission
    // check (which still applies regardless of what the client shows).
    var presetsAllowed: Boolean = false
        private set

    // Set from login_result - gates whether the Mute button is offered,
    // mirroring the server's own per-user check (which still applies
    // regardless of what the client shows). Defaults true: an account
    // with no explicit setting, and an older server that never sends the
    // field, both mean "allowed".
    var muteAllowed: Boolean = true
        private set

    // Set from login_result - whether this account may take the console's
    // own channel faders, mutes and pans (the main mix) rather than one
    // performer's sends. Gates the choice offered right after login; the
    // server checks it again on every write regardless. Defaults false,
    // which is also what an older server that never sends the field
    // means.
    var mixerControlAllowed: Boolean = false
        private set

    // Set from login_result - whether this account may give channels its
    // own labels on the aux screens. Purely a display thing: the server
    // files the label against the account and swaps it into this
    // connection's pushes, and nothing about it reaches the console or
    // any other account. Defaults false, which is also what an older
    // server that never sends the field means.
    var personalizationAllowed: Boolean = false
        private set

    // The snapshot live on the console right now, from the levels frames
    // - not the account's snapshot *scope*, which may be "All Snapshots".
    // A personal rename is filed against this, so the rename dialog can
    // name the show the label will belong to. Null until the first frame,
    // or against a server too old to send it.
    var liveSnapshot: String? = null
        private set

    // Set from login_result - the range each head-amp dial sweeps, in dB,
    // as the desktop reads it off the console's own parameter table. Held
    // here rather than compiled into the sheet so a corrected range ships
    // with the desktop instead of waiting on a store release; the defaults
    // are the same values and are what an older server that never sends
    // the field leaves in place.
    var gainRange: ClosedFloatingPointRange<Double> = HEAD_AMP_GAIN_RANGE
        private set

    var trimRange: ClosedFloatingPointRange<Double> = HEAD_AMP_TRIM_RANGE
        private set

    // Advances once per received meter frame. The server only sends a
    // frame when something actually changed, so a bar that stops being
    // fed stops being pushed back up and releases away, exactly as on
    // the desk.
    private var meterSequence: Long = 0

    // The one protocol error callers treat specially rather than just
    // displaying: the account is scoped to a different snapshot than the
    // one currently live on the console, which is a standing permissions
    // problem rather than anything retrying will fix. Exposed as a
    // constant so ConnectActivity can recognize it without re-hardcoding
    // the wording that friendlyServerMessage below produces.
    const val SNAPSHOT_DENIED =
        "Access denied: not permitted for the current snapshot"

    // What the "aux" field of a levels frame carries in mixer mode: no
    // bus is being ridden, and the server sends a value no real aux index
    // could collide with rather than dropping the field the aux screens
    // already parse.
    const val MIXER_AUX = -1

    // Called once from CLMixApplication.onCreate() - gives this singleton
    // an application Context (never an Activity one, to avoid leaking it)
    // so it can start/stop MixerConnectionService itself.
    fun init(context: Context) {
        appContext = context.applicationContext
    }

    fun connect(host: String, port: Int) {
        disconnect()

        // REMOVE WITH DEMO MODE. No socket, and deliberately no
        // foreground service either: that exists to keep a *live* socket
        // alive through a screen lock, and there is nothing here for it
        // to protect.
        if (host == DemoMixer.HOST) {
            demoMode = true
            isConnected = true
            DemoMixer.start()
            onMain { listener?.onConnected() }
            return
        }

        val request = Request.Builder()
            .url("ws://$host:$port")
            .build()

        webSocket = httpClient.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                isConnected = true

                // Started here rather than in connect(): the service
                // exists to keep a *live* socket alive, so starting it
                // for an attempt that may never connect only creates a
                // notification to immediately withdraw - and a start/stop
                // race with it. An unreachable server now never starts
                // one at all.
                appContext?.let(MixerConnectionService::start)

                onMain { listener?.onConnected() }
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                handleMessage(text)
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                if (!releaseIfCurrent(webSocket)) return
                onMain { listener?.onConnectionFailed("Server unreachable") }
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                if (!releaseIfCurrent(webSocket)) return
                onMain { listener?.onDisconnected() }
            }
        })
    }

    fun disconnect() {
        // REMOVE WITH DEMO MODE. Leaving the demo is the same act as
        // closing a socket, so it runs the same teardown and reports
        // itself the same way - the real path's onDisconnected arrives
        // from the socket's own close callback, which there is none of
        // here.
        if (demoMode) {
            demoMode = false
            DemoMixer.stop()
            releaseConnection()
            onMain { listener?.onDisconnected() }
            return
        }

        webSocket?.close(1000, "bye")
        releaseConnection()
    }

    /**
     * Tears down after a socket ends on its own - a server that stopped,
     * or a phone that walked out of range of it.
     *
     * Returns false, having done nothing, when the callback belongs to a
     * socket that is no longer the current one: connect() closes the
     * previous socket before opening a new one, and the old socket's
     * callbacks can land afterwards. Without this check that late
     * callback would tear down the connection that just replaced it.
     */
    @Synchronized
    private fun releaseIfCurrent(socket: WebSocket): Boolean {
        if (socket !== webSocket) return false

        releaseConnection()
        return true
    }

    /**
     * Drops every trace of a live connection, foreground service included.
     *
     * Stopping the service is the point: it is what keeps the process
     * alive and exempt from background throttling while a socket is up,
     * so leaving it running after the socket is gone means the app sits
     * in the notification shade burning battery for a server that is no
     * longer there - which is exactly what happened when someone left
     * the building with the app still open.
     */
    private fun releaseConnection() {
        webSocket = null
        isConnected = false
        presetsAllowed = false
        muteAllowed = true
        mixerControlAllowed = false
        personalizationAllowed = false
        liveSnapshot = null
        gainRange = HEAD_AMP_GAIN_RANGE
        trimRange = HEAD_AMP_TRIM_RANGE
        appContext?.let(MixerConnectionService::stop)
    }

    /**
     * Folds in the head-amp ranges from login_result, if the server sent
     * any. A malformed or inverted pair is ignored rather than applied -
     * a dial whose min exceeds its max cannot be turned at all, and the
     * built-in defaults are correct for every console seen so far.
     */
    private fun readHeadAmpRanges(headAmp: JSONObject?) {
        gainRange = readRange(headAmp, "gain") ?: HEAD_AMP_GAIN_RANGE
        trimRange = readRange(headAmp, "trim") ?: HEAD_AMP_TRIM_RANGE
    }

    private fun readRange(
        headAmp: JSONObject?,
        key: String
    ): ClosedFloatingPointRange<Double>? {
        val pair = headAmp?.optJSONArray(key) ?: return null

        if (pair.length() < 2) return null

        val low = pair.optDoubleOrNull(0) ?: return null
        val high = pair.optDoubleOrNull(1) ?: return null

        return if (low < high) low..high else null
    }

    fun login(username: String, password: String) = send(
        JSONObject()
            .put("action", "login")
            .put("username", username)
            .put("password", password)
    )

    // Resumes a previously-issued session (see SessionStore) instead of
    // sending the password again - the server replies with the same
    // login_result shape either way (RemoteServer._handle_token_login).
    fun loginWithToken(token: String) = send(
        JSONObject()
            .put("action", "login")
            .put("token", token)
    )

    // Explicit logout: revokes the token server-side (RemoteServer pops it
    // from _sessions) before the socket closes, so a copy of the token
    // sitting on disk elsewhere can no longer resume this account.
    fun logout(token: String?) {
        if (token != null) {
            send(JSONObject().put("action", "logout").put("token", token))
        }
    }

    fun requestAuxes() = send(JSONObject().put("action", "list_auxes"))

    fun requestBanks() = send(JSONObject().put("action", "list_banks"))

    fun selectAux(aux: Int) =
        send(JSONObject().put("action", "select_aux").put("aux", aux))

    // Switches this socket to full mixer control: from here on the
    // level/pan/mute actions below ride the console's own channel fader,
    // panner and mute instead of an aux's sends. selectAux() switches it
    // back. Refused unless the account holds the permission.
    fun selectMixerControl() = send(JSONObject().put("action", "select_mixer"))

    fun selectBank(bank: String?) {
        val msg = JSONObject().put("action", "select_bank")
        msg.put("bank", bank ?: JSONObject.NULL)
        send(msg)
    }

    fun setLevel(channel: Int, level: Double) = send(
        JSONObject()
            .put("action", "set_level")
            .put("channel", channel)
            .put("level", level)
    )

    fun setPan(channel: Int, pan: Double) = send(
        JSONObject()
            .put("action", "set_pan")
            .put("channel", channel)
            .put("pan", pan)
    )

    // In aux mode this mutes the channel in the selected mix only (the
    // server writes the console's per-send on/off flag). In mixer mode it
    // writes the console's own channel mute - and with hard set, also
    // drops every aux send, taking the channel out of the monitors as
    // well as the room. The console has no single control for that on an
    // input channel; the server assembles it.
    fun setMute(channel: Int, muted: Boolean, hard: Boolean = false) = send(
        JSONObject()
            .put("action", "set_mute")
            .put("channel", channel)
            .put("muted", muted)
            .put("hard", hard)
    )

    // The channel's own input stage. Mixer mode only: a head amp feeds
    // every mix and the recording at once, so the server refuses these
    // outright on an aux socket.
    fun setGain(channel: Int, gain: Double) = send(
        JSONObject()
            .put("action", "set_gain")
            .put("channel", channel)
            .put("gain", gain)
    )

    fun setTrim(channel: Int, trim: Double) = send(
        JSONObject()
            .put("action", "set_trim")
            .put("channel", channel)
            .put("trim", trim)
    )

    fun setPhantom(channel: Int, phantom: Boolean) = send(
        JSONObject()
            .put("action", "set_phantom")
            .put("channel", channel)
            .put("phantom", phantom)
    )

    // phase is the console's own enum, not a flag - 0 normal, 1..3 the
    // inverted states a stereo channel has (see ChannelState.phase). The
    // number is sent so a state the desk already holds is written back
    // unchanged rather than collapsed to 1.
    fun setPhase(channel: Int, phase: Int) = send(
        JSONObject()
            .put("action", "set_phase")
            .put("channel", channel)
            .put("phase", phase)
    )

    // Renames the channel on the console itself - every surface and every
    // other phone sees it. The server trims and caps the text.
    fun setName(channel: Int, name: String) = send(
        JSONObject()
            .put("action", "set_name")
            .put("channel", channel)
            .put("name", name)
    )

    // The opposite of setName above, despite the likeness: this one
    // changes nothing on the console. The label is stored against this
    // account and the snapshot currently live on the desk, and swapped
    // into this connection's own pushes - no other phone, and no surface
    // on the desk, ever sees it. An empty name clears the label and puts
    // the console's own name back. Aux screens only; the server refuses
    // it from a socket in mixer mode.
    fun setPersonalName(channel: Int, name: String) = send(
        JSONObject()
            .put("action", "set_personal_name")
            .put("channel", channel)
            .put("name", name)
    )

    /**
     * This account's own banks and the channel list to build them from.
     * Behind the personalization permission, like personal names and for
     * the same reason: it is this account's view of a console everyone
     * else is sharing, and nothing it writes reaches the desk.
     */
    fun requestCustomBanks() = send(JSONObject().put("action", "list_custom_banks"))

    /**
     * Replaces the whole set. Whole set rather than one bank at a time
     * because add, remove, rename and re-checking a bank's channels are
     * then the same write, and the order sent is the order the picker
     * shows.
     */
    fun saveCustomBanks(banks: List<CustomBank>) {
        val array = JSONArray()

        for (bank in banks) {
            array.put(
                JSONObject()
                    .put("name", bank.name)
                    .put("channels", JSONArray(bank.channels))
            )
        }

        send(JSONObject().put("action", "save_custom_banks").put("banks", array))
    }

    /** Throws this account's set away so the server seeds it from the
     *  console's own banks again. */
    fun resetCustomBanks() = send(JSONObject().put("action", "reset_custom_banks"))

    fun requestPresets() = send(JSONObject().put("action", "list_presets"))

    fun savePreset(name: String) = send(
        JSONObject()
            .put("action", "save_preset")
            .put("name", name)
    )

    fun loadPreset(name: String) = send(
        JSONObject()
            .put("action", "load_preset")
            .put("name", name)
    )

    private fun send(json: JSONObject) {
        // REMOVE WITH DEMO MODE.
        if (demoMode) {
            DemoMixer.handle(json)
            return
        }

        webSocket?.send(json.toString())
    }

    /**
     * The demo console's way back in: what it hands over is parsed by
     * exactly the code a real server's reply is.
     *
     * REMOVE WITH DEMO MODE.
     */
    internal fun deliverDemoMessage(text: String) {
        if (!demoMode) return
        handleMessage(text)
    }

    private fun handleMessage(text: String) {
        val json = JSONObject(text)

        when (json.optString("type")) {
            "login_result" -> {
                val ok = json.optBoolean("ok", false)
                val message = json.optString("message").takeIf { it.isNotEmpty() }
                val token = json.optString("token").takeIf { it.isNotEmpty() }
                presetsAllowed = ok && json.optBoolean("presets", false)
                muteAllowed = !ok || json.optBoolean("mute", true)
                mixerControlAllowed = ok && json.optBoolean("mixer_control", false)
                personalizationAllowed = ok && json.optBoolean("personalization", false)
                readHeadAmpRanges(json.optJSONObject("head_amp"))
                onMain { listener?.onLoginResult(ok, message, token) }
            }

            "auxes" -> {
                val arr = json.getJSONArray("auxes")
                val list = (0 until arr.length()).map {
                    val o = arr.getJSONObject(it)
                    AuxBus(
                        o.getInt("index"),
                        o.getString("name"),
                        o.optBoolean("stereo", true)
                    )
                }
                onMain { listener?.onAuxes(list) }
            }

            "banks" -> {
                val arr = json.getJSONArray("banks")
                val list = (0 until arr.length()).map { arr.getString(it) }
                onMain { listener?.onBanks(list) }
            }

            "levels" -> {
                // Mixer-mode frames carry no bus - see MIXER_AUX.
                val aux = json.optInt("aux", MIXER_AUX)
                liveSnapshot = json.optString("snapshot").takeIf { it.isNotEmpty() }
                val arr = json.getJSONArray("channels")
                val list = (0 until arr.length()).map {
                    val o = arr.getJSONObject(it)
                    ChannelState(
                        channel = o.getInt("channel"),
                        name = o.getString("name"),
                        level = if (o.isNull("level")) null else o.getDouble("level"),
                        pan = if (o.isNull("pan")) null else o.getDouble("pan"),
                        muted = o.getBoolean("muted"),
                        stereo = o.optBoolean("stereo", false),
                        gain = if (o.isNull("gain")) null else o.optDouble("gain"),
                        trim = if (o.isNull("trim")) null else o.optDouble("trim"),
                        phantom = o.optBoolean("phantom", false),
                        // phase_state is the console's value, 0..3. An
                        // older server sends only the "phase" bool, which
                        // cannot tell 3 from 1 - falling back to it loses
                        // which leg is inverted but still lights the
                        // button, which is what that server could do too.
                        phase = if (o.has("phase_state")) {
                            o.optInt("phase_state", PHASE_NORMAL)
                        } else if (o.optBoolean("phase", false)) {
                            PHASE_INVERTED
                        } else {
                            PHASE_NORMAL
                        }
                    )
                }
                onMain { listener?.onLevels(aux, list) }
            }

            "meters" -> {
                val arr = json.getJSONArray("meters")
                val map = HashMap<Int, MeterLevels>(arr.length())

                for (i in 0 until arr.length()) {
                    // Positional [channel, peakL, rmsL, peakR, rmsR] -
                    // see RemoteServer._meter_states for why it is not
                    // an object.
                    val row = arr.getJSONArray(i)
                    map[row.getInt(0)] = MeterLevels(
                        leftPeak = row.optDoubleOrNull(1),
                        leftRms = row.optDoubleOrNull(2),
                        rightPeak = row.optDoubleOrNull(3),
                        rightRms = row.optDoubleOrNull(4)
                    )
                }

                meterSequence++
                val sequence = meterSequence
                onMain { listener?.onMeters(sequence, map) }
            }

            "custom_banks" -> {
                val bankArr = json.getJSONArray("banks")
                val banks = (0 until bankArr.length()).map {
                    val o = bankArr.getJSONObject(it)
                    val chArr = o.optJSONArray("channels") ?: JSONArray()
                    CustomBank(
                        name = o.getString("name"),
                        channels = (0 until chArr.length()).map { i -> chArr.getInt(i) }
                    )
                }

                val chanArr = json.optJSONArray("channels") ?: JSONArray()
                val channels = (0 until chanArr.length()).map {
                    val o = chanArr.getJSONObject(it)
                    BankChannel(o.getInt("channel"), o.getString("name"))
                }

                onMain { listener?.onCustomBanks(banks, channels) }
            }

            "presets" -> {
                val arr = json.getJSONArray("presets")
                val list = (0 until arr.length()).map { arr.getString(it) }
                onMain { listener?.onPresets(list) }
            }

            "preset_saved" -> {
                val name = json.getString("name")
                onMain { listener?.onPresetSaved(name) }
            }

            "preset_loaded" -> {
                val name = json.getString("name")
                onMain { listener?.onPresetLoaded(name) }
            }

            "error" -> {
                val message = json.optString("message", "Unknown error")
                onMain { listener?.onError(friendlyServerMessage(message)) }
            }
        }
    }

    private fun onMain(action: () -> Unit) {
        mainHandler.post(action)
    }
}

// Translates RemoteServer's raw protocol error strings (services/remote_server.py)
// into wording that reads as a plain user-facing message rather than a log line -
// permission rejections in particular get an explicit "Access denied" prefix so
// they can't be mistaken for a network problem. Anything not recognized here is
// already a plain sentence from the server, so it's passed through unchanged.
private fun friendlyServerMessage(raw: String): String = when (raw) {
    "Not permitted for the current snapshot" -> MixerClient.SNAPSHOT_DENIED
    "Not permitted for this aux" -> "Access denied: not permitted for this aux"
    "Not permitted for presets" -> "Access denied: not permitted for presets"
    "Not permitted for mixer control" ->
        "Access denied: not permitted for full mixer control"
    "Not permitted for personalization" ->
        "Access denied: not permitted to rename channels"
    "Mixer not connected" -> "Mixer not connected - try again shortly"
    "Not authenticated" -> "Not logged in"
    else -> raw
}

/** Null where the console reported its no-signal sentinel. */
data class MeterLevels(
    val leftPeak: Double?,
    val leftRms: Double?,
    val rightPeak: Double?,
    val rightRms: Double?
)

private fun JSONArray.optDoubleOrNull(index: Int): Double? =
    if (isNull(index)) null else optDouble(index)

// Head-amp dial ranges, in dB, used until a server states its own (see
// MixerClient.gainRange). One range per parameter: the desk stores gain
// over -20..+60 and trim over -40..+40, which the official app's own
// parameter table confirms for gain and narrows for trim - see
// docs/mixer_protocol/PROTOCOL.md, "Head-amp ranges".
//
// Both dials used to sweep the union of the two, -40..+60, so that they
// read alike. That cost gain 20 dB of dead travel at the bottom, where
// the desk clamps every value back to -20.
val HEAD_AMP_GAIN_RANGE = -20.0..60.0
val HEAD_AMP_TRIM_RANGE = -40.0..40.0
