"""Technologies editor.

Layout: collapsible folder list (left) · toolbar + canvas + collapsible problems
panel (center) · inspector (right). The editor orchestrates `TechnologyService`
(model/IO over ``common/technologies`` + ``technology_tags``), `TechGuiService`
(per-folder pixel layout from the interface ``.gui`` files) and `TechCanvas`.

Unlike the focuses editor a folder view spans *many* documents (vanilla splits
folders across files and paths cross file boundaries), so the editor tracks a set
of open documents: techs from vanilla files render read-only (copy-to-mod
override per file), techs from mod files are editable in place. Undo snapshots
every document a command touched.
"""
from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from ...core.pdx import dumps
from ...services.interface_service import InterfaceService
from ...services.tech_gui_service import FolderView, TechGuiService
from ...services.technology_service import (
    TechDocRef,
    TechDocument,
    Technology,
    TechnologyService,
)
from ..base import EditorModule, EditorRegistry
from ..common import TextPromptDialog
from ..common.commands import Command, CommandStack
from .canvas import (
    LINK_DEP,
    LINK_PATH,
    LINK_PATH_IGNORED,
    LINK_XOR,
    ZOOM_STEPS,
    CanvasModel,
    CanvasNode,
    TechCanvas,
)

ANKA_TECH_FILE = "anka_technologies.txt"


class _SnapshotCommand(Command):
    """Undo/redo one mutation by swapping the serialized text of every document
    the command touched (techs and their references may span several files)."""

    label_key = "technologies.cmd.edit"

    def __init__(self, entries: list[tuple[TechDocRef, str, str]]):
        self.entries = entries               # (ref, before, after)

    def undo(self, editor) -> None:
        editor._restore_snapshot([(r, b) for r, b, _a in self.entries])

    def redo(self, editor) -> None:
        editor._restore_snapshot([(r, a) for r, _b, a in self.entries])


