"""Scripture references in a transcript: "Matthew chapter four verse
five", "John three sixteen", "1 Corinthians 13:4-7", "От Матфея глава
четвёртая, стих пятый" - each found and written out the short way
("Matthew 4:5", "От Матфея 4:5") for the projection team to look up.

English and Russian (Synodal names and numbering - so 3 Царств is
1 Kings), and the numbers in either spoken as words or written as digits,
since the recogniser does both.

find(text) -> [(start, end, reference)] is all Voice to Text needs; the
words to digits work is also used by voice_highlights for "verse two".
"""

import re

# --- Numbers ---

_EN_WORDS = {}
for _value, _words in enumerate((
        ("zero",), ("one", "first"), ("two", "second"), ("three", "third"),
        ("four", "fourth"), ("five", "fifth"), ("six", "sixth"),
        ("seven", "seventh"), ("eight", "eighth"), ("nine", "ninth"))):
    for _word in _words:
        _EN_WORDS[_word] = (_value, "unit")
for _value, _words in enumerate((
        ("ten", "tenth"), ("eleven", "eleventh"), ("twelve", "twelfth"),
        ("thirteen", "thirteenth"), ("fourteen", "fourteenth"),
        ("fifteen", "fifteenth"), ("sixteen", "sixteenth"),
        ("seventeen", "seventeenth"), ("eighteen", "eighteenth"),
        ("nineteen", "nineteenth")), start=10):
    for _word in _words:
        _EN_WORDS[_word] = (_value, "teen")
for _value, _words in zip(range(20, 100, 10), (
        ("twenty", "twentieth"), ("thirty", "thirtieth"), ("forty", "fortieth"),
        ("fifty", "fiftieth"), ("sixty", "sixtieth"), ("seventy", "seventieth"),
        ("eighty", "eightieth"), ("ninety", "ninetieth"))):
    for _word in _words:
        _EN_WORDS[_word] = (_value, "tens")
_EN_WORDS.update({"hundred": (100, "hundred"), "hundredth": (100, "hundred"),
                  "1st": (1, "unit"), "2nd": (2, "unit"), "3rd": (3, "unit")})

# Russian numbers change their endings with case and gender - пятый,
# пятого, пятой, пятую - so they are matched by pattern, ё read as е.
_ORD = r"(?:ый|ий|ой|ое|ее|ого|его|ому|ему|ая|яя|ую|юю|ом|ем|ым|им|ые|ых)"
_RU_PATTERNS = [(re.compile(pattern), value, kind) for pattern, value, kind in (
    (r"сто|сотый|сотого|сотой|сотом|сотая|сотую", 100, "hundred"),
    (r"двадцат\w*", 20, "tens"), (r"тридцат\w*", 30, "tens"),
    (r"сорок\w*", 40, "tens"), (r"пят[ьи]десят\w*", 50, "tens"),
    (r"шест[ьи]десят\w*", 60, "tens"), (r"сем[ьи]десят\w*", 70, "tens"),
    (r"восем[ьи]десят\w*|восьмидесят\w*", 80, "tens"),
    (r"девяност\w*", 90, "tens"),
    (r"одиннадцат\w*", 11, "teen"), (r"двенадцат\w*", 12, "teen"),
    (r"тринадцат\w*", 13, "teen"), (r"четырнадцат\w*", 14, "teen"),
    (r"пятнадцат\w*", 15, "teen"), (r"шестнадцат\w*", 16, "teen"),
    (r"семнадцат\w*", 17, "teen"), (r"восемнадцат\w*", 18, "teen"),
    (r"девятнадцат\w*", 19, "teen"),
    (r"десять|десяти|десят" + _ORD, 10, "teen"),
    (r"один|одна|одно|одного|одной|одному|перв" + _ORD, 1, "unit"),
    (r"два|две|двух|втор" + _ORD, 2, "unit"),
    (r"три|трех|трем|трет(?:ий|ья|ье|ьего|ьей|ью|ьем|ьи)", 3, "unit"),
    (r"четыре|четырех|четверт" + _ORD, 4, "unit"),
    (r"пять|пяти|пят" + _ORD, 5, "unit"),
    (r"шесть|шести|шест" + _ORD, 6, "unit"),
    (r"семь|семи|седьм" + _ORD, 7, "unit"),
    (r"восемь|восьми|восьм" + _ORD, 8, "unit"),
    (r"девять|девяти|девят" + _ORD, 9, "unit"),
)]


