"""Scripture references in a transcript: "Matthew chapter four verse
five", "John three sixteen", "1 Corinthians 13:4-7", "От Матфея глава
четвёртая, стих пятый" - each found and written out the short way
("Matthew 4:5", "От Матфея 4:5") for the projection team to look up.

English, Russian (Synodal names and numbering - so 3 Царств is
1 Kings) and Ukrainian (the names of Ohienko's translation - "Від
Матвія", "До римлян"), and the numbers in any of them spoken as words or
written as digits, since the recogniser does both. Many names are the
same in Russian and Ukrainian; which is written comes from the language
Whisper heard, else from the letters - і, ї, є and ґ are Ukrainian's
alone.

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
# Ukrainian's, the same way.
_UK = r"(?:ий|ій|ого|ому|им|ім|а|ої|у|ою|е|і|их|ими)"
_RU_PATTERNS = [(re.compile(pattern), value, kind) for pattern, value, kind in (
    (r"сто|сотый|сотого|сотой|сотом|сотая|сотую|сотий|сотому|сота|сотої|соту",
     100, "hundred"),
    (r"п'ятдесят\w*", 50, "tens"), (r"шістдесят\w*", 60, "tens"),
    (r"сімдесят\w*", 70, "tens"), (r"вісімдесят\w*", 80, "tens"),
    (r"дев'яност\w*", 90, "tens"),
    (r"двадцят\w*", 20, "tens"), (r"тридцят\w*", 30, "tens"),
    (r"одинадцят\w*", 11, "teen"), (r"дванадцят\w*", 12, "teen"),
    (r"тринадцят\w*", 13, "teen"), (r"чотирнадцят\w*", 14, "teen"),
    (r"п'ятнадцят\w*", 15, "teen"), (r"шістнадцят\w*", 16, "teen"),
    (r"сімнадцят\w*", 17, "teen"), (r"вісімнадцят\w*", 18, "teen"),
    (r"дев'ятнадцят\w*", 19, "teen"), (r"десят" + _UK, 10, "teen"),
    (r"одне|перш" + _UK, 1, "unit"),
    (r"дві|друг(?:ий|ого|ому|им|ім|а|ої|ій|у|ою|е|і|их)", 2, "unit"),
    (r"трьох|трет(?:ій|ього|ьому|ім|я|ьої|ю|ьою|є|і)", 3, "unit"),
    (r"чотири|чотирьох|четверт" + _UK, 4, "unit"),
    (r"п'ять|п'яти|п'ят" + _UK, 5, "unit"),
    (r"шість|шост" + _UK, 6, "unit"),
    (r"сім|сьом" + _UK, 7, "unit"),
    (r"вісім|восьм" + _UK, 8, "unit"),
    (r"дев'ять|дев'яти|дев'ят" + _UK, 9, "unit"),
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
    return word.lower().replace("ё", "е").replace("’", "'").replace("ʼ", "'")


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


# Endings a Russian ordinal takes after its digits: "5-я", "5-го".
_DIGIT_SUFFIX = re.compile(r"(?:е|я|й|ю|м|х|го|ое|ая|ой|ую|ом|ый|ий|ых|ми|ти)")

# A word may have an apostrophe inside it: Ukrainian's "п'ять".
_TOKEN = re.compile(r"\d+|[^\W\d_]+(?:['’ʼ][^\W\d_]+)*|[:\-–—,.]")


def _tokens(text):
    return [(m.start(), m.end(), m.group()) for m in _TOKEN.finditer(text)]


def _number(tokens, i):
    """(value, next index) for the number starting at tokens[i] - digits,
    or up to "one hundred and nineteen" in words - else None."""
    if i >= len(tokens):
        return None
    word = tokens[i][2]
    if word.isdigit():
        if len(word) > 3:
            return None
        j = i + 1
        # "5th", and "5-я", "5-го" - an ending written onto the digits.
        if j < len(tokens) and tokens[j][0] == tokens[i][1]:
            if _fold(tokens[j][2]) in ("th", "st", "nd", "rd"):
                j += 1
            elif tokens[j][2] == "-" and j + 1 < len(tokens) and \
                    _DIGIT_SUFFIX.fullmatch(_fold(tokens[j + 1][2])):
                j += 2
        return int(word), j

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

# Ukrainian: {English name: (name as written, names as spoken)}, read
# the same way as the Russian above. "Від" before a gospel and "до"
# before an epistle are taken with the name, as "от" is.
_UKRAINIAN = {
    "Genesis": ("Буття", ["бутт\\w*"]),
    "Exodus": ("Вихід", ["вих[іо]д\\w*"]),
    "Leviticus": ("Левит", ["левит\\w*"]),
    "Numbers": ("Числа", ["числ\\w*"]),
    "Deuteronomy": ("Повторення Закону", ["повторенн\\w* закон\\w*"]),
    "Joshua": ("Ісус Навин", ["ісус\\w* навин\\w*", "навин\\w*"]),
    "Judges": ("Судді", ["судд(?:і|ів|ям)"]),
    "Ruth": ("Рут", ["рут[иі]?"]),
    "1 Samuel": ("1 Самуїлова", ["1: самуїл\\w*"]),
    "2 Samuel": ("2 Самуїлова", ["2: самуїл\\w*"]),
    "1 Kings": ("1 Царів", ["1: цар(?:ів|ям|ях)"]),
    "2 Kings": ("2 Царів", ["2: цар(?:ів|ям|ях)"]),
    "1 Chronicles": ("1 Хроніки", ["1: хронік\\w*", "1: літопис\\w*"]),
    "2 Chronicles": ("2 Хроніки", ["2: хронік\\w*", "2: літопис\\w*"]),
    "Ezra": ("Ездра", ["ездр\\w*"]),
    "Nehemiah": ("Неемія", ["неемі\\w*"]),
    "Esther": ("Естер", ["естер\\w*"]),
    "Job": ("Йов", ["йов\\w*"]),
    "Psalm": ("Псалом", ["псал(?:ом|ма|ми|мі|мів|тир\\w*)"]),
    "Proverbs": ("Приповісті", ["приповіст\\w*"]),
    "Ecclesiastes": ("Екклезіяст", ["е?кклезіяст\\w*", "проповідник\\w*"]),
    "Song of Solomon": ("Пісня над піснями",
                        ["пісн[яі] над пісн\\w*", "пісн[яі] пісень"]),
    "Isaiah": ("Ісая", ["іса[яїю]\\w*"]),
    "Jeremiah": ("Єремія", ["єремі\\w*"]),
    "Lamentations": ("Плач Єремії", ["плач\\w* єремі\\w*"]),
    "Ezekiel": ("Єзекіїль", ["єзекіїл\\w*"]),
    "Daniel": ("Даниїл", ["даниїл\\w*"]),
    "Hosea": ("Осія", ["осі[яїю]"]),
    "Joel": ("Йоїл", ["йоїл\\w*"]),
    "Amos": ("Амос", ["амос\\w*"]),
    "Obadiah": ("Овдій", ["овді\\w*"]),
    "Jonah": ("Йона", ["йон[аиі]"]),
    "Micah": ("Михей", ["ми[хк]е\\w*"]),
    "Nahum": ("Наум", ["наум\\w*"]),
    "Habakkuk": ("Авакум", ["авакум\\w*"]),
    "Zephaniah": ("Софонія", ["софоні\\w*"]),
    "Haggai": ("Огій", ["огі[йяї]"]),
    "Zechariah": ("Захарія", ["захарі\\w*"]),
    "Malachi": ("Малахія", ["малахі\\w*"]),
    "Matthew": ("Від Матвія", ["матві\\w*", "матфі\\w*"]),
    "Mark": ("Від Марка", ["марк[аоу]?"]),
    "Luke": ("Від Луки", ["лук[аиі]"]),
    "John": ("Від Івана", ["іван\\w*"]),
    "Acts": ("Дії", ["ді[їя]"]),
    "Romans": ("До римлян", ["римлян\\w*"]),
    "1 Corinthians": ("1 до коринтян", ["1: коринтян\\w*", "1: коринфян\\w*"]),
    "2 Corinthians": ("2 до коринтян", ["2: коринтян\\w*", "2: коринфян\\w*"]),
    "Galatians": ("До галатів", ["галат\\w*"]),
    "Ephesians": ("До ефесян", ["ефесян\\w*"]),
    "Philippians": ("До филип'ян", ["фил[иі]пп?'ян\\w*"]),
    "Colossians": ("До колоссян", ["колосс?ян\\w*"]),
    "1 Thessalonians": ("1 до солунян", ["1: солунян\\w*"]),
    "2 Thessalonians": ("2 до солунян", ["2: солунян\\w*"]),
    "1 Timothy": ("1 до Тимофія", ["1: тимофі\\w*"]),
    "2 Timothy": ("2 до Тимофія", ["2: тимофі\\w*"]),
    "Titus": ("До Тита", ["тит[аові]"]),
    "Philemon": ("До Филимона", ["ф[иі]лимон\\w*"]),
    "Hebrews": ("До євреїв", ["євре\\w*"]),
    "James": ("Якова", ["яков\\w*"]),
    "1 Peter": ("1 Петра", ["1: петр\\w*"]),
    "2 Peter": ("2 Петра", ["2: петр\\w*"]),
    "1 John": ("1 Івана", ["1: іван\\w*"]),
    "2 John": ("2 Івана", ["2: іван\\w*"]),
    "3 John": ("3 Івана", ["3: іван\\w*"]),
    "Jude": ("Юди", ["юд[иа]"]),
    "Revelation": ("Об'явлення", ["об'явленн\\w*", "одкровенн\\w*"]),
}

# Words taken with the name that follows them: "от Матфея", "від
# Матвія", "до римлян" - each for the names written with it.
_LEADS = {"от": "От ", "від": "Від ", "до": "До "}

# Books named by an everyday word - "mark", "acts", "числа" (dates) -
# are only taken with "chapter" said, or a verse as well.
_EVERYDAY = {"Mark", "Job", "Acts", "Numbers", "James", "Judges", "Titus",
             "Jude", "Ruth", "Amos", "Jonah", "Luke"}

# Between a book's number and its name: "First letter to the
# Corinthians", "Первое послание к Коринфянам".
_LINKS = {"letter", "epistle", "to", "the", "of", "послание", "послания",
          "посланию", "к", "ко", "послання", "посланні", "до"}
# Ordinal endings written after a digit: "1-е", "2-го".
_SUFFIX = re.compile(r"(?:е|я|й|го|ое|ая|ого|ой)")

_CHAPTER = re.compile(r"chapters?|глав(?:а|е|у|ы|ой|ою|і|и)?"
                      r"|розділ(?:у|і|ом|и|ів|ах)?")
_VERSE = re.compile(r"verses?|стих(?:а|и|е|у|ов|ам|ами|ах|ом)?"
                    r"|вірш(?:а|і|у|ів|ем|ах|ами)?")
_RANGE = re.compile(r"[-–—]|to|through|thru|till|until|по|до")
# Said before a verse or range: "from verse 5", "стихи с 5 по 7".
_FROM = {"from", "с", "со", "из", "от", "з", "із", "зі", "від"}
# Words between a chapter or verse and the book it is in: "chapter 5
# from Matthew", "fifth chapter of the Gospel of Matthew", "пятую главу
# в Евангелии от Матфея".
_OF = {"of", "from", "in", "the", "book", "gospel", "according", "to",
       "letter", "epistle", "из", "в", "во", "книги", "книга", "книге",
       "книгу", "евангелия", "евангелие", "евангелии", "евангелию",
       "послания", "послание", "послании", "посланию", "к", "ко", "на",
       "з", "із", "у", "книзі", "євангелія", "євангеліє", "євангелії",
       "послання", "посланні"}
# Most of those in a row before the book's name.
MAX_OF = 4

MAX_VERSE = 176


def _compile():
    names = []
    for english, russian, chapters, en_names, ru_names in _BOOKS:
        ukrainian, uk_names = _UKRAINIAN[english]
        for lang, spoken, shown in (("en", en_names, english),
                                    ("ru", ru_names, russian),
                                    ("uk", uk_names, ukrainian)):
            for name in spoken:
                prefix = None
                if name[:1].isdigit() and name[1:2] == ":":
                    prefix, name = int(name[0]), name[2:].strip()
                words = [re.compile(part) for part in name.split()]
                lead = next((word for word, start in _LEADS.items()
                             if shown.startswith(start)), None)
                names.append({"english": english, "shown": shown, "lang": lang,
                              "chapters": chapters, "prefix": prefix,
                              "words": words, "lead": lead,
                              "everyday": english in _EVERYDAY})
    return names


def _ordered(names, prefer):
    """names to try in order: longer names first, numbered ones before
    the plain - "1 John" before "John" - and of a Russian and a
    Ukrainian name spelt alike, the `prefer`red language's."""
    return sorted(names, key=lambda n: (n["prefix"] is None, -len(n["words"]),
                                        n["lang"] != prefer))


