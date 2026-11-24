import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from services import voice_highlights

# Matches the desktop Chat window.
LOG_BG = "#161616"
TEXT_FG = "#ececec"
META_FG = "#a9acb4"
LIVE_FG = "#8fd3ff"
OK_FG = "#66bb6a"
ERROR_FG = "#ef5350"

POLL_MS = 120
FONT_SIZES = (10, 12, 14, 17, 20, 24, 30)
DEFAULT_FONT_SIZE = 2


class VoiceLiveWindow:
    """Live Text: what Voice to Text hears, as it hears it. The bottom
    box always holds each channel's latest words - the sentence so far
    while someone speaks, else the last thing said there - under a
    scrolling list of finished lines.

    Reads the running VoiceEngine; while open it asks the engine for the
    in-progress text (engine.watchers), which it otherwise skips.
    get_speaker(number) -> (name, user) and get_words() -> highlight
    words are the main window's, so lines read as they do in the chat.
    """

    def __init__(self, master, engine, get_speaker, get_words, on_closed):
        self.engine = engine
        self.get_speaker = get_speaker
        self.get_words = get_words
        self.on_closed = on_closed
        self._shown = 0
        self._job = None
        self._size = DEFAULT_FONT_SIZE
        self._tags = set()
        self._live_shown = None

        engine.watchers += 1

        self.window = tk.Toplevel(master)
        self.window.title("Live Text")
        self.window.geometry("760x520")
        self.window.minsize(420, 300)
        window_bg = self.window.tk.eval("set ttk::theme::sv_dark::colors(-bg)")
        self.window.configure(bg=window_bg)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Escape>", lambda event: self.close())

        family = tkfont.nametofont("TkDefaultFont").actual("family")
        self.family = family
        self.text_font = tkfont.Font(family=family)
        self.bold_font = tkfont.Font(family=family, weight="bold")
        self.live_font = tkfont.Font(family=family, slant="italic")
        self.meta_font = tkfont.Font(family=family)

        body = tk.Frame(self.window, bg=window_bg, padx=10, pady=10)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        top = tk.Frame(body, bg=window_bg)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        # Buttons packed first, so a long status line gives way to them.
        ttk.Button(top, text="Clear", command=self._clear).pack(side="right")
        ttk.Button(top, text="A+", width=3,
                   command=lambda: self._resize(1)).pack(side="right",
                                                         padx=(0, 6))
        ttk.Button(top, text="A−", width=3,
                   command=lambda: self._resize(-1)).pack(side="right",
                                                          padx=(0, 4))
        self.status = tk.Label(top, bg=window_bg, fg=META_FG, anchor="w")
        self.status.pack(side="left", fill="x", expand=True)

        # Finished lines.
        self.text = tk.Text(
            body, wrap="word", state="disabled", cursor="arrow", bg=LOG_BG,
            fg=TEXT_FG, relief="flat", highlightthickness=0, padx=10, pady=8,
            font=self.text_font,
        )
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.grid(row=1, column=0, sticky="nsew")
        scroll.grid(row=1, column=1, sticky="ns")
        self.text.tag_configure("meta", foreground=META_FG, font=self.meta_font)
        self.text.tag_configure("name", font=self.bold_font)
        self.text.tag_configure("gap", spacing3=6)

        # What is being said right now, one line per channel.
        self.live = tk.Text(
            body, wrap="word", state="disabled", cursor="arrow", bg=LOG_BG,
            fg=TEXT_FG, relief="flat", highlightthickness=0, padx=10, pady=8,
            height=3, font=self.text_font,
        )
        self.live.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.live.tag_configure("name", font=self.bold_font)
        self.live.tag_configure("idle", foreground=META_FG)
        # Still being spoken: may yet change.
        self.live.tag_configure("speaking", foreground=LIVE_FG,
                                font=self.live_font)

        self._apply_size()
        self._catch_up()
        self._poll()

    # --- Drawing ---

    def _apply_size(self):
        size = FONT_SIZES[self._size]
        self.text_font.configure(size=size)
        self.bold_font.configure(size=size)
        self.live_font.configure(size=size)
        self.meta_font.configure(size=max(8, size - 3))
        # Redraw the latest-words box in the new size.
        self._live_shown = None
        for tag in self._tags:
            self.text.tag_configure(tag, font=self.bold_font)
            self.live.tag_configure(tag, font=self.bold_font)

    def _resize(self, step):
        self._size = min(max(self._size + step, 0), len(FONT_SIZES) - 1)
        self._apply_size()

    def _highlight_tag(self, color):
        try:
            rgb = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
        except (ValueError, TypeError):
            color, rgb = voice_highlights.DEFAULT_COLOR, (0xF9, 0xA8, 0x25)
        name = f"hl{color.lower()}"
        luma = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
        ink = "#1a1a1a" if luma > 150 else "#ffffff"
        for widget in (self.text, self.live):
            widget.tag_configure(name, background=color, foreground=ink,
                                 font=self.bold_font)
            widget.tag_raise(name)
        self._tags.add(name)
        return name

    def _insert(self, widget, text, words, base=()):
        """text into widget with its highlight words picked out."""
        text, spans = voice_highlights.highlight(text, words)
        at = 0
        for span in spans:
            widget.insert("end", text[at:span["start"]], base)
            widget.insert("end", text[span["start"]:span["end"]],
                          base + (self._highlight_tag(span["color"]),))
            at = span["end"]
        widget.insert("end", text[at:], base)

    def _catch_up(self):
        """Adds the lines finished since the last call."""
        recent = list(self.engine.recent)
        new = [line for line in recent if line["time"] > self._shown]
        if not new:
            return

        follow = self.text.yview()[1] >= 0.999
        words = self.get_words()
        self.text.configure(state="normal")
        for line in new:
            name, _user = self.get_speaker(line["channel"])
            at = time.strftime("%H:%M:%S", time.localtime(line["time"]))
            self.text.insert("end", f"{at}  Ch {line['channel']}  ", ("meta",))
            self.text.insert("end", f"{name}: ", ("name",))
            self._insert(self.text, line["text"], words)
            self.text.insert("end", "\n", ("gap",))
            self._shown = line["time"]
        self.text.configure(state="disabled")
        if follow:
            self.text.see("end")

    def _show_live(self):
        """Each channel's latest words: mid-sentence ones in italics
        with a trailing ellipsis, else the last line it finished."""
        latest = {}
        for line in self.engine.recent:
            latest[line["channel"]] = (line["text"], False)
        for number, text in self.engine.partials().items():
            latest[number] = (text, True)

        shown = sorted(latest.items())
        hint = self._gate_hint() if not shown else ""
        if (shown, hint) == self._live_shown:
            return
        self._live_shown = (shown, hint)

        words = self.get_words()
        self.live.configure(state="normal")
        self.live.delete("1.0", "end")
        if not shown:
            self.live.insert("end", "Nothing heard yet." + hint, ("idle",))
        for i, (number, (text, speaking)) in enumerate(shown):
            if i:
                self.live.insert("end", "\n")
            name, _user = self.get_speaker(number)
            self.live.insert("end", f"{name}: ", ("name",))
            if speaking:
                self._insert(self.live, text + " \u2026", words, ("speaking",))
            else:
                self._insert(self.live, text, words)
        self.live.configure(state="disabled",
                            height=min(8, max(2, len(shown) + 1)))

    def _gate_hint(self):
        """Why nothing is heard, when it is the gate: every channel
        quieter than it, so none is passed on to be recognised."""
        engine = self.engine
        levels = engine.levels()
        gate = getattr(engine, "_gate_db", None)
        if engine.state != "running" or not levels or gate is None:
            return ""
        loudest = max(levels.values())
        if loudest >= gate:
            return ""
        # In 5 dB steps, so the text does not flicker with every block.
        return (f" Loudest input is about {round(loudest / 5) * 5} dBFS, "
                f"under the {gate} dBFS gate in Settings - speak up, "
                "raise the input gain, or lower the gate.")

    def _show_status(self):
        engine = self.engine
        levels = engine.levels()
        gate = getattr(engine, "_gate_db", None)
        meters = "   ".join(
            f"Ch {n} " + "█" * max(0, min(8, round((db + 60) / 7.5)))
            + "░" * (8 - max(0, min(8, round((db + 60) / 7.5))))
            # Open: loud enough to be listened to.
            + (" ●" if gate is not None and db >= gate else "")
            for n, db in sorted(levels.items())[:8]
        )
        color = {"running": OK_FG, "error": ERROR_FG}.get(engine.state, META_FG)
        self.status.configure(
            text=engine.message + (f"     {meters}" if meters else ""),
            fg=color)

    def _poll(self):
        self._job = None
        if not self.window.winfo_exists():
            return
        self._catch_up()
        self._show_live()
        self._show_status()
        self._job = self.window.after(POLL_MS, self._poll)

    def _clear(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    # --- Window ---

    def show(self):
        self.window.deiconify()
        self.window.lift()

    def close(self):
        if self._job is not None:
            self.window.after_cancel(self._job)
            self._job = None
        self.engine.watchers = max(0, self.engine.watchers - 1)
        self.window.destroy()
        self.on_closed()