def _fold(word):
    return word.lower().replace("ё", "е")


def number_word(word):
    """(value, kind) for one spoken number word, English or Russian, or
    None. kind is "unit" (0-9), "teen" (10-19), "tens" or "hundred"."""
    word = _fold(word)
    if word in _EN_WORDS:
        return _EN_WORDS[word]
    for pattern, value, kind in _RU_PATTERNS:
        if pattern.fullmatch(word):
            return value, kind
    return None


_TOKEN = re.compile(r"\d+|[^\W\d_]+|[:\-–—,.]")


def _tokens(text):
    return [(m.start(), m.end(), m.group()) for m in _TOKEN.finditer(text)]


def _number(tokens, i):
    """(value, next index) for the number starting at tokens[i] - digits,
    or up to "one hundred and nineteen" in words - else None."""
    if i >= len(tokens):
        return None
    word = tokens[i][2]
    if word.isdigit():
        return (int(word), i + 1) if len(word) <= 3 else None

    first = number_word(word)
    if first is None:
        return None

    def at(j):
        return number_word(tokens[j][2]) if j < len(tokens) else None

    value, kind = first
    j = i + 1
    total = 0
    # "one oh five" - a verse said digit by digit.
    if kind == "unit" and value and j + 1 < len(tokens) and \
            _fold(tokens[j][2]) in ("oh", "o"):
        last = at(j + 1)
        if last is not None and last[1] == "unit":
            return value * 100 + last[0], j + 2
    if kind == "unit" and value and (at(j) or (0, ""))[1] == "hundred":
        total, j = value * 100, j + 1
    elif kind == "hundred":
        total = 100
    if total:
        if j < len(tokens) and _fold(tokens[j][2]) == "and":
            nxt = at(j + 1)
            if nxt is None or nxt[1] == "hundred":
                return total, j
            j += 1
        following = at(j)
        if following is None or following[1] == "hundred":
            return total, j
        value, kind = following
        j += 1

    if kind == "tens":
        total += value
        # "twenty-three" as well as "twenty three".
        k = j + 1 if j < len(tokens) and tokens[j][2] == "-" else j
        unit = at(k)
        if unit is not None and unit[1] == "unit" and unit[0]:
            return total + unit[0], k + 1
        return total, j
    return total + value, j


def number_value(word):
    """A single word or digits as the number it is, or None."""
    if word.isdigit():
        return int(word)
    found = number_word(word)
    return found[0] if found else None


# --- Books ---
#
# (English name, Russian name, chapters, English names as spoken,
#  Russian names as spoken). A name as spoken is a space-separated row of
# patterns, one per word, matched whole against the lowercased word with
# ё read as е. A book with a number in front - 1 Corinthians - has it as
# "N:" at the start of each name. A Russian name may be preceded by "от",
# as the gospels are ("От Матфея"), which is kept in the match.

