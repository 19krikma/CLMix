package com.clmix

import android.app.Activity
import android.app.Application
import android.content.pm.ActivityInfo
import android.os.Bundle

/**
 * Keeps every screen in landscape on a tablet (Context.isLargeScreen),
 * either way round with the device. Phones are left as each activity's
 * manifest entry has them.
 *
 * Set on each activity as it is created rather than in the manifest,
 * because the manifest cannot tell a tablet from a phone.
 *
 * Android 16 ignores orientation requests on large screens for apps
 * targeting API 36. The manifest's PROPERTY_COMPAT_ALLOW_RESTRICTED_RESIZABILITY
 * opts out of that for now; once the app targets API 37, where the
 * opt-out is gone, the OS will rotate tablets freely whatever this asks,
 * and the tablet layouts' portrait handling takes over again. The user
 * can also override it from the system's per-app aspect ratio settings.
 */
object TabletLandscape : Application.ActivityLifecycleCallbacks {
    override fun onActivityCreated(activity: Activity, savedInstanceState: Bundle?) {
        if (activity.isLargeScreen()) {
            activity.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE
        }
    }

    override fun onActivityStarted(activity: Activity) {}
    override fun onActivityResumed(activity: Activity) {}
    override fun onActivityPaused(activity: Activity) {}
    override fun onActivityStopped(activity: Activity) {}
    override fun onActivitySaveInstanceState(activity: Activity, outState: Bundle) {}
    override fun onActivityDestroyed(activity: Activity) {}
}
