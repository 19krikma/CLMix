"""Copy To: one snapshot's channel settings written onto other snapshots.

The everyday job Show Backup's machinery was already doing the hard part
of. An engineer gets a channel right in one snapshot - the gate, the EQ,
the aux sends - and wants the same thing in the other twenty snapshots of
the show without touching each one by hand.

So: read the chosen channels out of one snapshot, then walk the chosen
snapshots and write those settings into each, asking the operator to
press Update on the desk before moving on - the same bargain RestoreJob
makes, for the same reason (no command for storing a snapshot is known;
see services/show_backup.py).

Nothing is saved to disk. The source is the console's own current state
of a snapshot, not a backup, so a copy is always of what the desk holds
right now.

What can be copied is chosen by group - EQ, Dynamics, Aux Sends - rather
than by address, because that is how an engineer thinks about a channel.
The groups are matched against the leaf of each address (COPY_GROUPS), and
anything a desk reports that no group claims lands in the catch-all, so
"All" really is all of it however the protocol grows.
"""

from services.show_backup import (NEVER_RESTORE_SUFFIXES, SETTLE_SECONDS,
                                  STRIP_ADDRESS, JobAborted, ShowBackupJob,
                                  restore_order, same_value)

# The channel's processing, in the order it runs, as the surface groups
# it. Each entry is (label, patterns): a pattern ending in "/" or "*"
# matches every leaf under it, anything else is the whole leaf. The first
# group that matches a leaf owns it, which is what keeps Channel_Input's
# name out of "Input & Gain" - see group_of().
#
# The list is not exhaustive and is not meant to be. A parameter no group
# here claims is copied only when the whole strip is being copied - see
# CopySettingsJob's `groups` - so growing the protocol notes never
# silently drops anything, and nor does it add a group nobody asked for.
#
# Mustard is in the list although no Mustard address has turned up in the
# protocol capture yet (docs/mixer_protocol). On a desk that has it the
# group copies it; on one that does not it matches nothing and the notes
# say it contributed nothing, which is the honest answer either way.
COPY_GROUPS = (
    ("Name", ("Channel_Input/name",)),
    ("Input & Gain", ("Channel_Input/",)),
    ("Filters", ("Filters/",)),
    ("EQ", ("EQ/",)),
    ("Dynamics", ("Dynamics/",)),
    ("Mustard", ("Mustard*",)),
    ("Delay", ("Channel_Delay/",)),
    ("Insert", ("Insert/",)),
    ("Panner", ("Panner/",)),
    ("Aux Sends", ("Aux_Send/",)),
    ("Group Sends", ("Group_Send/",)),
    ("Fader", ("fader",)),
    ("Mute & Solo", ("mute", "hard_mute", "solo", "alternate_solo",
                     "auto_solo", "solo_1_or_2")),
    ("Control Groups", ("CGs_*",)),
)

# What the leftovers are called in the notes. Not a group anyone can pick:
# it is only there so a run that copies the whole strip can say how much
# of what it wrote belonged to no named group.
CATCH_ALL = "Other"

GROUP_LABELS = tuple(label for label, _patterns in COPY_GROUPS)

# Copying a name puts the source snapshot's channel name on every
# snapshot picked, which is a bigger change than it looks on a desk where
# snapshots deliberately name a channel differently. Off unless asked for.
DEFAULT_GROUPS = tuple(label for label in GROUP_LABELS if label != "Name")


def group_of(leaf):
    """Which COPY_GROUPS entry owns one address leaf."""
    for label, patterns in COPY_GROUPS:
        for pattern in patterns:
            if pattern.endswith("/"):
                if leaf.startswith(pattern):
                    return label
            elif pattern.endswith("*"):
                if leaf.startswith(pattern[:-1]):
                    return label
            elif leaf == pattern:
                return label

    return CATCH_ALL


def leaf_of(address):
    """The part of an address below its strip: "Dynamics/gate_in"."""
    match = STRIP_ADDRESS.match(address)
    return address[len(match.group(1)) + 1:] if match else address


