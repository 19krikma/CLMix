package com.clmix

import android.view.LayoutInflater
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
 */
class BankAdapter(
    private val onClick: (String) -> Unit
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
        val bank = banks[position]
        val context = holder.binding.root.context
        val isSelected = bank == selectedBank

        holder.binding.bankName.text = bank
        holder.binding.root.setOnClickListener { onClick(bank) }

        // Same accent fill the drawer uses for the live aux, so "this is
        // the one you are on" looks identical wherever it appears.
        holder.binding.root.setCardBackgroundColor(
            ContextCompat.getColor(
                context, if (isSelected) R.color.primary else R.color.surface
            )
        )
        holder.binding.root.strokeColor = ContextCompat.getColor(
            context, if (isSelected) R.color.primary else R.color.surface_variant
        )
        holder.binding.bankName.setTextColor(
            ContextCompat.getColor(
                context, if (isSelected) R.color.on_primary else R.color.on_surface
            )
        )
    }

    override fun getItemCount() = banks.size
}
