package com.clmix

import org.json.JSONObject

/** One account riding an aux right now. */
data class AuxUser(
    val user: String,
    /** "First Last" where set, the username otherwise. */
    val name: String
)

/**
 * Who has each aux open right now, app-wide like Profile and Chat: the
 * aux list screen and the mixer's aux sheet both draw from here.
 * MixerClient hands "aux_users" straight in; the server's side is
 * RemoteServer._aux_users_message, which sends it whenever anyone opens
 * or leaves an aux.
 */
object AuxUsers {
    /** False against a desktop too old to say who is on which aux - and
     *  so too old to understand leave_aux. */
    var supported = false
        private set

    /** Aux index to the accounts on it, sorted by name. */
    var byAux: Map<Int, List<AuxUser>> = emptyMap()
        private set

    private val observers = mutableListOf<(Map<Int, List<AuxUser>>) -> Unit>()

    fun observe(observer: (Map<Int, List<AuxUser>>) -> Unit) {
        observers.remove(observer)
        observers.add(observer)
        observer(byAux)
    }

    fun unobserve(observer: (Map<Int, List<AuxUser>>) -> Unit) {
        observers.remove(observer)
    }

    // --- From MixerClient, on the main thread ---

    fun onMessage(json: JSONObject) {
        supported = true
        val auxes = json.optJSONArray("auxes")
        val map = mutableMapOf<Int, List<AuxUser>>()

        if (auxes != null) {
            for (i in 0 until auxes.length()) {
                val entry = auxes.optJSONObject(i) ?: continue
                val users = entry.optJSONArray("users") ?: continue
                map[entry.optInt("index")] = (0 until users.length()).mapNotNull { j ->
                    users.optJSONObject(j)?.let { u ->
                        val user = u.optString("user")
                        AuxUser(user, u.optString("name").ifEmpty { user })
                    }
                }
            }
        }

        byAux = map
        observers.toList().forEach { it(byAux) }
    }

    /** The next connection may be another account, or another desk. */
    fun clear() {
        supported = false
        byAux = emptyMap()
        observers.toList().forEach { it(byAux) }
    }
}
