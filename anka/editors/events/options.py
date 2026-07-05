"""Option list editor of the Events editor.

A reorderable list of the event's ``option`` blocks (▲/▼ buttons AND drag&drop by
the ≡ handle) with an expandable sub-form for the selected option: name key +
localised text, trigger / ai_chance / effects scripts and the
``original_recipient_only`` flag. All edits commit straight into the block-backed
`Option`; the owner editor tracks dirty documents.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from ...services.event_service import Option, option_letter
from ..common import ScriptEditorDialog
from ...ui.widgets.tooltip import attach_help

_OPTION_SCRIPTS = (
    ("trigger", ("trigger",)),
    ("ai_chance", ("trigger",)),
    ("effects", ("effect", "trigger")),
)


class OptionListEditor(ttk.Frame):
    def __init__(self, master, owner, on_change):
        super().__init__(master, style="Card.TFrame")
        self.owner = owner
        self.t = owner.t
        self.palette = owner.palette
        self._on_change = on_change
        self._event = None
        self._editable = True
        self._loading = False
        self._selected: Option | None = None
        self._rows: list[dict] = []
        self._jobs: dict[str, tuple[str, object]] = {}
        self._drag_from: int | None = None
        self._drag_to: int | None = None

        head = ttk.Frame(self, style="Card.TFrame")
        head.pack(fill="x")
        label = ttk.Label(head, text=self.t("events.options"), style="Card.TLabel")
        label.pack(side="left")
        attach_help(label, self.t, "event.options", self.palette)
        self._add_btn = ttk.Button(head, text="➕ " + self.t("events.add_option"),
                                   command=self._add)
        self._add_btn.pack(side="left", padx=(8, 0))

        self._rows_host = ttk.Frame(self, style="Card.TFrame")
        self._rows_host.pack(fill="x", pady=(3, 0))
        self._build_subform()

    # ------------------------------------------------------------------ public
    def show(self, event, editable: bool) -> None:
        self.flush()
        self._event = event
        self._editable = editable
        self._selected = None
        self._rebuild()

    def flush(self) -> None:
        for job, _c in list(self._jobs.values()):
            self.after_cancel(job)
        pending = [c for _j, c in self._jobs.values()]
        self._jobs.clear()
        for commit in pending:
            commit()

    def reload_locale_texts(self) -> None:
        """Language switched: refresh the labels and the sub-form text."""
        self._rebuild(keep_selection=True)

    # --------------------------------------------------------------- internals
    def _debounce(self, name: str, commit, delay: int = 900) -> None:
        if self._loading:
            return
        pending = self._jobs.pop(name, None)
        if pending is not None:
            self.after_cancel(pending[0])
        job = self.after(delay, lambda: (self._jobs.pop(name, None), commit()))
        self._jobs[name] = (job, commit)

    def _event_options(self) -> list[Option]:
        return self._event.options() if self._event is not None else []

    def _rebuild(self, keep_selection: bool = False) -> None:
        self._loading = True
        try:
            selected_pair = self._selected.pair if (keep_selection and
                                                    self._selected) else None
            for row in self._rows:
                row["frame"].destroy()
            self._rows.clear()
            options = self._event_options()
            self._selected = None
            for i, option in enumerate(options):
                if selected_pair is not None and option.pair is selected_pair:
                    self._selected = option
                self._build_row(i, option, len(options))
            self._add_btn.configure(
                state="normal" if self._editable and self._event is not None
                else "disabled")
            self._refresh_selection()
        finally:
            self._loading = False
        self._load_subform()

    def _build_row(self, index: int, option: Option, total: int) -> None:
        frame = tk.Frame(self._rows_host, bg=self.palette.surface_alt, bd=0)
        frame.pack(fill="x", pady=1)
        row: dict = {"frame": frame, "option": option, "labels": []}

        handle = tk.Label(frame, text="≡", bg=self.palette.surface_alt,
                          fg=self.palette.text_muted, cursor="fleur",
                          font=("Segoe UI", 10), padx=6)
        handle.pack(side="left")
        row["labels"].append(handle)
        if self._editable:
            handle.bind("<ButtonPress-1>", lambda e, i=index: self._drag_start(i))
            handle.bind("<B1-Motion>", self._drag_motion)
            handle.bind("<ButtonRelease-1>", self._drag_release)

        state = "normal" if self._editable else "disabled"
        up = ttk.Button(frame, text="▲", width=2, state=state,
                        command=lambda o=option: self._move(o, -1))
        up.pack(side="left")
        down = ttk.Button(frame, text="▼", width=2, state=state,
                          command=lambda o=option: self._move(o, +1))
        down.pack(side="left", padx=(1, 6))
        if index == 0:
            up.configure(state="disabled")
        if index == total - 1:
            down.configure(state="disabled")

        name_key = option.name_key
        loc = self.owner.loc_get(name_key, self.owner.loc_language) if name_key else ""
        text = f"{option_letter(index)} · {name_key or '—'}"
        if loc:
            text += f" · {loc}"
        label = tk.Label(frame, text=text, bg=self.palette.surface_alt,
                         fg=self.palette.text, anchor="w", font=("Segoe UI", 9))
        label.pack(side="left", fill="x", expand=True, padx=(0, 4))
        row["labels"].append(label)

        ttk.Button(frame, text="✕", width=2, state=state,
                   command=lambda o=option: self._remove(o)).pack(
            side="right", padx=(0, 3))

        for widget in (frame, label):
            widget.bind("<ButtonPress-1>", lambda e, o=option: self._select(o))
        self._rows.append(row)

    def _refresh_selection(self) -> None:
        for row in self._rows:
            selected = self._selected is not None and \
                row["option"].pair is self._selected.pair
            bg = self.palette.accent if selected else self.palette.surface_alt
            fg = self.palette.bg if selected else self.palette.text
            row["frame"].configure(bg=bg)
            for lbl in row["labels"]:
                muted = lbl.cget("text") == "≡"
                lbl.configure(bg=bg, fg=(self.palette.bg if selected else (
                    self.palette.text_muted if muted else self.palette.text)))

    def _select(self, option: Option) -> None:
        if self._selected is not None and option.pair is self._selected.pair:
            return
        self.flush()
        self._selected = option
        self._refresh_selection()
        self._load_subform()

    # ------------------------------------------------------------ drag & drop
    def _drag_start(self, index: int) -> None:
        self._drag_from = index
        self._drag_to = index

    def _drag_motion(self, event) -> None:
        if self._drag_from is None:
            return
        y = event.y_root
        target = self._drag_from
        for i, row in enumerate(self._rows):
            top = row["frame"].winfo_rooty()
            if top <= y < top + row["frame"].winfo_height():
                target = i
                break
        else:
            if self._rows:
                if y < self._rows[0]["frame"].winfo_rooty():
                    target = 0
                elif y >= (self._rows[-1]["frame"].winfo_rooty()
                           + self._rows[-1]["frame"].winfo_height()):
                    target = len(self._rows) - 1
        if target != self._drag_to:
            self._drag_to = target
            # ghost highlight of the would-be insertion row
            for i, row in enumerate(self._rows):
                base = self.palette.surface_alt
                row["frame"].configure(
                    bg=self.palette.surface if i == target else base)
                for lbl in row["labels"]:
                    lbl.configure(bg=self.palette.surface if i == target else base)

    def _drag_release(self, _event) -> None:
        src, dst = self._drag_from, self._drag_to
        self._drag_from = self._drag_to = None
        if src is None or dst is None or src == dst or self._event is None:
            self._refresh_selection()
            return
        option = self._rows[src]["option"]
        if self._event.move_option_to(option, dst):
            self._selected = option
            self._on_change()
            self._rebuild(keep_selection=True)

    # ------------------------------------------------------------------ subform
    def _build_subform(self) -> None:
        form = ttk.Frame(self, style="Card.TFrame", padding=(16, 4, 0, 4))
        self._form = form
        form.columnconfigure(1, weight=1)
        r = 0
        ttk.Label(form, text=self.t("events.option_name_key"),
                  style="CardMuted.TLabel").grid(row=r, column=0, sticky="w", pady=2)
        self._name_var = tk.StringVar()
        name_entry = ttk.Entry(form, textvariable=self._name_var, width=24,
                               font=("Consolas", 9))
        name_entry.grid(row=r, column=1, sticky="w", padx=(8, 0), pady=2); r += 1
        self._name_var.trace_add("write", lambda *_: self._debounce(
            "name", self._commit_name))
        name_entry.bind("<FocusOut>", lambda e: self._commit_name())

        ttk.Label(form, text=self.t("events.option_text"),
                  style="CardMuted.TLabel").grid(row=r, column=0, sticky="w", pady=2)
        self._text_var = tk.StringVar()
        text_entry = ttk.Entry(form, textvariable=self._text_var)
        text_entry.grid(row=r, column=1, sticky="ew", padx=(8, 0), pady=2); r += 1
        self._text_var.trace_add("write", lambda *_: self._debounce(
            "text", self._commit_text, 1200))
        text_entry.bind("<FocusOut>", lambda e: self._commit_text())

        self._orig_var = tk.BooleanVar()
        cb = ttk.Checkbutton(form, text="original_recipient_only",
                             style="Card.TCheckbutton", variable=self._orig_var,
                             command=self._commit_flag)
        cb.grid(row=r, column=0, columnspan=2, sticky="w", pady=2); r += 1
        attach_help(cb, self.t, "event.original_recipient_only", self.palette)

        self._script_status: dict[str, ttk.Label] = {}
        for name, _kinds in _OPTION_SCRIPTS:
            row = ttk.Frame(form, style="Card.TFrame")
            row.grid(row=r, column=0, columnspan=2, sticky="ew", pady=1); r += 1
            status = ttk.Label(row, text="○", style="CardMuted.TLabel", width=2)
            status.pack(side="left")
            label = ttk.Label(row, text=name, style="CardMuted.TLabel")
            label.pack(side="left")
            attach_help(label, self.t, f"event.option_{name}", self.palette)
            self._script_status[name] = status
            ttk.Button(row, text="✎", width=3,
                       command=lambda n=name: self._edit_script(n)).pack(
                side="left", padx=(6, 0))

        actions = ttk.Frame(form, style="Card.TFrame")
        actions.grid(row=r, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Button(actions, text="⧉ " + self.t("events.duplicate_option"),
                   command=self._duplicate).pack(side="left")

    def _load_subform(self) -> None:
        option = self._selected
        if option is None:
            self._form.pack_forget()
            return
        self._form.pack(fill="x")
        self._loading = True
        try:
            self._name_var.set(option.name_key)
            key = option.name_key
            self._text_var.set(self.owner.loc_get(key, self.owner.loc_language)
                               if key else "")
            self._orig_var.set(option.get_flag("original_recipient_only"))
            self._refresh_scripts()
            state = "normal" if self._editable else "disabled"
            for child in self._form.winfo_children():
                for widget in ([child] + child.winfo_children()):
                    if isinstance(widget, (ttk.Entry, ttk.Button, ttk.Checkbutton)):
                        widget.configure(state=state)
        finally:
            self._loading = False

    def _refresh_scripts(self) -> None:
        option = self._selected
        for name, label in self._script_status.items():
            value = self._get_script(option, name) if option is not None else ""
            filled = bool(value.strip())
            label.configure(text="●" if filled else "○",
                            foreground=self.palette.accent if filled
                            else self.palette.text_muted)

    @staticmethod
    def _get_script(option: Option, name: str) -> str:
        if name == "effects":
            return option.effects_script
        return option.get_script(name)

    # ------------------------------------------------------------------ commits
    def _commit_name(self) -> None:
        option = self._selected
        if self._loading or not self._editable or option is None:
            return
        new_key = self._name_var.get().strip()
        if not new_key or new_key == option.name_key:
            return
        self.flush()
        option.name_key = new_key
        self._on_change()
        was = self._loading
        self._loading = True
        self._text_var.set(self.owner.loc_get(new_key, self.owner.loc_language))
        self._loading = was
        self._selected = option
        self._rebuild(keep_selection=True)

    def _commit_text(self) -> None:
        option = self._selected
        if self._loading or not self._editable or option is None:
            return
        key = option.name_key
        if not key:
            return
        text = self._text_var.get().strip()
        current = self.owner.loc_get(key, self.owner.loc_language)
        if text != current and (text or current):
            self.owner.loc_set(key, self.owner.loc_language, text)
            self._update_row_label(option)

    def _update_row_label(self, option: Option) -> None:
        for i, row in enumerate(self._rows):
            if row["option"].pair is option.pair:
                loc = self.owner.loc_get(option.name_key, self.owner.loc_language)
                text = f"{option_letter(i)} · {option.name_key or '—'}"
                if loc:
                    text += f" · {loc}"
                row["labels"][-1].configure(text=text)
                return

    def _commit_flag(self) -> None:
        option = self._selected
        if self._loading or not self._editable or option is None:
            return
        option.set_flag("original_recipient_only", self._orig_var.get())
        self._on_change()

    # ------------------------------------------------------------------ actions
    def _auto_key(self) -> str:
        used = {o.name_key for o in self._event_options()}
        for i in range(64):
            candidate = f"{self._event.id}.{option_letter(i)}"
            if candidate not in used:
                return candidate
        return f"{self._event.id}.{option_letter(len(used))}"

    def _add(self) -> None:
        if self._event is None or not self._editable:
            return
        self.flush()
        option = self._event.add_option(self._auto_key())
        self._selected = option
        self._on_change()
        self._rebuild(keep_selection=True)

    def _remove(self, option: Option) -> None:
        if self._event is None or not self._editable:
            return
        # effects are not reconstructible — those deletions get a confirmation
        if option.has_effects() and not messagebox.askyesno(
                "ANKA", self.t("events.confirm_delete_option",
                               name=option.name_key or "—")):
            return
        self.flush()
        if self._selected is not None and self._selected.pair is option.pair:
            self._selected = None
        self._event.remove_option(option)
        self._on_change()
        self._rebuild(keep_selection=True)

    def _duplicate(self) -> None:
        option = self._selected
        if self._event is None or not self._editable or option is None:
            return
        self.flush()
        new_key = self._auto_key()
        new = self._event.duplicate_option(option, new_key)
        # carry the visible localisation over to the fresh key
        for lang in dict.fromkeys((self.owner.loc_language, "english")):
            text = self.owner.loc_get(option.name_key, lang)
            if text:
                self.owner.loc_set(new_key, lang, text)
        self._selected = new
        self._on_change()
        self._rebuild(keep_selection=True)

    def _move(self, option: Option, delta: int) -> None:
        if self._event is None or not self._editable:
            return
        self.flush()
        if self._event.move_option(option, delta):
            self._selected = option
            self._on_change()
            self._rebuild(keep_selection=True)

    def _edit_script(self, name: str) -> None:
        option = self._selected
        if option is None or self._event is None:
            return
        kinds = dict(_OPTION_SCRIPTS)[name]
        initial = self._get_script(option, name)

        def submitted(text: str) -> None:
            if not self._editable:
                return
            try:
                if name == "effects":
                    option.effects_script = text
                else:
                    option.set_script(name, text)
            except Exception:
                return
            self._on_change()
            self._refresh_scripts()

        ScriptEditorDialog(self, self.owner, f"option · {name}", initial,
                           submitted if self._editable else (lambda text: None),
                           kinds, self._event.id)
