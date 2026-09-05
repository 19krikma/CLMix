"""The Help > About window: what this app is, what version it is, where to
get the next one, and the handful of facts a support email needs.

It is deliberately a near-mirror of docs/index.html - same graphic, same
tagline, same framing - so a user who lands on either one recognizes the
other. When the wording here changes, change it there too. That page is
only served if GitHub Pages is switched on for docs/, which it is not
today, so every link below goes to github.com instead.
"""

import platform
import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import font as tkfont
from tkinter import messagebox
from tkinter import ttk

import sv_ttk

from services import updater
from services.update_checker import REPO, check_for_update
from ui.resources import resource_path
from version import VERSION

TAGLINE = "Aux-send mixing for the DiGiCo Q225 Quantum"

AUTHOR = "Mark Krikunov"
CONTACT_EMAIL = "markkrikunov2000@gmail.com"
COPYRIGHT_YEAR = "2026"

# Every link below is derived from update_checker's REPO so the repo slug
# is named in exactly one place.
#
# These all point at github.com rather than at the GitHub Pages build of
# docs/, which is not switched on for this repo - so the policy the app
# links to is the one file that is definitely published, its source in the
# repository. If Pages is ever enabled, PROJECT_URL/PRIVACY_URL can move to
# https://19krikma.github.io/CLMix/ and its privacy.html.
REPO_URL = f"https://github.com/{REPO}"
PROJECT_URL = REPO_URL
PRIVACY_URL = f"{REPO_URL}/blob/main/legal/PRIVACY_POLICY.md"
RELEASES_URL = f"{REPO_URL}/releases"
ISSUES_URL = f"{REPO_URL}/issues"

# The muted gray the rest of the desktop UI already uses for secondary text.
MUTED_FG = "#888888"

# The banner is drawn at half the artwork's own 1024px width - Tk scales a
# PhotoImage only by whole-number subsampling, so this is 2:1 rather than
# an arbitrary fit-to-window size.
BANNER_WIDTH = 512

# The graphic's bottom ~14% is flat backdrop with nothing drawn on it. The
# Android connect screen crops at the same point (FadingBannerView's
# TAIL_START) - without it the window opens with a band of dead space
# under the wordmark.
BANNER_ARTWORK_FRACTION = 0.858

# How far the banner keeps going below the artwork, dissolving into the
# window, as a fraction of the artwork's own height. Without it the black
# graphic meets the (not quite black) window background at a hard line
# right under the wordmark.
#
# The Android banner gets this by erasing its own alpha (FadingBannerView)
# and the web About page by stacking a CSS gradient over the artwork. A Tk
# PhotoImage has neither partial transparency nor a compositing layer to
# put a gradient in, so the fade is painted into the image itself, one row
# of solid colour at a time.
BANNER_FADE_FRACTION = 0.18


def _scaled_font(delta, **options):
    """A copy of the default UI font, grown by `delta` steps.

    Tk font sizes are points when positive and *pixels* when negative (the
    usual platform default on Linux), so growing one means subtracting as
    often as it means adding - hence not just `size + delta`.
    """
    base = tkfont.nametofont("TkDefaultFont")
    size = base.cget("size")

    font = tkfont.Font(font=base)
    font.configure(size=size + delta if size >= 0 else size - delta, **options)
    return font


def _update_status_text(result):
    """The one-line summary of a check_for_update() result."""
    if result["error"]:
        return f"Check failed: {result['error']}"

    if result["available"]:
        return f"Update available: v{result['latest_version']}"

    return "You're up to date"


def _self_install_blocker(result):
    """Why this update can't be installed in place, or None if it can.

    None of these are errors - they just mean the operator has to take the
    release page and do it by hand, which is what happened for every update
    before this window learned to do it itself.
    """
    if not updater.can_self_install():
        return "Running from source"

    installer = result.get("installer")

    if not installer:
        return "This release has no Windows installer attached"

    if not installer.get("digest"):
        return "This release has no checksum to verify against"

    return None


def _megabytes(count):
    return f"{count / (1024 * 1024):.1f} MB"


