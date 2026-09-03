"""A password Entry with an eye button, inside the field, that reveals what's typed.

The icon is drawn here rather than shipped as a file. Two reasons: the
app has to run both from source and from a PyInstaller bundle, and every
image file that crosses that line needs listing in clmix.spec's `datas`
(see ui/resources.py) - not worth it for one 16px glyph. And Tk 8.6 can't
render astral-plane characters at all, which rules out the obvious "just
use the eye emoji" shortcut.

So the glyph is rasterized in pure Python: an anti-aliased alpha mask
built from the intersection of two circles (the lens shape), packed into
a PNG that tkinter.PhotoImage decodes from base64 - the same trick
ui/app_icon.py uses, minus the pre-baked bytes, since the colours have to
come from the theme rather than a fixed palette.

Sitting the icon *inside* the entry is what makes the theme coupling
awkward. sv_ttk draws entry fields as images, not flat colours, and swaps
that image per state - rest, hover, focus and disabled are four different
fills. A label parked on top of the field therefore has to repaint itself
to match whichever fill is showing, or it reads as a patch. Those fills
aren't in sv_ttk's `colors` array, but the images themselves are Tcl
photo objects in the theme's namespace, so _field_colour samples a pixel
straight out of the live one - the same "read a Tcl variable the theme
owns" trick as ui/main_window.py's panel_bg, one level deeper.

Colours are sampled when the widget is built. That's enough because the
only password field lives in a modal dialog: the theme control sits in
the Setup window's Config tab, which is behind the dialog's grab while it
is open, so the theme can't change out from under a live icon.
"""

import base64
import math
import struct
import tkinter as tk
import zlib
from tkinter import ttk

import sv_ttk

ICON_SIZE = 16

# Gap between the icon's right edge and the field's right edge, and the
# room reserved on the text's right so a long password scrolls out of
# sight behind the field edge rather than running under the icon.
_ICON_INSET = 8
_TEXT_INSET = ICON_SIZE + 12

# Derived from TEntry, which sv_ttk configures with padding {6 1 4 2} -
# only the right inset changes here.
_ENTRY_STYLE = "Reveal.TEntry"

# Samples per pixel per axis when rasterizing. 4 is plenty to keep the
# curves smooth at 16px without making the (uncached, once-per-dialog)
# render noticeable.
_SUPERSAMPLE = 4


