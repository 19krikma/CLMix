# App Store listing copy

The text that goes in App Store Connect for the iOS app, kept here so it
is versioned alongside the app rather than living only in a web form.

**Apple Guideline 2.3.10 forbids referring to other mobile platforms in
App Store metadata.** The first submission was rejected for naming Android
in the description. Nothing below mentions it, and nothing added later
should - not in the description, subtitle, keywords, promotional text,
What's New, or the screenshots. The Windows desktop app is deliberately
still named: it is a hard requirement for this app to do anything at all,
so describing it is accuracy, not an irrelevant third-party reference, and
review did not object to it.

**Apple Guideline 5.2.1 forbids naming third-party hardware without its
maker's authorization.** The September 2026 resubmission was rejected for
naming the console's brand and model in the description and keywords. The
console is now "a supported digital mixing console" below, and its brand
and model should not come back into any of the fields listed above, the
screenshots, or the App Review notes. The iOS app itself names no
third-party brand either, in its UI or in its source comments - keep it
that way. The desktop app, the site's front page and the desktop packaging
still name the console; none of those are the iOS app's, which has its own
support page (`docs/ios.html`, see Support and privacy URLs below).

---

## Subtitle (30 characters max)

```
Personal monitor mixing
```

## Promotional text (170 characters max)

```
Give every performer control of their own monitor mix from their phone - level, pan and per-send mute on their own aux bus, over your own Wi-Fi network.
```

## Keywords (100 characters max, comma-separated, no spaces)

```
monitor,mix,aux,wedge,iem,foh,soundcheck,console,mixer,stage,live,audio,osc
```

## Description

```
CLMix hands each performer their own monitor mix, on their own phone.

Level, pan and per-send mute for every input channel on the aux bus that person is assigned to - adjusted from where they are standing, over your own Wi-Fi network.


REQUIRES THE CLMIX DESKTOP APP

CLMix is a remote control, not a standalone mixer. It needs the free CLMix desktop application running on a Windows computer on the same network, connected to a supported digital mixing console. The desktop app mirrors the console's channel names, aux buses, banks and snapshots, and serves each phone only the parts that person is allowed to touch.


THEIR MIX, NOT THE MIX

The mute button is the console's per-send on/off, so muting a channel drops it out of that one wedge - never out of the room.


SCOPED ACCOUNTS

Each login is tied to one aux bus and one snapshot, or to all of either. Passwords never leave your own machine, where they are kept salted and hashed.


SNAPSHOT AWARE

Recall a different snapshot on the desk and accounts scoped to another one stop being able to move anything.


PRESETS

Save an aux mix under a name from the phone and pull it back later. Presets recall as ordinary fader moves, which the desk sees like any other.


FINDS YOUR SERVER

The app discovers the desktop app on your local network by itself, or takes its address and port by hand.


FINE MODE

A precision drag for small level changes, when a phone-sized fader is not enough travel.


STAYS LOGGED IN

A session token survives the app being closed and relaunched, and ages out on its own once it stops being used.


LIGHT AND DARK

Both themes, for the pit and for the daylight load-in alike.


YOUR NETWORK ONLY

There is no CLMix account and no cloud service. The app connects only to the server you run yourself, on your own network, and nowhere else. Nothing is collected, and nothing is sent to the developer.
```

## Support and privacy URLs

| Field | Value |
|---|---|
| Support URL | https://19krikma.github.io/CLMix/ios.html |
| Marketing URL | https://19krikma.github.io/CLMix/ios.html |
| Privacy Policy URL | https://19krikma.github.io/CLMix/privacy.html |

The privacy policy does name Android, because it is one document covering
every CLMix client and has to describe all of them to be truthful. That is
a linked legal document rather than App Store description copy, and is not
what 2.3.10 is aimed at. Leave it as it is unless review says otherwise.

It does not name the console's brand or model, though (5.2.1) - it has no
need to.

Support and Marketing URL both point at `docs/ios.html`, not the site's
front page: `docs/index.html` is the desktop app's page too and names the
console by brand and model, which 5.2.1 rules out for anything App Review
reads about this app. `ios.html` never names the console and never links
to the front page - keep both true.

## Reply to App Review - 2.1(b) and 5.2.1 (September 2026)

Sent from App Store Connect's App Review messages, after the metadata
changes above are saved. It deliberately does not name the console either.

```
Thank you for the review. Answers to both points below.

Guideline 2.1(b) - Information Needed

6. How do users obtain an account? Do users have to pay a fee to create an account?

No fee is charged, and nothing is sold anywhere in CLMix. The iOS app is free, with no in-app purchases, subscriptions, advertising, paid content or links to purchases outside the app. The CLMix desktop application it connects to is also free.

CLMix has no account system of its own and no server operated by us. Accounts exist only inside the user's own copy of the free CLMix desktop application: the person running it (typically the sound engineer) creates an account for each performer under Setup > Accounts, choosing its username, password and which mixes it may adjust, and hands those details to the performer. The account is stored and checked only on that computer, over the local network.

To review the app without an account or server, tap "Demo Mode" on the login screen.

Guideline 5.2.1 - Legal - Intellectual Property

We have removed the third-party brand and model name from the app's description and keywords, and the Support and Marketing URLs now point to a support page that does not mention it. The app itself contains no third-party names, logos, images or other content, and we have confirmed the screenshots contain none either.

The iOS app does not communicate with any third-party hardware or service. Its only network connection is to our own free CLMix desktop application on the user's local network, using CLMix's own protocol.
```

