package com.clmix

import android.content.Context
import android.content.res.Configuration
import android.os.Build
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.UUID
import java.util.concurrent.TimeUnit

/**
 * Anonymous feedback: bug reports, feature requests and the like, sent
 * from the Feedback screen to the clmix-feedback Cloudflare Worker, which
 * files them as GitHub issues (see feedback-worker/ at the repo root).
 *
 * This is the one place the app talks to anything other than the user's
 * own mixer server, so it works without that server: every submission is
 * written to a queue on the phone first, and sent from there - straight
 * away when there is internet, otherwise by [FeedbackSyncWorker] once
 * there is. Retries back off as 1 min, 5 min, 30 min, 2 h, then daily.
 *
 * Nothing identifies the person: the only id sent is [installId], a
 * random UUID made on first launch and used for nothing but grouping one
 * installation's reports and rate limiting. Diagnostics go only when the
 * user ticks the box, and the screen shows exactly what they are.
 */
object Feedback {
    val CATEGORIES = listOf("Bug Report", "Feature Request", "General Feedback", "Other")
    const val MIN_LENGTH = 10
    const val MAX_LENGTH = 5000

    private const val PREFS = "feedback"
    private const val KEY_INSTALL_ID = "install_id"
    private const val QUEUE_FILE = "feedback_queue.json"
    private const val WORK_NAME = "feedback-sync"

    private const val STATUS_PENDING = "Pending"
    private const val STATUS_SENDING = "Sending"

    private val RETRY_DELAYS_MS = longArrayOf(
        TimeUnit.MINUTES.toMillis(1),
        TimeUnit.MINUTES.toMillis(5),
        TimeUnit.MINUTES.toMillis(30),
        TimeUnit.HOURS.toMillis(2),
        TimeUnit.HOURS.toMillis(24),
    )

    // An item left in Sending this long was cut off mid-send (the app was
    // killed) and is retried. The Worker drops a repeat of an id it has
    // already filed, so a send that did land is not filed twice.
    private val STALE_SENDING_MS = TimeUnit.MINUTES.toMillis(2)

    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .writeTimeout(15, TimeUnit.SECONDS)
        .build()

    private val lock = Any()

    /** Whether this build knows where to send feedback. Without it,
     *  submissions wait in the queue until a build that does. */
    val configured: Boolean get() = BuildConfig.FEEDBACK_URL.isNotBlank()

    /** Called once from CLMixApplication: makes the install id on first
     *  launch, and sends anything left over from last time. */
    fun init(context: Context) {
        installId(context)
        scheduleSync(context)
    }

    fun installId(context: Context): String {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        prefs.getString(KEY_INSTALL_ID, null)?.let { return it }
        val id = UUID.randomUUID().toString()
        prefs.edit().putString(KEY_INSTALL_ID, id).apply()
        return id
    }

    /** What "Include diagnostics" adds, as shown to the user before they
     *  send it. Nothing in it tells one phone from another of the same
     *  model. */
    fun diagnostics(context: Context): String {
        val config = context.resources.configuration
        val orientation =
            if (config.orientation == Configuration.ORIENTATION_LANDSCAPE) "landscape" else "portrait"
        return listOf(
            "App: ${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})",
            "Android: ${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT})",
            "Device: ${Build.MANUFACTURER} ${Build.MODEL}",
            "Screen: ${config.screenWidthDp}x${config.screenHeightDp} dp, $orientation",
            "Connected to server: ${if (MixerClient.isConnected) "yes" else "no"}",
        ).joinToString("\n")
    }

    enum class Outcome { SENT, QUEUED, REJECTED }

    /**
     * Queues one submission and tries to send it now. Blocks on the
     * network, so call it off the main thread. Whatever happens it is
     * already safe in the queue, and anything not sent is scheduled.
     */
    fun submit(context: Context, category: String, message: String, includeDiagnostics: Boolean): Outcome {
        val item = JSONObject()
            .put("id", UUID.randomUUID().toString())
            .put("createdUtc", isoNow())
            .put("category", category)
            .put("message", message)
            .put("installId", installId(context))
            .put("version", BuildConfig.VERSION_NAME)
            .put("platform", "Android")
            .put("includeDiagnostics", includeDiagnostics)
            .put("diagnostics", if (includeDiagnostics) diagnostics(context) else JSONObject.NULL)
            .put("status", STATUS_PENDING)
            .put("retryCount", 0)
            .put("nextAttemptMs", 0L)
        synchronized(lock) {
            val queue = load(context)
            queue.put(item)
            save(context, queue)
        }

        val outcome = if (configured) attempt(context, item.getString("id")) else Outcome.QUEUED
        if (outcome == Outcome.QUEUED) scheduleSync(context)
        return outcome
    }

    /** Sends every queued item that is due. Returns how long until the
     *  next one is, or null when nothing is left to send. */
    internal fun syncDue(context: Context): Long? {
        if (!configured) return null
        val now = System.currentTimeMillis()
        val due = synchronized(lock) {
            val queue = load(context)
            (0 until queue.length()).map { queue.getJSONObject(it) }
                .filter { isWaiting(it, now) && it.optLong("nextAttemptMs") <= now }
                .map { it.getString("id") }
        }
        for (id in due) attempt(context, id)
        return nextDelay(context)
    }

    /** Puts the sync job on, for when the next item is due and there is a
     *  network. Replaces any job already waiting, so a new submission is
     *  not held up behind a long retry delay. */
    fun scheduleSync(context: Context) {
        val delay = nextDelay(context) ?: return
        enqueue(context, delay, ExistingWorkPolicy.REPLACE)
    }

