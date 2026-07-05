"""Shared base for editor inspectors (decisions / events / ideas / ...).

An inspector is a *dumb form* over a block-backed model: values load on
``show()`` and edits commit back instantly — debounced for typed fields — through
the owning editor, which tracks dirty documents and saves them on demand /
``on_leave``. This base bundles the plumbing every such form repeats:

* a scrollable card body (``self.body``, 2-column grid);
* debounced instant commits (``_debounce`` / ``flush_pending``);
* an edit guard (``_guard``: only commit when not loading and editable);
* row builders and a bulk read-only switch (``_entry_row`` / ``_set_state_all``);
* sprite thumbnails (``_icon_preview``) — the CALLER must keep a reference.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk

from ...ui.widgets import ScrollableFrame


class InspectorBase(ttk.Frame):
    """Scrollable card + debounced instant commits + read-only plumbing."""

    def __init__(self, master, owner):
        super().__init__(master, style="Card.TFrame")
        self.owner = owner
        self.t = owner.t
        self.palette = owner.palette
        self._loading = False
        self._editable = True
        self._commit_jobs: dict[str, tuple[str, object]] = {}
        self._icon_photo: ImageTk.PhotoImage | None = None
        scroll = ScrollableFrame(self, bg=self.palette.surface)
        scroll.pack(fill="both", expand=True)
        self.body = scroll.body
        self.body.configure(style="Card.TFrame", padding=(14, 10))
        self.body.columnconfigure(1, weight=1)

    # --- debounced instant commits -----------------------------------------
    def _debounce(self, key: str, commit, delay: int = 700) -> None:
        if self._loading:
            return
        pending = self._commit_jobs.pop(key, None)
        if pending is not None:
            self.after_cancel(pending[0])
        job = self.after(delay, lambda: (self._commit_jobs.pop(key, None), commit()))
        self._commit_jobs[key] = (job, commit)

    def flush_pending(self) -> None:
        for job, _c in list(self._commit_jobs.values()):
            self.after_cancel(job)
        pending = [c for _j, c in self._commit_jobs.values()]
        self._commit_jobs.clear()
        for commit in pending:
            commit()

    def _guard(self) -> bool:
        return not self._loading and self._editable

    # --- shared row builders ------------------------------------------------
    def _entry_row(self, row: int, label: str, commit_key: str,
                   commit) -> tk.StringVar:
        ttk.Label(self.body, text=label, style="CardMuted.TLabel").grid(
            row=row, column=0, sticky="w", pady=3)
        var = tk.StringVar()
        entry = ttk.Entry(self.body, textvariable=var, width=18)
        entry.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=3)
        var.trace_add("write", lambda *_: self._debounce(commit_key, commit))
        entry.bind("<FocusOut>", lambda e: commit())
        return var

    def _set_state_all(self, editable: bool) -> None:
        state = "normal" if editable else "disabled"

        def walk(parent):
            for child in parent.winfo_children():
                if isinstance(child, (ttk.Entry, ttk.Spinbox, ttk.Combobox,
                                      ttk.Button, ttk.Checkbutton, tk.Text, tk.Button)):
                    try:
                        child.configure(state=("readonly" if isinstance(child, ttk.Combobox)
                                               and editable else state))
                    except tk.TclError:
                        pass
                walk(child)
        walk(self.body)

    def _icon_preview(self, sprite: str,
                      size: tuple[int, int] = (48, 48)) -> ImageTk.PhotoImage:
        """Thumbnail PhotoImage for a sprite. The CALLER must keep a reference
        (assign to an attribute), or Tk garbage-collects the image."""
        img = None
        path = (self.owner.resolver.resolve(sprite)
                if sprite and self.owner.resolver_ready() else None)
        if path is not None:
            try:
                with Image.open(path) as im:
                    img = im.convert("RGBA")
                img.thumbnail(size, Image.LANCZOS)
            except Exception:
                img = None
        if img is None:
            img = Image.new("RGBA", size, (0, 0, 0, 0))
        return ImageTk.PhotoImage(img)
