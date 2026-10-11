import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk

from services import feedback
from ui.about_window import MUTED_FG, PRIVACY_URL

# How often the "waiting to send" line is re-read while the page is open.
STATUS_REFRESH_MS = 2000
WRAPLENGTH = 620


class FeedbackWindow:
    """Bug reports and feature requests from the desktop, and the switch
    that opts in to sending error logs.

    Reports from here take the same path as the phones' (see
    services/feedback.py): saved to the outbox, then sent - straight away
    if the collector answers, later if it doesn't.
    """

    def __init__(self, master, get_sharing, set_sharing):
        self.get_sharing = get_sharing
        self.set_sharing = set_sharing

        # A page inside the main window - MainWindow packs this frame
        # into its page area and calls close() when it is left.
        self.window = ttk.Frame(master, padding=15)

        self._results = queue.Queue()
        self._status_job = None
        self._result_job = None
        self._sending = False

        self.build_ui()
        self.refresh_status()

    def build_ui(self):
        ttk.Label(self.window, text="Report a problem or request a feature",
                  font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        ttk.Label(
            self.window, foreground=MUTED_FG, wraplength=WRAPLENGTH,
            justify="left",
            text="Goes to the CLMix developer. Phones can send these too, "
                 "through this server. Without internet the report is kept "
                 "here and sent once the connection is back."
        ).pack(anchor="w", pady=(2, 10))

        form = ttk.Frame(self.window)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="Type").grid(row=0, column=0, sticky="w")
        types = ttk.Frame(form)
        types.grid(row=0, column=1, sticky="w", pady=4)
        self.type_var = tk.StringVar(value="bug")

        for value, text in (("bug", "Something isn't working"),
                            ("feature", "Feature request")):
            ttk.Radiobutton(types, text=text, value=value,
                            variable=self.type_var).pack(side="left",
                                                         padx=(0, 16))

        ttk.Label(form, text="Title").grid(row=1, column=0, sticky="w")
        self.title_entry = ttk.Entry(form)
        self.title_entry.grid(row=1, column=1, sticky="ew", pady=4)

        ttk.Label(form, text="Details").grid(row=2, column=0, sticky="nw",
                                             pady=4)
        self.description_text = tk.Text(form, height=8, wrap="word",
                                        undo=True, padx=6, pady=4)
        self.description_text.grid(row=2, column=1, sticky="ew", pady=4)

        ttk.Label(form, text="Contact").grid(row=3, column=0, sticky="w",
                                             padx=(0, 12))
        self.contact_entry = ttk.Entry(form)
        self.contact_entry.grid(row=3, column=1, sticky="ew", pady=4)
        ttk.Label(form, foreground=MUTED_FG,
                  text="Optional - a name or email, if you'd like a reply"
                  ).grid(row=4, column=1, sticky="w")

        send_row = ttk.Frame(self.window)
        send_row.pack(fill="x", pady=(10, 0))
        self.send_button = ttk.Button(send_row, text="Send",
                                      style="Accent.TButton",
                                      command=self.on_send)
        self.send_button.pack(side="left")
        self.result_label = ttk.Label(send_row, text="",
                                      wraplength=WRAPLENGTH - 100,
                                      justify="left")
        self.result_label.pack(side="left", padx=12)

        ttk.Separator(self.window).pack(fill="x", pady=15)

        sharing = ttk.Frame(self.window)
        sharing.pack(fill="x")
        sharing.columnconfigure(1, weight=1)

        self.sharing_var = tk.BooleanVar(value=self.get_sharing())
        ttk.Checkbutton(sharing, style="Switch.TCheckbutton",
                        variable=self.sharing_var,
                        command=self.on_sharing_toggled
                        ).grid(row=0, column=0, sticky="nw", padx=(0, 12))
        ttk.Label(sharing, text="Send error logs to the developer").grid(
            row=0, column=1, sticky="w")
        ttk.Label(
            sharing, foreground=MUTED_FG, wraplength=WRAPLENGTH,
            justify="left",
            text="When CLMix logs an error, it sends the error, the 20 log "
                 "lines before it, the CLMix version, the operating system "
                 "and a random ID for this install. Those log lines can "
                 "include account names and network addresses. Off by "
                 "default. Turning it off deletes errors not sent yet."
        ).grid(row=1, column=1, sticky="w")

        privacy = ttk.Label(sharing, text="Privacy policy", cursor="hand2",
                            foreground="#3b8eea")
        privacy.grid(row=2, column=1, sticky="w", pady=(4, 0))
        privacy.bind("<Button-1>", lambda _event: webbrowser.open(PRIVACY_URL))

        self.status_label = ttk.Label(self.window, text="", foreground=MUTED_FG,
                                      wraplength=WRAPLENGTH, justify="left")
        self.status_label.pack(anchor="w", pady=(15, 0))

    # --- Sending ------------------------------------------------------------

    def on_send(self):
        if self._sending:
            return

        report, problem = feedback.validate_report({
            "type": self.type_var.get(),
            "title": self.title_entry.get(),
            "description": self.description_text.get("1.0", "end"),
            "contact": self.contact_entry.get(),
        })

        if problem:
            self.result_label.config(text=problem)
            return

        try:
            uid = feedback.outbox.add_report(report)
        except OSError as ex:
            self.result_label.config(text=f"Could not save the report: {ex}")
            return

        self._sending = True
        self.send_button.state(["disabled"])
        self.result_label.config(text="Sending…")

        # Off the Tkinter thread: the attempt waits up to a few seconds
        # on the collector.
        threading.Thread(
            target=lambda: self._results.put(
                feedback.outbox.send_report_now(uid)),
            daemon=True,
        ).start()
        self._result_job = self.window.after(100, self._poll_result)

    def _poll_result(self):
        try:
            answer = self._results.get_nowait()
        except queue.Empty:
            self._result_job = self.window.after(100, self._poll_result)
            return

        self._result_job = None
        self._sending = False
        self.send_button.state(["!disabled"])

        if answer and answer.get("ref"):
            text = f"Sent - thank you. Reference {answer['ref']}."
        else:
            feedback.outbox.wake()
            text = ("Saved. It will be sent when the developer's server "
                    "can be reached.")

        self.result_label.config(text=text)
        self.title_entry.delete(0, "end")
        self.description_text.delete("1.0", "end")
        self.refresh_status()

    # --- Error sharing --------------------------------------------------------

    def on_sharing_toggled(self):
        self.set_sharing(self.sharing_var.get())
        self.refresh_status()

    def refresh_status(self):
        if self._status_job is not None:
            self.window.after_cancel(self._status_job)

        reports, errors = feedback.outbox.pending()
        parts = []

        if reports:
            parts.append(f"{reports} report{'s' if reports != 1 else ''}")

        if errors:
            parts.append(f"{errors} error{'s' if errors != 1 else ''}")

        if not feedback.outbox.url:
            text = ("This build has no developer server configured, so "
                    "nothing can be sent.")
            if parts:
                text += f" Waiting: {' and '.join(parts)}."
        elif parts:
            text = f"Waiting to send: {' and '.join(parts)}."

            if feedback.outbox.last_error:
                text += f" ({feedback.outbox.last_error} - will retry.)"
        else:
            text = "Nothing waiting to be sent."

        self.status_label.config(text=text)
        self._status_job = self.window.after(STATUS_REFRESH_MS,
                                             self.refresh_status)

    def close(self):
        for job in (self._status_job, self._result_job):
            if job is not None:
                self.window.after_cancel(job)

        self._status_job = self._result_job = None
        self.window.destroy()
