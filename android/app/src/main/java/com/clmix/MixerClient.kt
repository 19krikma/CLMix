package com.clmix

import android.content.Context
import android.os.Handler
import android.os.Looper
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
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
    fun onBanks(banks: List<String>) {}
    fun onLevels(aux: Int, channels: List<ChannelState>) {}
    fun onPresets(names: List<String>) {}
    fun onPresetSaved(name: String) {}
    fun onPresetLoaded(name: String) {}
}

/**
 * Talks to the CLMix desktop app's RemoteServer
 * (services/remote_server.py) over a WebSocket, using the same JSON
 * protocol: login/logout/list_auxes/list_banks/select_aux/select_bank/
 * set_level/set_pan/list_presets/save_preset/load_preset out,
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

    var listener: MixerClientListener? = null
    var isConnected: Boolean = false
        private set

    // Set from login_result - gates whether MixerActivity shows the
    // Presets UI at all, mirroring the server's own per-user permission
    // check (which still applies regardless of what the client shows).
    var presetsAllowed: Boolean = false
        private set

    // Called once from CLMixApplication.onCreate() - gives this singleton
    // an application Context (never an Activity one, to avoid leaking it)
    // so it can start/stop MixerConnectionService itself.
    fun init(context: Context) {
        appContext = context.applicationContext
    }

    fun connect(host: String, port: Int) {
        disconnect()

        appContext?.let(MixerConnectionService::start)

        val request = Request.Builder()
            .url("ws://$host:$port")
            .build()

        webSocket = httpClient.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                isConnected = true
                onMain { listener?.onConnected() }
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                handleMessage(text)
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                isConnected = false
                onMain { listener?.onConnectionFailed("Server unreachable") }
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                isConnected = false
                onMain { listener?.onDisconnected() }
            }
        })
    }

    fun disconnect() {
        webSocket?.close(1000, "bye")
        webSocket = null
        isConnected = false
        presetsAllowed = false
        appContext?.let(MixerConnectionService::stop)
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
        webSocket?.send(json.toString())
    }

    private fun handleMessage(text: String) {
        val json = JSONObject(text)

        when (json.optString("type")) {
            "login_result" -> {
                val ok = json.optBoolean("ok", false)
                val message = json.optString("message").takeIf { it.isNotEmpty() }
                val token = json.optString("token").takeIf { it.isNotEmpty() }
                presetsAllowed = ok && json.optBoolean("presets", false)
                onMain { listener?.onLoginResult(ok, message, token) }
            }

            "auxes" -> {
                val arr = json.getJSONArray("auxes")
                val list = (0 until arr.length()).map {
                    val o = arr.getJSONObject(it)
                    AuxBus(o.getInt("index"), o.getString("name"))
                }
                onMain { listener?.onAuxes(list) }
            }

            "banks" -> {
                val arr = json.getJSONArray("banks")
                val list = (0 until arr.length()).map { arr.getString(it) }
                onMain { listener?.onBanks(list) }
            }

            "levels" -> {
                val aux = json.getInt("aux")
                val arr = json.getJSONArray("channels")
                val list = (0 until arr.length()).map {
                    val o = arr.getJSONObject(it)
                    ChannelState(
                        channel = o.getInt("channel"),
                        name = o.getString("name"),
                        level = if (o.isNull("level")) null else o.getDouble("level"),
                        pan = if (o.isNull("pan")) null else o.getDouble("pan"),
                        muted = o.getBoolean("muted")
                    )
                }
                onMain { listener?.onLevels(aux, list) }
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
    "Not permitted for the current snapshot" -> "Access denied: not permitted for the current snapshot"
    "Not permitted for this aux" -> "Access denied: not permitted for this aux"
    "Not permitted for presets" -> "Access denied: not permitted for presets"
    "Mixer not connected" -> "Mixer not connected - try again shortly"
    "Not authenticated" -> "Not logged in"
    else -> raw
}
