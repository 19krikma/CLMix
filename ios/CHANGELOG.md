# CLMix for iOS — Changelog

Versions here are this app's own (`MARKETING_VERSION` in
`ios/project.yml`), which moves independently of the desktop app's
`version.py`.

**Nothing in this file may name another mobile platform or the console's
brand and model.** Release notes are App Store metadata, and both are
ruled out there — see `ios/APP_STORE_LISTING.md` for which guidelines and
why. Write entries so they can be pasted straight into What's New.

## 1.9.0

- **Dynamics 1 shows its mode.** The heading reads COMPRESSOR, MULTIBAND
  or DE-ESSER, whichever the console is running.
- **Multiband controls.** LOW, MID and HIGH tabs, each with threshold,
  ratio, attack, release, gain and its own power button, and ALL for the
  overall threshold and gain and the two crossovers.
- **De-esser controls.** Threshold, ratio, attack, release, frequency and
  width. Frequency and width show the console's raw steps for now.
- **Power icon.** The section's IN button is now a power button.

## 1.8.1

- **Dynamics 1 has no gate.** Opened from a strip's COMP button, the
  Dynamics screen now shows Dynamics 1 alone, with no COMP/GATE switch
  and no gate on the graph. Opened from GATE it is unchanged.

## 1.8.0

- **Server Syncing.** When the CLMix server refreshes its information
  from the mixer, the app goes back to the AUX Only / Mixer Control
  choice (or to the aux list) and shows "Server Syncing" with a spinner
  until it is done. The aux list refreshes afterwards, so renamed auxes
  show their new names.

## 1.7.0

- **Permissions check.** A gear button beside the light/dark toggle on
  the login screen checks that CLMix can reach your local network. A
  green tick on it means everything is allowed; the first tap asks for
  access if it has never been asked for, and if it was turned down a tap
  opens CLMix's page in Settings, where the Local Network switch is.

## 1.6.0

- **Channel EQ.** Every Mixer Control strip shows its channel's EQ curve
  in miniature under its name - greyed while the EQ is out. Tapping it opens the channel's
  EQ full screen, turned on its side: a graph of the curve across
  20 Hz-20 kHz with a handle for each of the four bands and for the high-
  and low-pass filters. Drag a band's handle to move its frequency and
  gain, pinch to change its Q, or pick it from the row of chips and turn
  its Freq, Gain and Q dials. Each band and filter can be switched on and
  off, and EQ IN switches the whole EQ in or out - while it is out the
  curve stays drawn in grey, so you can see what switching it back in
  would do.
- **Channel dynamics.** Under the EQ, each Mixer Control strip shows its
  compressor's curve over its gate's in miniature, each drawn in colour
  while that section is in and greyed while it is out.
  Tapping one opens the channel's Dynamics full screen on its side: a
  graph of level in against level out with both thresholds as handles to
  drag, an IN button, and five dials per section - compressor threshold,
  ratio, attack, release and makeup gain; gate threshold, range, attack,
  hold and release. A section the console is running in another mode
  (multiband, de-esser, duck) is named on screen; the mode itself is
  changed on the desk.
- **The meter moved up beside the channel name** on Mixer Control, as a
  slim bar running from the channel number down to the foot of the name,
  leaving the fader row to the fader.
  The aux screens keep it beside the fader.
- **The input sheet has a meter.** Opened from a channel's number or
  name, the sheet now shows that channel's meter down its right-hand
  side, beside the gain and trim dials it reflects.
- **Hard mute is gone** from the Mixer Control menu. Mute on that screen
  is the channel's own mute, nothing more.

## 1.5.0

- **Only the live input can be adjusted.** On a channel's input sheet,
  the input that is not selected - Main or ALT - has its 48V, Gain and Trim
  greyed out and locked. Its button stays usable, since it is how to
  switch over.
- **Main is locked when nothing is patched to it**, the same way ALT
  already was.
- **Gain and Trim can be typed in.** Tap the number box beside a dial,
  type the value and press Done. It is clamped to the dial's range and
  reads back formatted - typing 0 shows 0.0.
- **Gain dials turn more slowly**, a quarter of their old speed, so a
  head amp can be set without overshooting.
- **ALT can be selected on an input sitting at 0 dB.** It stayed locked
  on such an input, and locked again after switching back to Main.

## 1.4.0

- **Custom Banks.** Group the desk's channels your own way. Banks start
  from the console's own and can be edited from the phone, or reset back
  to the desk's.
- **Light and dark themes.** A sun/moon button on the login screen sets
  the theme for the whole app, the login screen included.
- **A reworked login screen.** New wallpaper, a refresh button and scan
  spinner for discovered servers, and manual entry only when none are
  found.
- **Full-screen mixer.** The status bar hides and the home indicator
  fades away, giving the faders the whole screen.
- **Better landscape.** The controls move to the camera side and the
  strips use the full height of the screen.
- **Smoother scrolling.** A sideways swipe across the channels scrolls
  them instead of moving a fader.
- **Back from the aux mixer** to the AUX Only / Mixer Control choice,
  for accounts that have both.

## 1.3.0

- **Personalization.** Accounts with permission can rename channels on
  their own aux screens. The names are theirs alone and never change
  the console.
- **Landscape support** on every screen.
- **A faster interface**, and faders that are easier to grab.

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
