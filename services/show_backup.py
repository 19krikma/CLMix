"""Show Backup: every channel's settings, per snapshot, saved and put back.

For the day a session on the console is corrupt and has to be rebuilt.
The racks, patching and session structure are rebuilt by hand - nothing
on the wire says which socket a channel is patched to (PROTOCOL.md,
"Input patching") - but the hours of per-channel settings on top of it
come back from here:

    <root>/<session name>/<date time>/
        session.json                 what the console was, and the
                                     snapshot list; the backup's manifest
        snapshots/
            001 - Show 1.json        every parameter the console
            002 - Soundcheck.json    reported for every strip, read
            ...                      with that snapshot recalled

Nothing here is a fixed list of parameters. A backup asks each strip for
everything it has ("/Input_Channels/3/?" dumps the lot) and keeps
whatever comes back, and a restore writes back whatever was kept. So as
the protocol notes grow - the DiGiCo App capture is how - backups grow
with them, with no change here.

With one caveat, which is the reason DUMP_GAP_LEAVES exists: the
console's dump turned out not to be the whole strip. A capture of the
official app showed it asking by name for three gate parameters the dump
never volunteers, so "everything it has" needs a short list of known
exceptions asked for on top. Anything found the same way belongs there.

Two things the console may or may not let CLMix do over OSC:

  - Recall a snapshot. The backup has to be on each snapshot to read it.
    CLMix tries RECALL_COMMAND and watches /Snapshots/Current_Snapshot
    to see whether the desk followed; if not, it asks the operator to
    recall each one on the surface and carries on the moment they do.
  - Store a snapshot. No command for it is known yet, so a restore
    writes a snapshot's settings to the live desk and then asks the
    operator to press Update, which is seconds per snapshot rather than
    hours per show.

The manifest also carries the console's macro names, which are session
work nobody wants to retype from memory. Nothing can write them back -
no address for it has been seen - so they are saved to be read, not
restored.

Both run in a background thread (ShowBackupJob) that the window polls.
"""

import json
import re
import shutil
import struct
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from services.log_store import log
from version import VERSION

FORMAT = "clmix-show-backup/1"

# Somewhere a person will find it and think to copy to a USB stick, not a
# hidden directory.
_DOCUMENTS = Path.home() / "Documents"
DEFAULT_ROOT = (_DOCUMENTS if _DOCUMENTS.is_dir() else Path.home()) / \
    "CLMix Show Backups"

MANIFEST_NAME = "session.json"
SNAPSHOTS_DIR = "snapshots"

# The console broadcasts this address when a snapshot is recalled, and
# this is the guess that it also accepts it as a command - unconfirmed on
# a real desk. ensure_snapshot() checks rather than trusts it, and falls
# back to the operator recalling on the surface. The arguments mirror the
# console's own broadcast exactly - index in the address, a single int
# zero as the argument (PROTOCOL.md, "Snapshot recall on the wire") -
# since a form the desk itself emits is the best guess available.
RECALL_COMMAND = ("/Snapshots/Recall_Snapshot/{index}", [0], "i")

# The desk announces the end of a recall. Current_Snapshot arrives in the
# middle of that burst, not at the end of it, so on a snapshot that
# changes a lot the desk is still pushing new values when it lands -
# reading strips then catches parameters on their way. Waited for after
# every recall, however it was started.
RECALL_END = "/Snapshots/End_Recall_Snapshot"
RECALL_END_MAX_WAIT = 20.0

# Reported in /Console/Channels/? but nothing under it answers (PROTOCOL).
SKIP_CATEGORIES = {"Talkback_Outputs"}

# /Console/<name> replies to /Console/Channels/? that are not strip
# counts, should the console ever send them in the same burst.
NOT_CATEGORIES = {"Name", "Channels", "Session"}

# Never written back. input_type follows from the patching (PROTOCOL.md
# "Input patching") - the patch is what sets it, not the other way round,
# and the patch is rebuilt by hand.
NEVER_RESTORE_SUFFIXES = ("/Channel_Input/input_type",)

# Whole-strip dumps: how many strips to have in flight at once, and when
# one counts as finished - at least MIN_WAIT after asking, then QUIET
# with nothing new for it, or MAX_WAIT regardless.
STRIPS_IN_FLIGHT = 4
STRIP_MIN_WAIT = 0.3
STRIP_QUIET = 0.25
STRIP_MAX_WAIT = 4.0

