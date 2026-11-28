import Foundation
import Network
import UIKit

/// Anonymous feedback: bug reports, feature requests and the like, sent
/// from FeedbackView to the clmix Cloudflare Worker, which files them as
/// GitHub issues (see feedback-worker/ at the repo root). Mirrors
/// Android's Feedback.kt.
///
/// This is the one place the app talks to anything other than the user's
/// own mixer server, so it works without that server: every submission is
/// written to a queue on the phone first and sent from there - straight
/// away when there is internet, otherwise when the network comes back, at
/// the next launch, or on the retry timer while the app is open. Retries
/// back off as 1 min, 5 min, 30 min, 2 h, then daily.
///
/// Nothing identifies the person: the only id sent is `installId`, a
/// random UUID made on first launch and used only to group one
/// installation's reports and for rate limiting. Diagnostics go only when
/// the user turns them on, and the screen shows exactly what they are.
enum Feedback {
    static let categories = ["Bug Report", "Feature Request", "General Feedback", "Other"]
    static let minLength = 10
    static let maxLength = 5000
    static let endpoint = URL(string: "https://clmix.19krikma.workers.dev/api/feedback")!

    private static let installIdKey = "feedbackInstallId"

    static var installId: String {
        if let id = UserDefaults.standard.string(forKey: installIdKey) { return id }
        let id = UUID().uuidString.lowercased()
        UserDefaults.standard.set(id, forKey: installIdKey)
        return id
    }

    static var appVersion: String {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0"
    }

    /// What "Include diagnostics" adds, as shown to the user before they
    /// send it. Nothing in it tells one phone from another of the same
    /// model.
    @MainActor
    static func diagnostics(connected: Bool) -> String {
        let build = Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "?"
        let device = UIDevice.current
        var lines = [
            "App: \(appVersion) (\(build))",
            "\(device.systemName): \(device.systemVersion)",
            "Device: \(modelIdentifier())",
        ]
        if let scene = UIApplication.shared.connectedScenes.compactMap({ $0 as? UIWindowScene }).first {
            let size = scene.screen.bounds.size
            let orientation = size.width > size.height ? "landscape" : "portrait"
            lines.append("Screen: \(Int(size.width))x\(Int(size.height)) pt, \(orientation)")
        }
        lines.append("Connected to server: \(connected ? "yes" : "no")")
        return lines.joined(separator: "\n")
    }

    /// The hardware model, e.g. "iPhone16,2" - the same for every phone
    /// of that model.
    private static func modelIdentifier() -> String {
        var info = utsname()
        uname(&info)
        return withUnsafeBytes(of: &info.machine) { raw in
            String(decoding: raw.prefix(while: { $0 != 0 }), as: UTF8.self)
        }
    }
}

enum FeedbackOutcome {
    case sent
    case queued
    case rejected
}

struct FeedbackItem: Codable {
    var id: String
    var createdUtc: String
    var category: String
    var message: String
    var installId: String
    var version: String
    var platform: String
    var includeDiagnostics: Bool
    var diagnostics: String?
    var status: String
    var retryCount: Int
    var nextAttempt: Date
    var sendingSince: Date?
    var lastError: String?
}