_ALL_NAMES = _compile()
_NAMES = {lang: _ordered(_ALL_NAMES, lang) for lang in ("ru", "uk")}
_UKRAINIAN_LETTERS = re.compile(r"[іїєґІЇЄҐ]")


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


def _word(tokens, j):
    return _fold(tokens[j][2]) if j < len(tokens) else ""


def _chapter(tokens, j):
    """(chapter, index after) for a chapter said with the word -
    "chapter 5", "fifth chapter", "главу пятую", "5-я глава" - or None."""
    if _CHAPTER.fullmatch(_word(tokens, j)):
        found = _number(tokens, j + 1)
        return found
    found = _number(tokens, j)
    if found is not None and _CHAPTER.fullmatch(_word(tokens, found[1])):
        return found[0], found[1] + 1
    return None


def _range(tokens, j, verse):
    """(end verse, index after) for "to 7", "- 7", "по седьмой", "and 6"
    following a verse, or (None, j)."""
    word = _word(tokens, j)
    if _RANGE.fullmatch(word) or word in ("and", "и"):
        k = j + 1
        if _VERSE.fullmatch(_word(tokens, k)):
            k += 1
        upto = _number(tokens, k)
        if upto is not None and upto[0] > verse and \
                (word not in ("and", "и") or upto[0] == verse + 1):
            return upto
    return None, j


