"""Decode a "DiGiCo OSC Commands" dictionary out of the official iPad app.

The app carries its own table of every OSC parameter it knows about, and
that table answers the one question the console itself will not: what a
parameter's limits are. docs/mixer_protocol/PROTOCOL.md had to establish
ranges by pushing values past the end and watching the desk pin them,
because there is no min/max address to ask. This file states them
outright, along with each parameter's unit and whether it is a switch, a
float, a meter or a string.

The blob is a dump of the app's in-memory table rather than a tidy
serialisation - stray 32-bit heap pointers sit between the fields, and
the ones here are simply skipped. Layout, per record:

    -24  u16   always 1
    -22  u16   parameter id (shared by parameters that share a slot -
               comp_thresh_1 and gate_thresh are both 1294, because the
               section is one engine wearing two labels)
    -20  u16   instance index, 0-based: eq_gain_1 is index 0
    -18  u8    always 1
    -17  u8    type - 0 switch/enum, 1 float, 3 meter, 4 string
    -16  f32   minimum
    -12  f32   maximum
     -8  8 x 0 unused
      0  u8    1, or 5 when the command takes an index
     +1  u16be name length, big-endian where the file header is little
     +3  name, ASCII, spaces where the OSC address has underscores

Records run in category order - the same order /Console/Channels/? lists
them in - and the trailing run are console-level commands rather than
strip parameters, keyed by an opcode and a target group instead of a
parameter id.

Full strip addresses are filled in by joining leaf names against
docs/mixer_protocol/commands.csv, which is what knows that trim lives
under Channel_Input and eq_freq_1 under EQ. A leaf that file has never
seen is left with an empty address rather than a guessed one.

**The min/max columns are the app's range, not necessarily the wire's.**
They agree for almost everything, and where they do not it is because the
app's control sweeps further than the desk stores, or in different units.
Three known divergences, all recorded in PROTOCOL.md:

    trim      dictionary -40..+60; the desk clamps at +40
    pan       dictionary -1..+1;   the wire is 0..1 with 0.5 as centre
    modes     Input/Aux are 1..2, but Group/Monitoring/Solo run to 6

So treat a range here as an upper bound on what a parameter accepts, and
prefer an observed clamp wherever one exists.

Usage:
    python tools/parse_digico_dict.py IPAD_Q2.DOSC \
        -o docs/mixer_protocol/ipad_q2_params.csv
"""

import argparse
import collections
import csv
import math
import re
import struct
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs" / "mixer_protocol"

TYPES = {0: "switch/enum", 1: "float", 3: "meter", 4: "string"}
UNITS = {"dB", "Hz", "kHz", "ms", "s", "%", "samples", "deg", ":1"}

# Path segments commands.csv uses between the strip index and the leaf.
SECTIONS = {
    "EQ", "Aux_Send", "Dynamics", "Output", "Buss_Trim", "Channel_Input",
    "Insert", "Channel_Delay", "Group_Send", "Matrix_Send", "Filters", "Panner",
}

# Where each block of records starts, by record number. Fixed by matching
# each block's leaf names against commands.csv: the seven-parameter block
# is exactly Control_Groups' seven leaves, the four-parameter one exactly
# Matrix_Inputs'. Talkback_Outputs carries a single `name` and nothing
# else, which is why /Talkback_Outputs/{n}/? never answered anything -
# see PROTOCOL.md's "Undocumented / not reachable" list.
BLOCKS = [
    ("Input_Channels", 0), ("Aux_Outputs", 163), ("Group_Outputs", 334),
    ("Control_Groups", 510), ("Talkback_Outputs", 517), ("Matrix_Inputs", 518),
    ("Matrix_Outputs", 522), ("Graphic_EQ", 690), ("Multis", 727),
    ("(console)", 731),
]

# Target group -> OSC root, for the console-level commands. Only the roots
# PROTOCOL.md already corroborates are named; the rest stay blank rather
# than invented.
ROOTS = {
    0x0800: "/Snapshots", 0xFE00: "/Macros", 0xD000: "/Presets",
    0x1A00: "/Meters", 0x1B00: "/Meters", 0x1C00: "/Meters",
    0x1000: "/Layout/Layout",
}
ROOTS.update({t: "/Console" for t in (
    0x9000, 0x9100, 0xE900, 0xEB00, 0x1800, 0x1900, 0x1D00, 0x1E00, 0x2100,
    0x2200, 0x2500, 0x2600, 0x2700, 0x2800, 0x2900, 0x2A00, 0xF300, 0xF400,
    0xF500, 0xFD00, 0x0100, 0x0200, 0x0400, 0x0700,
)})


def read_records(blob):
    """Yield (offset, flag, name) for every record, in file order."""
    header_len = struct.unpack_from("<H", blob, 0)[0]
    header = blob[2:2 + header_len].decode()
    declared = struct.unpack_from("<I", blob, 2 + header_len)[0]

    found, i = [], 2 + header_len + 4
    while i < len(blob) - 3:
        if blob[i] in (1, 5):
            n = struct.unpack_from(">H", blob, i + 1)[0]
            if 1 <= n <= 48 and i + 3 + n <= len(blob):
                name = blob[i + 3:i + 3 + n]
                if all(32 <= c < 127 for c in name):
                    found.append((i, blob[i], name.decode()))
                    i += 3 + n
                    continue
        i += 1
    # `samples` is the unit label on fine_delay, stored like a record but
    # not one. Dropping the four of them lands exactly on the declared count.
    found = [r for r in found if r[2] != "samples"]
    return header, declared, found


