package com.clmix

import android.content.Context
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import androidx.core.view.GravityCompat
import androidx.drawerlayout.widget.DrawerLayout
import androidx.recyclerview.widget.GridLayoutManager
import androidx.recyclerview.widget.RecyclerView

/**
 * Smallest width, in dp, from which CLMix lays a screen out as a tablet
 * rather than a phone. Set to the OnePlus Pad 3 used for testing (914dp
 * across in portrait), so for now "tablet" means a screen at least that
 * size. Lower it once the tablet layouts have been tried on smaller ones.
 *
 * Smallest width rather than the current width so a tablet stays a tablet
 * in both orientations, and a phone on its side does not become one.
 */
const val LARGE_SCREEN_MIN_WIDTH_DP = 914

/**
 * Whether there is room to stop hiding things. The phone layouts fold
 * most controls away behind toggles to give the faders the screen; on a
 * tablet those controls stay out.
 */
fun Context.isLargeScreen(): Boolean =
    resources.configuration.smallestScreenWidthDp >= LARGE_SCREEN_MIN_WIDTH_DP

/** Width each bank button gets on a tablet, where the number to a row
 *  follows the room instead: about the width of a phone's own. */
const val LARGE_BANK_TILE_DP = 115

/**
 * Moves the bank panel up into the middle of the top bar, in an outlined
 * box, where the bar's spacer was. On a tablet there is room beside the
 * menu and Fine for the whole grid, so the banks stop taking a row of
 * their own away from the faders.
 *
 * The same view is moved rather than a second copy made, so its adapter,
 * drag handlers and edit row come with it.
 */
fun moveBanksIntoTopBar(
    topBar: LinearLayout,
    spacer: View,
    panel: View,
    grid: RecyclerView? = null
) {
    val density = topBar.resources.displayMetrics.density
    fun dp(value: Int) = (value * density).toInt()

    (panel.parent as? ViewGroup)?.removeView(panel)
    val slot = topBar.indexOfChild(spacer)
    topBar.removeView(spacer)
    topBar.addView(
        panel,
        slot,
        LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
            marginStart = dp(12)
            marginEnd = dp(2)
            topMargin = dp(2)
            bottomMargin = dp(2)
        }
    )
    panel.setBackgroundResource(R.drawable.bank_box_border)
    panel.setPadding(dp(4), dp(4), dp(4), dp(4))

    // As many to a row as the box has room for. Set after the box has
    // been laid out, since its width is whatever the bar leaves over -
    // and posted, as a change asked for during layout is dropped.
    // Without a grid (Mixer Control's layered panel, which lays its own
    // rows out four to a layer) there is nothing to fit.
    grid?.addOnLayoutChangeListener { view, left, _, right, _, _, _, _, _ ->
        val columns = maxOf(1, (right - left - view.paddingLeft - view.paddingRight) /
            dp(LARGE_BANK_TILE_DP))
        val manager = (view as RecyclerView).layoutManager as? GridLayoutManager
        if (manager != null && manager.spanCount != columns) {
            view.post { manager.spanCount = columns }
        }
    }
}

/**
 * Docks the side menu down the left of the screen, or puts it back in its
 * drawer. Docked, the rest of the screen sits beside it rather than under
 * it, and no button is needed to find it. Used for a tablet on its side,
 * where there is width to spare; upright, the faders want that width more.
 *
 * Done without moving either view: DrawerLayout lays out any child
 * without a gravity as plain content, so docking is taking the menu's
 * gravity away and giving [content] - the drawer's main child - a margin
 * of the menu's width. An earlier version wrapped the two in a row of
 * their own, and that row dropped relayouts: faders came back from a
 * rotation drawn without their tracks.
 */
fun setSideMenuDocked(drawer: DrawerLayout, menu: View, content: View, docked: Boolean) {
    val menuParams = menu.layoutParams as DrawerLayout.LayoutParams
    val contentParams = content.layoutParams as DrawerLayout.LayoutParams
    val wanted = if (docked) Gravity.NO_GRAVITY else GravityCompat.START

    if (menuParams.gravity == wanted) return

    // Closed first, while it is still a drawer to close. Only on the way
    // in: docked it is not a drawer, and closeDrawer on it throws.
    if (docked) drawer.closeDrawer(menu, false)

    menuParams.gravity = wanted
    contentParams.marginStart = if (docked) menuParams.width else 0
    // DrawerLayout stretches plain content to its full width whatever
    // width it asks for, so a docked menu covered the whole screen. A
    // margin of everything past its own width holds it to that width.
    // The screen's size here is the configuration's; the listener below
    // corrects it against the size actually laid out.
    menuParams.marginEnd = if (docked) {
        val screen = (drawer.resources.configuration.screenWidthDp *
            drawer.resources.displayMetrics.density).toInt()
        maxOf(0, screen - menuParams.width)
    } else {
        0
    }
    menu.layoutParams = menuParams
    content.layoutParams = contentParams

    // A closed drawer is hidden by making the view itself INVISIBLE, and
    // that would stay with it.
    if (docked) menu.visibility = View.VISIBLE
    drawer.setDrawerLockMode(
        if (docked) DrawerLayout.LOCK_MODE_LOCKED_CLOSED else DrawerLayout.LOCK_MODE_UNLOCKED
    )
    drawer.requestLayout()

    if (drawer.getTag(R.id.menu_content) == null) {
        drawer.setTag(R.id.menu_content, true)
        drawer.addOnLayoutChangeListener { view, left, _, right, _, _, _, _, _ ->
            val params = menu.layoutParams as DrawerLayout.LayoutParams
            if (params.gravity != Gravity.NO_GRAVITY) return@addOnLayoutChangeListener
            val margin = maxOf(0, right - left - params.width)
            // Posted: a change asked for during layout is dropped.
            if (params.marginEnd != margin) {
                view.post {
                    params.marginEnd = margin
                    menu.layoutParams = params
                }
            }
        }
    }
}

/** Whether this screen should have its side menu docked: a tablet on its side. */
fun Context.wantsDockedMenu(): Boolean =
    isLargeScreen() &&
        resources.configuration.orientation == android.content.res.Configuration.ORIENTATION_LANDSCAPE

/**
 * Closes the side menu's drawer if it is one. Docked (see
 * [setSideMenuDocked]) there is no drawer to close, and closeDrawer on a
 * missing drawer throws.
 */
fun DrawerLayout.closeMenuDrawer() {
    if (isDrawerOpen(GravityCompat.START)) closeDrawer(GravityCompat.START)
}
