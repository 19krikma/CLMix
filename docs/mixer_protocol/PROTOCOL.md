# Mixer OSC Protocol Reference

Reverse-engineered by live-probing the actual console at `10.5.20.242` on 2026-08-09 (send port 1091, recv port 1090 at that time), and extended on 2026-09-03 from live probing plus two packet captures of the **official DiGiCo client** talking to the same console (`digico.pcapng`, `sound sample.pcapng`), and again on 2026-09-20 from an eight-minute recording of the official app made with CLMix's own DiGiCo App Capture (`services/digico_bridge.py`) - the first capture taken with the app driven deliberately, opening panels and moving controls to see what each one asks for.

> **The send/recv ports are console configuration, not protocol constants.** They have been observed as 1091/1090, then 800/900, then 10025/10026 on this same console. Never hardcode them; treat them as user settings (which is what CLMix already does). The console is a **DiGiCo Q225 Quantum**. Its `/Console/Name` reply is `SD7Q-Q2`, which is a name string rather than the model - don't read the "SD7" in it as the console type.

This document was generated from **946 concrete addresses** the console actually replied with, collapsed into **358 generalized command patterns**. See `commands.csv` in this folder for the full flat list (every concrete address + the live value it held at probe time).

## Transport / wire protocol

- **OSC 1.0 over UDP.** App sends to `mixer_ip:send_port`; the console replies to the app's bound `recv_port` - matches [`ui/main_window.py`](../../ui/main_window.py)'s `MixerWorker`.
- **The console ignores your source port entirely.** It only ever transmits to the destination port configured in its own External Control panel. Querying from an ephemeral socket gets you silence no matter how correct the address is - you must bind the exact `recv_port` the console is configured to send to.
- **The console transmits from an ephemeral source port of its own** (observed 65510 and 65511). It is stable for the life of the console's OSC engine but changes across console restarts, so never filter incoming packets by source port. `MixerWorker` discards the source address in `recvfrom`, which is correct.
- **Beware privileged ports on Linux.** A `recv_port` below 1024 cannot be bound without root and fails with `PermissionError`. Prefer configuring the console to a port above 1024.
- **GET a value:** send the address with `/?` appended, empty arg list, e.g. `/Input_Channels/1/Channel_Input/name/?`. The console replies with the same address (no `/?`) and the current value(s) as OSC args.
- **GET a whole channel strip in one shot:** append `/?` directly to a bare index path with *no* leaf, e.g. `/Input_Channels/1/?` or `/Aux_Outputs/1/?` - the console dumps **every** parameter under that strip as a burst of individual reply messages (that's how this whole document was built: one query per category, not hundreds of guesses).
- **SET a value:** send the bare address (no `/?`) with the new value as the single OSC arg, e.g. `/Input_Channels/1/mute 1.0`. Confirmed live: the console only echoes the address back to listeners when the value actually *changes* - setting a parameter to its current value produces no reply, so don't rely on a SET always producing a confirmation message.
  Re-confirmed decisively 2026-09-20 watching the app drag an input gain: every step of the drag was echoed, and then the app went on sending `analog_gain 60.0` six more times after the control hit the top of its travel, for which the console said nothing at all. **The exception is strings.** Setting `/Input_Channels/5/Channel_Input/name` to the name it already held *was* echoed, twice over, on both attempts. So "no echo" is a property of unchanged numbers, not of unchanged values; code that waits for an echo to confirm a write must not assume one is coming.
- Args observed as OSC float32 for virtually everything, including boolean-style on/off flags (`0.0`/`1.0`) - only names/labels come back as OSC strings.
- Meter addresses (anything with `meter` in the name) reply with an **empty arg list** when queried directly with `/?`. They are not gettable that way - but they are fully readable through the meter subscription mechanism documented in [Metering](#metering) below.
- `/Talkback_Outputs/*` (2 exist per `/Console/Channels/?`) did not answer any address pattern tried (`/Talkback_Outputs/1/?`, `.../mute`, `.../name`, `.../fader`, `.../Buss_Trim/name`) - left undocumented.
- `/Snapshots/names/?` (used by the app to build its snapshot-name catalog, [`ui/main_window.py:206`](../../ui/main_window.py#L206)) **does reply** - re-confirmed 2026-09-03, when it returned one `/Snapshots/name` message per snapshot for all 16 snapshots. Its silence during the 2026-08-09 session was an anomaly, not the norm.
  Arg shape is 4 values: `[index, cue_number, 0, name]`, e.g. `[13, 450, 0, "NATALIYA FILISTOVICH"]`. Reading `args[0]` and `args[-1]` (what `_handle_snapshot_name` does) is correct.

## Console discovery (UDP broadcast beacon)

The console continuously broadcasts a **22-byte non-OSC beacon** to `255.255.255.255:2029`, from source port 2029, **every ~2.02 seconds**, forever - with no client connected and nothing having been sent to it. Listening for this is the only way to find a console without already knowing its IP.

Observed payload (identical in all 45 beacons of one 92-second capture):

```
16 00 00 00 | ff 00 00 00 | 00 00 00 00 00 00 00 00 | 0a 05 14 f2 | 2a 02
^^^^^^^^^^^   ^^^^^^^^^^^   ^^^^^^^^^^^^^^^^^^^^^^^   ^^^^^^^^^^^   ^^^^^
length=22     255           zero padding              10.5.20.242   ?
(uint32 LE)   (type?)                                 console IP    unidentified
```

| Offset | Size | Meaning |
|---|---|---|
| 0 | 4 | Payload length, `22`, uint32 little-endian |
| 4 | 4 | Constant `255` - message type or protocol version (only one value ever seen) |
| 8 | 8 | Zero padding |
| 16 | 4 | **The console's own IPv4 address, raw bytes** |
| 20 | 2 | `2a 02` - unidentified; constant across every beacon observed |

Only one console was present during capture, so the fields called "type?" and "unidentified" are constant by circumstance and could mean something else entirely. Treat offset 16-20 as the reliable part.

**To auto-discover a console:** bind UDP 2029, accept broadcasts, read the IP at offset 16. This removes the most common class of setup failure, since a wrong or stale IP no longer needs to be typed by hand.

## Connection lifecycle

**There is no negotiated handshake, no session, and no registration.** The console has no concept of a connected client - it simply transmits to whatever destination IP/port its External Control panel names. That configuration *is* the registration. Consequences:

- If the console's configured destination IP does not match your machine, it will accept every packet you send and answer none of them. This looks identical to a wrong port or a firewall, and is the single most likely cause of one-way silence.
- Nothing expires. Verified by going completely silent for 150 seconds: the console kept pushing change broadcasts unprompted and still answered queries immediately afterwards.
- A successful socket `bind()` proves nothing about whether a console is there. The **first inbound datagram** is the only real evidence of a live console.

### What the official DiGiCo client does on connect

Captured 2026-09-03. Client `10.5.20.211:63337` to console `:800`; console replied from `:65511` to client `:900`.

| Step | Message | Notes |
|---|---|---|
| 1 | `/Console/Name/?` | **The de-facto handshake.** Sent repeatedly (3x in the first 11 ms) and *retried until answered* - the client does not proceed until `/Console/Name` comes back. |
| 2 | `/Console/Session/Filename/?` | e.g. `["cl default.ses"]` |
| 3 | `/Snapshots/Surface_Snapshot/?` | |
| 4 | `/Console/Channels/?` | Triggers the 9-message topology burst |
| 5 | `/Console/Aux_Outputs/modes/?` | 30 values, `1`=mono `2`=stereo |
| 6 | `/Console/Input_Channels/modes/?` | 72 values |
| 7 | `/Console/Group_Outputs/modes/?` | 3 values |
| 8 | `/Console/Multis/?` | |
| 9 | `/Layout/Layout/Banks/?` | 24 replies (3 layers x 4 banks x L/R) |
| 10 | `/Meters/clear` then `/Meters/request/{n}` | See [Metering](#metering) |

Steps 2-8 are fired as a single burst within about 1 ms; the client does not wait for each reply.

**Re-observed 2026-09-20, and step 1 is not what it looked like.** The app sent `/Console/Name/?` and was answered in 36 ms - and then kept asking, about every 1.5 s, for a further **16 seconds**, before firing the boot burst. It was not retrying until answered; it was sitting on its connection screen polling for a console to still be there, and the burst went out when the session was actually opened. So `/Console/Name/?` is both the handshake and an idle poll, and a console seeing it repeatedly is not a sign anything is wrong.

The burst itself was the same set of queries in a slightly different order (`Session/Filename`, `Aux_Outputs/modes`, `Surface_Snapshot`, `Input_Channels/modes`, `Channels`, `Session/Filename` again, `Group_Outputs/modes`, `Multis`, then `Layout/Layout/Banks` 55 ms later), which suggests the order is incidental and only the set matters. `/Console/Session/Filename/?` appearing twice in one burst is the keep-alive's first tick landing inside it.

Then, per visible strip, the app reads the nine parameters its channel strip actually draws:

```
Channel_Input/name      mute        CGs_level
Channel_Input/main/alt_in   solo    CGs_mute
Panner/pan              fader       Channel_Input/stereo_mode
```

followed by `/Meters/clear` and one `/Meters/request/{slot}` per leg. Opening a processing panel adds that panel's parameters for the selected channel only - the whole EQ section, or the whole dynamics section - rather than re-reading the strip.

### Keep-alive

The official client sends **`/Console/Session/Filename/?` every 2.00 seconds** for the entire life of the session - dead regular, and the only recurring traffic in a settled connection.

The console does **not** require this (it answers fine after 150 s of silence), so it is a client-side liveness detector rather than a protocol obligation. CLMix's own 3-second `/Snapshots/Current_Snapshot/?` heartbeat in `_check_heartbeat` serves the identical purpose and is equally valid; matching the official 2.0 s / `/Console/Session/Filename/?` is optional.

## Metering

Meters are **not** readable via `/?`. They use a **slot-based subscription**: you bind meter addresses to numbered slots, and the console then streams all subscribed slots in a single compact message at ~30 Hz.

### Subscribing

```
/Meters/clear                                                    (no args)
/Meters/request/0   "/Input_Channels/33/Channel_Input/post_meter/left"
/Meters/request/1   "/Input_Channels/35/Channel_Input/post_meter/left"
/Meters/request/2   "/Input_Channels/34/Channel_Input/post_meter/left"
...
```

- `/Meters/clear` takes no arguments and resets the whole subscription list. Send it before (re)building a subscription - e.g. when the user changes bank or page.
- `/Meters/request/{slot}` takes exactly **one OSC string**: the full meter address to bind to that slot number. Slots are zero-based and assigned by you.
- Any address ending in a meter leaf works, e.g. `Channel_Input/post_meter/left`, `.../pre_meter/right`, `Dynamics/GR_meter_1`. See the per-category tables below for the full set.
- The official client subscribed 12 slots - one per visible strip. Subscribe only what is actually on screen; this is a continuous 30 Hz stream, not a poll.
- **Slot numbers are each client's own**, and nothing in a `/Meters/values` packet says which channel a slot means. That makes the stream proxyable: one client can subscribe to the union of what several want, then re-number each packet into whatever slots each of them asked for and hand it on. `services/digico_bridge.py` does exactly this, so CLMix and the official app can both meter at once off a table only CLMix is subscribed to - the app's `/Meters/clear` and `/Meters/request` never reach the console. It is the only way two clients can meter this desk simultaneously.
- **The app re-asserts the whole subscription about once a second**, and not only when the visible set changes: two `/Meters/clear` and then every slot again, 8-slot to 16-slot bursts, roughly every 1.1 s for as long as the meter view is up. Two clients doing this would simply take the table from each other once a second, which is why CLMix stays off it entirely while the bridge is running (`services/digico_bridge.py`).
- **Slots are not capped at 12.** The app used 0..15 for a bank of 16 legs, and adds more on top when a processing panel is open - the selected channel's `Dynamics/gate_meter`, `Dynamics/GR_meter_{n}` and `EQ/GR_meter_{n}` go into slots above the strip meters. The ceiling, if there is one, has not been found.
- Two meter addresses the whole-strip dump never mentioned turned up in the app's subscriptions: **`/Input_Channels/{n}/Dynamics/gate_meter`** and **`/Input_Channels/{n}/EQ/GR_meter_0`**. The second one matters beyond itself: `EQ/GR_meter` is **0-based**, while `Dynamics/GR_meter` is 1-based (`GR_meter_1`..`GR_meter_4`). Don't assume one convention across the address space.

### Receiving

The console pushes `/Meters/values` with a **flat list of alternating `[slot, value]` int pairs**:

```
/Meters/values  [0, 1572897, 1, 1376286, 2, 1769505, 4, 2359338, ...]
                 ^slot ^value ^slot ^value
```

- **Only slots whose value changed** are included, so message length varies packet to packet. Never assume a fixed layout or that slot *n* sits at index *2n* - always read the pairs.
- Update rate is ~35 ms (about 29 Hz), regardless of how many slots are subscribed.
- Both fields are OSC **int32**.

### Decoding a meter value

Each 24-bit value packs **two independent 8-bit fields**; the middle byte is always zero:

```
value = (peak << 16) | rms          # byte 1 is always 0x00
peak  = (value >> 16) & 0xFF
rms   =  value        & 0xFF
```

**A field is simply dB below zero**: `24` means -24 dB.

```
dB = -field
```

The scale runs **0 dB down to -60 dB**, matching the scale printed on the console's own meters, and `126` is a no-signal sentinel.

> **Do not read the "multiple of 3" as a scale factor.** Every field is a multiple of 3 because the console quantises meters to **3 dB steps**, not because the value needs dividing by 3. Dividing by 3 was the original mistake here: it compressed the real -6..-60 dB range into -2..-20 dB, so every bar read about 20 dB hot and sat near full scale. It was caught by filming the console's meters and CLMix's side by side.

The decisive evidence is the value distribution across both reference captures. The fields take **every multiple of 3 from 6 to 60, and then nothing at all until 126**:

```
high byte:  6 9 12 ... 51 54 57                126
low  byte:  6 9 12 ... 54 57 60                126
                              ^^^^^^^^^^^^^^^^
                              clean gap - no values between 60 and 126
```

A field of `60` is therefore -60 dB, the bottom of the scale, and `126` is a sentinel rather than a measurement. Had the scale really been `-field/3`, values would have run continuously up to 126 with no gap.

| Field | Byte | Meaning | Evidence |
|---|---|---|---|
| `value >> 16` | high | **Peak**, with peak-hold behaviour | Changed on only 40 of 878 transitions for a busy channel |
| `value & 0xFF` | low | **RMS / instantaneous** level | Changed on 442 of the same 878 transitions |

Decoded over a capture with music playing, peak spans -39..-6 dB (median -18) and RMS spans -60..-6 dB (median -24) - consistent with the console's own meters in the same room reading roughly -20 to -25 dB.

**The peak field falls back to the sentinel on its own.** With a steady low-level signal and no recent transient, the high byte reads `126` while the low byte still reports a real level - accounting for all 48 of the "peak quieter than RMS" samples in the capture. Render the bar from whichever field is present and omit the peak marker when peak is `None`; do not treat it as a decoding error. Deriving peak-hold locally from the displayed level is more stable than trusting this field frame to frame.

**Rejected alternative: the two fields are not stereo left/right.** Worth recording because it is the obvious guess. All 12 channels subscribed in `sound sample.pcapng` are mono per `/Console/Input_Channels/modes`, yet they reported *both* fields carrying a level in 9,248 of 9,350 samples. A left/right split would leave the second field at sentinel for every mono channel.

**Stereo lives in the address space, not in the value.** A stereo channel is metered by subscribing *two* slots - `.../post_meter/left` and `.../post_meter/right` - each carrying its own independent peak/RMS pair. `/Console/Input_Channels/modes` says which channels warrant the second slot; the `/right` address answers on mono channels too, so it cannot be used to detect stereo. This doubles the slot cost of a bank of stereo strips, which is the practical reason to subscribe only what is on screen.

### Worked example

```python
FLOOR_FIELD = 126           # no-signal sentinel

def decode_meter(value):
    """(peak_db, rms_db) from a /Meters/values int. None == no signal."""
    peak, rms = (value >> 16) & 0xFF, value & 0xFF
    return (None if peak >= FLOOR_FIELD else float(-peak),
            None if rms  >= FLOOR_FIELD else float(-rms))

# /Meters/values [0, 1572897, ...]
decode_meter(1572897)   # 0x180021 -> (-24.0 dB peak, -33.0 dB rms)
decode_meter(3932220)   # 0x3C003C -> (-60.0, -60.0)  - bottom of scale
decode_meter(8257662)   # 0x7E007E -> (None, None)    - no signal
```

### Notes for CLMix

- Resolution is coarse: **3 dB steps over a 60 dB span**, i.e. 21 discrete levels per field. Plenty for a bar meter, but do not present it as a precise readout - and smooth the fall in the UI, or the bar visibly jumps a twentieth of its height at a time.
- One slot per *leg*, not per channel: a bank of 12 strips costs 12 slots if they are all mono and 24 if they are all stereo.
- At 30 Hz with 12 slots this is ~30 packets/sec - the dominant traffic on the link (594 of 1,240 console packets in one capture, 1,054 of 1,895 in another). Re-subscribe with `/Meters/clear` when the visible set changes rather than subscribing every channel on the console.
- Meter traffic shares the same `recv_port` as everything else, so it lands in the same `receive_osc` loop. `/Meters/values` should be dispatched before the generic cache path, and must **not** be cached per-address - it is a stream, not a parameter.

## Console topology (from `/Console/Channels/?`)

A single query to `/Console/Channels/?` triggers the console to broadcast one count message per category - this is the fastest way to learn a console's shape:

| Category | Count | OSC address |
|---|---|---|
| Input_Channels | 72 | `/Console/Input_Channels` |
| Aux_Outputs | 30 | `/Console/Aux_Outputs` |
| Group_Outputs | 3 | `/Console/Group_Outputs` |
| Talkback_Outputs | 2 | `/Console/Talkback_Outputs` |
| Control_Groups | 12 | `/Console/Control_Groups` |
| Matrix_Inputs | 12 | `/Console/Matrix_Inputs` |
| Matrix_Outputs | 12 | `/Console/Matrix_Outputs` |
| Graphic_EQ | 16 | `/Console/Graphic_EQ` |
| Multis | 1 | `/Console/Multis` |

Console name (`/Console/Name`): **SD7Q-Q2**

**Only the burst answers.** The individual count addresses are push-only: the app sent `/Console/Multis/?` fourteen times on its own and was answered none of them, and the two replies it did get both arrived in the burst triggered by a `/Console/Channels/?` sent in the same millisecond. Treat `/Console/Channels/?` as the only way to read any of these - asking for one category by name looks exactly like a dead console.

## Already used by CLMix today

For reference, these are the addresses `ui/main_window.py` already speaks - all confirmed live against this console during probing:

| Address pattern | Purpose |
|---|---|
| `/Console/Channels/?` | Boot: triggers the topology burst above |
| `/Console/Aux_Outputs/modes/?` | Boot: per-aux mono/stereo mode list. Gives the aux count (its length) and, per entry, whether that bus has a pan axis at all - see below |
| `/Console/Input_Channels/modes/?` | Boot: per-channel mono/stereo mode list. Decides how many meter slots each channel takes - see below |
| `/Aux_Outputs/{n}/Buss_Trim/name/?` | Aux bus display name |
| `/Input_Channels/{n}/Channel_Input/name/?` | Channel display name |
| `/Input_Channels/{n}/mute` (get/set) | Channel mute |
| `/Input_Channels/{n}/Aux_Send/{a}/send_level` (get/set) | Channel's send level to aux `a` |
| `/Input_Channels/{n}/Aux_Send/{a}/send_pan` (get/set) | Channel's send pan to aux `a`. Only meaningful when aux `a` is stereo; CLMix hides the control and skips the query on a mono bus |
| `/Input_Channels/{n}/Aux_Send/{a}/send_on` (get/set) | Whether channel `n` is in aux `a`'s mix at all (`0.0` = out). What the phone apps' per-channel Mute button drives, since it affects only that one aux mix - unlike `/Input_Channels/{n}/mute` above, which cuts the source everywhere. |
| `/Meters/clear`, `/Meters/request/{slot}` | Meter subscription for the visible strips: one slot on `.../post_meter/left` for a mono channel, two (`left` and `right`) for a stereo one |
| `/Snapshots/Current_Snapshot/?` | Currently recalled snapshot number |
| `/Snapshots/names/?` | Broadcasts `/Snapshots/name [index, name]` per snapshot |
| `/Snapshots/Rename_Snapshot/{n}` | Broadcast when a snapshot is renamed |
| `/Snapshots/Recall_Snapshot/{n}`, `/Snapshots/Change_Surface_Snapshot/{n}` | Broadcast on snapshot recall |
| `/Layout/Layout/Banks/?` | Custom surface bank layout (one reply per bank) |

Show Backup (`services/show_backup.py`) speaks these on top, all of them added from the 2026-09-20 capture:

| Address pattern | Purpose |
|---|---|
| `/{category}/{n}/?` | The whole-strip dump a backup is built from |
| `/{category}/{n}/Dynamics/gate_hold`, `gate_range`, `gate-duck-comp` | Asked for by name because the dump omits them - see [Parameters a strip dump leaves out](#parameters-a-strip-dump-leaves-out) |
| `/Snapshots/name/?` with `,i [n]` | Refills one snapshot name the bulk list dropped |
| `/Snapshots/Surface_Snapshot/?` | Recorded in the manifest beside the recalled snapshot |
| `/Macros/names/?` | Macro names, saved for a rebuild; nothing writes them back |
| `/Snapshots/End_Recall_Snapshot` (listened for) | When it is safe to start reading after a recall |

Newly discovered below (channel EQ, dynamics, gate, delay, input gain/phantom/pad, routing to groups/matrix, aux/group/matrix bus processing, DCAs, graphic EQs, multitrack returns) is **not yet wired into the app** - it's everything else the console exposes.

## Names are session state

Every naming address on this console - `Channel_Input/name`, `Buss_Trim/name`, and the bare `/name` on `Control_Groups`, `Graphic_EQ` and `Multis` - belongs to the **session**, not to a snapshot. The desk keeps a strip's name across a recall, which is why the channel you renamed stays renamed whatever scene you jump to.

Evidence from the 2026-09-20 capture, which is corroboration rather than proof:

- The recall at 08:06:09 broadcast **no** name messages at all, while broadcasting other changed parameters in the same burst.
- Across 45 distinct input channels, read repeatedly over eight minutes and across that recall, **no channel name ever reported two different values**.

The caveat is that the one recall observed was to the snapshot already loaded, so it changed very little - this is consistent with names being session state rather than demonstrating it outright.

**What this means for a backup.** Saving names inside every snapshot stores the same strings once per snapshot and makes a restore rename the whole desk once per snapshot, for no gain. `services/show_backup.py` therefore lifts them into the manifest once and restores them as their own job (`RestoreNamesJob`), which needs no snapshot recalled and no Update pressed - so a rebuilt desk can be made to *read* correctly in seconds, long before anyone has time for the per-snapshot settings.

Because the claim above is corroborated rather than proved, the backup checks it instead of trusting it: every snapshot's names are compared against the session's, and any that disagree are kept with that snapshot and reported. If this console turns out to be per-snapshot after all, a backup will say so rather than quietly losing a name.

There is one genuinely notable asymmetry to remember when writing names back: a **string SET is echoed even when the value has not changed**, unlike a numeric one - see [Transport / wire protocol](#transport--wire-protocol).

## Parameters a strip dump leaves out

**A whole-strip dump is not the whole strip.** This is the most consequential thing the 2026-09-20 capture established, because everything else in this document rests on the opposite assumption - the 946 addresses below were collected by dumping one strip per category and writing down what came back.

Three parameters the app asked for by name are not in any of those dumps, and answer a direct `/?` perfectly well:

| Address | Type | Sample | What it is |
|---|---|---|---|
| `/Input_Channels/{n}/Dynamics/gate_hold` | float (seconds) | `[0.0799]`, `[0.0359]` | Gate hold time |
| `/Input_Channels/{n}/Dynamics/gate_range` | float (dB) | `[15.0]`, `[40.235]` | Gate range/depth |
| `/Input_Channels/{n}/Dynamics/gate-duck-comp` | float (enum) | `[0.0]`, `[2.0]` | Which of gate / duck / comp the section is running - the sibling of the already-documented `comp-multiband-desser` |

The app queries all three whenever it opens a channel's dynamics panel, and the console answers with real per-channel values, so they are ordinary stored channel state that the dump simply does not volunteer.

**Consequences:**

- A backup built purely from strip dumps silently loses them. `services/show_backup.py` asks for them by name after each dump (`DUMP_GAP_LEAVES`) for exactly this reason, and `tools/mock_mixer.py` reproduces the gap so the code that copes with it is actually exercised.
- More generally: **the dump cannot be trusted to be exhaustive.** There is no reason to think these three are the only ones, and no way to enumerate what is missing except by watching the official app ask for something and noticing it was never in the dump. Every future capture is worth diffing against `commands.csv` on exactly this question.
- Only `Input_Channels` was observed. The `Dynamics` block is identical across `Aux_Outputs`, `Group_Outputs` and `Matrix_Outputs` in the map below, so the same three are very likely missing there too; CLMix asks for them on any strip whose dump showed a gate, which costs nothing if they are not there.

> **These three are deliberately *not* in `commands.csv`.** That file is the record of what the console's own dumps returned, and `tools/mock_mixer.py` builds its simulated strips straight from it - putting them in would both misrepresent the dump and quietly remove the gap the mock exists to reproduce. They live here and in `DUMP_GAP_LEAVES` instead.

## Full command map by category

Numeric path segments and `_N` suffixes are collapsed to `{n}` (e.g. `eq_gain_1`..`eq_gain_4` become `eq_gain_{n}`, count 4). `count` is how many concrete instances were seen on channel/bus **index 1** - multiply by the category count above for the true total across the whole console. `sample value` is whatever the console actually held for that parameter at probe time, not a spec default.

### Console

Console identity/topology. A single query ("/Console/Channels/?") triggers a burst of one message per category giving its channel count - this is how the category counts above were discovered.

| Pattern | Count | Type | Sample value | Sample address |
|---|---|---|---|---|
| `/Console/Name` | 1 | string | `["SD7Q-Q2"]` | `/Console/Name` |
| `/Console/Session/Filename` | 1 | string | `["cl default.ses"]` | `/Console/Session/Filename` |
| `/Console/Input_Channels/modes` | 1 | int list (72) | `[1, 1, 1, ..., 2, 2]` | `/Console/Input_Channels/modes` |
| `/Console/Group_Outputs/modes` | 1 | int list (3) | `[2, 1, 1]` | `/Console/Group_Outputs/modes` |

`*/modes` lists carry one entry per channel/bus: `1` = mono, `2` = stereo. `/Console/Aux_Outputs/modes` (already used by CLMix) is the same shape with 30 entries.

`/Console/Input_Channels/modes` is what tells you whether a channel's `.../post_meter/right` is worth a meter slot: the address exists on every channel regardless, so the modes list is the only way to know a right leg carries anything. CLMix subscribes one slot per leg on this basis - see [Metering](#metering).

`/Console/Aux_Outputs/modes` answers the equivalent question for sends: **a mono aux bus has no pan axis**, so `/Input_Channels/{n}/Aux_Send/{a}/send_pan` is a no-op on one. The console accepts the write and echoes it back like any other parameter either way, so there is no way to discover this by trying it - the modes list is the only signal. CLMix reads it to drop the pan control from the strip and skip the per-channel `send_pan/?` queries on aux change, and passes the same fact to phone clients as a `stereo` flag on each entry of its aux list.

`/Console/Session/Filename` is the currently loaded session file. The official client polls it every 2.0 s as its keep-alive - see [Connection lifecycle](#connection-lifecycle).

### Bulk routing reads under `/Console`

Three addresses under `/Console` carry per-strip routing as one message instead of one per send. They duplicate what the strip dump already gives, but in a form that is one datagram per strip rather than 12, which is why the app uses them.

| Pattern | Type | Sample | Same as |
|---|---|---|---|
| `/Console/Input_Channels/{n}/group_sends` | int list, one per Group_Output | `,iii [1, 1, 0]` | `/Input_Channels/{n}/Group_Send/{g}/group` |
| `/Console/Matrix_Inputs/{n}/send_levels` | float list, one per Matrix_Output | `,ffff... [0.0, 0.0, -150.0, ...]` | `/Matrix_Inputs/{n}/Matrix_Send/{m}/send_level` |
| `/Console/Matrix_Inputs/{n}/send_ons` | int list, one per Matrix_Output | `,iiii... [1, 1, 0, ...]` | `/Matrix_Inputs/{n}/Matrix_Send/{m}/send_on` |

`-150.0` is this console's "off" for a send level. Note the types: these lists come back as `i` where the per-send addresses are `f`, so a value read through one and written through the other needs converting.

### Macros

The console's macro buttons, by name. Nothing here writes or fires one - `/Macros/Buttons/?` was asked six times and never answered, and no address that triggers a macro has been seen - so this is a read-only catalogue.

| Pattern | Count | Type | Sample value |
|---|---|---|---|
| `/Macros/name` | per macro | `[index, name]`, `,is` | `[11, "Save Current Snapshot"]` |

`/Macros/names/?` broadcasts one `/Macros/name` per macro, 22 of them on this console, and the index is **0-based**. Worth saving in a show backup: macro names are session work, and re-typing them from memory after a rebuild is exactly the kind of hour this tool exists to avoid.

### Snapshots

Scene/snapshot recall and naming.

| Pattern | Count | Type | Sample value | Sample address |
|---|---|---|---|---|
| `/Snapshots/Current_Snapshot` | 1 | int | `[3]` | `/Snapshots/Current_Snapshot` |
| `/Snapshots/Surface_Snapshot` | 1 | int | `[13]` | `/Snapshots/Surface_Snapshot` |
| `/Snapshots/count` | 1 | int | `[10]` | `/Snapshots/count` |
| `/Snapshots/name` | per snapshot | `[index, cue, 0, name]`, `,iiis` | `[14, 450, 0, "NATALIYA FILISTOVICH"]` | reply to `/Snapshots/names/?` |

**Snapshot indices are 0-based.** `/Snapshots/count` came back `17` for a session whose snapshots ran `0` ("CL DEFAULT") to `16`, and `/Snapshots/Current_Snapshot` uses the same numbering. So the valid range is `0 .. count - 1`, and there is no snapshot `count`.

**One name at a time: `/Snapshots/name/?` takes an index argument.** Sending it with `,i [n]` returns that one snapshot's name, in the same `,iiis` shape as the bulk list:

```
-> /Snapshots/name/?   ,i  [1]
<- /Snapshots/name     ,iiis  [1, 82, 0, "VIKA GUTSUL"]
```

This is how the app follows the current snapshot's name - it polls exactly this, every 3 s, rather than re-reading the whole list. It is also the repair for the one real hazard in `/Snapshots/names/?`: that reply is one datagram per snapshot, so a single lost packet silently drops a snapshot from your list, and re-asking for all of them to recover one is both wasteful and no less likely to drop another. Ask for the missing index instead.

#### Snapshot recall on the wire

A recall - whether started at the surface or by a client - broadcasts **four messages in a fixed order**, 11 ms end to end for a snapshot that changed nothing:

```
/Snapshots/Recall_Snapshot/1         ,i [0]
/Snapshots/Change_Surface_Snapshot/1 ,i [0]
/Snapshots/Current_Snapshot          ,i [1]
/Snapshots/End_Recall_Snapshot       ,i [0]
```

The index is in the **address**; the argument is a constant int `0` carrying no information. Two things follow that matter to anyone reading the desk around a recall:

- **`/Snapshots/Current_Snapshot` arrives in the middle, not at the end.** Waiting for it and then reading parameters means reading a desk that is still moving. On a snapshot that changes hundreds of values the tail of that burst keeps coming after it.
- **`/Snapshots/End_Recall_Snapshot` is the console saying it has finished.** It is the signal to wait for. `services/show_backup.py` does, falling back to a fixed settle for a desk that never sends one.

Whether the console *accepts* `/Snapshots/Recall_Snapshot/{n}` as a command is still unconfirmed - this capture only ever saw it broadcast. But since the desk's own form carries `,i [0]`, that is the form worth sending, and it is what CLMix sends.

### Layout

The custom fader layout: which strip sits on each fader, for every bank, on every layer, on both sides of the surface. `/Layout/Layout/Banks/?` broadcasts **one message per bank per side**.

| Pattern | Count | Type | Sample value | Sample address |
|---|---|---|---|---|
| `/Layout/Layout/Banks` | per bank per side | `ssii` then `si` x12 | `["CHOIR", "R", 2, 0, "Input_Channels", 58, ...]` | `/Layout/Layout/Banks` |

The argument shape is fixed - `,ssiisisisisisisisisisisisisi` in every one of the 18 replies captured on 2026-09-20:

| Args | Meaning |
|---|---|
| 0 | Bank name, e.g. `"VOCALS"`, `"DCA/    FX"` (the spacing is the operator's) |
| 1 | `"L"` or `"R"` - which side of the surface |
| 2 | Layer, `0`-`2` |
| 3 | Bank within that layer, `0`-`3` |
| 4.. | **12 `(category, index)` pairs**, one per fader, e.g. `"Input_Channels", 33`. An empty fader is `"", 0` |

So a fader can carry anything, not just an input: `Control_Groups`, `Aux_Outputs`, `Group_Outputs`, `Matrix_Outputs` and `Multis` all appeared on faders in this session.

**All four of the first arguments are needed to identify a bank.** The `L` and `R` entries of one bank carry the same name, so keying a collection of these on the name alone silently keeps one of each pair and loses half the layout. (They held identical strips in every bank captured here, but that is an observation about this session's layout, not a rule.)

**Writing it back is unproven.** The official app only ever reads this address - it was never written in eight minutes of deliberate driving - and no write form has been confirmed. `services/show_backup.py` attempts it anyway, by sending the console's own reply back verbatim, then reads the layout again to see whether it took, and reports the banks for rebuilding by hand if it did not. Anyone testing this on a real desk: `tools/mock_mixer.py --no-layout-write` plays the console that ignores the write.

One practical trap if you do write it: **every bank goes to the same address**, so a client that coalesces queued commands by address (as `MixerWorker._drain_commands` does) will collapse the whole layout into a single bank. They have to be paced.

### Input_Channels

Input channel strips (mic/line inputs). 72 on this console.

#### Head-amp ranges, and the console clamping them

The console never states a parameter's limits over OSC - there is no
min/max address - so the only way to learn them is to push a value past
the end and see what comes back. The official app does exactly that: it
sends raw dial positions without clamping them itself, and the desk pins
them.

| Parameter | Range | Default | How it was established |
|---|---|---|---|
| `Channel_Input/analog_gain` | **-20 dB to +60 dB** | `0` | Operator-stated, and confirmed on the wire: across 185 replies the console never reported outside it, while the app sent it values from -40 to +60 |
| `Channel_Input/trim` | **-40 dB to +40 dB** | - | Clamp observed at `+40`; `-40` was reported by the console, so the floor is at least that and may be lower |

The clamp caught in the act, gain first:

```
APP->MIXER  /Input_Channels/44/.../analog_gain ,f [-25.396825790405273]
MIXER->APP  /Input_Channels/44/.../analog_gain ,f [-20.0]          <- pinned
```

and trim:

```
APP->MIXER  /Input_Channels/44/.../trim ,f [41.78010559082031]
MIXER->APP  /Input_Channels/44/.../trim ,f [40.0]                  <- pinned
```

**Never assume a SET was stored as sent.** The console accepts the
datagram either way and reports what it actually kept, so the echo is
the value - which is another reason to read back rather than trust a
write (see [Transport / wire protocol](#transport--wire-protocol)).

> **A caveat to the "no echo when unchanged" rule.** Held at the gain
> floor, the app sent `-40.0` thirteen more times and the console said
> nothing at all - the stored value was already `-20.0` and unchanged, as
> that rule predicts. But held at the trim ceiling it echoed `40.0` to
> *every* repeat of `60.0`. So the rule holds for numbers the console
> stores as sent and cannot be relied on for ones it clamps. Anything
> waiting on an echo needs a timeout either way.

#### Input patching (`Channel_Input/input_type`)

`input_type` says whether the strip has a route patched to its **main**
input: `2.0` patched, `0.0` empty. Captured live 2026-09-17 by patching
and unpatching channels 17, 18, 40 and 51 from the console surface while
logging every inbound datagram.

Patching a route emits a **three-message burst in a fixed order**, all
within the same second:

```
/Input_Channels/40/Channel_Input/analog_gain ,f [45.0]
/Input_Channels/40/Channel_Input/phantom     ,f [1.0]
/Input_Channels/40/Channel_Input/input_type  ,f [2.0]
```

`input_type` comes **last** - it confirms the patch rather than
announcing it. The `analog_gain` and `phantom` values are the head-amp
state *stored against the socket being patched in*, not a reset: the
same channel 40 patched to two different sockets reported `45.0`/on and
then `37.0`/on, while channels 18 and 51 reported `0.0`/off. Those two
values are consequently the only clue on the wire as to *which* socket
was patched - no address carries the socket identity itself.

Unpatching emits **`input_type 0.0` alone**, with no `analog_gain` or
`phantom`, there being no head-amp left to report. Repatching a channel
to a different socket is simply the two events back to back: `0.0`, then
the full burst a second later.

The **alt input** slot has its own parameters and the burst carries them
instead of, or alongside, the main pair - `alt_analog_gain`,
`alt_phantom`. `input_type` tracks the main slot only: patching alt
while main was empty still reported `input_type 0.0`. Which of the two
slots is actually feeding the channel is a separate parameter,
`Channel_Input/main/alt_in`.

Only `0.0` and `2.0` have ever been observed, and `2.0` implies a `1.0`
that nothing has yet produced - plausibly another source class. Test it
as **non-zero means patched** rather than `== 2.0`.

It is stored channel state, so a snapshot recall also broadcasts it for
every channel whose value changes, alongside that channel's
`analog_gain`, `phantom` and EQ.

**Read freely; do not write it.** The 2026-09-20 capture is independent
evidence for this. The official app queried `input_type` 112 times - it
is part of the set it reads whenever an input panel is opened, alongside
`phase`, `analog_gain`, `trim`, `Insert/insert_A_in`, `insert_B_in`,
`Channel_Delay/*`, `stereo_mode` and `main/alt_in` - and was answered
every time. **It never once wrote it**, in eight minutes of deliberately
opening panels and moving controls; the only things the app set all
session were `analog_gain`, `trim`, `mute`, `Channel_Input/name` and
`Channel_Delay/delay_on`. That is the behaviour you would expect of a
value that reports a patch rather than making one.

The same capture corroborates the head-amp rule above from the other
direction: the two unpatched channels (18 and 43) reported `input_type
0.0` **and** `analog_gain 0.0` / `phantom 0.0`, while every patched
channel reported `2.0` with a real gain - so an empty channel really has
no head-amp state to give, rather than holding a stale one. Only `0.0`
and `2.0` have now been seen across both sessions. `main/alt_in` read
`0.0` on all 122 samples, so nothing here exercised the alt slot.

The practical consequence for CLMix: `input_type` is worth reading (it
is how you know a strip is live at all) and is deliberately in
`NEVER_RESTORE_SUFFIXES` in `services/show_backup.py`. Even if the desk
accepted a write, no address on the wire carries socket identity, so
there is nothing to tell it *which* socket to patch - see
`tools/probe_patching.py`.

| Pattern | Count | Type | Sample value | Sample address |
|---|---|---|---|---|
| `/Input_Channels/{n}/Aux_Send/{n}/send_level` | 30 | float | `[-5.670000076293945]` | `/Input_Channels/1/Aux_Send/1/send_level` |
| `/Input_Channels/{n}/Aux_Send/{n}/send_on` | 30 | float (0/1 flag) | `[1.0]` | `/Input_Channels/1/Aux_Send/1/send_on` |
| `/Input_Channels/{n}/Aux_Send/{n}/send_pan` | 30 | float | `[0.5]` | `/Input_Channels/1/Aux_Send/1/send_pan` |
| `/Input_Channels/{n}/CGs_level` | 1 | float | `[0.5217241048812866]` | `/Input_Channels/1/CGs_level` |
| `/Input_Channels/{n}/CGs_mute` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/CGs_mute` |
| `/Input_Channels/{n}/Channel_Delay/delay` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Channel_Delay/delay` |
| `/Input_Channels/{n}/Channel_Delay/delay_on` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Channel_Delay/delay_on` |
| `/Input_Channels/{n}/Channel_Delay/fine_delay` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Channel_Delay/fine_delay` |
| `/Input_Channels/{n}/Channel_Input/alt_analog_gain` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Channel_Input/alt_analog_gain` |
| `/Input_Channels/{n}/Channel_Input/alt_input_pad` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Channel_Input/alt_input_pad` |
| `/Input_Channels/{n}/Channel_Input/alt_phantom` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Channel_Input/alt_phantom` |
| `/Input_Channels/{n}/Channel_Input/analog_gain` | 1 | float (dB, -20..+60, default 0) | `[20.0]` | `/Input_Channels/1/Channel_Input/analog_gain` |
| `/Input_Channels/{n}/Channel_Input/input_pad` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Channel_Input/input_pad` |
| `/Input_Channels/{n}/Channel_Input/input_type` | 1 | float (enum, see below) | `[2.0]` | `/Input_Channels/1/Channel_Input/input_type` |
| `/Input_Channels/{n}/Channel_Input/main/alt_in` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Channel_Input/main/alt_in` |
| `/Input_Channels/{n}/Channel_Input/name` | 1 | string | `["KICK"]` | `/Input_Channels/1/Channel_Input/name` |
| `/Input_Channels/{n}/Channel_Input/phantom` | 1 | float (0/1 flag) | `[1.0]` | `/Input_Channels/1/Channel_Input/phantom` |
| `/Input_Channels/{n}/Channel_Input/phase` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Channel_Input/phase` |
| `/Input_Channels/{n}/Channel_Input/post_meter/left` | 1 | none (meter/empty) | `[]` | `/Input_Channels/1/Channel_Input/post_meter/left` |
| `/Input_Channels/{n}/Channel_Input/post_meter/right` | 1 | none (meter/empty) | `[]` | `/Input_Channels/1/Channel_Input/post_meter/right` |
| `/Input_Channels/{n}/Channel_Input/pre_meter/left` | 1 | none (meter/empty) | `[]` | `/Input_Channels/1/Channel_Input/pre_meter/left` |
| `/Input_Channels/{n}/Channel_Input/pre_meter/right` | 1 | none (meter/empty) | `[]` | `/Input_Channels/1/Channel_Input/pre_meter/right` |
| `/Input_Channels/{n}/Channel_Input/stereo_mode` | 1 | float (0/1 flag) | `[1.0]` | `/Input_Channels/1/Channel_Input/stereo_mode` |
| `/Input_Channels/{n}/Channel_Input/trim` | 1 | float (dB, -40..+40) | `[0.0]` | `/Input_Channels/1/Channel_Input/trim` |
| `/Input_Channels/{n}/Dynamics/GR_meter_{n}` | 4 | none (meter/empty) | `[]` | `/Input_Channels/1/Dynamics/GR_meter_1` |
| `/Input_Channels/{n}/Dynamics/comp-multiband-desser` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Dynamics/comp-multiband-desser` |
| `/Input_Channels/{n}/Dynamics/comp_HP_crossover` | 1 | float | `[1000.0]` | `/Input_Channels/1/Dynamics/comp_HP_crossover` |
| `/Input_Channels/{n}/Dynamics/comp_LP_crossover` | 1 | float | `[130.0]` | `/Input_Channels/1/Dynamics/comp_LP_crossover` |
| `/Input_Channels/{n}/Dynamics/comp_all_gain` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Dynamics/comp_all_gain` |
| `/Input_Channels/{n}/Dynamics/comp_all_thresh` | 1 | float | `[-25.176437377929688]` | `/Input_Channels/1/Dynamics/comp_all_thresh` |
| `/Input_Channels/{n}/Dynamics/comp_attack_{n}` | 3 | float | `[0.049162182956933975]` | `/Input_Channels/1/Dynamics/comp_attack_1` |
| `/Input_Channels/{n}/Dynamics/comp_auto-gain_{n}` | 4 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Dynamics/comp_auto-gain_1` |
| `/Input_Channels/{n}/Dynamics/comp_band_in_{n}` | 3 | float (0/1 flag) | `[1.0]` | `/Input_Channels/1/Dynamics/comp_band_in_1` |
| `/Input_Channels/{n}/Dynamics/comp_gain_{n}` | 4 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Dynamics/comp_gain_1` |
| `/Input_Channels/{n}/Dynamics/comp_in` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Dynamics/comp_in` |
| `/Input_Channels/{n}/Dynamics/comp_knee_{n}` | 4 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Dynamics/comp_knee_1` |
| `/Input_Channels/{n}/Dynamics/comp_listen_{n}` | 3 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Dynamics/comp_listen_1` |
| `/Input_Channels/{n}/Dynamics/comp_ratio_{n}` | 4 | float | `[4.930753231048584]` | `/Input_Channels/1/Dynamics/comp_ratio_1` |
| `/Input_Channels/{n}/Dynamics/comp_release_{n}` | 3 | float | `[0.06719738245010376]` | `/Input_Channels/1/Dynamics/comp_release_1` |
| `/Input_Channels/{n}/Dynamics/comp_thresh_{n}` | 3 | float | `[-25.176437377929688]` | `/Input_Channels/1/Dynamics/comp_thresh_1` |
| `/Input_Channels/{n}/Dynamics/desser_centre_freq` | 1 | float | `[127.0]` | `/Input_Channels/1/Dynamics/desser_centre_freq` |
| `/Input_Channels/{n}/Dynamics/desser_freq_width` | 1 | float | `[255.0]` | `/Input_Channels/1/Dynamics/desser_freq_width` |
| `/Input_Channels/{n}/Dynamics/gate_attack` | 1 | float | `[0.0019596272613853216]` | `/Input_Channels/1/Dynamics/gate_attack` |
| `/Input_Channels/{n}/Dynamics/gate_centre_freq` | 1 | float | `[127.0]` | `/Input_Channels/1/Dynamics/gate_centre_freq` |
| `/Input_Channels/{n}/Dynamics/gate_freq_width` | 1 | float | `[255.0]` | `/Input_Channels/1/Dynamics/gate_freq_width` |
| `/Input_Channels/{n}/Dynamics/gate_hold` | 1 | float (seconds) | `[0.0799]` | not in the dump - see [Parameters a strip dump leaves out](#parameters-a-strip-dump-leaves-out) |
| `/Input_Channels/{n}/Dynamics/gate_in` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Dynamics/gate_in` |
| `/Input_Channels/{n}/Dynamics/gate_meter` | 1 | none (meter/empty) | `[]` | subscribed by the app; not in the dump |
| `/Input_Channels/{n}/Dynamics/gate_range` | 1 | float (dB) | `[15.0]` | not in the dump - see [Parameters a strip dump leaves out](#parameters-a-strip-dump-leaves-out) |
| `/Input_Channels/{n}/Dynamics/gate-duck-comp` | 1 | float (enum) | `[2.0]` | not in the dump - see [Parameters a strip dump leaves out](#parameters-a-strip-dump-leaves-out) |
| `/Input_Channels/{n}/Dynamics/gate_release` | 1 | float | `[0.017051173374056816]` | `/Input_Channels/1/Dynamics/gate_release` |
| `/Input_Channels/{n}/Dynamics/gate_thresh` | 1 | float | `[-9.41171646118164]` | `/Input_Channels/1/Dynamics/gate_thresh` |
| `/Input_Channels/{n}/Dynamics/input_meter/left` | 1 | none (meter/empty) | `[]` | `/Input_Channels/1/Dynamics/input_meter/left` |
| `/Input_Channels/{n}/Dynamics/input_meter/right` | 1 | none (meter/empty) | `[]` | `/Input_Channels/1/Dynamics/input_meter/right` |
| `/Input_Channels/{n}/Dynamics/key_solo` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Dynamics/key_solo` |
| `/Input_Channels/{n}/EQ/GR_meter_{n}` | 4 | none (meter/empty) | `[]` | `/Input_Channels/1/EQ/GR_meter_1` - but the app subscribes `GR_meter_0` too, so this one is 0-based |
| `/Input_Channels/{n}/EQ/dynamic_eq_on_{n}` | 4 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/EQ/dynamic_eq_on_1` |
| `/Input_Channels/{n}/EQ/eq_Q_{n}` | 4 | float | `[2.9718434810638428]` | `/Input_Channels/1/EQ/eq_Q_1` |
| `/Input_Channels/{n}/EQ/eq_attack_{n}` | 4 | float | `[0.009999999776482582]` | `/Input_Channels/1/EQ/eq_attack_1` |
| `/Input_Channels/{n}/EQ/eq_curve_{n}` | 4 | float (0/1 flag) | `[1.0]` | `/Input_Channels/1/EQ/eq_curve_1` |
| `/Input_Channels/{n}/EQ/eq_freq_{n}` | 4 | float | `[6410.8017578125]` | `/Input_Channels/1/EQ/eq_freq_1` |
| `/Input_Channels/{n}/EQ/eq_gain_{n}` | 4 | float | `[4.588225364685059]` | `/Input_Channels/1/EQ/eq_gain_1` |
| `/Input_Channels/{n}/EQ/eq_in` | 1 | float (0/1 flag) | `[1.0]` | `/Input_Channels/1/EQ/eq_in` |
| `/Input_Channels/{n}/EQ/eq_on_{n}` | 4 | float (0/1 flag) | `[1.0]` | `/Input_Channels/1/EQ/eq_on_1` |
| `/Input_Channels/{n}/EQ/eq_over-under_{n}` | 4 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/EQ/eq_over-under_1` |
| `/Input_Channels/{n}/EQ/eq_ratio_{n}` | 4 | float | `[2.0]` | `/Input_Channels/1/EQ/eq_ratio_1` |
| `/Input_Channels/{n}/EQ/eq_release_{n}` | 4 | float | `[0.30000001192092896]` | `/Input_Channels/1/EQ/eq_release_1` |
| `/Input_Channels/{n}/EQ/eq_symm_Q_{n}` | 4 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/EQ/eq_symm_Q_1` |
| `/Input_Channels/{n}/EQ/eq_thresh_{n}` | 4 | float | `[-36.0]` | `/Input_Channels/1/EQ/eq_thresh_1` |
| `/Input_Channels/{n}/Filters/hi_filter_freq` | 1 | float | `[2768.257568359375]` | `/Input_Channels/1/Filters/hi_filter_freq` |
| `/Input_Channels/{n}/Filters/hi_filter_in` | 1 | float (0/1 flag) | `[1.0]` | `/Input_Channels/1/Filters/hi_filter_in` |
| `/Input_Channels/{n}/Filters/lo_filter_freq` | 1 | float | `[45.078678131103516]` | `/Input_Channels/1/Filters/lo_filter_freq` |
| `/Input_Channels/{n}/Filters/lo_filter_in` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Filters/lo_filter_in` |
| `/Input_Channels/{n}/Group_Send/{n}/group` | 3 | float (0/1 flag) | `[1.0]` | `/Input_Channels/1/Group_Send/1/group` |
| `/Input_Channels/{n}/Insert/insert_A_analog_gain` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Insert/insert_A_analog_gain` |
| `/Input_Channels/{n}/Insert/insert_A_in` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Insert/insert_A_in` |
| `/Input_Channels/{n}/Insert/insert_A_input_pad` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Insert/insert_A_input_pad` |
| `/Input_Channels/{n}/Insert/insert_A_phantom` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Insert/insert_A_phantom` |
| `/Input_Channels/{n}/Insert/insert_B_analog_gain` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Insert/insert_B_analog_gain` |
| `/Input_Channels/{n}/Insert/insert_B_in` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Insert/insert_B_in` |
| `/Input_Channels/{n}/Insert/insert_B_input_pad` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Insert/insert_B_input_pad` |
| `/Input_Channels/{n}/Insert/insert_B_phantom` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Insert/insert_B_phantom` |
| `/Input_Channels/{n}/Output/meter/LFE` | 1 | none (meter/empty) | `[]` | `/Input_Channels/1/Output/meter/LFE` |
| `/Input_Channels/{n}/Output/meter/SL` | 1 | none (meter/empty) | `[]` | `/Input_Channels/1/Output/meter/SL` |
| `/Input_Channels/{n}/Output/meter/SR` | 1 | none (meter/empty) | `[]` | `/Input_Channels/1/Output/meter/SR` |
| `/Input_Channels/{n}/Output/meter/centre` | 1 | none (meter/empty) | `[]` | `/Input_Channels/1/Output/meter/centre` |
| `/Input_Channels/{n}/Output/meter/left` | 1 | none (meter/empty) | `[]` | `/Input_Channels/1/Output/meter/left` |
| `/Input_Channels/{n}/Output/meter/right` | 1 | none (meter/empty) | `[]` | `/Input_Channels/1/Output/meter/right` |
| `/Input_Channels/{n}/Output/meter2` | 1 | none (meter/empty) | `[]` | `/Input_Channels/1/Output/meter2` |
| `/Input_Channels/{n}/Output/meter4` | 1 | none (meter/empty) | `[]` | `/Input_Channels/1/Output/meter4` |
| `/Input_Channels/{n}/Panner/LFE_level` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Panner/LFE_level` |
| `/Input_Channels/{n}/Panner/LFE_off-only-all` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Panner/LFE_off-only-all` |
| `/Input_Channels/{n}/Panner/f-b` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Panner/f-b` |
| `/Input_Channels/{n}/Panner/pan` | 1 | float | `[0.5]` | `/Input_Channels/1/Panner/pan` |
| `/Input_Channels/{n}/fader` | 1 | float | `[-4.44444465637207]` | `/Input_Channels/1/fader` |
| `/Input_Channels/{n}/fader_meter/left` | 1 | none (meter/empty) | `[]` | `/Input_Channels/1/fader_meter/left` |
| `/Input_Channels/{n}/fader_meter/right` | 1 | none (meter/empty) | `[]` | `/Input_Channels/1/fader_meter/right` |
| `/Input_Channels/{n}/mute` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/mute` |
| `/Input_Channels/{n}/solo` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/solo` |

### Aux_Outputs

Aux/IEM sends ("Buss_Trim" wraps a mono/stereo aux bus). 30 on this console.

| Pattern | Count | Type | Sample value | Sample address |
|---|---|---|---|---|
| `/Aux_Outputs/{n}/Buss_Trim/name` | 1 | string | `["IEM 5"]` | `/Aux_Outputs/1/Buss_Trim/name` |
| `/Aux_Outputs/{n}/Buss_Trim/phase` | 1 | float (0/1 flag) | `[0.0]` | `/Aux_Outputs/1/Buss_Trim/phase` |
| `/Aux_Outputs/{n}/Buss_Trim/post_meter` | 1 | none (meter/empty) | `[]` | `/Aux_Outputs/1/Buss_Trim/post_meter` |
| `/Aux_Outputs/{n}/Buss_Trim/post_meter/right` | 1 | none (meter/empty) | `[]` | `/Aux_Outputs/1/Buss_Trim/post_meter/right` |
| `/Aux_Outputs/{n}/Buss_Trim/pre_meter` | 1 | none (meter/empty) | `[]` | `/Aux_Outputs/1/Buss_Trim/pre_meter` |
| `/Aux_Outputs/{n}/Buss_Trim/pre_meter/right` | 1 | none (meter/empty) | `[]` | `/Aux_Outputs/1/Buss_Trim/pre_meter/right` |
| `/Aux_Outputs/{n}/Buss_Trim/trim` | 1 | float (0/1 flag) | `[0.0]` | `/Aux_Outputs/1/Buss_Trim/trim` |
| `/Aux_Outputs/{n}/Buss_Trim/tube_meter/left` | 1 | none (meter/empty) | `[]` | `/Aux_Outputs/1/Buss_Trim/tube_meter/left` |
| `/Aux_Outputs/{n}/Buss_Trim/tube_meter/right` | 1 | none (meter/empty) | `[]` | `/Aux_Outputs/1/Buss_Trim/tube_meter/right` |
| `/Aux_Outputs/{n}/CGs_level` | 1 | float (0/1 flag) | `[0.0]` | `/Aux_Outputs/1/CGs_level` |
| `/Aux_Outputs/{n}/CGs_mute` | 1 | float (0/1 flag) | `[0.0]` | `/Aux_Outputs/1/CGs_mute` |
| `/Aux_Outputs/{n}/Channel_Delay/delay` | 1 | float (0/1 flag) | `[0.0]` | `/Aux_Outputs/1/Channel_Delay/delay` |
| `/Aux_Outputs/{n}/Channel_Delay/delay_on` | 1 | float (0/1 flag) | `[0.0]` | `/Aux_Outputs/1/Channel_Delay/delay_on` |
| `/Aux_Outputs/{n}/Channel_Delay/fine_delay` | 1 | float (0/1 flag) | `[0.0]` | `/Aux_Outputs/1/Channel_Delay/fine_delay` |
| `/Aux_Outputs/{n}/Dynamics/GR_meter_{n}` | 4 | none (meter/empty) | `[]` | `/Aux_Outputs/1/Dynamics/GR_meter_1` |
| `/Aux_Outputs/{n}/Dynamics/comp-multiband-desser` | 1 | float (0/1 flag) | `[0.0]` | `/Aux_Outputs/1/Dynamics/comp-multiband-desser` |
| `/Aux_Outputs/{n}/Dynamics/comp_HP_crossover` | 1 | float | `[1000.0]` | `/Aux_Outputs/1/Dynamics/comp_HP_crossover` |
| `/Aux_Outputs/{n}/Dynamics/comp_LP_crossover` | 1 | float | `[130.0]` | `/Aux_Outputs/1/Dynamics/comp_LP_crossover` |
| `/Aux_Outputs/{n}/Dynamics/comp_all_gain` | 1 | float (0/1 flag) | `[0.0]` | `/Aux_Outputs/1/Dynamics/comp_all_gain` |
| `/Aux_Outputs/{n}/Dynamics/comp_all_thresh` | 1 | float | `[-20.0]` | `/Aux_Outputs/1/Dynamics/comp_all_thresh` |
| `/Aux_Outputs/{n}/Dynamics/comp_attack_{n}` | 3 | float | `[0.009999999776482582]` | `/Aux_Outputs/1/Dynamics/comp_attack_1` |
| `/Aux_Outputs/{n}/Dynamics/comp_auto-gain_{n}` | 4 | float (0/1 flag) | `[0.0]` | `/Aux_Outputs/1/Dynamics/comp_auto-gain_1` |
| `/Aux_Outputs/{n}/Dynamics/comp_band_in_{n}` | 3 | float (0/1 flag) | `[1.0]` | `/Aux_Outputs/1/Dynamics/comp_band_in_1` |
| `/Aux_Outputs/{n}/Dynamics/comp_gain_{n}` | 4 | float (0/1 flag) | `[0.0]` | `/Aux_Outputs/1/Dynamics/comp_gain_1` |
| `/Aux_Outputs/{n}/Dynamics/comp_in` | 1 | float (0/1 flag) | `[0.0]` | `/Aux_Outputs/1/Dynamics/comp_in` |
| `/Aux_Outputs/{n}/Dynamics/comp_knee_{n}` | 4 | float (0/1 flag) | `[0.0]` | `/Aux_Outputs/1/Dynamics/comp_knee_1` |
| `/Aux_Outputs/{n}/Dynamics/comp_listen_{n}` | 3 | float (0/1 flag) | `[0.0]` | `/Aux_Outputs/1/Dynamics/comp_listen_1` |
| `/Aux_Outputs/{n}/Dynamics/comp_ratio_{n}` | 4 | float | `[3.0]` | `/Aux_Outputs/1/Dynamics/comp_ratio_1` |
| `/Aux_Outputs/{n}/Dynamics/comp_release_{n}` | 3 | float | `[0.5]` | `/Aux_Outputs/1/Dynamics/comp_release_1` |
| `/Aux_Outputs/{n}/Dynamics/comp_thresh_{n}` | 3 | float | `[-20.0]` | `/Aux_Outputs/1/Dynamics/comp_thresh_1` |
| `/Aux_Outputs/{n}/Dynamics/desser_centre_freq` | 1 | float | `[127.0]` | `/Aux_Outputs/1/Dynamics/desser_centre_freq` |
| `/Aux_Outputs/{n}/Dynamics/desser_freq_width` | 1 | float | `[255.0]` | `/Aux_Outputs/1/Dynamics/desser_freq_width` |
| `/Aux_Outputs/{n}/Dynamics/gate_attack` | 1 | float | `[0.0020000000949949026]` | `/Aux_Outputs/1/Dynamics/gate_attack` |
| `/Aux_Outputs/{n}/Dynamics/gate_centre_freq` | 1 | float | `[127.0]` | `/Aux_Outputs/1/Dynamics/gate_centre_freq` |
| `/Aux_Outputs/{n}/Dynamics/gate_freq_width` | 1 | float | `[255.0]` | `/Aux_Outputs/1/Dynamics/gate_freq_width` |
| `/Aux_Outputs/{n}/Dynamics/gate_in` | 1 | float (0/1 flag) | `[0.0]` | `/Aux_Outputs/1/Dynamics/gate_in` |
| `/Aux_Outputs/{n}/Dynamics/gate_release` | 1 | float | `[0.14000000059604645]` | `/Aux_Outputs/1/Dynamics/gate_release` |
| `/Aux_Outputs/{n}/Dynamics/gate_thresh` | 1 | float | `[-20.0]` | `/Aux_Outputs/1/Dynamics/gate_thresh` |
| `/Aux_Outputs/{n}/Dynamics/input_meter/left` | 1 | none (meter/empty) | `[]` | `/Aux_Outputs/1/Dynamics/input_meter/left` |
| `/Aux_Outputs/{n}/Dynamics/input_meter/right` | 1 | none (meter/empty) | `[]` | `/Aux_Outputs/1/Dynamics/input_meter/right` |
| `/Aux_Outputs/{n}/Dynamics/key_solo` | 1 | float (0/1 flag) | `[0.0]` | `/Aux_Outputs/1/Dynamics/key_solo` |
| `/Aux_Outputs/{n}/EQ/GR_meter_{n}` | 4 | none (meter/empty) | `[]` | `/Aux_Outputs/1/EQ/GR_meter_1` |
| `/Aux_Outputs/{n}/EQ/dynamic_eq_on_{n}` | 4 | float (0/1 flag) | `[0.0]` | `/Aux_Outputs/1/EQ/dynamic_eq_on_1` |
| `/Aux_Outputs/{n}/EQ/eq_Q_{n}` | 8 | float | `[0.7099999785423279]` | `/Aux_Outputs/1/EQ/eq_Q_1` |
| `/Aux_Outputs/{n}/EQ/eq_attack_{n}` | 4 | float | `[0.009999999776482582]` | `/Aux_Outputs/1/EQ/eq_attack_1` |
| `/Aux_Outputs/{n}/EQ/eq_curve_{n}` | 8 | float (0/1 flag) | `[1.0]` | `/Aux_Outputs/1/EQ/eq_curve_1` |
| `/Aux_Outputs/{n}/EQ/eq_freq_{n}` | 8 | float | `[8000.0]` | `/Aux_Outputs/1/EQ/eq_freq_1` |
| `/Aux_Outputs/{n}/EQ/eq_gain_{n}` | 8 | float (0/1 flag) | `[0.0]` | `/Aux_Outputs/1/EQ/eq_gain_1` |
| `/Aux_Outputs/{n}/EQ/eq_in` | 1 | float (0/1 flag) | `[0.0]` | `/Aux_Outputs/1/EQ/eq_in` |
| `/Aux_Outputs/{n}/EQ/eq_on_{n}` | 8 | float (0/1 flag) | `[1.0]` | `/Aux_Outputs/1/EQ/eq_on_1` |
| `/Aux_Outputs/{n}/EQ/eq_over-under_{n}` | 4 | float (0/1 flag) | `[0.0]` | `/Aux_Outputs/1/EQ/eq_over-under_1` |
| `/Aux_Outputs/{n}/EQ/eq_pre-ins` | 1 | float (0/1 flag) | `[0.0]` | `/Aux_Outputs/1/EQ/eq_pre-ins` |
| `/Aux_Outputs/{n}/EQ/eq_ratio_{n}` | 4 | float | `[2.0]` | `/Aux_Outputs/1/EQ/eq_ratio_1` |
| `/Aux_Outputs/{n}/EQ/eq_release_{n}` | 4 | float | `[0.30000001192092896]` | `/Aux_Outputs/1/EQ/eq_release_1` |
| `/Aux_Outputs/{n}/EQ/eq_symm_Q_{n}` | 8 | float (0/1 flag) | `[0.0]` | `/Aux_Outputs/1/EQ/eq_symm_Q_1` |
| `/Aux_Outputs/{n}/EQ/eq_thresh_{n}` | 4 | float | `[-36.0]` | `/Aux_Outputs/1/EQ/eq_thresh_1` |
| `/Aux_Outputs/{n}/Insert/insert_A_in` | 1 | float (0/1 flag) | `[0.0]` | `/Aux_Outputs/1/Insert/insert_A_in` |
| `/Aux_Outputs/{n}/Insert/insert_B_in` | 1 | float (0/1 flag) | `[0.0]` | `/Aux_Outputs/1/Insert/insert_B_in` |
| `/Aux_Outputs/{n}/Output/dir_meter/left` | 1 | none (meter/empty) | `[]` | `/Aux_Outputs/1/Output/dir_meter/left` |
| `/Aux_Outputs/{n}/Output/dir_meter/right` | 1 | none (meter/empty) | `[]` | `/Aux_Outputs/1/Output/dir_meter/right` |
| `/Aux_Outputs/{n}/Output/meter/LFE` | 1 | none (meter/empty) | `[]` | `/Aux_Outputs/1/Output/meter/LFE` |
| `/Aux_Outputs/{n}/Output/meter/SL` | 1 | none (meter/empty) | `[]` | `/Aux_Outputs/1/Output/meter/SL` |
| `/Aux_Outputs/{n}/Output/meter/SR` | 1 | none (meter/empty) | `[]` | `/Aux_Outputs/1/Output/meter/SR` |
| `/Aux_Outputs/{n}/Output/meter/centre` | 1 | none (meter/empty) | `[]` | `/Aux_Outputs/1/Output/meter/centre` |
| `/Aux_Outputs/{n}/Output/meter/left` | 1 | none (meter/empty) | `[]` | `/Aux_Outputs/1/Output/meter/left` |
| `/Aux_Outputs/{n}/Output/meter/right` | 1 | none (meter/empty) | `[]` | `/Aux_Outputs/1/Output/meter/right` |
| `/Aux_Outputs/{n}/Output/meter2` | 1 | none (meter/empty) | `[]` | `/Aux_Outputs/1/Output/meter2` |
| `/Aux_Outputs/{n}/Output/meter4` | 1 | none (meter/empty) | `[]` | `/Aux_Outputs/1/Output/meter4` |
| `/Aux_Outputs/{n}/alternate_solo` | 1 | float (0/1 flag) | `[0.0]` | `/Aux_Outputs/1/alternate_solo` |
| `/Aux_Outputs/{n}/auto_solo` | 1 | float (0/1 flag) | `[0.0]` | `/Aux_Outputs/1/auto_solo` |
| `/Aux_Outputs/{n}/fader` | 1 | float | `[0.17724137008190155]` | `/Aux_Outputs/1/fader` |
| `/Aux_Outputs/{n}/fader_meter/left` | 1 | none (meter/empty) | `[]` | `/Aux_Outputs/1/fader_meter/left` |
| `/Aux_Outputs/{n}/fader_meter/right` | 1 | none (meter/empty) | `[]` | `/Aux_Outputs/1/fader_meter/right` |
| `/Aux_Outputs/{n}/hard_mute` | 1 | float (0/1 flag) | `[0.0]` | `/Aux_Outputs/1/hard_mute` |
| `/Aux_Outputs/{n}/mute` | 1 | float (0/1 flag) | `[1.0]` | `/Aux_Outputs/1/mute` |
| `/Aux_Outputs/{n}/sends_to_faders` | 1 | float (0/1 flag) | `[0.0]` | `/Aux_Outputs/1/sends_to_faders` |
| `/Aux_Outputs/{n}/sends_to_rotaries` | 1 | float (0/1 flag) | `[0.0]` | `/Aux_Outputs/1/sends_to_rotaries` |
| `/Aux_Outputs/{n}/solo` | 1 | float (0/1 flag) | `[0.0]` | `/Aux_Outputs/1/solo` |
| `/Aux_Outputs/{n}/solo_1_or_{n}` | 1 | float (0/1 flag) | `[0.0]` | `/Aux_Outputs/1/solo_1_or_2` |

### Group_Outputs

Subgroup/master busses (e.g. "MASTER"). Only 3 exist, but each carries a full processing chain plus sends to every aux and group.

| Pattern | Count | Type | Sample value | Sample address |
|---|---|---|---|---|
| `/Group_Outputs/{n}/Aux_Send/{n}/send_level` | 30 | float | `[-150.0]` | `/Group_Outputs/1/Aux_Send/1/send_level` |
| `/Group_Outputs/{n}/Aux_Send/{n}/send_on` | 30 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/Aux_Send/1/send_on` |
| `/Group_Outputs/{n}/Aux_Send/{n}/send_pan` | 30 | float | `[0.5]` | `/Group_Outputs/1/Aux_Send/1/send_pan` |
| `/Group_Outputs/{n}/Aux_Send/{n}/send_pre-post` | 30 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/Aux_Send/1/send_pre-post` |
| `/Group_Outputs/{n}/Buss_Trim/name` | 1 | string | `["MASTER"]` | `/Group_Outputs/1/Buss_Trim/name` |
| `/Group_Outputs/{n}/Buss_Trim/phase` | 1 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/Buss_Trim/phase` |
| `/Group_Outputs/{n}/Buss_Trim/post_meter` | 1 | none (meter/empty) | `[]` | `/Group_Outputs/1/Buss_Trim/post_meter` |
| `/Group_Outputs/{n}/Buss_Trim/post_meter/right` | 1 | none (meter/empty) | `[]` | `/Group_Outputs/1/Buss_Trim/post_meter/right` |
| `/Group_Outputs/{n}/Buss_Trim/pre_meter` | 1 | none (meter/empty) | `[]` | `/Group_Outputs/1/Buss_Trim/pre_meter` |
| `/Group_Outputs/{n}/Buss_Trim/pre_meter/right` | 1 | none (meter/empty) | `[]` | `/Group_Outputs/1/Buss_Trim/pre_meter/right` |
| `/Group_Outputs/{n}/Buss_Trim/trim` | 1 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/Buss_Trim/trim` |
| `/Group_Outputs/{n}/Buss_Trim/tube_meter/left` | 1 | none (meter/empty) | `[]` | `/Group_Outputs/1/Buss_Trim/tube_meter/left` |
| `/Group_Outputs/{n}/Buss_Trim/tube_meter/right` | 1 | none (meter/empty) | `[]` | `/Group_Outputs/1/Buss_Trim/tube_meter/right` |
| `/Group_Outputs/{n}/CGs_level` | 1 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/CGs_level` |
| `/Group_Outputs/{n}/CGs_mute` | 1 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/CGs_mute` |
| `/Group_Outputs/{n}/Channel_Delay/delay` | 1 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/Channel_Delay/delay` |
| `/Group_Outputs/{n}/Channel_Delay/delay_on` | 1 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/Channel_Delay/delay_on` |
| `/Group_Outputs/{n}/Channel_Delay/fine_delay` | 1 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/Channel_Delay/fine_delay` |
| `/Group_Outputs/{n}/Dynamics/GR_meter_{n}` | 4 | none (meter/empty) | `[]` | `/Group_Outputs/1/Dynamics/GR_meter_1` |
| `/Group_Outputs/{n}/Dynamics/comp-multiband-desser` | 1 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/Dynamics/comp-multiband-desser` |
| `/Group_Outputs/{n}/Dynamics/comp_HP_crossover` | 1 | float | `[1000.0]` | `/Group_Outputs/1/Dynamics/comp_HP_crossover` |
| `/Group_Outputs/{n}/Dynamics/comp_LP_crossover` | 1 | float | `[130.0]` | `/Group_Outputs/1/Dynamics/comp_LP_crossover` |
| `/Group_Outputs/{n}/Dynamics/comp_all_gain` | 1 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/Dynamics/comp_all_gain` |
| `/Group_Outputs/{n}/Dynamics/comp_all_thresh` | 1 | float | `[-20.0]` | `/Group_Outputs/1/Dynamics/comp_all_thresh` |
| `/Group_Outputs/{n}/Dynamics/comp_attack_{n}` | 3 | float | `[0.009999999776482582]` | `/Group_Outputs/1/Dynamics/comp_attack_1` |
| `/Group_Outputs/{n}/Dynamics/comp_auto-gain_{n}` | 4 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/Dynamics/comp_auto-gain_1` |
| `/Group_Outputs/{n}/Dynamics/comp_band_in_{n}` | 3 | float (0/1 flag) | `[1.0]` | `/Group_Outputs/1/Dynamics/comp_band_in_1` |
| `/Group_Outputs/{n}/Dynamics/comp_gain_{n}` | 4 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/Dynamics/comp_gain_1` |
| `/Group_Outputs/{n}/Dynamics/comp_in` | 1 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/Dynamics/comp_in` |
| `/Group_Outputs/{n}/Dynamics/comp_knee_{n}` | 4 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/Dynamics/comp_knee_1` |
| `/Group_Outputs/{n}/Dynamics/comp_listen_{n}` | 3 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/Dynamics/comp_listen_1` |
| `/Group_Outputs/{n}/Dynamics/comp_ratio_{n}` | 4 | float | `[3.0]` | `/Group_Outputs/1/Dynamics/comp_ratio_1` |
| `/Group_Outputs/{n}/Dynamics/comp_release_{n}` | 3 | float | `[0.5]` | `/Group_Outputs/1/Dynamics/comp_release_1` |
| `/Group_Outputs/{n}/Dynamics/comp_thresh_{n}` | 3 | float | `[-20.0]` | `/Group_Outputs/1/Dynamics/comp_thresh_1` |
| `/Group_Outputs/{n}/Dynamics/desser_centre_freq` | 1 | float | `[127.0]` | `/Group_Outputs/1/Dynamics/desser_centre_freq` |
| `/Group_Outputs/{n}/Dynamics/desser_freq_width` | 1 | float | `[255.0]` | `/Group_Outputs/1/Dynamics/desser_freq_width` |
| `/Group_Outputs/{n}/Dynamics/gate_attack` | 1 | float | `[0.0020000000949949026]` | `/Group_Outputs/1/Dynamics/gate_attack` |
| `/Group_Outputs/{n}/Dynamics/gate_centre_freq` | 1 | float | `[127.0]` | `/Group_Outputs/1/Dynamics/gate_centre_freq` |
| `/Group_Outputs/{n}/Dynamics/gate_freq_width` | 1 | float | `[255.0]` | `/Group_Outputs/1/Dynamics/gate_freq_width` |
| `/Group_Outputs/{n}/Dynamics/gate_in` | 1 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/Dynamics/gate_in` |
| `/Group_Outputs/{n}/Dynamics/gate_release` | 1 | float | `[0.14000000059604645]` | `/Group_Outputs/1/Dynamics/gate_release` |
| `/Group_Outputs/{n}/Dynamics/gate_thresh` | 1 | float | `[-20.0]` | `/Group_Outputs/1/Dynamics/gate_thresh` |
| `/Group_Outputs/{n}/Dynamics/input_meter/left` | 1 | none (meter/empty) | `[]` | `/Group_Outputs/1/Dynamics/input_meter/left` |
| `/Group_Outputs/{n}/Dynamics/input_meter/right` | 1 | none (meter/empty) | `[]` | `/Group_Outputs/1/Dynamics/input_meter/right` |
| `/Group_Outputs/{n}/Dynamics/key_solo` | 1 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/Dynamics/key_solo` |
| `/Group_Outputs/{n}/EQ/GR_meter_{n}` | 4 | none (meter/empty) | `[]` | `/Group_Outputs/1/EQ/GR_meter_1` |
| `/Group_Outputs/{n}/EQ/dynamic_eq_on_{n}` | 4 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/EQ/dynamic_eq_on_1` |
| `/Group_Outputs/{n}/EQ/eq_Q_{n}` | 8 | float | `[0.7099999785423279]` | `/Group_Outputs/1/EQ/eq_Q_1` |
| `/Group_Outputs/{n}/EQ/eq_attack_{n}` | 4 | float | `[0.009999999776482582]` | `/Group_Outputs/1/EQ/eq_attack_1` |
| `/Group_Outputs/{n}/EQ/eq_curve_{n}` | 8 | float (0/1 flag) | `[1.0]` | `/Group_Outputs/1/EQ/eq_curve_1` |
| `/Group_Outputs/{n}/EQ/eq_freq_{n}` | 8 | float | `[8000.0]` | `/Group_Outputs/1/EQ/eq_freq_1` |
| `/Group_Outputs/{n}/EQ/eq_gain_{n}` | 8 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/EQ/eq_gain_1` |
| `/Group_Outputs/{n}/EQ/eq_in` | 1 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/EQ/eq_in` |
| `/Group_Outputs/{n}/EQ/eq_on_{n}` | 8 | float (0/1 flag) | `[1.0]` | `/Group_Outputs/1/EQ/eq_on_1` |
| `/Group_Outputs/{n}/EQ/eq_over-under_{n}` | 4 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/EQ/eq_over-under_1` |
| `/Group_Outputs/{n}/EQ/eq_pre-ins` | 1 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/EQ/eq_pre-ins` |
| `/Group_Outputs/{n}/EQ/eq_ratio_{n}` | 4 | float | `[2.0]` | `/Group_Outputs/1/EQ/eq_ratio_1` |
| `/Group_Outputs/{n}/EQ/eq_release_{n}` | 4 | float | `[0.30000001192092896]` | `/Group_Outputs/1/EQ/eq_release_1` |
| `/Group_Outputs/{n}/EQ/eq_symm_Q_{n}` | 8 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/EQ/eq_symm_Q_1` |
| `/Group_Outputs/{n}/EQ/eq_thresh_{n}` | 4 | float | `[-36.0]` | `/Group_Outputs/1/EQ/eq_thresh_1` |
| `/Group_Outputs/{n}/Group_Send/{n}/group` | 3 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/Group_Send/1/group` |
| `/Group_Outputs/{n}/Insert/insert_A_in` | 1 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/Insert/insert_A_in` |
| `/Group_Outputs/{n}/Insert/insert_B_in` | 1 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/Insert/insert_B_in` |
| `/Group_Outputs/{n}/Output/dir_meter/left` | 1 | none (meter/empty) | `[]` | `/Group_Outputs/1/Output/dir_meter/left` |
| `/Group_Outputs/{n}/Output/dir_meter/right` | 1 | none (meter/empty) | `[]` | `/Group_Outputs/1/Output/dir_meter/right` |
| `/Group_Outputs/{n}/Output/meter/LFE` | 1 | none (meter/empty) | `[]` | `/Group_Outputs/1/Output/meter/LFE` |
| `/Group_Outputs/{n}/Output/meter/SL` | 1 | none (meter/empty) | `[]` | `/Group_Outputs/1/Output/meter/SL` |
| `/Group_Outputs/{n}/Output/meter/SR` | 1 | none (meter/empty) | `[]` | `/Group_Outputs/1/Output/meter/SR` |
| `/Group_Outputs/{n}/Output/meter/centre` | 1 | none (meter/empty) | `[]` | `/Group_Outputs/1/Output/meter/centre` |
| `/Group_Outputs/{n}/Output/meter/left` | 1 | none (meter/empty) | `[]` | `/Group_Outputs/1/Output/meter/left` |
| `/Group_Outputs/{n}/Output/meter/right` | 1 | none (meter/empty) | `[]` | `/Group_Outputs/1/Output/meter/right` |
| `/Group_Outputs/{n}/Output/meter2` | 1 | none (meter/empty) | `[]` | `/Group_Outputs/1/Output/meter2` |
| `/Group_Outputs/{n}/Output/meter4` | 1 | none (meter/empty) | `[]` | `/Group_Outputs/1/Output/meter4` |
| `/Group_Outputs/{n}/alternate_solo` | 1 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/alternate_solo` |
| `/Group_Outputs/{n}/auto_solo` | 1 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/auto_solo` |
| `/Group_Outputs/{n}/fader` | 1 | float | `[-150.0]` | `/Group_Outputs/1/fader` |
| `/Group_Outputs/{n}/fader_meter/left` | 1 | none (meter/empty) | `[]` | `/Group_Outputs/1/fader_meter/left` |
| `/Group_Outputs/{n}/fader_meter/right` | 1 | none (meter/empty) | `[]` | `/Group_Outputs/1/fader_meter/right` |
| `/Group_Outputs/{n}/hard_mute` | 1 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/hard_mute` |
| `/Group_Outputs/{n}/mute` | 1 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/mute` |
| `/Group_Outputs/{n}/sends_to_faders` | 1 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/sends_to_faders` |
| `/Group_Outputs/{n}/sends_to_rotaries` | 1 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/sends_to_rotaries` |
| `/Group_Outputs/{n}/solo` | 1 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/solo` |
| `/Group_Outputs/{n}/solo_1_or_{n}` | 1 | float (0/1 flag) | `[0.0]` | `/Group_Outputs/1/solo_1_or_2` |

### Matrix_Inputs

Summing feeds into the matrix section - lighter parameter set (just sends to Matrix_Outputs), no channel processing of their own.

| Pattern | Count | Type | Sample value | Sample address |
|---|---|---|---|---|
| `/Matrix_Inputs/{n}/Channel_Input/name` | 1 | string | `["Master L"]` | `/Matrix_Inputs/1/Channel_Input/name` |
| `/Matrix_Inputs/{n}/Matrix_Send/{n}/send_level` | 12 | float (0/1 flag) | `[0.0]` | `/Matrix_Inputs/1/Matrix_Send/1/send_level` |
| `/Matrix_Inputs/{n}/Matrix_Send/{n}/send_on` | 12 | float (0/1 flag) | `[1.0]` | `/Matrix_Inputs/1/Matrix_Send/1/send_on` |

### Matrix_Outputs

Matrix output busses - full processing chain like Group_Outputs.

| Pattern | Count | Type | Sample value | Sample address |
|---|---|---|---|---|
| `/Matrix_Outputs/{n}/Buss_Trim/name` | 1 | string | `["MAIN L"]` | `/Matrix_Outputs/1/Buss_Trim/name` |
| `/Matrix_Outputs/{n}/Buss_Trim/phase` | 1 | float (0/1 flag) | `[0.0]` | `/Matrix_Outputs/1/Buss_Trim/phase` |
| `/Matrix_Outputs/{n}/Buss_Trim/post_meter` | 1 | none (meter/empty) | `[]` | `/Matrix_Outputs/1/Buss_Trim/post_meter` |
| `/Matrix_Outputs/{n}/Buss_Trim/post_meter/right` | 1 | none (meter/empty) | `[]` | `/Matrix_Outputs/1/Buss_Trim/post_meter/right` |
| `/Matrix_Outputs/{n}/Buss_Trim/pre_meter` | 1 | none (meter/empty) | `[]` | `/Matrix_Outputs/1/Buss_Trim/pre_meter` |
| `/Matrix_Outputs/{n}/Buss_Trim/pre_meter/right` | 1 | none (meter/empty) | `[]` | `/Matrix_Outputs/1/Buss_Trim/pre_meter/right` |
| `/Matrix_Outputs/{n}/Buss_Trim/trim` | 1 | float (0/1 flag) | `[0.0]` | `/Matrix_Outputs/1/Buss_Trim/trim` |
| `/Matrix_Outputs/{n}/Buss_Trim/tube_meter/left` | 1 | none (meter/empty) | `[]` | `/Matrix_Outputs/1/Buss_Trim/tube_meter/left` |
| `/Matrix_Outputs/{n}/Buss_Trim/tube_meter/right` | 1 | none (meter/empty) | `[]` | `/Matrix_Outputs/1/Buss_Trim/tube_meter/right` |
| `/Matrix_Outputs/{n}/CGs_level` | 1 | float (0/1 flag) | `[0.0]` | `/Matrix_Outputs/1/CGs_level` |
| `/Matrix_Outputs/{n}/CGs_mute` | 1 | float (0/1 flag) | `[0.0]` | `/Matrix_Outputs/1/CGs_mute` |
| `/Matrix_Outputs/{n}/Channel_Delay/delay` | 1 | float (0/1 flag) | `[0.0]` | `/Matrix_Outputs/1/Channel_Delay/delay` |
| `/Matrix_Outputs/{n}/Channel_Delay/delay_on` | 1 | float (0/1 flag) | `[0.0]` | `/Matrix_Outputs/1/Channel_Delay/delay_on` |
| `/Matrix_Outputs/{n}/Channel_Delay/fine_delay` | 1 | float (0/1 flag) | `[0.0]` | `/Matrix_Outputs/1/Channel_Delay/fine_delay` |
| `/Matrix_Outputs/{n}/Dynamics/GR_meter_{n}` | 4 | none (meter/empty) | `[]` | `/Matrix_Outputs/1/Dynamics/GR_meter_1` |
| `/Matrix_Outputs/{n}/Dynamics/comp-multiband-desser` | 1 | float (0/1 flag) | `[0.0]` | `/Matrix_Outputs/1/Dynamics/comp-multiband-desser` |
| `/Matrix_Outputs/{n}/Dynamics/comp_HP_crossover` | 1 | float | `[1000.0]` | `/Matrix_Outputs/1/Dynamics/comp_HP_crossover` |
| `/Matrix_Outputs/{n}/Dynamics/comp_LP_crossover` | 1 | float | `[130.0]` | `/Matrix_Outputs/1/Dynamics/comp_LP_crossover` |
| `/Matrix_Outputs/{n}/Dynamics/comp_all_gain` | 1 | float (0/1 flag) | `[0.0]` | `/Matrix_Outputs/1/Dynamics/comp_all_gain` |
| `/Matrix_Outputs/{n}/Dynamics/comp_all_thresh` | 1 | float | `[-16.235252380371094]` | `/Matrix_Outputs/1/Dynamics/comp_all_thresh` |
| `/Matrix_Outputs/{n}/Dynamics/comp_attack_{n}` | 3 | float | `[0.009999999776482582]` | `/Matrix_Outputs/1/Dynamics/comp_attack_1` |
| `/Matrix_Outputs/{n}/Dynamics/comp_auto-gain_{n}` | 4 | float (0/1 flag) | `[0.0]` | `/Matrix_Outputs/1/Dynamics/comp_auto-gain_1` |
| `/Matrix_Outputs/{n}/Dynamics/comp_band_in_{n}` | 3 | float (0/1 flag) | `[1.0]` | `/Matrix_Outputs/1/Dynamics/comp_band_in_1` |
| `/Matrix_Outputs/{n}/Dynamics/comp_gain_{n}` | 4 | float (0/1 flag) | `[0.0]` | `/Matrix_Outputs/1/Dynamics/comp_gain_1` |
| `/Matrix_Outputs/{n}/Dynamics/comp_in` | 1 | float (0/1 flag) | `[0.0]` | `/Matrix_Outputs/1/Dynamics/comp_in` |
| `/Matrix_Outputs/{n}/Dynamics/comp_knee_{n}` | 4 | float (0/1 flag) | `[0.0]` | `/Matrix_Outputs/1/Dynamics/comp_knee_1` |
| `/Matrix_Outputs/{n}/Dynamics/comp_listen_{n}` | 3 | float (0/1 flag) | `[0.0]` | `/Matrix_Outputs/1/Dynamics/comp_listen_1` |
| `/Matrix_Outputs/{n}/Dynamics/comp_ratio_{n}` | 4 | float | `[3.0]` | `/Matrix_Outputs/1/Dynamics/comp_ratio_1` |
| `/Matrix_Outputs/{n}/Dynamics/comp_release_{n}` | 3 | float | `[0.5]` | `/Matrix_Outputs/1/Dynamics/comp_release_1` |
| `/Matrix_Outputs/{n}/Dynamics/comp_thresh_{n}` | 3 | float | `[-16.235252380371094]` | `/Matrix_Outputs/1/Dynamics/comp_thresh_1` |
| `/Matrix_Outputs/{n}/Dynamics/desser_centre_freq` | 1 | float | `[127.0]` | `/Matrix_Outputs/1/Dynamics/desser_centre_freq` |
| `/Matrix_Outputs/{n}/Dynamics/desser_freq_width` | 1 | float | `[255.0]` | `/Matrix_Outputs/1/Dynamics/desser_freq_width` |
| `/Matrix_Outputs/{n}/Dynamics/gate_attack` | 1 | float | `[0.0020000000949949026]` | `/Matrix_Outputs/1/Dynamics/gate_attack` |
| `/Matrix_Outputs/{n}/Dynamics/gate_centre_freq` | 1 | float | `[127.0]` | `/Matrix_Outputs/1/Dynamics/gate_centre_freq` |
| `/Matrix_Outputs/{n}/Dynamics/gate_freq_width` | 1 | float | `[255.0]` | `/Matrix_Outputs/1/Dynamics/gate_freq_width` |
| `/Matrix_Outputs/{n}/Dynamics/gate_in` | 1 | float (0/1 flag) | `[0.0]` | `/Matrix_Outputs/1/Dynamics/gate_in` |
| `/Matrix_Outputs/{n}/Dynamics/gate_release` | 1 | float | `[0.14000000059604645]` | `/Matrix_Outputs/1/Dynamics/gate_release` |
| `/Matrix_Outputs/{n}/Dynamics/gate_thresh` | 1 | float | `[-20.0]` | `/Matrix_Outputs/1/Dynamics/gate_thresh` |
| `/Matrix_Outputs/{n}/Dynamics/input_meter/left` | 1 | none (meter/empty) | `[]` | `/Matrix_Outputs/1/Dynamics/input_meter/left` |
| `/Matrix_Outputs/{n}/Dynamics/input_meter/right` | 1 | none (meter/empty) | `[]` | `/Matrix_Outputs/1/Dynamics/input_meter/right` |
| `/Matrix_Outputs/{n}/Dynamics/key_solo` | 1 | float (0/1 flag) | `[0.0]` | `/Matrix_Outputs/1/Dynamics/key_solo` |
| `/Matrix_Outputs/{n}/EQ/GR_meter_{n}` | 4 | none (meter/empty) | `[]` | `/Matrix_Outputs/1/EQ/GR_meter_1` |
| `/Matrix_Outputs/{n}/EQ/dynamic_eq_on_{n}` | 4 | float (0/1 flag) | `[0.0]` | `/Matrix_Outputs/1/EQ/dynamic_eq_on_1` |
| `/Matrix_Outputs/{n}/EQ/eq_Q_{n}` | 8 | float | `[0.7099999785423279]` | `/Matrix_Outputs/1/EQ/eq_Q_1` |
| `/Matrix_Outputs/{n}/EQ/eq_attack_{n}` | 4 | float | `[0.009999999776482582]` | `/Matrix_Outputs/1/EQ/eq_attack_1` |
| `/Matrix_Outputs/{n}/EQ/eq_curve_{n}` | 8 | float (0/1 flag) | `[1.0]` | `/Matrix_Outputs/1/EQ/eq_curve_1` |
| `/Matrix_Outputs/{n}/EQ/eq_freq_{n}` | 8 | float | `[8000.0]` | `/Matrix_Outputs/1/EQ/eq_freq_1` |
| `/Matrix_Outputs/{n}/EQ/eq_gain_{n}` | 8 | float (0/1 flag) | `[0.0]` | `/Matrix_Outputs/1/EQ/eq_gain_1` |
| `/Matrix_Outputs/{n}/EQ/eq_in` | 1 | float (0/1 flag) | `[0.0]` | `/Matrix_Outputs/1/EQ/eq_in` |
| `/Matrix_Outputs/{n}/EQ/eq_on_{n}` | 8 | float (0/1 flag) | `[1.0]` | `/Matrix_Outputs/1/EQ/eq_on_1` |
| `/Matrix_Outputs/{n}/EQ/eq_over-under_{n}` | 4 | float (0/1 flag) | `[0.0]` | `/Matrix_Outputs/1/EQ/eq_over-under_1` |
| `/Matrix_Outputs/{n}/EQ/eq_pre-ins` | 1 | float (0/1 flag) | `[1.0]` | `/Matrix_Outputs/1/EQ/eq_pre-ins` |
| `/Matrix_Outputs/{n}/EQ/eq_ratio_{n}` | 4 | float | `[2.0]` | `/Matrix_Outputs/1/EQ/eq_ratio_1` |
| `/Matrix_Outputs/{n}/EQ/eq_release_{n}` | 4 | float | `[0.30000001192092896]` | `/Matrix_Outputs/1/EQ/eq_release_1` |
| `/Matrix_Outputs/{n}/EQ/eq_symm_Q_{n}` | 8 | float (0/1 flag) | `[0.0]` | `/Matrix_Outputs/1/EQ/eq_symm_Q_1` |
| `/Matrix_Outputs/{n}/EQ/eq_thresh_{n}` | 4 | float | `[-36.0]` | `/Matrix_Outputs/1/EQ/eq_thresh_1` |
| `/Matrix_Outputs/{n}/Insert/insert_A_in` | 1 | float (0/1 flag) | `[1.0]` | `/Matrix_Outputs/1/Insert/insert_A_in` |
| `/Matrix_Outputs/{n}/Insert/insert_B_in` | 1 | float (0/1 flag) | `[0.0]` | `/Matrix_Outputs/1/Insert/insert_B_in` |
| `/Matrix_Outputs/{n}/Output/dir_meter/left` | 1 | none (meter/empty) | `[]` | `/Matrix_Outputs/1/Output/dir_meter/left` |
| `/Matrix_Outputs/{n}/Output/dir_meter/right` | 1 | none (meter/empty) | `[]` | `/Matrix_Outputs/1/Output/dir_meter/right` |
| `/Matrix_Outputs/{n}/Output/meter/LFE` | 1 | none (meter/empty) | `[]` | `/Matrix_Outputs/1/Output/meter/LFE` |
| `/Matrix_Outputs/{n}/Output/meter/SL` | 1 | none (meter/empty) | `[]` | `/Matrix_Outputs/1/Output/meter/SL` |
| `/Matrix_Outputs/{n}/Output/meter/SR` | 1 | none (meter/empty) | `[]` | `/Matrix_Outputs/1/Output/meter/SR` |
| `/Matrix_Outputs/{n}/Output/meter/centre` | 1 | none (meter/empty) | `[]` | `/Matrix_Outputs/1/Output/meter/centre` |
| `/Matrix_Outputs/{n}/Output/meter/left` | 1 | none (meter/empty) | `[]` | `/Matrix_Outputs/1/Output/meter/left` |
| `/Matrix_Outputs/{n}/Output/meter/right` | 1 | none (meter/empty) | `[]` | `/Matrix_Outputs/1/Output/meter/right` |
| `/Matrix_Outputs/{n}/Output/meter2` | 1 | none (meter/empty) | `[]` | `/Matrix_Outputs/1/Output/meter2` |
| `/Matrix_Outputs/{n}/Output/meter4` | 1 | none (meter/empty) | `[]` | `/Matrix_Outputs/1/Output/meter4` |
| `/Matrix_Outputs/{n}/alternate_solo` | 1 | float (0/1 flag) | `[0.0]` | `/Matrix_Outputs/1/alternate_solo` |
| `/Matrix_Outputs/{n}/fader` | 1 | float (0/1 flag) | `[0.0]` | `/Matrix_Outputs/1/fader` |
| `/Matrix_Outputs/{n}/fader_meter/left` | 1 | none (meter/empty) | `[]` | `/Matrix_Outputs/1/fader_meter/left` |
| `/Matrix_Outputs/{n}/fader_meter/right` | 1 | none (meter/empty) | `[]` | `/Matrix_Outputs/1/fader_meter/right` |
| `/Matrix_Outputs/{n}/hard_mute` | 1 | float (0/1 flag) | `[0.0]` | `/Matrix_Outputs/1/hard_mute` |
| `/Matrix_Outputs/{n}/mute` | 1 | float (0/1 flag) | `[0.0]` | `/Matrix_Outputs/1/mute` |
| `/Matrix_Outputs/{n}/solo` | 1 | float (0/1 flag) | `[0.0]` | `/Matrix_Outputs/1/solo` |
| `/Matrix_Outputs/{n}/solo_1_or_{n}` | 1 | float (0/1 flag) | `[0.0]` | `/Matrix_Outputs/1/solo_1_or_2` |

### Control_Groups

DCA-style control/mute groups (e.g. "DRUMS DCA"). Simple: name, fader, mute, solo, mode, aux_send, auto-mute.

| Pattern | Count | Type | Sample value | Sample address |
|---|---|---|---|---|
| `/Control_Groups/{n}/auto-mute` | 1 | float (0/1 flag) | `[0.0]` | `/Control_Groups/1/auto-mute` |
| `/Control_Groups/{n}/aux_send` | 1 | float (0/1 flag) | `[0.0]` | `/Control_Groups/1/aux_send` |
| `/Control_Groups/{n}/fader` | 1 | float | `[0.5217241048812866]` | `/Control_Groups/1/fader` |
| `/Control_Groups/{n}/mode` | 1 | float (0/1 flag) | `[1.0]` | `/Control_Groups/1/mode` |
| `/Control_Groups/{n}/mute` | 1 | float (0/1 flag) | `[0.0]` | `/Control_Groups/1/mute` |
| `/Control_Groups/{n}/name` | 1 | string | `["DRUMS DCA"]` | `/Control_Groups/1/name` |
| `/Control_Groups/{n}/solo` | 1 | float (0/1 flag) | `[0.0]` | `/Control_Groups/1/solo` |

### Graphic_EQ

Assignable 31-band graphic EQs (geq_gain_1..32) that can be inserted on an output.

| Pattern | Count | Type | Sample value | Sample address |
|---|---|---|---|---|
| `/Graphic_EQ/{n}/geq__trim` | 1 | float (0/1 flag) | `[0.0]` | `/Graphic_EQ/1/geq__trim` |
| `/Graphic_EQ/{n}/geq_gain_{n}` | 32 | float (0/1 flag) | `[0.0]` | `/Graphic_EQ/1/geq_gain_1` |
| `/Graphic_EQ/{n}/geq_in` | 1 | float (0/1 flag) | `[1.0]` | `/Graphic_EQ/1/geq_in` |
| `/Graphic_EQ/{n}/input_meter` | 1 | none (meter/empty) | `[]` | `/Graphic_EQ/1/input_meter` |
| `/Graphic_EQ/{n}/name` | 1 | string | `[""]` | `/Graphic_EQ/1/name` |
| `/Graphic_EQ/{n}/output_meter` | 1 | none (meter/empty) | `[]` | `/Graphic_EQ/1/output_meter` |

### Multis

Multitrack recorder return channels (e.g. "FX"). Just fader/mute/solo/name.

| Pattern | Count | Type | Sample value | Sample address |
|---|---|---|---|---|
| `/Multis/{n}/fader` | 1 | float | `[0.693965494632721]` | `/Multis/1/fader` |
| `/Multis/{n}/mute` | 1 | float (0/1 flag) | `[0.0]` | `/Multis/1/mute` |
| `/Multis/{n}/name` | 1 | string | `["FX"]` | `/Multis/1/name` |
| `/Multis/{n}/solo` | 1 | float (0/1 flag) | `[0.0]` | `/Multis/1/solo` |

## Undocumented / not reachable this session

- **Whether `Channel_Input/input_type` can be written.** Read constantly by the official app, never written by it - see [Input patching](#input-patching-channel_inputinput_type). Untested rather than disproven: nobody has yet sent `input_type 2.0` to an unpatched channel to see whether the desk ignores it, refuses it, or does something surprising. Worth trying **on a scratch session, not a show file**, since a half-applied patch state is not something to discover during a soundcheck. Even a success would be of limited use - no address carries socket identity, so there is no way to say *which* socket to patch to.
- `/Macros/Buttons/?` - asked six times by the official app across two sessions, never answered once. Whatever the app wanted from it, it carried on without it. No address that *fires* a macro has been seen either, so macros are readable by name and nothing more.
- **Storing a snapshot.** Still the one gap that costs real time: a restore has to write a snapshot's settings to the live desk and then ask the operator to press Update. The app never stored a snapshot during this capture, so there was nothing to learn from it - the next capture worth taking is one where somebody does.
- `/Talkback_Outputs/{n}` - exists (count 2) per console topology, but no query form tried got a reply.
- `/Console/Session_Name`, `/Console/Show_File`, `/Console/Sample_Rate`, `/Console/Version`, `/Console/Type`, `/Console/Desk_Type` - guessed metadata addresses, none answered.
  **Resolved since:** the real session address is `/Console/Session/Filename` (not `/Console/Session`), found by capturing the official client rather than by guessing. Worth remembering as a method - guessing addresses found almost nothing here, while one capture of the real client resolved several at once.
- `/Snapshots/Count`, `/Snapshots/Total` - guessed; the real one is `/Snapshots/count` -> `[10]`.

## Provenance

| Date | Source | What it established |
|---|---|---|
| 2026-08-09 | Live OSC probing (send 1091 / recv 1090) | The 946-address command map above; console topology; GET/SET semantics |
| 2026-09-03 | Live probing (send 10025 / recv 10026) | Ports are console config, not constants; no keep-alive required; console ignores sender port; `/Snapshots/names/?` does reply |
| 2026-09-03 | `~/Documents/digico.pcapng` - official client, 92 s | Discovery beacon on 2029; `/Console/Name/?` handshake; 2.0 s keep-alive; meter subscription mechanism |
| 2026-09-03 | `~/Documents/sound sample.pcapng` - official client with live audio, 42 s | Meter value encoding: packed peak/RMS fields, 3 dB quantisation, `126` no-signal sentinel |
| 2026-09-03 | `~/Pictures/VID2026090321*.mp4` - phone video of the console's meters and CLMix side by side | Corrected the meter scale: a field is dB directly (`dB = -field`, 0..-60), not `-field/3` |
| 2026-09-20 | `digico-capture_2026-09-20_08-05-51.log` - official app via CLMix's own DiGiCo App Capture, 8 min, 13,119 datagrams | That a strip dump is not exhaustive (`gate_hold`, `gate_range`, `gate-duck-comp`); snapshot indices are 0-based; the four-message recall burst and `End_Recall_Snapshot`; `/Snapshots/name/?` by index; `/Macros/names/?`; the bulk `/Console` routing reads; that per-category counts answer only inside the `/Console/Channels/?` burst; meter subscriptions re-asserted ~1/s and running past 12 slots; that a string SET echoes even unchanged |

Anything marked "unidentified" above stayed unidentified because only one console was ever observed - constant fields may be constant by circumstance rather than by design.
