import tkinter as tk
from tkinter import ttk

# The muted gray the rest of the desktop UI uses for secondary text.
MUTED_FG = "#888888"

# Where a switch's failure message wraps.
STATUS_WRAPLENGTH = 600


class StartupSection:
    """The two "what happens when this machine boots" switches, in
    Settings > General.

    Switches rather than a menu item: both are things an operator sets
    once for a machine that lives in a rack and then never touches
    again, which is the same reason they are worth having at all.

    MainWindow owns the settings and the registration with the desktop;
    this only draws the switches. `get_options` returns {key: bool};
    `set_option(key, value)` returns an error string to show beneath
    them, or None if it took.
    """

    # (settings key, caption, one-line explanation), in display order.
    ROWS = (
        (
            "launch_on_startup",
            "Launch on Startup",
            "Opens CLMix automatically when you log in to this computer.",
        ),
        (
            "connect_on_launch",
            "Connect on Launch",
            "Connects to the mixer as soon as CLMix opens.",
        ),
    )

    def __init__(self, parent, get_options, set_option):
        self.get_options = get_options
        self.set_option = set_option
        # Guards the toggle callbacks while refresh() writes the switches
        # back, so redrawing state never looks like an operator flipping it.
        self._syncing = False

        self.frame = ttk.Frame(parent, padding=15)
        self.frame.pack(fill="x")

        ttk.Label(
            self.frame, text="Startup", font=("TkDefaultFont", 10, "bold")
        ).pack(anchor="w")

        grid = ttk.Frame(self.frame)
        grid.pack(fill="x", pady=(6, 0))
        grid.columnconfigure(1, weight=1)

        self.vars = {}

        for row, (key, caption, hint) in enumerate(self.ROWS):
            var = tk.BooleanVar(value=False)
            self.vars[key] = var

            ttk.Checkbutton(
                grid,
                style="Switch.TCheckbutton",
                variable=var,
                command=lambda k=key: self._on_toggled(k),
            ).grid(row=row * 2, column=0, sticky="w", padx=(0, 12), pady=(0, 2))

            ttk.Label(grid, text=caption).grid(row=row * 2, column=1, sticky="w")
            ttk.Label(grid, text=hint, foreground=MUTED_FG).grid(
                row=row * 2 + 1, column=1, sticky="w", pady=(0, 6)
            )

        self.grid = grid

        # Only shown when a switch fails to take.
        self.status_label = ttk.Label(
            self.frame, text="", foreground=MUTED_FG,
            wraplength=STATUS_WRAPLENGTH, justify="left"
        )

        self.refresh()

    def _set_status(self, text):
        if not text:
            self.status_label.pack_forget()
            return

        self.status_label.config(text=text)
        self.status_label.pack(anchor="w", pady=(2, 0), after=self.grid)

    def _on_toggled(self, key):
        if self._syncing:
            return

        var = self.vars[key]
        error = self.set_option(key, bool(var.get()))

        if error:
            # Put the switch back where it was: the setting did not take,
            # and a switch that stays on would be claiming otherwise.
            self._syncing = True
            var.set(not var.get())
            self._syncing = False

        self._set_status(error)

    def refresh(self):
        """Re-reads the switches - Launch on Startup from the desktop's
        own login list, which can change from outside CLMix."""
        options = self.get_options()

        self._syncing = True
        for key, _caption, _hint in self.ROWS:
            self.vars[key].set(bool(options.get(key)))
        self._syncing = False