def _verse(tokens, j):
    """(verse, end verse or None, index after) for verses said with the
    word - "verse 5", "verses 5 to 7", "from verse 5 to verse 7",
    "стихи с 5 по 7", "с пятого по седьмой стих", "fifth verse" - or
    None."""
    k = j + 1 if _word(tokens, j) in _FROM else j
    if _VERSE.fullmatch(_word(tokens, k)):
        k += 1
        if _word(tokens, k) in _FROM:
            k += 1
        found = _number(tokens, k)
        if found is None:
            return None
        end, k = _range(tokens, found[1], found[0])
        return found[0], end, k
    found = _number(tokens, k)
    if found is None:
        return None
    end, k = _range(tokens, found[1], found[0])
    if _VERSE.fullmatch(_word(tokens, k)):
        return found[0], end, k + 1
    return None


def _skip_of(tokens, j):
    skipped = 0
    while _word(tokens, j) in _OF and skipped < MAX_OF:
        j, skipped = j + 1, skipped + 1
    return j


def _skip_comma(tokens, j):
    return j + 1 if j < len(tokens) and tokens[j][2] == "," else j


def _books(tokens, j, names):
    """Every (name, index after) that a book's name at tokens[j] could
    be, trying `names` in order - "от Матфея", "від Матвія", "до
    римлян" taken whole."""
    lead = _word(tokens, j)
    for name in names:
        if name["lead"] is not None and name["lead"] == lead:
            after = _match_name(tokens, j + 1, name)
            if after is not None:
                yield name, after
        after = _match_name(tokens, j, name)
        if after is not None:
            yield name, after


