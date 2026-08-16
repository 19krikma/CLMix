package com.clmix

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.os.Handler
import android.os.Looper
import android.util.Log

data class DiscoveredServer(val name: String, val host: String, val port: Int)

interface MdnsDiscoveryListener {
    fun onServerFound(server: DiscoveredServer)
    fun onServerLost(name: String)
}

// Wraps NsdManager to find CLMix desktop servers advertising themselves via
// mDNS/DNS-SD on the local network (see RemoteServer._advertise_mdns /
// MDNS_SERVICE_TYPE in services/remote_server.py) - lets ConnectActivity
// offer a tap-to-fill list instead of the user typing in an IP and port.
class MdnsDiscovery(context: Context) {
    private val nsdManager =
        context.applicationContext.getSystemService(Context.NSD_SERVICE) as NsdManager
    private val mainHandler = Handler(Looper.getMainLooper())

    private var discoveryListener: NsdManager.DiscoveryListener? = null

    fun start(listener: MdnsDiscoveryListener) {
        stop()

        val newListener = object : NsdManager.DiscoveryListener {
            override fun onDiscoveryStarted(serviceType: String) {}

            // Only invoked for services matching what we asked
            // discoverServices() for below, so no need to re-check the
            // type here - Android's own NSD implementation is
            // inconsistent about whether it reports it back with a
            // trailing "local." across OS versions/OEMs anyway.
            override fun onServiceFound(service: NsdServiceInfo) {
                resolve(service, listener)
            }

            override fun onServiceLost(service: NsdServiceInfo) {
                mainHandler.post { listener.onServerLost(service.serviceName) }
            }

            override fun onDiscoveryStopped(serviceType: String) {}

            override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) {
                Log.w(TAG, "Start discovery failed: $errorCode")
            }

            override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) {
                Log.w(TAG, "Stop discovery failed: $errorCode")
                // The manager considers discovery over either way once
                // this fires - drop our reference so a later start()
                // doesn't try to stop a listener it already gave up on.
                discoveryListener = null
            }
        }

        discoveryListener = newListener
        nsdManager.discoverServices(SERVICE_TYPE, NsdManager.PROTOCOL_DNS_SD, newListener)
    }

    fun stop() {
        val listener = discoveryListener ?: return
        discoveryListener = null

        try {
            nsdManager.stopServiceDiscovery(listener)
        } catch (e: IllegalArgumentException) {
            // Already stopped (e.g. onStartDiscoveryFailed already fired) -
            // nothing left to tear down.
        }
    }

    private fun resolve(service: NsdServiceInfo, listener: MdnsDiscoveryListener) {
        nsdManager.resolveService(service, object : NsdManager.ResolveListener {
            override fun onResolveFailed(serviceInfo: NsdServiceInfo, errorCode: Int) {
                Log.w(TAG, "Resolve failed for ${serviceInfo.serviceName}: $errorCode")
            }

            override fun onServiceResolved(serviceInfo: NsdServiceInfo) {
                val host = serviceInfo.host?.hostAddress ?: return
                mainHandler.post {
                    listener.onServerFound(
                        DiscoveredServer(serviceInfo.serviceName, host, serviceInfo.port)
                    )
                }
            }
        })
    }

    companion object {
        private const val TAG = "MdnsDiscovery"
        private const val SERVICE_TYPE = "_clmix._tcp."
    }
}
