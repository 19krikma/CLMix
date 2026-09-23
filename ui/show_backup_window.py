"""The Show Backup window: back the console's show up, and put it back.

See services/show_backup.py for what a backup holds and how the jobs
run. This window starts them, shows their progress, puts their prompts
in front of the operator (recall this snapshot on the desk, press Update
now), and lists the backups already on disk.
"""

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path

from services.show_backup import (BackupJob, RestoreJob, RestoreSessionJob,
                                  ShowBackupStore)
from ui.logs_window import open_folder

POLL_MS = 150


class ShowBackupWindow:
    def __init__(self, master, settings, save_settings, get_worker,
                 command_queue):
        self.settings = settings
        self.save_settings = save_settings
        self.get_worker = get_worker
        self.command_queue = command_queue
        self.store = ShowBackupStore(settings.get("show_backup_dir"))

        self.job = None
        self._prompt_shown = None
        self._notes_shown = 0
        self._poll_job = None

        self.window = tk.Toplevel(master)
        self.window.title("Mixer Backup")
        self.window.geometry("900x640")
        self.window.minsize(760, 520)
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        self.build_ui()
        self.refresh_list()

    # ------------------------------------------------------------------ UI

    def build_ui(self):
        top = ttk.Frame(self.window, padding=(10, 10, 10, 4))
        top.pack(fill="x")

        ttk.Label(top, text="Folder:").pack(side="left")
        self.folder_label = ttk.Label(top, text="", foreground="#888888")
        self.folder_label.pack(side="left", padx=(6, 0))
        ttk.Button(top, text="Open Folder", command=self.open_folder
                   ).pack(side="right")
        ttk.Button(top, text="Change", command=self.change_folder
                   ).pack(side="right", padx=(0, 8))

        actions = ttk.Frame(self.window, padding=(10, 4))
        actions.pack(fill="x")

        self.backup_all_button = ttk.Button(
            actions, text="Back Up All Snapshots",
            command=lambda: self.start_backup(all_snapshots=True)
        )
        self.backup_all_button.pack(side="left")
        self.backup_current_button = ttk.Button(
            actions, text="Back Up Current Snapshot",
            command=lambda: self.start_backup(all_snapshots=False)
        )
        self.backup_current_button.pack(side="left", padx=(8, 0))

        self.remove_button = ttk.Button(
            actions, text="Remove", command=self.remove_selected,
            state="disabled"
        )
        self.remove_button.pack(side="right")
        self.restore_button = ttk.Button(
            actions, text="Restore to Console", command=self.start_restore,
            state="disabled"
        )
        self.restore_button.pack(side="right", padx=(0, 8))
        # Its own button because it is its own job: the fader layout
        # belongs to the session, and the names it writes alongside are a
        # first pass over a rebuilt desk - neither needs a snapshot
        # recalled or an Update pressed. See RestoreSessionJob.
        self.restore_session_button = ttk.Button(
            actions, text="Restore Session", command=self.start_restore_session,
            state="disabled"
        )
        self.restore_session_button.pack(side="right", padx=(0, 8))

        list_frame = ttk.Frame(self.window, padding=(10, 4))
        list_frame.pack(fill="both", expand=True)
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)

        # "extended": several snapshots of one backup can be picked and
        # restored in one run, which is the common case after a rebuild -
        # a handful of a show's snapshots are wrong, not all of them.
        self.tree = ttk.Treeview(
            list_frame, columns=("details", "status"), selectmode="extended"
        )
        self.tree.heading("#0", text="Session / Backup / Snapshot")
        self.tree.heading("details", text="Details")
        self.tree.heading("status", text="Status")
        self.tree.column("#0", width=380, stretch=True)
        self.tree.column("details", width=220, stretch=False)
        self.tree.column("status", width=180, stretch=False)
        scroll = ttk.Scrollbar(list_frame, orient="vertical",
                               command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.update_buttons())

        job = ttk.LabelFrame(self.window, text="Progress", padding=10)
        job.pack(fill="x", padx=10, pady=(4, 10))

        status_row = ttk.Frame(job)
        status_row.pack(fill="x")
        self.status_label = ttk.Label(
            status_row, text="Idle.", wraplength=740, justify="left"
        )
        self.status_label.pack(side="left", fill="x", expand=True)
        self.cancel_button = ttk.Button(
            status_row, text="Cancel", command=self.cancel_job,
            state="disabled"
        )
        self.cancel_button.pack(side="right")

        self.progress = ttk.Progressbar(job, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(8, 0))

        # Filled while the job waits on the operator; empty otherwise.
        self.prompt_frame = ttk.Frame(job)
        self.prompt_frame.pack(fill="x", pady=(8, 0))
        self.prompt_label = ttk.Label(
            self.prompt_frame, text="", wraplength=620, justify="left",
            font=("TkDefaultFont", 10, "bold")
        )
        self.prompt_label.pack(side="left", fill="x", expand=True)
        self.prompt_buttons = ttk.Frame(self.prompt_frame)
        self.prompt_buttons.pack(side="right")

        self.notes = tk.Text(job, height=5, wrap="word", state="disabled")
        self.notes.pack(fill="x", pady=(8, 0))

    def update_buttons(self):
        busy = self.job is not None and not self.job.finished
        target = self._selection()

        for button in (self.backup_all_button, self.backup_current_button):
            button.configure(state="disabled" if busy else "normal")

        if not target or target[1] is None:
            restore_text = "Restore to Console"
        elif len(target[1]) == 1:
            restore_text = "Restore 1 Snapshot"
        else:
            restore_text = f"Restore {len(target[1])} Snapshots"

        self.restore_button.configure(
            text=restore_text,
            state="normal" if target and not busy else "disabled",
        )
        self.restore_session_button.configure(
            state="normal" if target and not busy else "disabled"
        )
        self.remove_button.configure(
            state="normal" if target and target[1] is None and not busy
            else "disabled"
        )
        self.cancel_button.configure(state="normal" if busy else "disabled")

    # ---------------------------------------------------------------- list

    def refresh_list(self):
        self.folder_label.configure(text=str(self.store.root))
        self.tree.delete(*self.tree.get_children())

        sessions = {}

        for path, manifest in self.store.list_backups():
            session = path.parent.name

            if session not in sessions:
                sessions[session] = self.tree.insert(
                    "", "end", iid=f"session:{session}", text=session,
                    values=(manifest.get("console_name", ""), ""), open=True
                )

            snapshots = manifest.get("snapshots", [])
            saved = [s for s in snapshots if not s.get("skipped")]
            backup_id = self.tree.insert(
                sessions[session], "end", iid=str(path),
                text=manifest.get("created_at", path.name).replace("T", "  "),
                values=(f"{len(saved)} snapshot(s), {manifest.get('scope', '')}",
                        manifest.get("status", "")),
            )

            for snapshot in snapshots:
                if snapshot.get("skipped"):
                    details, status = "", "skipped"
                else:
                    details, status = f"{snapshot.get('parameters', 0)} settings", ""

                self.tree.insert(
                    backup_id, "end",
                    iid=f"{path}|{snapshot.get('file', snapshot['index'])}",
                    text=f"{snapshot['index']}  {snapshot['name']}",
                    values=(details, status),
                )

        self.update_buttons()

    @staticmethod
    def _row(item):
        """(backup dir, snapshot file or None) for one row, or None for a
        session heading or a skipped snapshot with nothing saved."""
        if item.startswith("session:"):
            return None

        if "|" in item:
            backup_dir, snapshot_file = item.rsplit("|", 1)
            if not snapshot_file.endswith(".json"):
                return None  # a skipped snapshot - nothing saved to restore
            return backup_dir, snapshot_file

        return item, None

    def _selection(self):
        """(backup dir, [snapshot files] or None) for what is selected.

        None for a selection there is nothing to do with. A whole backup
        row means every snapshot in it, so it wins over any snapshot rows
        picked alongside it. Rows from two different backups are refused
        rather than guessed at - a restore walks one backup's snapshots.
        """
        rows = [row for row in map(self._row, self.tree.selection())
                if row is not None]

        if not rows:
            return None

        backups = {backup_dir for backup_dir, _file in rows}
        if len(backups) != 1:
            return None

        backup_dir = backups.pop()
        files = [file for _dir, file in rows if file is not None]

        if len(files) != len(rows):
            # At least one whole-backup row is in the selection.
            return backup_dir, None

        return backup_dir, files

    # -------------------------------------------------------------- folder

    def change_folder(self):
        chosen = filedialog.askdirectory(
            parent=self.window, initialdir=str(self.store.root),
            title="Where to keep show backups"
        )
        if not chosen:
            return

        self.settings["show_backup_dir"] = chosen
        self.save_settings()
        self.store = ShowBackupStore(chosen)
        self.refresh_list()

    def open_folder(self):
        try:
            self.store.root.mkdir(parents=True, exist_ok=True)
        except OSError as ex:
            messagebox.showerror("Mixer Backup", str(ex), parent=self.window)
            return
        open_folder(self.store.root)

    # ---------------------------------------------------------------- jobs

    def _console_ready(self):
        worker = self.get_worker()

        if worker is None or not worker.is_alive() or not worker.loaded:
            messagebox.showinfo(
                "Mixer Backup", "Connect to the console first.",
                parent=self.window
            )
            return False

        if worker.bridge_only:
            messagebox.showinfo(
                "Mixer Backup",
                "CLMix is in DiGiCo App mode. Turn it off in Setup first.",
                parent=self.window
            )
            return False

        return True

    def start_backup(self, all_snapshots):
        if not self._console_ready():
            return

        if all_snapshots and not messagebox.askokcancel(
            "Back Up All Snapshots",
            "CLMix will recall every snapshot on the console in turn to "
            "read it, so the desk's output changes while this runs. Only "
            "do this when the console is not live.\n\n"
            "It goes back to the current snapshot at the end.",
            icon="warning", parent=self.window
        ):
            return

        self._run(BackupJob(self.get_worker, self.command_queue, self.store,
                            all_snapshots=all_snapshots))

    def start_restore(self):
        target = self._selection()

        if target is None or not self._console_ready():
            return

        backup_dir, files = target

        if files is None:
            what = "every snapshot in this backup"
        elif len(files) == 1:
            what = "this snapshot"
        else:
            what = f"these {len(files)} snapshots"

        if not messagebox.askokcancel(
            "Restore to Console",
            f"This writes the saved settings for {what} to the console, "
            "recalling each snapshot and asking you to press Update on the "
            "desk after each one. Only do this when the console is not "
            "live.\n\n"
            "The session must be rebuilt first: the same channel and bus "
            "counts, and snapshots named as they were. Strip names come "
            "back with their snapshot. Patching is not restored, and "
            "neither is the fader layout - that belongs to the session, "
            "so use Restore Session for it.\n\n"
            "Input channels go back first, and anything that fails or is "
            "missing is left until the end rather than stopping the run - "
            "whatever is still wrong is listed when it finishes.",
            icon="warning", parent=self.window
        ):
            return

        self._run(RestoreJob(self.get_worker, self.command_queue, self.store,
                             backup_dir, only_files=files))

    def start_restore_session(self):
        target = self._selection()

        if target is None or not self._console_ready():
            return

        if not messagebox.askokcancel(
            "Restore Session",
            "This writes the fader banks and layout to the console, plus "
            "one pass of channel, bus, DCA and graphic EQ names taken "
            "from the backup's first snapshot.\n\n"
            "Nothing is recalled and there is no need to press Update, so "
            "a rebuilt desk reads and banks correctly straight away. The "
            "layout belongs to the session; the names do not - they are "
            "restored properly, per snapshot, by Restore to Console, and "
            "this pass is only to make the desk usable before that. The "
            "channel and bus counts still have to match the backup.\n\n"
            "The names are known to write back. The fader layout is not: "
            "no console has been seen accepting one, so CLMix writes it, "
            "reads it back and tells you whether it took - if it did not, "
            "it lists the banks for rebuilding on the surface.",
            icon="warning", parent=self.window
        ):
            return

        self._run(RestoreSessionJob(self.get_worker, self.command_queue,
                                    self.store, target[0]))

    def _run(self, job):
        self.job = job
        self._prompt_shown = None
        self._notes_shown = 0
        self._set_notes("")
        self.progress.configure(value=0)
        job.start()
        self.update_buttons()
        self._poll()

    def cancel_job(self):
        if self.job is not None and not self.job.finished:
            self.job.cancel()

    def remove_selected(self):
        target = self._selection()

        if target is None or target[1] is not None:
            return

        if not messagebox.askyesno(
            "Remove Backup", "Delete this backup? This cannot be undone.",
            icon="warning", parent=self.window
        ):
            return

        try:
            self.store.remove(target[0])
        except (OSError, ValueError) as ex:
            messagebox.showerror("Remove Backup", str(ex), parent=self.window)

        self.refresh_list()

    # ------------------------------------------------------------- polling

    def _poll(self):
        self._poll_job = None
        job = self.job

        if job is None or not self.window.winfo_exists():
            return

        done, total = job.progress
        self.progress.configure(value=(100.0 * done / total) if total else 0)
        self.status_label.configure(text=job.status)

        if job.prompt is not self._prompt_shown:
            self._show_prompt(job.prompt)

        if len(job.notes) > self._notes_shown:
            self._append_notes(job.notes[self._notes_shown:])
            self._notes_shown = len(job.notes)

        if job.finished:
            self._show_prompt(None)
            self.status_label.configure(
                text=job.outcome,
                foreground="#e5473f" if job.failed else ""
            )
            if not job.failed:
                self.progress.configure(value=100)
            self.refresh_list()
            return

        self.status_label.configure(foreground="")
        self._poll_job = self.window.after(POLL_MS, self._poll)

    def _show_prompt(self, prompt):
        self._prompt_shown = prompt

        for child in self.prompt_buttons.winfo_children():
            child.destroy()

        if prompt is None:
            self.prompt_label.configure(text="")
            return

        self.prompt_label.configure(text=prompt.text)

        for choice in prompt.choices:
            ttk.Button(
                self.prompt_buttons, text=choice,
                command=lambda c=choice: self.job.respond(c),
                style="Accent.TButton" if choice == "Continue" else "TButton"
            ).pack(side="left", padx=(8, 0))

        # A prompt is the job waiting on the operator - make sure they see it.
        self.window.deiconify()
        self.window.lift()

    def _set_notes(self, text):
        self.notes.configure(state="normal")
        self.notes.delete("1.0", "end")
        self.notes.insert("end", text)
        self.notes.configure(state="disabled")

    def _append_notes(self, lines):
        self.notes.configure(state="normal")
        for line in lines:
            self.notes.insert("end", line + "\n")
        self.notes.see("end")
        self.notes.configure(state="disabled")

    # --------------------------------------------------------------- close

    def close(self):
        if self.job is not None and not self.job.finished:
            if not messagebox.askyesno(
                "Mixer Backup",
                "A backup or restore is still running. Stop it and close?",
                icon="warning", parent=self.window
            ):
                return
            self.job.cancel()

        if self._poll_job is not None:
            self.window.after_cancel(self._poll_job)

        self.window.destroy()
