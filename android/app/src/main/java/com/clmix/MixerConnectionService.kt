package com.clmix

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat

// Exists purely so the OS treats this process as actively running (not
// subject to Doze/App Standby network throttling and background-execution
// freezing) for as long as MixerClient wants a live websocket - without
// this, the OS can suspend the app's network I/O within seconds of it
// leaving the foreground, OkHttp's ping/pong (MixerClient's 15s
// pingInterval) fails, and that read as a dropped connection - which is
// what was forcing a fresh login every time the user switched apps.
//
// Holds no state of its own and isn't bound to - MixerClient starts/stops
// it alongside the socket it's actually keeping alive (connect()/
// disconnect()).
class MixerConnectionService : Service() {
    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForeground(NOTIFICATION_ID, buildNotification())
        return START_NOT_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun buildNotification(): Notification =
        NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(getString(R.string.app_name))
            .setContentText("Connected to mixer server")
            .setSmallIcon(R.drawable.ic_menu)
            .setOngoing(true)
            .setSilent(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return

        val manager = getSystemService(NotificationManager::class.java)
        val channel = NotificationChannel(
            CHANNEL_ID, "Mixer connection", NotificationManager.IMPORTANCE_LOW
        ).apply {
            description = "Shown while CLMix is connected to a mixer server"
        }
        manager.createNotificationChannel(channel)
    }

    companion object {
        private const val CHANNEL_ID = "mixer_connection"
        private const val NOTIFICATION_ID = 1

        fun start(context: Context) {
            val intent = Intent(context, MixerConnectionService::class.java)
            ContextCompat.startForegroundService(context, intent)
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, MixerConnectionService::class.java))
        }
    }
}