_BOOKS = (
    ("Genesis", "Бытие", 50, ["genesis"], ["быти[еяю]"]),
    ("Exodus", "Исход", 40, ["exodus"], ["исход\\w*"]),
    ("Leviticus", "Левит", 27, ["leviticus"], ["левит\\w*"]),
    ("Numbers", "Числа", 36, ["numbers"], ["числ\\w*"]),
    ("Deuteronomy", "Второзаконие", 34, ["deuteronomy"], ["второзакони\\w*"]),
    ("Joshua", "Иисус Навин", 24, ["joshua"],
     ["иисус\\w* навин\\w*", "навин\\w*"]),
    ("Judges", "Судьи", 21, ["judges"], ["суд(?:ьи|ей|ьям)"]),
    ("Ruth", "Руфь", 4, ["ruth"], ["руф(?:ь|и)"]),
    ("1 Samuel", "1 Царств", 31, ["1: samuel"], ["1: царств\\w*", "1: самуил\\w*"]),
    ("2 Samuel", "2 Царств", 24, ["2: samuel"], ["2: царств\\w*", "2: самуил\\w*"]),
    ("1 Kings", "3 Царств", 22, ["1: kings?"], ["3: царств\\w*"]),
    ("2 Kings", "4 Царств", 25, ["2: kings?"], ["4: царств\\w*"]),
    ("1 Chronicles", "1 Паралипоменон", 29, ["1: chronicles?"],
     ["1: паралипоменон\\w*", "1: хроник\\w*", "1: летопис\\w*"]),
    ("2 Chronicles", "2 Паралипоменон", 36, ["2: chronicles?"],
     ["2: паралипоменон\\w*", "2: хроник\\w*", "2: летопис\\w*"]),
    ("Ezra", "Ездра", 10, ["ezra"], ["ездр\\w*"]),
    ("Nehemiah", "Неемия", 13, ["nehemiah"], ["неем\\w*"]),
    ("Esther", "Есфирь", 10, ["esther"], ["есфир\\w*", "эсфир\\w*"]),
    ("Job", "Иов", 42, ["job"], ["иов\\w*"]),
    ("Psalm", "Псалом", 150, ["psalms?", "psalter"],
     ["псал(?:ом|ма|мы|ме|мов|тирь|тири)"]),
    ("Proverbs", "Притчи", 31, ["proverbs?"], ["притч\\w*"]),
    ("Ecclesiastes", "Екклесиаст", 12, ["ecclesiastes"],
     ["е?кклесиаст\\w*", "экклезиаст\\w*"]),
    ("Song of Solomon", "Песнь Песней", 8,
     ["songs? of solomon", "songs? of songs"], ["песн[ьи] песней"]),
    ("Isaiah", "Исаия", 66, ["isaiah"], ["исаи[яиюе]"]),
    ("Jeremiah", "Иеремия", 52, ["jeremiah"], ["иереми[яиюе]"]),
    ("Lamentations", "Плач Иеремии", 5, ["lamentations?"],
     ["плач\\w* иереми\\w*"]),
    ("Ezekiel", "Иезекииль", 48, ["ezekiel"], ["иезекиил\\w*"]),
    ("Daniel", "Даниил", 12, ["daniel"], ["даниил\\w*"]),
    ("Hosea", "Осия", 14, ["hosea"], ["оси[яиюе]"]),
    ("Joel", "Иоиль", 3, ["joel"], ["иоил\\w*"]),
    ("Amos", "Амос", 9, ["amos"], ["амос\\w*"]),
    ("Obadiah", "Авдий", 1, ["obadiah"], ["авди\\w*"]),
    ("Jonah", "Иона", 4, ["jonah"], ["ион[аыуе]"]),
    ("Micah", "Михей", 7, ["micah"], ["михе[йяюе]"]),
    ("Nahum", "Наум", 3, ["nahum"], ["наум\\w*"]),
    ("Habakkuk", "Аввакум", 3, ["habakkuk"], ["аввакум\\w*"]),
    ("Zephaniah", "Софония", 3, ["zephaniah"], ["софони\\w*"]),
    ("Haggai", "Аггей", 2, ["haggai"], ["агге[йяюе]"]),
    ("Zechariah", "Захария", 14, ["zechariah"], ["захари\\w*"]),
    ("Malachi", "Малахия", 4, ["malachi"], ["малахи\\w*"]),
    ("Matthew", "От Матфея", 28, ["matthew"], ["матфе\\w*"]),
    ("Mark", "От Марка", 16, ["mark"], ["марк[аеу]?"]),
    ("Luke", "От Луки", 24, ["luke"], ["лук[аиеу]"]),
    ("John", "От Иоанна", 21, ["john"], ["иоанн\\w*"]),
    ("Acts", "Деяния", 28, ["acts"], ["деяни\\w*"]),
    ("Romans", "Римлянам", 16, ["romans"], ["римлян\\w*"]),
    ("1 Corinthians", "1 Коринфянам", 16, ["1: corinthians"], ["1: коринфян\\w*"]),
    ("2 Corinthians", "2 Коринфянам", 13, ["2: corinthians"], ["2: коринфян\\w*"]),
    ("Galatians", "Галатам", 6, ["galatians"], ["галат\\w*"]),
    ("Ephesians", "Ефесянам", 6, ["ephesians"], ["ефесян\\w*", "эфесян\\w*"]),
    ("Philippians", "Филиппийцам", 4, ["philippians"], ["филиппийц\\w*"]),
    ("Colossians", "Колоссянам", 4, ["colossians"], ["колоссян\\w*"]),
    ("1 Thessalonians", "1 Фессалоникийцам", 5, ["1: thessalonians"],
     ["1: фессалоники\\w*"]),
    ("2 Thessalonians", "2 Фессалоникийцам", 3, ["2: thessalonians"],
     ["2: фессалоники\\w*"]),
    ("1 Timothy", "1 Тимофею", 6, ["1: timothy"], ["1: тимофе\\w*"]),
    ("2 Timothy", "2 Тимофею", 4, ["2: timothy"], ["2: тимофе\\w*"]),
    ("Titus", "Титу", 3, ["titus"], ["тит[ау]"]),
    ("Philemon", "Филимону", 1, ["philemon"], ["филимон\\w*"]),
    ("Hebrews", "Евреям", 13, ["hebrews"], ["евре\\w*"]),
    ("James", "Иакова", 5, ["james"], ["иаков\\w*"]),
    ("1 Peter", "1 Петра", 5, ["1: peter"], ["1: петр\\w*"]),
    ("2 Peter", "2 Петра", 3, ["2: peter"], ["2: петр\\w*"]),
    ("1 John", "1 Иоанна", 5, ["1: john"], ["1: иоанн\\w*"]),
    ("2 John", "2 Иоанна", 1, ["2: john"], ["2: иоанн\\w*"]),
    ("3 John", "3 Иоанна", 1, ["3: john"], ["3: иоанн\\w*"]),
    ("Jude", "Иуды", 1, ["jude"], ["иуд[аыу]"]),
    ("Revelation", "Откровение", 22, ["revelations?"], ["откровени\\w*"]),
)

