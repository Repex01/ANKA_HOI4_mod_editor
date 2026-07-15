"""Technology tags dialog: categories and folder definitions from
``common/technology_tags``.

Categories: searchable list with an "AI" marker (the category appears in a
``common/ai_focuses`` research block — without one the AI never researches the
techs), per-language localisation editing, and creation of new categories in the
mod's additive ``anka_technology.txt``. Folders: the declared folder tabs with
ledger/doctrine info (creation goes through the New Folder wizard).
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..common import BaseDialog, TextPromptDialog


class TagsDialog(BaseDialog):
    def __init__(self, master, editor):
        super().__init__(master, editor, editor.t("technologies.tags.title"),
                         (620, 560))
        self.editor = editor
        self.service = editor.service

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=12, pady=12)

        # --- categories tab ---------------------------------------------------
        cat_tab = ttk.Frame(nb, style="Card.TFrame", padding=10)
        nb.add(cat_tab, text=self.t("technologies.tags.categories"))
        cat_tab.rowconfigure(1, weight=1)
        cat_tab.columnconfigure(0, weight=1)

        self._search = tk.StringVar()
        self._search.trace_add("write", lambda *_: self._refresh_categories())
        ttk.Entry(cat_tab, textvariable=self._search).grid(
            row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))

        self._cats = ttk.Treeview(cat_tab, columns=("ai", "loc"), show="tree headings")
        self._cats.heading("#0", text=self.t("technologies.tags.category"))
        self._cats.heading("ai", text="AI")
        self._cats.heading("loc", text=self.t("technologies.field.loc_name"))
        self._cats.column("#0", width=230)
        self._cats.column("ai", width=36, anchor="center", stretch=False)
        self._cats.column("loc", width=220)
        self._cats.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(cat_tab, orient="vertical", command=self._cats.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self._cats.configure(yscrollcommand=sb.set)
        self._cats.tag_configure("noai", foreground=self.palette.text_muted)
        self._cats.bind("<Double-1>", self._edit_category_loc)

        bar = ttk.Frame(cat_tab, style="Card.TFrame")
        bar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(bar, text="➕ " + self.t("technologies.tags.add_category"),
                   style="Accent.TButton",
                   command=self._add_category).pack(side="left")
        ttk.Label(bar, text=self.t("technologies.tags.ai_hint"),
                  style="CardMuted.TLabel", wraplength=380,
                  justify="left").pack(side="left", padx=12)

        # --- folders tab -------------------------------------------------------
        fol_tab = ttk.Frame(nb, style="Card.TFrame", padding=10)
        nb.add(fol_tab, text=self.t("technologies.tags.folders"))
        fol_tab.rowconfigure(0, weight=1)
        fol_tab.columnconfigure(0, weight=1)

        self._fols = ttk.Treeview(fol_tab, columns=("ledger", "doctrine", "avail"),
                                  show="tree headings")
        self._fols.heading("#0", text=self.t("technologies.col.folder"))
        self._fols.heading("ledger", text=self.t("technologies.col.ledger"))
        self._fols.heading("doctrine", text=self.t("technologies.tags.doctrine"))
        self._fols.heading("avail", text=self.t("technologies.tags.available"))
        self._fols.column("#0", width=190)
        self._fols.column("ledger", width=70, anchor="center", stretch=False)
        self._fols.column("doctrine", width=70, anchor="center", stretch=False)
        self._fols.column("avail", width=210)
        self._fols.grid(row=0, column=0, sticky="nsew")
        sb2 = ttk.Scrollbar(fol_tab, orient="vertical", command=self._fols.yview)
        sb2.grid(row=0, column=1, sticky="ns")
        self._fols.configure(yscrollcommand=sb2.set)

        ttk.Button(fol_tab, text="➕ " + self.t("technologies.new_folder"),
                   style="Accent.TButton",
                   command=lambda: (self.destroy(), editor._new_folder())).grid(
            row=1, column=0, sticky="w", pady=(8, 0))

        self._refresh_categories()
        self._refresh_folders()

    # ---------------------------------------------------------------- categories
    def _refresh_categories(self) -> None:
        query = self._search.get().strip().lower()
        self._cats.delete(*self._cats.get_children())
        ai = self.service.ai_focus_categories()
        lang = self.editor.loc_language
        for cat in self.service.categories():
            if query and query not in cat.lower():
                continue
            loc = self.service.loc_get(cat, lang) or ""
            in_ai = cat in ai
            self._cats.insert("", "end", iid=cat, text=cat,
                              values=("✓" if in_ai else "—", loc),
                              tags=() if in_ai else ("noai",))

    def _add_category(self) -> None:
        taken = set(self.service.categories())

        def submit(name: str) -> None:
            self.service.add_category(name)
            self._refresh_categories()

        TextPromptDialog(self, self.editor,
                         self.t("technologies.tags.add_category"),
                         self.t("technologies.tags.category"), submit,
                         initial="my_category", taken=taken)

    def _edit_category_loc(self, _event=None) -> None:
        sel = self._cats.selection()
        if not sel:
            return
        cat = sel[0]
        lang = self.editor.loc_language
        current = self.service.loc_get(cat, lang) or ""

        def submit(text: str) -> None:
            self.service.loc_set(cat, lang, text)
            # the research_bonus tooltip uses the "<cat>_research" twin key
            if self.service.loc_get(f"{cat}_research", lang) is None:
                self.service.loc_set(f"{cat}_research", lang, text)
            self._refresh_categories()

        TextPromptDialog(self, self.editor,
                         self.t("technologies.tags.category_loc", cat=cat),
                         self.t("technologies.field.loc_name") + f" ({lang})",
                         submit, initial=current, pattern=r"^.+$")

    # ------------------------------------------------------------------- folders
    def _refresh_folders(self) -> None:
        self._fols.delete(*self._fols.get_children())
        for fid, fdef in self.service.folders().items():
            avail = " ".join(fdef.available.split()) if fdef.available else ""
            if len(avail) > 40:
                avail = avail[:37] + "…"
            self._fols.insert("", "end", iid=fid, text=fid,
                              values=(fdef.ledger, "✓" if fdef.doctrine else "—",
                                      avail))
