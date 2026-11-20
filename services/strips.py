"""The strips a Mixer Control bank can put on a fader besides inputs.

Shared by the mixer worker (ui/main_window.py), which meters them, and
the phone server (services/remote_server.py), which drives them. Both
need the same ids, so they live here rather than in either.

Each is known to a phone by a "channel" number of its kind's base plus
its own number (DCA 3 is 10003), so it can never be taken for an input.
Every one of these faders is dB on the same -150..+10 scale as an
input's (the official app's parameter dictionary - PROTOCOL.md), DCAs
included.

Metering: a strip has a meter only if its category has a meter address
at all. Auxes, groups and matrices carry fader_meter/left and /right;
DCAs (Control_Groups) and multitrack returns (Multis) carry none -
their whole parameter list is in PROTOCOL.md and not one is a meter -
so they have no meter to show.

Processing: groups, auxes and matrices are full buses - the same
8-band EQ (no high- or low-pass filters, unlike an input's 4 bands and
two filters), the same dynamics and a hard mute, all under their own
/{category}/{n} exactly as an input's are under /Input_Channels/{n}
(PROTOCOL.md, the three output tables). DCAs and multis have none.
"""

from collections import namedtuple

StripKind = namedtuple(
    "StripKind",
    "base label name_address meter_address modes_address modes_max eq_bands",
)
# base           what the strip's number is added to for its id
# label          what the strip shows above its name, "DCA 3"
# name_address   where its name lives
# meter_address  its meter, with {n} and {leg}; None when it has none
# modes_address  the /Console modes list saying whether it is stereo;
#                None when there is none, which meters it as mono
# modes_max      the top of that list's range. 2 means 1 mono / 2 stereo;
#                above 2 (groups run 1..6, LCR and wider) any mode over 1
#                has a right leg. PROTOCOL.md, "Console".
# eq_bands       how many EQ bands it has; 0 for no EQ, dynamics or hard
#                mute at all (see "Processing" above)
STRIP_KINDS = {
    "Control_Groups": StripKind(
        10000, "DCA", "/Control_Groups/{n}/name", None, None, None, 0),
    "Group_Outputs": StripKind(
        20000, "GRP", "/Group_Outputs/{n}/Buss_Trim/name",
        "/Group_Outputs/{n}/fader_meter/{leg}",
        "/Console/Group_Outputs/modes", 6, 8),
    "Aux_Outputs": StripKind(
        30000, "AUX", "/Aux_Outputs/{n}/Buss_Trim/name",
        "/Aux_Outputs/{n}/fader_meter/{leg}",
        "/Console/Aux_Outputs/modes", 2, 8),
    "Matrix_Outputs": StripKind(
        40000, "MTX", "/Matrix_Outputs/{n}/Buss_Trim/name",
        "/Matrix_Outputs/{n}/fader_meter/{leg}", None, None, 8),
    "Multis": StripKind(
        50000, "MULTI", "/Multis/{n}/name", None, None, None, 0),
}
STRIP_BASE = 10000

# Mono or stereo, as a /Console modes list says. See StripKind.modes_max.
MODE_MONO = 1
MODE_STEREO = 2


def strip_id(kind, number):
    """The id a phone knows a strip by: an input's own number, or its
    kind's base plus its number for anything else."""
    if kind == "Input_Channels":
        return int(number)
    return STRIP_KINDS[kind].base + int(number)


def strip_of(channel):
    """(kind, number) for a strip id, or None for an input channel."""
    try:
        channel = int(channel)
    except (TypeError, ValueError):
        return None
    if channel < STRIP_BASE:
        return None
    for kind, info in STRIP_KINDS.items():
        if info.base < channel < info.base + STRIP_BASE:
            return kind, channel - info.base
    return None


# An input's EQ: four bands, with the high- and low-pass filters ahead
# of them that no bus has.
INPUT_EQ_BANDS = 4


def processing_prefix(channel):
    """The address an input's or a bus's EQ, Dynamics and hard_mute sit
    under - "/Input_Channels/3", "/Group_Outputs/1" - or None for a strip
    with none (a DCA, a multi) or an id that is no strip at all."""
    try:
        channel = int(channel)
    except (TypeError, ValueError):
        return None
    if channel < 1:
        return None
    strip = strip_of(channel)
    if strip is None:
        return None if channel >= STRIP_BASE else f"/Input_Channels/{channel}"
    kind, number = strip
    return f"/{kind}/{number}" if STRIP_KINDS[kind].eq_bands else None


def eq_band_count(channel):
    """How many EQ bands a strip has - see processing_prefix."""
    strip = strip_of(channel)
    return INPUT_EQ_BANDS if strip is None else STRIP_KINDS[strip[0]].eq_bands


def has_filters(channel):
    """Whether a strip has the high- and low-pass filters - inputs only."""
    return strip_of(channel) is None


def is_stereo_mode(mode, modes_max):
    """Whether one entry of a /Console modes list has a right leg.

    A 1..2 list is mono or stereo and nothing else, so only 2 is stereo.
    A wider list (groups, 1..6) is LCR and up beyond 2, which still has a
    right leg - "== 2" would call those mono, the one answer certainly
    wrong for them.
    """
    mode = int(mode)
    if modes_max is not None and modes_max > MODE_STEREO:
        return mode > MODE_MONO
    return mode == MODE_STEREO
