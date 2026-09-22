import tkinter as tk
from tkinter import messagebox, ttk

REFRESH_MS = 2000


def _elapsed(seconds):
    """A connection's age, at the coarsest unit that still says something."""
    seconds = int(seconds)

    if seconds < 60:
        return f"{seconds}s"

    if seconds < 3600:
        return f"{seconds // 60}m"

    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


def _mixing(row):
    """What this phone is riding right now, in the operator's words."""
    if row.get("mode") == "mixer":
        return "Console faders"

    if row.get("aux") is None:
        return "-"

    return row.get("aux_name") or f"Aux {row['aux']}"


class PhonesWindow:
    """Who is connected to the remote server right now.

    Every column is something the connection already had to establish to
    work at all - the address it dialled in from, the account it logged in
    as, and which bus that account put it on. Nothing here is asked of the
    phone for this window's sake: the protocol collects no device name,
    model, OS or identifier, so none is shown.

    `get_clients` is a callable rather than a list for the same reason the
    About window takes one: phones join and drop while this window is
    open, and it re-reads on a timer. `on_kick` is handed a row's "id" and
    drops that one connection.
    """

    # key, heading, width, minimum width, anchor, whether it absorbs slack.
    # Only Mixing stretches: the other three hold values of a known size,
    # so widening the window should go to the one column whose contents
    # are named by whoever set up the console.
    COLUMNS = (
        ("account", "Account", 120, 90, "w", False),
        ("address", "Address", 115, 100, "w", False),
        ("mixing", "Mixing", 160, 110, "w", True),
        ("connected", "Connected", 80, 70, "center", False),
    )

    # What the list can be squeezed to before the window stops shrinking -
    # a few readable rows rather than nothing.
    MIN_LIST_HEIGHT = 110

    def __init__(self, master, get_clients, is_running=None, on_kick=None):
        self.get_clients = get_clients
        self.is_running = is_running or (lambda: True)
        self.on_kick = on_kick

        self.window = tk.Toplevel(master)
        self.window.title("Phone List")
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        self._refresh_job = None

        self.build_ui()
        self._size_to_contents()
        self.refresh_list()

    def build_ui(self):
        # The footer is packed before the list, from the bottom up. pack
        # hands out space in the order widgets are added, so a list packed
        # first with expand=True takes the whole window and leaves nothing
        # for what follows - which is exactly what happened here: at the
        # old fixed 520x320 the Refresh button was never mapped at all and
        # the status line was clipped to 6px.
        btn_bar = ttk.Frame(self.window, padding=(10, 0, 10, 10))
        btn_bar.pack(side="bottom", fill="x")

        ttk.Button(btn_bar, text="Refresh", command=self.refresh_list).pack(side="left")

        # Disabled until something is picked: there is no sensible "kick
        # whoever happens to be first", and an always-live button next to
        # a list of performers is the wrong thing to leave armed.
        self.kick_btn = ttk.Button(
            btn_bar, text="Kick", state="disabled", command=self.kick_selected
        )
        self.kick_btn.pack(side="right")

        self.status_label = ttk.Label(self.window, text="")
        self.status_label.pack(side="bottom", anchor="w", padx=10, pady=(6, 6))

        list_frame = ttk.Frame(self.window)
        list_frame.pack(side="top", fill="both", expand=True, padx=10, pady=(10, 0))

        self.tree = ttk.Treeview(
            list_frame, columns=[column[0] for column in self.COLUMNS],
            show="headings", height=8
        )

        for key, heading, width, minwidth, anchor, stretch in self.COLUMNS:
            self.tree.heading(key, text=heading)
            self.tree.column(
                key, width=width, minwidth=minwidth, anchor=anchor, stretch=stretch
            )

        # More phones can be connected than there are rows on screen - a
        # band with a big in-ear rig is the case this window exists for -
        # so the ones past the bottom have to be reachable.
        scrollbar = ttk.Scrollbar(
            list_frame, orient="vertical", command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

        self.tree.bind("<<TreeviewSelect>>", self._on_selection_changed)

    def _on_selection_changed(self, _event=None):
        self.kick_btn.config(
            state="normal" if self.tree.selection() else "disabled"
        )

    def kick_selected(self):
        """Drops the selected phone, after saying whose it is.

        Confirmed because it is not undoable from this end: the phone's
        user has to log back in themselves, and doing that to the wrong
        performer mid-show is worth one keystroke of friction.
        """
        selected = self.tree.selection()

        if not selected or self.on_kick is None:
            return

        iid = selected[0]
        account, address = self.tree.item(iid, "values")[:2]

        if not messagebox.askyesno(
            "Kick phone",
            f"Disconnect {account} at {address}?\n\n"
            "They will have to log in with their password again.",
            parent=self.window,
        ):
            return

        self.on_kick(iid)
        self.refresh_list()

    def _size_to_contents(self):
        """Opens at whatever the contents actually need.

        Measured rather than hard-coded: a fixed size is only ever right
        for the font it was picked against, and the column widths, the
        row height and the button all scale with the user's.
        """
        self.window.update_idletasks()

        width = self.window.winfo_reqwidth()
        height = self.window.winfo_reqheight()

        self.window.geometry(f"{width}x{height}")

        # The list is the only part that can usefully give up space, so
        # the floor is everything else plus a few rows of it. Resizing is
        # left on: a long aux name is worth being able to widen for.
        self.window.minsize(
            width, height - self.tree.winfo_reqheight() + self.MIN_LIST_HEIGHT
        )

    def refresh_list(self):
        """Redraws the list and books the next redraw.

        Rows are keyed by address so the selection and scroll position
        survive a refresh that changed nothing - the common case, given
        this runs itself every couple of seconds.
        """
        if not self.window.winfo_exists():
            return

        if not self.is_running():
            self.tree.delete(*self.tree.get_children())
            self.status_label.config(
                text="Not connected - the phone server runs while CLMix is "
                     "connected to the mixer."
            )
            self._on_selection_changed()
            self._schedule_refresh()
            return

        rows = self.get_clients()
        seen = set()

        for row in rows:
            # Keyed by the server's own per-connection id, not by address:
            # two phones behind one NAT can share an address, and the id
            # is also what a kick has to name.
            iid = row["id"]
            seen.add(iid)

            values = (
                row.get("user") or "Not logged in",
                row["address"],
                _mixing(row),
                _elapsed(row["connected_seconds"]),
            )

            if self.tree.exists(iid):
                self.tree.item(iid, values=values)
            else:
                self.tree.insert("", "end", iid=iid, values=values)

        for iid in self.tree.get_children():
            if iid not in seen:
                self.tree.delete(iid)

        count = len(rows)
        self.status_label.config(
            text=f"{count} phone{'' if count == 1 else 's'} connected"
        )

        # Deleting the selected row does not fire <<TreeviewSelect>>, so a
        # phone that drops (or was just kicked) would otherwise leave the
        # button armed with nothing behind it.
        self._on_selection_changed()

        self._schedule_refresh()

    def _schedule_refresh(self):
        self._refresh_job = self.window.after(REFRESH_MS, self.refresh_list)

    def close(self):
        # Cancelled explicitly: an after() job that fires against a
        # destroyed window raises out of the Tk callback rather than
        # simply doing nothing.
        if self._refresh_job is not None:
            self.window.after_cancel(self._refresh_job)
            self._refresh_job = None

        self.window.destroy()
