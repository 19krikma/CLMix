package com.clmix

import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.widget.TextView

/**
 * The "★ Leader" pill next to a snapshot Leader's name in chat, and the
 * colours a leader can pick for it on their profile screen. The server
 * keeps the choice (UserStore.leader_color) and stamps it on each
 * message, so everyone sees the colour the leader picked.
 */
object LeaderBadge {
    const val DEFAULT_HEX = "#D4AF37"
    val DEFAULT_COLOR = Color.parseColor(DEFAULT_HEX)
    const val TEXT = "\u2605 Leader"

    /** What the profile screen offers, gold first. */
    val PALETTE = listOf(
        "#D4AF37", "#F28C28", "#E5484D", "#E54887", "#8E4EC6",
        "#3E7BFA", "#12A594", "#46A758", "#9BA1A6",
    )

    fun parse(hex: String?): Int = try {
        if (hex.isNullOrEmpty()) DEFAULT_COLOR else Color.parseColor(hex)
    } catch (e: IllegalArgumentException) {
        DEFAULT_COLOR
    }

    /** Dark ink on light colours, white on dark ones. */
    fun inkFor(color: Int): Int {
        val luma = 0.299 * Color.red(color) + 0.587 * Color.green(color) + 0.114 * Color.blue(color)
        return if (luma > 150) Color.parseColor("#1A1A1A") else Color.WHITE
    }

    fun style(view: TextView, color: Int) {
        val density = view.resources.displayMetrics.density
        view.text = TEXT
        view.background = GradientDrawable().apply {
            // Fully round ends, however tall the text makes it.
            cornerRadius = 100 * density
            setColor(color)
        }
        view.setTextColor(inkFor(color))
    }
}