def parse(blob):
    header, declared, found = read_records(blob)

    def f32(off):
        v = struct.unpack_from("<f", blob, off)[0]
        return v if math.isfinite(v) else None

    rows = []
    for k, (off, flag, name) in enumerate(found):
        pre = blob[off - 24:off]
        end = (found[k + 1][0] - 24) if k + 1 < len(found) else len(blob)
        body = blob[off + 3 + len(name):end]
        units = [t.decode() for t in re.findall(rb"[A-Za-z%:][ -~]{0,8}", body)]
        units = [u for u in units if u in UNITS]
        rows.append({
            "name": name,
            "offset": off,
            "takes_index": int(flag == 5),
            "param_id": struct.unpack_from("<H", pre, 2)[0],
            "index": struct.unpack_from("<H", pre, 4)[0],
            "type": pre[7],
            "min": f32(off - 16),
            "max": f32(off - 12),
            "unit": units[0] if units else "",
            "alt_unit": units[1] if len(units) > 1 else "",
        })

    for j, (cat, start) in enumerate(BLOCKS):
        stop = BLOCKS[j + 1][1] if j + 1 < len(BLOCKS) else len(rows)
        for r in rows[start:stop]:
            r["category"] = cat
    return header, declared, rows


def address_patterns():
    """leaf name -> documented address pattern, per category."""
    pats = collections.defaultdict(dict)
    path = DOCS / "commands.csv"
    if not path.exists():
        return pats
    for row in csv.DictReader(path.open()):
        parts = row["address"].strip("/").split("/")
        cat, tail = parts[0], parts[2:]
        leaf = list(tail)
        if leaf and leaf[0] in SECTIONS:
            leaf = leaf[1:]
            if leaf and leaf[0].isdigit():
                leaf = leaf[1:]
        pats[cat].setdefault("/".join(leaf),
                             re.sub(r"/\d+/", "/{i}/", "/".join(tail)))
    # The gate leaves a strip dump never volunteers, so commands.csv has
    # none of them - PROTOCOL.md places all four under Dynamics/.
    for cat in ("Input_Channels", "Aux_Outputs", "Group_Outputs", "Matrix_Outputs"):
        for leaf in ("gate_hold", "gate_range", "gate-duck-comp", "gate_meter"):
            pats[cat].setdefault(leaf, "Dynamics/" + leaf)
    return pats


# GR_meter is the one leaf that lives under two sections, so the leaf name
# alone cannot place it. Parameter id 1036 is the dynamic EQ's gain
# reduction and 1292 the dynamics section's, and they are numbered
# differently on the wire: EQ/GR_meter is 0-based, Dynamics/GR_meter
# 1-based (PROTOCOL.md, "Metering"). The dictionary labels both runs 1..4
# and carries a 0-based instance index, so the EQ addresses are built from
# the index and the dynamics ones from the label.
GR_METER_EQ_PARAM_ID = 1036


def to_csv_rows(rows):
    pats = address_patterns()
    out = []
    for r in rows:
        leaf = r["name"].replace(" ", "_")
        num = lambda v: "" if v is None else f"{v:.6g}"
        if leaf.startswith("GR_meter") and r["param_id"] == GR_METER_EQ_PARAM_ID:
            leaf = f"EQ/GR_meter_{r['index']}"
        if r["category"] == "(console)":
            root = ROOTS.get(r["index"], "")
            suffix = leaf[-2:] if leaf.endswith(("/?", "/!")) else ""
            stem = leaf[:-2] if suffix else leaf
            out.append({
                "category": "(console)", "label": r["name"],
                "address": f"{root}/{stem}{suffix}" if root else "",
                "type": TYPES.get(r["type"], r["type"]),
                "takes_index": r["takes_index"], "index": "",
                "min": "", "max": "", "unit": "", "alt_unit": "",
                "param_id": "", "op_code": r["param_id"],
                "target": f"0x{r['index']:04x}", "file_offset": r["offset"],
            })
            continue
        # A leaf already carrying its section (see GR_METER_EQ_PARAM_ID) is
        # the pattern; everything else is placed by commands.csv.
        pattern = leaf if leaf.startswith("EQ/") \
            else pats[r["category"]].get(leaf, "")
        ranged = r["type"] in (0, 1)
        out.append({
            "category": r["category"], "label": r["name"],
            "address": f"/{r['category']}/{{n}}/{pattern}" if pattern else "",
            "type": TYPES.get(r["type"], r["type"]),
            "takes_index": r["takes_index"], "index": r["index"],
            "min": num(r["min"]) if ranged else "",
            "max": num(r["max"]) if ranged else "",
            "unit": r["unit"], "alt_unit": r["alt_unit"],
            "param_id": r["param_id"], "op_code": "", "target": "",
            "file_offset": r["offset"],
        })
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("blob", help="the dictionary file from the iPad app")
    ap.add_argument("-o", "--out", help="write CSV here instead of stdout")
    args = ap.parse_args()

    header, declared, rows = parse(Path(args.blob).read_bytes())
    print(f"{header!r}: {declared} records declared, {len(rows)} parsed")
    if declared != len(rows):
        print("  WARNING: counts disagree - the layout may have changed")
    counts = collections.Counter(r["category"] for r in rows)
    for cat, _ in BLOCKS:
        print(f"  {cat:<18} {counts[cat]}")

    csv_rows = to_csv_rows(rows)
    if args.out:
        with open(args.out, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(csv_rows[0]))
            writer.writeheader()
            writer.writerows(csv_rows)
        blank = sum(1 for r in csv_rows if not r["address"])
        print(f"wrote {args.out} ({len(csv_rows)} rows, {blank} without an address)")


if __name__ == "__main__":
    main()