# A strip missing any parameter that other strips of its kind reported
# is asked again and the answers merged, for as long as each re-ask still
# turns up something new - so a UDP reply lost in the burst costs a
# re-ask, not a hole in the backup, while a strip that simply has fewer
# parameters than its neighbours (mono beside stereo, say) stops after
# one re-ask that adds nothing. A kind with a single strip has nothing to
# be compared with, so that strip is always read twice. Never more than
# STRIP_ASKS reads of any strip.
STRIP_ASKS = 5

# A whole-strip dump is not quite the whole strip. These three answer a
# direct "/?" but the console leaves them out of its own dump - found by
# capturing the official app, which asks for them by name (PROTOCOL.md,
# "Parameters a strip dump leaves out"). Without them a backup silently
# loses the gate's hold time and range and the choice between gate, duck
# and compressor. Asked for after the dump, on strips whose dump showed a
# gate at all, so the categories with no dynamics cost nothing - and if a
# desk does include them in its dump, asking again simply agrees.
DUMP_GAP_TRIGGER = "/Dynamics/gate_thresh"
DUMP_GAP_LEAVES = ("/Dynamics/gate_hold", "/Dynamics/gate_range",
                   "/Dynamics/gate-duck-comp")

SESSION_QUIET = 0.6
SESSION_MAX_WAIT = 6.0

# The session queries are all asked this many times and the answers
# merged. The channel counts and the snapshot list each arrive as one
# reply per item, and a single lost reply would quietly drop a whole bus
# type, or a whole snapshot, from the backup.
SESSION_ASKS = 2
SNAPSHOT_NAME_ASKS = 3

SESSION_QUERIES = (
    "/Console/Session/Filename/?", "/Console/Name/?", "/Console/Channels/?",
    "/Console/Input_Channels/modes/?", "/Console/Aux_Outputs/modes/?",
    "/Console/Group_Outputs/modes/?", "/Snapshots/count/?",
    "/Snapshots/names/?", "/Snapshots/Current_Snapshot/?",
    "/Snapshots/Surface_Snapshot/?", "/Layout/Layout/Banks/?",
    "/Macros/names/?",
)

# One reply per missing snapshot name, asked for by index rather than
# re-asking for the whole list (PROTOCOL.md, "Snapshots"). Paced, because
# the worker coalesces commands by address and a burst of these would
# collapse into one - see MixerWorker._drain_commands.
SNAPSHOT_NAME_QUERY = ("/Snapshots/name/?", None, "i")
SNAPSHOT_NAME_GAP = 0.15

RECALL_CONFIRM_SECONDS = 3.0

# After the desk lands on a snapshot, before reading it: replies to
# queries sent just before the recall must not be taken as its values.
SETTLE_SECONDS = 0.6

# Restore writes are paced, not fired all at once - thousands of UDP
# datagrams in one burst is how packets get lost.
WRITE_BATCH = 40
WRITE_BATCH_GAP = 0.03
VERIFY_SETTLE_SECONDS = 0.5

POLL_SECONDS = 0.05

STRIP_ADDRESS = re.compile(r"^(/[A-Za-z_]+/\d+)/")

# What wait_for() returns when the condition it was watching came true,
# rather than the operator pressing a button.
AUTO = "__auto__"


# ------------------------------------------------------------------- files

def safe_name(text, fallback="Untitled"):
    """A file/folder name that works on Windows as well as here."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(text)).strip()
    cleaned = cleaned.rstrip(". ")[:80]
    return cleaned or fallback


def session_folder_name(filename):
    return safe_name(re.sub(r"\.ses$", "", filename or "", flags=re.I),
                     "Unknown Session")


def flatten(snapshot_data):
    """{address: (tags, args)} from a snapshot file's strips."""
    flat = {}
    for strip, params in snapshot_data.get("strips", {}).items():
        for leaf, (tags, args) in params.items():
            flat[f"/{strip}/{leaf}"] = (tags, args)
    return flat


def _float32(value):
    return struct.unpack("f", struct.pack("f", float(value)))[0]


def same_value(a, b):
    """Whether two argument lists say the same thing on the wire.

    Floats are compared as the float32 the console actually holds - a
    value typed as 6410.53 comes back as 6410.5298, and is the same
    setting.
    """
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if isinstance(x, float) or isinstance(y, float):
            try:
                if _float32(x) != _float32(y):
                    return False
            except (TypeError, ValueError, OverflowError, struct.error):
                if x != y:
                    return False
        elif x != y:
            return False
    return True


