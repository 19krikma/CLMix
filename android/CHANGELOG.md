# CLMix Android — Changelog

Versions here are the phone app's own (`android/version.properties`), which
moves independently of the desktop app's `version.py`.

## 2.5.0

Everything the console already knew but the phone did not: how loud each
channel is, whether it is stereo, whether this aux can pan, and whether
this account may mute. Plus a rework of how banks and auxes are chosen.

### Metering

- **Channel meters.** A post-fader meter beside every fader, on the
  console's own 0..-60 dB scale — deliberately not the fader's -150..+10,
  which is why it sits beside the fader rather than against its ruler.
  Same gradient and same ballistics as the desktop: the bar is always
  falling and each arriving sample pushes it back up.
- **Stereo channels meter as two bars.** A stereo channel draws two
  narrow bars, a mono channel one wide one, inside the same total width —
  so a strip never changes size depending on the console's configuration.
  Which is which comes from the console's channel modes; the right-hand
  meter address answers on mono channels too, so it cannot be inferred
  from the meter data itself.

### Aux and channel awareness

- **Pan is hidden on a mono aux.** A mono bus sums its sends to one leg,
  so panning into it does nothing. The console accepts and echoes the
  write regardless, which is exactly why the server has to say so rather
  than the app discovering it by trying.
- **Mute is hidden without permission.** Accounts denied mute no longer
  see a button whose only possible outcome is an error.
- **The live aux is obvious.** Its name shows at the bottom of the mixer
  screen, and it is highlighted in the aux list.

### Choosing a bank

- **The bank dropdown is gone**, along with its "Bank" label. In its
  place, a chevron beside *Fine* pulls down a panel of bank buttons —
  the whole set reachable in one press, with the live one filled in the
  accent colour. It opens with a 100 ms slide rather than snapping.
- **No more "All".** The app opens on the console's first bank. Every
  channel at once is a whole console's worth of strips, faders and meters
  for a phone that can show a dozen. A console reporting no banks at all
  still falls back to showing everything, so no one is left with an empty
  screen.

### Choosing an aux

- **Moved out of the side drawer to the bottom**, within thumb reach.
  The bar naming the live mix *is* the collapsed picker: tap it, or drag
  it up, and the aux list rises over the strips — floating above them
  like the pan sheet, so the faders keep their positions and their
  height while choosing.
- **Arrows either side of the name**, grey rather than accent so the name
  stays what the eye lands on. They turn with the drag rather than
  snapping at the end, and point the way the sheet will travel.
- **Opens on the mix you are on.** With a console's worth of auxes the
  live one is usually below the fold, so the list scrolls to it as the
  sheet starts to move — one row down, so it arrives with context rather
  than jammed against the top edge.

### Login

- **Enter submits.** Enter already moved from username to password; on
  the password field it now logs in. Routed through the same guard as the
  button, so hammering it cannot start a second attempt while one is in
  flight. Hardware and Bluetooth keyboards work too, and are filtered to
  the key-down so a single press does not submit twice.

### Channel strips

- **Narrower strips**, 118dp down to 76dp, so more of the console fits on
  screen at once - five full strips on a phone where three and a half
  fitted before. Most of that came from dead space rather than from the
  controls: the fader's touch band is far wider than the track drawn down
  the middle of it, and the strip was sized around the band.
- **The Mute button reads MUTE in both states.** It names the button
  rather than reporting the state - colour carries that, which reads
  faster across a row of strips than four characters on each, and stops
  the label changing width as it toggles.
- **The dB scale reads as a scale.** Labels below unity carry their sign
  (-5, -10 ... -60, -∞) - the ruler used to run 10, 5, 0, 5, 10 downward,
  the same "5" appearing twice with nothing to say which was which. The
  numbers are right-aligned so they all end against their tick line, and
  the lines now run all the way to the fader they point at.
- **The meter sits against its fader** instead of across a gap of dead
  space - the fader's touch band is far wider than the track drawn down
  the middle of it, and that width was holding the meter at arm's length.
- **Long channel names stack onto a second line** instead of running out
  of room. Both lines are reserved on every strip whether the name needs
  them or not: the fader takes whatever height is left, so letting the
  name grow only when it wraps would leave one strip's fader shorter than
  its neighbours' along the row.

### Landscape

- **The bars fold away when the phone is on its side**, leaving the whole
  screen to the strips - turned sideways the faders are short, and the
  top and bottom bars were costing most of what travel there was.
- **A floating eye button** in the bottom-right corner brings them back,
  and hides them again. It floats over the strips rather than reserving a
  row of its own, which would give back the space hiding the bars just
  freed, and lifts clear of the aux bar whenever that is showing so it is
  never half-buried behind the thing it dismisses. The icon shows what
  pressing it will do rather than what is currently on screen.
- Portrait is unchanged and never shows the button.

### Connection lifetime

- **The app lets go when the server does.** A socket that ends on its own
  - the server stopped, or the phone walked out of range of it - now
  tears the connection down properly, foreground service and notification
  included. Previously only the UI was told, so leaving the building with
  the app open left it sitting in the notification shade, apparently
  connected, until something happened to touch it.
- **A screen resuming onto a dead socket returns to login** rather than
  showing a grid of faders attached to nothing. The connection usually
  ends while the app is in the background, where there is no screen
  listening to hear about it.
- The foreground service now starts when the socket opens rather than
  when a connection is attempted, so an unreachable server never raises
  a notification it has to immediately withdraw.

### Fixed

- **The keyboard no longer covers the password field.** The login screen
  scrolls the focused field clear when the keyboard opens. This screen
  draws edge-to-edge, so the window never resizes for the keyboard and
  the ScrollView considered a field sitting behind it perfectly visible -
  the overlap is now worked out against the keyboard's own height
  instead.

- Channel strips measured against a stale height when the channel list
  resized, which pushed the Mute button off the bottom of the strip.

### Compatibility

Nothing here requires a newer desktop. Against an older one, meters
simply do not move and pan/mute stay visible as they always were — the
new fields default to the previous behaviour when absent.
