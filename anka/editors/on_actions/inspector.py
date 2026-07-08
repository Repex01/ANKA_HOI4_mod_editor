"""Inspector of one on_action hook entry.

A dumb form over `OnActionEntry`: the repeatable ``effect`` blocks are edited
as one merged script (loss-free — the engine just concatenates them),
``random_events`` as a weight→event block. Unknown keys are listed and
preserved untouched.
"""
from __future__ import annotations

from tkinter import messagebox, ttk

from ...core.pdx import Block, Pair
from ..common import InspectorBase, PdxPreviewDialog, ScriptEditorDialog

_FIELDS = ("effect", "random_events")


class OnActionInspector(InspectorBase):
    def __init__(self, master, owner):
        super().__init__(master, owner)
        self.doc = None
        self.entry = None
        self._build()

    def _build(self) -> None:
        b = self.body
        r = 0
        self._title = ttk.Label(b, text="", style="Heading.TLabel")
        self._title.grid(row=r, column=0, columnspan=2, sticky="w")
        r += 1
        self._file_lbl = ttk.Label(b, text="", style="CardMuted.TLabel",
                                   wraplength=420)
        self._file_lbl.grid(row=r, column=0, columnspan=2, sticky="w",
                            pady=(0, 8))
        r += 1

        self._script_status: dict[str, ttk.Label] = {}
        for name in _FIELDS:
            row = ttk.Frame(b, style="Card.TFrame")
            row.grid(row=r, column=0, columnspan=2, sticky="ew", pady=1)
            r += 1
            status = ttk.Label(row, text="○", style="CardMuted.TLabel", width=2)
            status.pack(side="left")
            ttk.Label(row, text=name, style="CardMuted.TLabel").pack(side="left")
            self._script_status[name] = status
            ttk.Button(row, text="✎", width=3,
                       command=lambda n=name: self._edit_script(n)).pack(
                side="left", padx=(6, 0))

        self._extra_lbl = ttk.Label(b, text="", style="CardMuted.TLabel",
                                    wraplength=420, justify="left")
        self._extra_lbl.grid(row=r, column=0, columnspan=2, sticky="w",
                             pady=(6, 0))
        r += 1

        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew", pady=8)
        r += 1
        actions = ttk.Frame(b, style="Card.TFrame")
        actions.grid(row=r, column=0, columnspan=2, sticky="ew")
        ttk.Button(actions, text="👁 " + self.t("focuses.preview"),
                   command=self._preview).pack(side="left")
        self._delete_btn = ttk.Button(actions,
                                      text="🗑 " + self.t("on_actions.delete"),
                                      command=self._delete)
        self._delete_btn.pack(side="right")

    # -------------------------------------------------------------------- show
    def show(self, doc, entry, editable: bool) -> None:
        self.flush_pending()
        self._loading = True
        self.doc = doc
        self.entry = entry
        self._editable = editable
        if entry is None:
            self._loading = False
            return
        self._title.configure(
            text=entry.name + ("  🔒" if not editable else ""))
        self._file_lbl.configure(text=doc.ref.rel_file)
        self._refresh_scripts()
        extras = entry.extra_keys
        self._extra_lbl.configure(
            text=(self.t("on_actions.extra_keys") + ": " + ", ".join(extras))
            if extras else "")
        self._delete_btn.configure(state="normal" if editable else "disabled")
        self._loading = False

    def _refresh_scripts(self) -> None:
        if self.entry is None:
            return
        texts = {"effect": self.entry.effect_text,
                 "random_events": self.entry.random_events_text}
        for name, status in self._script_status.items():
            status.configure(text="●" if texts[name].strip() else "○")

    # ----------------------------------------------------------------- actions
    def _edit_script(self, name: str) -> None:
        entry = self.entry
        if entry is None:
            return
        text = (entry.effect_text if name == "effect"
                else entry.random_events_text)
        if not self._editable:
            ScriptEditorDialog(self, self.owner, name, text,
                               lambda t: None, ("effect", "trigger"), entry.name)
            return

        def submitted(new_text: str) -> None:
            try:
                if name == "effect":
                    entry.set_effect_text(new_text)
                else:
                    entry.set_random_events_text(new_text)
            except Exception:
                return
            self.owner.mark_dirty(self.doc)
            self._refresh_scripts()

        ScriptEditorDialog(self, self.owner, name, text, submitted,
                           ("effect", "trigger"), entry.name)

    def _preview(self) -> None:
        if self.entry is not None:
            PdxPreviewDialog(self, self.owner, self.entry.name,
                             Block([Pair("on_actions",
                                         Block([self.entry.pair]))]))

    def _delete(self) -> None:
        if self.entry is None or not self._editable:
            return
        if not messagebox.askyesno("ANKA", self.t("on_actions.confirm_delete",
                                                  name=self.entry.name)):
            return
        self.owner.delete_entry(self.doc, self.entry)
