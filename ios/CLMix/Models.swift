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
    let muted: Bool

    var id: Int { channel }
}
