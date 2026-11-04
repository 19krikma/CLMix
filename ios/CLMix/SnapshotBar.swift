import SwiftUI

/// "Snapshot: NAME" along the very bottom of both mixer screens - which
/// show the console is on, so a recall nobody announced is noticed. Read
/// from AppModel.liveSnapshot, which every levels frame refreshes; empty
/// rather than gone until the first one arrives, so nothing shifts when
/// it does. Mirrors Android's snapshot_bar.xml.
struct SnapshotBar: View {
    let snapshot: String?

    static let height: CGFloat = 22

    var body: some View {
        Text(snapshot.map { "Snapshot: \($0)" } ?? "")
            .font(.system(size: 11))
            .foregroundStyle(Color.clmixOnSurfaceVariant)
            .lineLimit(1)
            .truncationMode(.tail)
            .padding(.horizontal, 12)
            .frame(maxWidth: .infinity)
            .frame(height: Self.height)
            .background(Color.clmixBackground)
    }
}
