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
number spoken with a word goes with it - "verse two" and "second verse"
both come out as "Verse 2", "chorus in two" as "Chorus in 2", highlighted
whole - and every match is written the way the list spells it. A Russian
word matches in any case ending: Припев finds "припева" and "припеве".

highlight can also pick out scripture references (services/
voice_scripture.py), written the short way - "Matthew 4:5" - in
SCRIPTURE_COLOR.
"""

import re
import secrets

from services import voice_scripture

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
    {"id": "refrain", "text": "Refrain", "color": "#f9a825"},
    {"id": "repeat", "text": "Repeat", "color": "#ffa726"},
    {"id": "again", "text": "Again", "color": "#ffa726"},
    # The same in Russian, for a service or a leader that speaks it.
    {"id": "ru_intro", "text": "Вступление", "color": "#26a69a"},
    {"id": "ru_verse", "text": "Куплет", "color": "#42a5f5"},
    {"id": "ru_pre_chorus", "text": "Предприпев", "color": "#ab47bc"},
    {"id": "ru_chorus", "text": "Припев", "color": "#f9a825"},
    {"id": "ru_bridge", "text": "Бридж", "color": "#ef5350"},
    {"id": "ru_interlude", "text": "Проигрыш", "color": "#8d6e63"},
    {"id": "ru_repeat", "text": "Повтор", "color": "#ffa726"},
    {"id": "ru_again", "text": "Ещё раз", "color": "#ffa726"},
    {"id": "ru_ending", "text": "Концовка", "color": "#78909c"},
)

# Words added to the defaults after the first release, with the
# revision that added them - a list saved before then gets them once, in
# normalize, rather than only a list made fresh. See REVISION.
ADDED_IN = {2: ("refrain", "repeat", "again", "ru_intro", "ru_verse",
                "ru_pre_chorus", "ru_chorus", "ru_bridge", "ru_interlude",
                "ru_repeat", "ru_again", "ru_ending")}
REVISION = max(ADDED_IN)

SCRIPTURE_COLOR = "#ff7043"

_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")

# A number said after a word - "verse two", "куплет второй" - as digits
# or a word for one to ten. Which number a word is comes from
# voice_scripture.number_word.
_RU_END = r"(?:ый|ий|ой|ое|ее|ого|его|ому|ая|ую|ом|ым)"
_NUMBER = (
    r"(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|first|"
    r"second|third|fourth|fifth|один|одна|два|две|три|четыре|пять|шесть|"
    r"семь|восемь|девять|десять|перв" + _RU_END + r"|втор" + _RU_END
    + r"|трет(?:ий|ья|ье|ьего|ьей|ью|ьем)|четв[её]рт" + _RU_END
    + r"|пят" + _RU_END + r"|шест" + _RU_END + r"|седьм" + _RU_END
    + r"|восьм" + _RU_END + r")"
)
# An ordinal before a word: "second verse", "второй куплет".
_ORDINAL = (
    r"(?:first|second|third|fourth|fifth|last|перв" + _RU_END + r"|втор"
    + _RU_END + r"|трет(?:ий|ья|ье|ьего|ьей|ью|ьем)|четв[её]рт" + _RU_END
    + r"|последн\w*)"
)
# "chorus in two" - coming up in two bars.
_IN = r"(?:in|через)"
_CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)


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


def normalize(raw, revision=REVISION):
    """A config as saved settings may hold it, made safe to use.
    `revision` is the REVISION it was saved at - an older one has the
    default words added since put in its default list."""
    if not isinstance(raw, dict):
        return default_config()

    default = raw.get("default")
    config = {
        "default": default_options() if default is None
        else _clean_options(default),
        "custom": [],
    }
    if default is not None:
        new = [option_id for rev, ids in ADDED_IN.items() if rev > revision
               for option_id in ids]
        have_ids = {o["id"] for o in config["default"]}
        have_words = {_key(o["text"]) for o in config["default"]}
        for option in DEFAULT_OPTIONS:
            if option["id"] in new and option["id"] not in have_ids \
                    and _key(option["text"]) not in have_words:
                config["default"].append(dict(option))
        config["default"] = config["default"][:MAX_OPTIONS]

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
    parts = [part for part in re.split(r"[\s-]+", text) if part]
    escaped = [_escape(part) for part in parts]
    if escaped and _CYRILLIC.search(parts[-1]):
        # Any case ending: "припев" finds "припева", "куплет" "куплете",
        # "концовка" "концовку".
        last = re.sub(r"[аяоеёуюыиьй]$", "", parts[-1].lower())
        if len(last) >= 3:
            escaped[-1] = _escape(last) + r"[а-яё]{0,3}"
    return r"[\s-]*".join(escaped)


def _escape(word):
    """word as a pattern, е and ё standing for each other - the
    recogniser writes "еще" as often as "ещё"."""
    return "".join("[её]" if char in "еёЕЁ" else re.escape(char)
                   for char in word)


def _number(word):
    value = voice_scripture.number_value(word)
    return value if value is not None else word


def _matcher(options):
    # Longest first, so "Pre-Chorus" wins over the "Chorus" inside it.
    ordered = sorted(options, key=lambda o: len(_key(o["text"])),
                     reverse=True)
    words = "|".join(f"(?P<w{i}>{_pattern(o['text'])})"
                     for i, o in enumerate(ordered))
    matcher = re.compile(
        rf"(?<![\w-])(?:(?P<pre>{_ORDINAL})[\s-]+)?(?:{words})"
        rf"(?:[\s-]+(?:(?P<in>{_IN})[\s-]+)?(?P<num>{_NUMBER}))?(?![\w-])",
        re.IGNORECASE,
    )
    return ordered, matcher


def _words(text, options, offset):
    """(text, highlights) for the listed words alone, offsets moved on
    by `offset`."""
    if not options or not text:
        return text, []
    ordered, matcher = _matcher(options)

    out = []
    highlights = []
    last = 0
    length = offset

    for match in matcher.finditer(text):
        index = next(int(name[1:]) for name, value in match.groupdict().items()
                     if name.startswith("w") and value is not None)
        option = ordered[index]

        replacement = option["text"]
        said = match.group(f"w{index}")
        if _CYRILLIC.search(replacement) and \
                _key(said).replace("ё", "е") != _key(replacement).replace("ё", "е"):
            # A Russian word in another case - "после припева" - is kept
            # as said, only capitalised, rather than made ungrammatical.
            replacement = said[:1].upper() + said[1:]
        number = match.group("num")
        before_word = match.group("pre")
        if before_word and before_word.lower().startswith(("last",
                                                           "последн")):
            # "last chorus" - kept as said, highlighted with it.
            replacement = f"{before_word} {replacement}"
        elif before_word and not number:
            number = before_word
        if number:
            if match.group("in"):
                replacement += f" {match.group('in').lower()}"
            replacement += f" {_number(number)}"

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


def highlight(text, options, scripture=False):
    """(text, highlights): the transcript with every listed word in it
    written as the list spells it - and, with `scripture`, every
    reference the short way - and where each one is: a list of
    {"start", "end", "color"}, offsets in characters (code points) into
    the returned text, in order and never overlapping."""
    if not text or (not options and not scripture):
        return text, []

    references = voice_scripture.find(text) if scripture else []
    out = []
    highlights = []
    length = 0
    last = 0
    for start, end, reference in references + [(len(text), len(text), None)]:
        piece, found = _words(text[last:start], options, length)
        out.append(piece)
        highlights += found
        length += len(piece)
        if reference is not None:
            out.append(reference)
            highlights.append({"start": length, "end": length + len(reference),
                               "color": SCRIPTURE_COLOR})
            length += len(reference)
        last = end
    return "".join(out), highlights
