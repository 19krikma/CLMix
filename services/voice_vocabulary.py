"""Voice to Text's corrections: words the recogniser gets wrong, put right
before anything else sees the text - "mathew" is written "Matthew",
"first corinthians" "1 Corinthians".

The built-in map below covers worship terms and Bible book names. The
operator's own go in CORRECTIONS_FILE, a JSON object of
{"what is heard": "what to write"}, read again whenever it changes; an
entry there overrides a built-in one of the same spelling. Matching is
whole words, ignoring case.
"""

import json
import re
import threading
from pathlib import Path

from services import voice_scripture
from services.log_store import log

CORRECTIONS_FILE = Path.home() / ".clmix_voice" / "corrections.json"

DEFAULT_CORRECTIONS = {
    # Worship terms.
    "pre course": "Pre-Chorus",
    "pre-course": "Pre-Chorus",
    "precourse": "Pre-Chorus",
    "pre chorus": "Pre-Chorus",
    "prechorus": "Pre-Chorus",
    "re-frain": "Refrain",
    "out row": "Outro",
    "outtro": "Outro",
    "inter lude": "Interlude",
    "предприпев": "Предприпев",
    "пред припев": "Предприпев",
    "бриж": "Бридж",
    "бридш": "Бридж",
    # Bible books, as they are misheard or misspelt.
    "mathew": "Matthew",
    "matthews": "Matthew",
    "mathews": "Matthew",
    "philipians": "Philippians",
    "phillipians": "Philippians",
    "phillippians": "Philippians",
    "colosians": "Colossians",
    "collosians": "Colossians",
    "galations": "Galatians",
    "efesians": "Ephesians",
    "thesalonians": "Thessalonians",
    "corinthins": "Corinthians",
    "revelations": "Revelation",
    "deuteronomie": "Deuteronomy",
    "duteronomy": "Deuteronomy",
    "ecclesiastics": "Ecclesiastes",
    "habakuk": "Habakkuk",
    "habbakuk": "Habakkuk",
    "zephania": "Zephaniah",
    "zecharia": "Zechariah",
    "zachariah": "Zechariah",
    "malachai": "Malachi",
    "isaiha": "Isaiah",
    "isiah": "Isaiah",
    "jeremia": "Jeremiah",
    "ezekial": "Ezekiel",
    "nehemia": "Nehemiah",
    "obediah": "Obadiah",
    "levitikus": "Leviticus",
    "levitticus": "Leviticus",
    "phileman": "Philemon",
    "song of solomon's": "Song of Solomon",
    "матвея": "Матфея",
    "от матвея": "от Матфея",
    # "От Иоанна" heard as a name - "от Анна" is never right, and there
    # is no Gospel of Anna.
    "от анна": "от Иоанна",
    "евангелие от анны": "Евангелие от Иоанна",
    "иоан": "Иоанна",
    "иоана": "Иоанна",
    "коринфяном": "Коринфянам",
    "филипийцам": "Филиппийцам",
    "филлипийцам": "Филиппийцам",
    "колосянам": "Колоссянам",
    "ефесяном": "Ефесянам",
}


def _numbered():
    """"first corinthians" -> "1 Corinthians", and the like, for every
    numbered book."""
    spoken = {1: ("first", "1st"), 2: ("second", "2nd"), 3: ("third", "3rd")}
    found = {}
    for number, name, written in voice_scripture.numbered_books():
        for word in spoken[number]:
            found[f"{word} {name.lower()}"] = written
    return found


_lock = threading.Lock()
_cache = {"mtime": None, "pattern": None, "map": {}}


def _user_corrections():
    """The operator's own, or {} - and the file's mtime, for the cache."""
    try:
        mtime = CORRECTIONS_FILE.stat().st_mtime
    except OSError:
        return {}, None
    try:
        raw = json.loads(CORRECTIONS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as ex:
        log("warning", f"Voice to Text: {CORRECTIONS_FILE} is not valid "
                       f"JSON, so it is ignored: {ex}")
        return {}, mtime
    if not isinstance(raw, dict):
        return {}, mtime
    return {key: value for key, value in raw.items()
            if isinstance(key, str) and isinstance(value, str)
            and key.strip() and not key.startswith("_")}, mtime


def _key(text):
    return " ".join(text.lower().replace("ё", "е").split())


def _compiled():
    user, mtime = _user_corrections()
    with _lock:
        if _cache["pattern"] is not None and _cache["mtime"] == mtime:
            return _cache["pattern"], _cache["map"]
        mapping = {_key(k): v for k, v in {**_numbered(), **DEFAULT_CORRECTIONS}
                   .items()}
        mapping.update({_key(k): v for k, v in user.items()})
        # Longest first, so "first corinthians" wins over "corinthian".
        heard = sorted(mapping, key=len, reverse=True)
        pattern = re.compile(
            r"(?<!\w)(?:" + "|".join(
                r"[\s-]+".join(re.escape(part).replace("е", "[её]")
                               for part in phrase.split())
                for phrase in heard) + r")(?!\w)",
            re.IGNORECASE)
        _cache.update(mtime=mtime, pattern=pattern, map=mapping)
        return pattern, mapping


def correct(text):
    """text with every known misrecognition put right."""
    if not text:
        return text
    pattern, mapping = _compiled()

    def replace(match):
        found = _key(re.sub(r"[\s-]+", " ", match.group()))
        return mapping.get(found, match.group())

    fixed = pattern.sub(replace, text)
    return fixed[:1].upper() + fixed[1:]


def corrections_file():
    """The operator's corrections file, made with an example first if
    there is none yet - for Settings to open in an editor."""
    if not CORRECTIONS_FILE.exists():
        CORRECTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        example = {
            "_about": "What Voice to Text hears wrong, and what to write "
                      "instead - one \"heard\": \"written\" pair per line. "
                      "Whole words, any case. Keys starting with _ are "
                      "ignored. Saved changes apply straight away.",
            "pastor dave": "Pastor Dave",
            "hill song": "Hillsong",
        }
        CORRECTIONS_FILE.write_text(json.dumps(example, indent=2,
                                               ensure_ascii=False) + "\n",
                                    encoding="utf-8")
    return CORRECTIONS_FILE