def _bare(tokens, j):
    """(chapter, verse, end, index after) for numbers straight after a
    book's name - "5", "5:5", "5 5", "five five", "5, verses 5 to 7" -
    or None."""
    found = _number(tokens, j)
    if found is None:
        return None
    chapter, j = found
    verse = end = None
    k = j
    separator = None
    if k < len(tokens) and tokens[k][2] in (":", ",", "."):
        separator, k = tokens[k][2], k + 1
    said = _verse(tokens, k)
    if said is not None:
        verse, end, j = said
        return chapter, verse, end, j
    after = _number(tokens, k)
    # "3:16", "3 16" and "three sixteen" all give a verse; a bare comma
    # or full stop only between digits - "Psalm 23, one of my
    # favourites" is chapter 23 alone.
    if after is not None and (
            separator in (None, ":")
            or (tokens[j - 1][2].isdigit() and tokens[k][2].isdigit())):
        verse, j = after
        end, j = _range(tokens, j, verse)
    return chapter, verse, end, j


def _after_book(tokens, j):
    """(chapter, verse, end, said "chapter", index after) for what
    follows a book's name, or None."""
    chapter = _chapter(tokens, _skip_comma(tokens, j))
    if chapter is not None:
        chapter, k = chapter
        verse = _verse(tokens, _skip_comma(tokens, k))
        if verse is not None:
            return chapter, verse[0], verse[1], True, verse[2]
        if k < len(tokens) and tokens[k][2] == ":":
            found = _number(tokens, k + 1)
            if found is not None:
                end, after = _range(tokens, found[1], found[0])
                return chapter, found[0], end, True, after
        return chapter, None, None, True, k
    bare = _bare(tokens, j)
    if bare is not None:
        return (*bare[:3], False, bare[3])
    return None


