import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from services.chat_log import KIND_SOS, KIND_TECH, MAX_TEXT, TECH_NAME, clean_text

# Backgrounds per kind of message. The sound engineer's own stand out in
# the theme's blue, the way they do on the phones; an SOS is red.
BUBBLE_BG = "#2a2d33"
TECH_BG = "#1f4a8a"
SOS_BG = "#8e1f1f"
TEXT_FG = "#ececec"
META_FG = "#a9acb4"
LOG_BG = "#161616"


class ChatWindow:
    """The shared chat, from the desk: everything every phone has said,
    SOS calls included, and a box to answer as "Sound Tech".

    A window of its own rather than a page in the main window, so it can
    be put wherever suits during a show and left open beside the mixer.
    The history lives in the ChatLog the main window owns; this only
    draws it, and is told to catch up by refresh().
    """

    def __init__(self, master, chat_log, on_send, on_closed):
        self.chat_log = chat_log
        self.on_send = on_send
        self.on_closed = on_closed
        self.shown_id = 0

        self.window = tk.Toplevel(master)
        self.window.title("Chat")
        self.window.geometry("420x560")
        self.window.minsize(320, 320)
        # Plain tk frames painted by hand: sv_ttk themes ttk widgets but
        # leaves a window's own background to the app, as MainWindow does
        # for the root.
        window_bg = self.window.tk.eval("set ttk::theme::sv_dark::colors(-bg)")
        self.window.configure(bg=window_bg)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Escape>", lambda event: self.close())

        family = tkfont.nametofont("TkDefaultFont").actual("family")

        body = tk.Frame(self.window, bg=window_bg, padx=10, pady=10)
        body.pack(fill="both", expand=True)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        self.text = tk.Text(
            body, wrap="word", state="disabled", cursor="arrow",
            bg=LOG_BG, fg=TEXT_FG, relief="flat", highlightthickness=0,
            padx=8, pady=8, font=(family, 11),
        )
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")

        # A message is two lines - who and when, then what - sharing one
        # tag so the background runs behind both as a single block.
        for kind, bg in (("user", BUBBLE_BG), (KIND_TECH, TECH_BG), (KIND_SOS, SOS_BG)):
            self.text.tag_configure(
                kind, background=bg, lmargin1=10, lmargin2=10, rmargin=10,
                spacing1=2, spacing3=2,
            )
        self.text.tag_configure("name", font=(family, 10, "bold"))
        self.text.tag_configure("meta", foreground=META_FG, font=(family, 9))
        self.text.tag_configure("gap", font=(family, 4))

        entry_row = tk.Frame(body, bg=window_bg)
        entry_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        entry_row.columnconfigure(0, weight=1)

        self.entry_var = tk.StringVar()
        self.entry = ttk.Entry(entry_row, textvariable=self.entry_var)
        self.entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.entry.bind("<Return>", lambda event: self.send())
        self.entry_var.trace_add("write", self._cap_length)

        ttk.Button(
            entry_row, text="Send", style="Accent.TButton", command=self.send
        ).grid(row=0, column=1)

        tk.Label(
            body, text=f"Phones see your messages as “{TECH_NAME}”.",
            bg=window_bg, fg=META_FG,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

        self.refresh()
        self.entry.focus_set()

    def _cap_length(self, *args):
        text = self.entry_var.get()
        if len(text) > MAX_TEXT:
            self.entry_var.set(text[:MAX_TEXT])

    def send(self):
        text = clean_text(self.entry_var.get())
        if text is None:
            return
        self.entry_var.set("")
        self.on_send(text)
        self.refresh()
        self.text.see("end")

    def refresh(self):
        """Draws whatever has arrived since the last call, and keeps the
        newest in view if the operator was already at the bottom."""
        new = self.chat_log.messages(after_id=self.shown_id)
        if not new:
            return

        # The first fill, or an operator already reading the newest.
        follow = self.shown_id == 0 or self.text.yview()[1] >= 0.999

        self.text.configure(state="normal")
        for message in new:
            self._append(message)
            self.shown_id = message["id"]
        self.text.configure(state="disabled")

        if follow:
            self.text.see("end")

    def _append(self, message):
        kind = message["kind"]
        tag = kind if kind in (KIND_TECH, KIND_SOS) else "user"
        at = time.strftime("%H:%M", time.localtime(message["time"]))

        self.text.insert("end", message["name"] or "?", (tag, "name"))
        self.text.insert("end", f"   {at}\n", (tag, "meta"))
        self.text.insert("end", message["text"] + "\n", (tag,))
        self.text.insert("end", "\n", ("gap",))

    def show(self):
        self.window.deiconify()
        self.window.lift()
        self.entry.focus_set()

    def close(self):
        self.window.destroy()
        self.on_closed()
