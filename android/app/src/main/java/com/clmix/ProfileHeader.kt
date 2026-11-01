package com.clmix

import android.content.res.ColorStateList
import android.graphics.Bitmap
import android.widget.ImageView
import androidx.core.content.ContextCompat
import androidx.core.view.isVisible
import com.clmix.databinding.DrawerProfileHeaderBinding
import com.google.android.material.imageview.ShapeableImageView

/**
 * Draws an account's picture into a round image view, or a person
 * figure on a plain circle when it has none.
 */
fun ShapeableImageView.showProfilePicture(
    picture: Bitmap?,
    placeholder: Int = R.drawable.ic_person,
) {
    if (picture != null) {
        setContentPadding(0, 0, 0, 0)
        scaleType = ImageView.ScaleType.CENTER_CROP
        imageTintList = null
        backgroundTintList = null
        background = null
        setImageBitmap(picture)
    } else {
        // A tenth of the width either side, so the figure fills most of
        // the circle at any size.
        val inset = (layoutParams?.width ?: width).coerceAtLeast(0) / 10
        setContentPadding(inset, inset, inset, inset)
        scaleType = ImageView.ScaleType.FIT_CENTER
        setBackgroundResource(R.drawable.profile_placeholder)
        imageTintList = ColorStateList.valueOf(
            ContextCompat.getColor(context, R.color.on_mute_inactive)
        )
        setImageResource(placeholder)
    }
}

/**
 * The side menu's header: picture and name, kept up to date from
 * Profile while the screen is in front. Hidden against a desktop too
 * old to have profiles.
 */
class ProfileHeader(
    private val binding: DrawerProfileHeaderBinding,
    onOpen: () -> Unit,
) {
    private val observer: (AccountProfile?) -> Unit = { show(it) }

    init {
        binding.root.setOnClickListener { onOpen() }
    }

    fun start() = Profile.observe(observer)

    fun stop() = Profile.unobserve(observer)

    private fun show(profile: AccountProfile?) {
        binding.root.isVisible = profile != null
        if (profile == null) return

        binding.profileName.text = profile.displayName
        binding.profilePicture.showProfilePicture(profile.picture)
    }
}