def _check(name, chapter, verse, end, keyword):
    """(chapter, verse, end) made sense of for the book, or None if it
    cannot be a reference to it."""
    chapters = name["chapters"]
    if chapters == 1 and verse is None and not keyword:
        # "Jude 3" is its third verse - it has only the one chapter.
        chapter, verse = 1, chapter
    if chapter is None or not 1 <= chapter <= chapters:
        return None
    if verse is not None and not 1 <= verse <= MAX_VERSE:
        return None
    if end is not None and end > MAX_VERSE:
        end = None
    if name["everyday"] and not keyword and verse is None:
        return None
    return chapter, verse, end


def _book_first(tokens, i, names):
    """"Matthew 5:5-7", "Matthew chapter 5 verses 5 to 7", "От Матфея
    пятая глава, с пятого по седьмой стих"."""
    for name, j in _books(tokens, i, names):
        found = _after_book(tokens, j)
        if found is None:
            continue
        chapter, verse, end, keyword, after = found
        checked = _check(name, chapter, verse, end, keyword)
        if checked is not None:
            return name, checked, after
    return None


def _chapter_first(tokens, i, names):
    """"chapter 5 from Matthew, verse 5 to 7", "пятую главу от Матфея,
    стихи с пятого по седьмой", "chapter 5 verse 5 of Matthew"."""
    chapter = _chapter(tokens, i)
    if chapter is None:
        return None
    chapter, j = chapter
    verse = _verse(tokens, _skip_comma(tokens, j))
    if verse is not None:
        j = verse[2]
    j = _skip_of(tokens, _skip_comma(tokens, j))
    for name, after in _books(tokens, j, names):
        if verse is not None:
            v, e, k = verse[0], verse[1], after
        else:
            v, e, k = _verse(tokens, _skip_comma(tokens, after)) \
                or (None, None, after)
        checked = _check(name, chapter, v, e, True)
        if checked is not None:
            return name, checked, k
    return None


def _verse_first(tokens, i, names):
    """"verses 5 to 7 of chapter 5 of Matthew", "verse 5 of Matthew 5",
    "стих 16 главы 3 от Иоанна"."""
    verse = _verse(tokens, i)
    if verse is None:
        return None
    v, e, j = verse
    j = _skip_of(tokens, _skip_comma(tokens, j))
    chapter = _chapter(tokens, j)
    if chapter is not None:
        chapter, j = chapter
        j = _skip_of(tokens, _skip_comma(tokens, j))
        for name, after in _books(tokens, j, names):
            checked = _check(name, chapter, v, e, True)
            if checked is not None:
                return name, checked, after
        return None
    for name, after in _books(tokens, j, names):
        number = _chapter(tokens, _skip_comma(tokens, after)) or \
            _number(tokens, _skip_comma(tokens, after))
        if number is None:
            continue
        checked = _check(name, number[0], v, e, True)
        if checked is not None:
            return name, checked, number[1]
    return None


def _reference(name, chapter, verse, end):
    text = f"{name['shown']} {chapter}"
    if verse is not None:
        text += f":{verse}"
        if end is not None:
            text += f"-{end}"
    return text


def find(text, language=None):
    """[(start, end, reference)] - every scripture reference in text, by
    character offsets, in order and never overlapping. The book, chapter
    and verses may be said in any order: "Matthew 5:5-7", "chapter 5
    from Matthew, verses 5 to 7" and "пятую главу от Матфея, стихи с
    пятого по седьмой" are all found. `language` is the one Whisper heard
    - for a name Russian and Ukrainian share - when known."""
    if language not in ("ru", "uk"):
        language = "uk" if _UKRAINIAN_LETTERS.search(text) else "ru"
    names = _NAMES[language]
    tokens = _tokens(text)
    found = []
    i = 0
    while i < len(tokens):
        match = (_book_first(tokens, i, names)
                 or _chapter_first(tokens, i, names)
                 or _verse_first(tokens, i, names))
        if match is None:
            i += 1
            continue
        name, (chapter, verse, end), j = match
        found.append((tokens[i][0], tokens[j - 1][1],
                      _reference(name, chapter, verse, end)))
        i = j
    return found
