# CLMix for iOS

A SwiftUI port of the Android remote-control app (`android/`), speaking
the same WebSocket JSON protocol to the desktop app's `RemoteServer`
(`services/remote_server.py`). Source-only - there is no `.xcodeproj`
here, since generating one without Xcode to verify it opens correctly is
too likely to produce a corrupt project file.

## Setting it up

1. In Xcode: **File > New > Project > iOS > App**, name it `CLMix`,
   interface **SwiftUI**, language **Swift**.
2. Delete the template's `ContentView.swift` and default `@main` app
   file.
3. Drag every `.swift` file in this folder into the project (checking
   "Copy items if needed" and adding to the `CLMix` target).
4. Add the App Transport Security exception below to `Info.plist` -
   without it, iOS blocks the plain `ws://` connection outright. This is
   the iOS equivalent of `android:usesCleartextTraffic="true"` in
   `android/app/src/main/AndroidManifest.xml`.

```xml
<key>NSAppTransportSecurity</key>
<dict>
    <key>NSAllowsLocalNetworking</key>
    <true/>
</dict>
```

   `NSAllowsLocalNetworking` only exempts private/local-network
   addresses (matching how this app is actually used - connecting to a
   desktop CLMix instance on the same LAN), rather than disabling ATS
   globally with `NSAllowsArbitraryLoads`.
5. If running on a physical device rather than the Simulator, add
   `NSLocalNetworkUsageDescription` and `NSBonjourServices` too - iOS
   14+ silently refuses to browse for a custom Bonjour service type
   (`MdnsDiscovery.swift`'s search for the desktop app, advertised as
   `_clmix._tcp.` by `RemoteServer._advertise_mdns` in
   `services/remote_server.py`) unless that type is declared up front,
   and prompts for local-network permission the first time the app
   either opens a socket to a LAN address or browses for services.

```xml
<key>NSLocalNetworkUsageDescription</key>
<string>CLMix finds and connects to the desktop mixer app on your local network.</string>
<key>NSBonjourServices</key>
<array>
    <string>_clmix._tcp.</string>
</array>
```

## Status

Every file here was written without an Xcode/macOS environment to
compile or run it against - unlike the Android app, none of this has
been built or tested. The networking and math layers (`MixerClient`,
`AuxTaper`, `PanFormat`, `Models`) are plain Foundation and should be
solid. The custom gesture-driven controls (`LevelFaderView`,
`PanSheetView`'s `CenteredPanSlider`) are the highest-risk area - SwiftUI
`DragGesture` geometry math is fiddly to get exactly right without
live testing, so expect to need some hands-on adjustment there.

## Structure

| File | Mirrors (Android) | Purpose |
|---|---|---|
| `Models.swift` | `Models.kt` | `AuxBus`, `ChannelState` |
| `AuxTaper.swift` | `AuxTaper.kt` | dB <-> fader-fraction taper math |
| `PanFormat.swift` | `PanFormat.kt` | Pan value <-> "C"/"L35"/"R20" labels |
| `MixerClient.swift` | `MixerClient.kt` | WebSocket client, JSON protocol |
| `SessionStore.swift` | `SessionStore.kt` | Keychain-backed session token, so a relaunch resumes instead of asking for the password again |
| `MdnsDiscovery.swift` | `MdnsDiscovery.kt` | Finds CLMix servers on the LAN via Bonjour/mDNS |
| `AppModel.swift` | `ConnectActivity`/`AuxListActivity`/`MixerActivity` | Navigation + mixer state |
| `ConnectView.swift` | `ConnectActivity` | Login screen |
| `AuxListView.swift` | `AuxListActivity` | Aux bus picker |
| `MixerView.swift` | `MixerActivity` | Bank picker, channel grid, aux switcher |
| `ChannelStripView.swift` | `ChannelAdapter.kt` | Per-channel fader/pan/mute |
| `LevelFaderView.swift` | `ChannelAdapter.kt`'s fine-drag handling | Vertical fader with Fine-mode precision drag |
| `LevelRulerView.swift` | `LevelRulerView.kt` | dB scale beside the fader, ticks packed tight near -infinity |
| `PanSheetView.swift` | `PanBottomSheet.kt` + `PanTrackDrawable.kt` | Full-width pan control, center-anchored fill |
| `PresetSaveSheet.swift` | `PresetSaveBottomSheet.kt` | Name a preset and capture the active aux's levels+pans |
| `PresetLoadSheet.swift` | `PresetLoadBottomSheet.kt` | Pick a saved preset and apply it to the active aux |
| `CLMixApp.swift` | n/a (Activities) | App entry point |
