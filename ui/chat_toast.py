import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont

from ui.chat_window import BUBBLE_BG, META_FG, TEXT_FG, TECH_BG
from ui.resources import resource_path

# How long a notification stays up after its newest message.
SHOW_MS = 8000

# Longest stretch of a message shown before it is cut with an ellipsis -
# the Chat window has the rest.
PREVIEW_CHARS = 160

# Gap from the screen's corner.
MARGIN = 24


class ChatToast:
    """A small notice in the bottom-right corner of the screen when a
    phone says something in chat: who, and what. Always on top and not
    tied to the main window, so it shows with CLMix minimised or behind
    other windows.

    One at a time: a message arriving while it is up replaces what it
    says and counts the ones before it, rather than stacking another.
    Clicking it opens the Chat window; it goes away on its own after
    SHOW_MS, or when clicked or closed with its x.
    """

    def __init__(self, master, on_open):
        self.master = master
        self.on_open = on_open
        self.window = None
        self.count = 0
        self._hide_job = None

    def notify(self, messages):
        """Shows the newest of `messages` (oldest first), counting the
        rest - and any still on screen from before."""
        if not messages:
            return

        if self.window is None or not self.window.winfo_exists():
            self._build()
            self.count = 0

        self.count += len(messages)
        newest = messages[-1]

        text = " ".join(newest["text"].split())
        if len(text) > PREVIEW_CHARS:
            text = text[:PREVIEW_CHARS - 1].rstrip() + "…"

        self.name_label.configure(text=newest["name"] or "?")
        self.text_label.configure(text=text)
        more = self.count - 1
        self.more_label.configure(
            text=f"+{more} more message{'s' if more != 1 else ''}" if more else ""
        )

        self._place()
        self.window.lift()

        if self._hide_job is not None:
            self.window.after_cancel(self._hide_job)
        self._hide_job = self.window.after(SHOW_MS, self.close)

    def close(self):
        if self.window is not None and self.window.winfo_exists():
            self.window.destroy()
        self.window = None
        self._hide_job = None

    def _open(self, event=None):
        self.close()
        self.on_open()

    def _build(self):
        family = tkfont.nametofont("TkDefaultFont").actual("family")

        self.window = tk.Toplevel(self.master)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        # A thin accent edge, so it reads as CLMix's and stands off
        # whatever is behind it.
        self.window.configure(bg=TECH_BG)

        body = tk.Frame(self.window, bg=BUBBLE_BG, padx=14, pady=10)
        body.pack(fill="both", expand=True, padx=2, pady=2)
        body.columnconfigure(0, weight=1)

        tk.Label(
            body, text="New chat message", bg=BUBBLE_BG, fg=META_FG,
            font=(family, 9)
        ).grid(row=0, column=0, sticky="w")

        close = tk.Label(
            body, text="✕", bg=BUBBLE_BG, fg=META_FG, cursor="hand2",
            font=(family, 10)
        )
        close.grid(row=0, column=1, sticky="ne", padx=(10, 0))
        close.bind("<Button-1>", lambda event: self.close())

        self.name_label = tk.Label(
            body, bg=BUBBLE_BG, fg=TEXT_FG, font=(family, 11, "bold"),
            anchor="w"
        )
        self.name_label.grid(row=1, column=0, columnspan=2, sticky="w",
                             pady=(2, 0))

        self.text_label = tk.Label(
            body, bg=BUBBLE_BG, fg=TEXT_FG, font=(family, 11),
            wraplength=300, justify="left", anchor="w"
        )
        self.text_label.grid(row=2, column=0, columnspan=2, sticky="w")

        self.more_label = tk.Label(
            body, bg=BUBBLE_BG, fg=META_FG, font=(family, 9), anchor="w"
        )
        self.more_label.grid(row=3, column=0, sticky="w", pady=(4, 0))

        hint = tk.Label(
            body, text="Click to open Chat", bg=BUBBLE_BG, fg=META_FG,
            font=(family, 9)
        )
        hint.grid(row=3, column=1, sticky="e", pady=(4, 0))

        for widget in (body, self.name_label, self.text_label,
                       self.more_label, hint):
            widget.configure(cursor="hand2")
            widget.bind("<Button-1>", self._open)

    def _place(self):
        self.window.update_idletasks()
        width = max(self.window.winfo_reqwidth(), 300)
        height = self.window.winfo_reqheight()
        x = self.window.winfo_screenwidth() - width - MARGIN
        # Clear of a taskbar or dock along the bottom.
        y = self.window.winfo_screenheight() - height - MARGIN * 3
        self.window.geometry(f"{width}x{height}+{x}+{y}")
        self.window.deiconify()


