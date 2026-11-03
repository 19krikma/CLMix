package com.clmix

import android.content.ClipData
import android.graphics.Canvas
import android.graphics.Paint
import android.os.Build
import android.view.DragEvent
import android.view.View
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.RecyclerView
import kotlin.math.hypot
import kotlin.math.max

/**
 * Hold-and-drag reordering for one RecyclerView, with a line drawn in the
 * gap the item will land in if let go there.
 *
 * Built on the platform's drag and drop rather than ItemTouchHelper:
 * ItemTouchHelper shuffles the neighbours out of the way as the finger
 * moves, which in a grid of banks makes everything jump about at once.
 * Here nothing moves until the drop - the line alone says where it goes.
 *
 * Drop positions are insertion points: 0 is before the first item and
 * [count] is after the last. [onDrop] gets the picked-up position and
 * the insertion point; it is not called when the two mean "same place".
 */
class ReorderDrag(
    private val recycler: RecyclerView,
    private val count: () -> Int,
    private val horizontalScroll: Boolean,
    private val onLift: (Int?) -> Unit,
    private val onDrop: (from: Int, to: Int) -> Unit
) {
    private var from: Int? = null
    private var dropIndex: Int? = null
    private var lastX = 0f
    private var lastY = 0f
    private var scrollStep = 0

    private val density = recycler.resources.displayMetrics.density
    private val edgeZone = 56 * density

    private val linePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = ContextCompat.getColor(recycler.context, R.color.primary)
        strokeWidth = 3 * density
        strokeCap = Paint.Cap.ROUND
    }

    private val autoScroll = object : Runnable {
        override fun run() {
            if (scrollStep == 0 || from == null) return
            recycler.scrollBy(scrollStep, 0)
            updateDropIndex(lastX, lastY)
            recycler.postOnAnimation(this)
        }
    }

    init {
        recycler.addItemDecoration(object : RecyclerView.ItemDecoration() {
            override fun onDrawOver(c: Canvas, parent: RecyclerView, state: RecyclerView.State) {
                drawLine(c)
            }
        })

        recycler.setOnDragListener { _, event -> handle(event) }
    }

    /** Whether [view] is an item of this helper's list. */
    fun owns(view: View): Boolean = view.parent === recycler

    fun start(view: View, position: Int) {
        from = position
        onLift(position)

        val shadow = View.DragShadowBuilder(view)
        val clip = ClipData.newPlainText("", "")

        // The token is this helper, so a bank dragged over the strips (or
        // a strip over the banks) is ignored by the other list.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            view.startDragAndDrop(clip, shadow, this, 0)
        } else {
            @Suppress("DEPRECATION")
            view.startDrag(clip, shadow, this, 0)
        }
        view.performHapticFeedback(android.view.HapticFeedbackConstants.LONG_PRESS)
    }

    private fun handle(event: DragEvent): Boolean {
        if (event.localState !== this) return false

        when (event.action) {
            DragEvent.ACTION_DRAG_STARTED -> return true

            DragEvent.ACTION_DRAG_LOCATION -> {
                lastX = event.x
                lastY = event.y
                updateDropIndex(event.x, event.y)
                updateAutoScroll(event.x)
            }

            DragEvent.ACTION_DRAG_EXITED -> {
                stopAutoScroll()
                setDropIndex(null)
            }

            DragEvent.ACTION_DROP -> {
                stopAutoScroll()
                val start = from
                val target = computeDropIndex(event.x, event.y)
                setDropIndex(null)

                if (start != null && target != start && target != start + 1) {
                    onDrop(start, target)
                }
            }

            DragEvent.ACTION_DRAG_ENDED -> {
                stopAutoScroll()
                setDropIndex(null)
                from = null
                onLift(null)
            }
        }

        return true
    }

    private fun updateDropIndex(x: Float, y: Float) = setDropIndex(computeDropIndex(x, y))

    private fun setDropIndex(index: Int?) {
        if (dropIndex == index) return
        dropIndex = index
        recycler.invalidate()
    }

    /** The gap nearest the finger: beside whichever item is closest,
     *  on the side of it the finger is on. */
    private fun computeDropIndex(x: Float, y: Float): Int {
        val total = count()
        var best: View? = null
        var bestPosition = -1
        var bestDistance = Float.MAX_VALUE

        for (i in 0 until recycler.childCount) {
            val child = recycler.getChildAt(i)
            val position = recycler.getChildAdapterPosition(child)
            if (position == RecyclerView.NO_POSITION || position >= total) continue

            val dx = max(0f, max(child.left - x, x - child.right))
            val dy = max(0f, max(child.top - y, y - child.bottom))
            val distance = hypot(dx, dy)

            if (distance < bestDistance) {
                bestDistance = distance
                best = child
                bestPosition = position
            }
        }

        val child = best ?: return if (total == 0) 0 else total
        val centre = (child.left + child.right) / 2f

        return if (x < centre) bestPosition else bestPosition + 1
    }

    private fun updateAutoScroll(x: Float) {
        if (!horizontalScroll) return

        val step = (10 * density).toInt()
        val wanted = when {
            x < edgeZone -> -step
            x > recycler.width - edgeZone -> step
            else -> 0
        }

        if (wanted == scrollStep) return
        val wasIdle = scrollStep == 0
        scrollStep = wanted

        if (wanted != 0 && wasIdle) recycler.postOnAnimation(autoScroll)
    }

    private fun stopAutoScroll() {
        scrollStep = 0
        recycler.removeCallbacks(autoScroll)
    }

    private fun drawLine(c: Canvas) {
        val index = dropIndex ?: return
        val total = count()
        val manager = recycler.layoutManager ?: return

        // The item the line goes in front of, or the last one when it
        // goes at the very end.
        val atEnd = index >= total
        val anchorPosition = if (atEnd) total - 1 else index
        val anchor = manager.findViewByPosition(anchorPosition) ?: return
        val params = anchor.layoutParams as RecyclerView.LayoutParams

        // Midway into the gap: half the margin out from the item itself.
        val x = if (atEnd) {
            manager.getDecoratedRight(anchor) + params.rightMargin / 2f
        } else {
            manager.getDecoratedLeft(anchor) - params.leftMargin / 2f
        }.coerceIn(linePaint.strokeWidth, recycler.width - linePaint.strokeWidth)

        val inset = 4 * density
        val top = manager.getDecoratedTop(anchor) + inset
        val bottom = manager.getDecoratedBottom(anchor) - inset

        c.drawLine(x, top, x, bottom, linePaint)
    }
}
