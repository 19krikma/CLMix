"""Survive a string the console did not send as UTF-8.

python-osc decodes every OSC string as strict UTF-8, and converts only
IndexError and TypeError into its own ParseError. A byte that is not
valid UTF-8 raises UnicodeDecodeError instead, which is a ValueError -
so it sails straight past every `except ParseError` in this codebase and
out of the worker thread, taking the console connection with it:

    Worker error: UnicodeDecodeError('utf-8', b'?\\x80', 1, 2,
                                     'invalid start byte')

Seen 2026-09-20 during a Mixer Backup, which is where it would surface
first: normal operation reads a handful of names, while a backup asks
every strip for everything it has and so reads every string the session
contains. One channel named with a character the desk does not encode as
UTF-8 was enough to end the backup and drop the connection.

The console runs Windows (see PROTOCOL.md) and states its encoding
nowhere, so rather than guess once, this tries UTF-8, then cp1252, then
latin-1 - which cannot fail, every byte being a character - and keeps
the first that decodes. A name comes back readable instead of killing
the connection, and if the guess is wrong it is wrong in one strip's
name rather than in whether CLMix is connected at all.

install() patches python-osc in place. Both OscMessage and OscBundle
reach the function through its module rather than by importing the name,
so the patch covers every parse in the app - the worker's, the Show
Backup collector's, and the capture bridge's - whatever order they were
imported in.
"""

from pythonosc.parsing import osc_types

from services.log_store import log

# Tried in order. latin-1 is last because it always succeeds, so it is
# the backstop rather than a real guess.
ENCODINGS = ("utf-8", "cp1252", "latin-1")

# How many fallbacks to log before going quiet. A session whose names are
# all cp1252 would otherwise log one line per string per strip dump.
LOG_LIMIT = 5

_original_get_string = None
_fallbacks = 0


def decode(raw):
    """Text from OSC string bytes, by whichever encoding reads them."""
    for encoding in ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue

    # Unreachable while latin-1 is in ENCODINGS, and here so that
    # trimming that list can never reintroduce the original crash.
    return raw.decode("utf-8", errors="replace")


def _tolerant_get_string(dgram, start_index):
    try:
        return _original_get_string(dgram, start_index)
    except UnicodeDecodeError:
        pass

    # Only reachable once the original has already scanned this string
    # and found its bounds sound - it decodes last - so repeating the
    # scan here cannot run off the end or land on a short datagram.
    offset = 0
    while dgram[start_index + offset] != 0:
        offset += 1

    total_len = offset + 1
    if total_len % 4 != 0:
        total_len += 4 - (total_len % 4)

    raw = dgram[start_index:start_index + offset]
    text = decode(raw)

    global _fallbacks
    _fallbacks += 1
    if _fallbacks <= LOG_LIMIT:
        log("warning", f"OSC string is not UTF-8, read as {raw!r} -> "
                       f"{text!r}"
                       + (" (further ones will not be logged)"
                          if _fallbacks == LOG_LIMIT else ""))

    return text, start_index + total_len


def install():
    """Make python-osc's string parsing tolerant. Safe to call twice."""
    global _original_get_string

    if _original_get_string is not None:
        return

    _original_get_string = osc_types.get_string
    osc_types.get_string = _tolerant_get_string


def fallback_count():
    return _fallbacks
