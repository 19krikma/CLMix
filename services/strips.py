"""Strip ids: one integer for anything that can sit on a fader.

The phone apps, the meter table and most of RemoteServer were built
around input channels, keyed by the console's own channel number. A
surface bank can carry far more than inputs, though - DCAs, aux and group
masters, matrix outputs and multis all turn up on faders (PROTOCOL.md,
"Layout"). Rather than change every one of those keys into a pair, each
non-input strip gets an integer of its own in a range no input number can
reach:

    1..999          Input_Channels n          (the channel number itself)
    1000 + n        Aux_Outputs n
    2000 + n        Group_Outputs n           (the masters)
    3000 + n        Matrix_Outputs n
    4000 + n        Control_Groups n          (DCAs)
    5000 + n        Multis n

So an input channel's id is exactly what it always was, and every client
that only ever sees inputs is unaffected.

What each kind carries is the console's own parameter dictionary
(PROTOCOL.md, "Aux_Outputs" onwards, and ipad_q2_params.csv): the three
output buses have a fader, mute, a full EQ (eight bands, no high- or
low-pass filters), the same dynamics as an input and a Buss_Trim with
name, trim and polarity. A DCA and a multi have a name, fader and mute and
nothing else - no processing, no meter.
"""

from typing import NamedTuple, Optional

INPUT_CATEGORY = "Input_Channels"
STRIDE = 1000


class StripKind(NamedTuple):
    category: str        # the OSC address space, e.g. "Aux_Outputs"
    kind: str            # what the phone is told it is
    base: int            # id = base + console index
    name_leaf: str       # where its name lives under the strip
    processing: bool     # EQ, Dynamics and Buss_Trim trim / phase
    eq_bands: int        # parametric bands, 0 where there is no EQ
    metered: bool        # fader_meter/left|right answer for it
    modes: Optional[str]  # the console's mono/stereo list, if any


INPUT = StripKind(INPUT_CATEGORY, "input", 0, "Channel_Input/name",
                  True, 4, True, "/Console/Input_Channels/modes")

KINDS = (
    StripKind("Aux_Outputs", "aux", 1000, "Buss_Trim/name",
              True, 8, True, "/Console/Aux_Outputs/modes"),
    StripKind("Group_Outputs", "group", 2000, "Buss_Trim/name",
              True, 8, True, "/Console/Group_Outputs/modes"),
    StripKind("Matrix_Outputs", "matrix", 3000, "Buss_Trim/name",
              True, 8, True, None),
    StripKind("Control_Groups", "dca", 4000, "name",
              False, 0, False, None),
    StripKind("Multis", "multi", 5000, "name",
              False, 0, False, None),
)

_BY_CATEGORY = {kind.category: kind for kind in KINDS}
_BY_BASE = {kind.base: kind for kind in KINDS}


def strip_id(category, index):
    """The strip id for a (category, index) pair off the layout, or None
    for a category nothing here knows how to drive."""
    try:
        index = int(index)
    except (TypeError, ValueError):
        return None

    if not 1 <= index < STRIDE:
        return None

    if category == INPUT_CATEGORY:
        return index

    kind = _BY_CATEGORY.get(category)
    return None if kind is None else kind.base + index


def resolve(strip):
    """(StripKind, console index) for a strip id, or None if it is not one.

    Accepts anything a phone might send: a bool or a string is refused
    rather than coerced, since True would otherwise be input channel 1.
    """
    if isinstance(strip, bool):
        return None

    try:
        strip = int(strip)
    except (TypeError, ValueError):
        return None

    base, index = divmod(strip, STRIDE)
    base *= STRIDE

    if index < 1:
        return None

    if base == 0:
        return INPUT, index

    kind = _BY_BASE.get(base)
    return None if kind is None else (kind, index)


def is_input(strip):
    resolved = resolve(strip)
    return resolved is not None and resolved[0] is INPUT


def path(strip):
    """The strip's OSC prefix, e.g. "/Aux_Outputs/3", or None."""
    resolved = resolve(strip)

    if resolved is None:
        return None

    kind, index = resolved
    return f"/{kind.category}/{index}"
