"""Scripted localisation editor — ``common/scripted_localisation``.

A standalone module (sidebar entry): toolbar · file→entry tree (left) · a
structured form for the selected ``defined_text`` (center). The form mirrors the
script model directly — a name, and one card per ``text`` alternative:

* **+ text** appends a new alternative;
* inside a card, **+ trigger** adds a ``trigger = { … }`` and opens the trigger
  script editor (with the shared block editor, so state/country pickers work);
* the localisation value for each ``localization_key`` is edited **inline, right
  next to the key**, per language (collision-safe writes through `LocCatalog`).

Edits mutate the parsed nodes in place, so anything the form doesn't surface
(e.g. ``random_list`` weights) is preserved. Dependency mods and the base game
layer in read-only with one-click copy-to-mod; the loc index is warmed on a
background thread so opening the editor never blocks the UI.
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from ...config.constants import HOI4_LANGUAGES
from ...core.pdx import Block, Pair, Scalar, dumps
from ...core.pdx import parse as pdx_parse
from ...services._locutil import LocCatalog
from ...services.scripted_loc_service import ScriptedLocService
from ...ui.widgets import ScrollableFrame
from ..base import EditorModule, EditorRegistry
from ..common import ScriptEditorDialog, TextPromptDialog


@EditorRegistry.register
class ScriptedLocEditor(EditorModule):
    id = "scripted_loc"
    name_key = "editors.scripted_loc.name"
    desc_key = "editors.scripted_loc.desc"
    order = 82

    def __init__(self, context, services):
        super().__init__(context, services)
        self.service = ScriptedLocService(context)
        self.loc_language = {"ru": "russian"}.get(
            services.settings.current.language, "english")
        # Referenced loc keys can live anywhere in vanilla, so index the whole tree
        # (empty filter matches every file); warmed off-thread at build time.
        self.loc = LocCatalog(
            context.mod.path, context.game_path, vanilla_filter="",
            default_pattern="anka_scripted_localisation_l_{lang}.yml",
            dep_roots=context.dependency_paths)
        self._ready = threading.Event()
        self._panel: ScriptedLocPanel | None = None

    # ------------------------------------------------------------------- build
    def build(self, parent) -> ttk.Widget:
        threading.Thread(target=self._warm, daemon=True).start()
        self._panel = ScriptedLocPanel(parent, self)
        return self._panel

    def _warm(self) -> None:
        try:
            self.loc.get("CLOSE", self.loc_language)   # build the loc index
        except Exception:
            pass
        finally:
            self._ready.set()

    # ----------------------------------------------------------- owner protocol
    def loc_ready(self) -> bool:
        return self._ready.is_set()

    def loc_get(self, key: str, language: str) -> str:
        return self.loc.get(key, language) or ""

    def loc_set(self, key: str, language: str, text: str) -> None:
        self.loc.set(key, language, text)

    def value_options(self, vtype: str) -> list[tuple[str, str]]:
        return []

    # ------------------------------------------------------------------ saving
    def save_all(self) -> None:
        if self._panel is not None:
            self._panel.save_all()

    def on_leave(self) -> None:
        self.save_all()


class ScriptedLocPanel(ttk.Frame):
    def __init__(self, master, editor: ScriptedLocEditor):
        super().__init__(master, style="TFrame")
        self.editor = editor
        self.t = editor.t
        self.palette = editor.palette
        self.service = editor.service
        self.loc_language = editor.loc_language

        self._mod_docs: list = []
        self._vanilla_refs: list = []
        self._dirty: set[str] = set()
        self._items: dict[str, tuple] = {}
        self._copy_ref = None
        self._doc = None
        self._entry = None
        self._entry_index = 0
        self._editable = False
        self._jobs: dict[str, tuple[str, object]] = {}
        self._loc_refreshers: list = []
        self._loc_retry: str | None = None

        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)
        self._build_toolbar()
        self._build_list_panel()
        self._build_center()
        self.reload_tree()

    # --------------------------------------------------- script-owner protocol
    @property
    def context(self):
        return self.editor.context

    def value_options(self, vtype: str) -> list[tuple[str, str]]:
        return self.editor.value_options(vtype)

    def loc_get(self, key: str, language: str) -> str:
        return self.editor.loc_get(key, language)

    def loc_set(self, key: str, language: str, text: str) -> None:
        self.editor.loc_set(key, language, text)

    # ------------------------------------------------------------------- build
    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, style="TFrame")
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self._btn_list = ttk.Button(bar, text="☰ " + self.t("interface.gfx.panel"),
                                    command=self._toggle_list)
        self._btn_list.pack(side="left")
        ttk.Button(bar, text="🗎 " + self.t("scripted_loc.new_file"),
                   command=self._new_file).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="➕ " + self.t("scripted_loc.new_entry"),
                   command=self._new_entry).pack(side="left", padx=4)
        ttk.Button(bar, text="💾 " + self.t("common.save"),
                   command=self.save_all).pack(side="left", padx=4)
        self._copy_btn = ttk.Button(bar, text="⧉ " + self.t("focuses.copy_to_mod"),
                                    command=self._copy_to_mod)

        ttk.Label(bar, text="🌐").pack(side="left", padx=(16, 2))
        self._lang_var = tk.StringVar(value=self.loc_language)
        lang_box = ttk.Combobox(bar, textvariable=self._lang_var, width=12,
                                state="readonly", values=list(HOI4_LANGUAGES))
        lang_box.pack(side="left")
        lang_box.bind("<<ComboboxSelected>>",
                      lambda _e: self._language_changed())

    def _build_list_panel(self) -> None:
        panel = ttk.Frame(self, style="Card.TFrame", padding=10)
        panel.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        panel.rowconfigure(2, weight=1)
        panel.columnconfigure(0, weight=1)
        self._list_panel = panel
        self._list_visible = True
        self.columnconfigure(0, minsize=300)

        self._search = tk.StringVar()
        self._search.trace_add("write", lambda *_: self._refresh_tree())
        ttk.Entry(panel, textvariable=self._search).grid(
            row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self._vanilla = tk.BooleanVar(value=False)
        ttk.Checkbutton(panel, text=self.t("interface.show_vanilla"),
                        style="Card.TCheckbutton", variable=self._vanilla,
                        command=self.reload_tree).grid(row=1, column=0,
                                                       sticky="w", pady=(0, 6))
        self._tree = ttk.Treeview(panel, show="tree", selectmode="browse")
        self._tree.grid(row=2, column=0, sticky="nsew")
        sb = ttk.Scrollbar(panel, orient="vertical", command=self._tree.yview)
        sb.grid(row=2, column=1, sticky="ns")
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.tag_configure("vanilla", foreground=self.palette.text_muted)
        self._tree.tag_configure("file", font=("Segoe UI Semibold", 10))
        self._tree.bind("<<TreeviewSelect>>", self._on_select)
        self._tree.bind("<Delete>", self._delete_selected)

    def _build_center(self) -> None:
        center = ttk.Frame(self, style="TFrame")
        center.grid(row=1, column=1, sticky="nsew")
        center.rowconfigure(0, weight=1)
        center.columnconfigure(0, weight=1)

        self._placeholder = ttk.Label(center,
                                      text=self.t("scripted_loc.select_hint"),
                                      style="Muted.TLabel", wraplength=420)
        self._placeholder.grid(row=0, column=0)

        editor = ttk.Frame(center, style="TFrame")
        editor.rowconfigure(2, weight=1)
        editor.columnconfigure(0, weight=1)
        self._editor_frame = editor

        self._title = ttk.Label(editor, text="", style="Heading.TLabel")
        self._title.grid(row=0, column=0, sticky="w", pady=(0, 6))

        # name row -----------------------------------------------------------
        namerow = ttk.Frame(editor, style="TFrame")
        namerow.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        namerow.columnconfigure(1, weight=1)
        ttk.Label(namerow, text=self.t("scripted_loc.field.name"),
                  style="CardMuted.TLabel").grid(row=0, column=0, sticky="w")
        self._name_var = tk.StringVar()
        name_entry = ttk.Entry(namerow, textvariable=self._name_var)
        name_entry.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self._name_entry = name_entry
        self._name_var.trace_add(
            "write", lambda *_: self._debounce("name", self._commit_name, 700))
        name_entry.bind("<FocusOut>", lambda _e: self._commit_name())

        card = ttk.Frame(editor, style="Card.TFrame", padding=8)
        card.grid(row=2, column=0, sticky="nsew")
        card.rowconfigure(0, weight=1)
        card.columnconfigure(0, weight=1)
        scroll = ScrollableFrame(card, bg=self.palette.surface)
        scroll.grid(row=0, column=0, sticky="nsew")
        self._form = scroll.body
        self._form.configure(style="Card.TFrame", padding=(6, 4))
        self._form.columnconfigure(0, weight=1)

        self._show_editor(False)

    # ---------------------------------------------------------------- toggles
    def _toggle_list(self) -> None:
        self._list_visible = not self._list_visible
        if self._list_visible:
            self.columnconfigure(0, minsize=300)
            self._list_panel.grid()
        else:
            self._list_panel.grid_remove()
            self.columnconfigure(0, minsize=0)

    def _show_editor(self, visible: bool) -> None:
        if visible:
            self._placeholder.grid_remove()
            self._editor_frame.grid(row=0, column=0, sticky="nsew")
        else:
            self._editor_frame.grid_remove()
            self._placeholder.grid(row=0, column=0)

    def _language_changed(self) -> None:
        self.flush_pending()
        self.loc_language = self._lang_var.get()
        self._rebuild_form()

    # ------------------------------------------------------------------- tree
    def reload_tree(self, keep_selection: bool = False) -> None:
        selected = self._tree.selection() if keep_selection else ()
        self._mod_docs = []
        for ref in self.service.list_docs(include_vanilla=False):
            try:
                self._mod_docs.append(self.service.load(ref))
            except Exception:
                continue
        self._vanilla_refs = ([r for r in self.service.list_docs(True)
                               if r.is_vanilla]
                              if self._vanilla.get() else [])
        self._refresh_tree()
        if selected and self._tree.exists(selected[0]):
            self._tree.selection_set(selected[0])

    def _refresh_tree(self) -> None:
        query = self._search.get().strip().lower()
        open_files = {iid for iid in self._tree.get_children("")
                      if self._tree.item(iid, "open")}
        self._tree.delete(*self._tree.get_children())
        self._items.clear()

        def add_file(rel_file: str, names: list[str], is_vanilla: bool,
                     payload) -> None:
            rows = [(i, name) for i, name in enumerate(names)
                    if not query or query in name.lower()]
            if not rows and query and query not in rel_file.lower():
                return
            if not rows and not query and is_vanilla:
                return
            file_iid = f"f::{rel_file}"
            self._tree.insert("", "end", iid=file_iid,
                              text=rel_file.rsplit("/", 1)[-1],
                              open=bool(query) or file_iid in open_files,
                              tags=("file",) + (("vanilla",) if is_vanilla
                                                else ()))
            self._items[file_iid] = ("file", payload, is_vanilla)
            for index, name in rows:
                iid = f"e::{rel_file}::{index}"
                self._tree.insert(file_iid, "end", iid=iid, text=name,
                                  tags=("vanilla",) if is_vanilla else ())
                self._items[iid] = ("entry", payload, index)

        for doc in self._mod_docs:
            add_file(doc.ref.rel_file, [e.name for e in doc.entries()],
                     False, doc)
        for ref in self._vanilla_refs:
            add_file(ref.rel_file, ref.names, True, ref)

    def _on_select(self, _event=None) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        payload = self._items.get(sel[0])
        if payload is None:
            return
        self.flush_pending()
        if payload[0] == "file":
            self._set_copy_btn(payload[1] if payload[2] else None)
            self._doc = self._entry = None
            self._show_editor(False)
            return
        _kind, doc_or_ref, index = payload
        is_vanilla = not hasattr(doc_or_ref, "entries")
        try:
            doc = self.service.load(doc_or_ref) if is_vanilla else doc_or_ref
        except Exception:
            self._show_editor(False)
            return
        entries = doc.entries()
        if index >= len(entries):
            self._show_editor(False)
            return
        self._doc = doc
        self._entry = entries[index]
        self._entry_index = index
        self._editable = not doc.ref.is_vanilla
        self._set_copy_btn(doc.ref if doc.ref.is_vanilla else None)
        self._load_entry()

    def _set_copy_btn(self, vanilla_ref) -> None:
        self._copy_ref = vanilla_ref
        if vanilla_ref is not None:
            self._copy_btn.pack(side="left", padx=2)
        else:
            self._copy_btn.pack_forget()

    # ------------------------------------------------------------- entry view
    def _load_entry(self) -> None:
        self._show_editor(True)
        self._title.configure(
            text=(self._entry.name or "—")
            + ("  🔒" if not self._editable else ""))
        self._name_var.set(self._entry.name)
        self._name_entry.configure(
            state="normal" if self._editable else "readonly")
        self._rebuild_form()

    def _commit_name(self) -> None:
        if self._entry is None or not self._editable:
            return
        new = self._name_var.get().strip()
        if not new or new == self._entry.name:
            return
        self._entry.set_name(new)
        self.mark_dirty(self._doc)
        self._title.configure(text=new)
        iid = f"e::{self._doc.ref.rel_file}::{self._entry_index}"
        if self._tree.exists(iid):
            self._tree.item(iid, text=new)

    # ------------------------------------------------------------------- form
    def _rebuild_form(self) -> None:
        if self._loc_retry is not None:
            self.after_cancel(self._loc_retry)
            self._loc_retry = None
        self._loc_refreshers = []
        for child in self._form.winfo_children():
            child.destroy()
        if self._entry is None:
            return
        row = 0
        for pair in list(self._entry.block.pairs()):
            if pair.key.lower() == "text" and isinstance(pair.value, Block):
                self._text_card(row, pair)
                row += 1
        if self._editable:
            add = ttk.Frame(self._form, style="Card.TFrame")
            add.grid(row=row, column=0, sticky="w", pady=(6, 2))
            ttk.Button(add, text="➕ " + self.t("scripted_loc.add_text"),
                       command=self._add_text).pack(side="left")
        self._refresh_translations()

    def _text_card(self, row: int, text_pair: Pair) -> None:
        block = text_pair.value
        box = tk.Frame(self._form, bg=self.palette.surface,
                       highlightbackground=self.palette.border,
                       highlightcolor=self.palette.border, highlightthickness=1)
        box.grid(row=row, column=0, sticky="ew", pady=4)
        box.columnconfigure(0, weight=1)

        head = ttk.Frame(box, style="Card.TFrame")
        head.grid(row=0, column=0, sticky="ew", padx=6, pady=(4, 0))
        head.columnconfigure(0, weight=1)
        ttk.Label(head, text=self.t("scripted_loc.text"),
                  style="CardMuted.TLabel",
                  font=("Segoe UI Semibold", 9)).grid(row=0, column=0, sticky="w")
        if self._editable:
            ttk.Button(head, text="🗑", width=3,
                       command=lambda: self._remove_text(text_pair)).grid(
                row=0, column=1, sticky="e")

        body = ttk.Frame(box, style="Card.TFrame")
        body.grid(row=1, column=0, sticky="ew", padx=6, pady=(2, 6))
        body.columnconfigure(1, weight=1)
        br = self._trigger_row(body, 0, block)
        rl = block.get_block_ci("random_list")
        if rl is not None:
            self._random_list_rows(body, br, rl)
        else:
            self._loc_key_row(body, br, block)

    def _trigger_row(self, body: ttk.Frame, br: int, block: Block) -> int:
        trigger = block.get_block_ci("trigger")
        cell = ttk.Frame(body, style="Card.TFrame")
        cell.grid(row=br, column=0, columnspan=2, sticky="w", pady=(0, 4))
        if trigger is not None:
            summary = dumps(trigger, top_level=False).replace("\n", " ").strip()
            ttk.Label(cell, text="trigger", style="CardMuted.TLabel").pack(
                side="left")
            ttk.Button(cell, text="✎", width=3,
                       command=lambda: self._edit_trigger(trigger)).pack(
                side="left", padx=(6, 0))
            if self._editable:
                ttk.Button(cell, text="✕", width=3,
                           command=lambda: self._remove_trigger(block)).pack(
                    side="left", padx=(2, 0))
            ttk.Label(cell, text=(summary[:60] + "…") if len(summary) > 60
                      else summary, style="CardMuted.TLabel").pack(
                side="left", padx=(8, 0))
        elif self._editable:
            ttk.Button(cell, text="➕ " + self.t("scripted_loc.add_trigger"),
                       command=lambda: self._add_trigger(block)).pack(side="left")
        return br + 1

    def _loc_key_row(self, body: ttk.Frame, br: int, block: Block) -> int:
        """A ``localization_key`` field with its translation inline right below."""
        lk = next((p for p in block.pairs()
                   if p.key.lower() == "localization_key"
                   and isinstance(p.value, Scalar)), None)
        if lk is None:
            lk = Pair("localization_key", Scalar(""))
            block.items.append(lk)
        return self._key_and_translation(body, br, lk.value)

    def _key_and_translation(self, body: ttk.Frame, br: int,
                             key_scalar: Scalar) -> int:
        uid = str(id(key_scalar))
        ttk.Label(body, text="localization_key",
                  style="CardMuted.TLabel").grid(row=br, column=0, sticky="w",
                                                 pady=2)
        key_var = tk.StringVar(value=key_scalar.raw)
        key_entry = ttk.Entry(body, textvariable=key_var, width=30)
        key_entry.grid(row=br, column=1, sticky="ew", padx=(8, 0), pady=2)
        br += 1

        loc_var = tk.StringVar()
        syncing = [False]
        row2 = ttk.Frame(body, style="Card.TFrame")
        row2.grid(row=br, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        row2.columnconfigure(1, weight=1)
        ttk.Label(row2, text="🌐 " + self.loc_language,
                  style="CardMuted.TLabel").grid(row=0, column=0, sticky="w",
                                                 padx=(16, 6))
        loc_entry = ttk.Entry(row2, textvariable=loc_var)
        loc_entry.grid(row=0, column=1, sticky="ew")
        br += 1

        def reload_loc() -> None:
            syncing[0] = True
            try:
                key = key_var.get().strip()
                loc_var.set((self.editor.loc_get(key, self.loc_language)
                             if key else "").replace("\\n", "\n"))
            finally:
                syncing[0] = False

        def commit_key() -> None:
            if not self._editable:
                return
            key = key_var.get().strip()
            if key == key_scalar.raw:
                return
            key_scalar.raw = key
            self.mark_dirty(self._doc)
            reload_loc()

        def commit_loc() -> None:
            if syncing[0] or not self._editable or not self.editor.loc_ready():
                return
            key = key_var.get().strip()
            if not key:
                return
            text = loc_var.get()
            if text == (self.editor.loc_get(key, self.loc_language) or ""):
                return
            try:
                self.editor.loc_set(key, self.loc_language, text)
            except Exception as exc:                      # noqa: BLE001
                messagebox.showerror("ANKA", str(exc))

        key_var.trace_add(
            "write", lambda *_: self._debounce(uid + ":k", commit_key, 700))
        key_entry.bind("<FocusOut>", lambda _e: commit_key())
        loc_var.trace_add(
            "write", lambda *_: (None if syncing[0]
                                 else self._debounce(uid + ":l", commit_loc, 900)))
        loc_entry.bind("<FocusOut>", lambda _e: commit_loc())
        if not self._editable:
            key_entry.configure(state="readonly")
            loc_entry.configure(state="readonly")
        self._loc_refreshers.append(reload_loc)
        return br

    def _random_list_rows(self, body: ttk.Frame, br: int, rl: Block) -> int:
        ttk.Label(body, text="random_list", style="CardMuted.TLabel",
                  font=("Segoe UI Semibold", 9)).grid(
            row=br, column=0, columnspan=2, sticky="w", pady=(2, 0))
        br += 1
        for pair in list(rl.pairs()):
            if not isinstance(pair.value, Block):
                continue
            weight_var = tk.StringVar(value=pair.key)
            line = ttk.Frame(body, style="Card.TFrame")
            line.grid(row=br, column=0, columnspan=2, sticky="ew", pady=(2, 0))
            ttk.Label(line, text=self.t("scripted_loc.weight"),
                      style="CardMuted.TLabel").pack(side="left", padx=(16, 4))
            w_entry = ttk.Entry(line, textvariable=weight_var, width=6)
            w_entry.pack(side="left")
            if self._editable:
                ttk.Button(line, text="✕", width=3,
                           command=lambda p=pair: self._remove_random(rl, p)).pack(
                    side="right")

            def commit_weight(p=pair, v=weight_var) -> None:
                if self._editable and v.get().strip():
                    p.key = v.get().strip()
                    self.mark_dirty(self._doc)

            weight_var.trace_add(
                "write", lambda *_a, c=commit_weight, u=id(pair):
                self._debounce(f"{u}:w", c, 700))
            w_entry.bind("<FocusOut>", lambda _e, c=commit_weight: c())
            if not self._editable:
                w_entry.configure(state="readonly")
            br += 1
            lk = next((p for p in pair.value.pairs()
                       if p.key.lower() == "localization_key"
                       and isinstance(p.value, Scalar)), None)
            if lk is None:
                lk = Pair("localization_key", Scalar(""))
                pair.value.items.append(lk)
            br = self._key_and_translation(body, br, lk.value)
        if self._editable:
            add = ttk.Frame(body, style="Card.TFrame")
            add.grid(row=br, column=0, columnspan=2, sticky="w", pady=(2, 0))
            ttk.Button(add, text="➕ " + self.t("scripted_loc.add_option"),
                       command=lambda: self._add_random(rl)).pack(side="left",
                                                                  padx=(16, 0))
            br += 1
        return br

    def _refresh_translations(self) -> None:
        if self._loc_retry is not None:
            self.after_cancel(self._loc_retry)
            self._loc_retry = None
        if not self._loc_refreshers:
            return
        if not self.editor.loc_ready():
            self._loc_retry = self.after(400, self._refresh_translations)
            return
        for fn in self._loc_refreshers:
            try:
                fn()
            except tk.TclError:
                pass

    # --------------------------------------------------------- form mutations
    def _fresh_loc_key(self) -> str:
        existing: set[str] = set()

        def collect(block: Block) -> None:
            for p in block.pairs():
                if (p.key.lower() == "localization_key"
                        and isinstance(p.value, Scalar)):
                    existing.add(p.value.raw)
                elif isinstance(p.value, Block):
                    collect(p.value)

        collect(self._entry.block)
        base = self._entry.name or "text"
        i = 1
        while f"{base}_{i}" in existing:
            i += 1
        return f"{base}_{i}"

    def _add_text(self) -> None:
        if not self._editable:
            return
        block = Block()
        block.add("localization_key", Scalar(self._fresh_loc_key()))
        self._entry.block.items.append(Pair("text", block))
        self.mark_dirty(self._doc)
        self._rebuild_form()

    def _remove_text(self, text_pair: Pair) -> None:
        if not self._editable:
            return
        self._entry.block.items = [it for it in self._entry.block.items
                                   if it is not text_pair]
        self.mark_dirty(self._doc)
        self._rebuild_form()

    def _add_trigger(self, block: Block) -> None:
        if not self._editable:
            return
        trigger = Block()
        block.items.insert(0, Pair("trigger", trigger))
        self.mark_dirty(self._doc)
        self._rebuild_form()
        self._edit_trigger(trigger)

    def _remove_trigger(self, block: Block) -> None:
        if not self._editable:
            return
        block.remove_ci("trigger")
        self.mark_dirty(self._doc)
        self._rebuild_form()

    def _edit_trigger(self, trigger: Block) -> None:
        initial = dumps(trigger, top_level=False) if len(trigger.items) else ""

        def submitted(new_text: str) -> None:
            try:
                parsed = (pdx_parse(new_text, recover=False)
                          if new_text.strip() else Block())
            except Exception:                             # noqa: BLE001
                return
            trigger.items = parsed.items
            self.mark_dirty(self._doc)
            self._rebuild_form()

        ScriptEditorDialog(self, self, self.t("scripted_loc.trigger"), initial,
                           submitted if self._editable else (lambda t: None),
                           ("trigger",), self._entry.name if self._entry else "")

    def _add_random(self, rl: Block) -> None:
        if not self._editable:
            return
        option = Block()
        option.add("localization_key", Scalar(self._fresh_loc_key()))
        rl.items.append(Pair("10", option))
        self.mark_dirty(self._doc)
        self._rebuild_form()

    def _remove_random(self, rl: Block, pair: Pair) -> None:
        if not self._editable:
            return
        rl.items = [it for it in rl.items if it is not pair]
        self.mark_dirty(self._doc)
        self._rebuild_form()

    # ----------------------------------------------------------------- actions
    def _new_file(self) -> None:
        def create(name: str) -> None:
            ref = self.service.create_doc(name)
            if any(d.ref.rel_file == ref.rel_file for d in self._mod_docs):
                messagebox.showerror("ANKA", self.t("focuses.err.file_exists"))
                return
            self.reload_tree()
            file_iid = f"f::{ref.rel_file}"
            if self._tree.exists(file_iid):
                self._tree.selection_set(file_iid)
                self._tree.see(file_iid)

        TextPromptDialog(self._tree, self, self.t("scripted_loc.new_file"),
                         self.t("interface.gfx.file_label"), create,
                         pattern=r"^[\w./-]+$")

    def _new_entry(self) -> None:
        def create(name: str) -> None:
            doc = self._target_doc()
            self.service.add_entry(doc, name)
            if all(d.ref.path != doc.ref.path for d in self._mod_docs):
                self._mod_docs.append(doc)
            self.mark_dirty(doc)
            self.save_all()
            self.reload_tree()
            index = len(doc.entries()) - 1
            iid = f"e::{doc.ref.rel_file}::{index}"
            if self._tree.exists(iid):
                self._tree.selection_set(iid)
                self._tree.see(iid)

        TextPromptDialog(self._tree, self, self.t("scripted_loc.new_entry"),
                         self.t("scripted_loc.name_label"), create,
                         pattern=r"^\w+$")

    def _target_doc(self):
        """The mod file new entries go into: the one whose file is selected in the
        tree (if editable), else the shared ANKA default."""
        sel = self._tree.selection()
        if sel:
            payload = self._items.get(sel[0])
            if payload is not None:
                doc = payload[1]
                if hasattr(doc, "entries") and not doc.ref.is_vanilla:
                    return doc
        return self.service.mod_target_doc()

    def _copy_to_mod(self) -> None:
        if self._copy_ref is None:
            return
        selected = self._tree.selection()
        new_ref = self.service.copy_to_mod(self._copy_ref)
        self.reload_tree()
        if selected and selected[0].startswith("e::"):
            index = selected[0].split("::")[-1]
            iid = f"e::{new_ref.rel_file}::{index}"
            if self._tree.exists(iid):
                self._tree.selection_set(iid)
                self._tree.see(iid)

    def _delete_selected(self, _event=None) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        payload = self._items.get(sel[0])
        if payload is None:
            return
        if payload[0] == "file":
            doc_or_ref = payload[1]
            ref = doc_or_ref.ref if hasattr(doc_or_ref, "entries") else doc_or_ref
            if ref.is_vanilla:
                return
            if messagebox.askyesno("ANKA", self.t("scripted_loc.confirm_delete_file",
                                                  name=ref.rel_file.rsplit("/", 1)[-1])):
                self.service.delete_file(ref)
                self._dirty.discard(str(ref.path))
                self._doc = self._entry = None
                self._show_editor(False)
                self.reload_tree()
            return
        _kind, doc, index = payload
        if not hasattr(doc, "entries") or doc.ref.is_vanilla:
            return
        entries = doc.entries()
        if index < len(entries):
            entry = entries[index]
            if messagebox.askyesno("ANKA", self.t("scripted_loc.confirm_delete",
                                                  name=entry.name)):
                self.service.remove_entry(doc, entry)
                self.mark_dirty(doc)
                self._doc = self._entry = None
                self._show_editor(False)
                self.reload_tree()
                file_iid = f"f::{doc.ref.rel_file}"
                if self._tree.exists(file_iid):
                    self._tree.selection_set(file_iid)

    # ------------------------------------------------------------------ saving
    def mark_dirty(self, doc) -> None:
        if doc is not None and not doc.ref.is_vanilla:
            self._dirty.add(str(doc.ref.path))

    def _debounce(self, key: str, commit, delay: int = 800) -> None:
        pending = self._jobs.pop(key, None)
        if pending is not None:
            self.after_cancel(pending[0])
        job = self.after(delay, lambda: (self._jobs.pop(key, None), commit()))
        self._jobs[key] = (job, commit)

    def flush_pending(self) -> None:
        pending = list(self._jobs.values())
        self._jobs.clear()
        for job, commit in pending:
            self.after_cancel(job)
            try:
                commit()
            except tk.TclError:
                pass

    def save_all(self) -> None:
        self.flush_pending()
        for doc in self._mod_docs:
            if str(doc.ref.path) in self._dirty:
                try:
                    self.service.save(doc)
                    self._dirty.discard(str(doc.ref.path))
                except Exception as exc:                  # noqa: BLE001
                    messagebox.showerror("ANKA", self.t("focuses.err.save",
                                                        error=str(exc)))
