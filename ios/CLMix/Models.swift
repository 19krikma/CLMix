import Foundation

struct AuxBus: Identifiable, Hashable {
    let index: Int
    let name: String

    var id: Int { index }
}

struct ChannelState: Identifiable, Hashable {
    let channel: Int
    let name: String
    let level: Double?
    let pan: Double?
    var muted: Bool

    var id: Int { channel }
}
