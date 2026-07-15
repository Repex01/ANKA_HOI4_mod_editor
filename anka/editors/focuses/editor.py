"""Focuses editor.

Layout: collapsible tree list (left) · toolbar + canvas + collapsible problems panel
(center) · collapsible inspector (right). The editor orchestrates `FocusService`
(model/IO), `FocusCanvas` (view) and `FocusInspector` (form); shared/joint focuses from
other files are rendered read-only alongside the tree that attaches them.

Editing follows the app conventions: vanilla content is never written — a vanilla tree
opens read-only with a one-click "copy into mod" override; edits set a dirty flag and
are saved explicitly or automatically on leaving the editor.
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from ...core.gfx import SpriteResolver
from ...core.pdx import Block, Pair, dumps
from ...core.pdx import parse as pdx_parse
from ...services.focus_service import Focus, FocusService, FocusTreeRef
from ..base import EditorModule, EditorRegistry
from ..common.commands import Command, CommandStack
from .canvas import ZOOM_STEPS, CanvasModel, CanvasNode, FocusCanvas


class _SnapshotCommand(Command):
    """Undo/redo one document mutation by swapping the whole serialized tree.
    Coarse but robust — every structural change (move / create / delete / link /
    tree props / inspector field edits) is captured the same way."""

    label_key = "focuses.cmd.edit"

    def __init__(self, before: str, after: str):
        self.before, self.after = before, after

    def undo(self, editor) -> None:
        editor._restore_snapshot(self.before)

    def redo(self, editor) -> None:
        editor._restore_snapshot(self.after)
from .dialogs import NewTreeDialog, TextPromptDialog, TreePropertiesDialog
from .inspector import FocusInspector
from .templates import load_templates, prereq_groups, template_cells

_KIND_BADGE = {"tree": "", "shared": "S", "joint": "J"}


@EditorRegistry.register
class FocusesEditor(EditorModule):
    id = "focuses"
    name_key = "editors.focuses.name"
    desc_key = "editors.focuses.desc"
    order = 20

    def __init__(self, context, services):
        super().__init__(context, services)
        self.service = FocusService(context)
        self.resolver = SpriteResolver.for_mod(context.mod.path, context.game_path,
                                               context.dependency_paths)
        self.loc_language = {"ru": "russian"}.get(services.settings.current.language,
                                                  "english")
        self._refs: list[FocusTreeRef] = []
        self._ref: FocusTreeRef | None = None
        self._doc = None
        self._attached: list[tuple[FocusTreeRef, Focus]] = []
        self._editable = False
        self._dirty = False
        self._issues_by_focus: dict[str, str] = {}
        self._clipboard: tuple[str, dict[str, tuple[int, int]]] | None = None
        self._menu: tk.Menu | None = None
        self._or_session: tuple[str, int] | None = None   # (source fid, group index)
        self._value_options: dict[str, list[tuple[str, str]]] = {}
        # Undo/redo — snapshot-based history of the open document (Command pattern).
        self._history = CommandStack(limit=60)
        self._hist_baseline: str | None = None    # doc text at the last recorded state
        self._restoring = False                   # suppress recording during undo/redo
        # The sprite map (game + all DLC .gfx files) takes seconds to build; warm it
        # in the background so opening a tree never blocks on it.
        self._resolver_ready = threading.Event()
        threading.Thread(target=self._warm_resolver, daemon=True).start()

    def _warm_resolver(self) -> None:
        try:
            self.resolver.resolve("")
        finally:
            self._resolver_ready.set()

    def resolver_ready(self) -> bool:
        return self._resolver_ready.is_set()

    # ------------------------------------------------------------------- build
    def build(self, parent) -> ttk.Widget:
        root = ttk.Frame(parent, style="TFrame")
        root.columnconfigure(1, weight=1)
        root.rowconfigure(1, weight=1)

        self._build_toolbar(root)
        self._build_list_panel(root)

        center = ttk.Frame(root, style="TFrame")
        center.grid(row=1, column=1, sticky="nsew")
        center.rowconfigure(0, weight=1)
        center.columnconfigure(0, weight=1)
        self.canvas = FocusCanvas(
            center, self.palette,
            resolve_icon=self.resolver.resolve,
            on_select=self._on_select,
            on_move=self._on_move,
            on_create=self._on_create,
            on_link=self._on_link,
            on_context=self._on_context,
            on_delete=self._delete_selected,
            on_link_end=self._on_link_end,
            link_hints={
                "single": self.t("focuses.link_status.single"),
                "or": self.t("focuses.link_status.or"),
                "and": self.t("focuses.link_status.and"),
            },
            icons_ready=self.resolver_ready,
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        from ...ui.hotkeys import bind_ctrl
        for widget in (self.canvas.canvas, self._list):
            bind_ctrl(widget, "z", lambda e: (self.undo(), "break")[1])
            bind_ctrl(widget, "y", lambda e: (self.redo(), "break")[1])
            bind_ctrl(widget, "z", lambda e: (self.redo(), "break")[1],
                      shift=True)
        self._build_problems(center)

        side = ttk.Frame(root, style="TFrame")
        side.grid(row=1, column=2, sticky="nsew", padx=(10, 0))
        side.rowconfigure(0, weight=1)
        self._inspector_panel = side
        self.inspector = FocusInspector(side, self)
        self.inspector.grid(row=0, column=0, sticky="nsew")
        side.columnconfigure(0, minsize=330)

        self._reload_list()
        self._show_placeholder()
        return root

    def _build_toolbar(self, root) -> None:
        bar = ttk.Frame(root, style="TFrame")
        bar.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))

        self._btn_trees = ttk.Button(bar, text="☰ " + self.t("focuses.panel.trees"),
                                     command=self._toggle_list)
        self._btn_trees.pack(side="left")
        self._tree_label = ttk.Label(bar, text="—", style="Muted.TLabel")
        self._tree_label.pack(side="left", padx=12)

        ttk.Button(bar, text="➕ " + self.t("focuses.add_focus"),
                   command=self._add_focus_toolbar).pack(side="left", padx=2)
        ttk.Button(bar, text="⚙ " + self.t("focuses.tree_props"),
                   command=self._open_tree_props).pack(side="left", padx=2)
        self._undo_btn = ttk.Button(bar, text="↶", width=3, command=self.undo,
                                    state="disabled")
        self._undo_btn.pack(side="left", padx=(6, 1))
        self._redo_btn = ttk.Button(bar, text="↷", width=3, command=self.redo,
                                    state="disabled")
        self._redo_btn.pack(side="left", padx=1)
        ttk.Button(bar, text="💾 " + self.t("common.save"),
                   command=self._save).pack(side="left", padx=2)
        self._copy_btn = ttk.Button(bar, text="⧉ " + self.t("focuses.copy_to_mod"),
                                    command=self._copy_to_mod)

        self._btn_inspector = ttk.Button(bar, text=self.t("focuses.panel.inspector") + " ▸",
                                         command=self._toggle_inspector)
        self._btn_inspector.pack(side="right")
        self._btn_problems = ttk.Button(bar, text="⚠ 0", command=self._toggle_problems)
        self._btn_problems.pack(side="right", padx=4)

        ttk.Button(bar, text="－", width=3, command=lambda: self._zoom(-1)).pack(
            side="right", padx=(8, 0))
        self._zoom_label = ttk.Label(bar, text="100%", style="Muted.TLabel", width=5,
                                     anchor="center")
        self._zoom_label.pack(side="right")
        ttk.Button(bar, text="＋", width=3, command=lambda: self._zoom(1)).pack(side="right")

        self._branch_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text=self.t("focuses.move_branch"),
                        variable=self._branch_var).pack(side="right", padx=10)
        self._grid_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text=self.t("focuses.grid"), variable=self._grid_var,
                        command=lambda: self.canvas.set_grid(self._grid_var.get())
                        ).pack(side="right", padx=(10, 0))

    def _build_list_panel(self, root) -> None:
        panel = ttk.Frame(root, style="Card.TFrame", padding=10)
        panel.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        panel.rowconfigure(2, weight=1)
        panel.columnconfigure(0, weight=1)
        self._list_panel = panel

        self._search = tk.StringVar()
        self._search.trace_add("write", lambda *_: self._refresh_list())
        ttk.Entry(panel, textvariable=self._search).grid(
            row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self._vanilla = tk.BooleanVar(value=False)
        ttk.Checkbutton(panel, text=self.t("focuses.show_vanilla"),
                        style="Card.TCheckbutton", variable=self._vanilla,
                        command=self._reload_list).grid(row=1, column=0, sticky="w",
                                                        pady=(0, 6))
        self._list = ttk.Treeview(panel, columns=("country", "count"), show="tree headings",
                                  selectmode="browse")
        self._list.heading("#0", text=self.t("focuses.col.tree"))
        self._list.heading("country", text=self.t("focuses.col.country"))
        self._list.heading("count", text="#")
        self._list.column("#0", width=170)
        self._list.column("country", width=55, anchor="center")
        self._list.column("count", width=42, anchor="e")
        self._list.grid(row=2, column=0, sticky="nsew")
        sb = ttk.Scrollbar(panel, orient="vertical", command=self._list.yview)
        sb.grid(row=2, column=1, sticky="ns")
        self._list.configure(yscrollcommand=sb.set)
        self._list.tag_configure("vanilla", foreground=self.palette.text_muted)
        self._list.bind("<<TreeviewSelect>>", self._on_pick_tree)

        btns = ttk.Frame(panel, style="Card.TFrame")
        btns.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        btns.columnconfigure((0, 1), weight=1)
        ttk.Button(btns, text="➕ " + self.t("focuses.new_tree"), style="Accent.TButton",
                   command=self._new_tree).grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(btns, text="🗑", width=4, command=self._delete_tree).grid(
            row=0, column=1, sticky="ew")

    def _build_problems(self, center) -> None:
        panel = ttk.Frame(center, style="Card.TFrame", padding=(8, 6))
        panel.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        panel.columnconfigure(0, weight=1)
        self._problems_panel = panel
        self._problems = ttk.Treeview(panel, columns=("sev", "focus", "msg"),
                                      show="headings", height=5)
        self._problems.heading("sev", text="!")
        self._problems.heading("focus", text=self.t("focuses.col.focus"))
        self._problems.heading("msg", text=self.t("focuses.col.problem"))
        self._problems.column("sev", width=30, anchor="center", stretch=False)
        self._problems.column("focus", width=230, stretch=False)
        self._problems.column("msg", width=400)
        self._problems.grid(row=0, column=0, sticky="ew")
        sb = ttk.Scrollbar(panel, orient="vertical", command=self._problems.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self._problems.configure(yscrollcommand=sb.set)
        self._problems.tag_configure("error", foreground=self.palette.danger)
        self._problems.bind("<Double-1>", self._jump_to_problem)
        panel.grid_remove()
        self._problems_visible = False

    # ------------------------------------------------------------- panel toggles
    # NB: explicit flags, not winfo_ismapped() — the latter lags behind grid()/
    # grid_remove() until redraw, so fast toggling could get stuck collapsed.
    def _toggle_list(self) -> None:
        self._list_visible = not getattr(self, "_list_visible", True)
        if self._list_visible:
            self._list_panel.grid()
        else:
            self._list_panel.grid_remove()

    def _toggle_inspector(self) -> None:
        self._inspector_visible = not getattr(self, "_inspector_visible", True)
        if self._inspector_visible:
            self._inspector_panel.grid()
            self._btn_inspector.configure(text=self.t("focuses.panel.inspector") + " ▸")
        else:
            self._inspector_panel.grid_remove()
            self._btn_inspector.configure(text="◂ " + self.t("focuses.panel.inspector"))

    def _toggle_problems(self) -> None:
        self._problems_visible = not self._problems_visible
        if self._problems_visible:
            self._problems_panel.grid()
        else:
            self._problems_panel.grid_remove()

    def _zoom(self, direction: int) -> None:
        steps = list(ZOOM_STEPS)
        idx = min(range(len(steps)), key=lambda i: abs(steps[i] - self.canvas.zoom))
        idx = max(0, min(idx + direction, len(steps) - 1))
        self.canvas.set_zoom(steps[idx])
        self._zoom_label.configure(text=f"{round(steps[idx] * 100)}%")

    # --------------------------------------------------------------- tree list
    def _reload_list(self) -> None:
        self._refs = self.service.list_trees(include_vanilla=self._vanilla.get())
        self._refresh_list()

    def _refresh_list(self) -> None:
        query = self._search.get().strip().lower()
        self._list.delete(*self._list.get_children())
        for i, ref in enumerate(self._refs):
            if query and query not in ref.id.lower() and query not in ref.country.lower():
                continue
            badge = _KIND_BADGE.get(ref.kind, "")
            name = f"{ref.id}" + (f"  [{badge}]" if badge else "")
            if ref.edited:
                name = "✎ " + name
            self._list.insert("", "end", iid=str(i), text=name,
                              values=(ref.country, ref.focus_count),
                              tags=("vanilla",) if ref.is_vanilla else ())

    def _on_pick_tree(self, _event=None) -> None:
        sel = self._list.selection()
        if not sel:
            return
        ref = self._refs[int(sel[0])]
        if self._ref is not None and ref.path == self._ref.path:
            return
        self._open(ref)

    def _open(self, ref: FocusTreeRef) -> None:
        self._save()                        # flush the previous document
        try:
            self._doc = self.service.load(ref)
        except Exception as exc:
            messagebox.showerror("ANKA", self.t("focuses.err.parse", error=str(exc)))
            return
        self._ref = ref
        self._editable = not ref.is_vanilla
        self._attached = self.service.attached_shared(self._doc) if self._doc.tree else []
        self._dirty = False
        label = ref.id + ("  🔒 " + self.t("focuses.readonly") if not self._editable else "")
        self._tree_label.configure(text=label)
        if ref.is_vanilla:
            self._copy_btn.pack(side="left", padx=2)
        else:
            self._copy_btn.pack_forget()
        self.inspector.show(None)
        self.refresh_canvas(keep_view=False)
        self._validate()
        # Fresh document → reset the undo history and its baseline snapshot.
        self._hist_baseline = dumps(self._doc.root) if self._doc is not None else None
        self._history.clear()
        self._update_history_buttons()

    def _show_placeholder(self) -> None:
        self.canvas.set_model(CanvasModel(), keep_view=False)

    def _new_tree(self) -> None:
        from ...services.country_service import CountryService
        tags = [r.tag for r in CountryService(self.context).list_tags(include_vanilla=True)]

        def submit(file_name: str, tree_id: str, tag: str, default: bool) -> None:
            try:
                ref = self.service.create_tree(file_name, tree_id, tag, default)
            except FileExistsError:
                messagebox.showerror("ANKA", self.t("focuses.err.file_exists"))
                return
            self._reload_list()
            self._open(ref)

        NewTreeDialog(self.canvas, self, tags, submit)

    def _copy_to_mod(self) -> None:
        if self._ref is None or not self._ref.is_vanilla:
            return
        ref = self.service.copy_to_mod(self._ref)
        self._ref = None
        self._reload_list()
        self._open(ref)

    def _delete_tree(self) -> None:
        sel = self._list.selection()
        if not sel:
            return
        ref = self._refs[int(sel[0])]
        if ref.is_vanilla:
            messagebox.showinfo("ANKA", self.t("focuses.err.vanilla_delete"))
            return
        if not messagebox.askyesno("ANKA", self.t("focuses.confirm_delete_tree",
                                                  name=ref.id)):
            return
        self.service.delete(ref)
        if self._ref is not None and ref.path == self._ref.path:
            self._ref = None
            self._doc = None
            self._dirty = False
            self._hist_baseline = None
            self._history.clear()
            self._update_history_buttons()
            self._tree_label.configure(text="—")
            self._show_placeholder()
            self.inspector.show(None)
        self._reload_list()

    def _open_tree_props(self) -> None:
        if self._doc is None or self._doc.tree is None or not self._editable:
            return
        shared_ids = sorted(self.service.shared_index())

        def changed() -> None:
            self.mark_dirty()
            self._attached = self.service.attached_shared(self._doc)
            self._tree_label.configure(text=self._doc.tree.id)
            self.refresh_canvas()
            self._validate()

        TreePropertiesDialog(self.canvas, self, self._doc, shared_ids, changed)

    # ---------------------------------------------------------------- model → view
    def _display_focuses(self) -> list[tuple[Focus, bool]]:
        """(focus, editable) for everything on the canvas."""
        if self._doc is None:
            return []
        out = [(f, self._editable) for f in self._doc.all_focuses()]
        out.extend((f, False) for _ref, f in self._attached)
        return out

    def known_ids(self) -> list[str]:
        return [f.id for f, _e in self._display_focuses() if f.id]

    def positions(self) -> dict[str, tuple[int, int]]:
        return self.service.resolved_positions([f for f, _e in self._display_focuses()])

    def refresh_canvas(self, keep_view: bool = True) -> None:
        display = self._display_focuses()
        positions = self.service.resolved_positions([f for f, _e in display])
        nodes: list[CanvasNode] = []
        prereqs: list[tuple[str, str, bool]] = []
        mutex_pairs: set[frozenset[str]] = set()
        ids = {f.id for f, _e in display}
        for focus, editable in display:
            if not focus.id:
                continue
            loc_key = focus.text or focus.id
            name = self.service.focus_name(loc_key, self.loc_language)
            nodes.append(CanvasNode(
                fid=focus.id, pos=positions.get(focus.id, (0, 0)),
                icon=focus.icon, name=name, kind=focus.kind, editable=editable,
                days=int(focus.cost * 7),
                has_issues=focus.id in self._issues_by_focus))
            for group in focus.prerequisites:
                for parent in group:
                    if parent in ids:
                        prereqs.append((focus.id, parent, len(group) > 1))
            for other in focus.mutually_exclusive:
                if other in ids:
                    mutex_pairs.add(frozenset((focus.id, other)))
        mutexes = [tuple(sorted(p)) for p in mutex_pairs if len(p) == 2]
        selection = list(self.canvas.selection)
        self.canvas.set_model(CanvasModel(nodes, prereqs, mutexes), keep_view=keep_view)
        self.canvas.set_selection(selection)

    # -------------------------------------------------------------- canvas events
    def _on_select(self, fids: list[str]) -> None:
        if not fids or self._doc is None:
            self.inspector.show(None)
            return
        fid = fids[0]
        focus = self._doc.find(fid)
        if focus is not None:
            self.inspector.show(focus, self._editable)
            return
        for _ref, f in self._attached:
            if f.id == fid:
                self.inspector.show(f, False)
                return
        self.inspector.show(None)

    def _on_move(self, fids: list[str], delta: tuple[int, int]) -> None:
        if self._doc is None or not self._editable:
            return
        moved = set(fids)
        if self._branch_var.get():
            moved |= self._descendants(moved)
        positions = self.positions()
        for fid in moved:
            focus = self._doc.find(fid)
            if focus is None:                       # attached/read-only: skip
                continue
            if focus.relative_position_id in moved:
                continue                            # follows its moved anchor for free
            old = positions.get(fid, (0, 0))
            self.service.move_focus(focus, (old[0] + delta[0], old[1] + delta[1]),
                                    positions)
        self.mark_dirty()
        self.refresh_canvas()
        self._validate()
        if self.inspector.focus_obj is not None:
            self.inspector.show(self.inspector.focus_obj, self._editable)

    def _descendants(self, roots: set[str]) -> set[str]:
        """Focuses whose prerequisite chain leads into `roots` (branch move)."""
        children: dict[str, set[str]] = {}
        for focus, _e in self._display_focuses():
            for group in focus.prerequisites:
                for parent in group:
                    children.setdefault(parent, set()).add(focus.id)
        out: set[str] = set()
        frontier = list(roots)
        while frontier:
            for child in children.get(frontier.pop(), ()):
                if child not in out and child not in roots:
                    out.add(child)
                    frontier.append(child)
        return out

    def _on_create(self, cell: tuple[int, int]) -> None:
        if self._doc is None or not self._editable:
            return
        doc = self._doc
        kinds: list[tuple[str, str]] = []
        if doc.tree is not None:
            kinds.append(("focus", self.t("focuses.kind.focus")))
        kinds.append(("shared_focus", self.t("focuses.kind.shared")))
        kinds.append(("joint_focus", self.t("focuses.kind.joint")))
        default_kind = kinds[0][0] if doc.tree is not None else (
            "joint_focus" if doc.joint and not doc.shared else "shared_focus")
        kinds.sort(key=lambda kv: kv[0] != default_kind)

        prefix = (self._ref.country or self._ref.id or "my").split("_")[0]
        taken = set(self.known_ids())
        n = 1
        while f"{prefix}_focus_{n}" in taken:
            n += 1

        def submit(fid: str, kind: str) -> None:
            focus = self.service.add_focus(doc, kind, fid=fid, x=cell[0], y=cell[1])
            self.mark_dirty()
            self.refresh_canvas()
            self._validate()
            self.canvas.set_selection([fid], notify=True)

        TextPromptDialog(self.canvas, self, self.t("focuses.add_focus"),
                         self.t("focuses.focus_id"), submit,
                         initial=f"{prefix}_focus_{n}", taken=taken,
                         choices_label=self.t("focuses.kind_label"), choices=kinds)

    def _add_focus_toolbar(self) -> None:
        if self._doc is None or not self._editable:
            return
        positions = self.positions()
        if positions:
            max_y = max(p[1] for p in positions.values())
            min_x = min(p[0] for p in positions.values())
            cell = (min_x, max_y + 1)
        else:
            cell = (0, 0)
        self._on_create(cell)

    def _add_child(self, parent_id: str) -> None:
        positions = self.positions()
        base = positions.get(parent_id, (0, 0))
        taken = set(positions.values())
        cell = (base[0], base[1] + 1)
        while cell in taken:
            cell = (cell[0] + 1, cell[1])

        prefix = (self._ref.country or self._ref.id or "my").split("_")[0]
        ids = set(self.known_ids())
        n = 1
        while f"{prefix}_focus_{n}" in ids:
            n += 1
        parent = self._doc.find(parent_id)
        kind = parent.kind if parent is not None else "focus"

        def submit(fid: str) -> None:
            focus = self.service.add_focus(
                self._doc, kind, fid=fid,
                x=cell[0] - base[0], y=cell[1] - base[1],
                relative_to=parent_id, prerequisite=parent_id)
            self.mark_dirty()
            self.refresh_canvas()
            self._validate()
            self.canvas.set_selection([fid], notify=True)

        TextPromptDialog(self.canvas, self, self.t("focuses.add_child"),
                         self.t("focuses.focus_id"), submit,
                         initial=f"{prefix}_focus_{n}", taken=ids)

    def _on_link(self, source: str, target: str, kind: str) -> None:
        if self._doc is None or not self._editable:
            return
        focus = self._doc.find(source)
        if focus is None:
            return
        if kind in ("prerequisite", "prerequisite_or"):
            groups = focus.prerequisites
            if any(target in g for g in groups):
                return
            if kind == "prerequisite_or" and self._or_session is not None \
                    and self._or_session[0] == source \
                    and self._or_session[1] < len(groups):
                groups[self._or_session[1]].append(target)   # extend the OR group
            else:
                groups.append([target])                       # new AND group
                if kind == "prerequisite_or":
                    self._or_session = (source, len(groups) - 1)
            focus.prerequisites = groups
        else:
            if target not in focus.mutually_exclusive:
                focus.mutually_exclusive = focus.mutually_exclusive + [target]
            other = self._doc.find(target)
            if other is not None and source not in other.mutually_exclusive:
                other.mutually_exclusive = other.mutually_exclusive + [source]
        self.mark_dirty()
        self.refresh_canvas()
        self._validate()
        if self.inspector.focus_obj is not None:
            self.inspector.show(self.inspector.focus_obj, self._editable)

    def _on_link_end(self) -> None:
        """Link mode left (modifier released / Esc / click-away): close the OR session."""
        self._or_session = None

    # ---------------------------------------------------------------- context menu
    def _on_context(self, event, fid: str | None, cell: tuple[int, int]) -> None:
        if self._doc is None:
            return
        if self._menu is not None:
            self._menu.destroy()
        menu = tk.Menu(self.canvas, tearoff=0, bg=self.palette.surface,
                       fg=self.palette.text, activebackground=self.palette.accent,
                       activeforeground=self.palette.accent_text, bd=0)
        self._menu = menu
        t = self.t
        focus = self._doc.find(fid) if fid else None

        if fid is None:
            if self._editable:
                menu.add_command(label="➕ " + t("focuses.add_focus_here"),
                                 command=lambda: self._on_create(cell))
                self._add_template_menu(menu, cell)
                if self._clipboard is not None:
                    menu.add_command(label="📋 " + t("focuses.paste"),
                                     command=lambda: self._paste(cell))
        else:
            menu.add_command(label="🔍 " + t("focuses.center_here"),
                             command=lambda: self.canvas.center_on(fid))
            menu.add_command(label="📄 " + t("focuses.copy"),
                             command=lambda: self._copy_selection())
            if focus is not None and self._editable:
                menu.add_separator()
                menu.add_command(label="➕ " + t("focuses.add_child"),
                                 command=lambda: self._add_child(fid))
                menu.add_command(
                    label="⭡ " + t("focuses.link_prereq"),
                    command=lambda: self.canvas.start_link_mode(fid, "prerequisite"))
                menu.add_command(
                    label="⇄ " + t("focuses.link_mutex"),
                    command=lambda: self.canvas.start_link_mode(fid, "mutually_exclusive"))
                self._add_unlink_menu(menu, focus)
                menu.add_separator()
                menu.add_command(label="⧉ " + t("focuses.duplicate"),
                                 command=lambda: self._duplicate(focus))
                if self._clipboard is not None:
                    menu.add_command(label="📋 " + t("focuses.paste"),
                                     command=lambda: self._paste(cell))
                menu.add_command(label="🗑 " + t("focuses.inspector.delete"),
                                 command=lambda: self.delete_focus(focus))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
            self.canvas.clear_preview()     # drop any template hover phantoms

    # ------------------------------------------------------------- templates
    def _menu_colors(self) -> dict:
        return dict(tearoff=0, bg=self.palette.surface, fg=self.palette.text,
                    activebackground=self.palette.accent,
                    activeforeground=self.palette.accent_text, bd=0)

    def _add_template_menu(self, menu: tk.Menu, cell: tuple[int, int]) -> None:
        """`+ Add template here` → category → template submenus. Hovering a template
        paints translucent phantom focuses on the canvas; clicking inserts it."""
        templates = load_templates()
        if not templates:
            return
        root = tk.Menu(menu, **self._menu_colors())
        for cat_key, tpls in templates.items():
            cat_menu = tk.Menu(root, **self._menu_colors())
            index_map: dict[int, list] = {}
            for i, (tpl_key, placements) in enumerate(tpls.items()):
                label = self.t(f"focuses.template.{cat_key}.{tpl_key}")
                cat_menu.add_command(
                    label=label,
                    command=lambda p=placements: self._insert_template(cell, p))
                index_map[i] = placements
            cat_menu.bind(
                "<<MenuSelect>>",
                lambda e, m=cat_menu, im=index_map, c=cell:
                self._preview_template(m, im, c))
            root.add_cascade(label=self.t(f"focuses.template_cat.{cat_key}"),
                             menu=cat_menu)
        menu.add_cascade(label="🧩 " + self.t("focuses.add_template_here"), menu=root)

    def _preview_template(self, cat_menu: tk.Menu, index_map: dict,
                          cell: tuple[int, int]) -> None:
        try:
            active = cat_menu.index("active")
        except tk.TclError:
            active = None
        placements = index_map.get(active) if active is not None else None
        if not placements:
            self.canvas.clear_preview()
            return
        self.canvas.preview_cells(template_cells(placements, cell), anchor=cell)

    def _insert_template(self, cell: tuple[int, int], placements: list) -> None:
        if self._doc is None or not self._editable or not placements:
            return
        doc = self._doc
        kind = ("focus" if doc.tree is not None
                else ("joint_focus" if doc.joint and not doc.shared
                      else "shared_focus"))
        # generate a unique id per placement up front (prereqs reference them)
        prefix = (self._ref.country or self._ref.id or "my").split("_")[0]
        taken = set(self.known_ids())
        id_map: dict[int, str] = {}
        n = 1
        for p in placements:
            while f"{prefix}_focus_{n}" in taken:
                n += 1
            fid = f"{prefix}_focus_{n}"
            taken.add(fid)
            id_map[p["id"]] = fid
            n += 1
        anchor = next((p for p in placements if p.get("id") == 0), placements[0])
        ox, oy = anchor.get("x", 0), anchor.get("y", 0)
        created: dict[int, Focus] = {}
        for p in placements:
            fx = cell[0] + p.get("x", 0) - ox
            fy = cell[1] + p.get("y", 0) - oy
            created[p["id"]] = self.service.add_focus(
                doc, kind, fid=id_map[p["id"]], x=fx, y=fy)
        # wire prerequisites and mutual exclusivity once every focus exists
        for p in placements:
            focus = created[p["id"]]
            groups = prereq_groups(p.get("prerequisites") or {}, id_map)
            if groups:
                focus.prerequisites = groups
            excl = [id_map[e] for e in p.get("exclusive", ()) if e in id_map]
            if excl:
                focus.mutually_exclusive = excl
        self.canvas.clear_preview()
        self.mark_dirty()
        self.refresh_canvas()
        self._validate()
        self.canvas.set_selection([id_map[anchor["id"]]], notify=True)

    def _add_unlink_menu(self, menu: tk.Menu, focus: Focus) -> None:
        links: list[tuple[str, str]] = []
        for group in focus.prerequisites:
            for parent in group:
                links.append(("prerequisite", parent))
        for other in focus.mutually_exclusive:
            links.append(("mutually_exclusive", other))
        if not links:
            return
        sub = tk.Menu(menu, tearoff=0, bg=self.palette.surface, fg=self.palette.text,
                      activebackground=self.palette.accent,
                      activeforeground=self.palette.accent_text, bd=0)
        for kind, other in links:
            label = ("⭡ " if kind == "prerequisite" else "⇄ ") + other
            sub.add_command(label=label,
                            command=lambda k=kind, o=other: self._unlink(focus, k, o))
        menu.add_cascade(label="✂ " + self.t("focuses.remove_link"), menu=sub)

    def _unlink(self, focus: Focus, kind: str, other: str) -> None:
        if kind == "prerequisite":
            groups = [[f for f in g if f != other] for g in focus.prerequisites]
            focus.prerequisites = [g for g in groups if g]
        else:
            focus.mutually_exclusive = [f for f in focus.mutually_exclusive if f != other]
            twin = self._doc.find(other)
            if twin is not None and focus.id in twin.mutually_exclusive:
                twin.mutually_exclusive = [f for f in twin.mutually_exclusive
                                           if f != focus.id]
        self.mark_dirty()
        self.refresh_canvas()
        self._validate()
        if self.inspector.focus_obj is not None:
            self.inspector.show(self.inspector.focus_obj, self._editable)

    # ------------------------------------------------------------ clipboard / dup
    def _copy_selection(self) -> None:
        if self._doc is None or not self.canvas.selection:
            return
        positions = self.positions()
        blocks: list = []
        pos_map: dict[str, tuple[int, int]] = {}
        for fid in self.canvas.selection:
            focus = self._doc.find(fid)
            if focus is None:
                focus = next((f for _r, f in self._attached if f.id == fid), None)
            if focus is None:
                continue
            blocks.append(Pair(focus.kind, focus.block))
            pos_map[fid] = positions.get(fid, (0, 0))
        if blocks:
            self._clipboard = (dumps(Block(blocks)), pos_map)

    def _paste(self, cell: tuple[int, int]) -> None:
        if self._doc is None or not self._editable or self._clipboard is None:
            return
        text, pos_map = self._clipboard
        try:
            root = pdx_parse(text)
        except Exception:
            return
        base = (min(p[0] for p in pos_map.values()),
                min(p[1] for p in pos_map.values())) if pos_map else (0, 0)
        taken = set(self.known_ids())
        renames: dict[str, str] = {}
        pasted: list[Focus] = []
        for pair in root.pairs():
            if not isinstance(pair.value, Block):
                continue
            kind = pair.key if pair.key in ("focus", "shared_focus", "joint_focus") else "focus"
            focus = Focus(pair.value, kind)
            old_id = focus.id
            new_id = old_id
            while new_id in taken:
                new_id += "_copy"
            renames[old_id] = new_id
            focus.id = new_id
            taken.add(new_id)
            old_pos = pos_map.get(old_id, base)
            focus.relative_position_id = None
            focus.x = cell[0] + (old_pos[0] - base[0])
            focus.y = cell[1] + (old_pos[1] - base[1])
            if kind == "focus" and self._doc.tree is not None:
                self._doc.tree.block.add("focus", focus.block)
                self._doc.focuses.append(focus)
            elif kind == "joint_focus":
                self._doc.root.add("joint_focus", focus.block)
                self._doc.joint.append(focus)
            else:
                focus.kind = "shared_focus"
                self._doc.root.add("shared_focus", focus.block)
                self._doc.shared.append(focus)
            pasted.append(focus)
        known = set(self.known_ids())
        for focus in pasted:
            groups = [[renames.get(f, f) for f in g] for g in focus.prerequisites]
            groups = [[f for f in g if f in known] for g in groups]
            focus.prerequisites = [g for g in groups if g]
            mutex = [renames.get(f, f) for f in focus.mutually_exclusive]
            focus.mutually_exclusive = [f for f in mutex if f in known]
        self.mark_dirty()
        self.refresh_canvas()
        self._validate()
        self.canvas.set_selection([f.id for f in pasted], notify=True)

    def _duplicate(self, focus: Focus) -> None:
        taken = set(self.known_ids())
        new_id = focus.id + "_copy"
        while new_id in taken:
            new_id += "_copy"
        dup = self.service.duplicate_focus(self._doc, focus, new_id)
        self.mark_dirty()
        self.refresh_canvas()
        self._validate()
        self.canvas.set_selection([dup.id], notify=True)

    # ------------------------------------------------------------------ actions
    def rename_focus(self, focus: Focus, new_id: str) -> None:
        try:
            old = self.service.rename_focus(self._doc, focus, new_id)
        except ValueError as exc:
            messagebox.showerror("ANKA", str(exc))
            return
        self.service.rename_focus_loc(old, new_id)
        self.mark_dirty()
        self.refresh_canvas()
        self._validate()
        self.inspector.show(focus, self._editable)
        self.canvas.set_selection([new_id])

    def delete_focus(self, focus: Focus | None) -> None:
        if focus is None or self._doc is None or not self._editable:
            return
        if not messagebox.askyesno("ANKA", self.t("focuses.confirm_delete",
                                                  name=focus.id)):
            return
        self.service.remove_focus(self._doc, focus)
        self.mark_dirty()
        self.inspector.show(None)
        self.refresh_canvas()
        self._validate()

    def _delete_selected(self, fids: list[str]) -> None:
        """Delete-key handler: remove every selected document focus (one confirm)."""
        if self._doc is None or not self._editable:
            return
        focuses = [f for fid in fids if (f := self._doc.find(fid)) is not None]
        if not focuses:
            return
        if len(focuses) == 1:
            self.delete_focus(focuses[0])
            return
        if not messagebox.askyesno("ANKA", self.t(
                "focuses.confirm_delete_many", count=len(focuses))):
            return
        for focus in focuses:
            self.service.remove_focus(self._doc, focus)
        self.mark_dirty()
        self.inspector.show(None)
        self.refresh_canvas()
        self._validate()

    def set_mutex(self, focus: Focus, values: list[str]) -> None:
        before = set(focus.mutually_exclusive)
        after = set(values)
        focus.mutually_exclusive = values
        for added in after - before:
            other = self._doc.find(added)
            if other is not None and focus.id not in other.mutually_exclusive:
                other.mutually_exclusive = other.mutually_exclusive + [focus.id]
        for removed in before - after:
            other = self._doc.find(removed)
            if other is not None and focus.id in other.mutually_exclusive:
                other.mutually_exclusive = [f for f in other.mutually_exclusive
                                            if f != focus.id]
        self.mark_dirty()
        self.refresh_canvas()

    def import_icon(self, path, focus: Focus, keep_size: bool = False) -> str | None:
        """Convert a dropped image into GFX_focus_<id> (DDS + sprite registration).
        `keep_size` writes the image at its original dimensions (no resize)."""
        try:
            dds, _gfx = self.context.icons.add_focus_icon(path, focus.id,
                                                          resize=not keep_size)
        except Exception as exc:
            messagebox.showerror("ANKA", self.t("focuses.err.icon", error=str(exc)))
            return None
        sprite = f"GFX_focus_{focus.id}"
        self.resolver.add(sprite, dds)
        self.canvas.invalidate_icon(sprite)
        return sprite

    # owner protocol for the shared script editor (editors/common) -------------
    def loc_get(self, key: str, language: str) -> str:
        value = self.service.focus_name(key, language)
        return "" if value == key else value

    def loc_set(self, key: str, language: str, text: str) -> None:
        self.service.set_focus_loc(key, language, text, None)

    def value_options(self, vtype: str) -> list[tuple[str, str]]:
        """(display, value) choices for typed script fields; cached per session.
        Displays include both id and human name, so search finds either."""
        if vtype == "event":
            # never cached: events are created/renamed during the session
            from ...services.event_service import EventService
            if getattr(self, "_event_service", None) is None:
                self._event_service = EventService(self.context)
            return self._event_service.event_options()
        cached = self._value_options.get(vtype)
        if cached is not None:
            return cached
        opts: list[tuple[str, str]] = []
        if vtype == "country":
            from ...services.country_service import CountryService
            for ref in CountryService(self.context).list_tags(include_vanilla=True):
                opts.append((f"{ref.tag} · {ref.name}", ref.tag))
        elif vtype == "state":
            from ...services.state_service import StateService
            for st in StateService(self.context).list_states():
                opts.append((f"{st.id} · {st.name}", str(st.id)))
        elif vtype == "idea":
            from ...services.idea_service import IdeaService
            for idea in IdeaService(self.context).list_ideas():
                opts.append((f"{idea.id} · {idea.category}", idea.id))
        elif vtype == "focus":
            for fid in self.service.focus_ids():
                opts.append((fid, fid))
        elif vtype == "modifier":
            from ..effects import ScriptCatalog
            for name, mod in sorted(ScriptCatalog.modifiers().items()):
                scopes = ", ".join(mod.scopes)
                opts.append((f"{name} · {scopes}" if scopes else name, name))
        self._value_options[vtype] = opts
        return opts

    # ---------------------------------------------------------------- validation
    def _validate(self) -> None:
        if self._doc is None:
            return
        sprite_exists = ((lambda s: self.resolver.resolve(s) is not None)
                         if self.resolver_ready() else None)
        issues = self.service.validate(
            self._doc, self._attached, language=self.loc_language,
            sprite_exists=sprite_exists)
        self._issues_by_focus = {}
        self._problems.delete(*self._problems.get_children())
        errors = 0
        for i, issue in enumerate(sorted(issues, key=lambda i: i.severity)):
            if issue.severity == "error":
                errors += 1
            msg = self.t(f"focuses.issue.{issue.code}", detail=issue.detail)
            self._issues_by_focus.setdefault(issue.focus_id, msg)
            self._problems.insert("", "end", iid=str(i),
                                  values=("⛔" if issue.severity == "error" else "⚠",
                                          issue.focus_id, msg),
                                  tags=(issue.severity,))
        label = f"⚠ {len(issues)}" if not errors else f"⛔ {errors} · ⚠ {len(issues) - errors}"
        self._btn_problems.configure(text=label)

    def _jump_to_problem(self, _event=None) -> None:
        sel = self._problems.selection()
        if not sel:
            return
        fid = self._problems.item(sel[0], "values")[1]
        if fid:
            self.canvas.center_on(fid)
            self.canvas.set_selection([fid], notify=True)

    # ------------------------------------------------------------------ saving
    def mark_dirty(self) -> None:
        self._dirty = True
        # Record a history step: snapshot the doc and, if it changed since the last
        # recorded state, push the (before → after) transition. Skipped while an
        # undo/redo is restoring, and when the change touched no document content
        # (e.g. a localisation edit, which lives in separate .yml files).
        if self._restoring or self._doc is None or self._hist_baseline is None:
            return
        after = dumps(self._doc.root)
        if after != self._hist_baseline:
            self._history.record(_SnapshotCommand(self._hist_baseline, after))
            self._hist_baseline = after
            self._update_history_buttons()

    # ---------------------------------------------------------------- undo / redo
    def undo(self) -> None:
        self.inspector.flush_pending()          # land debounced edits first
        if self._history.can_undo():
            self._history.undo(self)
            self._update_history_buttons()

    def redo(self) -> None:
        self.inspector.flush_pending()
        if self._history.can_redo():
            self._history.redo(self)
            self._update_history_buttons()

    def _restore_snapshot(self, text: str) -> None:
        if self._ref is None:
            return
        self._restoring = True
        try:
            self._doc = self.service.document_from_text(self._ref, text)
            self._attached = (self.service.attached_shared(self._doc)
                              if self._doc.tree else [])
            self._hist_baseline = text
            self._dirty = True                  # differs from disk until saved
            self.inspector.show(None)
            self.refresh_canvas()
            self._validate()
        finally:
            self._restoring = False

    def _update_history_buttons(self) -> None:
        self._undo_btn.configure(
            state="normal" if self._history.can_undo() else "disabled")
        self._redo_btn.configure(
            state="normal" if self._history.can_redo() else "disabled")

    def _save(self) -> None:
        if self._doc is None or not self._dirty or not self._editable:
            return
        try:
            self.service.save(self._doc)
            self._dirty = False
        except Exception as exc:
            messagebox.showerror("ANKA", self.t("focuses.err.save", error=str(exc)))

    def on_leave(self) -> None:
        self.inspector.flush_pending()   # typed-but-not-yet-committed edits
        self._save()
