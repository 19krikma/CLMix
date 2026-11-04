package com.clmix

import android.app.Activity
import android.content.res.ColorStateList
import android.view.View
import android.widget.TextView
import androidx.core.content.ContextCompat
import androidx.core.view.isVisible
import com.clmix.databinding.MenuModeButtonsBinding

/**
 * The sideways slide between Mixer Control and the AUX screens. Mixer
 * Control sits on the left: going to AUX pushes it off that way and
 * brings the aux list in from the right, and Channels runs it back.
 *
 * Call right after the startActivity or finish it belongs to.
 */
fun Activity.slideToAux() = slide(R.anim.slide_in_right, R.anim.slide_out_left)

fun Activity.slideToChannels() = slide(R.anim.slide_in_left, R.anim.slide_out_right)

// The one-shot form on purpose. Android 14's replacement,
// overrideActivityTransition, sets this activity's animation for every
// later open or close as well, which would carry the slide into screens
// that have nothing to do with this move.
@Suppress("DEPRECATION")
private fun Activity.slide(enter: Int, exit: Int) = overridePendingTransition(enter, exit)

/** Which of the two screens an account holding Mixer Control is on. */
enum class MixScreen { CHANNELS, AUX }

/**
 * The Channels and AUX buttons at the top of the side menu. Both are
 * always shown, the one in front filled in the accent so it reads as
 * where you are, the other grey as the way across. Tapping the one you
 * are on does nothing. Gone for an account without Mixer Control, which
 * only ever has the aux screens.
 */
fun Activity.bindModeButtons(
    buttons: MenuModeButtonsBinding,
    current: MixScreen,
    goAcross: () -> Unit
) {
    buttons.root.isVisible = MixerClient.mixerControlAllowed

    listOf(
        buttons.channelsButton to MixScreen.CHANNELS,
        buttons.auxButton to MixScreen.AUX
    ).forEach { (button, screen) ->
        val active = screen == current
        button.backgroundTintList = ColorStateList.valueOf(
            ContextCompat.getColor(this, if (active) R.color.primary else R.color.mute_inactive)
        )
        button.setTextColor(
            ContextCompat.getColor(this, if (active) R.color.on_primary else R.color.on_mute_inactive)
        )
        button.isSelected = active
        // Still clickable when it is the one you are on, with nothing to
        // do: a button that is not clickable lets the tap through the
        // menu to whatever strip is underneath - it opened an EQ.
        button.setOnClickListener { if (!active) goAcross() }
    }
}

/**
 * "Snapshot: NAME" on the strip along the bottom of both mixer screens.
 * Called on every levels frame, which is what carries the name, so it is
 * a no-op unless the snapshot changed - a recall shows up on the next
 * frame. Stays invisible until a name has arrived.
 */
fun TextView.showSnapshot(name: String?) {
    val wanted = name?.let { "Snapshot: $it" }
    if (wanted == null) return
    if (text != wanted) text = wanted
    if (visibility == View.INVISIBLE) visibility = View.VISIBLE
}
