package com.clmix

import android.content.Context
import android.content.SharedPreferences
import android.util.Log
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

// Holds only the server-issued session token (see RemoteServer._sessions in
// services/remote_server.py) - never the account password, which this app
// never writes to disk at all. Backed by EncryptedSharedPreferences (a
// Keystore-derived key encrypts the file at rest) since the token is a
// bearer credential: anyone who reads it can act as this user until the
// desktop app restarts or the user explicitly logs out.
object SessionStore {
    private const val TAG = "SessionStore"
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

    // The Keystore-backed cipher underneath EncryptedSharedPreferences is
    // known to throw on some OEM Keystore implementations (observed on
    // several Samsung/foldable builds) - most often after the device's
    // lock-screen credential changes and invalidates the existing key.
    // Session persistence is a convenience, not something worth crashing
    // the whole app over (this runs unconditionally on every launch, from
    // ConnectActivity.onCreate), so any failure here is treated the same
    // as "no saved session" rather than propagating, and the on-disk file
    // is wiped so a corrupt one doesn't keep failing on every future
    // launch.
    fun getToken(context: Context): String? = try {
        prefs(context).getString(KEY_TOKEN, null)
    } catch (e: Exception) {
        Log.w(TAG, "Failed to read session token, clearing stored session", e)
        context.deleteSharedPreferences(FILE_NAME)
        null
    }

    fun saveToken(context: Context, token: String) {
        try {
            prefs(context).edit().putString(KEY_TOKEN, token).apply()
        } catch (e: Exception) {
            Log.w(TAG, "Failed to save session token", e)
        }
    }

    fun clear(context: Context) {
        try {
            prefs(context).edit().remove(KEY_TOKEN).apply()
        } catch (e: Exception) {
            Log.w(TAG, "Failed to clear session token", e)
            context.deleteSharedPreferences(FILE_NAME)
        }
    }
}
