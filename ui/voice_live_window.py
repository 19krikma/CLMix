import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

from services import voice_highlights

# Matches the desktop Chat window.
LOG_BG = "#161616"
TEXT_FG = "#ececec"
META_FG = "#a9acb4"
LIVE_FG = "#8fd3ff"
# A line Whisper was unsure of - shown, but quieter.
UNSURE_FG = "#9a9a9a"
OK_FG = "#66bb6a"
WAIT_FG = "#ffb74d"
ERROR_FG = "#ef5350"
OFF_FG = "#777777"

POLL_MS = 100
# Readable from across a booth: up to projector-confidence-monitor size.
FONT_SIZES = (12, 14, 17, 20, 24, 30, 36, 44, 54)
DEFAULT_FONT_SIZE = 3

# Lines below this confidence (0-1, Whisper's) are dimmed.
UNSURE_BELOW = 0.45


class VoiceLiveWindow:
    """Live Text: what Voice to Text hears, as it hears it. The bottom
    box always holds each channel's latest words - the sentence so far,
    growing as it is spoken, else the last thing said there - under a
    scrolling history of finished lines that follows the newest unless
    scrolled back.

    Reads the running VoiceEngine; while open it asks the engine for the
    in-progress text (engine.watchers), which it otherwise skips.
    get_speaker(number) -> (name, user) and mark(text) -> (text,
    highlights) are the main window's, so lines read - command words and
    scripture picked out - as they do in the chat.
    """

    def __init__(self, master, engine, get_speaker, mark, on_closed):
        self.engine = engine
        self.get_speaker = get_speaker
        self.mark = mark
        self.on_closed = on_closed
        self._shown = 0
        self._job = None
        self._size = DEFAULT_FONT_SIZE
        self._tags = set()
        self._live_shown = None
        self._status_shown = None

        engine.watchers += 1

        self.window = tk.Toplevel(master)
        self.window.title("Live Text")
        self.window.geometry("1000x680")
        self.window.minsize(480, 320)
        window_bg = self.window.tk.eval("set ttk::theme::sv_dark::colors(-bg)")
        self.window.configure(bg=window_bg)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Escape>", lambda event: self.close())
        self.window.bind("<Control-plus>", lambda event: self._resize(1))
        self.window.bind("<Control-equal>", lambda event: self._resize(1))
        self.window.bind("<Control-minus>", lambda event: self._resize(-1))

        family = tkfont.nametofont("TkDefaultFont").actual("family")
        self.family = family
        self.text_font = tkfont.Font(family=family)
        self.bold_font = tkfont.Font(family=family, weight="bold")
        self.live_font = tkfont.Font(family=family, slant="italic")
        self.meta_font = tkfont.Font(family=family)
        self.dot_font = tkfont.Font(family=family, size=14)
        self.state_font = tkfont.Font(family=family, size=11, weight="bold")

        body = tk.Frame(self.window, bg=window_bg, padx=10, pady=10)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        top = tk.Frame(body, bg=window_bg)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        # Buttons packed first, so a long status line gives way to them.
        ttk.Button(top, text="Clear", command=self._clear).pack(side="right")
        ttk.Button(top, text="Save...", command=self._save).pack(
            side="right", padx=(0, 6))
        ttk.Button(top, text="A+", width=3,
                   command=lambda: self._resize(1)).pack(side="right",
                                                         padx=(0, 6))
        ttk.Button(top, text="A−", width=3,
                   command=lambda: self._resize(-1)).pack(side="right",
                                                          padx=(0, 4))
        # The engine's state at a glance: green listening, amber
        # starting, red failed, grey off.
        self.dot = tk.Label(top, text="●", bg=window_bg, fg=OFF_FG,
                            font=self.dot_font)
        self.dot.pack(side="left")
        self.state_label = tk.Label(top, bg=window_bg, fg=META_FG,
                                    font=self.state_font)
        self.state_label.pack(side="left", padx=(4, 10))
        self.status = tk.Label(top, bg=window_bg, fg=META_FG, anchor="w")
        self.status.pack(side="left", fill="x", expand=True)

        # Finished lines.
        self.text = tk.Text(
            body, wrap="word", state="disabled", cursor="arrow", bg=LOG_BG,
            fg=TEXT_FG, relief="flat", highlightthickness=0, padx=12, pady=10,
            font=self.text_font,
        )
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.grid(row=1, column=0, sticky="nsew")
        scroll.grid(row=1, column=1, sticky="ns")
        self.text.tag_configure("meta", foreground=META_FG, font=self.meta_font)
        self.text.tag_configure("name", font=self.bold_font)
        self.text.tag_configure("unsure", foreground=UNSURE_FG)
        self.text.tag_configure("gap", spacing3=8)

        # What is being said right now, one line per channel.
        self.live = tk.Text(
            body, wrap="word", state="disabled", cursor="arrow", bg=LOG_BG,
            fg=TEXT_FG, relief="flat", highlightthickness=0, padx=12, pady=10,
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
        self.meta_font.configure(size=max(9, round(size * 0.6)))
        # Redraw the latest-words box in the new size.
        self._live_shown = None
        for tag in self._tags:
            self.text.tag_configure(tag, font=self.bold_font)
            self.live.tag_configure(tag, font=self.bold_font)
        self.text.see("end")

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

    def _insert(self, widget, text, base=(), language=None):
        """text into widget with its command words and scripture picked
        out."""
        text, spans = self.mark(text, language)
        at = 0
        for span in spans:
            widget.insert("end", text[at:span["start"]], base)
            widget.insert("end", text[span["start"]:span["end"]],
                          base + (self._highlight_tag(span["color"]),))
            at = span["end"]
        widget.insert("end", text[at:], base)

    def _catch_up(self):
        """Adds the lines finished since the last call."""
        recent = self.engine.visible_recent()
        new = [line for line in recent if line["time"] > self._shown]
        if not new:
            return

        follow = self.text.yview()[1] >= 0.999
        self.text.configure(state="normal")
        for line in new:
            name, _user = self.get_speaker(line["channel"])
            at = time.strftime("%H:%M:%S", time.localtime(line["time"]))
            meta = f"{at}  Ch {line['channel']}"
            if line.get("language"):
                meta += f"  {line['language'].upper()}"
            self.text.insert("end", meta + "  ", ("meta",))
            self.text.insert("end", f"{name}: ", ("name",))
            confidence = line.get("confidence")
            unsure = confidence is not None and confidence < UNSURE_BELOW
            self._insert(self.text, line["text"], ("unsure",) if unsure else (),
                         line.get("language"))
            self.text.insert("end", "\n", ("gap",))
            self._shown = line["time"]
        self.text.configure(state="disabled")
        if follow:
            self.text.see("end")

    def _show_live(self):
        """Each channel's latest words: mid-sentence ones in italics
        with a trailing ellipsis, else the last line it finished."""
        latest = {}
        for line in self.engine.visible_recent():
            latest[line["channel"]] = (line["text"], False,
                                       line.get("language"))
        for number, text in self.engine.partials().items():
            latest[number] = (text, True, None)

        shown = sorted(latest.items())
        hint = self._gate_hint() if not shown else ""
        if (shown, hint) == self._live_shown:
            return
        self._live_shown = (shown, hint)

        self.live.configure(state="normal")
        self.live.delete("1.0", "end")
        if not shown:
            self.live.insert("end", "Nothing heard yet." + hint, ("idle",))
        for i, (number, (text, speaking, language)) in enumerate(shown):
            if i:
                self.live.insert("end", "\n")
            name, _user = self.get_speaker(number)
            self.live.insert("end", f"{name}: ", ("name",))
            if speaking:
                self._insert(self.live, text + " …", ("speaking",))
            else:
                self._insert(self.live, text, language=language)
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
        state = engine.state
        color = {"running": OK_FG, "starting": WAIT_FG,
                 "error": ERROR_FG}.get(state, OFF_FG)
        word = {"running": "LISTENING", "starting": "STARTING",
                "error": "STOPPED"}.get(state, "OFF")
        where = getattr(engine, "where", "")
        if state == "running" and where:
            word += f" · {where}"
        shown = (color, word, engine.message, meters)
        if shown == self._status_shown:
            return
        self._status_shown = shown
        self.dot.configure(fg=color)
        self.state_label.configure(text=word, fg=color)
        self.status.configure(
            text=engine.message + (f"     {meters}" if meters else ""),
            fg=ERROR_FG if state == "error" else META_FG)

    def _poll(self):
        self._job = None
        if not self.window.winfo_exists():
            return
        self._catch_up()
        self._show_live()
        self._show_status()
        self._job = self.window.after(POLL_MS, self._poll)

    def _clear(self):
        """Clears the transcript for good: emptying only this box used to
        let every line come back the next time the window was opened,
        since a new window fills itself from the engine's history. The
        engine's cut-off applies to every Live Text view, the web page
        included."""
        self.engine.clear_recent()
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        # The latest-words box goes too.
        self._live_shown = None
        self._show_live()

    def _save(self):
        """The history shown, as a text file."""
        content = self.text.get("1.0", "end").strip()
        if not content:
            messagebox.showinfo("Live Text", "Nothing to save yet.",
                                parent=self.window)
            return
        path = filedialog.asksaveasfilename(
            parent=self.window, title="Save Transcript",
            defaultextension=".txt",
            initialfile=time.strftime("transcript-%Y-%m-%d-%H%M.txt"),
            filetypes=[("Text", "*.txt"), ("All files", "*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as out:
                out.write(content + "\n")
        except OSError as ex:
            messagebox.showerror("Live Text", f"Could not save it: {ex}",
                                 parent=self.window)

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
