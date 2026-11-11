"""The SOS messages a phone's SOS button offers.

Kept in the desktop's settings under "sos" as

    {
        "default": [option, ...],
        "custom": [
            {"name": "Sunday", "snapshots": ["Sunday AM", ...],
             "options": [option, ...]},
            ...
        ],
    }

where an option is {"id", "text", "color"}. The phones are sent the list
for the snapshot live on the console: the first custom list tied to it,
or the default list when none is. The id is what a phone sends back, so
an option keeps its id through any edit of its text or colour.
"""

import re
import secrets

# The SOS red, as the phones and the desktop's SOS window have always
# drawn it.
SOS_RED = "#c62828"

# The three every phone app knew before the list could be edited - their
# ids are what those older apps still send.
DEFAULT_OPTIONS = (
    {"id": "dead_battery", "text": "DEAD BATTERY", "color": SOS_RED},
    {"id": "aux_not_working", "text": "AUX NOT WORKING", "color": SOS_RED},
    {"id": "need_assistance", "text": "NEED ASSISTANCE", "color": SOS_RED},
)

# Longest message text, in characters - it has to fit a phone button.
MAX_TEXT = 40

# Most options in one list - more than this no longer fits a phone screen.
MAX_OPTIONS = 12

_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def default_options():
    return [dict(option) for option in DEFAULT_OPTIONS]


def default_config():
    return {"default": default_options(), "custom": []}


def new_option_id():
    return "custom_" + secrets.token_hex(4)


def clean_text(text):
    """Option text as it is stored, or None if there is nothing left."""
    if not isinstance(text, str):
        return None
    text = " ".join(text.split())[:MAX_TEXT]
    return text or None


def clean_color(color):
    return color.lower() if isinstance(color, str) and _COLOR.match(color) \
        else SOS_RED


def _clean_options(raw):
    options = []
    seen = set()

    for item in raw if isinstance(raw, list) else ():
        if not isinstance(item, dict):
            continue

        text = clean_text(item.get("text"))
        option_id = item.get("id")

        if text is None:
            continue
        if not isinstance(option_id, str) or not _ID.match(option_id) \
                or option_id in seen:
            option_id = new_option_id()

        seen.add(option_id)
        options.append({
            "id": option_id, "text": text,
            "color": clean_color(item.get("color")),
        })

    return options[:MAX_OPTIONS]


def normalize(raw):
    """A config as saved settings may hold it, made safe to use: anything
    missing or malformed is dropped or replaced by its default."""
    if not isinstance(raw, dict):
        return default_config()

    default = raw.get("default")
    config = {
        # A missing default list is the built-in one; an emptied one is
        # kept empty - that is the sound engineer's choice to make.
        "default": default_options() if default is None
        else _clean_options(default),
        "custom": [],
    }

    taken = set()

    for item in raw.get("custom") or ():
        if not isinstance(item, dict):
            continue

        name = clean_text(item.get("name"))
        if name is None:
            continue

        # A snapshot belongs to one list at most - the first that has it.
        snapshots = []
        for snapshot in item.get("snapshots") or ():
            if isinstance(snapshot, str) and snapshot and snapshot not in taken:
                taken.add(snapshot)
                snapshots.append(snapshot)

        config["custom"].append({
            "name": name,
            "snapshots": snapshots,
            "options": _clean_options(item.get("options")),
        })

    return config


def options_for(config, snapshot):
    """The options phones are offered while snapshot is live."""
    if snapshot:
        for custom in config["custom"]:
            if snapshot in custom["snapshots"]:
                return custom["options"]

    return config["default"]


def find_option(config, option_id):
    """The option with this id in any list, or None. Any list rather than
    only the live one, so an SOS chosen just before a recall still goes."""
    for options in (config["default"],
                    *(custom["options"] for custom in config["custom"])):
        for option in options:
            if option["id"] == option_id:
                return option

    return None
