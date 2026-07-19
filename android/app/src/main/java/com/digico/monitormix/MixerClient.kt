package com.digico.monitormix

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
    fun onError(message: String) {}
    fun onAuxes(auxes: List<AuxBus>) {}
    fun onBanks(banks: List<String>) {}
    fun onLevels(aux: Int, channels: List<ChannelState>) {}
}

/**
 * Talks to the DigicoMonitorMix desktop app's RemoteServer
 * (services/remote_server.py) over a WebSocket, using the same JSON
 * protocol: list_auxes/list_banks/select_aux/select_bank/set_level/
 * set_mute out, auxes/banks/levels/error in.
 */
object MixerClient {
    private val httpClient = OkHttpClient.Builder()
        .pingInterval(15, TimeUnit.SECONDS)
        .build()

    private val mainHandler = Handler(Looper.getMainLooper())

    private var webSocket: WebSocket? = null

    var listener: MixerClientListener? = null
    var isConnected: Boolean = false
        private set

    fun connect(host: String, port: Int) {
        disconnect()

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
                onMain { listener?.onError(t.message ?: "Connection failed") }
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

    fun setMute(channel: Int, muted: Boolean) = send(
        JSONObject()
            .put("action", "set_mute")
            .put("channel", channel)
            .put("muted", muted)
    )

    private fun send(json: JSONObject) {
        webSocket?.send(json.toString())
    }

    private fun handleMessage(text: String) {
        val json = JSONObject(text)

        when (json.optString("type")) {
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
                        muted = o.getBoolean("muted")
                    )
                }
                onMain { listener?.onLevels(aux, list) }
            }

            "error" -> {
                val message = json.optString("message", "Unknown error")
                onMain { listener?.onError(message) }
            }
        }
    }

    private fun onMain(action: () -> Unit) {
        mainHandler.post(action)
    }
}
