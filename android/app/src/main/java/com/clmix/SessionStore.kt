package com.clmix

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

// Holds only the server-issued session token (see RemoteServer._sessions in
// services/remote_server.py) - never the account password, which this app
// never writes to disk at all. Backed by EncryptedSharedPreferences (a
// Keystore-derived key encrypts the file at rest) since the token is a
// bearer credential: anyone who reads it can act as this user until the
// desktop app restarts or the user explicitly logs out.
object SessionStore {
    private const val FILE_NAME = "session"
    private const val KEY_TOKEN = "token"

    private fun prefs(context: Context): SharedPreferences {
        val masterKey = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()

        return EncryptedSharedPreferences.create(
            context,
            FILE_NAME,
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
        )
    }

    fun getToken(context: Context): String? =
        prefs(context).getString(KEY_TOKEN, null)

    fun saveToken(context: Context, token: String) {
        prefs(context).edit().putString(KEY_TOKEN, token).apply()
    }

    fun clear(context: Context) {
        prefs(context).edit().remove(KEY_TOKEN).apply()
    }
}
