# Privacy Policy

**App:** CLMix
**Effective date:** October 11, 2026

CLMix ("the app", "we", "our") is a remote-control client for a digital
audio mixing console. This policy explains what information CLMix
accesses, collects, uses, and shares, across its Android, iOS, and Windows
desktop clients.

## Summary

Each installation of CLMix connects to a mixer control server that you (or
your organization) run yourselves, over your own local network. The
developer operates one server of its own: a feedback server that receives
the bug reports and feature requests you choose to send, and error logs
from the desktop app if you turn that on. Nothing reaches it unless you
send a report or switch on error log sharing.

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
machine and automatically deleted after 30 days. These log files are never
transmitted anywhere. Only individual errors are sent, and only if you turn
on error log sharing, described below.

**Bug reports and feature requests.** You can send the developer a bug
report or feature request from the desktop app's Feedback page, or from the
phone apps. A report contains the type, title and description you write,
and a name or email address only if you enter one for a reply. It is sent
along with the CLMix version, your server's operating system, and a random
install ID (see below). A report from a phone also includes the phone app's
version, the phone model and its operating system version. A phone does
not send the report to the developer itself: it goes to your own CLMix
server, which forwards it. If your server has no internet connection, it
keeps the report and sends it once a connection is available. Nothing is
sent unless you write a report and press Send.

**Error logs (desktop app only, off by default).** If you turn on "Send
error logs to the developer" on the desktop app's Feedback page, the
desktop app sends each error it logs to the developer's feedback server,
so problems can be found and fixed. Each error is sent with the 20
log lines before it, the CLMix version, the operating system and the
random install ID. Those log lines can include remote-access account
usernames and the network addresses of your mixer, server and connected
phones. Passwords and session tokens are never written to the log, so they
are never part of this. Errors from the phone apps are never collected.
Turning the setting off stops it immediately and deletes any errors not yet
sent.

**Install ID (desktop app only).** The desktop app generates a random ID the
first time it runs and sends it with reports and error logs, so the
developer can tell that several reports came from the same installation.
It is not derived from your device, your network or anything about you,
and is not used for anything else.

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

- Name, email address, or other personal identifiers, other than a name
  or email address you choose to enter in a report
- Location data (approximate or precise)
- Contacts, calendar, photos, videos, or files
- Microphone or camera data
- Financial, health, or fitness information
- Messages, browsing history, or installed-app lists
- Device identifiers (e.g. the iOS advertising identifier (IDFA) or
  identifier for vendor (IDFV), or the Android advertising ID or Android ID)
- Analytics or usage tracking of any kind
- Crash logs or diagnostics, other than the opt-in error logs described
  above

## Third Parties

CLMix does not use any analytics, advertising, or crash-reporting SDKs, and
does not share data with any third party. The Android and iOS apps make no
outside network requests whatsoever - they talk only to your own mixer
control server on your local network, and that includes the reports you
send from them. On the Windows desktop app, the only outside requests are
the anonymous GitHub version check, the update download if you ask for it,
and the reports and opt-in error logs sent to the developer's own feedback
server, all described above.

## Data Storage & Security

- Session tokens are protected at rest by the platform's own secure
  storage (Android: Android Keystore-backed encryption; iOS: the system
  Keychain).
- Remote-access account passwords are never stored in plain text; only a
  salted hash is kept, and only on the machine you designate as the server.
- All mixer-control traffic (login, fader/mute/pan commands, presets) stays
  on your local network between the client app and your own server.
- Reports and error logs are sent to the developer's feedback server over
  an encrypted (HTTPS) connection.

## Data Retention & Deletion

- Logging out of the app clears the locally stored session token.
- Uninstalling the app removes all locally stored app data, including the
  stored session token and your saved connection settings.
- An administrator can delete a remote-access account at any time from the
  desktop app's Access management screen.
- Desktop activity logs are automatically deleted after 30 days.
- Reports and error logs sent to the developer are kept only as long as
  they are needed to investigate and fix the problems they describe. To
  have a report deleted, email us its reference number (shown when it was
  sent) or describe it.

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
