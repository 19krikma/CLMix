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

### Fixed

- Channel strips measured against a stale height when the channel list
  resized, which pushed the Mute button off the bottom of the strip.

### Compatibility

Nothing here requires a newer desktop. Against an older one, meters
simply do not move and pan/mute stay visible as they always were — the
new fields default to the previous behaviour when absent.
