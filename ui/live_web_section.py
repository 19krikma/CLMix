import tkinter as tk
import webbrowser
from tkinter import ttk

from services import live_text_web

MUTED_FG = "#888888"
OK_FG = "#66bb6a"
ERROR_FG = "#ef5350"
LINK_FG = "#8fd3ff"


class LiveWebPanel:
    """Settings > Voice to Text's "Live Text on the Web": the switch and
    port for services/live_text_web.py, and the address(es) to open.

    get_config() returns the normalized "live_web" settings;
    set_config(config) stores them and (re)starts the server.
    get_state() -> (running, error, urls) says how that went.
    """

    def __init__(self, parent, get_config, set_config, get_state):
        self.get_config = get_config
        self.set_config = set_config
        self.get_state = get_state
        self._syncing = False

        self.frame = ttk.LabelFrame(parent, text="Live Text on the Web",
                                    padding=10)
        self.frame.pack(fill="x", pady=(12, 0))

        ttk.Label(
            self.frame,
            text="Shows Live Text in a web browser on any phone, tablet or "
                 "screen on the same network as this computer - nothing to "
                 "install. Read-only and with no login, so anyone on the "
                 "network who has the address can watch. It listens on the "
                 "adapter chosen for the phone server in General.",
            foreground=MUTED_FG, wraplength=640, justify="left"
        ).pack(anchor="w", pady=(0, 8))

        row = ttk.Frame(self.frame)
        row.pack(fill="x")
        self.enabled_var = tk.BooleanVar()
        ttk.Checkbutton(row, text="Show Live Text on the web",
                        style="Switch.TCheckbutton",
                        variable=self.enabled_var,
                        command=self._save).pack(side="left")

        ttk.Label(row, text="Port").pack(side="left", padx=(24, 6))
        self.port_var = tk.StringVar()
        port_entry = ttk.Entry(row, width=7, textvariable=self.port_var)
        port_entry.pack(side="left")
        port_entry.bind("<Return>", lambda event: self._save())
        port_entry.bind("<FocusOut>", lambda event: self._save())

        self.state_label = ttk.Label(self.frame, text="", foreground=MUTED_FG,
                                     wraplength=640, justify="left")
        self.state_label.pack(anchor="w", pady=(8, 0))
        self.links = ttk.Frame(self.frame)
        self.links.pack(anchor="w")

        self.refresh()

    def _save(self):
        if self._syncing:
            return
        config = dict(self.get_config())
        config["enabled"] = self.enabled_var.get()
        try:
            port = int(self.port_var.get().strip())
        except ValueError:
            port = None
        if port is None or not 1 <= port <= 65535:
            self.port_var.set(str(config["port"]))
        else:
            config["port"] = port
        if config != self.get_config():
            self.set_config(live_text_web.normalize(config))
        self.refresh()

    def refresh(self):
        config = self.get_config()
        self._syncing = True
        self.enabled_var.set(config["enabled"])
        if self.port_var.get().strip() != str(config["port"]):
            self.port_var.set(str(config["port"]))
        self._syncing = False

        for child in self.links.winfo_children():
            child.destroy()

        running, error, urls = self.get_state()
        if error:
            self.state_label.configure(
                text=f"Could not start on port {config['port']}: {error}",
                foreground=ERROR_FG)
        elif running:
            self.state_label.configure(
                text="On - open one of these in a browser:" if urls
                else "On, but no network address was found.",
                foreground=OK_FG)
            for url in urls:
                link = tk.Label(self.links, text=url, fg=LINK_FG,
                                bg=self._bg(), cursor="hand2",
                                font=("TkDefaultFont", 10, "underline"))
                link.pack(anchor="w")
                link.bind("<Button-1>", lambda event, u=url: webbrowser.open(u))
        else:
            self.state_label.configure(text="Off.", foreground=MUTED_FG)

    def _bg(self):
        try:
            return self.frame.tk.eval("set ttk::theme::sv_dark::colors(-bg)")
        except tk.TclError:
            return "#1c1c1c"
