package com.clmix

import android.graphics.Typeface
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.RecyclerView
import com.clmix.databinding.ItemBankBinding

/**
 * Banks as buttons in the pull-down panel, replacing the spinner that
 * used to sit in the top bar. A spinner shows one bank and hides the
 * rest behind a tap; on a mixing surface the whole set wants to be
 * reachable in one press, with the live one obvious without opening
 * anything.
 *
 * In edit mode (the aux screen only, see MixerActivity.setBankEditing)
 * the grid ends in a dashed "+" tile, a bank is dragged by holding it,
 * and a bank being named shows as a "New bank" tile until the name is in.
 */
class BankAdapter(
    private val onClick: (String) -> Unit,
    private val onAddClicked: () -> Unit = {},
    private val onStartDrag: (View, Int) -> Unit = { _, _ -> }
) : RecyclerView.Adapter<BankAdapter.ViewHolder>() {

    class ViewHolder(val binding: ItemBankBinding) : RecyclerView.ViewHolder(binding.root)

    private var banks: List<String> = emptyList()

    /** The bank on screen, highlighted; null before one has been chosen. */
    var selectedBank: String? = null
        set(value) {
            if (field != value) {
                field = value
                notifyDataSetChanged()
            }
        }

    var editMode: Boolean = false
        set(value) {
            if (field != value) {
                field = value
                if (!value) placeholder = false
                notifyDataSetChanged()
            }
        }

    /** A bank that exists on screen only while its name is asked for. */
    var placeholder: Boolean = false
        set(value) {
            if (field != value) {
                field = value
                notifyDataSetChanged()
            }
        }

    /** The bank being dragged, drawn faded where it was picked up from. */
    var liftedPosition: Int? = null
        set(value) {
            if (field != value) {
                field = value
                notifyDataSetChanged()
            }
        }

    /** Banks proper - the tiles a drag may land between. */
    val bankCount: Int get() = banks.size

    fun submit(newBanks: List<String>) {
        if (banks == newBanks) return
        banks = newBanks
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
        val binding = ItemBankBinding.inflate(
            LayoutInflater.from(parent.context), parent, false
        )
        return ViewHolder(binding)
    }

    override fun onBindViewHolder(holder: ViewHolder, position: Int) {
        val context = holder.binding.root.context
        val card = holder.binding.root
        val label = holder.binding.bankName

        card.alpha = 1f
        card.contentDescription = null
        label.scaleX = 1f
        label.scaleY = 1f
        card.setOnLongClickListener(null)
        card.isLongClickable = false

        if (position >= banks.size) {
            bindExtraTile(holder, isPlaceholder = placeholder && position == banks.size)
            return
        }

        val bank = banks[position]
        val isSelected = bank == selectedBank

        label.text = bank
        label.setTypeface(null, Typeface.BOLD)
        label.textSize = 13f
        card.setOnClickListener { onClick(bank) }

        if (editMode) {
            card.setOnLongClickListener {
                val at = holder.bindingAdapterPosition
                if (at != RecyclerView.NO_POSITION && at < banks.size) {
                    onStartDrag(card, at)
                }
                true
            }
        }

        if (position == liftedPosition) card.alpha = 0.3f

        // Same accent fill the drawer uses for the live aux, so "this is
        // the one you are on" looks identical wherever it appears.
        card.setCardBackgroundColor(
            ContextCompat.getColor(
                context, if (isSelected) R.color.primary else R.color.surface
            )
        )
        card.strokeColor = ContextCompat.getColor(
            context, if (isSelected) R.color.primary else R.color.surface_variant
        )
        label.setTextColor(
            ContextCompat.getColor(
                context, if (isSelected) R.color.on_primary else R.color.on_surface
            )
        )
    }

    /** The placeholder tile, or the dashed "+" that ends the grid. */
    private fun bindExtraTile(holder: ViewHolder, isPlaceholder: Boolean) {
        val context = holder.binding.root.context
        val card = holder.binding.root
        val label = holder.binding.bankName
        val primary = ContextCompat.getColor(context, R.color.primary)

        card.setCardBackgroundColor(ContextCompat.getColor(context, R.color.surface))
        card.strokeColor = primary
        label.setTextColor(primary)

        if (isPlaceholder) {
            label.text = "New bank"
            label.setTypeface(null, Typeface.ITALIC)
            label.textSize = 13f
            card.setOnClickListener(null)
        } else {
            // Drawn larger by scaling rather than by text size, so the
            // tile keeps the same height as the banks in its row.
            label.text = "+"
            label.setTypeface(null, Typeface.BOLD)
            label.textSize = 13f
            label.scaleX = 1.8f
            label.scaleY = 1.8f
            card.contentDescription = "Add bank"
            card.setOnClickListener { onAddClicked() }
        }
    }

    override fun getItemCount(): Int {
        if (!editMode) return banks.size
        // The "+" always; the placeholder ahead of it while one is named.
        return banks.size + 1 + if (placeholder) 1 else 0
    }
}
