"""Inspector of the Events editor: the full event form.

A dumb form over the block-backed `Event`: values load on `show()`, edits commit
back instantly (debounced for typed fields) through the owning editor, which
tracks dirty documents and saves them on demand/on_leave. Title/desc/picture are
edited as ordered conditional-variant lists (`VariantListEditor`), options via
`OptionListEditor`.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ...config.constants import HOI4_LANGUAGES
from ...core.pdx import Block
from ...services.event_service import (
    EVENT_FLAG_DEFAULTS,
    EVENT_SCRIPT_FIELDS,
    Event,
)
from ...ui.widgets.tooltip import attach_help
from ..common import (
    InspectorBase,
    PdxPreviewDialog,
    ScriptEditorDialog,
    TextPromptDialog,
    VariantListEditor,
)
from .options import OptionListEditor

# Visual-editor kinds per event script block. mean_time_to_happen holds
# days/months factors + modifier blocks — trigger-flavoured, like ai_will_do.
_SCRIPT_KINDS = {
    "trigger": ("trigger",),
    "immediate": ("effect", "trigger"),
    "mean_time_to_happen": ("trigger",),
}

_EVENT_ID_PATTERN = r"^\w+(?:\.\w+)*\.\w+$"

# Sprite families for the picture gallery, by event kind.
_PICTURE_PREFIXES = {
    "country_event": ("GFX_report_event_",),
    "news_event": ("GFX_news_event_",),
}
_PICTURE_PREFIXES_DEFAULT = ("GFX_report_event_", "GFX_news_event_")


class EventInspector(InspectorBase):
    def __init__(self, master, owner):
        super().__init__(master, owner)
        self.event: Event | None = None
        self.doc = None
        self._build()

    def _build(self) -> None:
        b = self.body
        r = 0
        self._title_label = ttk.Label(b, text="", style="Heading.TLabel")
        self._title_label.grid(row=r, column=0, columnspan=2, sticky="w",
                               pady=(0, 2)); r += 1
        self._subtitle = ttk.Label(b, text="", style="CardMuted.TLabel",
                                   wraplength=460)
        self._subtitle.grid(row=r, column=0, columnspan=2, sticky="w",
                            pady=(0, 8)); r += 1

        # id + rename
        ttk.Label(b, text="ID", style="CardMuted.TLabel").grid(
            row=r, column=0, sticky="w", pady=3)
        id_row = ttk.Frame(b, style="Card.TFrame")
        id_row.grid(row=r, column=1, sticky="ew", padx=(8, 0)); r += 1
        id_row.columnconfigure(0, weight=1)
        self._id_entry = ttk.Entry(id_row, state="readonly", font=("Consolas", 10))
        self._id_entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(id_row, text="✎", width=3, command=self._rename).grid(
            row=0, column=1, padx=(4, 0))

        # localisation language (shared with the whole editor)
        ttk.Label(b, text=self.t("focuses.inspector.language"),
                  style="CardMuted.TLabel").grid(row=r, column=0, sticky="w", pady=3)
        self._lang = ttk.Combobox(b, state="readonly",
                                  values=list(HOI4_LANGUAGES), width=14)
        self._lang.grid(row=r, column=1, sticky="w", padx=(8, 0), pady=3); r += 1
        self._lang.set(self.owner.loc_language)
        self._lang.bind("<<ComboboxSelected>>", self._on_language)

        # title / desc / picture variant lists
        mark = self._mark_dirty
        self._var_title = VariantListEditor(b, self.owner, "title", "loc", mark)
        self._var_title.grid(row=r, column=0, columnspan=2, sticky="ew",
                             pady=(6, 2)); r += 1
        self._var_desc = VariantListEditor(b, self.owner, "desc", "loc", mark)
        self._var_desc.grid(row=r, column=0, columnspan=2, sticky="ew",
                            pady=2); r += 1
        self._var_picture = VariantListEditor(
            b, self.owner, "picture", "sprite", mark,
            sprite_prefixes=self._picture_prefixes,
            on_import=self._import_picture)
        self._var_picture.grid(row=r, column=0, columnspan=2, sticky="ew",
                               pady=2); r += 1

        # flags + timeout
        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew",
                              pady=6); r += 1
        self._flags: dict[str, tk.BooleanVar] = {}
        for name in EVENT_FLAG_DEFAULTS:
            var = tk.BooleanVar()
            self._flags[name] = var
            cb = ttk.Checkbutton(b, text=name, style="Card.TCheckbutton",
                                 variable=var,
                                 command=lambda n=name: self._commit_flag(n))
            cb.grid(row=r, column=0, columnspan=2, sticky="w"); r += 1
            attach_help(cb, self.t, f"event.{name}", self.palette)

        timeout_label = ttk.Label(b, text="timeout_days", style="CardMuted.TLabel")
        timeout_label.grid(row=r, column=0, sticky="w", pady=3)
        attach_help(timeout_label, self.t, "event.timeout_days", self.palette)
        self._timeout_var = tk.StringVar()
        timeout_entry = ttk.Entry(b, textvariable=self._timeout_var, width=10)
        timeout_entry.grid(row=r, column=1, sticky="w", padx=(8, 0), pady=3); r += 1
        self._timeout_var.trace_add("write", lambda *_: self._debounce(
            "timeout", self._commit_timeout))
        timeout_entry.bind("<FocusOut>", lambda e: self._commit_timeout())

        # scripts
        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew",
                              pady=6); r += 1
        ttk.Label(b, text=self.t("focuses.inspector.scripts"),
                  style="Card.TLabel").grid(row=r, column=0, columnspan=2,
                                            sticky="w", pady=(0, 3)); r += 1
        self._script_status: dict[str, ttk.Label] = {}
        for field_name in EVENT_SCRIPT_FIELDS:
            row = ttk.Frame(b, style="Card.TFrame")
            row.grid(row=r, column=0, columnspan=2, sticky="ew", pady=1); r += 1
            status = ttk.Label(row, text="○", style="CardMuted.TLabel", width=2)
            status.pack(side="left")
            label = ttk.Label(row, text=field_name, style="CardMuted.TLabel")
            label.pack(side="left")
            attach_help(label, self.t, f"event.{field_name}", self.palette)
            self._script_status[field_name] = status
            ttk.Button(row, text="✎", width=3,
                       command=lambda n=field_name: self._edit_script(n)).pack(
                side="left", padx=(6, 0))

        # options
        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew",
                              pady=6); r += 1
        self._options_editor = OptionListEditor(b, self.owner, self._mark_dirty)
        self._options_editor.grid(row=r, column=0, columnspan=2, sticky="ew"); r += 1

        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew",
                              pady=8); r += 1
        actions = ttk.Frame(b, style="Card.TFrame")
        actions.grid(row=r, column=0, columnspan=2, sticky="ew")
        ttk.Button(actions, text="👁 " + self.t("focuses.preview"),
                   command=self._preview).pack(side="left", fill="x",
                                               expand=True, padx=(0, 4))
        self._dup_btn = ttk.Button(actions, text="⧉ " + self.t("focuses.duplicate"),
                                   command=lambda: self.owner.duplicate_event())
        self._dup_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self._del_btn = ttk.Button(actions, text="🗑 " + self.t("events.delete"),
                                   command=lambda: self.owner.delete_event())
        self._del_btn.pack(side="left", fill="x", expand=True)

    def _mark_dirty(self) -> None:
        self.owner.mark_dirty(self.doc)

    def _picture_prefixes(self) -> tuple[str, ...]:
        kind = self.event.kind if self.event is not None else ""
        return _PICTURE_PREFIXES.get(kind, _PICTURE_PREFIXES_DEFAULT)

    def _import_picture(self, path) -> str | None:
        if self.event is None:
            return None
        return self.owner.import_picture(path, self.event)

    # --------------------------------------------------------------------- show
    def flush_pending(self) -> None:
        super().flush_pending()
        for widget in (self._var_title, self._var_desc, self._var_picture,
                       self._options_editor):
            widget.flush()

    def show(self, doc, event: Event | None, editable: bool = True) -> None:
        self.flush_pending()
        self.doc = doc
        self.event = event
        self._editable = editable
        self._loading = True
        try:
            if event is None:
                return
            self._title_label.configure(text=event.id or "—")
            origin = doc.ref.rel_file if doc is not None else ""
            kind_part = self.t(f"events.kind.{event.kind}")
            self._subtitle.configure(
                text=f"{kind_part}  ·  {origin}"
                     + ("" if editable else "  ·  🔒 " + self.t("focuses.readonly")))
            self._id_entry.configure(state="normal")
            self._id_entry.delete(0, "end")
            self._id_entry.insert(0, event.id)
            self._id_entry.configure(state="readonly")
            self._lang.set(self.owner.loc_language)
            self._var_title.show(event, editable)
            self._var_desc.show(event, editable)
            self._var_picture.show(event, editable)
            for name, var in self._flags.items():
                var.set(event.get_flag(name))
            self._timeout_var.set(event.get_raw("timeout_days"))
            self._refresh_scripts()
            self._options_editor.show(event, editable)
            state = "normal" if editable else "disabled"
            for widget in self._flags_widgets():
                widget.configure(state=state)
            self._dup_btn.configure(state=state)
            self._del_btn.configure(state=state)
        finally:
            self._loading = False

    def _flags_widgets(self):
        return [w for w in self.body.winfo_children()
                if isinstance(w, ttk.Checkbutton)]

    def _refresh_scripts(self) -> None:
        event = self.event
        for name, label in self._script_status.items():
            filled = bool(event and event.get_script(name).strip())
            label.configure(text="●" if filled else "○",
                            foreground=self.palette.accent if filled
                            else self.palette.text_muted)

    # ------------------------------------------------------------------ commits
    def _commit_flag(self, name: str) -> None:
        if not self._guard() or self.event is None:
            return
        self.event.set_flag(name, self._flags[name].get())
        self._mark_dirty()

    def _commit_timeout(self) -> None:
        if not self._guard() or self.event is None:
            return
        value = self._timeout_var.get().strip()
        if value != self.event.get_raw("timeout_days"):
            self.event.set_raw("timeout_days", value)
            self._mark_dirty()

    def _on_language(self, _event=None) -> None:
        self.owner.loc_language = self._lang.get()
        if self.event is not None:
            self.show(self.doc, self.event, self._editable)
        self.owner.refresh_tree_labels()

    # ------------------------------------------------------------------ actions
    def _rename(self) -> None:
        if not self._guard() or self.event is None:
            return
        event = self.event
        TextPromptDialog(self, self.owner, self.t("events.rename"),
                         self.t("events.rename_label"),
                         lambda new_id: self.owner.rename_event(event, new_id),
                         initial=event.id,
                         taken=set(self.owner.known_ids()) - {event.id},
                         pattern=_EVENT_ID_PATTERN)

    def _edit_script(self, name: str) -> None:
        event = self.event
        if event is None:
            return

        def submitted(text: str) -> None:
            if not self._editable:
                return
            try:
                event.set_script(name, text)
            except Exception:
                return
            self._mark_dirty()
            self._refresh_scripts()

        ScriptEditorDialog(self, self.owner, name, event.get_script(name),
                           submitted if self._editable else (lambda text: None),
                           _SCRIPT_KINDS.get(name, ("trigger",)), event.id)

    def _preview(self) -> None:
        if self.event is not None:
            self.flush_pending()
            PdxPreviewDialog(self, self.owner,
                             self.t("focuses.preview_title", id=self.event.id),
                             Block([self.event.pair]))
