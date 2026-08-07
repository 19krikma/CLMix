import math
import tkinter as tk
from tkinter import ttk


class AuxWindow:
    """Per-aux "View" checkboxes, letting the operator hide aux buses they
    don't care about. Hidden auxes are remembered (by name) in settings
    across restarts.

    Laid out as several side-by-side [Aux | View] groups rather than one
    tall list, since a console can have dozens of aux buses - the row
    count per group is chosen to keep the whole thing roughly square
    (e.g. 25 auxes -> 5 groups of 5 rows) instead of one very tall,
    narrow column.
    """

    def __init__(self, master, settings, save_settings, get_worker, on_change=None):
        self.settings = settings
        self.save_settings = save_settings
        self.get_worker = get_worker
        self.on_change = on_change
        self.vars = {}

        self.window = tk.Toplevel(master)
        self.window.title("Aux Visibility")
        self.window.geometry("700x420")

        self.build_ui()

    def build_ui(self):
        # Deferred to avoid a circular import - ui.main_window imports
        # AuxWindow at module level, so these can only be pulled in once
        # ui.main_window has finished loading.
        from ui.main_window import build_aux_list, panel_bg, panel_fg, stripe_bg

        worker = self.get_worker()

        if not worker or not worker.is_alive() or not worker.loaded:
            ttk.Label(
                self.window,
                text="Connect to the mixer to manage aux visibility.",
                padding=20
            ).pack()
            return

        aux_list = build_aux_list(worker)

        if not aux_list:
            ttk.Label(self.window, text="No aux buses found.", padding=20).pack()
            return

        hidden = set(self.settings.get("hidden_auxes", []))
        base_bg = panel_bg(self.window)
        fg = panel_fg(self.window)
        alt_bg = stripe_bg(self.window)

        canvas_container = ttk.Frame(self.window)
        canvas_container.pack(fill="both", expand=True)

        canvas = tk.Canvas(canvas_container, highlightthickness=0, bg=base_bg)
        h_scroll = ttk.Scrollbar(
            canvas_container, orient="horizontal", command=canvas.xview
        )
        canvas.configure(xscrollcommand=h_scroll.set)
        canvas.pack(side="top", fill="both", expand=True)
        h_scroll.pack(side="bottom", fill="x")

        container = tk.Frame(canvas, bg=base_bg, padx=15, pady=15)
        canvas.create_window((0, 0), window=container, anchor="nw")
        container.bind(
            "<Configure>",
            lambda event: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        # Raw tk.Checkbutton's indicator rendering is unreliable across
        # platforms/themes (its "checked" fill can end up indistinguishable
        # from its own background) - ttk.Checkbutton gets a proper themed
        # checkmark from sv_ttk instead. ttk widgets can't take a plain
        # bg=, so the two alternating row backgrounds get their own styles.
        style = ttk.Style()
        style.configure("AuxRowBase.TCheckbutton", background=base_bg)
        style.configure("AuxRowAlt.TCheckbutton", background=alt_bg)

        rows_per_group = max(1, math.ceil(math.sqrt(len(aux_list))))
        header_font = ("TkDefaultFont", 9, "bold")
        group_frames = {}

        for i, (aux_index, name) in enumerate(aux_list):
            group = i // rows_per_group
            row_in_group = i % rows_per_group
            group_bg = alt_bg if group % 2 == 1 else base_bg

            group_frame = group_frames.get(group)
            if group_frame is None:
                group_frame = tk.Frame(container, bg=group_bg)
                group_frame.grid(row=0, column=group, sticky="n", padx=(0, 4))
                group_frames[group] = group_frame

                tk.Label(
                    group_frame, text="Aux", bg=group_bg, fg=fg, font=header_font
                ).grid(row=0, column=0, sticky="w", padx=(8, 16), pady=(8, 4))
                tk.Label(
                    group_frame, text="View", bg=group_bg, fg=fg, font=header_font
                ).grid(row=0, column=1, sticky="e", padx=(0, 8), pady=(8, 4))

            var = tk.BooleanVar(value=name not in hidden)
            self.vars[name] = var

            tk.Label(
                group_frame, text=name, bg=group_bg, fg=fg, anchor="w"
            ).grid(row=row_in_group + 1, column=0, sticky="w", padx=(8, 16), pady=2)

            row_style = "AuxRowAlt.TCheckbutton" if group % 2 == 1 else "AuxRowBase.TCheckbutton"

            ttk.Checkbutton(
                group_frame,
                variable=var,
                style=row_style,
                command=lambda n=name, v=var: self.on_toggle(n, v)
            ).grid(row=row_in_group + 1, column=1, sticky="e", padx=(0, 8), pady=2)

    def on_toggle(self, name, var):
        hidden = set(self.settings.get("hidden_auxes", []))

        if var.get():
            hidden.discard(name)
        else:
            hidden.add(name)

        self.settings["hidden_auxes"] = sorted(hidden)
        self.save_settings()

        if self.on_change:
            self.on_change()
