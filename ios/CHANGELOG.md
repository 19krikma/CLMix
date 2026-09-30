# CLMix for iOS — Changelog

Versions here are this app's own (`MARKETING_VERSION` in
`ios/project.yml`), which moves independently of the desktop app's
`version.py`.

**Nothing in this file may name another mobile platform or the console's
brand and model.** Release notes are App Store metadata, and both are
ruled out there — see `ios/APP_STORE_LISTING.md` for which guidelines and
why. Write entries so they can be pasted straight into What's New.

## 1.2.0

Two corrections to the input stage, both from reading the console's own
parameter table rather than guessing at it.

### Polarity

- **Polarity no longer flattens a stereo channel.** Polarity is not an
  on/off switch on the console — a stereo channel has several inverted
  states, one per leg — and the app had been treating it as one. Tapping
  the button on such a channel wrote a different state than the one it was
  showing, quietly moving which leg was inverted. It now carries the
  console's own value, so switching polarity off and back on returns the
  channel to exactly where it was.

### Gain and Trim

- **Each dial sweeps its own range.** Gain and Trim shared one span,
  −40 to +60 dB, so that the two dials read alike. Gain does not go below
  −20 on the console, which left the bottom quarter of that dial doing
  nothing but snapping back; Trim stops at +40, which left the top doing
  the same. Gain now sweeps −20…+60 and Trim −40…+40, and every part of
  both dials reaches a value the console will keep.
- **The ranges come from the desktop app.** They arrive when the app logs
  in rather than being built in, so a correction to them ships with the
  desktop rather than waiting on a release here.

## 1.1.0

The console itself, for the accounts trusted with it: its own channel
faders rather than one performer's sends, the input stage behind each
channel, and a mute that reaches everywhere at once. Plus narrower
strips, so more of the desk fits on a phone.

### Full Mixer Control

- **A choice after login.** An account granted Full Mixer Control is
  asked which it wants — *AUX Only*, the mix-one-send screen that has
  always been there, or *Mixer Control*, the console's own faders. The
  two choices are deliberately the same size and weight: neither is a
  default, and picking the wrong one reaches either someone's wedge or
  the whole room. Every other account goes straight to the aux list as
  before and never sees this screen.
- **The console's faders, pans and mutes.** The same strips, riding the
  channel itself instead of a send. The bank panel and the Fine drag come
  along; the aux picker and presets do not, because both belong to
  riding one send.
- **The channel's number above its name**, the way the desk refers to a
  strip. It is also the way in to that channel's input stage.

### The input stage

- **Gain and trim on dials**, opened from the number or name at the top
  of a strip. Deliberately dials rather than sliders: a head amp is set
  in small deliberate steps, and a phone screen is far too short to put
  60 dB of range on. Hold a dial and lean sideways — a little to the
  right creeps, further spins — and it stops the moment the finger
  lifts, so a pocket or a stray brush cannot move a head amp.
- **One span for both**, rather than a range each, so gain and trim read
  alike and neither dial is short of a value its parameter really holds.
- **48V and polarity.** 48V wears the warning red rather than the app's
  accent: it is the one control on the sheet that can damage a source, so
  it should look like a live state rather than a selected option.
  Polarity cannot damage anything, only sound wrong, so it takes the
  accent.
- **Rename a channel** by holding its name. Every surface in the building
  sees the new name, which is why it is a hold rather than a tap.
- **Nothing is shown that has not been reported.** A channel the console
  has not answered for yet reads a dash and its dials grey out, rather
  than showing 0 dB as though that were the setting.

### Hard mute

- **A mute that leaves the monitors too.** Off on every fresh entry to
  the screen, because it reaches every wedge in the building.
- **A strip muted under it breathes** rather than sitting still — it has
  to register in peripheral vision across a row of strips without
  becoming the thing the eye keeps snapping back to during a show.

### Channel strips

- **Narrower strips**, so more of the console fits on screen at once —
  five full strips where three and a half fitted before. Most of that
  came out of dead space rather than out of the controls.
- **The Mute button reads MUTE in both states.** It names the button
  rather than reporting the state; colour carries that, which reads
  faster across a row of strips, and the label no longer changes width
  as it toggles.
- **The dB scale reads as a scale.** Labels below unity carry their sign
  (-5, -10 … -60, -∞) — the ruler used to run 10, 5, 0, 5, 10 downward,
  the same "5" appearing twice with nothing to say which was which. The
  numbers are right-aligned so they all end against their tick line, and
  the lines now run all the way to the fader they point at.
- **The meter sits against its fader** instead of across a gap of dead
  space.
- **Long channel names stack onto a second line** instead of running out
  of room. Both lines are reserved on every strip whether the name needs
  them or not, so one strip's fader is never shorter than its
  neighbours' along the row.

### Demo mode

Demo sessions carry Full Mixer Control too, so the choice after login,
the console strips and the input stage behind them are all reachable
without a server — including a hard mute that really does drop every
simulated send.

### Compatibility

Full Mixer Control needs a desktop app that offers it, and an account it
has been granted to. Against an older desktop the new screen is simply
never offered, and everything else here works as it always did.
