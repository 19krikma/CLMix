# Mixer OSC Protocol Reference

Reverse-engineered by live-probing the actual console at `10.5.20.242` on 2026-08-09 (send port 1091, recv port 1090 at that time), and extended on 2026-09-03 from live probing plus two packet captures of the **official DiGiCo client** talking to the same console (`digico.pcapng`, `sound sample.pcapng`).

> **The send/recv ports are console configuration, not protocol constants.** They have been observed as 1091/1090, then 800/900, then 10025/10026 on this same console. Never hardcode them; treat them as user settings (which is what CLMix already does). Console identifies itself as **SD7Q-Q2** - a DiGiCo SD7 Quantum (Quantum engine 2).

This document was generated from **946 concrete addresses** the console actually replied with, collapsed into **358 generalized command patterns**. See `commands.csv` in this folder for the full flat list (every concrete address + the live value it held at probe time).

## Transport / wire protocol

- **OSC 1.0 over UDP.** App sends to `mixer_ip:send_port`; the console replies to the app's bound `recv_port` - matches [`ui/main_window.py`](../../ui/main_window.py)'s `MixerWorker`.
- **The console ignores your source port entirely.** It only ever transmits to the destination port configured in its own External Control panel. Querying from an ephemeral socket gets you silence no matter how correct the address is - you must bind the exact `recv_port` the console is configured to send to.
- **The console transmits from an ephemeral source port of its own** (observed 65510 and 65511). It is stable for the life of the console's OSC engine but changes across console restarts, so never filter incoming packets by source port. `MixerWorker` discards the source address in `recvfrom`, which is correct.
- **Beware privileged ports on Linux.** A `recv_port` below 1024 cannot be bound without root and fails with `PermissionError`. Prefer configuring the console to a port above 1024.
- **GET a value:** send the address with `/?` appended, empty arg list, e.g. `/Input_Channels/1/Channel_Input/name/?`. The console replies with the same address (no `/?`) and the current value(s) as OSC args.
- **GET a whole channel strip in one shot:** append `/?` directly to a bare index path with *no* leaf, e.g. `/Input_Channels/1/?` or `/Aux_Outputs/1/?` - the console dumps **every** parameter under that strip as a burst of individual reply messages (that's how this whole document was built: one query per category, not hundreds of guesses).
- **SET a value:** send the bare address (no `/?`) with the new value as the single OSC arg, e.g. `/Input_Channels/1/mute 1.0`. Confirmed live: the console only echoes the address back to listeners when the value actually *changes* - setting a parameter to its current value produces no reply, so don't rely on a SET always producing a confirmation message.
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

Both fields are **always a multiple of 3**, and convert to dBFS as:

```
dB = -field / 3.0
```

giving a range of **0 dB down to -42 dB** (field `0` .. `126`).

| Field | Byte | Meaning | Evidence |
|---|---|---|---|
| `value >> 16` | high | **Peak**, with peak-hold behaviour | Changed on only 40 of 878 transitions for a busy channel |
| `value & 0xFF` | low | **RMS / instantaneous** level | Changed on 442 of the same 878 transitions |

Verified across 9,350 samples from a capture with real audio playing: every field was a multiple of 3, and peak was louder than or equal to RMS in **99.5%** of samples. The remaining 0.5% are all the same case - `peak == 126` (the floor sentinel) while RMS still reads a real level.

**`126` (i.e. -42 dB) is the floor / no-signal sentinel.** The distinct values observed jump from `20*3` straight to `42*3` with nothing between, so `126` is a sentinel rather than a genuine measurement. Treat a peak of 126 as "no signal" and render an empty meter.

### Worked example

```python
def decode_meter(value):
    """(peak_db, rms_db) from a /Meters/values int. None == no signal."""
    peak, rms = (value >> 16) & 0xFF, value & 0xFF
    return (None if peak == 126 else -peak / 3.0,
            None if rms  == 126 else -rms  / 3.0)

# /Meters/values [0, 1572897, ...]
decode_meter(1572897)   # 0x180021 -> (-8.0 dB peak, -11.0 dB rms)
decode_meter(8257662)   # 0x7E007E -> (None, None)  - silent
```

### Notes for CLMix

- Resolution is coarse: 1/3 dB steps over a 42 dB span, i.e. 43 discrete levels per field. That is plenty for a bar meter, but do not present it as a precise readout.
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

## Already used by CLMix today

For reference, these are the addresses `ui/main_window.py` already speaks - all confirmed live against this console during probing:

| Address pattern | Purpose |
|---|---|
| `/Console/Channels/?` | Boot: triggers the topology burst above |
| `/Console/Aux_Outputs/modes/?` | Boot: per-aux mono/stereo mode list |
| `/Aux_Outputs/{n}/Buss_Trim/name/?` | Aux bus display name |
| `/Input_Channels/{n}/Channel_Input/name/?` | Channel display name |
| `/Input_Channels/{n}/mute` (get/set) | Channel mute |
| `/Input_Channels/{n}/Aux_Send/{a}/send_level` (get/set) | Channel's send level to aux `a` |
| `/Input_Channels/{n}/Aux_Send/{a}/send_pan` (get/set) | Channel's send pan to aux `a` |
| `/Input_Channels/{n}/Aux_Send/{a}/send_on` (get/set) | Whether channel `n` is in aux `a`'s mix at all (`0.0` = out). What the phone apps' per-channel Mute button drives, since it affects only that one aux mix - unlike `/Input_Channels/{n}/mute` above, which cuts the source everywhere. |
| `/Snapshots/Current_Snapshot/?` | Currently recalled snapshot number |
| `/Snapshots/names/?` | Broadcasts `/Snapshots/name [index, name]` per snapshot |
| `/Snapshots/Rename_Snapshot/{n}` | Broadcast when a snapshot is renamed |
| `/Snapshots/Recall_Snapshot/{n}`, `/Snapshots/Change_Surface_Snapshot/{n}` | Broadcast on snapshot recall |
| `/Layout/Layout/Banks/?` | Custom surface bank layout (one reply per bank) |

Newly discovered below (channel EQ, dynamics, gate, delay, input gain/phantom/pad, routing to groups/matrix, aux/group/matrix bus processing, DCAs, graphic EQs, multitrack returns) is **not yet wired into the app** - it's everything else the console exposes.

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

`/Console/Session/Filename` is the currently loaded session file. The official client polls it every 2.0 s as its keep-alive - see [Connection lifecycle](#connection-lifecycle).

### Snapshots

Scene/snapshot recall and naming.

| Pattern | Count | Type | Sample value | Sample address |
|---|---|---|---|---|
| `/Snapshots/Current_Snapshot` | 1 | int | `[3]` | `/Snapshots/Current_Snapshot` |
| `/Snapshots/Surface_Snapshot` | 1 | int | `[13]` | `/Snapshots/Surface_Snapshot` |
| `/Snapshots/count` | 1 | int | `[10]` | `/Snapshots/count` |
| `/Snapshots/name` | per snapshot | `[index, cue, 0, name]` | `[13, 450, 0, "NATALIYA FILISTOVICH"]` | reply to `/Snapshots/names/?` |

### Layout

Custom surface bank layouts (which strips are assigned to which physical layer).

| Pattern | Count | Type | Sample value | Sample address |
|---|---|---|---|---|
| `/Layout/Layout/Banks` | 1 | string | `["CHOIR", "R", 2, 0, "Input_Channels"...` | `/Layout/Layout/Banks` |

### Input_Channels

Input channel strips (mic/line inputs). 72 on this console.

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
| `/Input_Channels/{n}/Channel_Input/analog_gain` | 1 | float | `[20.0]` | `/Input_Channels/1/Channel_Input/analog_gain` |
| `/Input_Channels/{n}/Channel_Input/input_pad` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Channel_Input/input_pad` |
| `/Input_Channels/{n}/Channel_Input/input_type` | 1 | float | `[2.0]` | `/Input_Channels/1/Channel_Input/input_type` |
| `/Input_Channels/{n}/Channel_Input/main/alt_in` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Channel_Input/main/alt_in` |
| `/Input_Channels/{n}/Channel_Input/name` | 1 | string | `["KICK"]` | `/Input_Channels/1/Channel_Input/name` |
| `/Input_Channels/{n}/Channel_Input/phantom` | 1 | float (0/1 flag) | `[1.0]` | `/Input_Channels/1/Channel_Input/phantom` |
| `/Input_Channels/{n}/Channel_Input/phase` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Channel_Input/phase` |
| `/Input_Channels/{n}/Channel_Input/post_meter/left` | 1 | none (meter/empty) | `[]` | `/Input_Channels/1/Channel_Input/post_meter/left` |
| `/Input_Channels/{n}/Channel_Input/post_meter/right` | 1 | none (meter/empty) | `[]` | `/Input_Channels/1/Channel_Input/post_meter/right` |
| `/Input_Channels/{n}/Channel_Input/pre_meter/left` | 1 | none (meter/empty) | `[]` | `/Input_Channels/1/Channel_Input/pre_meter/left` |
| `/Input_Channels/{n}/Channel_Input/pre_meter/right` | 1 | none (meter/empty) | `[]` | `/Input_Channels/1/Channel_Input/pre_meter/right` |
| `/Input_Channels/{n}/Channel_Input/stereo_mode` | 1 | float (0/1 flag) | `[1.0]` | `/Input_Channels/1/Channel_Input/stereo_mode` |
| `/Input_Channels/{n}/Channel_Input/trim` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Channel_Input/trim` |
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
| `/Input_Channels/{n}/Dynamics/gate_in` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Dynamics/gate_in` |
| `/Input_Channels/{n}/Dynamics/gate_release` | 1 | float | `[0.017051173374056816]` | `/Input_Channels/1/Dynamics/gate_release` |
| `/Input_Channels/{n}/Dynamics/gate_thresh` | 1 | float | `[-9.41171646118164]` | `/Input_Channels/1/Dynamics/gate_thresh` |
| `/Input_Channels/{n}/Dynamics/input_meter/left` | 1 | none (meter/empty) | `[]` | `/Input_Channels/1/Dynamics/input_meter/left` |
| `/Input_Channels/{n}/Dynamics/input_meter/right` | 1 | none (meter/empty) | `[]` | `/Input_Channels/1/Dynamics/input_meter/right` |
| `/Input_Channels/{n}/Dynamics/key_solo` | 1 | float (0/1 flag) | `[0.0]` | `/Input_Channels/1/Dynamics/key_solo` |
| `/Input_Channels/{n}/EQ/GR_meter_{n}` | 4 | none (meter/empty) | `[]` | `/Input_Channels/1/EQ/GR_meter_1` |
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
| 2026-09-03 | `~/Documents/sound sample.pcapng` - official client with live audio, 42 s | Meter value encoding: packed peak/RMS, 1/3 dB steps, -42 dB floor sentinel |

Anything marked "unidentified" above stayed unidentified because only one console was ever observed - constant fields may be constant by circumstance rather than by design.
