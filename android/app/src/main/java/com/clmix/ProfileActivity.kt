package com.clmix

import android.os.Bundle
import android.widget.Toast
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.isVisible
import androidx.core.widget.doAfterTextChanged
import com.clmix.databinding.ActivityProfileBinding
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import kotlin.concurrent.thread

/**
 * This account's own profile: its picture, first and last name,
 * username and password, all editable from the phone. Opened from the
 * header at the top of the side menu.
 *
 * Changing the username or password asks for the current password too;
 * the server checks it (RemoteServer._handle_profile).
 */
class ProfileActivity : AppCompatActivity(), MixerClientListener {
    private lateinit var binding: ActivityProfileBinding

    // The profile the form was last filled from, to tell what changed.
    private var shown: AccountProfile? = null
    private var saving = false

    private val pickPicture = registerForActivityResult(
        ActivityResultContracts.PickVisualMedia()
    ) { uri ->
        if (uri == null) return@registerForActivityResult
        setStatus("Uploading photo…", error = false)

        // Off the main thread: decoding a full-size photo takes a moment.
        thread {
            val encoded = try {
                Profile.encodePicture(contentResolver, uri)
            } catch (e: Exception) {
                null
            }
            runOnUiThread {
                if (encoded == null) {
                    setStatus("Couldn't read that photo", error = true)
                } else {
                    MixerClient.setProfilePicture(encoded)
                }
            }
        }
    }

    private val observer: (AccountProfile?) -> Unit = { fill(it) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        binding = ActivityProfileBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val bar = binding.topBar
        val barPadding = intArrayOf(bar.paddingLeft, bar.paddingTop, bar.paddingRight)
        val content = binding.content
        val contentPadding = intArrayOf(content.paddingLeft, content.paddingRight, content.paddingBottom)
        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { _, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            val ime = insets.getInsets(WindowInsetsCompat.Type.ime())
            bar.setPadding(
                barPadding[0] + bars.left, barPadding[1] + bars.top,
                barPadding[2] + bars.right, bar.paddingBottom
            )
            content.setPadding(
                contentPadding[0] + bars.left, content.paddingTop,
                contentPadding[1] + bars.right,
                contentPadding[2] + maxOf(bars.bottom, ime.bottom)
            )
            insets
        }

        binding.closeButton.setOnClickListener { finish() }
        binding.picture.setOnClickListener { choosePicture() }
        binding.changePictureButton.setOnClickListener { choosePicture() }
        binding.removePictureButton.setOnClickListener { confirmRemovePicture() }
        binding.saveButton.setOnClickListener { save() }

        listOf(
            binding.firstNameInput, binding.lastNameInput, binding.usernameInput,
            binding.newPasswordInput, binding.confirmPasswordInput,
            binding.currentPasswordInput,
        ).forEach { it.doAfterTextChanged { onEdited() } }
    }

    override fun onResume() {
        super.onResume()

        if (!MixerClient.isConnected || !Profile.supported) {
            finish()
            return
        }

        MixerClient.claimListener(this)
        Profile.onResult = ::onResult
        Profile.observe(observer)
        MixerClient.requestProfile()
    }

    override fun onPause() {
        super.onPause()
        Profile.onResult = null
        Profile.unobserve(observer)
        MixerClient.releaseListener(this)
    }

    override fun onDisconnected() {
        finish()
    }

    override fun onError(message: String) {
        setStatus(message, error = true)
        saving = false
        onEdited()
    }

    private fun fill(profile: AccountProfile?) {
        if (profile == null) return
        val previous = shown
        shown = profile

        binding.picture.showProfilePicture(profile.picture)
        binding.removePictureButton.isVisible = profile.picture != null

        // Text fields are refilled only when what the server holds
        // changed, so a picture arriving doesn't wipe a half-typed name.
        if (previous == null || previous.firstName != profile.firstName) {
            binding.firstNameInput.setText(profile.firstName)
        }
        if (previous == null || previous.lastName != profile.lastName) {
            binding.lastNameInput.setText(profile.lastName)
        }
        if (previous == null || previous.username != profile.username) {
            binding.usernameInput.setText(profile.username)
        }
        onEdited()
    }

    private fun text(input: com.google.android.material.textfield.TextInputEditText) =
        input.text?.toString() ?: ""

    private fun usernameChanged() =
        shown != null && text(binding.usernameInput).trim() != shown!!.username

    private fun passwordChanged() = text(binding.newPasswordInput).isNotEmpty()

    private fun hasChanges(): Boolean {
        val profile = shown ?: return false
        return text(binding.firstNameInput).trim() != profile.firstName ||
            text(binding.lastNameInput).trim() != profile.lastName ||
            usernameChanged() || passwordChanged()
    }

    private fun onEdited() {
        val needsPassword = usernameChanged() || passwordChanged()
        binding.confirmPasswordLayout.isVisible = passwordChanged()
        binding.currentPasswordLayout.isVisible = needsPassword
        binding.usernameLayout.error = null
        binding.confirmPasswordLayout.error = null
        binding.currentPasswordLayout.error = null
        binding.saveButton.isEnabled = !saving && hasChanges()
    }

    private fun save() {
        val profile = shown ?: return
        val username = text(binding.usernameInput).trim()
        val newPassword = text(binding.newPasswordInput)
        val current = text(binding.currentPasswordInput)

        if (username.isEmpty()) {
            binding.usernameLayout.error = "Username can't be empty"
            return
        }
        if (passwordChanged() && newPassword != text(binding.confirmPasswordInput)) {
            binding.confirmPasswordLayout.error = "Passwords don't match"
            return
        }
        if ((usernameChanged() || passwordChanged()) && current.isEmpty()) {
            binding.currentPasswordLayout.error = "Enter your current password"
            return
        }

        saving = true
        binding.saveButton.isEnabled = false
        setStatus("Saving…", error = false)

        MixerClient.updateProfile(
            firstName = text(binding.firstNameInput).trim(),
            lastName = text(binding.lastNameInput).trim(),
            username = username.takeIf { it != profile.username },
            newPassword = newPassword.takeIf { it.isNotEmpty() },
            currentPassword = current.takeIf { it.isNotEmpty() },
        )
    }

    private fun onResult(ok: Boolean, message: String?) {
        saving = false

        if (ok) {
            binding.newPasswordInput.setText("")
            binding.confirmPasswordInput.setText("")
            binding.currentPasswordInput.setText("")
            binding.statusLabel.isVisible = false
            Toast.makeText(this, "Profile saved", Toast.LENGTH_SHORT).show()
        } else {
            val text = message ?: "Couldn't save the profile"
            if (text.contains("password", ignoreCase = true) && text.contains("current", ignoreCase = true)) {
                binding.currentPasswordLayout.error = text
                binding.statusLabel.isVisible = false
            } else {
                setStatus(text, error = true)
            }
        }
        binding.saveButton.isEnabled = hasChanges()
    }

    private fun choosePicture() {
        pickPicture.launch(
            PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly)
        )
    }

    private fun confirmRemovePicture() {
        MaterialAlertDialogBuilder(this)
            .setTitle("Remove photo?")
            .setNegativeButton("Cancel", null)
            .setPositiveButton("Remove") { _, _ -> MixerClient.setProfilePicture(null) }
            .show()
    }

    private fun setStatus(text: String, error: Boolean) {
        binding.statusLabel.text = text
        binding.statusLabel.setTextColor(
            ContextCompat.getColor(this, if (error) R.color.sos else R.color.on_surface_variant)
        )
        binding.statusLabel.isVisible = true
    }
}
