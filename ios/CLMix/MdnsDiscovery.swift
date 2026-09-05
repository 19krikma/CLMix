import Foundation

struct DiscoveredServer: Identifiable, Equatable {
    let id: String
    let host: String
    let port: Int
}

@MainActor
protocol MdnsDiscoveryDelegate: AnyObject {
    func mdnsDidFindServer(_ server: DiscoveredServer)
    func mdnsDidLoseServer(id: String)
}

/// Finds CLMix desktop servers advertising themselves via Bonjour/mDNS on
/// the local network (see RemoteServer._advertise_mdns / MDNS_SERVICE_TYPE
/// in services/remote_server.py) - lets ConnectView offer a tap-to-fill
/// list instead of the user typing in an IP and port.
///
/// Uses the classic NetServiceBrowser/NetService API rather than
/// Network.framework's NWBrowser - it resolves straight to a host/port
/// pair, where NWBrowser's Bonjour endpoints need an actual connection
/// opened before the OS will hand back a resolved address.
final class MdnsDiscovery: NSObject {
    private let serviceType = "_clmix._tcp."
    private let browser = NetServiceBrowser()

    // Keyed by NetService.name (the mDNS instance name, e.g. "CLMix on
    // Marks-Laptop") - resolving services must be kept alive here or
    // NetService cancels the in-flight resolve.
    private var services: [String: NetService] = [:]
    private weak var delegate: MdnsDiscoveryDelegate?

    func start(delegate: MdnsDiscoveryDelegate) {
        stop()
        self.delegate = delegate
        browser.delegate = self
        browser.searchForServices(ofType: serviceType, inDomain: "local.")
    }

    func stop() {
        browser.stop()
        services.values.forEach { $0.stop() }
        services.removeAll()
        delegate = nil
    }
}

// NetServiceBrowser/NetService deliver delegate callbacks on whatever run
// loop they were scheduled on, which in practice is the calling thread at
// start()/resolve() time - but that's an implementation detail, not a
// guarantee, so every callback hops to the main actor explicitly (matching
// how MixerClient.swift handles its own delegate callbacks) since the
// delegate is AppModel, a @MainActor type.
extension MdnsDiscovery: NetServiceBrowserDelegate {
    func netServiceBrowser(
        _ browser: NetServiceBrowser, didFind service: NetService, moreComing: Bool
    ) {
        services[service.name] = service
        service.delegate = self
        service.resolve(withTimeout: 5)
    }

    func netServiceBrowser(
        _ browser: NetServiceBrowser, didRemove service: NetService, moreComing: Bool
    ) {
        services.removeValue(forKey: service.name)
        let id = service.name
        Task { @MainActor in self.delegate?.mdnsDidLoseServer(id: id) }
    }
}

extension MdnsDiscovery: NetServiceDelegate {
    func netServiceDidResolveAddress(_ sender: NetService) {
        guard let hostName = sender.hostName else { return }

        // The system resolver handles ".local" mDNS hostnames
        // transparently, so the raw name (minus Bonjour's trailing root
        // dot) works directly as a URL host - no need to pick apart the
        // numeric addresses in `sender.addresses`.
        let host = hostName.hasSuffix(".") ? String(hostName.dropLast()) : hostName
        let server = DiscoveredServer(id: sender.name, host: host, port: sender.port)

        Task { @MainActor in self.delegate?.mdnsDidFindServer(server) }
    }

    func netService(_ sender: NetService, didNotResolve errorDict: [String: NSNumber]) {
        // Best-effort discovery convenience - the manual host/port fields
        // stay usable either way, so a resolve failure is just dropped
        // rather than surfaced anywhere.
    }
}
