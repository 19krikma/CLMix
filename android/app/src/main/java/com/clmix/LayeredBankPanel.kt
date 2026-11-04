package com.clmix

import android.content.Context
import android.graphics.Typeface
import android.util.AttributeSet
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.content.ContextCompat
import com.clmix.databinding.ItemBankBinding

/**
 * Mixer Control's bank picker: every bank on the console's surface,
 * layer by layer, the way the desk itself lays them out.
 *
 * Each layer is a labelled row of its banks - four to a row, which is
 * what a layer holds on the desk. On a phone the label sits above its
 * row; [inline] puts it at the start of the row instead, which keeps the
 * tablet's top bar, where this lives there, from growing a line per
 * layer.
 *
 * A bank with no input channels on it - DCAs, outputs - is shown dimmed
 * and does nothing when tapped: it is on the surface, so it is listed,
 * but Mixer Control only drives input channels and has no strips to show
 * for it.
 *
 * A server too old to send the layout gives only bank names; those come
 * in as one layer numbered 0, which is drawn without a label.
 */
class LayeredBankPanel @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null
) : LinearLayout(context, attrs) {

    var onPick: (LayoutBank) -> Unit = {}

    /** Label beside each row rather than above it - see the class note. */
    var inline: Boolean = false
        set(value) {
            if (field != value) {
                field = value
                rebuild()
            }
        }

    private var layers: List<BankLayer> = emptyList()
    private var selectedId: String? = null
    private val cards = mutableMapOf<String, ItemBankBinding>()

    init {
        orientation = VERTICAL
        // The panel floats over the strips, so a tap on it that no bank
        // takes - a dimmed one, a label, a gap - must stop here rather
        // than land on the fader or EQ underneath.
        isClickable = true
    }

    fun show(layers: List<BankLayer>, selectedId: String?) {
        this.layers = layers
        this.selectedId = selectedId
        rebuild()
    }

    fun select(id: String?) {
        if (selectedId == id) return
        selectedId = id
        cards.forEach { (bankId, card) -> style(card, bankId == id) }
    }

    private fun rebuild() {
        removeAllViews()
        cards.clear()

        for (layer in layers) {
            val label = if (layer.number > 0) "LAYER ${layer.number}" else null

            // Four to a row, as a layer holds on the desk; a names-only
            // list from an older server can run longer and wraps.
            layer.banks.chunked(BANKS_PER_ROW).forEachIndexed { rowIndex, chunk ->
                val rowLabel = label.takeIf { rowIndex == 0 }

                if (!inline && rowLabel != null) addView(header(rowLabel))

                val row = LinearLayout(context).apply {
                    orientation = HORIZONTAL
                    gravity = Gravity.CENTER_VERTICAL
                }

                if (inline && label != null) {
                    // Every row of the layer keeps the label's width, so
                    // the banks line up under each other.
                    row.addView(
                        sideLabel(rowLabel ?: ""),
                        LayoutParams(dp(SIDE_LABEL_DP), LayoutParams.WRAP_CONTENT)
                    )
                }

                for (bank in chunk) row.addView(card(bank), LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f))

                // Short rows keep the same button width as full ones.
                repeat(BANKS_PER_ROW - chunk.size) {
                    row.addView(View(context), LayoutParams(0, 1, 1f))
                }

                addView(row, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT))
            }
        }
    }

    private fun header(text: String) = TextView(context).apply {
        this.text = text
        textSize = 11f
        letterSpacing = 0.08f
        setTypeface(typeface, Typeface.BOLD)
        setTextColor(ContextCompat.getColor(context, R.color.on_surface_variant))
        setPadding(dp(6), dp(8), dp(6), dp(2))
    }

    private fun sideLabel(text: String) = TextView(context).apply {
        // "LAYER 1" over two lines, so it stays narrow.
        this.text = text.replace(" ", "\n")
        textSize = 10f
        letterSpacing = 0.08f
        gravity = Gravity.CENTER
        setTypeface(typeface, Typeface.BOLD)
        setTextColor(ContextCompat.getColor(context, R.color.on_surface_variant))
    }

    private fun card(bank: LayoutBank): View {
        val binding = ItemBankBinding.inflate(LayoutInflater.from(context), this, false)
        val usable = bank.inputs > 0

        binding.bankName.text = bank.name
        binding.bankName.setTypeface(null, Typeface.BOLD)
        binding.bankName.textSize = 13f
        binding.root.alpha = if (usable) 1f else DIMMED_ALPHA
        binding.root.isEnabled = usable
        binding.root.contentDescription =
            if (usable) bank.name else "${bank.name}, no input channels"
        binding.root.setOnClickListener { if (usable) onPick(bank) }

        cards[bank.id] = binding
        style(binding, bank.id == selectedId)
        return binding.root
    }

    /** The same look as the aux screen's bank buttons (BankAdapter). */
    private fun style(binding: ItemBankBinding, selected: Boolean) {
        binding.root.setCardBackgroundColor(
            ContextCompat.getColor(context, if (selected) R.color.primary else R.color.surface)
        )
        binding.root.strokeColor = ContextCompat.getColor(
            context, if (selected) R.color.primary else R.color.surface_variant
        )
        binding.bankName.setTextColor(
            ContextCompat.getColor(context, if (selected) R.color.on_primary else R.color.on_surface)
        )
    }

    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()

    private companion object {
        const val BANKS_PER_ROW = 4
        const val SIDE_LABEL_DP = 52
        const val DIMMED_ALPHA = 0.4f
    }
}