## Reply to App Review - 2.1 follow-up, seven business-model questions (September 2026)

App Review came back on 2.1 after the reply above, asking seven questions
about paid content. The honest answer to all seven is "there is none",
which is a weak-looking answer on its own - so the reply leads with *why*
there is nothing to describe, and then explains what an account actually
is, since a login screen asking for a username and password is the most
likely reason the question was asked at all.

Like the reply above it, this names neither the console's brand and model
(5.2.1) nor any other mobile platform (2.3.10). Keep both true if it is
edited.

App Store Connect caps a Resolution Center message at 4000 characters, and
counts a newline as CRLF - so the ceiling to check against is
`chars + lines`, not the character count alone. This reply is 3710
characters over 36 lines, i.e. 3746 by that measure. A fuller draft that
answered the same seven questions came to 4338 and would not send.

```
Thank you for the follow-up. Answers to all seven questions below, then the business model and the account system.

CLMix has no business model. Nothing is sold, licensed, subscribed to or unlocked anywhere in it, by us or anyone else, inside the app or outside it. It is a free remote control for audio mixing equipment the user already owns, and needs our free CLMix desktop application running on a Windows computer on the same local network - a public GitHub Releases download with no registration, licence or payment. To address the underlying concern directly: nothing is purchased outside of in-app purchase, because nothing is purchased at all.

1. Does your app access any paid content/services/package?
No. None of any kind.

2. What are the paid content/services/package?
There are none. No content library, media, catalog, premium tier or locked features. Every capability is present for every user from first launch.

3. Who are the users that will use the paid content/service/package?
Not applicable. For context on who uses the app at all: musicians on stage, adjusting their own monitor mix - what they hear from their wedge speakers or in-ear monitors.

4. If no, does a company or organization pay for the content/service/package?
No. No company or organization pays anything, to us or to anyone else. No site licence, enterprise agreement, per-seat fee or volume purchase.

5. Where do they pay, and what's the payment method?
Nowhere, and there is none. No checkout, billing, licence key, activation code, trial period or donation link anywhere in the app, the desktop application, or our website.

6. What specific types of previously purchased content can a user access in the app?
None. Nothing a user sees was purchased, from us or anyone else. The app displays only the live state of audio equipment the user already owns and is connected to on their own network: channel names, aux bus names, fader levels, pan positions and meter readings, read in real time. None of it is content we supply, host or sell, and none exists before they connect their equipment.

7. What paid content, subscriptions, or features are unlocked within your app that do not use in-app purchase?
None. No feature is gated, unlocked or extended by any payment or entitlement, by in-app purchase or otherwise. The app is complete and fully functional as downloaded, free, for everyone.

WHAT AN ACCOUNT IS

The login screen asks for a username and password, which may be what prompted these questions.

An account is not a purchase, subscription or entitlement. It carries no billing relationship and grants access to no content. It is a permission setting saying which of the owner's own mixes a performer may adjust, and whether they may use presets and mute - safety boundaries so one performer cannot alter another's mix, not tiers of a paid product.

There is no CLMix account, no sign-up and no server operated by us, and we hold no user records. Accounts are created by the person running the desktop application - normally the sound engineer - under Setup > Accounts. They choose the username and password and hand them to the performer. Each account is stored and checked only on that computer, over their own local network, and never reaches us. Creating them is free and unlimited, and an account cannot be bought, sold or transferred.

REVIEWING WITHOUT EQUIPMENT

A reviewer has no console or Windows computer to point the app at. Tap "Demo Mode" on the login screen: it needs no server address, account or credentials, and runs the entire app - aux selection, channel grid, faders, meters, pan, mute and presets - against a simulated console built in. Every feature is reachable this way, free.
```

### Reference - the facts behind that reply

Kept here so a future round of this conversation doesn't have to re-derive
them from the source.

| Question | Answer | Where it lives |
|---|---|---|
| Is anything sold? | No. No payment code, licence check, trial, activation or donation link exists anywhere in the repository. | verified by search across `*.py`, `*.swift`, `*.kt`, `*.md`, `*.html` |
| Is the desktop app free? | Yes, a public GitHub Releases download with no registration. | `docs/ios.html`, `docs/index.html` |
| Where do accounts live? | `~/.clmix_users.json` on the machine running the desktop app - never transmitted anywhere. | `services/user_store.USERS_PATH` |
| How are passwords stored? | Per-account 16-byte random salt, SHA-256 of salt+password. The password itself is never stored, and the phone never writes it to disk. | `services/user_store.UserStore._hash`, `ios/CLMix/SessionStore.swift` |
| What does an account scope? | One snapshot (or all), a list of aux buses (or all), plus `presets` and `mute` capability flags. | `services/user_store.UserStore` docstring |
| Who creates them? | The desktop operator, under Setup > Accounts. No self-service sign-up exists. | `ui/main_window.py` |
| What does the phone store? | A server-issued session token in the Keychain, plus host/port/username and the dark-mode choice in UserDefaults. No password. | `ios/CLMix/SessionStore.swift`, `ios/CLMix/PrivacyInfo.xcprivacy` |

The one thing in the app that could be mistaken for purchased content is
the channel and aux names in the grid. They are read live off the user's
own console over their own network and do not exist until they connect it -
worth saying explicitly if review asks again, which is why question 6's
answer says so.