class CopySettingsJob(ShowBackupJob):
    """Copies chosen settings on chosen channels from one snapshot to others.

    `strips` are strip prefixes ("/Input_Channels/3"), `groups` labels
    from COPY_GROUPS - or None for the whole strip, every parameter the
    desk reports, named group or not - and `targets` snapshot indices,
    None for every snapshot on the console. `source` is a snapshot index,
    or None for whichever one the desk is on when the job starts.

    The shape of a run follows RestoreJob deliberately, because the
    console imposes it: get the desk onto a snapshot, read what it holds,
    write only what differs, read it back, have the operator press Update.
    A snapshot the desk never reached is put aside and tried once more
    after the others rather than stopping the run, and nothing but a
    cancel ends it early.

    The source snapshot is only read, never written - and it is dropped
    from the targets if it is picked there too, since copying a snapshot
    onto itself would ask for an Update that changes nothing.
    """

    LOG_LABEL = "Copy To"

    def __init__(self, get_worker, command_queue, source, strips, groups,
                 targets):
        super().__init__(get_worker, command_queue, None)
        self.source = source
        self.strips = list(strips)
        self.groups = None if groups is None else set(groups)
        self.targets = None if targets is None else list(targets)

        # Strips finished and strips expected. On the job rather than in
        # execute()'s locals because _apply advances them and the second
        # pass adds to the total as it goes - as in RestoreJob.
        self._done = 0
        self._total = 0

    def execute(self):
        if not self.strips:
            raise JobAborted("No channels were chosen.")
        if self.groups is not None and not self.groups:
            raise JobAborted("No settings were chosen.")

        info = self.read_session()
        by_index = {s["index"]: s for s in info["snapshots"]}

        self._check_strips(info["topology"])

        source = self.source if self.source is not None else info["current"]

        if source is None:
            raise JobAborted("The console did not say which snapshot it is on.")

        source_name = by_index.get(source, {}).get("name", "")
        source_label = self._label({"index": source, "name": source_name})
        targets = self._target_snapshots(by_index, source)

        if not targets:
            raise JobAborted("There is no other snapshot on the console to "
                             "copy to.")

        original = info["current"]
        saved = self._read_source(source, source_name, len(targets))

        applied = 0
        deferred = []

        for number, snapshot in enumerate(targets, start=1):
            if self._apply(snapshot, saved, number, len(targets)):
                applied += 1
            else:
                deferred.append(snapshot)

        if deferred:
            self.note(f"{len(deferred)} snapshot(s) were not reached on the "
                      "first pass - trying those again now.")

            # A second pass over the same strips, so the bar is given that
            # much more to climb rather than sitting full.
            self._total += len(deferred) * len(self.strips)
            self.progress = (self._done, self._total)

            for number, snapshot in enumerate(deferred, start=1):
                if self._apply(snapshot, saved, number, len(deferred),
                               retry=True):
                    applied += 1
                else:
                    self.note(f"Snapshot {self._label(snapshot)} could not be "
                              "reached on either pass - it was left alone.")

        if original is not None and self.current_snapshot() != original:
            self.ensure_snapshot(
                original, by_index.get(original, {}).get("name", "")
            )

        return (f"Copied {len(self.strips)} channel(s) from snapshot "
                f"{source_label} to {applied} of {len(targets)} snapshot(s).")

    # --- planning

    @staticmethod
    def _label(snapshot):
        return f"{snapshot['index']} “{snapshot.get('name', '')}”"

    def _check_strips(self, topology):
        """Refuse strips the console does not have.

        The window builds its channel list from what was loaded at connect
        time; a session change since then would otherwise have the job
        writing settings to a channel that is not there.
        """
        missing = []

        for prefix in self.strips:
            _, category, number = prefix.split("/")
            if int(number) > topology.get(category, 0):
                missing.append(prefix)

        if missing:
            raise JobAborted(
                f"The console does not have {len(missing)} of the channels "
                f"chosen (e.g. {', '.join(missing[:3])}) - reconnect and try "
                "again."
            )

    def _target_snapshots(self, by_index, source):
        """The snapshots to write to, in console order, source excluded."""
        if self.targets is None:
            wanted = [i for i in sorted(by_index) if i != source]
        else:
            wanted = []
            for index in self.targets:
                if index == source:
                    self.note("The snapshot being copied from was also picked "
                              "as a destination - it is left as it is.")
                elif index in by_index:
                    wanted.append(index)
                else:
                    self.note(f"Snapshot {index} is no longer on the console "
                              "- left out.")

        return [by_index[index] for index in wanted]

    # --- the source

    def _read_source(self, index, name, target_count):
        """The chosen settings, as {address: (tags, args)}, read off the desk."""
        label = self._label({"index": index, "name": name})

        self._total = (1 + target_count) * len(self.strips)
        self._done = 0
        self.progress = (0, self._total)

        if not self.ensure_snapshot(index, name):
            raise JobAborted(f"The console was not on snapshot {label}, so "
                             "there was nothing to copy from.")

        self.sleep(SETTLE_SECONDS)
        self.status = f"Reading {len(self.strips)} channel(s) from snapshot " \
                      f"{label}..."

        strips = sorted(self.strips, key=restore_order)
        now = self._flat(self.dump(strips, self._strip_done))

        saved = {}
        counts = {}

        for address, value in now.items():
            if address.endswith(NEVER_RESTORE_SUFFIXES):
                continue

            group = group_of(leaf_of(address))
            if self.groups is not None and group not in self.groups:
                continue

            saved[address] = value
            counts[group] = counts.get(group, 0) + 1

        if not saved:
            raise JobAborted(
                f"Snapshot {label} reported none of the chosen settings for "
                "those channels."
            )

        order = GROUP_LABELS + (CATCH_ALL,)
        breakdown = ", ".join(f"{group} {counts[group]}"
                              for group in order if group in counts)
        self.note(f"Read {len(saved)} setting(s) from snapshot {label} "
                  f"({breakdown}).")

        # Which of the groups asked for the desk had nothing for. Worth
        # saying: a group that copies nothing is usually a desk without
        # that processing rather than a copy that went wrong.
        wanted = set(GROUP_LABELS) if self.groups is None else self.groups
        empty = [group for group in GROUP_LABELS
                 if group in wanted and group not in counts]
        if empty:
            self.note("Nothing to copy for: " + ", ".join(empty) + ".")

        return saved

    def _strip_done(self, _prefix):
        self._done += 1
        self.progress = (self._done, self._total)

    # --- the targets

    def _apply(self, snapshot, saved, number, count, retry=False):
        """Write the copied settings into one snapshot.

        False if the desk never got onto it, which is the one outcome
        worth a second pass - a recall that did not take is usually the
        desk still being busy with the snapshot before it.
        """
        label = self._label(snapshot)
        strips = sorted({STRIP_ADDRESS.match(a).group(1) for a in saved},
                        key=restore_order)

        if not self.ensure_snapshot(snapshot["index"], snapshot.get("name", "")):
            if not retry:
                self.note(f"Could not get the desk onto snapshot {label} - "
                          "leaving it until the rest are done.")

            self._done += len(strips)
            self.progress = (self._done, self._total)
            return False

        self.sleep(SETTLE_SECONDS)
        self.status = f"Reading snapshot {label} as it is now " \
                      f"({number} of {count}{', second pass' if retry else ''})..."

        now = self._flat(self.dump(strips, self._strip_done))

        unknown = [a for a in saved if a not in now]
        writes = sorted([a for a in saved if a in now
                         and not same_value(now[a][1], saved[a][1])],
                        key=restore_order)

        if not writes:
            self.note(f"Snapshot {label}: already the same, nothing written.")
            return True

        self.status = f"Writing {len(writes)} setting(s) to snapshot {label}..."
        self._write(writes, saved)

        # Whatever did not take is rewritten once the whole snapshot has
        # had its first pass, not in the middle of one: a value refused
        # while the desk was still settling usually takes on the way round
        # again, and channels should not wait behind a matrix that is
        # arguing. Same reasoning as RestoreJob._restore_snapshot.
        wrong = sorted(self._verify(writes, saved), key=restore_order)

        if wrong:
            self.status = f"Retrying {len(wrong)} setting(s) on snapshot " \
                          f"{label}..."
            self._write(wrong, saved)
            wrong = self._verify(wrong, saved)

        summary = f"Snapshot {label}: {len(writes)} setting(s) written"
        if wrong:
            summary += f", {len(wrong)} did not take (e.g. " \
                       f"{', '.join(wrong[:3])})"
        if unknown:
            summary += f", {len(unknown)} not on this console (not written)"
        self.note(summary + ".")

        self.status = f"Store snapshot {label} on the console."
        self.ask(
            f"Snapshot {label} now has the copied settings. Press Update on "
            "the console to store it, then click Continue.",
            ["Continue"],
        )

        return True
