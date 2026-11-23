"""The words Voice to Text picks out of a transcript - Chorus, Bridge,
Verse... - and the colour each is highlighted in.

Kept in the desktop's settings under "voice" -> "highlights", in the same
shape services/sos_options.py gives the SOS lists, so the Settings editor
for those serves both:

    {
        "default": [word, ...],
        "custom": [
            {"name": "Sunday", "snapshots": ["Sunday AM", ...],
             "options": [word, ...]},
            ...
        ],
    }

where a word is {"id", "text", "color"}. A transcript is matched against
the list for the snapshot live on the console when it was spoken: the
first custom list tied to it, or the default list when none is.

Matching ignores case and treats spaces and hyphens alike, since the
recogniser writes "pre chorus" for what the list calls "Pre-Chorus". A
number spoken straight after a word goes with it - "verse two" comes out
as "Verse 2", highlighted whole - and every match is written the way the
list spells it.
"""

import re
import secrets

# Longest word or phrase, in characters.
MAX_TEXT = 32

# Most words in one list.
MAX_OPTIONS = 40

DEFAULT_COLOR = "#f9a825"

DEFAULT_OPTIONS = (
    {"id": "intro", "text": "Intro", "color": "#26a69a"},
    {"id": "verse", "text": "Verse", "color": "#42a5f5"},
    {"id": "pre_chorus", "text": "Pre-Chorus", "color": "#ab47bc"},
    {"id": "chorus", "text": "Chorus", "color": "#f9a825"},
    {"id": "bridge", "text": "Bridge", "color": "#ef5350"},
    {"id": "instrumental", "text": "Instrumental", "color": "#8d6e63"},
    {"id": "interlude", "text": "Interlude", "color": "#8d6e63"},
    {"id": "tag", "text": "Tag", "color": "#66bb6a"},
    {"id": "vamp", "text": "Vamp", "color": "#66bb6a"},
    {"id": "outro", "text": "Outro", "color": "#26a69a"},
    {"id": "ending", "text": "Ending", "color": "#78909c"},
)

_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")

# Spoken numbers the recogniser writes out, as the digit they stand for.
_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
}
_NUMBER = r"(?:\d{1,2}|" + "|".join(sorted(_NUMBERS, key=len, reverse=True)) \
    + r")"


def default_options():
    return [dict(option) for option in DEFAULT_OPTIONS]


def default_config():
    return {"default": default_options(), "custom": []}


def new_option_id():
    return "word_" + secrets.token_hex(4)


def clean_text(text):
    """A word as it is stored, or None if there is nothing left."""
    if not isinstance(text, str):
        return None
    text = " ".join(text.split())[:MAX_TEXT]
    return text or None


def clean_color(color):
    return color.lower() if isinstance(color, str) and _COLOR.match(color) \
        else DEFAULT_COLOR


def _clean_options(raw):
    options = []
    seen_ids = set()
    seen_words = set()

    for item in raw if isinstance(raw, list) else ():
        if not isinstance(item, dict):
            continue

        text = clean_text(item.get("text"))
        if text is None or _key(text) in seen_words:
            continue

        option_id = item.get("id")
        if not isinstance(option_id, str) or not _ID.match(option_id) \
                or option_id in seen_ids:
            option_id = new_option_id()

        seen_ids.add(option_id)
        seen_words.add(_key(text))
        options.append({
            "id": option_id, "text": text,
            "color": clean_color(item.get("color")),
        })

    return options[:MAX_OPTIONS]


def _key(text):
    """What two spellings of one word have in common."""
    return " ".join(re.split(r"[\s-]+", text.lower())).strip()


def normalize(raw):
    """A config as saved settings may hold it, made safe to use."""
    if not isinstance(raw, dict):
        return default_config()

    default = raw.get("default")
    config = {
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
    """The words highlighted while snapshot is live."""
    if snapshot:
        for custom in config["custom"]:
            if snapshot in custom["snapshots"]:
                return custom["options"]

    return config["default"]


def _pattern(text):
    parts = [re.escape(part) for part in re.split(r"[\s-]+", text) if part]
    return r"[\s-]*".join(parts)


def highlight(text, options):
    """(text, highlights): the transcript with every listed word in it
    written as the list spells it, and where each one is - a list of
    {"start", "end", "color"}, offsets in characters (code points) into
    the returned text, in order and never overlapping."""
    if not options or not text:
        return text, []

    # Longest first, so "Pre-Chorus" wins over the "Chorus" inside it.
    ordered = sorted(options, key=lambda o: len(_key(o["text"])),
                     reverse=True)
    words = "|".join(f"(?P<w{i}>{_pattern(o['text'])})"
                     for i, o in enumerate(ordered))
    matcher = re.compile(
        rf"(?<![\w-])(?:{words})(?:[\s-]+(?P<num>{_NUMBER}))?(?![\w-])",
        re.IGNORECASE,
    )

    out = []
    highlights = []
    last = 0
    length = 0

    for match in matcher.finditer(text):
        index = next(int(name[1:]) for name, value in match.groupdict().items()
                     if name != "num" and value is not None)
        option = ordered[index]

        replacement = option["text"]
        number = match.group("num")
        if number:
            digit = _NUMBERS.get(number.lower())
            replacement += f" {digit if digit is not None else number}"

        before = text[last:match.start()]
        out.append(before)
        length += len(before)
        highlights.append({"start": length, "end": length + len(replacement),
                           "color": option["color"]})
        out.append(replacement)
        length += len(replacement)
        last = match.end()

    out.append(text[last:])
    return "".join(out), highlights
