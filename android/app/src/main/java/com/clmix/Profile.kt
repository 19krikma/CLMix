package com.clmix

import android.content.ContentResolver
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.media.ExifInterface
import android.net.Uri
import android.util.Base64
import org.json.JSONObject
import java.io.ByteArrayOutputStream

/** The logged-in account as its profile screen shows it. */
data class AccountProfile(
    val username: String,
    val firstName: String,
    val lastName: String,
    /** "First Last" where either is set, the username otherwise. */
    val displayName: String,
    val picture: Bitmap?,
)

/**
 * The logged-in account's own profile, app-wide like Chat: the menu's
 * header and the profile screen both draw from here. MixerClient hands
 * "profile" and "profile_result" straight in; the server's side is
 * RemoteServer._handle_profile.
 */
object Profile {
    // Big enough to look sharp in the profile screen's circle, small
    // enough to cost the server's users file a few tens of KB.
    private const val PICTURE_SIZE = 320
    private const val PICTURE_QUALITY = 85

    /** False against a desktop too old to have profiles. */
    var supported = false
        private set

    var current: AccountProfile? = null
        private set

    private val observers = mutableListOf<(AccountProfile?) -> Unit>()

    /** The profile screen's answer to an edit; null while it is closed. */
    var onResult: ((ok: Boolean, message: String?) -> Unit)? = null

    fun observe(observer: (AccountProfile?) -> Unit) {
        observers.remove(observer)
        observers.add(observer)
        observer(current)
    }

    fun unobserve(observer: (AccountProfile?) -> Unit) {
        observers.remove(observer)
    }

    // --- From MixerClient, on the main thread ---

    /** What login_result says about the account, before get_profile's
     *  full answer (with the picture) arrives. */
    fun onLogin(json: JSONObject) {
        supported = json.optBoolean("profile", false)
        val username = json.optString("username")

        if (!supported || username.isEmpty()) {
            current = null
        } else if (current?.username != username) {
            current = AccountProfile(
                username = username, firstName = "", lastName = "",
                displayName = json.optString("display_name").ifEmpty { username },
                picture = null,
            )
        }
        notifyObservers()
    }

    fun onProfile(json: JSONObject) {
        val username = json.optString("username")
        current = AccountProfile(
            username = username,
            firstName = json.optString("first_name"),
            lastName = json.optString("last_name"),
            displayName = json.optString("display_name").ifEmpty { username },
            picture = decodePicture(json.optString("picture").takeIf {
                !json.isNull("picture") && it.isNotEmpty()
            }),
        )
        notifyObservers()
    }

    fun onResult(json: JSONObject) {
        val message = json.optString("message").takeIf { it.isNotEmpty() }
        onResult?.invoke(json.optBoolean("ok", false), message)
    }

    /** A different account may log in next. */
    fun clear() {
        supported = false
        current = null
        notifyObservers()
    }

    private fun notifyObservers() {
        val profile = current
        observers.toList().forEach { it(profile) }
    }

    private fun decodePicture(base64: String?): Bitmap? {
        if (base64 == null) return null
        return try {
            val bytes = Base64.decode(base64, Base64.DEFAULT)
            BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
        } catch (e: IllegalArgumentException) {
            null
        }
    }

    /**
     * A photo off the phone, as the server wants it: upright, cropped to
     * a centred square, shrunk and JPEG'd, then base64. Null if the
     * picture can't be read.
     */
    fun encodePicture(resolver: ContentResolver, uri: Uri): String? {
        // Read the size first, so a 50-megapixel photo is decoded at a
        // fraction of its size rather than all at once.
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        resolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, bounds) }
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null

        var sample = 1
        while (minOf(bounds.outWidth, bounds.outHeight) / (sample * 2) >= PICTURE_SIZE) {
            sample *= 2
        }

        val decoded = resolver.openInputStream(uri)?.use {
            BitmapFactory.decodeStream(it, null, BitmapFactory.Options().apply { inSampleSize = sample })
        } ?: return null

        val rotation = try {
            resolver.openInputStream(uri)?.use {
                when (ExifInterface(it).getAttributeInt(
                    ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL
                )) {
                    ExifInterface.ORIENTATION_ROTATE_90 -> 90f
                    ExifInterface.ORIENTATION_ROTATE_180 -> 180f
                    ExifInterface.ORIENTATION_ROTATE_270 -> 270f
                    else -> 0f
                }
            } ?: 0f
        } catch (e: Exception) {
            0f
        }

        val side = minOf(decoded.width, decoded.height)
        val scale = minOf(1f, PICTURE_SIZE.toFloat() / side)
        val matrix = Matrix().apply {
            postScale(scale, scale)
            postRotate(rotation)
        }
        val square = Bitmap.createBitmap(
            decoded,
            (decoded.width - side) / 2, (decoded.height - side) / 2,
            side, side, matrix, true
        )

        val out = ByteArrayOutputStream()
        square.compress(Bitmap.CompressFormat.JPEG, PICTURE_QUALITY, out)
        return Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
    }
}