def _pixel_color(image, x, y):
    """One pixel of a PhotoImage as a #rrggbb string."""
    value = image.get(x, y)

    # Tk returns "r g b"; some tkinter versions hand back a tuple already.
    if isinstance(value, str):
        value = value.split()

    r, g, b = (int(part) for part in value[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def _blend(widget, start, end, fraction):
    """`start` mixed `fraction` of the way toward `end`, as #rrggbb."""
    # winfo_rgb reports 16-bit channels regardless of the display depth,
    # so the mix is done there and scaled back down to 8 bits per channel.
    mixed = (
        round((a + (b - a) * fraction) / 257)
        for a, b in zip(widget.winfo_rgb(start), widget.winfo_rgb(end))
    )
    return "#%02x%02x%02x" % tuple(mixed)


class Spinner:
    """A spinning arc, drawn to stand in for a button while it works.

    Tk has no busy indicator that fits inside a button - a ttk.Progressbar,
    the nearest thing it ships, is a bar rather than a circle - so this is a
    Canvas the caller swaps in where the button was. It takes the button's
    measured size so nothing else on the row shifts while it spins.

    Drawn rather than animated from a strip of images: a canvas arc is a
    handful of lines, always matches the theme it is asked for, and needs no
    artwork bundled and scaled per display.
    """

    FRAME_MS = 45
    STEP_DEGREES = 24

    # A little over a quarter turn of arc. Much less reads as a stray tick
    # mark at this size; much more and the gap stops being obvious enough
    # to see it turning.
    EXTENT_DEGREES = 100

    # Space between the canvas edge and the ring. Kept small: a Tk canvas
    # oval is drawn from straight segments, so a ring much under 18px
    # across reads as a visible octagon rather than a circle.
    INSET = 5

    def __init__(self, master, width, height, background, track, arc):
        self.canvas = tk.Canvas(
            master, width=width, height=height, bg=background,
            highlightthickness=0, borderwidth=0,
        )

        size = max(8, min(width, height) - 2 * self.INSET)
        left = (width - size) // 2
        top = (height - size) // 2
        box = (left, top, left + size, top + size)

        self.track = self.canvas.create_oval(*box, outline=track, width=2)
        self.arc = self.canvas.create_arc(
            *box, start=90, extent=self.EXTENT_DEGREES, style="arc",
            outline=arc, width=2,
        )

        self._angle = 90
        self._job = None

    def start(self):
        if self._job is None:
            self._tick()

    def _tick(self):
        # Clockwise, which is what a canvas arc's angles are not - they run
        # counter-clockwise from 3 o'clock, hence subtracting.
        self._angle = (self._angle - self.STEP_DEGREES) % 360
        self.canvas.itemconfigure(self.arc, start=self._angle)
        self._job = self.canvas.after(self.FRAME_MS, self._tick)

    def recolor(self, background, track, arc):
        self.canvas.configure(bg=background)
        self.canvas.itemconfigure(self.track, outline=track)
        self.canvas.itemconfigure(self.arc, outline=arc)

    def destroy(self):
        if self._job is not None:
            self.canvas.after_cancel(self._job)
            self._job = None

        self.canvas.destroy()


def _load_artwork(widget, theme):
    """The feature graphic, cropped and halved, or None if it isn't there.

    An unreadable file is not worth failing the window over - the About
    window's real content is the text below it - so the caller just leaves
    the banner out.
    """
    variant = "" if theme == "dark" else "-light"
    path = resource_path("images", f"clmix-feature-graphic{variant}.png")

    try:
        source = tk.PhotoImage(master=widget, file=str(path))
    except tk.TclError:
        return None

    factor = max(1, round(source.width() / BANNER_WIDTH))

    # Rounded down to a whole number of source rows per output row, so the
    # destination is exactly the size the copy fills - Tk grows a photo
    # that turns out too small, which would silently undo the crop.
    crop_height = int(source.height() * BANNER_ARTWORK_FRACTION) // factor * factor

    artwork = tk.PhotoImage(
        master=widget,
        width=source.width() // factor,
        height=crop_height // factor,
    )
    artwork.tk.call(
        artwork, "copy", source,
        "-from", 0, 0, source.width(), crop_height,
        "-subsample", factor,
    )
    return artwork


def _build_banner(widget, theme, width, background):
    """The artwork, widened to `width` and faded out into `background`."""
    artwork = _load_artwork(widget, theme)

    if artwork is None:
        return None

    width = max(width, artwork.width())
    height = artwork.height()
    fade = max(1, round(height * BANNER_FADE_FRACTION))

    banner = tk.PhotoImage(master=widget, width=width, height=height + fade)

    # Every row is first painted across the full width in its own leftmost
    # colour, then the artwork is dropped on top. A window wider than the
    # graphic therefore gets pillars that carry the artwork's own vertical
    # gradient, rather than one flat colour guessed from a single corner.
    for y in range(height):
        banner.put(_pixel_color(artwork, 0, y), to=(0, y, width, y + 1))

    banner.tk.call(
        banner, "copy", artwork, "-to", (width - artwork.width()) // 2, 0
    )

    # The artwork was cropped where its flat backdrop begins, so its bottom
    # row is that flat colour - which is what the fade starts from.
    tail = _pixel_color(artwork, 0, height - 1)

    for step in range(fade):
        banner.put(
            _blend(widget, tail, background, (step + 1) / fade),
            to=(0, height + step, width, height + step + 1),
        )

    return banner


class AboutWindow:
    """Product identity, version/update controls, links, and support details.

    `get_server_details` is a callable returning the live server facts
    (remote port, mixer, this machine's IP) shown in the support block -
    a getter rather than a snapshot, so reopening the window (or hitting
    Refresh in it) reports the state as it is now rather than as it was
    when the window was first built.
    """

    # Rows of the support block, in display order. Also the order they're
    # written to the clipboard by Copy.
    DETAIL_ROWS = (
        ("remote", "Remote server"),
        ("clients", "Connected phones"),
        ("mixer", "Mixer"),
        ("computer", "This computer"),
        ("system", "System"),
    )

    # The update check and the download both run on a worker thread and
    # hand their results back through a queue this window polls, rather
    # than calling into Tk from that thread - the same reason MainWindow
    # has a message_queue.
    POLL_MS = 100

    def __init__(self, master, get_server_details=None,
                 initial_result=None, on_result=None):
        self.get_server_details = get_server_details or (lambda: {})

        # The result of the check MainWindow ran at startup, so the window
        # can already say "update available" without the operator pressing
        # Check first. on_result hands our own checks back the other way.
        self.latest_result = initial_result
        self.on_result = on_result

        self.window = tk.Toplevel(master)
        self.window.title("About CLMix")
        self.window.resizable(False, False)

        self.banner_image = None
        self.link_labels = []
        self._checking = False
        self._downloading = False
        self._spinner = None
        self._cancel = None
        self._ready_installer = None
        self._results = queue.Queue()
        self._copy_reset_job = None

        self.title_font = _scaled_font(6, weight="bold")
        self.link_font = _scaled_font(0, underline=True)

        self.build_ui()

        # refresh() before apply_theme(), not after: it is what fills in
        # the support rows, and the widest of those is what decides how
        # wide the window ends up - which apply_theme has to measure to
        # build a banner that reaches both edges.
        self.refresh()
        self.apply_theme()
        self._show_result()

    # ------------------------------------------------------------------ UI

    def build_ui(self):
        self.banner_label = tk.Label(self.window, borderwidth=0, highlightthickness=0)
        self.banner_label.pack(fill="x")

        self.body = ttk.Frame(self.window, padding=16)
        self.body.pack(fill="both", expand=True)

        ttk.Label(self.body, text="CLMix", font=self.title_font).pack(anchor="w")
        ttk.Label(self.body, text=TAGLINE, foreground=MUTED_FG).pack(
            anchor="w", pady=(2, 0)
        )

        self.build_update_section(self.body)
        self.build_links_section(self.body)
        self.build_details_section(self.body)

        ttk.Separator(self.body, orient="horizontal").pack(fill="x", pady=12)
        ttk.Label(
            self.body,
            text=f"© {COPYRIGHT_YEAR} {AUTHOR} · {CONTACT_EMAIL}",
            foreground=MUTED_FG,
        ).pack(anchor="w")

    def build_update_section(self, parent):
        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=12)

        row = ttk.Frame(parent)
        row.pack(fill="x")

        ttk.Label(row, text=f"Version {VERSION}").pack(side="left")

        self.update_btn = ttk.Button(
            row, text="Update", command=self.on_update, state="disabled"
        )
        self.update_btn.pack(side="right")

        self.check_btn = ttk.Button(
            row, text="Check", command=self.on_check_update
        )
        self.check_btn.pack(side="right", padx=(0, 6))

        self.status_label = ttk.Label(parent, text="", foreground=MUTED_FG)
        self.status_label.pack(anchor="w", pady=(6, 0))

        # Packed here so pack(after=...) has the right slot to return it
        # to, then immediately hidden: an idle progress bar sitting under
        # the version reads as something already stalled.
        self.progress_row = ttk.Frame(parent)
        self.progress_row.pack(anchor="w", pady=(8, 0))

        self.progress = ttk.Progressbar(
            self.progress_row, mode="determinate", length=380
        )
        self.progress.pack(side="left")

        ttk.Button(
            self.progress_row, text="Cancel", command=self.cancel_download
        ).pack(side="left", padx=(8, 0))

        self.progress_row.pack_forget()

    def build_links_section(self, parent):
        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=12)

        links = ttk.Frame(parent)
        links.pack(fill="x")

        rows = (
            (("About & downloads", PROJECT_URL), ("Release notes", RELEASES_URL)),
            (("Source code", REPO_URL), ("Report an issue", ISSUES_URL)),
            (("Privacy policy", PRIVACY_URL),
             ("Email support", f"mailto:{CONTACT_EMAIL}")),
        )

        for row_index, row in enumerate(rows):
            for column, (text, url) in enumerate(row):
                self._link(links, text, url).grid(
                    row=row_index, column=column, sticky="w", padx=(0, 24), pady=1
                )

    def build_details_section(self, parent):
        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=12)

        header = ttk.Frame(parent)
        header.pack(fill="x")

        ttk.Label(header, text="Support details").pack(side="left")

        self.copy_btn = ttk.Button(header, text="Copy", command=self.copy_details)
        self.copy_btn.pack(side="right")

        ttk.Button(header, text="Refresh", command=self.refresh).pack(
            side="right", padx=(0, 6)
        )

        grid = ttk.Frame(parent)
        grid.pack(fill="x", pady=(6, 0))
        grid.columnconfigure(1, weight=1)

        self.detail_labels = {}

        for row, (key, caption) in enumerate(self.DETAIL_ROWS):
            ttk.Label(grid, text=caption, foreground=MUTED_FG).grid(
                row=row, column=0, sticky="w", padx=(0, 16)
            )

            value = ttk.Label(grid, text="")
            value.grid(row=row, column=1, sticky="w")
            self.detail_labels[key] = value

    def _link(self, parent, text, url):
        label = ttk.Label(parent, text=text, font=self.link_font, cursor="hand2")
        label.bind("<Button-1>", lambda _event: webbrowser.open(url))
        self.link_labels.append(label)
        return label

    def apply_theme(self):
        """Re-renders the theme-dependent pieces for the theme now in force.

        Called on open and again from MainWindow.apply_theme: the light and
        dark feature graphics are two separate files rather than one image
        the theme engine can recolor, and ttk leaves an explicitly colored
        link label alone.
        """
        # Deferred rather than imported at module scope: main_window
        # imports this module, so a top-level import back into it would be
        # circular. ui/aux_window.py reaches for the same helpers the same
        # way.
        from ui.main_window import accent_color, panel_bg

        accent = accent_color(self.window)

        for label in self.link_labels:
            label.configure(foreground=accent)

        if self._spinner is not None:
            self._spinner.recolor(*self._spinner_colors())

        background = panel_bg(self.window)

        # Measured, not assumed: the support block's System row is the
        # widest thing in the window and its text is machine-specific, so
        # the banner is built to whatever width the rest of the content
        # settled on - otherwise the fade would stop short of the edges.
        self.body.update_idletasks()
        width = max(BANNER_WIDTH, self.body.winfo_reqwidth())

        # Held on the instance because Tk keeps no reference of its own -
        # a locally-scoped PhotoImage is collected and the label blanks.
        self.banner_image = _build_banner(
            self.banner_label, sv_ttk.get_theme(), width, background
        )

        if self.banner_image is None:
            self.banner_label.pack_forget()
            return

        self.banner_label.configure(image=self.banner_image, bg=background)
        # `before` rather than a bare pack(): re-packing after a theme
        # switch would otherwise drop the banner below the body.
        self.banner_label.pack(fill="x", before=self.body)

    # ------------------------------------------------------------- details

    def refresh(self):
        details = self.get_server_details()

        port = details.get("remote_port")

        if details.get("remote_running"):
            remote = f"Listening on port {port}"
        elif port:
            remote = f"Stopped (port {port})"
        else:
            remote = "Stopped"

        self.detail_labels["remote"].config(text=remote)
        self.detail_labels["clients"].config(text=str(details.get("clients") or 0))

        mixer_ip = details.get("mixer_ip") or "Not set"
        connected = "connected" if details.get("mixer_connected") else "not connected"
        self.detail_labels["mixer"].config(text=f"{mixer_ip} · {connected}")

        self.detail_labels["computer"].config(
            text=details.get("computer_ip") or "Not found"
        )
        self.detail_labels["system"].config(text=self._system_summary())

    def _system_summary(self):
        return (
            f"{platform.system()} {platform.release()} · "
            f"Python {platform.python_version()} · "
            f"Tk {self.window.tk.call('info', 'patchlevel')}"
        )

    def copy_details(self):
        """Puts the whole support block on the clipboard, ready to paste
        into a bug report - the point of gathering these facts at all."""
        lines = [f"CLMix {VERSION}"] + [
            f"{caption}: {self.detail_labels[key].cget('text')}"
            for key, caption in self.DETAIL_ROWS
        ]

        self.window.clipboard_clear()
        self.window.clipboard_append("\n".join(lines))

        self.copy_btn.config(text="Copied")

        if self._copy_reset_job is not None:
            self.window.after_cancel(self._copy_reset_job)

        self._copy_reset_job = self.window.after(1500, self._reset_copy_button)

    def _reset_copy_button(self):
        self._copy_reset_job = None

        if self.window.winfo_exists():
            self.copy_btn.config(text="Copy")

    # -------------------------------------------------------------- update

    def on_check_update(self):
        if self._checking or self._downloading:
            return

        self._checking = True
        self.update_btn.config(state="disabled")
        self.status_label.config(text="")
        self._ready_installer = None
        self._start_spinner()

        threading.Thread(target=self._check_worker, daemon=True).start()
        self.window.after(self.POLL_MS, self._poll_result)

    def _check_worker(self):
        self._results.put(("check", check_for_update(VERSION)))

    def _poll_result(self):
        # The window can be closed mid-check; the worker thread then just
        # finishes into a queue nobody reads again.
        if not self.window.winfo_exists():
            return

        # Drained rather than taken one per tick: a download reports
        # progress every chunk, and only the last of those matters.
        while True:
            try:
                event = self._results.get_nowait()
            except queue.Empty:
                break

            self._handle_event(event)

        if self._checking or self._downloading:
            self.window.after(self.POLL_MS, self._poll_result)

    def _handle_event(self, event):
        kind, payload = event[0], event[1:]

        if kind == "check":
            self._apply_result(payload[0])
        elif kind == "status":
            self.status_label.config(text=payload[0])
        elif kind == "progress":
            self._show_progress(*payload)
        elif kind == "ready":
            self._install(payload[0])
        elif kind == "failed":
            self._end_download(payload[0])

    def _apply_result(self, result):
        self._checking = False
        self._stop_spinner()
        self.latest_result = result

        # Back to MainWindow, so the menu marker and the next opening of
        # this window agree with what was just found.
        if self.on_result is not None:
            self.on_result(result)

        self._show_result()

    def set_result(self, result):
        """Adopts a check someone else ran - MainWindow's startup one.

        Ignored while this window is busy with a check or a download of its
        own, so a late-arriving background result can't overwrite a status
        line that is reporting something more immediate.
        """
        if self._checking or self._downloading:
            return

        self.latest_result = result
        self._show_result()

    def _start_spinner(self):
        if self._spinner is not None:
            return

        # Measured before the button is unpacked, so its stand-in is the
        # same size and nothing else on the row moves. winfo_width() is 1
        # until the window has been mapped, hence the requested size too.
        width = max(self.check_btn.winfo_width(), self.check_btn.winfo_reqwidth())
        height = max(self.check_btn.winfo_height(), self.check_btn.winfo_reqheight())

        self.check_btn.pack_forget()
        self._spinner = Spinner(
            self.check_btn.master, width, height, *self._spinner_colors()
        )
        # Plain side="right": update_btn is already packed there, so this
        # lands to its left - exactly where the button just was.
        self._spinner.canvas.pack(side="right", padx=(0, 6))
        self._spinner.start()

    def _stop_spinner(self):
        if self._spinner is None:
            return

        self._spinner.destroy()
        self._spinner = None
        self.check_btn.pack(side="right", padx=(0, 6))

    def _spinner_colors(self):
        from ui.main_window import accent_color, panel_bg

        background = panel_bg(self.window)

        # The unfilled part of the ring: far enough off the background to
        # read as a track, nowhere near enough to compete with the arc.
        return (
            background,
            _blend(self.window, background, MUTED_FG, 0.45),
            accent_color(self.window),
        )

    def _show_result(self):
        result = self.latest_result

        if result is None:
            return

        self.status_label.config(text=_update_status_text(result))
        self.update_btn.config(
            state="normal" if result.get("available") else "disabled"
        )

    # --------------------------------------------------------- installing

    def on_update(self):
        result = self.latest_result

        if self._downloading or result is None or not result.get("available"):
            return

        blocker = _self_install_blocker(result)

        if blocker is not None:
            self.status_label.config(text=f"{blocker} - opening the release page")
            self._open_release_page()
            return

        if not self._confirm_update(result):
            return

        # A download that was verified and then stopped at the UAC prompt
        # is still good - no reason to pull forty megabytes down twice.
        if self._ready_installer is not None and self._ready_installer.exists():
            self._install(self._ready_installer)
            return

        self._start_download(result)

    def _confirm_update(self, result):
        details = self.get_server_details()
        clients = details.get("clients") or 0

        lines = [
            f"Install CLMix {result['latest_version']}?",
            "",
            "CLMix will close and reopen once the update is installed.",
        ]

        if details.get("mixer_connected"):
            lines.append("The mixer connection will drop while it restarts.")

        if clients:
            lines.append(
                f"{clients} connected phone{'' if clients == 1 else 's'} will be "
                "disconnected, and will have to log in with a password again."
            )

        lines += ["", "Windows will ask for permission to install."]

        return messagebox.askokcancel(
            "Update CLMix", "\n".join(lines), parent=self.window
        )

    def _start_download(self, result):
        self._downloading = True
        self._cancel = threading.Event()

        self.check_btn.config(state="disabled")
        self.update_btn.config(state="disabled")
        self.status_label.config(text="Starting download...")
        self.progress.config(value=0)
        self.progress_row.pack(anchor="w", pady=(8, 0), after=self.status_label)

        threading.Thread(
            target=self._download_worker, args=(result,), daemon=True
        ).start()
        self.window.after(self.POLL_MS, self._poll_result)

    def _download_worker(self, result):
        installer = result["installer"]

        try:
            path, digest = updater.download_installer(
                installer,
                on_progress=lambda done, total:
                    self._results.put(("progress", done, total)),
                cancel=self._cancel,
            )

            self._results.put(("status", "Verifying..."))
            updater.verify_installer(
                path, digest, installer["digest"], installer.get("size")
            )

        except updater.UpdateError as ex:
            self._results.put((
                "failed",
                "Download cancelled" if self._cancel.is_set() else str(ex),
            ))
            return

        self._results.put(("ready", path))

    def _show_progress(self, done, total):
        if total:
            self.progress.config(maximum=total, value=done)
            self.status_label.config(
                text=f"Downloading {_megabytes(done)} of {_megabytes(total)}"
            )
        else:
            # Neither the API nor the response said how big this is, so
            # the bar tracks what has arrived rather than pretending to
            # know how much is left.
            self.progress.config(maximum=max(done, 1), value=done)
            self.status_label.config(text=f"Downloading {_megabytes(done)}")

    def cancel_download(self):
        if self._cancel is not None:
            self._cancel.set()
            self.status_label.config(text="Cancelling...")

    def _install(self, path):
        self._downloading = False
        self._ready_installer = path
        self.progress_row.pack_forget()
        self.status_label.config(text="Starting installer...")

        try:
            updater.launch_installer(path)
        except updater.UpdateError as ex:
            self._end_download(str(ex))
            return

        # From here Inno Setup's Restart Manager closes this app and starts
        # the new one, so there is nothing left to drive - only to say so
        # for however many seconds this window has left.
        self.status_label.config(text="Installing - CLMix will close and reopen")
        self.check_btn.config(state="disabled")
        self.update_btn.config(state="disabled")

    def _end_download(self, message):
        self._downloading = False
        self.progress_row.pack_forget()
        self.status_label.config(text=message)
        self.check_btn.config(state="normal")
        self.update_btn.config(state="normal")

    def _open_release_page(self):
        url = (self.latest_result or {}).get("url")

        if url:
            webbrowser.open(url)