def _eye_alpha(size, slashed):
    """An anti-aliased alpha mask of the eye glyph, as rows of 0-255."""
    samples = size * _SUPERSAMPLE
    center = size / 2.0

    # The eye outline is the overlap of two equal circles offset
    # vertically - the classic lens/vesica shape. Given the half-width
    # and half-height we want, this is the radius that produces it.
    half_w, half_h = size * 0.44, size * 0.285
    radius = (half_w ** 2 + half_h ** 2) / (2 * half_h)
    offset = radius - half_h

    stroke = size * 0.095
    pupil = size * 0.15
    slash_reach = size * 0.42
    coverage = [[0] * size for _ in range(size)]

    for sample_y in range(samples):
        for sample_x in range(samples):
            x = (sample_x + 0.5) / _SUPERSAMPLE
            y = (sample_y + 0.5) / _SUPERSAMPLE

            upper = math.hypot(x - center, y - (center + offset))
            lower = math.hypot(x - center, y - (center - offset))

            # Inside the lens but outside the same lens shrunk by the
            # stroke width - i.e. the outline itself - plus the pupil.
            inked = (
                (upper <= radius and lower <= radius)
                and not (upper <= radius - stroke and lower <= radius - stroke)
            ) or math.hypot(x - center, y - center) <= pupil

            if slashed:
                # Distance from, and position along, the leading diagonal.
                across = abs(x - y) / math.sqrt(2)
                along = ((x - center) + (y - center)) / math.sqrt(2)

                if abs(along) <= slash_reach:
                    if across <= stroke * 0.62:
                        inked = True
                    elif across <= stroke * 1.5:
                        # A transparent gap on either side, so the slash
                        # stays legible where it crosses the outline.
                        inked = False

            if inked:
                coverage[sample_y // _SUPERSAMPLE][sample_x // _SUPERSAMPLE] += 1

    per_pixel = _SUPERSAMPLE ** 2
    return [
        [min(255, round(count * 255 / per_pixel)) for count in row]
        for row in coverage
    ]


def _png_base64(alpha, rgb):
    """A base64 8-bit RGBA PNG of `alpha`, every pixel filled with `rgb`."""
    size = len(alpha)
    red, green, blue = rgb

    raw = bytearray()

    for row in alpha:
        raw.append(0)  # per-scanline filter type: none

        for value in row:
            raw += bytes((red, green, blue, value))

    def chunk(tag, payload):
        body = tag + payload
        return (
            struct.pack(">I", len(payload)) + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )

    # A str, not bytes, matching what tk.PhotoImage(data=...) is handed
    # elsewhere in the app (see ui/app_icon.py's ICON_PNG_BASE64).
    return base64.b64encode(png).decode("ascii")


def _theme_namespace():
    return "sv_dark" if sv_ttk.get_theme() == "dark" else "sv_light"


def _theme_colour(widget, key):
    # Same lookup as ui/main_window.py's panel_fg - duplicated rather than
    # imported because main_window imports this module's caller, and a
    # module-level import back the other way would close the cycle.
    return widget.tk.eval(f"set ttk::theme::{_theme_namespace()}::colors({key})")


def _field_colour(widget, state):
    """The flat fill of sv_ttk's entry field in the given state.

    Read out of the theme's own sprite, so it stays correct if sv_ttk
    restyles rather than drifting from a hardcoded hex value. The fields
    are rounded rectangles with a flat interior; (10, 10) is inside the
    5px border the style declares on every side.
    """
    image = widget.tk.eval(f"set ttk::theme::{_theme_namespace()}::I(textbox-{state})")
    pixel = widget.tk.call(image, "get", 10, 10)

    # Tk hands this back as a 3-tuple on some builds and "r g b" on others.
    if isinstance(pixel, str):
        pixel = pixel.split()

    return "#%02x%02x%02x" % tuple(int(channel) for channel in pixel[:3])


def eye_photo(widget, slashed, size=ICON_SIZE):
    """A PhotoImage of the eye, inked in the current theme's foreground."""
    # winfo_rgb resolves any Tk colour spec (hex or name) to 16-bit
    # channels, so this doesn't care what form the theme states it in.
    rgb = tuple(v // 257 for v in widget.winfo_rgb(_theme_colour(widget, "-fg")))
    return tk.PhotoImage(data=_png_base64(_eye_alpha(size, slashed), rgb))


class PasswordEntry(ttk.Frame):
    """An Entry that masks its contents, with an in-field eye to unmask them.

    Drop-in for the ttk.Entry it replaces as far as callers are concerned
    - get() and focus_set() reach the entry inside, and the widget asks
    for exactly the width a bare Entry of the same `width` would, so it
    lines up with its neighbours in a grid. The reveal state is per-widget
    and always starts masked, so a dialog reopened on the same account
    never comes up showing a password.
    """

    def __init__(self, master, width=25, **kwargs):
        super().__init__(master, **kwargs)

        ttk.Style(self).configure(_ENTRY_STYLE, padding=(6, 1, _TEXT_INSET, 2))

        # Reserving room for the icon widens the style's padding, which
        # would otherwise make this field wider than a plain Entry asking
        # for the same `width` and break the column's alignment. So size
        # the frame from a throwaway stock Entry and stop it resizing to
        # its child: the field ends up the same overall width as its
        # neighbours, with the icon eating into the text area rather than
        # hanging off the end. Measured rather than computed from the font
        # so it stays right whatever the theme's padding happens to be.
        probe = ttk.Entry(self, width=width)
        self.configure(width=probe.winfo_reqwidth(), height=probe.winfo_reqheight())
        probe.destroy()
        self.pack_propagate(False)

        self.entry = ttk.Entry(self, width=width, show="*", style=_ENTRY_STYLE)
        self.entry.pack(fill="both", expand=True)

        # Held on the instance because Tk keeps only a weak reference to
        # image data - a PhotoImage that goes out of scope here would
        # leave the button blank.
        self._icons = {
            False: eye_photo(self, slashed=False),
            True: eye_photo(self, slashed=True),
        }
        self._revealed = False
        self._hovered = False
        self._focused = False

        # A tk.Label, not a ttk one, so its background can be repainted to
        # match whichever field image is showing underneath. highlight* is
        # what draws its focus ring, since sitting inside the field leaves
        # no room for a button border.
        self.button = tk.Label(
            self, image=self._icons[False], borderwidth=0,
            highlightthickness=1, cursor="hand2", takefocus=True
        )
        self.button.place(relx=1.0, rely=0.5, anchor="e", x=-_ICON_INSET)

        for widget in (self.entry, self.button):
            widget.bind("<Enter>", self._on_enter, add="+")
            widget.bind("<Leave>", self._on_leave, add="+")

        self.entry.bind("<FocusIn>", self._on_focus_in, add="+")
        self.entry.bind("<FocusOut>", self._on_focus_out, add="+")

        self.button.bind("<Button-1>", self._on_click)
        self.button.bind("<Return>", self._on_key)
        self.button.bind("<space>", self._on_key)

        self._repaint()

    # --- state tracking -------------------------------------------------

    def _on_enter(self, _event=None):
        self._hovered = True
        self._repaint()

    def _on_leave(self, _event=None):
        self._hovered = False
        self._repaint()

    def _on_focus_in(self, _event=None):
        self._focused = True
        self._repaint()

    def _on_focus_out(self, _event=None):
        self._focused = False
        self._repaint()

    def _field_state(self):
        # Mirrors the priority in sv_ttk's Entry.field state map: focus
        # beats hover, and a disabled field ignores both.
        if str(self.entry.cget("state")) == "disabled":
            return "dis"

        if self._focused:
            return "focus"

        return "hover" if self._hovered else "rest"

    def _repaint(self):
        colour = _field_colour(self, self._field_state())
        self.button.configure(
            background=colour,
            highlightbackground=colour,
            highlightcolor=_theme_colour(self, "-accent"),
        )

    # --- behaviour ------------------------------------------------------

    def _on_click(self, _event=None):
        self.toggle()
        # Keep the caret where the user was typing rather than parking
        # focus on the icon they just clicked.
        self.entry.focus_set()
        return "break"

    def _on_key(self, _event=None):
        self.toggle()
        return "break"

    def toggle(self):
        self._revealed = not self._revealed
        self.entry.config(show="" if self._revealed else "*")
        self.button.config(image=self._icons[self._revealed])

    def get(self):
        return self.entry.get()

    def focus_set(self):
        self.entry.focus_set()