class ShowBackupStore:
    """The backups on disk: one folder per session, one per backup."""

    def __init__(self, root=None):
        self.root = Path(root) if root else DEFAULT_ROOT

    def list_backups(self):
        """Every backup under root, newest first, as (path, manifest)."""
        found = []

        try:
            manifests = list(self.root.glob(f"*/*/{MANIFEST_NAME}"))
        except OSError:
            return []

        for manifest_path in manifests:
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue

            if manifest.get("format") != FORMAT:
                continue

            found.append((manifest_path.parent, manifest))

        found.sort(key=lambda item: item[1].get("created_at", ""), reverse=True)
        return found

    @staticmethod
    def load_manifest(backup_dir):
        return json.loads(
            (Path(backup_dir) / MANIFEST_NAME).read_text(encoding="utf-8")
        )

    @staticmethod
    def load_snapshot(backup_dir, entry):
        return json.loads(
            (Path(backup_dir) / entry["file"]).read_text(encoding="utf-8")
        )

    def new_backup_dir(self, session_filename):
        stamp = datetime.now().strftime("%Y-%m-%d %H-%M-%S")
        base = self.root / session_folder_name(session_filename)
        path = base / stamp
        suffix = 2

        while path.exists():
            path = base / f"{stamp} ({suffix})"
            suffix += 1

        (path / SNAPSHOTS_DIR).mkdir(parents=True)
        return path

    def remove(self, backup_dir):
        """Delete one backup - only ever a folder of ours under root."""
        backup_dir = Path(backup_dir).resolve()

        if self.root.resolve() not in backup_dir.parents or \
                not (backup_dir / MANIFEST_NAME).is_file():
            raise ValueError(f"{backup_dir} is not a Show Backup")

        shutil.rmtree(backup_dir)

        # Leave no empty session folder behind.
        session_dir = backup_dir.parent
        try:
            if not any(session_dir.iterdir()):
                session_dir.rmdir()
        except OSError:
            pass

    @staticmethod
    def write_json(path, data):
        tmp = Path(str(path) + ".tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True),
                       encoding="utf-8")
        tmp.replace(path)


# --------------------------------------------------------------- collector

class Collector:
    """Everything the console says while a job runs, sorted as it lands.

    Installed as MixerWorker.message_sink, so feed() runs on the worker
    thread and only files things away. Strip replies go into the bucket
    for their strip while one is open; session-level replies are kept by
    address, or appended where the console sends one per item (snapshot
    names, banks).
    """

    MULTI = {"/Snapshots/name", "/Layout/Layout/Banks", "/Macros/name"}

    def __init__(self):
        self._lock = threading.Lock()
        self.buckets = {}
        self.activity = {}
        self.singles = {}
        self.multi = {}
        # address -> when it last arrived, for the one-shot announcements
        # that carry no state worth keeping but say something happened -
        # RECALL_END. Kept apart from singles, which clear_session wipes.
        self.events = {}

    def feed(self, address, args, tags):
        tags = tags.lstrip(",") if tags and tags != "?" else ""
        now = time.monotonic()
        match = STRIP_ADDRESS.match(address)

        with self._lock:
            if match and match.group(1) in self.buckets:
                prefix = match.group(1)
                self.activity[prefix] = now

                # Meters answer a dump with no value at all; there is
                # nothing to keep, and nothing a restore could write.
                if args and "meter" not in address.lower():
                    self.buckets[prefix][address] = (tags, list(args))

                # Recorded here, so the worker leaves it out of the log.
                return True

            if address in self.MULTI:
                self.multi.setdefault(address, []).append(list(args))
            else:
                self.singles[address] = list(args)

            self.events[address] = now
            self.activity["session"] = now

        return False

    def open(self, prefix):
        with self._lock:
            self.buckets.setdefault(prefix, {})

    def take(self, prefix):
        with self._lock:
            self.activity.pop(prefix, None)
            return self.buckets.pop(prefix, {})

    def last_activity(self, key, default):
        with self._lock:
            return self.activity.get(key, default)

    def seen_since(self, address, when):
        """Whether `address` has arrived since monotonic time `when`."""
        with self._lock:
            return self.events.get(address, 0.0) > when

    def clear_session(self):
        with self._lock:
            self.singles = {}
            self.multi = {}
            self.activity.pop("session", None)


# --------------------------------------------------------------------- jobs

class JobCancelled(Exception):
    pass


class JobAborted(Exception):
    pass


class Prompt:
    """Something only the operator can do, and the buttons to answer."""

    def __init__(self, text, choices):
        self.text = text
        self.choices = choices
        self.answer = None
        self.answered = threading.Event()