# Books named by an everyday word - "mark", "acts", "числа" (dates) -
# are only taken with "chapter" said, or a verse as well.
_EVERYDAY = {"Mark", "Job", "Acts", "Numbers", "James", "Judges", "Titus",
             "Jude", "Ruth", "Amos", "Jonah", "Luke"}

# Between a book's number and its name: "First letter to the
# Corinthians", "Первое послание к Коринфянам".
_LINKS = {"letter", "epistle", "to", "the", "of", "послание", "послания",
          "посланию", "к", "ко"}
# Ordinal endings written after a digit: "1-е", "2-го".
_SUFFIX = re.compile(r"(?:е|я|й|го|ое|ая|ого|ой)")

_CHAPTER = re.compile(r"chapters?|глав[аеуы]?")
_VERSE = re.compile(r"verses?|стих(?:а|и|е|ов|ам|ами|ах)?")
_RANGE = re.compile(r"[-–—]|to|through|thru|по|до")

MAX_VERSE = 176


def _compile():
    names = []
    for english, russian, chapters, en_names, ru_names in _BOOKS:
        for lang, spoken, shown in (("en", en_names, english),
                                    ("ru", ru_names, russian)):
            for name in spoken:
                prefix = None
                if name[:1].isdigit() and name[1:2] == ":":
                    prefix, name = int(name[0]), name[2:].strip()
                words = [re.compile(part) for part in name.split()]
                names.append({"english": english, "shown": shown, "lang": lang,
                              "chapters": chapters, "prefix": prefix,
                              "words": words,
                              "everyday": english in _EVERYDAY})
    # Longer names first, numbered ones before the plain - "1 John"
    # before "John".
    names.sort(key=lambda n: (n["prefix"] is None, -len(n["words"])))
    return names


