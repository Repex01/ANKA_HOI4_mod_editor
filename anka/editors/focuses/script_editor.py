"""Script editor dialog: visual block constructor + raw text, kept in sync.

The default "Visual" tab is a Scratch-like recursive editor (`BlockTreeEditor`) over
the parsed PDX tree — [+] adds logic containers, catalog effects/triggers (pre-filled
from the game's documented examples) or free-form keys. The "Text" tab shows/accepts
raw script with highlighting and strict validation; switching tabs converts between
the two representations, and saving always emits serialized script text.

Trigger-only fields (available, bypass, cancel, allow_branch) restrict the picker to
triggers; reward-like fields offer both effects and triggers.
"""
from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk
from typing import Callable

from ...core.pdx import Block, dumps
from ...core.pdx import parse as pdx_parse
from .block_editor import BlockTreeEditor
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
                 on_submit: Callable[[str], None],
                 kinds: tuple[str, ...] = ("effect", "trigger"),
                 focus_id: str = ""):
        super().__init__(master, editor, title, (920, 660))
        self.resizable(True, True)
        self._on_submit = on_submit
        self._kinds = kinds
        self._highlight_job: str | None = None

        try:
            parsed = pdx_parse(initial, recover=False) if initial.strip() else Block()
            self._block = Block(parsed.items)
            broken_initial = None
        except Exception:
            self._block = Block()
            broken_initial = initial          # fall back to the text tab

        outer = ttk.Frame(self, style="TFrame")
        outer.pack(fill="both", expand=True, padx=12, pady=12)
        outer.rowconfigure(1, weight=1)
        outer.columnconfigure(0, weight=1)
        ttk.Label(outer, text=title, style="Title.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 6))

        self._nb = ttk.Notebook(outer)
        self._nb.grid(row=1, column=0, sticky="nsew")

        # --- visual tab -----------------------------------------------------
        visual_tab = ttk.Frame(self._nb, style="Card.TFrame", padding=8)
        self._nb.add(visual_tab, text=self.t("focuses.script.visual"))
        self.tree = BlockTreeEditor(visual_tab, editor, self._block, kinds,
                                    focus_id=focus_id, root_key=title)
        self.tree.pack(fill="both", expand=True)

        # --- text tab --------------------------------------------------------
        text_tab = ttk.Frame(self._nb, style="Card.TFrame", padding=8)
        self._nb.add(text_tab, text=self.t("focuses.script.text"))
        text_tab.rowconfigure(0, weight=1)
        text_tab.columnconfigure(0, weight=1)
        self.text = tk.Text(text_tab, wrap="none", undo=True,
                            bg=self.palette.surface_alt, fg=self.palette.text,
                            insertbackground=self.palette.text, relief="flat",
                            font=("Consolas", 11), tabs="24")
        self.text.grid(row=0, column=0, sticky="nsew")
        ysb = ttk.Scrollbar(text_tab, orient="vertical", command=self.text.yview)
        ysb.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=ysb.set)
        self.text.bind("<KeyRelease>", self._schedule_highlight)

        p = self.palette
        self.text.tag_configure("comment", foreground="#7a9e6d")
        self.text.tag_configure("string", foreground="#d8a657")
        self.text.tag_configure("number", foreground="#89b4fa" if p.is_dark else "#1a56c4")
        self.text.tag_configure("scope", foreground=p.accent)
        self.text.tag_configure("operator", foreground=p.text_muted)

        # --- bottom bar --------------------------------------------------------
        bar = ttk.Frame(outer, style="TFrame")
        bar.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self._status = ttk.Label(bar, text="", style="Muted.TLabel")
        self._status.pack(side="left")
        ttk.Button(bar, text=self.t("common.save"), style="Accent.TButton",
                   command=self._submit).pack(side="right")
        ttk.Button(bar, text=self.t("common.cancel"),
                   command=self.destroy).pack(side="right", padx=8)
        ttk.Button(bar, text=self.t("focuses.script.check"),
                   command=self._validate_text).pack(side="right", padx=8)
        ttk.Button(bar, text="📋 " + self.t("focuses.script.copy"),
                   command=self._copy).pack(side="right", padx=8)

        self._tab = 0
        self._nb.bind("<<NotebookTabChanged>>", self._tab_changed)
        if broken_initial is not None:
            self.text.insert("1.0", broken_initial)
            self._nb.select(1)
        self._highlight()

    # ------------------------------------------------------------- tab syncing
    def _current_text(self) -> str:
        """The script as text, whichever tab is active."""
        if self._nb.index(self._nb.select()) == 0:
            return dumps(self._block).rstrip("\n")
        return self.text.get("1.0", "end").rstrip()

    def _tab_changed(self, _event=None) -> None:
        new = self._nb.index(self._nb.select())
        if new == self._tab:
            return
        if new == 1:                                # visual -> text
            self.text.delete("1.0", "end")
            self.text.insert("1.0", dumps(self._block).rstrip("\n"))
            self.text.edit_reset()
            self._highlight()
            self._tab = 1
            return
        # text -> visual: must parse
        source = self.text.get("1.0", "end")
        if source.strip():
            try:
                parsed = pdx_parse(source, recover=False)
            except Exception as exc:
                self._nb.select(1)                  # stay on text
                self._report_error(exc)
                return
            self._block.items = parsed.items
        else:
            self._block.items = []
        self.tree.render()
        self._status.configure(text="")
        self._tab = 0

    # ------------------------------------------------------------- validation
    def _report_error(self, exc: Exception) -> None:
        line = getattr(exc, "line", None)
        hint = f" ({self.t('focuses.script.line')} {line})" if line else ""
        self._status.configure(text=self.t("focuses.script.invalid") + hint,
                               foreground=self.palette.danger)
        if line:
            self.text.mark_set("insert", f"{line}.0")
            self.text.see(f"{line}.0")

    def _validate_text(self) -> bool:
        source = self._current_text()
        if not source.strip():
            self._status.configure(text=self.t("focuses.script.empty_ok"),
                                   foreground=self.palette.text_muted)
            return True
        try:
            pdx_parse(source, recover=False)
        except Exception as exc:
            self._report_error(exc)
            return False
        self._status.configure(text="✓ " + self.t("focuses.script.valid"),
                               foreground="#3f8f5f")
        return True

    def _copy(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self._current_text())
        self._status.configure(text="✓ " + self.t("focuses.script.copied"),
                               foreground=self.palette.text_muted)

    # ------------------------------------------------------------- highlight
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
        if self._nb.index(self._nb.select()) == 1 and not self._validate_text():
            return
        value = self._current_text()
        self.destroy()
        self._on_submit(value)