    internal fun enqueue(context: Context, delayMs: Long, policy: ExistingWorkPolicy) {
        val request = OneTimeWorkRequestBuilder<FeedbackSyncWorker>()
            .setInitialDelay(delayMs, TimeUnit.MILLISECONDS)
            .setConstraints(
                Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()
            )
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(WORK_NAME, policy, request)
    }

    private fun nextDelay(context: Context): Long? {
        if (!configured) return null
        val now = System.currentTimeMillis()
        val next = synchronized(lock) {
            val queue = load(context)
            (0 until queue.length()).map { queue.getJSONObject(it) }
                .filter { isWaiting(it, now) }
                .minOfOrNull { it.optLong("nextAttemptMs") }
        } ?: return null
        return maxOf(0L, next - now)
    }

    private fun isWaiting(item: JSONObject, now: Long): Boolean = when (item.optString("status")) {
        STATUS_PENDING -> true
        STATUS_SENDING -> now - item.optLong("sendingSinceMs") > STALE_SENDING_MS
        else -> false
    }

    /** One try at one item: claims it, posts it, and settles the queue by
     *  what came back. */
    private fun attempt(context: Context, id: String): Outcome {
        val item = synchronized(lock) {
            val queue = load(context)
            val found = find(queue, id) ?: return Outcome.SENT
            if (!isWaiting(found, System.currentTimeMillis())) return Outcome.QUEUED
            found.put("status", STATUS_SENDING).put("sendingSinceMs", System.currentTimeMillis())
            save(context, queue)
            JSONObject(found.toString())
        }

        val code = try {
            post(item)
        } catch (e: IOException) {
            -1
        }

        synchronized(lock) {
            val queue = load(context)
            val found = find(queue, id) ?: return Outcome.SENT
            return when {
                // 409 is the Worker saying it already filed this id.
                code in 200..299 || code == 409 -> {
                    remove(queue, id)
                    save(context, queue)
                    Outcome.SENT
                }
                // Malformed or refused for good: retrying won't change it,
                // so it is dropped rather than kept on the phone forever.
                code in 400..499 && code != 408 && code != 429 -> {
                    remove(queue, id)
                    save(context, queue)
                    Outcome.REJECTED
                }
                else -> {
                    val retries = found.optInt("retryCount")
                    // Over the day's limit: no point trying before tomorrow.
                    val wait = if (code == 429) RETRY_DELAYS_MS.last()
                    else RETRY_DELAYS_MS[minOf(retries, RETRY_DELAYS_MS.size - 1)]
                    found.put("status", STATUS_PENDING)
                        .put("retryCount", retries + 1)
                        .put("nextAttemptMs", System.currentTimeMillis() + wait)
                        .put("lastError", if (code < 0) "network" else "HTTP $code")
                    save(context, queue)
                    Outcome.QUEUED
                }
            }
        }
    }

    private fun post(item: JSONObject): Int {
        val payload = JSONObject()
            .put("id", item.getString("id"))
            .put("category", item.getString("category"))
            .put("message", item.getString("message"))
            .put("version", item.getString("version"))
            .put("platform", item.getString("platform"))
            .put("installId", item.getString("installId"))
            .put("createdUtc", item.getString("createdUtc"))
            .put("includeDiagnostics", item.getBoolean("includeDiagnostics"))
            .put("diagnostics", item.opt("diagnostics") ?: JSONObject.NULL)
        val request = Request.Builder()
            .url(BuildConfig.FEEDBACK_URL)
            .post(payload.toString().toRequestBody("application/json".toMediaType()))
            .build()
        http.newCall(request).execute().use { return it.code }
    }

    private fun find(queue: JSONArray, id: String): JSONObject? =
        (0 until queue.length()).map { queue.getJSONObject(it) }.firstOrNull { it.optString("id") == id }

    private fun remove(queue: JSONArray, id: String) {
        for (i in 0 until queue.length()) {
            if (queue.getJSONObject(i).optString("id") == id) {
                queue.remove(i)
                return
            }
        }
    }

    private fun file(context: Context) = File(context.filesDir, QUEUE_FILE)

    private fun load(context: Context): JSONArray = try {
        val f = file(context)
        if (f.exists()) JSONArray(f.readText()) else JSONArray()
    } catch (e: Exception) {
        JSONArray()
    }

    // Written to a temp file and renamed over the old one, so the app
    // dying mid-write can't leave a half-written queue behind.
    private fun save(context: Context, queue: JSONArray) {
        val f = file(context)
        val tmp = File(f.parentFile, "$QUEUE_FILE.tmp")
        tmp.writeText(queue.toString())
        if (!tmp.renameTo(f)) {
            f.writeText(queue.toString())
            tmp.delete()
        }
    }

    private fun isoNow(): String =
        SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US)
            .apply { timeZone = TimeZone.getTimeZone("UTC") }
            .format(Date())
}

/** Sends the feedback queue when there is a network, and puts itself
 *  back on for the next retry while anything is left. */
class FeedbackSyncWorker(context: Context, params: WorkerParameters) :
    CoroutineWorker(context, params) {

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val next = Feedback.syncDue(applicationContext)
        // APPEND_OR_REPLACE, because REPLACE from inside the running job
        // would cancel this very job.
        if (next != null) {
            Feedback.enqueue(applicationContext, next, ExistingWorkPolicy.APPEND_OR_REPLACE)
        }
        Result.success()
    }
}