_NAMES = _compile()


def numbered_books():
    """[(number, English name without it, name with it)] - for spelling
    "first corinthians" as "1 Corinthians" wherever it is said."""
    found = []
    for english, _ru, _chapters, _en, _rn in _BOOKS:
        if english[0].isdigit():
            found.append((int(english[0]), english[2:], english))
    return found


def _match_name(tokens, i, name):
    """The index after `name` matched at tokens[i], or None."""
    j = i
    if name["prefix"] is not None:
        found = _number(tokens, j)
        if found is None or found[0] != name["prefix"] or found[1] != j + 1:
            return None
        j = found[1]
        if j + 1 < len(tokens) and tokens[j][2] == "-" and \
                _SUFFIX.fullmatch(_fold(tokens[j + 1][2])):
            j += 2
        skipped = 0
        while j < len(tokens) and _fold(tokens[j][2]) in _LINKS and skipped < 3:
            j, skipped = j + 1, skipped + 1
    for pattern in name["words"]:
        if j >= len(tokens) or not pattern.fullmatch(_fold(tokens[j][2])):
            return None
        j += 1
    return j


def _match_numbers(tokens, j, name):
    """(chapter, verse, end verse, index after) following a book's name,
    or None."""
    keyword = False
    if j < len(tokens) and _CHAPTER.fullmatch(_fold(tokens[j][2])):
        keyword, j = True, j + 1
    found = _number(tokens, j)
    if found is None:
        return None
    chapter, j = found

    verse = end = None
    k = j
    separator = None
    if k < len(tokens) and tokens[k][2] in (":", ",", "."):
        separator, k = tokens[k][2], k + 1
    said_verse = k < len(tokens) and _VERSE.fullmatch(_fold(tokens[k][2]))
    if said_verse:
        k += 1
    after = _number(tokens, k)
    # "3:16", "verse 16", "3 16" and "three sixteen" all give a verse; a
    # bare comma or full stop only between digits - "Psalm 23, one of my
    # favourites" is chapter 23 alone.
    if after is not None and (
            separator in (None, ":") or said_verse
            or (tokens[j - 1][2].isdigit() and tokens[k][2].isdigit())):
        verse, j = after
        if j + 1 < len(tokens) and _RANGE.fullmatch(_fold(tokens[j][2])):
            upto = _number(tokens, j + 1)
            if upto is not None and upto[0] > verse:
                end, j = upto

    chapters = name["chapters"]
    if chapters == 1 and verse is None and not keyword:
        # "Jude 3" is its third verse - it has only the one chapter.
        chapter, verse = 1, chapter
    if not 1 <= chapter <= chapters:
        return None
    if verse is not None and not 1 <= verse <= MAX_VERSE:
        return None
    if end is not None and end > MAX_VERSE:
        end = None
    if name["everyday"] and not keyword and verse is None:
        return None
    return chapter, verse, end, j


def _reference(name, chapter, verse, end):
    text = f"{name['shown']} {chapter}"
    if verse is not None:
        text += f":{verse}"
        if end is not None:
            text += f"-{end}"
    return text


def find(text):
    """[(start, end, reference)] - every scripture reference in text, by
    character offsets, in order and never overlapping."""
    tokens = _tokens(text)
    found = []
    i = 0
    while i < len(tokens):
        start = i
        # "от Матфея" - the "от" goes with the gospel's name.
        lead = _fold(tokens[i][2]) == "от"
        match = None
        for offset in ((1, 0) if lead else (0,)):
            for name in _NAMES:
                if offset and not name["shown"].startswith("От "):
                    continue
                j = _match_name(tokens, i + offset, name)
                if j is None:
                    continue
                numbers = _match_numbers(tokens, j, name)
                if numbers is not None:
                    match = name, numbers
                    break
            if match:
                break
        if match is None:
            i += 1
            continue
        name, (chapter, verse, end, j) = match
        found.append((tokens[start][0], tokens[j - 1][1],
                      _reference(name, chapter, verse, end)))
        i = j
    return found