/// The queue on the phone, and the sending of it.
actor FeedbackQueue {
    static let shared = FeedbackQueue()

    private static let retryDelays: [TimeInterval] = [60, 5 * 60, 30 * 60, 2 * 60 * 60, 24 * 60 * 60]
    // An item left in Sending this long was cut off mid-send (the app was
    // killed) and is retried. The Worker drops a repeat of an id it has
    // already filed, so a send that did land is not filed twice.
    private static let staleSending: TimeInterval = 120

    private var items: [FeedbackItem]
    private let fileURL: URL
    private let monitor = NWPathMonitor()
    private var timer: Task<Void, Never>?
    private var started = false

    init() {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        fileURL = dir.appendingPathComponent("feedback_queue.json")
        if let data = try? Data(contentsOf: fileURL),
           let decoded = try? JSONDecoder().decode([FeedbackItem].self, from: data) {
            items = decoded
        } else {
            items = []
        }
    }

    /// Called at launch and whenever the app comes back to the front:
    /// makes the install id on first launch, and sends anything waiting.
    func start() async {
        if !started {
            started = true
            _ = Feedback.installId
            // The network coming back is the moment to retry whatever
            // failed for want of it.
            monitor.pathUpdateHandler = { path in
                guard path.status == .satisfied else { return }
                Task { await FeedbackQueue.shared.syncDue(networkBack: true) }
            }
            monitor.start(queue: DispatchQueue(label: "com.clmix.feedback.network"))
        }
        await syncDue()
    }

    /// Queues one submission and tries to send it now. Whatever happens
    /// it is already safe in the queue, and anything not sent is retried.
    func submit(category: String, message: String, diagnostics: String?) async -> FeedbackOutcome {
        let item = FeedbackItem(
            id: UUID().uuidString.lowercased(),
            createdUtc: ISO8601DateFormatter().string(from: Date()),
            category: category,
            message: message,
            installId: Feedback.installId,
            version: Feedback.appVersion,
            platform: "iOS",
            includeDiagnostics: diagnostics != nil,
            diagnostics: diagnostics,
            status: "Pending",
            retryCount: 0,
            nextAttempt: Date(),
            sendingSince: nil,
            lastError: nil
        )
        items.append(item)
        save()
        let outcome = await attempt(item.id)
        scheduleTimer()
        return outcome
    }

    /// Sends every item that is due - and, when the network has just come
    /// back, every item that last failed for want of it.
    func syncDue(networkBack: Bool = false) async {
        let now = Date()
        let due = items.filter { item in
            isWaiting(item, now: now) &&
                (item.nextAttempt <= now || (networkBack && item.lastError == "network"))
        }.map(\.id)
        for id in due {
            _ = await attempt(id)
        }
        scheduleTimer()
    }

    private func isWaiting(_ item: FeedbackItem, now: Date) -> Bool {
        switch item.status {
        case "Pending":
            return true
        case "Sending":
            return now.timeIntervalSince(item.sendingSince ?? .distantPast) > Self.staleSending
        default:
            return false
        }
    }

    /// One try at one item: claims it, posts it, and settles the queue by
    /// what came back.
    private func attempt(_ id: String) async -> FeedbackOutcome {
        guard let index = items.firstIndex(where: { $0.id == id }) else { return .sent }
        guard isWaiting(items[index], now: Date()) else { return .queued }
        items[index].status = "Sending"
        items[index].sendingSince = Date()
        save()

        let code = await post(items[index])

        // Looked up again: the queue may have changed during the send.
        guard let i = items.firstIndex(where: { $0.id == id }) else { return .sent }
        // 409 is the Worker saying it already filed this id.
        if (200..<300).contains(code) || code == 409 {
            items.remove(at: i)
            save()
            return .sent
        }
        // Malformed or refused for good: retrying won't change it, so it
        // is dropped rather than kept on the phone forever.
        if (400..<500).contains(code) && code != 408 && code != 429 {
            items.remove(at: i)
            save()
            return .rejected
        }
        let retries = items[i].retryCount
        // Over the day's limit: no point trying before tomorrow.
        let wait = code == 429
            ? Self.retryDelays[Self.retryDelays.count - 1]
            : Self.retryDelays[min(retries, Self.retryDelays.count - 1)]
        items[i].status = "Pending"
        items[i].sendingSince = nil
        items[i].retryCount = retries + 1
        items[i].nextAttempt = Date().addingTimeInterval(wait)
        items[i].lastError = code < 0 ? "network" : "HTTP \(code)"
        save()
        return .queued
    }

    /// The HTTP status, or -1 when the Worker could not be reached.
    private nonisolated func post(_ item: FeedbackItem) async -> Int {
        let body: [String: Any] = [
            "id": item.id,
            "category": item.category,
            "message": item.message,
            "version": item.version,
            "platform": item.platform,
            "installId": item.installId,
            "createdUtc": item.createdUtc,
            "includeDiagnostics": item.includeDiagnostics,
            "diagnostics": item.diagnostics ?? NSNull(),
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: body) else { return 400 }
        var request = URLRequest(url: Feedback.endpoint, timeoutInterval: 15)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = data
        do {
            let (_, response) = try await URLSession.shared.data(for: request)
            return (response as? HTTPURLResponse)?.statusCode ?? -1
        } catch {
            return -1
        }
    }

    /// Wakes for the next retry while the app is open. A suspended app's
    /// timer stops too, so coming back to the front (start) and the
    /// network returning also send.
    private func scheduleTimer() {
        timer?.cancel()
        timer = nil
        let now = Date()
        guard let next = items.filter({ isWaiting($0, now: now) }).map(\.nextAttempt).min() else { return }
        let delay = max(1, next.timeIntervalSince(now))
        timer = Task {
            try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
            if Task.isCancelled { return }
            // A task of its own, so the next scheduleTimer cancelling this
            // one can't cut off a send in progress.
            Task { await FeedbackQueue.shared.syncDue() }
        }
    }

    private func save() {
        guard let data = try? JSONEncoder().encode(items) else { return }
        try? data.write(to: fileURL, options: .atomic)
    }
}