@EditorRegistry.register
class TechnologiesEditor(EditorModule):
    id = "technologies"
    name_key = "editors.technologies.name"
    desc_key = "editors.technologies.desc"
    order = 95

    def __init__(self, context, services):
        super().__init__(context, services)
        self.service = TechnologyService(context)
        self.interface = InterfaceService(context)
        self.gui = TechGuiService(context, self.interface, self.service)
        self.loc_language = {"ru": "russian"}.get(services.settings.current.language,
                                                  "english")
        self._folder_id: str | None = None
        self._folder_view: FolderView | None = None
        self._techs: list[tuple[TechDocRef, Technology]] = []
        self._docs: dict[Path, tuple[TechDocRef, TechDocument]] = {}
        self._dirty: set[Path] = set()
        self._issues_by_tech: dict[str, str] = {}
        self._menu: tk.Menu | None = None
        self._target_ref: TechDocRef | None = None      # where new techs go
        # Undo/redo — multi-document snapshot history.
        self._history = CommandStack(limit=60)
        self._baselines: dict[Path, str] = {}
        self._restoring = False
        self._resolver_ready = threading.Event()
        threading.Thread(target=self._warm_resolver, daemon=True).start()

    def _warm_resolver(self) -> None:
        try:
            self.interface.resolver().resolve("")
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
        self._build_folder_panel(root)

        center = ttk.Frame(root, style="TFrame")
        center.grid(row=1, column=1, sticky="nsew")
        center.rowconfigure(0, weight=1)
        center.columnconfigure(0, weight=1)
        self.canvas = TechCanvas(
            center, self.palette,
            resolve_icon=self.interface.resolver().resolve,
            on_select=self._on_select,
            on_move=self._on_move,
            on_create=self._on_create,
            on_link=self._on_link,
            on_context=self._on_context,
            on_delete=self._delete_selected,
            on_link_end=lambda: None,
            on_gridbox_move=self._on_gridbox_move,
            link_hints={
                LINK_PATH: self.t("technologies.link_status.path"),
                LINK_DEP: self.t("technologies.link_status.dep"),
                LINK_XOR: self.t("technologies.link_status.xor"),
                "single": self.t("technologies.link_status.path"),
            },
            icons_ready=self.resolver_ready,
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        for widget in (self.canvas.canvas, self._folders_list):
            widget.bind("<Control-z>", lambda e: (self.undo(), "break")[1])
            widget.bind("<Control-y>", lambda e: (self.redo(), "break")[1])
            widget.bind("<Control-Shift-Z>", lambda e: (self.redo(), "break")[1])
        self._build_problems(center)

        side = ttk.Frame(root, style="TFrame")
        side.grid(row=1, column=2, sticky="nsew", padx=(10, 0))
        side.rowconfigure(0, weight=1)
        self._inspector_panel = side
        from .inspector import TechInspector
        self.inspector = TechInspector(side, self)
        self.inspector.grid(row=0, column=0, sticky="nsew")
        side.columnconfigure(0, minsize=340)

        self._reload_folders()
        return root

    def _build_toolbar(self, root) -> None:
        bar = ttk.Frame(root, style="TFrame")
        bar.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))

        self._btn_folders = ttk.Button(bar, text="☰ " + self.t("technologies.panel.folders"),
                                       command=self._toggle_folders)
        self._btn_folders.pack(side="left")
        self._folder_label = ttk.Label(bar, text="—", style="Muted.TLabel")
        self._folder_label.pack(side="left", padx=12)

        ttk.Button(bar, text="➕ " + self.t("technologies.add_tech"),
                   command=self._add_tech_toolbar).pack(side="left", padx=2)
        ttk.Button(bar, text="🏷 " + self.t("technologies.tags.title"),
                   command=self._open_tags).pack(side="left", padx=2)
        from ...services.doctrine_service import DoctrineService
        if DoctrineService(self.context).exists():
            ttk.Button(bar, text="🎖 " + self.t("technologies.doctrines.title"),
                       command=self._open_doctrines).pack(side="left", padx=2)
        self._undo_btn = ttk.Button(bar, text="↶", width=3, command=self.undo,
                                    state="disabled")
        self._undo_btn.pack(side="left", padx=(6, 1))
        self._redo_btn = ttk.Button(bar, text="↷", width=3, command=self.redo,
                                    state="disabled")
        self._redo_btn.pack(side="left", padx=1)
        ttk.Button(bar, text="💾 " + self.t("common.save"),
                   command=self._save).pack(side="left", padx=2)

        self._btn_inspector = ttk.Button(
            bar, text=self.t("technologies.panel.inspector") + " ▸",
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

        self._layout_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text=self.t("technologies.layout_mode"),
                        variable=self._layout_var,
                        command=self._toggle_layout_mode).pack(side="right", padx=10)
        self._boxes_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text=self.t("technologies.gridboxes"),
                        variable=self._boxes_var,
                        command=lambda: self.canvas.set_boxes(self._boxes_var.get())
                        ).pack(side="right", padx=(10, 0))

    def _build_folder_panel(self, root) -> None:
        panel = ttk.Frame(root, style="Card.TFrame", padding=10)
        panel.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        panel.rowconfigure(1, weight=1)
        panel.columnconfigure(0, weight=1)
        self._folders_panel = panel

        self._search = tk.StringVar()
        self._search.trace_add("write", lambda *_: self._refresh_folders())
        ttk.Entry(panel, textvariable=self._search).grid(
            row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self._folders_list = ttk.Treeview(
            panel, columns=("ledger", "count"), show="tree headings",
            selectmode="browse")
        self._folders_list.heading("#0", text=self.t("technologies.col.folder"))
        self._folders_list.heading("ledger", text=self.t("technologies.col.ledger"))
        self._folders_list.heading("count", text="#")
        self._folders_list.column("#0", width=170)
        self._folders_list.column("ledger", width=62, anchor="center")
        self._folders_list.column("count", width=42, anchor="e")
        self._folders_list.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(panel, orient="vertical", command=self._folders_list.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self._folders_list.configure(yscrollcommand=sb.set)
        self._folders_list.tag_configure("nogui", foreground=self.palette.danger)
        self._folders_list.tag_configure("doctrine", foreground="#b08a3e")
        self._folders_list.bind("<<TreeviewSelect>>", self._on_pick_folder)
        self._folders_list.bind("<Button-3>", self._folder_context)

        btns = ttk.Frame(panel, style="Card.TFrame")
        btns.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        btns.columnconfigure(0, weight=1)
        self._new_folder_btn = ttk.Button(
            btns, text="➕ " + self.t("technologies.new_folder"),
            style="Accent.TButton", command=self._new_folder)
        self._new_folder_btn.grid(row=0, column=0, sticky="ew")

    def _build_problems(self, center) -> None:
        panel = ttk.Frame(center, style="Card.TFrame", padding=(8, 6))
        panel.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        panel.columnconfigure(0, weight=1)
        self._problems_panel = panel
        self._problems = ttk.Treeview(panel, columns=("sev", "tech", "msg"),
                                      show="headings", height=5)
        self._problems.heading("sev", text="!")
        self._problems.heading("tech", text=self.t("technologies.col.tech"))
        self._problems.heading("msg", text=self.t("technologies.col.problem"))
        self._problems.column("sev", width=30, anchor="center", stretch=False)
        self._problems.column("tech", width=230, stretch=False)
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
    def _toggle_folders(self) -> None:
        self._folders_visible = not getattr(self, "_folders_visible", True)
        if self._folders_visible:
            self._folders_panel.grid()
        else:
            self._folders_panel.grid_remove()

    def _toggle_inspector(self) -> None:
        self._inspector_visible = not getattr(self, "_inspector_visible", True)
        if self._inspector_visible:
            self._inspector_panel.grid()
            self._btn_inspector.configure(
                text=self.t("technologies.panel.inspector") + " ▸")
        else:
            self._inspector_panel.grid_remove()
            self._btn_inspector.configure(
                text="◂ " + self.t("technologies.panel.inspector"))

    def _toggle_problems(self) -> None:
        self._problems_visible = not self._problems_visible
        if self._problems_visible:
            self._problems_panel.grid()
        else:
            self._problems_panel.grid_remove()

    def _toggle_layout_mode(self) -> None:
        self.canvas.model.layout_mode = self._layout_var.get()
        self.canvas.render()

    def _zoom(self, direction: int) -> None:
        steps = list(ZOOM_STEPS)
        idx = min(range(len(steps)), key=lambda i: abs(steps[i] - self.canvas.zoom))
        idx = max(0, min(idx + direction, len(steps) - 1))
        self.canvas.set_zoom(steps[idx])
        self._zoom_label.configure(text=f"{round(steps[idx] * 100)}%")

    # --------------------------------------------------------------- folder list
    def _reload_folders(self) -> None:
        self.gui.invalidate()
        self._views = self.gui.folder_views(refresh=True)
        self._refresh_folders()

    def _refresh_folders(self) -> None:
        query = self._search.get().strip().lower()
        self._folders_list.delete(*self._folders_list.get_children())
        counts: dict[str, int] = {}
        for ref in self.service.list_docs(include_vanilla=True):
            try:
                doc = self.service.load(ref)
            except Exception:
                continue
            for tech in doc.techs:
                for fa in tech.folders:
                    counts[fa.name] = counts.get(fa.name, 0) + 1
        for folder_id, view in self._views.items():
            if query and query not in folder_id.lower():
                continue
            name = self.service.loc_get(folder_id, self.loc_language) or folder_id
            tags = []
            if view.definition.doctrine:
                tags.append("doctrine")
            if not view.has_gui:
                tags.append("nogui")
            label = folder_id if name == folder_id else f"{folder_id}"
            self._folders_list.insert(
                "", "end", iid=folder_id, text=label,
                values=(view.definition.ledger, counts.get(folder_id, 0)),
                tags=tuple(tags))

    def _folder_context(self, event) -> None:
        row = self._folders_list.identify_row(event.y)
        if not row:
            return
        self._folders_list.selection_set(row)
        view = self._views.get(row)
        if view is None:
            return
        if self._menu is not None:
            self._menu.destroy()
        menu = tk.Menu(self._folders_list, **self._menu_colors())
        self._menu = menu
        if not view.has_gui:
            menu.add_command(
                label="🛠 " + self.t("technologies.generate_folder_gui"),
                command=lambda: self._generate_folder_gui(row))
        menu.add_command(label="🏷 " + self.t("technologies.tags.title"),
                         command=self._open_tags)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _generate_folder_gui(self, folder_id: str) -> None:
        """Give an existing folder (e.g. a vanilla dummy doctrine container) a
        working GUI skeleton cloned from a template folder."""
        view = self._views.get(folder_id)
        if view is None:
            return
        if not messagebox.askyesno("ANKA", self.t(
                "technologies.confirm_generate_gui", folder=folder_id)):
            return
        try:
            self.gui.create_folder_gui(folder_id,
                                       doctrine=view.definition.doctrine,
                                       template_folder="infantry_folder")
        except Exception as exc:
            messagebox.showerror("ANKA", self.t("technologies.err.save",
                                                error=str(exc)))
            return
        self._reload_folders()
        self._folders_list.selection_set(folder_id)
        self.open_folder(folder_id)

    def _on_pick_folder(self, _event=None) -> None:
        sel = self._folders_list.selection()
        if not sel:
            return
        if sel[0] != self._folder_id:
            self.open_folder(sel[0])

    def open_folder(self, folder_id: str) -> None:
        self._folder_id = folder_id
        self._folder_view = self._views.get(folder_id)
        label = folder_id
        if self._folder_view is not None and not self._folder_view.has_gui:
            label += "  ⚠ " + self.t("technologies.no_gui")
        self._folder_label.configure(text=label)
        self.inspector.show(None)
        self.refresh_canvas(keep_view=False)
        self._validate()

    # ---------------------------------------------------------------- model → view
    def _load_doc(self, ref: TechDocRef) -> TechDocument:
        doc = self.service.load(ref)
        self._docs[ref.path] = (ref, doc)
        self._baselines.setdefault(ref.path, dumps(doc.root))
        return doc

    def _find(self, tid: str) -> tuple[TechDocRef, TechDocument, Technology] | None:
        for ref, tech in self._techs:
            if tech.id == tid:
                doc = self._docs.get(ref.path)
                if doc is None:
                    return ref, self._load_doc(ref), tech
                return ref, doc[1], tech
        return None

    def _editable(self, ref: TechDocRef) -> bool:
        return not ref.is_vanilla

    def refresh_canvas(self, keep_view: bool = True) -> None:
        folder_id = self._folder_id
        view = self._folder_view
        if folder_id is None or view is None:
            self.canvas.set_model(CanvasModel(), keep_view=False)
            return
        self._techs = self.service.techs_in_folder(folder_id)
        for ref, _tech in self._techs:
            if ref.path not in self._docs:
                self._load_doc(ref)
        assignment = self._assignment = self.gui.assign_gridboxes(folder_id,
                                                                  self._techs)
        in_folder = {t.id for _r, t in self._techs}

        nodes: list[CanvasNode] = []
        links: list[tuple[str, str, str, str]] = []
        xor_pairs: set[frozenset[str]] = set()
        for ref, tech in self._techs:
            doc = self._docs[ref.path][1]
            fa = tech.folder_in(folder_id)
            if fa is None:
                continue
            cell = fa.cell(doc.consts)
            gb_name = assignment.by_tech.get(tech.id, "")
            gb = view.gridboxes.get(gb_name)
            if gb is not None:
                px = gb.cell_to_px(cell)
            else:
                px = (cell[0] * 70.0, cell[1] * 70.0)     # unresolved fallback
            small = (tech.get_flag("force_use_small_tech_layout")
                     or (view.small_item is not None
                         and not tech.enable_equipments
                         and not tech.enable_equipment_modules))
            nodes.append(CanvasNode(
                tid=tech.id, px=px, cell=cell, gridbox=gb_name,
                icon=f"GFX_{tech.id}_medium",
                name=self.service.tech_name(tech.id, self.loc_language, tech),
                small=small, doctrine=tech.doctrine,
                editable=self._editable(ref),
                has_issues=tech.id in self._issues_by_tech,
                sub_count=len(tech.sub_technologies)))
            for path in tech.paths:
                target = path.leads_to_tech
                if target in in_folder:
                    kind = LINK_PATH_IGNORED if path.ignore_for_layout else LINK_PATH
                    coeff = path.research_cost_coeff
                    label = (f"×{coeff:g}" if coeff is not None and coeff != 1 else "")
                    links.append((target, tech.id, kind, label))
            for dep in tech.dependencies:
                if dep in in_folder:
                    links.append((tech.id, dep, LINK_DEP, ""))
            for other in tech.xor:
                if other in in_folder:
                    xor_pairs.add(frozenset((tech.id, other)))
        for pair in xor_pairs:
            if len(pair) == 2:
                a, b = sorted(pair)
                links.append((a, b, LINK_XOR, ""))

        from ...services.tech_gui_service import TextLabel
        selection = list(self.canvas.selection)
        model = CanvasModel(
            nodes=nodes, links=links, gridboxes=dict(view.gridboxes),
            labels=[TextLabel(name=lb.name, pos=lb.pos,
                              text=self._label_text(lb.text), font=lb.font)
                    for lb in view.labels],
            item=view.item, small_item=view.small_item,
            layout_mode=self._layout_var.get())
        self.canvas.set_model(model, keep_view=keep_view)
        self.canvas.set_selection(selection)

    def _label_text(self, key: str) -> str:
        value = self.service.loc_get(key, self.loc_language)
        return value if value is not None else key

    # -------------------------------------------------------------- canvas events
    def _on_select(self, tids: list[str]) -> None:
        if not tids:
            self.inspector.show(None)
            return
        found = self._find(tids[0])
        if found is None:
            self.inspector.show(None)
            return
        ref, doc, tech = found
        self.inspector.show(tech, doc=doc, editable=self._editable(ref))

    def _on_move(self, tids: list[str], delta: tuple[int, int]) -> None:
        if self._folder_id is None:
            return
        touched: list[TechDocument] = []
        for tid in tids:
            found = self._find(tid)
            if found is None:
                continue
            ref, doc, tech = found
            if not self._editable(ref):
                continue
            fa = tech.folder_in(self._folder_id)
            if fa is None:
                continue
            x, y = fa.cell(doc.consts)
            fa.set_cell(x + delta[0], y + delta[1], doc)
            touched.append(doc)
        if touched:
            self.mark_dirty(touched)
            self.refresh_canvas()
            self._validate()
            self.inspector.refresh()

    def _on_gridbox_move(self, name: str, delta: tuple[float, float]) -> None:
        view = self._folder_view
        if view is None or name not in view.gridboxes:
            return
        gb = view.gridboxes[name]
        gui_ref = view.gui_ref
        if gui_ref is None or gui_ref.is_vanilla:
            if not self._offer_gui_override():
                return
            view = self._folder_view
            gb = view.gridboxes.get(name)
            if gb is None:
                return
        x, y = gb.node.get_position()
        gb.node.set_position(round(x + delta[0]), round(y + delta[1]))
        gb.origin = (gb.origin[0] + round(delta[0]), gb.origin[1] + round(delta[1]))
        self._save_gui()
        self.refresh_canvas()

    def _offer_gui_override(self) -> bool:
        """The folder's .gui is vanilla: offer copying it into the mod."""
        view = self._folder_view
        if view is None or view.gui_ref is None:
            return False
        if not messagebox.askyesno("ANKA", self.t("technologies.confirm_gui_copy",
                                                  file=view.gui_ref.rel_file)):
            return False
        self.interface.copy_to_mod(view.gui_ref)
        self._reload_folders()
        if self._folder_id:
            self._folder_view = self._views.get(self._folder_id)
        return True

    def _save_gui(self) -> None:
        view = self._folder_view
        if view is None or view.gui_ref is None or view.gui_ref.is_vanilla:
            return
        try:
            self.interface.save(self.interface.load_gui(view.gui_ref))
        except Exception as exc:
            messagebox.showerror("ANKA", self.t("technologies.err.save", error=str(exc)))

    # ------------------------------------------------------------------ creation
    def _target_doc(self) -> TechDocument | None:
        """The mod document new techs go to (created on demand)."""
        refs = [r for r in self.service.list_docs(include_vanilla=False)
                if not r.is_vanilla]
        ref = None
        if self._target_ref is not None:
            ref = next((r for r in refs if r.path == self._target_ref.path), None)
        if ref is None and refs:
            ref = refs[0]
        if ref is None:
            try:
                ref = self.service.create_doc(ANKA_TECH_FILE)
            except FileExistsError:
                return None
        self._target_ref = ref
        return self._load_doc(ref)

    def _on_create(self, gridbox: str | None, cell: tuple[int, int],
                   px: tuple[float, float]) -> None:
        if self._folder_id is None:
            return
        doc = self._target_doc()
        if doc is None:
            return
        taken = set(self.service.tech_index())
        base = "my_tech"
        n = 1
        while f"{base}_{n}" in taken:
            n += 1

        def submit(tid: str) -> None:
            doctrine = bool(self._folder_view is not None
                            and self._folder_view.definition.doctrine)
            try:
                self.service.add_tech(doc, tid, folder=self._folder_id,
                                      cell=cell, doctrine=doctrine)
            except ValueError as exc:
                messagebox.showerror("ANKA", str(exc))
                return
            if gridbox is None:
                self._ensure_root_gridbox(tid, px)
            self.mark_dirty([doc])
            self.refresh_canvas()
            self._validate()
            self.canvas.set_selection([tid], notify=True)

        TextPromptDialog(self.canvas, self, self.t("technologies.add_tech"),
                         self.t("technologies.tech_id"), submit,
                         initial=f"{base}_{n}", taken=taken)

    def _ensure_root_gridbox(self, tid: str, px: tuple[float, float]) -> None:
        """A tech dropped outside every gridbox starts a new branch: it needs its
        own ``<tid>_tree`` gridbox at that pixel position (mod-side .gui only)."""
        view = self._folder_view
        if view is None or self._folder_id is None or view.container is None:
            return
        if view.gui_ref is not None and view.gui_ref.is_vanilla:
            if not self._offer_gui_override():
                return
            view = self._folder_view
            if view is None or view.container is None:
                return
        doctrine = view.definition.doctrine
        self.gui.ensure_gridbox(self._folder_id, f"{tid}_tree",
                                origin=(max(px[0], 0), max(px[1], 0)))
        try:
            self.gui.save_gui(doctrine)
        except Exception as exc:
            messagebox.showerror("ANKA", self.t("technologies.err.save",
                                                error=str(exc)))

    def _add_tech_toolbar(self) -> None:
        if self._folder_id is None or self._folder_view is None:
            return
        gridboxes = self.canvas.model.gridboxes
        name = next(iter(gridboxes), None)
        cell = (0, 0)
        if name is not None:
            cells = {n.cell for n in self.canvas.model.nodes if n.gridbox == name}
            while cell in cells:
                cell = (cell[0] + 1, cell[1])
        self._on_create(name, cell, (0.0, 0.0))

    def _add_child(self, parent_id: str) -> None:
        """Create a tech one cell after `parent_id` and wire a path to it."""
        found = self._find(parent_id)
        if found is None or self._folder_id is None:
            return
        _pref, pdoc, parent = found
        fa = parent.folder_in(self._folder_id)
        base_cell = fa.cell(pdoc.consts) if fa is not None else (0, 0)
        doc = self._target_doc()
        if doc is None:
            return
        taken = set(self.service.tech_index())
        n = 1
        while f"{parent_id}_{n}" in taken:
            n += 1

        def submit(tid: str) -> None:
            doctrine = bool(self._folder_view is not None
                            and self._folder_view.definition.doctrine)
            try:
                tech = self.service.add_tech(
                    doc, tid, folder=self._folder_id,
                    cell=(base_cell[0] + 2, base_cell[1]), doctrine=doctrine)
            except ValueError as exc:
                messagebox.showerror("ANKA", str(exc))
                return
            touched = [doc]
            parent_editable = not self._find(parent_id)[0].is_vanilla
            if parent_editable:
                parent.add_path(tid)
                if pdoc is not doc:
                    touched.append(pdoc)
            self.mark_dirty(touched)
            self.refresh_canvas()
            self._validate()
            self.canvas.set_selection([tid], notify=True)

        TextPromptDialog(self.canvas, self, self.t("technologies.add_child"),
                         self.t("technologies.tech_id"), submit,
                         initial=f"{parent_id}_{n}", taken=taken)

    # -------------------------------------------------------------------- links
    def _on_link(self, source: str, target: str, kind: str) -> None:
        found_src = self._find(source)
        if found_src is None:
            return
        ref, doc, tech = found_src
        if not self._editable(ref):
            messagebox.showinfo("ANKA", self.t("technologies.err.vanilla_tech"))
            return
        touched = [doc]
        if kind == LINK_PATH:
            if tech.path_to(target) is None:
                tech.add_path(target)
        elif kind == LINK_DEP:
            deps = tech.dependencies
            if target not in deps:
                deps[target] = 1
                tech.dependencies = deps
        elif kind == LINK_XOR:
            if target not in tech.xor:
                tech.xor = tech.xor + [target]
            found_other = self._find(target)
            if found_other is not None and self._editable(found_other[0]):
                _oref, odoc, other = found_other
                if source not in other.xor:
                    other.xor = other.xor + [source]
                if odoc is not doc:
                    touched.append(odoc)
        self.mark_dirty(touched)
        self.refresh_canvas()
        self._validate()
        self.inspector.refresh()

    def _unlink(self, tech: Technology, kind: str, other: str) -> None:
        found = self._find(tech.id)
        if found is None or not self._editable(found[0]):
            return
        doc = found[1]
        touched = [doc]
        if kind == LINK_PATH:
            tech.remove_path(other)
        elif kind == LINK_DEP:
            deps = tech.dependencies
            deps.pop(other, None)
            tech.dependencies = deps
        elif kind == LINK_XOR:
            tech.xor = [t for t in tech.xor if t != other]
            found_other = self._find(other)
            if found_other is not None and self._editable(found_other[0]):
                _oref, odoc, twin = found_other
                if tech.id in twin.xor:
                    twin.xor = [t for t in twin.xor if t != tech.id]
                if odoc is not doc:
                    touched.append(odoc)
        self.mark_dirty(touched)
        self.refresh_canvas()
        self._validate()
        self.inspector.refresh()

    # ---------------------------------------------------------------- context menu
    def _menu_colors(self) -> dict:
        return dict(tearoff=0, bg=self.palette.surface, fg=self.palette.text,
                    activebackground=self.palette.accent,
                    activeforeground=self.palette.accent_text, bd=0)

    def _on_context(self, event, tid: str | None, gridbox: str | None,
                    px: tuple[float, float]) -> None:
        if self._folder_id is None:
            return
        if self._menu is not None:
            self._menu.destroy()
        menu = tk.Menu(self.canvas, **self._menu_colors())
        self._menu = menu
        t = self.t
        found = self._find(tid) if tid else None

        if tid is None or found is None:
            cell = (0, 0)
            if gridbox and self._folder_view is not None:
                gb = self._folder_view.gridboxes.get(gridbox)
                if gb is not None:
                    cell = gb.px_to_cell(px)
            menu.add_command(label="➕ " + t("technologies.add_tech_here"),
                             command=lambda: self._on_create(gridbox, cell, px))
            self._add_template_menu(menu, gridbox, cell, px)
        else:
            ref, doc, tech = found
            editable = self._editable(ref)
            menu.add_command(label="🔍 " + t("technologies.center_here"),
                             command=lambda: self.canvas.center_on(tid))
            if ref.is_vanilla:
                menu.add_command(
                    label="⧉ " + t("technologies.copy_file_to_mod", file=ref.name),
                    command=lambda: self._copy_file_to_mod(ref))
            if editable:
                menu.add_separator()
                menu.add_command(label="➕ " + t("technologies.add_child"),
                                 command=lambda: self._add_child(tid))
                menu.add_command(
                    label="→ " + t("technologies.link_path"),
                    command=lambda: self.canvas.start_link_mode(tid, LINK_PATH))
                menu.add_command(
                    label="⇢ " + t("technologies.link_dep"),
                    command=lambda: self.canvas.start_link_mode(tid, LINK_DEP))
                menu.add_command(
                    label="⇄ " + t("technologies.link_xor"),
                    command=lambda: self.canvas.start_link_mode(tid, LINK_XOR))
                self._add_unlink_menu(menu, tech)
                menu.add_separator()
                menu.add_command(label="⧉ " + t("technologies.duplicate"),
                                 command=lambda: self._duplicate(doc, tech))
                menu.add_command(label="🗑 " + t("technologies.delete"),
                                 command=lambda: self.delete_tech(tech))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
            self.canvas.clear_preview()

    # ------------------------------------------------------------- templates
    def _template_px(self, gridbox: str | None, cells: list[tuple[int, int]],
                     base_cell: tuple[int, int],
                     base_px: tuple[float, float]) -> list[tuple[float, float]]:
        """World pixel anchors for template cells (LEFT-style fallback when the
        click landed outside every gridbox)."""
        view = self._folder_view
        gb = view.gridboxes.get(gridbox) if view is not None and gridbox else None
        out = []
        for cell in cells:
            if gb is not None:
                out.append(gb.cell_to_px(cell))
            else:
                dx, dy = cell[0] - base_cell[0], cell[1] - base_cell[1]
                out.append((base_px[0] + dy * 70.0, base_px[1] + dx * 70.0))
        return out

    def _add_template_menu(self, menu: tk.Menu, gridbox: str | None,
                           cell: tuple[int, int], px: tuple[float, float]) -> None:
        from .templates import load_templates
        templates = load_templates()
        if not templates:
            return
        root = tk.Menu(menu, **self._menu_colors())
        for cat_key, tpls in templates.items():
            cat_menu = tk.Menu(root, **self._menu_colors())
            index_map: dict[int, list] = {}
            for i, (tpl_key, placements) in enumerate(tpls.items()):
                label = self.t(f"technologies.template.{cat_key}.{tpl_key}")
                cat_menu.add_command(
                    label=label,
                    command=lambda p=placements: self._insert_template(
                        gridbox, cell, px, p))
                index_map[i] = placements
            cat_menu.bind(
                "<<MenuSelect>>",
                lambda e, m=cat_menu, im=index_map, g=gridbox, c=cell, w=px:
                self._preview_template(m, im, g, c, w))
            root.add_cascade(label=self.t(f"technologies.template_cat.{cat_key}"),
                             menu=cat_menu)
        menu.add_cascade(label="🧩 " + self.t("technologies.add_template_here"),
                         menu=root)

    def _preview_template(self, cat_menu: tk.Menu, index_map: dict,
                          gridbox: str | None, cell: tuple[int, int],
                          px: tuple[float, float]) -> None:
        from .templates import template_cells
        try:
            active = cat_menu.index("active")
        except tk.TclError:
            active = None
        placements = index_map.get(active) if active is not None else None
        if not placements:
            self.canvas.clear_preview()
            return
        cells = template_cells(placements, cell)
        points = self._template_px(gridbox, cells, cell, px)
        anchor_pt = self._template_px(gridbox, [cell], cell, px)[0]
        self.canvas.preview_px(points, anchor=anchor_pt)

    def _insert_template(self, gridbox: str | None, cell: tuple[int, int],
                         px: tuple[float, float], placements: list) -> None:
        from .templates import anchor_of, template_cells
        if self._folder_id is None or not placements:
            return
        doc = self._target_doc()
        if doc is None:
            return
        doctrine = bool(self._folder_view is not None
                        and self._folder_view.definition.doctrine)
        cells = template_cells(placements, cell)
        taken = set(self.service.tech_index())
        id_map: dict[int, str] = {}
        n = 1
        for p in placements:
            while f"my_tech_{n}" in taken:
                n += 1
            tid = f"my_tech_{n}"
            taken.add(tid)
            id_map[p["id"]] = tid
            n += 1
        anchor = anchor_of(placements)
        base_year = 1936
        created: dict[int, Technology] = {}
        for p, pcell in zip(placements, cells):
            tech = self.service.add_tech(doc, id_map[p["id"]],
                                         folder=self._folder_id, cell=pcell,
                                         doctrine=doctrine)
            if "cost" in p:
                tech.research_cost = float(p["cost"])
            tech.start_year = base_year + int(p.get("year_offset", 0))
            created[p["id"]] = tech
        for p in placements:
            tech = created[p["id"]]
            # template "paths" list this placement's PREREQUISITES; in script the
            # `path` block lives on the parent (leads_to_tech = child)
            for edge in p.get("paths") or []:
                parent = created.get(edge.get("to"))
                if parent is not None:
                    parent.add_path(tech.id, edge.get("coeff", 1))
            xor = [id_map[x] for x in p.get("xor") or [] if x in id_map]
            if xor:
                tech.xor = xor
        # the anchor starts a new branch (nothing paths into it), so it needs its
        # own gridbox even when the click landed inside an existing one
        anchor_px = px
        view = self._folder_view
        if gridbox and view is not None and gridbox in view.gridboxes:
            anchor_px = view.gridboxes[gridbox].cell_to_px(cell)
        self._ensure_root_gridbox(id_map[anchor["id"]], anchor_px)
        self.canvas.clear_preview()
        self.mark_dirty([doc])
        self.refresh_canvas()
        self._validate()
        self.canvas.set_selection([id_map[anchor["id"]]], notify=True)

    def _add_unlink_menu(self, menu: tk.Menu, tech: Technology) -> None:
        links: list[tuple[str, str]] = []
        for path in tech.paths:
            links.append((LINK_PATH, path.leads_to_tech))
        for dep in tech.dependencies:
            links.append((LINK_DEP, dep))
        for other in tech.xor:
            links.append((LINK_XOR, other))
        if not links:
            return
        sub = tk.Menu(menu, **self._menu_colors())
        icons = {LINK_PATH: "→ ", LINK_DEP: "⇢ ", LINK_XOR: "⇄ "}
        for kind, other in links:
            sub.add_command(label=icons[kind] + other,
                            command=lambda k=kind, o=other: self._unlink(tech, k, o))
        menu.add_cascade(label="✂ " + self.t("technologies.remove_link"), menu=sub)

    # ------------------------------------------------------------------ actions
    def _copy_file_to_mod(self, ref: TechDocRef) -> None:
        new_ref = self.service.copy_to_mod(ref)
        self._docs.pop(ref.path, None)
        self._baselines.pop(ref.path, None)
        self._load_doc(new_ref)
        self.refresh_canvas()
        self._validate()

    def _duplicate(self, doc: TechDocument, tech: Technology) -> None:
        taken = set(self.service.tech_index())
        new_id = tech.id + "_copy"
        while new_id in taken:
            new_id += "_copy"
        try:
            dup = self.service.duplicate_tech(doc, tech, new_id)
        except ValueError as exc:
            messagebox.showerror("ANKA", str(exc))
            return
        self.mark_dirty([doc])
        self.refresh_canvas()
        self._validate()
        self.canvas.set_selection([dup.id], notify=True)

    def rename_tech(self, tech: Technology, new_id: str) -> None:
        found = self._find(tech.id)
        if found is None:
            return
        _ref, doc, _tech = found
        try:
            old = self.service.rename_tech(doc, tech, new_id)
        except ValueError as exc:
            messagebox.showerror("ANKA", str(exc))
            return
        self.service.rename_tech_loc(old, new_id)
        # a branch root's gridbox is named after it — follow the rename when the
        # .gui is mod-side (vanilla layouts stay untouched)
        renamed_gui: set[bool] = set()
        for fa in tech.folders:
            view = self._views.get(fa.name)
            if (view is not None and view.gui_ref is not None
                    and not view.gui_ref.is_vanilla
                    and self.gui.rename_gridbox(fa.name, old, new_id)):
                renamed_gui.add(view.definition.doctrine)
        for doctrine in renamed_gui:
            try:
                self.gui.save_gui(doctrine)
            except Exception:
                pass
        touched = [doc] + [d for _r, d in
                           (self._docs[p] for p in list(self._docs))
                           if d is not doc]
        self.mark_dirty(touched)
        self.refresh_canvas()
        self._validate()
        self.inspector.refresh()
        self.canvas.set_selection([new_id])

    def delete_tech(self, tech: Technology | None) -> None:
        if tech is None:
            return
        found = self._find(tech.id)
        if found is None or not self._editable(found[0]):
            return
        if not messagebox.askyesno("ANKA", self.t("technologies.confirm_delete",
                                                  name=tech.id)):
            return
        _ref, doc, _tech = found
        others = self.service.remove_tech(doc, tech)
        self.mark_dirty([doc] + others)
        self.inspector.show(None)
        self.refresh_canvas()
        self._validate()

    def _delete_selected(self, tids: list[str]) -> None:
        techs = []
        for tid in tids:
            found = self._find(tid)
            if found is not None and self._editable(found[0]):
                techs.append((found[1], found[2]))
        if not techs:
            return
        if len(techs) == 1:
            self.delete_tech(techs[0][1])
            return
        if not messagebox.askyesno("ANKA", self.t(
                "technologies.confirm_delete_many", count=len(techs))):
            return
        touched: list[TechDocument] = []
        for doc, tech in techs:
            touched += self.service.remove_tech(doc, tech)
            if doc not in touched:
                touched.append(doc)
        self.mark_dirty(touched)
        self.inspector.show(None)
        self.refresh_canvas()
        self._validate()

    def _new_folder(self) -> None:
        from .folder_dialogs import NewFolderDialog

        def done(folder_id: str) -> None:
            self._reload_folders()
            self._folders_list.selection_set(folder_id)
            self.open_folder(folder_id)

        NewFolderDialog(self.canvas, self, done)

    def _open_tags(self) -> None:
        from .tags_panel import TagsDialog
        TagsDialog(self.canvas, self)

    def _open_doctrines(self) -> None:
        from .doctrines_tab import DoctrinesDialog
        DoctrinesDialog(self.canvas, self)

    # ---------------------------------------------------------------- validation
    def _validate(self) -> None:
        from ...services.technology_service import Issue
        issues: list[Issue] = []
        if self._techs:
            sprite_exists = ((lambda s: self.interface.resolver().resolve(s) is not None)
                             if self.resolver_ready() else None)
            issues = self.service.validate(self._techs, language=self.loc_language,
                                           sprite_exists=sprite_exists)
        # GUI-level rules from the last layout pass
        view = self._folder_view
        assignment = getattr(self, "_assignment", None)
        if view is not None and self._folder_id is not None:
            if not view.has_gui and self._techs:
                issues.append(Issue("warning", "folder_no_gui", "",
                                    self._folder_id))
            if assignment is not None:
                for tid in assignment.missing_root:
                    issues.append(Issue("error", "no_gridbox", tid,
                                        f"{tid}_tree"))
                for tid, candidates in assignment.ambiguous.items():
                    issues.append(Issue("warning", "ambiguous_gridbox", tid,
                                        ", ".join(candidates)))
            cells: dict[tuple[str, tuple[int, int]], str] = {}
            for node in self.canvas.model.nodes:
                if not node.gridbox:
                    continue
                key = (node.gridbox, node.cell)
                if key in cells:
                    issues.append(Issue("warning", "cell_collision", node.tid,
                                        cells[key]))
                else:
                    cells[key] = node.tid

        self._issues_by_tech = {}
        self._problems.delete(*self._problems.get_children())
        errors = 0
        order = {"error": 0, "warning": 1, "info": 2}
        for i, issue in enumerate(sorted(issues,
                                         key=lambda x: order.get(x.severity, 3))):
            if issue.severity == "error":
                errors += 1
            msg = self.t(f"technologies.issue.{issue.code}", detail=issue.detail)
            if issue.tech_id:
                self._issues_by_tech.setdefault(issue.tech_id, msg)
            sev = {"error": "⛔", "warning": "⚠", "info": "ℹ"}.get(issue.severity, "⚠")
            self._problems.insert("", "end", iid=str(i),
                                  values=(sev, issue.tech_id, msg),
                                  tags=(issue.severity,))
        label = (f"⚠ {len(issues)}" if not errors
                 else f"⛔ {errors} · ⚠ {len(issues) - errors}")
        self._btn_problems.configure(text=label)

    def _jump_to_problem(self, _event=None) -> None:
        sel = self._problems.selection()
        if not sel:
            return
        tid = self._problems.item(sel[0], "values")[1]
        if tid:
            self.canvas.center_on(tid)
            self.canvas.set_selection([tid], notify=True)

    # ------------------------------------------------------------------ history
    def mark_dirty(self, docs: list[TechDocument]) -> None:
        if self._restoring:
            return
        entries: list[tuple[TechDocRef, str, str]] = []
        seen: set[Path] = set()
        for doc in docs:
            path = doc.ref.path
            if path in seen:
                continue
            seen.add(path)
            before = self._baselines.get(path)
            after = dumps(doc.root)
            if before is None:
                self._baselines[path] = after
                continue
            if after != before:
                entries.append((doc.ref, before, after))
                self._baselines[path] = after
                self._dirty.add(path)
        if entries:
            self._history.record(_SnapshotCommand(entries))
            self._update_history_buttons()

    def undo(self) -> None:
        self.inspector.flush_pending()
        if self._history.can_undo():
            self._history.undo(self)
            self._update_history_buttons()

    def redo(self) -> None:
        self.inspector.flush_pending()
        if self._history.can_redo():
            self._history.redo(self)
            self._update_history_buttons()

    def _restore_snapshot(self, entries: list[tuple[TechDocRef, str]]) -> None:
        self._restoring = True
        try:
            for ref, text in entries:
                doc = self.service.document_from_text(ref, text)
                self._docs[ref.path] = (ref, doc)
                self._baselines[ref.path] = text
                self._dirty.add(ref.path)
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

    # ------------------------------------------------------------------ saving
    def _save(self) -> None:
        for path in list(self._dirty):
            entry = self._docs.get(path)
            if entry is None:
                self._dirty.discard(path)
                continue
            ref, doc = entry
            if ref.is_vanilla:
                self._dirty.discard(path)
                continue
            try:
                self.service.save(doc)
                self._dirty.discard(path)
            except Exception as exc:
                messagebox.showerror("ANKA",
                                     self.t("technologies.err.save", error=str(exc)))

    # owner protocol for shared dialogs/script editor ---------------------------
    @property
    def resolver(self):
        return self.interface.resolver()

    def loc_get(self, key: str, language: str) -> str:
        return self.service.loc_get(key, language) or ""

    def loc_set(self, key: str, language: str, text: str) -> None:
        self.service.loc_set(key, language, text)

    def import_icon(self, path, tech: Technology) -> str | None:
        """Convert an image into ``GFX_<id>_medium`` (DDS + sprite registration)."""
        try:
            dds, _gfx = self.context.icons.add_tech_icon(path, tech.id)
        except Exception as exc:
            messagebox.showerror("ANKA", self.t("technologies.err.icon",
                                                error=str(exc)))
            return None
        sprite = f"GFX_{tech.id}_medium"
        self.interface.resolver().add(sprite, dds)
        self.canvas.invalidate_icon(sprite)
        self.canvas.render()
        return sprite

    def value_options(self, vtype: str) -> list[tuple[str, str]]:
        """(display, value) choices for typed script fields; cached per session."""
        if vtype == "event":
            from ...services.event_service import EventService
            if getattr(self, "_event_service", None) is None:
                self._event_service = EventService(self.context)
            return self._event_service.event_options()
        cached = getattr(self, "_value_options_cache", None)
        if cached is None:
            cached = self._value_options_cache = {}
        if vtype in cached:
            return cached[vtype]
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
            from ...services.focus_service import FocusService
            for fid in FocusService(self.context).focus_ids():
                opts.append((fid, fid))
        elif vtype == "modifier":
            from ..effects import ScriptCatalog
            for name, mod in sorted(ScriptCatalog.modifiers().items()):
                scopes = ", ".join(mod.scopes)
                opts.append((f"{name} · {scopes}" if scopes else name, name))
        cached[vtype] = opts
        return opts

    def on_leave(self) -> None:
        self.inspector.flush_pending()
        self._save()
