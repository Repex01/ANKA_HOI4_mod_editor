"""PDX script editor dialog with the effect/trigger catalog at hand.

Free-form Paradox script (completion_reward, available, ai_will_do, ...) is edited as
text with light syntax highlighting and *strict* parser validation on save — invalid
script can never reach the mod files. The right-hand catalog browser (fed by
`ScriptCatalog`, i.e. the game's own documentation) searches effects/triggers by name,
scope and description, shows docs and inserts ready-made snippets at the cursor.
"""
from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk
from typing import Callable

from ...core.pdx import parse as pdx_parse
from ..effects import ScriptCatalog
from .dialogs import BaseDialog

_TOKEN_RES = (
    ("comment", re.compile(r"#[^\n]*")),
    ("string", re.compile(r'"[^"\n]*"')),
    ("number", re.compile(r"(?<![\w@])-?\d+(?:\.\d+)?\b")),
    ("scope", re.compile(r"\b(?:ROOT|PREV|THIS|FROM|OWNER|CONTROLLER|[A-Z]{3})\b")),
    ("operator", re.compile(r"[<>]=?|=")),
)


class ScriptEditorDialog(BaseDialog):
    def __init__(self, master, editor, title: str, initial: str,
                 on_submit: Callable[[str], None], default_kind: str = "effect"):
        super().__init__(master, editor, title, (980, 640))
        self.resizable(True, True)
        self._on_submit = on_submit
        self._highlight_job: str | None = None

        outer = ttk.Frame(self, style="TFrame")
        outer.pack(fill="both", expand=True, padx=12, pady=12)
        outer.columnconfigure(0, weight=3)
        outer.columnconfigure(1, weight=2)
        outer.rowconfigure(0, weight=1)

        # --- left: the text editor ----------------------------------------
        left = ttk.Frame(outer, style="Card.TFrame", padding=10)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)
        ttk.Label(left, text=title, style="Heading.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 6))

        self.text = tk.Text(left, wrap="none", undo=True, bg=self.palette.surface_alt,
                            fg=self.palette.text, insertbackground=self.palette.text,
                            relief="flat", font=("Consolas", 11), tabs="24")
        self.text.grid(row=1, column=0, sticky="nsew")
        ysb = ttk.Scrollbar(left, orient="vertical", command=self.text.yview)
        ysb.grid(row=1, column=1, sticky="ns")
        self.text.configure(yscrollcommand=ysb.set)
        self.text.insert("1.0", initial)
        self.text.edit_reset()
        self.text.bind("<KeyRelease>", self._schedule_highlight)

        p = self.palette
        self.text.tag_configure("comment", foreground="#7a9e6d")
        self.text.tag_configure("string", foreground="#d8a657")
        self.text.tag_configure("number", foreground="#89b4fa" if p.is_dark else "#1a56c4")
        self.text.tag_configure("scope", foreground=p.accent)
        self.text.tag_configure("operator", foreground=p.text_muted)

        status_row = ttk.Frame(left, style="Card.TFrame")
        status_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self._status = ttk.Label(status_row, text="", style="CardMuted.TLabel")
        self._status.pack(side="left")
        ttk.Button(status_row, text=self.t("common.save"), style="Accent.TButton",
                   command=self._submit).pack(side="right")
        ttk.Button(status_row, text=self.t("common.cancel"),
                   command=self.destroy).pack(side="right", padx=8)
        ttk.Button(status_row, text=self.t("focuses.script.check"),
                   command=self._validate).pack(side="right", padx=8)

        # --- right: catalog browser ----------------------------------------
        right = ttk.Frame(outer, style="Card.TFrame", padding=10)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(3, weight=3)
        right.rowconfigure(5, weight=2)
        right.columnconfigure(0, weight=1)

        self._kind = tk.StringVar(value=default_kind)
        kind_row = ttk.Frame(right, style="Card.TFrame")
        kind_row.grid(row=0, column=0, sticky="ew")
        for value, key in (("effect", "focuses.script.effects"),
                           ("trigger", "focuses.script.triggers")):
            ttk.Radiobutton(kind_row, text=self.t(key), value=value,
                            variable=self._kind, style="Card.TCheckbutton",
                            command=self._refresh_list).pack(side="left", padx=(0, 12))

        self._query = tk.StringVar()
        self._query.trace_add("write", lambda *_: self._refresh_list())
        ttk.Entry(right, textvariable=self._query).grid(row=1, column=0, sticky="ew",
                                                        pady=(8, 4))
        scope_row = ttk.Frame(right, style="Card.TFrame")
        scope_row.grid(row=2, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(scope_row, text=self.t("focuses.script.scope"),
                  style="CardMuted.TLabel").pack(side="left")
        self._scope = ttk.Combobox(scope_row, state="readonly", width=22)
        self._scope.pack(side="left", padx=(6, 0))
        self._scope.bind("<<ComboboxSelected>>", lambda e: self._refresh_list())

        self._list = tk.Listbox(right, bg=self.palette.surface_alt, fg=self.palette.text,
                                relief="flat", selectbackground=self.palette.accent,
                                font=("Consolas", 10), exportselection=False)
        self._list.grid(row=3, column=0, sticky="nsew")
        self._list.bind("<<ListboxSelect>>", self._show_doc)
        self._list.bind("<Double-1>", lambda e: self._insert())

        ttk.Button(right, text="⤺  " + self.t("focuses.script.insert"),
                   command=self._insert).grid(row=4, column=0, sticky="ew", pady=6)

        self._doc = tk.Text(right, height=9, wrap="word", state="disabled",
                            bg=self.palette.surface, fg=self.palette.text_muted,
                            relief="flat", font=("Consolas", 9))
        self._doc.grid(row=5, column=0, sticky="nsew")

        self._items: list = []
        self._reload_scopes()
        self._refresh_list()
        self._highlight()

    # --- catalog -----------------------------------------------------------
    def _reload_scopes(self) -> None:
        scopes = [self.t("focuses.script.any_scope")] + ScriptCatalog.scopes(self._kind.get())
        self._scope.configure(values=scopes)
        self._scope.current(0)

    def _refresh_list(self) -> None:
        kind = self._kind.get()
        if not self._scope.get() or self._scope.get() not in (
                [self.t("focuses.script.any_scope")] + ScriptCatalog.scopes(kind)):
            self._reload_scopes()
        scope = self._scope.get()
        if scope == self.t("focuses.script.any_scope"):
            scope = None
        self._items = ScriptCatalog.search(self._query.get(), kind, scope)[:400]
        self._list.delete(0, "end")
        for item in self._items:
            self._list.insert("end", item.name)

    def _selected_item(self):
        sel = self._list.curselection()
        return self._items[sel[0]] if sel else None

    def _show_doc(self, _event=None) -> None:
        item = self._selected_item()
        self._doc.configure(state="normal")
        self._doc.delete("1.0", "end")
        if item is not None:
            parts = [item.name]
            if item.scopes:
                parts.append(f"{self.t('focuses.script.scope')}: {', '.join(item.scopes)}")
            if item.targets:
                parts.append(f"{self.t('focuses.script.targets')}: {', '.join(item.targets)}")
            if item.description:
                parts.append("")
                parts.append(item.description)
            if item.example:
                parts.append("")
                parts.append(item.example)
            self._doc.insert("1.0", "\n".join(parts))
        self._doc.configure(state="disabled")

    def _insert(self) -> None:
        item = self._selected_item()
        if item is None:
            return
        snippet = item.snippet()
        line_start = self.text.index("insert linestart")
        current_line = self.text.get(line_start, "insert lineend")
        indent = re.match(r"[\t ]*", current_line).group(0)
        if current_line.strip():
            snippet = "\n" + snippet
        body = snippet.replace("\n", "\n" + indent)
        self.text.insert("insert", body)
        self.text.focus_set()
        self._schedule_highlight()

    # --- validation / highlight ----------------------------------------------
    def _validate(self) -> bool:
        source = self.text.get("1.0", "end")
        if not source.strip():
            self._status.configure(text=self.t("focuses.script.empty_ok"),
                                   foreground=self.palette.text_muted)
            return True
        try:
            pdx_parse(source, recover=False)
        except Exception as exc:
            line = getattr(exc, "line", None)
            hint = f" ({self.t('focuses.script.line')} {line})" if line else ""
            self._status.configure(text=self.t("focuses.script.invalid") + hint,
                                   foreground=self.palette.danger)
            if line:
                self.text.mark_set("insert", f"{line}.0")
                self.text.see(f"{line}.0")
            return False
        self._status.configure(text="✓ " + self.t("focuses.script.valid"),
                               foreground="#3f8f5f")
        return True

    def _schedule_highlight(self, _event=None) -> None:
        if self._highlight_job is not None:
            self.after_cancel(self._highlight_job)
        self._highlight_job = self.after(250, self._highlight)

    def _highlight(self) -> None:
        self._highlight_job = None
        text = self.text.get("1.0", "end-1c")
        for tag, _ in _TOKEN_RES:
            self.text.tag_remove(tag, "1.0", "end")
        for tag, regex in _TOKEN_RES:
            for m in regex.finditer(text):
                self.text.tag_add(tag, f"1.0+{m.start()}c", f"1.0+{m.end()}c")

    def _submit(self) -> None:
        if not self._validate():
            return
        value = self.text.get("1.0", "end").rstrip()
        self.destroy()
        self._on_submit(value)
