package com.clmix

import android.content.Context
import android.content.res.Configuration
import androidx.appcompat.app.AppCompatDelegate

// Remembers whether the user has overridden the phone's own light/dark
// setting from the mixer drawer, and puts that choice into effect.
//
// Nothing here is secret, so this is a plain SharedPreferences file
// rather than the encrypted one SessionStore needs for its token.
object ThemeStore {
    private const val FILE_NAME = "appearance"
    private const val KEY_DARK_MODE = "dark_mode"

    private fun prefs(context: Context) =
        context.getSharedPreferences(FILE_NAME, Context.MODE_PRIVATE)

    // Called from CLMixApplication, before any activity exists, so a
    // saved choice is already in effect when the first screen inflates -
    // applying it any later means visibly flashing the system theme and
    // then swapping out from under the user.
    fun apply(context: Context) {
        AppCompatDelegate.setDefaultNightMode(nightMode(context))
    }

    // Deliberately three states, not two: with nothing saved the app
    // follows the phone's own setting exactly as it always has, and only
    // stops once the user has actually expressed a preference.
    private fun nightMode(context: Context): Int {
        val prefs = prefs(context)

        if (!prefs.contains(KEY_DARK_MODE)) {
            return AppCompatDelegate.MODE_NIGHT_FOLLOW_SYSTEM
        }

        return if (prefs.getBoolean(KEY_DARK_MODE, false)) {
            AppCompatDelegate.MODE_NIGHT_YES
        } else {
            AppCompatDelegate.MODE_NIGHT_NO
        }
    }

    // What the drawer's switch should show. With no saved choice there's
    // nothing stored to read, so this falls back to what the phone is
    // actually rendering right now - the switch then starts out agreeing
    // with what's on screen rather than always starting off.
    fun isDarkMode(context: Context): Boolean {
        val prefs = prefs(context)

        if (prefs.contains(KEY_DARK_MODE)) {
            return prefs.getBoolean(KEY_DARK_MODE, false)
        }

        val nightFlags =
            context.resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK

        return nightFlags == Configuration.UI_MODE_NIGHT_YES
    }

    // Restarting the affected activities is left to AppCompat: none of
    // them declare uiMode in configChanges (see AndroidManifest.xml), so
    // setDefaultNightMode recreates whichever are running and they
    // re-inflate against the other set of colors. Colors applied in code
    // - mute/pan tints, the Fine button, the discovered-server rows -
    // only pick up the change on that rebuild, which is exactly why
    // handling the config change in-place isn't an option here.
    fun setDarkMode(context: Context, dark: Boolean) {
        prefs(context).edit().putBoolean(KEY_DARK_MODE, dark).apply()

        AppCompatDelegate.setDefaultNightMode(
            if (dark) AppCompatDelegate.MODE_NIGHT_YES else AppCompatDelegate.MODE_NIGHT_NO
        )
    }
}
