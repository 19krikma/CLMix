"""Traffic Analyzer: a rolling record of everything said to and by the
console, for working out what an undocumented parameter does.

The debug log already carries every message, but as prose lines mixed in
with everything else the app logs, and with nowhere to say "this is what I
just did on the desk". This keeps the traffic itself - direction, address,
type tags, arguments and bytes - in a ring buffer that is always filling,
so the operator can do something on the console first and open the
Analyzer afterwards to find it.

From there they tick the messages that belong to what they did, describe
it, and save that as an observation: the ticked messages, everything else
on the wire around them (CONTEXT_SECONDS either side - what they did not
think to tick is often the interesting part), and the snapshot it happened
on. Observations are copied out of the ring at that moment, so a later
burst cannot roll them away. export() writes them all as one Markdown
report, laid out to be read back by whoever is writing PROTOCOL.md.

Records come from the MixerWorker thread and are read from the Tk thread,
so everything goes through one lock. Nothing here knows about Tkinter.
"""

import itertools
import threading
import time
from collections import deque
from datetime import datetime

from services.log_store import LOGS_DIR

REPORTS_DIR = LOGS_DIR / "analyzer_reports"

# Messages held before the oldest are dropped. A Show Backup reads far
# more than this, but nobody studies a backup message by message; this is
# a few minutes of ordinary traffic, which is what an observation needs.
MAX_RECORDS = 20000

# How far either side of the ticked messages an observation reaches for
# unticked ones. Bursts land inside a second (PROTOCOL.md, Input
# patching); a little more catches the answers to CLMix's own follow-up
# queries as well.
CONTEXT_SECONDS = 1.5

# Bytes of each message kept for the report. Enough for any single
# parameter; a long name list is cut short rather than kept whole.
RAW_BYTES = 256

IN = "IN"
OUT = "OUT"
NOTE = "NOTE"

# The connection heartbeat (MixerWorker._check_heartbeat) and its answer,
# every few seconds whatever else is going on - noise in nearly every
# question, so hidden by default and left out of an observation's context.
HEARTBEAT_ADDRESSES = frozenset({
    "/Snapshots/Current_Snapshot/?",
    "/Snapshots/Current_Snapshot",
})

METER_ADDRESS = "/Meters/values"


class Record:
    __slots__ = ("seq", "at", "stamp", "direction", "address", "tags",
                 "args", "length", "raw")

    def __init__(self, seq, direction, address, tags, args, data):
        self.seq = seq
        self.at = time.monotonic()
        self.stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.direction = direction
        self.address = address
        self.tags = tags
        self.args = args
        self.length = len(data) if data else 0
        self.raw = data[:RAW_BYTES] if data else b""

    @property
    def heartbeat(self):
        return self.address in HEARTBEAT_ADDRESSES

    def args_text(self):
        return ", ".join(repr(arg) for arg in self.args)

    def hex(self):
        cut = "..." if self.length > len(self.raw) else ""
        return f"{self.raw.hex(' ')}{cut}"


class Observation:
    def __init__(self, note, ticked, records, context):
        self.note = note
        self.ticked = ticked
        self.records = records
        self.context = context
        self.saved_at = datetime.now()