class ShowBackupJob(threading.Thread):
    """Shared machinery for a backup or a restore running against the desk.

    The window reads status, progress, notes and prompt, and answers the
    prompt through respond(). Everything that touches the console goes
    through the worker's command_queue and comes back through Collector.
    """

    def __init__(self, get_worker, command_queue, store):
        super().__init__(daemon=True)
        self.get_worker = get_worker
        self.command_queue = command_queue
        self.store = store
        self.collector = Collector()

        self.status = "Starting..."
        self.progress = (0, 0)
        self.notes = []
        self.prompt = None
        self.finished = False
        self.outcome = None
        self.failed = False

        self._cancel = threading.Event()
        self._worker = None
        # None until the first recall shows whether the desk takes
        # RECALL_COMMAND; then True or False for the rest of the job.
        self.auto_recall = None
        # The same, for whether the desk announces the end of a recall -
        # so a desk that does not is waited for once, not once a snapshot.
        self.recall_end = None

    # --- window-facing

    def cancel(self):
        self._cancel.set()
        prompt = self.prompt
        if prompt is not None:
            prompt.answer = "Cancel"
            prompt.answered.set()

    def respond(self, choice):
        prompt = self.prompt
        if prompt is not None:
            prompt.answer = choice
            prompt.answered.set()

    def note(self, text):
        self.notes.append(text)
        log("info", f"Show Backup: {text}")

    # --- lifecycle

    def run(self):
        worker = self.get_worker()

        if worker is None or not worker.is_alive() or not worker.loaded:
            self._finish("The console is not connected.", failed=True)
            return

        if worker.bridge_only:
            self._finish("Turn off DiGiCo App mode first.", failed=True)
            return

        self._worker = worker
        worker.message_sink = self.collector.feed

        try:
            self.outcome = self.execute()
        except JobCancelled:
            self.on_stopped("cancelled")
            self._finish("Cancelled.", failed=True)
            return
        except JobAborted as ex:
            self.on_stopped(f"failed: {ex}")
            self._finish(str(ex), failed=True)
            return
        except Exception as ex:
            log("error", f"Show Backup job crashed: {ex!r}")
            self.on_stopped(f"failed: {ex!r}")
            self._finish(f"Stopped by an error: {ex}", failed=True)
            return
        finally:
            if worker.message_sink == self.collector.feed:
                worker.message_sink = None

        self._finish(self.outcome)

    def _finish(self, outcome, failed=False):
        self.outcome = outcome
        self.failed = failed
        self.prompt = None
        self.status = outcome
        self.finished = True
        log("warning" if failed else "info", f"Show Backup: {outcome}")

    def execute(self):
        raise NotImplementedError

    def on_stopped(self, status):
        """A job that ends early records how far it got."""

    # --- guards

    def check(self):
        if self._cancel.is_set():
            raise JobCancelled()

        worker = self._worker
        if not worker.is_alive():
            raise JobAborted("The console connection was lost.")
        if worker.bridge_only:
            raise JobAborted("DiGiCo App mode was turned on.")

    def sleep(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.check()
            time.sleep(POLL_SECONDS)

    def ask(self, text, choices, wait_for=None, poll=None):
        """Show a prompt and block until it is answered - or, if wait_for
        is given, until that comes true on its own (returns AUTO). The
        window's own Cancel answers any prompt with "Cancel"."""
        prompt = Prompt(text, choices)
        self.prompt = prompt
        next_poll = 0.0

        try:
            while not prompt.answered.wait(POLL_SECONDS):
                self.check()

                if poll is not None and time.monotonic() >= next_poll:
                    poll()
                    next_poll = time.monotonic() + 1.0

                if wait_for is not None and wait_for():
                    return AUTO
        finally:
            self.prompt = None

        if prompt.answer == "Cancel":
            raise JobCancelled()

        return prompt.answer

    # --- console reads

    def send(self, command):
        self.command_queue.put(command)

    def current_snapshot(self):
        value = self._worker.cache.get("/Snapshots/Current_Snapshot")
        return int(value[0]) if value else None

    def _ask_and_wait(self, queries):
        for query in queries:
            self.send(query)

        started = time.monotonic()
        while True:
            self.sleep(0.1)
            now = time.monotonic()
            last = self.collector.last_activity("session", started)
            if (now - last >= SESSION_QUIET and now - started >= SESSION_QUIET) \
                    or now - started >= SESSION_MAX_WAIT:
                return

    def _snapshot_names(self):
        snapshots = {}
        for args in self.collector.multi.get("/Snapshots/name", []):
            if args:
                index = int(args[0])
                snapshots[index] = {
                    "index": index,
                    "cue": args[1] if len(args) > 2 else None,
                    "name": str(args[-1]),
                }
        return snapshots

    def _ask_missing_names(self, expected, snapshots):
        """Ask for each snapshot name still missing, one at a time.

        Snapshot indices are 0-based and run to count - 1 (PROTOCOL.md,
        "Snapshots"). An index that is not really there simply goes
        unanswered, so guessing the range costs nothing.
        """
        address, _args, tags = SNAPSHOT_NAME_QUERY

        missing = [index for index in range(expected) if index not in snapshots]
        for index in missing:
            self.send((address, [index], tags))
            self.sleep(SNAPSHOT_NAME_GAP)

        self._ask_and_wait([])

    def read_session(self):
        """Who the console is, its shape, and its snapshot list."""
        self.status = "Reading the session..."
        self.collector.clear_session()

        for _ in range(SESSION_ASKS):
            self._ask_and_wait(SESSION_QUERIES)

        singles = self.collector.singles
        multi = self.collector.multi

        # Keep asking for names until there are as many as the console
        # says it has - a missing one is a snapshot never backed up.
        count = singles.get("/Snapshots/count")
        expected = int(count[0]) if count else None
        snapshots = self._snapshot_names()

        for _ in range(SNAPSHOT_NAME_ASKS):
            if expected is None or len(snapshots) >= expected:
                break
            self._ask_and_wait(["/Snapshots/names/?"])
            snapshots = self._snapshot_names()

        # Whatever is still missing, ask for by index. Cheaper than
        # another whole list and, more to the point, a different question
        # - one reply to lose instead of all of them.
        if expected is not None and len(snapshots) < expected:
            self._ask_missing_names(expected, snapshots)
            snapshots = self._snapshot_names()

        if expected is not None and len(snapshots) < expected:
            self.note(f"The console reports {expected} snapshots but only "
                      f"{len(snapshots)} names arrived.")

        topology = {}
        for address, args in singles.items():
            match = re.match(r"^/Console/([A-Za-z_]+)$", address)
            if match and match.group(1) not in NOT_CATEGORIES and args \
                    and isinstance(args[0], (int, float)):
                topology[match.group(1)] = int(args[0])

        if not topology.get("Input_Channels"):
            raise JobAborted("The console did not report its channel layout.")

        # Asked more than once, so the same bank can be here twice.
        banks = {}
        for args in multi.get("/Layout/Layout/Banks", []):
            if args:
                banks[str(args[0])] = args

        # Console-wide, not per-snapshot, and there is no known way to
        # write one back - kept so a rebuilt session can have its macros
        # typed in again from the backup rather than from memory.
        macros = {}
        for args in multi.get("/Macros/name", []):
            if args:
                macros[int(args[0])] = str(args[-1])

        surface = singles.get("/Snapshots/Surface_Snapshot")

        return {
            "session": (singles.get("/Console/Session/Filename") or [""])[0],
            "console_name": (singles.get("/Console/Name") or [""])[0],
            "topology": topology,
            "modes": {
                key: singles[f"/Console/{key}/modes"]
                for key in ("Input_Channels", "Aux_Outputs", "Group_Outputs")
                if f"/Console/{key}/modes" in singles
            },
            "banks": list(banks.values()),
            "macros": [{"index": i, "name": macros[i]} for i in sorted(macros)],
            "snapshots": [snapshots[i] for i in sorted(snapshots)],
            "current": self.current_snapshot(),
            "surface": int(surface[0]) if surface else None,
        }

    @staticmethod
    def all_strips(topology):
        return [
            f"/{category}/{n}"
            for category, count in topology.items()
            if category not in SKIP_CATEGORIES
            for n in range(1, count + 1)
        ]

    @staticmethod
    def _kind(prefix):
        return prefix.split("/")[1]

    def dump(self, strips, on_strip_done=None):
        """{strip prefix: {address: (tags, args)}} for every strip given."""
        results = {prefix: {} for prefix in strips}
        kinds = {}
        for prefix in strips:
            kinds.setdefault(self._kind(prefix), []).append(prefix)

        pending = deque(strips)
        # How many parameters each strip had before its latest re-ask;
        # a re-ask that adds nothing ends the strip's retries.
        before = {}

        for attempt in range(STRIP_ASKS):
            self._dump_pass(pending, results,
                            on_strip_done if attempt == 0 else None)

            # Every parameter name any strip of a kind reported, relative
            # to its strip - what each strip of that kind should have.
            expected = {}
            for prefix, params in results.items():
                leaves = expected.setdefault(self._kind(prefix), set())
                leaves.update(address[len(prefix):] for address in params)

            retry = []
            for prefix, params in results.items():
                kind = self._kind(prefix)
                have = {address[len(prefix):] for address in params}
                short = not expected[kind] <= have
                lone = attempt == 0 and len(kinds[kind]) == 1
                progressing = prefix not in before or len(params) > before[prefix]

                if (short and progressing) or lone:
                    retry.append(prefix)

            before = {prefix: len(results[prefix]) for prefix in retry}
            pending = deque(retry)

            if not pending:
                break

        self._fill_dump_gaps(results)

        return results

    def _fill_dump_gaps(self, results):
        """Ask by name for the parameters the dump does not volunteer.

        See DUMP_GAP_LEAVES. Only strips whose dump showed a gate are
        asked, since that is what these belong to, and a strip that
        already reported one of them is not asked for it again.
        """
        wanted = {}

        for prefix, params in results.items():
            if prefix + DUMP_GAP_TRIGGER not in params:
                continue

            missing = [prefix + leaf + "/?" for leaf in DUMP_GAP_LEAVES
                       if prefix + leaf not in params]
            if missing:
                wanted[prefix] = missing

        if not wanted:
            return

        self._dump_pass(deque(wanted), results, None,
                        queries=lambda prefix: wanted[prefix])

    def _dump_pass(self, pending, results, on_strip_done, queries=None):
        """Ask every strip in pending once, keeping STRIPS_IN_FLIGHT going,
        and merge what comes back into results.

        `queries` chooses what to ask each strip; the default is the
        whole-strip dump.
        """
        if queries is None:
            def queries(prefix):
                return [f"{prefix}/?"]

        in_flight = {}

        while pending or in_flight:
            self.check()
            now = time.monotonic()

            while pending and len(in_flight) < STRIPS_IN_FLIGHT:
                prefix = pending.popleft()
                self.collector.open(prefix)
                for query in queries(prefix):
                    self.send(query)
                in_flight[prefix] = now

            for prefix, sent_at in list(in_flight.items()):
                last = self.collector.last_activity(prefix, sent_at)
                waited = now - sent_at

                if (waited >= STRIP_MIN_WAIT and now - last >= STRIP_QUIET) \
                        or waited >= STRIP_MAX_WAIT:
                    del in_flight[prefix]
                    results[prefix].update(self.collector.take(prefix))

                    if on_strip_done is not None:
                        on_strip_done(prefix)

            time.sleep(POLL_SECONDS)

    def ensure_snapshot(self, index, name):
        """Get the desk onto snapshot `index`. False if the operator skips."""
        if self.current_snapshot() == index:
            return True

        label = f"{index} “{name}”"
        started = time.monotonic()

        if self.auto_recall is not False:
            self.status = f"Recalling snapshot {label}..."
            address, args, tags = RECALL_COMMAND
            self.send((address.format(index=index), args, tags))

            deadline = time.monotonic() + RECALL_CONFIRM_SECONDS
            while time.monotonic() < deadline:
                self.sleep(0.2)
                self.send("/Snapshots/Current_Snapshot/?")
                if self.current_snapshot() == index:
                    self.auto_recall = True
                    self._wait_for_recall_end(started)
                    return True

            if self.auto_recall is None:
                self.note("The console did not follow CLMix's recall command, "
                          "so each snapshot will be recalled on the desk.")
            self.auto_recall = False

        self.status = f"Waiting for snapshot {label}..."
        answer = self.ask(
            f"Recall snapshot {label} on the console. CLMix carries on as "
            f"soon as the desk is on it.",
            ["Skip"],
            wait_for=lambda: self.current_snapshot() == index,
            poll=lambda: self.send("/Snapshots/Current_Snapshot/?"),
        )

        if answer == AUTO:
            self._wait_for_recall_end(started)

        return answer == AUTO

    def _wait_for_recall_end(self, started):
        """Block until the desk says the recall is over, or gives up on it.

        Current_Snapshot lands mid-recall, so returning on it alone hands
        the caller a desk that is still changing. RECALL_END is the
        console's own "done" and arrives milliseconds later on a small
        snapshot; the timeout is only there so a desk that never sends it
        costs one wait rather than the whole job.
        """
        if self.recall_end is False:
            return False

        self.status = "Waiting for the recall to finish..."
        deadline = time.monotonic() + RECALL_END_MAX_WAIT

        while time.monotonic() < deadline:
            if self.collector.seen_since(RECALL_END, started):
                self.recall_end = True
                return True
            self.sleep(POLL_SECONDS)

        if self.recall_end is None:
            self.note("The console does not announce the end of a recall, "
                      "so each snapshot is read after a fixed settle "
                      "instead.")
        self.recall_end = False

        return False


class BackupJob(ShowBackupJob):
    """Reads every snapshot (or just the current one) into a new backup."""

    def __init__(self, get_worker, command_queue, store, all_snapshots=True):
        super().__init__(get_worker, command_queue, store)
        self.all_snapshots = all_snapshots
        self.backup_dir = None
        self.manifest = None

    def execute(self):
        info = self.read_session()
        strips = self.all_strips(info["topology"])
        original = info["current"]

        if self.all_snapshots:
            targets = info["snapshots"]
        else:
            targets = [s for s in info["snapshots"] if s["index"] == original] \
                or [{"index": original, "cue": None, "name": f"Snapshot {original}"}]

        if not targets:
            raise JobAborted("The console reported no snapshots.")

        self.backup_dir = self.store.new_backup_dir(info["session"])
        self.manifest = {
            "format": FORMAT,
            "clmix_version": VERSION,
            "session": info["session"],
            "console_name": info["console_name"],
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "status": "in progress",
            "scope": "all snapshots" if self.all_snapshots else "current snapshot",
            "topology": info["topology"],
            "modes": info["modes"],
            "banks": info["banks"],
            "macros": info["macros"],
            "snapshot_at_backup": original,
            "surface_snapshot_at_backup": info["surface"],
            "snapshots": [],
        }
        self._save_manifest()

        total = len(targets) * len(strips)
        done = 0
        self.progress = (0, total)

        for number, snapshot in enumerate(targets, start=1):
            entry = dict(snapshot)
            label = f"{snapshot['index']} “{snapshot['name']}”"

            if not self.ensure_snapshot(snapshot["index"], snapshot["name"]):
                entry["skipped"] = True
                self.manifest["snapshots"].append(entry)
                self._save_manifest()
                self.note(f"Skipped snapshot {label}.")
                done += len(strips)
                self.progress = (done, total)
                continue

            self.sleep(SETTLE_SECONDS)
            self.status = f"Reading snapshot {label} ({number} of {len(targets)})..."

            def strip_done(_prefix):
                nonlocal done
                done += 1
                self.progress = (done, total)

            results = self.dump(strips, strip_done)
            count = sum(len(params) for params in results.values())

            entry["file"] = f"{SNAPSHOTS_DIR}/{snapshot['index']:03d} - " \
                            f"{safe_name(snapshot['name'])}.json"
            entry["parameters"] = count

            self.store.write_json(self.backup_dir / entry["file"], {
                "format": FORMAT,
                "snapshot": snapshot,
                "captured_at": datetime.now().isoformat(timespec="seconds"),
                "parameters": count,
                "strips": {
                    prefix.lstrip("/"): {
                        address[len(prefix) + 1:]: [tags, args]
                        for address, (tags, args) in sorted(params.items())
                    }
                    for prefix, params in results.items() if params
                },
            })

            self.manifest["snapshots"].append(entry)
            self._save_manifest()
            self.note(f"Saved snapshot {label}: {count} settings.")

        self._return_to(original, info)

        saved = [s for s in self.manifest["snapshots"] if not s.get("skipped")]
        self.manifest["status"] = "complete"
        self.manifest["completed_at"] = datetime.now().isoformat(timespec="seconds")
        self._save_manifest()

        return (f"Backed up {len(saved)} of {len(targets)} snapshot(s) "
                f"to {self.backup_dir}.")

    def _return_to(self, original, info):
        if original is None or self.current_snapshot() == original:
            return

        name = next((s["name"] for s in info["snapshots"]
                     if s["index"] == original), "")
        self.ensure_snapshot(original, name)

    def _save_manifest(self):
        self.store.write_json(self.backup_dir / MANIFEST_NAME, self.manifest)

    def on_stopped(self, status):
        if self.manifest is not None:
            self.manifest["status"] = status
            try:
                self._save_manifest()
            except OSError:
                pass


class RestoreJob(ShowBackupJob):
    """Writes a backup's snapshots back onto the desk, one at a time.

    Snapshots are matched by name, so the rebuilt session needs them
    named as they were; a backup snapshot with no namesake on the desk is
    reported and left alone. For each one: get the desk onto it, read
    what it holds now, write only what differs, read it back to check,
    and have the operator press Update to store it.
    """

    def __init__(self, get_worker, command_queue, store, backup_dir,
                 only_file=None):
        super().__init__(get_worker, command_queue, store)
        self.backup_dir = Path(backup_dir)
        self.only_file = only_file

    def execute(self):
        manifest = self.store.load_manifest(self.backup_dir)
        info = self.read_session()

        self._check_layout(manifest["topology"], info["topology"])

        entries = [e for e in manifest["snapshots"]
                   if not e.get("skipped") and e.get("file")
                   and (self.only_file is None or e["file"] == self.only_file)]
        plan = self._match(entries, info["snapshots"])

        if not plan:
            raise JobAborted("None of the backup's snapshots are on the console "
                             "by name. Create them with the same names first.")

        original = info["current"]
        loaded = {}
        total = 0
        for entry, _target in plan:
            data = self.store.load_snapshot(self.backup_dir, entry)
            flat = {address: value for address, value in flatten(data).items()
                    if not address.endswith(NEVER_RESTORE_SUFFIXES)}
            loaded[entry["file"]] = flat
            total += len({STRIP_ADDRESS.match(a).group(1) for a in flat})

        done = 0
        self.progress = (0, total)
        restored = 0

        for number, (entry, target) in enumerate(plan, start=1):
            label = f"{target['index']} “{target['name']}”"
            saved = loaded[entry["file"]]
            strips = sorted({STRIP_ADDRESS.match(a).group(1) for a in saved})

            if not self.ensure_snapshot(target["index"], target["name"]):
                self.note(f"Skipped snapshot {label}.")
                done += len(strips)
                self.progress = (done, total)
                continue

            self.sleep(SETTLE_SECONDS)
            self.status = f"Reading snapshot {label} as it is now " \
                          f"({number} of {len(plan)})..."

            def strip_done(_prefix):
                nonlocal done
                done += 1
                self.progress = (done, total)

            now = self._flat(self.dump(strips, strip_done))

            unknown = [a for a in saved if a not in now]
            writes = [a for a in saved if a in now
                      and not same_value(now[a][1], saved[a][1])]

            self.status = f"Writing {len(writes)} settings to snapshot {label}..."
            self._write(writes, saved)

            wrong = self._verify(writes, saved)
            if wrong:
                self._write(wrong, saved)
                wrong = self._verify(wrong, saved)

            summary = f"Snapshot {label}: {len(writes)} settings written"
            if wrong:
                summary += f", {len(wrong)} did not take (e.g. " \
                           f"{', '.join(wrong[:3])})"
            if unknown:
                summary += f", {len(unknown)} not on this console (not written)"
            self.note(summary + ".")

            self.status = f"Store snapshot {label} on the console."
            self.ask(
                f"Snapshot {label} is restored on the desk. Press Update on "
                f"the console to store it, then click Continue.",
                ["Continue"],
            )
            restored += 1

        if original is not None and self.current_snapshot() != original:
            name = next((s["name"] for s in info["snapshots"]
                         if s["index"] == original), "")
            self.ensure_snapshot(original, name)

        return f"Restored {restored} of {len(plan)} snapshot(s)."

    @staticmethod
    def _flat(results):
        flat = {}
        for params in results.values():
            flat.update(params)
        return flat

    def _check_layout(self, saved, console):
        """Refuse to write a backup onto a desk of a different shape: a
        channel's settings belong on that channel number, and a rebuilt
        session that is not laid out the same would put them elsewhere."""
        differences = [
            f"{category}: {saved.get(category, 0)} in the backup, "
            f"{console.get(category, 0)} on the console"
            for category in sorted(set(saved) | set(console))
            if category not in SKIP_CATEGORIES
            and saved.get(category, 0) != console.get(category, 0)
        ]

        if differences:
            raise JobAborted("The console is not laid out like the backup - "
                             + "; ".join(differences))

    def _match(self, entries, console_snapshots):
        """(backup entry, console snapshot) pairs, matched by name - the
        nth snapshot of a name in the backup to the nth on the desk."""
        by_name = {}
        for snapshot in console_snapshots:
            by_name.setdefault(snapshot["name"], []).append(snapshot)

        used = {}
        plan = []

        for entry in entries:
            name = entry["name"]
            nth = used.get(name, 0)
            candidates = by_name.get(name, [])

            if nth < len(candidates):
                plan.append((entry, candidates[nth]))
                used[name] = nth + 1
            else:
                self.note(f"Snapshot “{name}” is not on the console "
                          f"- left out.")

        return plan

    def _write(self, addresses, saved):
        for start in range(0, len(addresses), WRITE_BATCH):
            self.check()
            for address in addresses[start:start + WRITE_BATCH]:
                tags, args = saved[address]
                self.send((address, list(args), tags or None))
            time.sleep(WRITE_BATCH_GAP)

    def _verify(self, addresses, saved):
        """The written addresses that still do not read back as saved."""
        if not addresses:
            return []

        self.sleep(VERIFY_SETTLE_SECONDS)
        strips = sorted({STRIP_ADDRESS.match(a).group(1) for a in addresses})
        now = self._flat(self.dump(strips))

        return [a for a in addresses
                if a not in now or not same_value(now[a][1], saved[a][1])]