NOTIFY_DEST = "org.freedesktop.Notifications"
NOTIFY_PATH = "/org/freedesktop/Notifications"
ACTION_PATTERN = re.compile(r"ActionInvoked \(uint32 (\d+), '([^']*)'\)")


def gvariant_string(text):
    """Text as a GVariant string literal: gdbus reads every argument as
    GVariant, so a message with quotes in it must not reach it raw."""
    escaped = (text.replace("\\", "\\\\").replace("'", "\\'")
               .replace("\n", "\\n"))
    return f"'{escaped}'"


class DesktopNotifier:
    """The same notice as ChatToast, through the Linux desktop's own
    notifications (org.freedesktop.Notifications, via gdbus).

    On Wayland - GNOME, KDE, COSMIC - Tk runs under XWayland and cannot
    put a window over other apps, so a ChatToast never shows unless
    CLMix is already in front. The desktop's notifications always do.

    One at a time, like ChatToast: a newer message replaces the shown
    notification and counts the ones before it. Clicking it opens the
    Chat window: the click arrives on a gdbus monitor thread, which only
    sets a flag; the Tk side picks it up through poll().
    """

    def __init__(self, on_open):
        self.on_open = on_open
        self.count = 0
        self.shown_id = 0
        self._lock = threading.Lock()
        self._open_requested = threading.Event()
        self._monitor = None
        icon = resource_path("images", "clmix-logo-round.png")
        self.icon = str(icon) if icon.exists() else ""

    @staticmethod
    def available():
        if not sys.platform.startswith("linux") or not shutil.which("gdbus"):
            return False
        try:
            result = subprocess.run(
                ["gdbus", "call", "--session", "--dest", NOTIFY_DEST,
                 "--object-path", NOTIFY_PATH, "--method",
                 f"{NOTIFY_DEST}.GetServerInformation"],
                capture_output=True, timeout=3,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0

    def notify(self, messages):
        if not messages:
            return

        self._start_monitor()

        with self._lock:
            # Still on screen, or dismissed? Counting on from a dismissed
            # one would be wrong, but the desktop gives no cheap way to
            # ask, so a gap of SHOW_MS stands in for it.
            now = time.monotonic()
            if now - getattr(self, "_last_at", 0) > SHOW_MS / 1000:
                self.count = 0
                replaces = 0
            else:
                replaces = self.shown_id
            self._last_at = now
            self.count += len(messages)
            count = self.count

        newest = messages[-1]
        text = " ".join(newest["text"].split())
        if len(text) > PREVIEW_CHARS:
            text = text[:PREVIEW_CHARS - 1].rstrip() + "…"
        more = count - 1
        if more:
            text += f"\n+{more} more message{'s' if more != 1 else ''}"

        args = [
            "gdbus", "call", "--session", "--dest", NOTIFY_DEST,
            "--object-path", NOTIFY_PATH, "--method", f"{NOTIFY_DEST}.Notify",
            gvariant_string("CLMix"), str(replaces),
            gvariant_string(self.icon),
            gvariant_string(newest["name"] or "Chat"), gvariant_string(text),
            "['default', 'Open Chat']",
            "{'category': <'im.received'>, 'desktop-entry': <'clmix'>}",
            str(SHOW_MS),
        ]
        # Off the Tk thread: gdbus is quick, but a stuck session bus
        # must not freeze the window.
        threading.Thread(target=self._send, args=(args,), daemon=True).start()

    def _send(self, args):
        try:
            result = subprocess.run(args, capture_output=True, text=True,
                                    timeout=5)
        except (OSError, subprocess.SubprocessError):
            return
        match = re.search(r"uint32 (\d+)", result.stdout)
        if match:
            with self._lock:
                self.shown_id = int(match.group(1))

    def _start_monitor(self):
        if self._monitor is not None and self._monitor.poll() is None:
            return
        try:
            self._monitor = subprocess.Popen(
                ["gdbus", "monitor", "--session", "--dest", NOTIFY_DEST,
                 "--object-path", NOTIFY_PATH],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            )
        except OSError:
            self._monitor = None
            return
        threading.Thread(target=self._watch, args=(self._monitor,),
                         daemon=True).start()

    def _watch(self, process):
        for line in process.stdout:
            match = ACTION_PATTERN.search(line)
            if match is None:
                continue
            with self._lock:
                mine = int(match.group(1)) == self.shown_id
            if mine:
                self._open_requested.set()

    def poll(self):
        """From the Tk thread: opens Chat if a notification was clicked."""
        if self._open_requested.is_set():
            self._open_requested.clear()
            self.on_open()

    def close(self):
        if self._monitor is not None:
            self._monitor.terminate()
            self._monitor = None
