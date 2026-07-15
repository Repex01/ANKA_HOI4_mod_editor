"""New-system doctrines window (``common/doctrines``, AAT+).

A resizable Toplevel owned by the Technologies editor: grouped entry tree on
the left (folders / grand doctrines / subdoctrines / tracks), a form on the
right. Scalar fields commit on focus-out; trigger/effect blocks open the
shared `ScriptEditorDialog`; milestones and rewards — anonymous blocks paired
with tracks by order — are edited as an ordered list. Vanilla files are
read-only with a one-click copy-into-mod override; dirty documents are saved
by the Save button and on close.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from ...core.pdx import Block, dumps
from ...core.pdx import parse as pdx_parse
from ...services.doctrine_service import (
    KINDS,
    DoctrineDocument,
    DoctrineService,
    GrandDoctrine,
    Subdoctrine,
    _Entry,
)
from ..common import TextPromptDialog
from ..common.script_editor import ScriptEditorDialog

_KIND_SCRIPTS = {
    "folders": (("allowed", ("trigger",)),),
    "grand_doctrines": (("available", ("trigger",)), ("visible", ("trigger",)),
                        ("ai_will_do", ("trigger", "modifier"))),
    "subdoctrines": (("available", ("trigger",)), ("visible", ("trigger",)),
                     ("ai_will_do", ("trigger", "modifier")),
                     ("mastery", ("modifier",))),
    "tracks": (("active", ("trigger",)), ("mastery", ("modifier",))),
}
_KIND_SCALARS = {
    "folders": ("name", "ledger", "tab_gfx", "ledger_gfx", "color_frame", "sound"),
    "grand_doctrines": ("folder", "name", "description", "icon", "xp_cost",
                        "xp_type", "max_track_rows", "max_track_columns"),
    "subdoctrines": ("name", "description", "icon", "reward_gfx", "xp_cost",
                     "xp_type", "allow_in_multiple_tracks"),
    "tracks": ("name", "background", "background_offset", "frame", "icon",
               "icon_frame"),
}


class DoctrinesDialog(tk.Toplevel):
    def __init__(self, master, editor):
        super().__init__(master)
        self.editor = editor
        self.t = editor.t
        self.palette = editor.palette
        self.service = DoctrineService(editor.context)
        self._docs: dict = {}                 # path -> DoctrineDocument
        self._dirty: set = set()
        self._current: tuple | None = None    # (ref, doc, entry)
        self._form_widgets: list = []
        self._vars: dict[str, tk.StringVar] = {}

        self.title(self.t("technologies.doctrines.title"))
        self.configure(bg=self.palette.bg)
        top = master.winfo_toplevel()
        self.transient(top)
        w, h = 1060, 680
        self.geometry(f"{w}x{h}+{max(0, top.winfo_rootx() + (top.winfo_width() - w) // 2)}"
                      f"+{max(0, top.winfo_rooty() + (top.winfo_height() - h) // 2)}")
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda e: self._close())

        bar = ttk.Frame(self, style="TFrame")
        bar.pack(fill="x", padx=12, pady=(12, 6))
        ttk.Button(bar, text="➕ " + self.t("technologies.doctrines.new_entry"),
                   command=self._new_entry).pack(side="left")
        self._copy_btn = ttk.Button(
            bar, text="⧉ " + self.t("technologies.doctrines.copy_to_mod"),
            command=self._copy_to_mod)
        ttk.Button(bar, text="💾 " + self.t("common.save"),
                   command=self._save).pack(side="right")
        self._issues_label = ttk.Label(bar, text="", style="Muted.TLabel")
        self._issues_label.pack(side="right", padx=12)

        body = ttk.Frame(self, style="TFrame")
        body.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        left = ttk.Frame(body, style="Card.TFrame", padding=8)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        self._tree = ttk.Treeview(left, show="tree", selectmode="browse")
        self._tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(left, orient="vertical", command=self._tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.column("#0", width=290)
        self._tree.tag_configure("vanilla", foreground=self.palette.text_muted)
        self._tree.bind("<<TreeviewSelect>>", self._on_pick)

        from ...ui.widgets import ScrollableFrame
        right = ttk.Frame(body, style="Card.TFrame")
        right.grid(row=0, column=1, sticky="nsew")
        scroll = ScrollableFrame(right, bg=self.palette.surface)
        scroll.pack(fill="both", expand=True)
        self._form = scroll.body
        self._form.configure(style="Card.TFrame", padding=(14, 10))
        self._form.columnconfigure(1, weight=1)

        self._reload_tree()
        self._refresh_issues()
        self.grab_set()

    # ------------------------------------------------------------------- tree
    def _reload_tree(self, select: str | None = None) -> None:
        self._tree.delete(*self._tree.get_children())
        for kind in KINDS:
            parent = self._tree.insert(
                "", "end", iid=f"kind:{kind}", open=True,
                text=self.t(f"technologies.doctrines.kind.{kind}"))
            for ref in self.service.list_docs(kind):
                for eid in ref.ids:
                    label = eid + ("" if not ref.edited else "  ✎")
                    iid = f"{kind}:{eid}"
                    if self._tree.exists(iid):
                        continue
                    self._tree.insert(parent, "end", iid=iid, text=label,
                                      tags=("vanilla",) if ref.is_vanilla else ())
        if select and self._tree.exists(select):
            self._tree.selection_set(select)
            self._tree.see(select)

    def _on_pick(self, _event=None) -> None:
        sel = self._tree.selection()
        if not sel or sel[0].startswith("kind:"):
            return
        kind, _sep, eid = sel[0].partition(":")
        located = self.service.entries(kind).get(eid)
        if located is None:
            return
        ref, _entry = located
        doc = self._load(ref)
        entry = doc.find(eid)
        if entry is None:
            return
        self._current = (ref, doc, entry)
        if ref.is_vanilla:
            self._copy_btn.pack(side="left", padx=6)
        else:
            self._copy_btn.pack_forget()
        self._build_form(ref, entry)

    def _load(self, ref) -> DoctrineDocument:
        doc = self.service.load(ref)
        self._docs[ref.path] = doc
        return doc

    def _touch(self) -> None:
        if self._current is not None:
            self._dirty.add(self._current[0].path)
        self._refresh_issues()

    # ------------------------------------------------------------------- form
    def _clear_form(self) -> None:
        for w in self._form_widgets:
            w.destroy()
        self._form_widgets = []
        self._vars = {}

    def _build_form(self, ref, entry: _Entry) -> None:
        self._clear_form()
        editable = not ref.is_vanilla
        kind = ref.kind
        row = 0

        def add(widget) -> None:
            self._form_widgets.append(widget)

        head = ttk.Label(self._form, text=f"{entry.id}   ·   {ref.name}"
                         + ("" if editable else "   🔒"),
                         style="CardTitle.TLabel")
        head.grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 8))
        add(head)
        row += 1

        for key in _KIND_SCALARS[kind]:
            lbl = ttk.Label(self._form, text=key, style="CardMuted.TLabel")
            lbl.grid(row=row, column=0, sticky="w", pady=2)
            add(lbl)
            var = tk.StringVar(value=entry.get(key) or "")
            self._vars[key] = var
            e = ttk.Entry(self._form, textvariable=var, width=26,
                          state="normal" if editable else "disabled")
            e.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=2)
            e.bind("<FocusOut>", lambda _e, k=key, v=var:
                   self._commit_scalar(k, v))
            add(e)
            row += 1

        if isinstance(entry, GrandDoctrine):
            row = self._list_row(row, "tracks", entry.tracks,
                                 lambda vals: (setattr(entry, "tracks", vals),
                                               self._touch()), editable)
        if isinstance(entry, Subdoctrine):
            row = self._list_row(row, "track", entry.tracks,
                                 lambda vals: (setattr(entry, "tracks", vals),
                                               self._touch()), editable)
            row = self._list_row(row, "xor", entry.xor,
                                 lambda vals: (setattr(entry, "xor", vals),
                                               self._touch()), editable)

        # script blocks
        for key, kinds in _KIND_SCRIPTS[kind]:
            filled = "●" if entry.get_script(key).strip() else "○"
            btn = ttk.Button(self._form, text=f"{filled} {key}",
                             state="normal" if editable else "disabled",
                             command=lambda k=key, kk=kinds:
                             self._edit_script(k, kk))
            btn.grid(row=row, column=0, columnspan=2, sticky="ew", pady=2)
            add(btn)
            row += 1

        # ordered milestone/reward block lists (anonymous or named children)
        if isinstance(entry, GrandDoctrine):
            row = self._block_list(row, "milestones", entry.milestones,
                                   lambda create=False:
                                   entry.milestones_container(create), editable,
                                   hint=[self._track_hint(entry, i)
                                         for i in range(len(entry.milestones()))])
        if isinstance(entry, Subdoctrine):
            row = self._block_list(row, "rewards", entry.rewards,
                                   lambda create=False:
                                   entry.rewards_container(create), editable,
                                   named_add=True)

        # escape hatch: the whole entry as script (covers activation effects)
        btn = ttk.Button(self._form, text="✎ " + self.t(
            "technologies.doctrines.edit_body"),
            state="normal" if editable else "disabled",
            command=self._edit_body)
        btn.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(10, 2))
        add(btn)
        row += 1

        if editable:
            extra = ttk.Frame(self._form, style="Card.TFrame")
            extra.grid(row=row, column=0, columnspan=2, sticky="w", pady=(6, 0))
            add(extra)
            ttk.Button(extra, text=self.t("technologies.rename"),
                       command=self._rename).pack(side="left")
            ttk.Button(extra, text="🗑 " + self.t("technologies.delete"),
                       command=self._delete).pack(side="left", padx=6)

    def _track_hint(self, grand: GrandDoctrine, index: int) -> str:
        tracks = grand.tracks
        return tracks[index] if index < len(tracks) else "?"

    def _list_row(self, row: int, key: str, values: list[str],
                  apply, editable: bool) -> int:
        lbl = ttk.Label(self._form, text=key, style="CardMuted.TLabel")
        lbl.grid(row=row, column=0, sticky="nw", pady=2)
        self._form_widgets.append(lbl)
        line = ttk.Frame(self._form, style="Card.TFrame")
        line.grid(row=row, column=1, sticky="ew", padx=(8, 0))
        self._form_widgets.append(line)
        ttk.Label(line, text=", ".join(values) or "—", style="Card.TLabel",
                  wraplength=380, justify="left").pack(side="left")
        if editable:
            def edit() -> None:
                def submit(raw: str) -> None:
                    vals = [v.strip() for v in raw.replace(",", " ").split()
                            if v.strip()]
                    apply(vals)
                    self._rebuild()

                TextPromptDialog(self, self.editor, key,
                                 self.t("technologies.list_prompt"), submit,
                                 initial=", ".join(values),
                                 pattern=r"^[\w, ]*$")
            ttk.Button(line, text="…", width=3, command=edit).pack(
                side="left", padx=(6, 0))
        return row + 1

    def _block_list(self, row: int, key: str, get_items, get_container,
                    editable: bool, hint: list[str] | None = None,
                    named_add: bool = False) -> int:
        lbl = ttk.Label(self._form, text=key, style="CardMuted.TLabel")
        lbl.grid(row=row, column=0, columnspan=2, sticky="w", pady=(8, 2))
        self._form_widgets.append(lbl)
        row += 1
        frame = ttk.Frame(self._form, style="Card.TFrame")
        frame.grid(row=row, column=0, columnspan=2, sticky="ew")
        self._form_widgets.append(frame)
        for i, (name, blk) in enumerate(get_items()):
            label = name or f"#{i + 1}"
            if hint and i < len(hint):
                label += f"  ({hint[i]})"
            preview = " ".join(dumps(blk, top_level=False).split())
            if len(preview) > 46:
                preview = preview[:43] + "…"
            rowf = ttk.Frame(frame, style="Card.TFrame")
            rowf.pack(fill="x", pady=1)
            ttk.Label(rowf, text=label, style="Card.TLabel", width=20).pack(side="left")
            ttk.Label(rowf, text=preview, style="CardMuted.TLabel").pack(
                side="left", fill="x", expand=True)
            if editable:
                ttk.Button(rowf, text="✎", width=3,
                           command=lambda b=blk: self._edit_anon(b)).pack(side="right")
                ttk.Button(rowf, text="↓", width=3,
                           command=lambda i=i: self._move_block(get_container, i, 1)
                           ).pack(side="right")
                ttk.Button(rowf, text="↑", width=3,
                           command=lambda i=i: self._move_block(get_container, i, -1)
                           ).pack(side="right")
                ttk.Button(rowf, text="✕", width=3,
                           command=lambda b=blk: self._remove_block(get_container, b)
                           ).pack(side="right")
        if editable:
            ttk.Button(frame, text="➕", width=4,
                       command=lambda: self._add_block(get_container, named_add)
                       ).pack(anchor="w", pady=(3, 0))
        return row + 1

    @staticmethod
    def _block_positions(container: Block) -> list[int]:
        """Indices of container items that are child blocks (named or anon)."""
        from ...core.pdx import Pair
        return [i for i, it in enumerate(container.items)
                if isinstance(it, Block)
                or (isinstance(it, Pair) and isinstance(it.value, Block))]

    # ---------------------------------------------------------------- commits
    def _guard(self) -> bool:
        return self._current is not None and not self._current[0].is_vanilla

    def _commit_scalar(self, key: str, var: tk.StringVar) -> None:
        if not self._guard():
            return
        entry = self._current[2]
        if (entry.get(key) or "") != var.get().strip():
            entry.set(key, var.get().strip() or None)
            self._touch()

    def _edit_script(self, key: str, kinds: tuple[str, ...]) -> None:
        if not self._guard():
            return
        entry = self._current[2]

        def apply(text: str) -> None:
            try:
                entry.set_script(key, text)
            except Exception as exc:
                messagebox.showerror("ANKA", str(exc), parent=self)
                return
            self._touch()
            self._rebuild()

        ScriptEditorDialog(self, self.editor, key, entry.get_script(key),
                           apply, kinds=kinds)

    def _edit_body(self) -> None:
        if not self._guard():
            return
        entry = self._current[2]

        def apply(text: str) -> None:
            try:
                parsed = pdx_parse(text, recover=False)
            except Exception as exc:
                messagebox.showerror("ANKA", str(exc), parent=self)
                return
            entry.block.items = parsed.items
            self._touch()
            self._rebuild()

        ScriptEditorDialog(self, self.editor, entry.id,
                           dumps(entry.block, top_level=False), apply,
                           kinds=("effect", "trigger", "modifier"))

    def _edit_anon(self, block: Block) -> None:
        def apply(text: str) -> None:
            try:
                parsed = pdx_parse(text, recover=False)
            except Exception as exc:
                messagebox.showerror("ANKA", str(exc), parent=self)
                return
            block.items = parsed.items
            self._touch()
            self._rebuild()

        ScriptEditorDialog(self, self.editor,
                           self.t("technologies.doctrines.block"),
                           dumps(block, top_level=False), apply,
                           kinds=("effect", "trigger", "modifier"))

    def _add_block(self, get_container, named: bool) -> None:
        if not self._guard():
            return
        container = get_container(True)
        if not named:
            container.items.append(Block())
            self._touch()
            self._rebuild()
            return
        from ...core.pdx import Pair
        taken = {it.key for it in container.items if isinstance(it, Pair)}
        n = 1
        while f"reward_{n}" in taken:
            n += 1

        def submit(name: str) -> None:
            container.items.append(Pair(name, Block()))
            self._touch()
            self._rebuild()

        TextPromptDialog(self, self.editor,
                         self.t("technologies.doctrines.new_entry"),
                         self.t("technologies.doctrines.block"), submit,
                         initial=f"reward_{n}", taken=taken)

    def _remove_block(self, get_container, block: Block) -> None:
        from ...core.pdx import Pair
        container = get_container(False)
        if container is None:
            return
        container.items = [
            it for it in container.items
            if not (it is block
                    or (isinstance(it, Pair) and it.value is block))]
        self._touch()
        self._rebuild()

    def _move_block(self, get_container, index: int, delta: int) -> None:
        container = get_container(False)
        if container is None:
            return
        pos = self._block_positions(container)
        if not (0 <= index < len(pos)) or not (0 <= index + delta < len(pos)):
            return
        a, b = pos[index], pos[index + delta]
        container.items[a], container.items[b] = container.items[b], container.items[a]
        self._touch()
        self._rebuild()

    def _rebuild(self) -> None:
        if self._current is not None:
            self._build_form(self._current[0], self._current[2])

    # ---------------------------------------------------------------- actions
    def _new_entry(self) -> None:
        sel = self._tree.selection()
        kind = KINDS[1]                       # grand_doctrines by default
        if sel:
            iid = sel[0]
            kind = iid[len("kind:"):] if iid.startswith("kind:") \
                else iid.split(":", 1)[0]
        if kind not in KINDS:
            kind = KINDS[1]
        refs = [r for r in self.service.list_docs(kind) if not r.is_vanilla]
        if refs:
            ref = refs[0]
        else:
            ref = self.service.create_doc(kind, f"anka_{kind}.txt")
        doc = self._load(ref)
        taken = set(self.service.entries(kind))

        def submit(eid: str) -> None:
            try:
                self.service.add_entry(doc, eid)
            except ValueError as exc:
                messagebox.showerror("ANKA", str(exc), parent=self)
                return
            self._dirty.add(ref.path)
            self._reload_tree(select=f"{kind}:{eid}")
            self._refresh_issues()

        TextPromptDialog(self, self.editor,
                         self.t("technologies.doctrines.new_entry"),
                         self.t(f"technologies.doctrines.kind.{kind}"), submit,
                         initial=f"my_{kind[:-1] if kind.endswith('s') else kind}",
                         taken=taken)

    def _copy_to_mod(self) -> None:
        if self._current is None or not self._current[0].is_vanilla:
            return
        ref, _doc, entry = self._current
        new_ref = self.service.copy_to_mod(ref)
        self._reload_tree(select=f"{new_ref.kind}:{entry.id}")

    def _rename(self) -> None:
        if not self._guard():
            return
        ref, doc, entry = self._current
        taken = set(self.service.entries(ref.kind))

        def submit(new_id: str) -> None:
            entry.id = new_id
            self._dirty.add(ref.path)
            self._reload_tree(select=f"{ref.kind}:{new_id}")
            self._refresh_issues()

        TextPromptDialog(self, self.editor, self.t("technologies.rename"),
                         self.t("technologies.doctrines.new_entry"), submit,
                         initial=entry.id, taken=taken)

    def _delete(self) -> None:
        if not self._guard():
            return
        ref, doc, entry = self._current
        if not messagebox.askyesno("ANKA", self.t("technologies.confirm_delete",
                                                  name=entry.id), parent=self):
            return
        self.service.remove_entry(doc, entry)
        self._dirty.add(ref.path)
        self._current = None
        self._clear_form()
        self._reload_tree()
        self._refresh_issues()

    def _refresh_issues(self) -> None:
        try:
            issues = self.service.validate()
        except Exception:
            issues = []
        errors = sum(1 for i in issues if i.severity == "error")
        self._issues_label.configure(
            text=f"⛔ {errors} · ⚠ {len(issues) - errors}" if issues else "✓")

    # ------------------------------------------------------------------- save
    def _save(self) -> None:
        for path in list(self._dirty):
            doc = self._docs.get(path)
            if doc is None or doc.ref.is_vanilla:
                self._dirty.discard(path)
                continue
            try:
                self.service.save(doc)
                self._dirty.discard(path)
            except Exception as exc:
                messagebox.showerror("ANKA", self.t("technologies.err.save",
                                                    error=str(exc)), parent=self)

    def _close(self) -> None:
        self._save()
        self.destroy()