class TrafficAnalyzer:
    def __init__(self, max_records=MAX_RECORDS):
        self._records = deque(maxlen=max_records)
        self._lock = threading.Lock()
        self._seq = itertools.count(1)
        self._observations = []

        # Meters arrive ~30 times a second and say nothing about a
        # parameter, so they are only kept when asked for.
        self.include_meters = False

    # --- recording, from any thread ------------------------------------

    def record(self, direction, address, args=(), tags="", data=b""):
        if address == METER_ADDRESS and not self.include_meters:
            return

        with self._lock:
            self._records.append(
                Record(next(self._seq), direction, address, tags,
                       list(args), data)
            )

    def note(self, text):
        """A line of the operator's own in the stream - a marker dropped
        before doing something, or a message sent by hand."""
        self.record(NOTE, text)

    # --- reading, from the UI ------------------------------------------

    def since(self, seq):
        with self._lock:
            if not self._records or self._records[-1].seq <= seq:
                return []

            return [r for r in self._records if r.seq > seq]

    def clear(self):
        with self._lock:
            self._records.clear()

    # --- observations --------------------------------------------------

    def save_observation(self, note, ticked_seqs, context=None):
        """Freeze the ticked records and their surroundings with a note.

        Returns the observation, or None if none of the ticked records
        are still held.
        """
        ticked_seqs = set(ticked_seqs)

        with self._lock:
            records = list(self._records)

        ticked = [r for r in records if r.seq in ticked_seqs]
        if not ticked:
            return None

        start = ticked[0].at - CONTEXT_SECONDS
        end = ticked[-1].at + CONTEXT_SECONDS

        window = [
            r for r in records
            if r.seq in ticked_seqs
            or (start <= r.at <= end and not r.heartbeat)
        ]

        observation = Observation(
            note.strip(), ticked_seqs, window, dict(context or {})
        )

        with self._lock:
            self._observations.append(observation)

        return observation

    def observations(self):
        with self._lock:
            return list(self._observations)

    def remove_observation(self, index):
        with self._lock:
            if 0 <= index < len(self._observations):
                del self._observations[index]

    def clear_observations(self):
        with self._lock:
            self._observations.clear()

    # --- the report ----------------------------------------------------

    def export(self, header=None, reports_dir=REPORTS_DIR):
        """Write every observation as one Markdown file. Returns its path."""
        observations = self.observations()
        reports_dir.mkdir(parents=True, exist_ok=True)

        path = reports_dir / \
            f"analyzer_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.md"
        path.write_text(render_report(observations, header or {}),
                        encoding="utf-8")
        return path


def _cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_report(observations, header):
    lines = [
        "# CLMix Analyzer report",
        "",
        f"Written {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    lines += [f"{key}: {value}" for key, value in header.items()]
    lines += [
        "",
        "Each observation is something the operator did, in their words, "
        "with what crossed the wire around it. **IN** is console -> CLMix, "
        "**OUT** is CLMix -> console (CLMix's own queries, the phones' "
        "moves and anything sent by hand), **NOTE** is the operator's own "
        "marker. Rows marked `*` are the ones the operator ticked as "
        "belonging to what they did; the rest are everything else within "
        f"{CONTEXT_SECONDS:g}s of them, heartbeat left out. `+ms` counts "
        "from the first ticked row.",
        "",
    ]

    if not observations:
        lines.append("_No observations._")

    for number, obs in enumerate(observations, 1):
        title = obs.note.splitlines()[0] if obs.note else "(no description)"
        lines += [
            f"## {number}. {title}",
            "",
            f"Saved {obs.saved_at.strftime('%H:%M:%S')}",
        ]
        lines += [f"{key}: {value}" for key, value in obs.context.items()]
        lines.append("")

        if obs.note:
            lines += [f"> {line}" if line else ">"
                      for line in obs.note.splitlines()]
            lines.append("")

        first = next(r.at for r in obs.records if r.seq in obs.ticked)

        lines += [
            "| | +ms | Time | Dir | Address | Types | Args |",
            "|---|---:|---|---|---|---|---|",
        ]
        for r in obs.records:
            mark = "*" if r.seq in obs.ticked else ""
            lines.append(
                f"| {mark} | {(r.at - first) * 1000:+.0f} | {r.stamp} | "
                f"{r.direction} | `{_cell(r.address)}` | {_cell(r.tags)} | "
                f"{_cell(r.args_text())} |"
            )

        lines += ["", "Raw bytes of the ticked messages:", "", "```"]
        for r in obs.records:
            if r.seq in obs.ticked and r.direction != NOTE:
                lines.append(f"{r.stamp} {r.direction:<3} {r.address}")
                lines.append(f"    {r.length} bytes: {r.hex()}")
        lines += ["```", ""]

    return "\n".join(lines) + "\n"


analyzer = TrafficAnalyzer()
