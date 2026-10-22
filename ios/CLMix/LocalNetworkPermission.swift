import Foundation
import Network

/// Whether this app may use the local network - the one permission CLMix
/// needs, since the desktop server it talks to is always on the LAN.
///
/// iOS has no API that reports this permission (Apple's TN3179 says as
/// much), so it is found out by using the network instead: advertise a
/// throwaway Bonjour service and browse for it. Seeing our own service come
/// back means access is allowed; the browser waiting on a "policy denied"
/// DNS error means it was refused. Doing this also raises the system's
/// prompt if the user has never been asked, which is the "request" half of
/// the login screen's Permissions box.
///
/// The service type is CLMix's own and is listed in NSBonjourServices
/// (project.yml) - browsing a type that is not listed fails on its own,
/// whatever the permission says. It is not `_clmix._tcp`, so the probe never
/// shows up in a discovered-server list, on this phone or any other.
@MainActor
final class LocalNetworkPermission: ObservableObject {
    enum Status: Equatable {
        case unchecked
        case checking
        case granted
        case denied
        /// Neither answer came back in time - typically no Wi-Fi at all, or
        /// the system prompt still waiting on the user. Rechecked the next
        /// time the app comes back to the foreground, which is also what
        /// dismissing that prompt does.
        case inconclusive
    }

    @Published private(set) var status: Status = .unchecked

    private static let serviceType = "_clmix-check._tcp"

    // kDNSServiceErr_PolicyDenied from dns_sd.h, spelled out rather than
    // imported: it is the whole of what this needs from that header.
    nonisolated private static let policyDenied: DNSServiceErrorType = -65570

    private static let timeout: Duration = .seconds(15)

    private var listener: NWListener?
    private var browser: NWBrowser?
    private var timeoutTask: Task<Void, Never>?

    func check() {
        guard status != .checking else { return }
        status = .checking

        let listener: NWListener
        do {
            listener = try NWListener(using: .tcp)
        } catch {
            finish(.inconclusive)
            return
        }

        // Unique per check, so a second phone running the same probe on the
        // same network is never mistaken for our own answer.
        listener.service = NWListener.Service(
            name: UUID().uuidString, type: Self.serviceType
        )
        listener.newConnectionHandler = { $0.cancel() }

        let browser = NWBrowser(
            for: .bonjour(type: Self.serviceType, domain: nil), using: NWParameters()
        )
        browser.stateUpdateHandler = { [weak self] state in
            guard case .waiting(let error) = state,
                  case .dns(let code) = error,
                  code == Self.policyDenied else { return }
            Task { @MainActor in self?.finish(.denied) }
        }
        browser.browseResultsChangedHandler = { [weak self] results, _ in
            guard !results.isEmpty else { return }
            Task { @MainActor in self?.finish(.granted) }
        }

        self.listener = listener
        self.browser = browser
        listener.start(queue: .main)
        browser.start(queue: .main)

        timeoutTask = Task { [weak self] in
            try? await Task.sleep(for: Self.timeout)
            guard !Task.isCancelled else { return }
            self?.finish(.inconclusive)
        }
    }

    private func finish(_ result: Status) {
        guard status == .checking else { return }
        status = result

        timeoutTask?.cancel()
        timeoutTask = nil
        browser?.cancel()
        browser = nil
        listener?.cancel()
        listener = nil
    }
}
