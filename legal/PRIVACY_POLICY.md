# Privacy Policy

**App:** CLMix
**Effective date:** September 1, 2026

CLMix ("the app", "we", "our") is a remote-control client for a DiGiCo SD7
Quantum audio mixing console. This policy explains what information CLMix
accesses, collects, uses, and shares, across its Android, iOS, and Windows
desktop clients.

## Summary

CLMix has no backend server operated by the developer. Each installation
connects only to a mixer control server that you (or your organization) run
yourselves, over your own local network. We do not operate any server that
receives, stores, or processes your data.

## Information We Access or Collect

**Login credentials.** When you log in, the username and password you enter
are sent directly to your own mixer control server over your local network.
They are never sent to the developer or to any third party.

**Session token.** After login, the app stores a session token on your
device so you don't have to log in again each time. On Android this is
encrypted at rest using the Android Keystore; on iOS it is held in the
system Keychain, readable only by CLMix itself and only once the device has
been unlocked. This token never leaves your device except back to the same
server that issued it.

**Connection settings.** The app remembers the server address, port and
username you last used, together with your light/dark theme choice, so you
do not have to re-enter them. These stay on your device; the address and
username leave it only in the course of reaching and logging in to your own
server. Your password is never written to disk on any platform.

**Local network access.** The app uses local network discovery (mDNS /
Bonjour) solely to find your mixer control server on the local network. On
Android this relies on the device's Wi-Fi/network state; on iOS it requires
the system's Local Network permission, which iOS asks for the first time
the app looks for or connects to a server on your network, and without
which CLMix cannot reach your server. Discovery is limited to CLMix servers
advertising themselves on your own network, and is not used to track your
location or identity.

**Remote-access accounts (desktop app only).** If you use the Windows
desktop app to grant other users remote access, it stores each account's
username and a salted, hashed password locally on the machine running the
desktop app. This data stays on that machine and is never transmitted
elsewhere.

**Activity logs (desktop app only).** The desktop app keeps local log files
of connection and mixer activity for troubleshooting, stored on the same
machine and automatically deleted after 30 days. These logs are never
transmitted anywhere.

**Update check (desktop app only).** Shortly after startup, and again
whenever you press Check in the About window, the desktop app makes an
anonymous request to GitHub's public API to read the latest released
version number. This request contains no personal or account data and is
not tied to you or your device.

**Update download (desktop app only).** If you press Update in the About
window, the desktop app downloads that release's installer from GitHub and
runs it. This is an anonymous public download, started only by you, and it
sends nothing about you or your installation. Nothing is ever downloaded or
installed without you pressing that button.

## What We Do Not Collect

CLMix does not access, collect, or share:

- Name, email address, or other personal identifiers
- Location data (approximate or precise)
- Contacts, calendar, photos, videos, or files
- Microphone or camera data
- Financial, health, or fitness information
- Messages, browsing history, or installed-app lists
- Device identifiers (e.g. the iOS advertising identifier (IDFA) or
  identifier for vendor (IDFV), or the Android advertising ID or Android ID)
- Crash logs, diagnostics, or analytics of any kind

## Third Parties

CLMix does not use any analytics, advertising, or crash-reporting SDKs, and
does not share data with any third party. The Android and iOS apps make no
outside network requests whatsoever - they talk only to your own mixer
control server on your local network. On the Windows desktop app, the only
outside requests are the anonymous GitHub version check and, if you ask for
it, the update download described above.

## Data Storage & Security

- Session tokens are protected at rest by the platform's own secure
  storage (Android: Android Keystore-backed encryption; iOS: the system
  Keychain).
- Remote-access account passwords are never stored in plain text; only a
  salted hash is kept, and only on the machine you designate as the server.
- All mixer-control traffic (login, fader/mute/pan commands, presets) stays
  on your local network between the client app and your own server.

## Data Retention & Deletion

- Logging out of the app clears the locally stored session token.
- Uninstalling the app removes all locally stored app data, including the
  stored session token and your saved connection settings.
- An administrator can delete a remote-access account at any time from the
  desktop app's Access management screen.
- Desktop activity logs are automatically deleted after 30 days.

## Children's Privacy

CLMix is a professional audio-production tool not directed at children and
is not knowingly used to collect data from children.

## Changes to This Policy

If this policy changes, the updated version will be posted at this same
location with a revised effective date.

## Contact Us

Questions about this privacy policy or CLMix's data practices can be sent
to:

Mark Krikunov
markkrikunov2000@gmail.com
