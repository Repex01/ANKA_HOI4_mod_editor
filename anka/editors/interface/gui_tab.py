"""GUI designer tab: hierarchy tree · WYSIWYG canvas · widget inspector.

The tab owns the open document/window, the undo stack and the renderer;
`GuiDesignCanvas`, `HierarchyPanel`, `WidgetPalette` and `WidgetInspector`
are dumb views that call back here. Every mutation goes through a command
(recorded already executed) so Ctrl+Z/Ctrl+Y replay cleanly; the frame is
re-rendered (debounced) after each change. The simulated resolution is
switchable to check anchoring behavior; vanilla docs open read-only with
one-click copy-to-mod.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from ...config.constants import HOI4_LANGUAGES
from ...core.guirender import (FontProvider, GuiRenderer, Rect,
                               SpriteImageCache)
from ...core.pdx import dumps
from ...core.pdx import parse as pdx_parse
from ..common.commands import CompoundCommand, CommandStack
from .canvas import GuiDesignCanvas
from .commands import (TOUCH_TREE, CreateNodeCommand, DeleteNodeCommand,
                       DeleteWindowCommand, ReorderCommand, SetAttrCommand)
from .inspectors import WidgetInspector
from .palette import WidgetPalette
from .tree_panel import HierarchyPanel

IndexPath = tuple[int, ...]

RESOLUTIONS = ((1920, 1080), (1366, 768), (2560, 1440), (3840, 2160),
               (1280, 720), (1600, 900))

_DEFAULT_SIZES = {
    "containerwindowtype": (220, 160), "windowtype": (220, 160),
    "gridboxtype": (200, 120), "listboxtype": (180, 200),
    "smoothlistboxtype": (180, 200), "editboxtype": (140, 24),
    "instanttextboxtype": (140, 20), "textboxtype": (140, 20),
    "overlappingelementsboxtype": (160, 40), "browsertype": (300, 200),
}


class GuiDesignerTab(ttk.Frame):
    def __init__(self, master, editor):
        super().__init__(master, style="TFrame")
        self.editor = editor
        self.t = editor.t
        self.palette = editor.palette
        self.context = editor.context
        self.service = editor.service
        self.loc_language = editor.loc_language

        self.mod_docs: list = []
        self.vanilla_refs: list = []
        self._dirty: set[str] = set()
        self.doc = None
        self.wi = 0
        self.window = None
        self.editable = False
        self.selection: list[IndexPath] = []
        self.resolution = RESOLUTIONS[0]
        self.commands = CommandStack(60)
        self._renderer: GuiRenderer | None = None
        self._result = None
        self._render_job: str | None = None
        self._copy_ref = None

        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)
        self._build_toolbar()
        self._build_left()
        self._build_center()
        self._build_right()
        self.reload_docs()

    # ----------------------------------------------------------- owner protocol
    @property
    def resolver(self):
        return self.editor.resolver

    def resolver_ready(self) -> bool:
        return self.editor.resolver_ready()

    def loc_get(self, key: str, language: str) -> str:
        return self.editor.loc_get(key, language)

    def loc_set(self, key: str, language: str, text: str) -> None:
        self.editor.loc_set(key, language, text)

    def loc_has(self, key: str, language: str | None = None) -> bool:
        return self.editor.loc_has(key, language or self.loc_language)

    def set_loc_language(self, language: str) -> None:
        """Language switch (toolbar or inspector row): keep both in sync and
        re-render so canvas texts follow."""
        self.loc_language = language
        if self._lang_var.get() != language:
            self._lang_var.set(language)
        self.schedule_render(0)

    def _language_changed(self) -> None:
        self.inspector.flush_pending()
        self.set_loc_language(self._lang_var.get())
        self._reshow_inspector()

    def value_options(self, vtype: str) -> list[tuple[str, str]]:
        return self.editor.value_options(vtype)

    def scripted_loc_names(self) -> list[str]:
        return self.editor.scripted_loc_names()

    def suggest_sprite_name(self, source) -> str:
        return self.service.suggest_sprite_name(source)

    def import_sprite(self, source, name: str) -> str | None:
        """Picker import: image → mod DDS + sprite in anka_interface.gfx."""
        try:
            sprite = self.service.import_sprite(source, name)
        except Exception as exc:                          # noqa: BLE001
            messagebox.showerror("ANKA", str(exc))
            return None
        renderer = self._get_renderer()
        if renderer is not None:
            renderer.sprites.invalidate(sprite)
        self.schedule_render()
        return sprite

    # ------------------------------------------------------------------- build
    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, style="TFrame")
        bar.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        self._btn_list = ttk.Button(bar, text="☰ " + self.t("interface.gfx.panel"),
                                    command=self._toggle_left)
        self._btn_list.pack(side="left")
        self._undo_btn = ttk.Button(bar, text="↶", width=3, command=self.undo)
        self._undo_btn.pack(side="left", padx=(8, 0))
        self._redo_btn = ttk.Button(bar, text="↷", width=3, command=self.redo)
        self._redo_btn.pack(side="left", padx=(2, 4))
        ttk.Button(bar, text="🗎 " + self.t("interface.gui.new_file"),
                   command=self._new_gui_file).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="▣ " + self.t("interface.gui.new_window"),
                   command=self._new_window).pack(side="left", padx=2)
        ttk.Button(bar, text="💾 " + self.t("common.save"),
                   command=self.save_all).pack(side="left", padx=4)
        self._copy_btn = ttk.Button(bar, text="⧉ " + self.t("focuses.copy_to_mod"),
                                    command=self._copy_to_mod)

        ttk.Label(bar, text=self.t("interface.gui.resolution")).pack(
            side="left", padx=(16, 4))
        self._res_var = tk.StringVar(value=self._res_label(self.resolution))
        values = [self._res_label(r) for r in RESOLUTIONS] + [
            self.t("interface.gui.res_custom")]
        self._res_box = ttk.Combobox(bar, textvariable=self._res_var,
                                     values=values, width=12,
                                     state="readonly")
        self._res_box.pack(side="left")
        self._res_box.bind("<<ComboboxSelected>>", self._res_changed)

        ttk.Label(bar, text="🌐").pack(side="left", padx=(12, 2))
        self._lang_var = tk.StringVar(value=self.loc_language)
        lang_box = ttk.Combobox(bar, textvariable=self._lang_var, width=11,
                                state="readonly", values=list(HOI4_LANGUAGES))
        lang_box.pack(side="left")
        lang_box.bind("<<ComboboxSelected>>",
                      lambda _e: self._language_changed())

        self._snap_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text=self.t("interface.gui.snap"),
                        variable=self._snap_var,
                        command=self._snap_changed).pack(side="left",
                                                         padx=(12, 0))
        self._ghost_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text=self.t("interface.gui.ghosts"),
                        variable=self._ghost_var,
                        command=lambda: self.schedule_render(0)).pack(
            side="left", padx=(8, 0))
        self._zoom_lbl = ttk.Label(bar, text="100%")
        self._zoom_lbl.pack(side="left", padx=(12, 0))

        self._btn_problems = ttk.Button(bar, text="⚠ 0",
                                        command=self._toggle_problems)
        self._btn_problems.pack(side="right")

    def _res_label(self, res: tuple[int, int]) -> str:
        return f"{res[0]}×{res[1]}"

    def _build_left(self) -> None:
        left = ttk.Frame(self, style="TFrame")
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        left.rowconfigure(0, weight=3)
        left.rowconfigure(1, weight=2)
        left.columnconfigure(0, weight=1)
        self._left = left
        self._left_visible = True
        self.columnconfigure(0, minsize=330)
        self.tree = HierarchyPanel(left, self, on_select=self._tree_selected,
                                   on_context=self._tree_context,
                                   on_multi_select=self._tree_multi_selected)
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.palette_panel = WidgetPalette(left, self)
        self.palette_panel.grid(row=1, column=0, sticky="nsew", pady=(8, 0))

    def _build_center(self) -> None:
        center = ttk.Frame(self, style="TFrame")
        center.grid(row=1, column=1, sticky="nsew")
        center.rowconfigure(0, weight=1)
        center.columnconfigure(0, weight=1)
        self.canvas = GuiDesignCanvas(
            center, self.palette,
            on_select=self._canvas_selected,
            on_move=self.move_nodes,
            on_resize=self.resize_node,
            on_create=self.create_node,
            on_reparent=self.reparent_nodes,
            on_context=self._canvas_context,
            on_delete=self.delete_nodes,
            on_zoom=lambda z: self._zoom_lbl.configure(text=f"{int(z * 100)}%"))
        self.canvas.grid(row=0, column=0, sticky="nsew")
        from ...ui.hotkeys import bind_ctrl
        for widget in (self.canvas.canvas, self.tree._tree):
            bind_ctrl(widget, "z", lambda e: (self.undo(), "break")[1])
            bind_ctrl(widget, "y", lambda e: (self.redo(), "break")[1])
            bind_ctrl(widget, "z", lambda e: (self.redo(), "break")[1],
                      shift=True)
            bind_ctrl(widget, "d",
                      lambda e: (self.duplicate_nodes(list(self.selection)),
                                 "break")[1])
            bind_ctrl(widget, "c",
                      lambda e: (self.copy_selection(), "break")[1])
            bind_ctrl(widget, "v",
                      lambda e: (self.paste_clipboard(), "break")[1])

        panel = ttk.Frame(center, style="Card.TFrame", padding=(8, 6))
        panel.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        panel.columnconfigure(0, weight=1)
        self._problems_panel = panel
        self._problems = ttk.Treeview(panel, columns=("sev", "subject", "msg"),
                                      show="headings", height=4)
        self._problems.heading("sev", text="!")
        self._problems.heading("subject", text=self.t("interface.gui.col.element"))
        self._problems.heading("msg", text=self.t("focuses.col.problem"))
        self._problems.column("sev", width=30, anchor="center", stretch=False)
        self._problems.column("subject", width=240, stretch=False)
        self._problems.column("msg", width=420)
        self._problems.grid(row=0, column=0, sticky="ew")
        self._problems.tag_configure("error", foreground=self.palette.danger)
        self._problems.bind("<Double-Button-1>", self._problem_open)
        panel.grid_remove()
        self._problems_visible = False
        self._problem_paths: dict[str, IndexPath] = {}

    def _build_right(self) -> None:
        right = ttk.Frame(self, style="TFrame")
        right.grid(row=1, column=2, sticky="nsew", padx=(10, 0))
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        self.columnconfigure(2, minsize=440)
        self.inspector = WidgetInspector(right, self)
        self.inspector.grid(row=0, column=0, sticky="nsew")
        self._placeholder = ttk.Label(right,
                                      text=self.t("interface.gui.select_hint"),
                                      style="Muted.TLabel", wraplength=380)
        self._placeholder.grid(row=0, column=0)
        self._show_inspector(False)

    # ---------------------------------------------------------------- toggles
    def _toggle_left(self) -> None:
        self._left_visible = not self._left_visible
        if self._left_visible:
            self.columnconfigure(0, minsize=330)
            self._left.grid()
        else:
            self._left.grid_remove()
            self.columnconfigure(0, minsize=0)

    def _toggle_problems(self) -> None:
        self._problems_visible = not self._problems_visible
        if self._problems_visible:
            self._validate()
            self._problems_panel.grid()
        else:
            self._problems_panel.grid_remove()

    def _show_inspector(self, visible: bool) -> None:
        if visible:
            self._placeholder.grid_remove()
            self.inspector.grid()
        else:
            self.inspector.grid_remove()
            self._placeholder.grid()

    def _snap_changed(self) -> None:
        self.canvas.snap_enabled = self._snap_var.get()

    def _res_changed(self, _event=None) -> None:
        label = self._res_var.get()
        if "×" in label:
            w, h = label.split("×")
            self.resolution = (int(w), int(h))
            self.schedule_render(0)
            return

        from ..common import TextPromptDialog

        def apply(text: str) -> None:
            try:
                w, h = text.lower().replace("×", "x").split("x")
                self.resolution = (max(320, int(w)), max(200, int(h)))
            except ValueError:
                return
            self._res_var.set(self._res_label(self.resolution))
            self.schedule_render(0)

        TextPromptDialog(self, self, self.t("interface.gui.res_custom"),
                         "WxH", apply, initial="1920x1080",
                         pattern=r"^\d{3,4}\s*[x×]\s*\d{3,4}$")

    # ------------------------------------------------------------------- docs
    def reload_docs(self) -> None:
        # Dependency-mod files are is_vanilla=True even with the checkbox off
        # (layered_docs always lists them): read-only base files, never
        # editable mod docs.
        refs = self.service.list_gui_docs(include_vanilla=self.tree.show_vanilla)
        self.mod_docs = []
        for ref in refs:
            if ref.is_vanilla:
                continue
            try:
                self.mod_docs.append(self.service.load_gui(ref))
            except Exception:
                continue
        self.vanilla_refs = [r for r in refs if r.is_vanilla]
        self.tree.refresh()

    def load_doc(self, ref):
        try:
            return self.service.load_gui(ref)
        except Exception:
            return None

    def open_by_ref(self, ref, wi: int) -> None:
        """Open a window given a doc ref (cross-tab navigation)."""
        doc = next((d for d in self.mod_docs
                    if d.ref.rel_file == ref.rel_file), None)
        if doc is None:
            doc = self.load_doc(ref)
        if doc is None:
            return
        if doc.ref.is_vanilla and not self.tree.show_vanilla:
            self.tree._vanilla.set(True)
            self.reload_docs()
        self.open_window(doc, wi)
        self._show_inspector(True)
        self.inspector.show(doc, wi, self.window, self.editable)
        self.tree.select_node(doc, wi, None)

    def _tree_selected(self, payload) -> None:
        self.inspector.flush_pending()
        kind = payload[0]
        if kind == "file":
            ref = payload[1]
            self._set_copy_btn(ref if ref.is_vanilla else None)
            return
        if kind == "window":
            _k, doc, wi = payload
            self.open_window(doc, wi)
            self._select_paths([], from_tree=True)
            self._show_inspector(True)
            self.inspector.show(doc, wi, self.window, self.editable)
            return
        _k, doc, wi, path = payload
        if doc is not self.doc or wi != self.wi:
            self.open_window(doc, wi)
        self._select_paths([path], from_tree=True)

    def _tree_multi_selected(self, doc, wi: int, paths: list) -> None:
        self.inspector.flush_pending()
        if doc is not self.doc or wi != self.wi:
            self.open_window(doc, wi)
        self._select_paths(list(paths), from_tree=True)

    def open_window(self, doc, wi: int) -> None:
        self.doc = doc
        self.wi = wi
        self.window = doc.find(wi)
        self.editable = not doc.ref.is_vanilla
        self.selection = []
        self._set_copy_btn(doc.ref if doc.ref.is_vanilla else None)
        self.schedule_render(0)

    def _set_copy_btn(self, vanilla_ref) -> None:
        self._copy_ref = vanilla_ref
        if vanilla_ref is not None:
            self._copy_btn.pack(side="left", padx=2)
        else:
            self._copy_btn.pack_forget()

    _DEFAULT_WINDOW = ('containerWindowType = {{\n'
                       '\tname = "{name}"\n'
                       '\tposition = {{ x = 0 y = 0 }}\n'
                       '\tsize = {{ width = 400 height = 300 }}\n'
                       '}}')

    def _new_gui_file(self) -> None:
        from ..common import TextPromptDialog

        def create(name: str) -> None:
            rel = (f"interface/{name}.gui" if not name.endswith(".gui")
                   else f"interface/{name}")
            if any(d.ref.rel_file == rel for d in self.mod_docs):
                messagebox.showerror("ANKA", self.t("focuses.err.file_exists"))
                return
            ref = self.service.create_doc(rel, "guiTypes")
            doc = self.service.load_gui(ref)
            self._append_window(doc, self._unique_window_name(doc))
            self.mark_dirty(doc)
            try:
                self.service.save(doc)
            except Exception as exc:                      # noqa: BLE001
                messagebox.showerror("ANKA", str(exc))
                return
            self.reload_docs()
            new = next((d for d in self.mod_docs
                        if d.ref.rel_file == rel), None)
            if new is not None:
                self.open_window(new, 0)
                self._show_inspector(True)
                self.inspector.show(new, 0, self.window, self.editable)
                self.tree.select_node(new, 0, None)

        TextPromptDialog(self, self, self.t("interface.gui.new_file"),
                         self.t("interface.gfx.file_label"), create,
                         pattern=r"^[\w./-]+$")

    def _new_window(self) -> None:
        if self.doc is None or self.doc.ref.is_vanilla:
            messagebox.showinfo("ANKA", self.t("interface.gui.new_window_hint"))
            return
        doc = self.doc
        wi = len(doc.windows())
        self._append_window(doc, self._unique_window_name(doc))
        self.mark_dirty(doc)
        self.reload_docs()
        self.open_window(doc, wi)
        self._show_inspector(True)
        self.inspector.show(doc, wi, self.window, self.editable)
        self.tree.select_node(doc, wi, None)

    def _append_window(self, doc, name: str) -> None:
        gt = doc.gui_types(create=True)
        parsed = pdx_parse(self._DEFAULT_WINDOW.format(name=name), recover=False)
        gt.items.extend(parsed.items)

    def _unique_window_name(self, doc) -> str:
        taken = {w.name for w in doc.windows()}
        i = 1
        while f"anka_window_{i}" in taken:
            i += 1
        return f"anka_window_{i}"

    def _copy_to_mod(self) -> None:
        if self._copy_ref is None:
            return
        wi = self.wi if self.doc is not None \
            and self.doc.ref.rel_file == self._copy_ref.rel_file else 0
        new_ref = self.service.copy_to_mod(self._copy_ref)
        self.reload_docs()
        doc = next((d for d in self.mod_docs
                    if d.ref.rel_file == new_ref.rel_file), None)
        if doc is not None:
            self.open_window(doc, wi)
            self.tree.select_node(doc, wi, None)

    # -------------------------------------------------------------- rendering
    def _get_renderer(self) -> GuiRenderer | None:
        if self._renderer is not None:
            return self._renderer
        if not self.editor.resolver_ready():
            return None
        self._renderer = GuiRenderer(
            SpriteImageCache(self.service.sprite_catalog()),
            FontProvider(),
            loc_get=lambda key: self.editor.loc_get(key, self.loc_language))
        return self._renderer

    def schedule_render(self, delay: int = 80) -> None:
        if self._render_job is not None:
            self.after_cancel(self._render_job)
        self._render_job = self.after(delay, self._render_now)

    def _render_now(self) -> None:
        self._render_job = None
        if self.window is None:
            self.canvas.set_scene(None, {}, set(), self.resolution, [], False)
            return
        renderer = self._get_renderer()
        if renderer is None:                # catalog still warming up
            self.canvas.set_scene(None, {}, set(), self.resolution, [], False)
            self._render_job = self.after(400, self._render_now)
            return
        try:
            result = renderer.render_window(self.window, self.resolution,
                                            ghost_boxes=self._ghost_var.get())
        except Exception:
            return
        self._result = result
        containers = set()
        for path in result.rects:
            node = self.doc.find(self.wi, path)
            if node is not None and node.is_container:
                containers.add(path)
        self.canvas.set_scene(result.image, result.rects, containers,
                              self.resolution, self.selection, self.editable)
        if self._problems_visible:
            self._validate()

    # -------------------------------------------------------------- selection
    def _canvas_selected(self, paths: list[IndexPath]) -> None:
        self._select_paths(paths, from_canvas=True)

    def _select_paths(self, paths: list[IndexPath], *, from_tree: bool = False,
                      from_canvas: bool = False) -> None:
        self.inspector.flush_pending()
        self.selection = list(paths)
        self.canvas.set_selection(self.selection)
        # Keep the hierarchy tree in step with a canvas (or programmatic) selection.
        if not from_tree and self.doc is not None:
            self.tree.select_nodes(self.doc, self.wi, self.selection)
        if len(paths) == 1:
            node = self.doc.find(self.wi, paths[0]) if self.doc else None
            if node is not None:
                self._show_inspector(True)
                self.inspector.show(self.doc, self.wi, node, self.editable)
                return
        if len(paths) > 1:
            nodes = [n for n in (self.node_at(p) for p in paths)
                     if n is not None]
            if nodes:
                self._show_inspector(True)
                self.inspector.show_group(self.doc, self.wi, nodes,
                                          self.editable)
                return
        if not paths and self.window is not None:
            self._show_inspector(True)
            self.inspector.show(self.doc, self.wi, self.window, self.editable)
            return
        self._show_inspector(False)

    def _reshow_inspector(self) -> None:
        """Re-display the inspector for the current selection (language or
        history change invalidated its contents)."""
        self._select_paths(list(self.selection), from_tree=True)

    # ------------------------------------------------------------ command core
    def node_at(self, path: IndexPath):
        return self.doc.find(self.wi, path) if self.doc is not None else None

    def mark_dirty(self, doc) -> None:
        if doc is not None and not doc.ref.is_vanilla:
            self._dirty.add(str(doc.ref.path))

    def _record(self, command) -> None:
        self.commands.record(command)
        self._history_buttons()

    def _history_buttons(self) -> None:
        self._undo_btn.state(["!disabled"] if self.commands.can_undo()
                             else ["disabled"])
        self._redo_btn.state(["!disabled"] if self.commands.can_redo()
                             else ["disabled"])

    def undo(self) -> None:
        self.inspector.flush_pending()
        command = self.commands.undo(self)
        if command is not None:
            self._after_history(command)

    def redo(self) -> None:
        self.inspector.flush_pending()
        command = self.commands.redo(self)
        if command is not None:
            self._after_history(command)

    def _after_history(self, command) -> None:
        self._history_buttons()
        touches = command.touches
        if TOUCH_TREE in touches:
            self.tree.refresh()
            self.selection = [p for p in self.selection
                              if self.node_at(p) is not None]
        self.schedule_render(0)
        self._reshow_inspector()

    # ---------------------------------------------------------------- commits
    def commit_attr(self, node, attr: str, kind: str, new) -> None:
        """Inspector commit: read old, apply, record (already executed)."""
        if not self.editable or self.doc is None:
            return
        key = node.position_key() if attr == "position" else attr
        if kind == "xy":
            old = node.get_xy(key)
            if tuple(new) == old:
                return
        elif kind == "script":
            old = node.get_script(key)
            if (new or "").strip() == old.strip():
                return
        else:
            old = node.get_attr(key)
            if (new or "").strip() == old:
                return
        command = SetAttrCommand(self.doc, self.wi, node.index_path(),
                                 key, kind, old, new)
        command.redo(self)
        self._record(command)
        if TOUCH_TREE in command.touches:
            self.tree.refresh()
        self.schedule_render()

    def commit_attr_group(self, attr: str, kind: str, new) -> None:
        """Group edit: apply one attribute value to every selected node
        (one compound undo step). Nodes whose value already matches are
        skipped."""
        if not self.editable or self.doc is None:
            return
        children: list[SetAttrCommand] = []
        for path in self.selection:
            node = self.node_at(path)
            if node is None:
                continue
            key = node.position_key() if attr == "position" else attr
            if kind == "xy":
                old = node.get_xy(key)
                if tuple(new) == old:
                    continue
            else:
                old = node.get_attr(key)
                if (new or "").strip() == old:
                    continue
            children.append(SetAttrCommand(self.doc, self.wi, path, key,
                                           kind, old, new))
        if not children:
            return
        command = children[0] if len(children) == 1 else CompoundCommand(
            children, "interface.cmd.set_attr")
        command.redo(self)
        self._record(command)
        if TOUCH_TREE in command.touches:
            self.tree.refresh()
        self.schedule_render()

    def commit_background(self, node, sprite: str) -> None:
        """Edit the sprite inside ``background = { ... }`` preserving the
        block's other keys."""
        if not self.editable or self.doc is None:
            return
        bg_key = "backGround" if node.block.has_ci("backGround") \
            and not node.block.has_ci("background") else "background"
        old = node.get_script(bg_key)
        block = pdx_parse(old, recover=False) if old.strip() else None
        from ...core.pdx import Block, Scalar
        bg = Block(block.items) if block is not None else Block()
        sprite = sprite.strip()
        ref_key = "spriteType" if (bg.has_ci("spriteType")
                                   and not bg.has_ci("quadTextureSprite")) \
            else "quadTextureSprite"
        if sprite:
            if not bg.has_ci("name"):
                bg.set_ci("name", Scalar("Background", quoted=True))
            bg.set_ci(ref_key, Scalar(sprite, quoted=True))
            new = dumps(bg, top_level=False)
        else:
            bg.remove_ci(ref_key)
            new = dumps(bg, top_level=False) if len(bg.items) else ""
        self.commit_attr(node, bg_key, "script", new)

    # ------------------------------------------------------- canvas mutations
    def _parent_rect(self, path: IndexPath) -> Rect:
        if self._result is not None and path[:-1] in self._result.rects:
            return self._result.rects[path[:-1]]
        return Rect(0, 0, float(self.resolution[0]),
                    float(self.resolution[1]))

    def move_nodes(self, paths: list[IndexPath],
                   delta: tuple[float, float]) -> None:
        if not self.editable or self._result is None:
            return
        renderer = self._get_renderer()
        if renderer is None:
            return
        children: list[SetAttrCommand] = []
        for path in paths:
            node = self.node_at(path)
            rect = self._result.rects.get(path)
            if node is None or rect is None:
                continue
            key = node.position_key()
            parent = self._parent_rect(path)
            nx, ny = renderer.solver.position_for_rect(
                node, rect.x + delta[0], rect.y + delta[1], parent)
            old = node.get_xy(key)
            new = (_fmt(nx), _fmt(ny))
            if new == old:
                continue
            children.append(SetAttrCommand(self.doc, self.wi, path, key,
                                           "xy", old, new))
        if not children:
            return
        command = children[0] if len(children) == 1 else CompoundCommand(
            children, "interface.cmd.move")
        command.redo(self)
        self._record(command)
        self.schedule_render(0)
        self.inspector.update_geometry()

    def resize_node(self, path: IndexPath, rect: Rect) -> None:
        if not self.editable or self._result is None:
            return
        node = self.node_at(path)
        old_rect = self._result.rects.get(path)
        renderer = self._get_renderer()
        if node is None or old_rect is None or renderer is None:
            return
        parent = self._parent_rect(path)
        children = self._resize_size_commands(node, path, rect, parent, renderer)
        if (round(rect.x), round(rect.y)) != (round(old_rect.x),
                                              round(old_rect.y)):
            key = node.position_key()
            # solve with the *new* size: apply size first, then invert
            for cmd in children:
                cmd.redo(self)
            nx, ny = renderer.solver.position_for_rect(node, rect.x, rect.y,
                                                       parent)
            old_pos = node.get_xy(key)
            new_pos = (_fmt(nx), _fmt(ny))
            if new_pos != old_pos:
                pos_cmd = SetAttrCommand(self.doc, self.wi, path, key, "xy",
                                         old_pos, new_pos)
                pos_cmd.redo(self)
                children.append(pos_cmd)
        else:
            for cmd in children:
                cmd.redo(self)
        if not children:
            return
        command = children[0] if len(children) == 1 else CompoundCommand(
            children, "interface.cmd.resize")
        self._record(command)
        self.schedule_render(0)
        self.inspector.update_geometry()

    def _resize_size_commands(self, node, path: IndexPath, rect: Rect,
                              parent: Rect, renderer) -> list:
        """Size commands for a canvas resize — redirected for types whose size is
        derived: instantTextBoxType edits maxWidth/maxHeight, iconType edits scale
        (its size is the sprite × scale). Everything else edits `size`."""
        tl = node.type_key.lower()
        out: list[SetAttrCommand] = []
        if tl == "instanttextboxtype":
            for attr, val in (("maxWidth", max(1, round(rect.w))),
                              ("maxHeight", max(1, round(rect.h)))):
                old, new = node.get_attr(attr), str(val)
                if new != old:
                    out.append(SetAttrCommand(self.doc, self.wi, path, attr,
                                              "scalar", old, new))
            return out
        if tl == "icontype":
            sprite = (node.get_attr("spriteType")
                      or node.get_attr("quadTextureSprite"))
            native = (renderer.solver.metrics.sprite_frame_size(sprite)
                      if sprite else None)
            if native and native[0] > 0:
                scale = max(0.05, rect.w / native[0])
                old = node.get_attr("scale")
                new = f"{scale:.3f}".rstrip("0").rstrip(".")
                if new != old:
                    out.append(SetAttrCommand(self.doc, self.wi, path, "scale",
                                              "scalar", old, new))
            return out
        old_size = node.get_xy("size")
        new_size = (_resize_component(old_size[0], rect.w, parent.w),
                    _resize_component(old_size[1], rect.h, parent.h))
        if new_size != old_size:
            out.append(SetAttrCommand(self.doc, self.wi, path, "size",
                                      "xy", old_size, new_size))
        return out

    def create_node(self, type_key: str, parent_path: IndexPath,
                    world_xy: tuple[float, float]) -> None:
        if not self.editable or self.doc is None or self.window is None:
            return
        parent = self.node_at(parent_path) or self.window
        if not parent.is_container:
            parent_path = parent_path[:-1]
            parent = self.node_at(parent_path) or self.window
        parent_rect = (self._result.rects.get(parent_path)
                       if self._result is not None else None) \
            or Rect(0, 0, float(self.resolution[0]), float(self.resolution[1]))
        px = max(0, round(world_xy[0] - parent_rect.x))
        py = max(0, round(world_xy[1] - parent_rect.y))
        name = self._unique_name(type_key)
        size = _DEFAULT_SIZES.get(type_key.lower())
        lines = [f'{type_key} = {{',
                 f'\tname = "{name}"',
                 f'\tposition = {{ x = {px} y = {py} }}']
        if size is not None:
            lines.append(f'\tsize = {{ width = {size[0]} height = {size[1]} }}')
        if type_key.lower() == "instanttextboxtype":
            lines += ['\ttext = "Text"', '\tfont = "hoi_18b"',
                      '\tmaxWidth = 140', '\tmaxHeight = 20',
                      '\tformat = left']
        if type_key.lower() == "gridboxtype":
            lines += ['\tslotsize = { width = 50 height = 50 }',
                      '\tmax_slots_horizontal = 4',
                      '\tformat = "UPPER_LEFT"']
        lines.append('}')
        index = len(parent.children())
        command = CreateNodeCommand(self.doc, self.wi, parent_path, index,
                                    "\n".join(lines))
        command.redo(self)
        self._record(command)
        self.tree.refresh()
        self.schedule_render(0)
        self._select_paths([parent_path + (index,)])

    def _taken_names(self) -> set[str]:
        """Every element name currently in the open window."""
        taken: set[str] = set()

        def collect(node) -> None:
            if node.name:
                taken.add(node.name)
            for child in node.children():
                collect(child)

        if self.window is not None:
            collect(self.window)
        return taken

    def _fresh_name(self, type_key: str, taken: set[str]) -> str:
        """A unique ``anka_<type>_<n>`` name; adds it to `taken` so repeated
        calls (duplicating / pasting several nodes at once) stay distinct."""
        base = type_key.lower().replace("type", "")
        i = 1
        while f"anka_{base}_{i}" in taken:
            i += 1
        name = f"anka_{base}_{i}"
        taken.add(name)
        return name

    def _unique_name(self, type_key: str) -> str:
        return self._fresh_name(type_key, self._taken_names())

    def reparent_nodes(self, paths: list[IndexPath],
                       new_parent_path: IndexPath) -> None:
        if not self.editable or self._result is None:
            return
        target = self.node_at(new_parent_path) or self.window
        if target is None or not target.is_container:
            return
        target_rect = self._result.rects.get(new_parent_path) or Rect(
            0, 0, float(self.resolution[0]), float(self.resolution[1]))
        from ...core.guitypes.views import GuiNode
        # collect valid moves (skip moving a node into itself/its own subtree)
        moves: list[tuple[IndexPath, str, str]] = []
        for path in paths:
            node = self.node_at(path)
            rect = self._result.rects.get(path)
            if node is None or rect is None:
                continue
            if new_parent_path[:len(path)] == path:   # target inside the moved node
                continue
            block = pdx_parse(node.snapshot(), recover=False)
            copy = GuiNode(next(p for p in block.pairs()))
            copy.set_xy(copy.position_key(),
                        _fmt(rect.x - target_rect.x),
                        _fmt(rect.y - target_rect.y))
            moves.append((path, node.snapshot(), dumps(block, top_level=True)))
        if not moves:
            return
        deleted = [m[0] for m in moves]
        # All deletions run first (deepest / highest index first so they don't
        # invalidate one another); then re-create under the target path shifted to
        # account for those deletions — CreateNodeCommand resolves its parent at
        # execution time, so the shifted path must match the post-delete tree.
        children: list = [
            DeleteNodeCommand(self.doc, self.wi, p[:-1], p[-1], snap)
            for p, snap, _copy in sorted(moves, key=lambda m: m[0], reverse=True)]
        adj_parent = self._shift_path(new_parent_path, deleted)
        moved_from_target = sum(1 for d in deleted
                                if d[:-1] == tuple(new_parent_path))
        base = max(0, len(target.children()) - moved_from_target)
        for i, (_p, _snap, copy_text) in enumerate(moves):
            children.append(CreateNodeCommand(self.doc, self.wi, adj_parent,
                                              base + i, copy_text))
        command = CompoundCommand(children, "interface.cmd.reparent")
        command.redo(self)
        self._record(command)
        self.tree.refresh()
        self.schedule_render(0)
        self._select_paths([])

    @staticmethod
    def _shift_path(path: IndexPath, deleted: list[IndexPath]) -> IndexPath:
        """`path` after removing every `deleted` node: a removed earlier sibling
        along the path decrements that component."""
        adj = list(path)
        for d in deleted:
            k = len(d) - 1
            if 0 <= k < len(adj) and tuple(adj[:k]) == d[:k] and d[k] < adj[k]:
                adj[k] -= 1
        return tuple(adj)

    def delete_nodes(self, paths: list[IndexPath]) -> None:
        if not self.editable or not paths:
            return
        children = []
        for path in sorted(paths, reverse=True):
            node = self.node_at(path)
            if node is None or not path:
                continue
            children.append(DeleteNodeCommand(self.doc, self.wi, path[:-1],
                                              path[-1], node.snapshot()))
        if not children:
            return
        command = children[0] if len(children) == 1 else CompoundCommand(
            children, "interface.cmd.delete")
        command.redo(self)
        self._record(command)
        self.tree.refresh()
        self.schedule_render(0)
        self._select_paths([])

    def delete_window(self, doc, wi: int) -> None:
        """Delete a top-level window from a mod document (undoable)."""
        if doc is None or doc.ref.is_vanilla:
            return
        windows = doc.windows()
        if wi >= len(windows):
            return
        window = windows[wi]
        if not messagebox.askyesno("ANKA", self.t(
                "interface.gui.confirm_delete_window",
                name=window.name or window.type_key)):
            return
        command = DeleteWindowCommand(doc, wi, window.snapshot())
        command.redo(self)
        self._record(command)
        if doc is self.doc and wi == self.wi:
            self.doc = None
            self.wi = 0
            self.window = None
            self.selection = []
            self._show_inspector(False)
        elif doc is self.doc and wi < self.wi:
            self.wi -= 1
        self.tree.refresh()
        self.schedule_render(0)

    def reorder_node(self, path: IndexPath, delta: int) -> None:
        if not self.editable or not path:
            return
        parent = self.node_at(path[:-1]) or self.window
        if parent is None:
            return
        count = len(parent.children())
        new_index = path[-1] + delta
        if not (0 <= new_index < count):
            return
        command = ReorderCommand(self.doc, self.wi, path[:-1], path[-1],
                                 new_index)
        command.redo(self)
        self._record(command)
        self.tree.refresh()
        self.schedule_render(0)
        self._select_paths([path[:-1] + (new_index,)])

    def duplicate_node(self, path: IndexPath) -> None:
        if not self.editable or not path:
            return
        node = self.node_at(path)
        if node is None:
            return
        from ...core.guitypes.views import GuiNode
        block = pdx_parse(node.snapshot(), recover=False)
        pair = next(p for p in block.pairs())
        copy = GuiNode(pair)
        if copy.name:
            copy.set_attr("name", self._unique_name(node.type_key))
        x, y = copy.get_position()
        copy.set_position(x + 10, y + 10)
        parent = self.node_at(path[:-1]) or self.window
        index = len(parent.children())
        command = CreateNodeCommand(self.doc, self.wi, path[:-1], index,
                                    dumps(block, top_level=True))
        command.redo(self)
        self._record(command)
        self.tree.refresh()
        self.schedule_render(0)
        self._select_paths([path[:-1] + (index,)])

    # --------------------------------------------------------- copy/paste/dup
    def duplicate_nodes(self, paths: list[IndexPath]) -> None:
        """Ctrl+D: clone each selected element next to itself, keeping its exact
        position (a fresh unique name avoids a duplicate-name warning). Group
        selections duplicate as one undo step; the copies become the selection."""
        if not self.editable or self.doc is None or not paths:
            return
        from ...core.guitypes.views import GuiNode
        taken = self._taken_names()
        specs: list[tuple[IndexPath, str]] = []
        for path in sorted(paths):
            node = self.node_at(path)
            if node is None or not path:
                continue
            block = pdx_parse(node.snapshot(), recover=False)
            copy = GuiNode(next(p for p in block.pairs()))
            if copy.name:
                copy.set_attr("name", self._fresh_name(node.type_key, taken))
            specs.append((path[:-1], dumps(block, top_level=True)))
        self._insert_copies(specs, "interface.cmd.duplicate")

    def copy_selection(self) -> None:
        """Ctrl+C: put the selected element(s) on the clipboard as PDX text."""
        if not self.selection:
            return
        self.inspector.flush_pending()
        snaps = [self.node_at(p).snapshot() for p in sorted(self.selection)
                 if self.node_at(p) is not None]
        if snaps:
            self.clipboard_clear()
            self.clipboard_append("\n".join(snaps))

    def paste_clipboard(self) -> None:
        """Ctrl+V: parse the clipboard as PDX and add every widget it holds to
        the selected container (else the current window), each with a fresh
        unique name. Non-widget clipboard text is ignored."""
        if not self.editable or self.doc is None or self.window is None:
            return
        try:
            text = self.clipboard_get()
        except tk.TclError:
            return
        if not text or not text.strip():
            return
        try:
            parsed = pdx_parse(text, recover=False)
        except Exception:                                 # noqa: BLE001
            return
        from ...core.guitypes.schema import widget_spec
        from ...core.guitypes.views import GuiNode
        from ...core.pdx import Block

        parent_path: IndexPath = ()
        if self.selection:
            node = self.node_at(self.selection[0])
            if node is not None:
                parent_path = (self.selection[0] if node.is_container
                               else self.selection[0][:-1])
        taken = self._taken_names()
        specs: list[tuple[IndexPath, str]] = []
        for pair in parsed.pairs():
            if not isinstance(pair.value, Block) or widget_spec(pair.key) is None:
                continue                                  # skip non-widget pairs
            node = GuiNode(pair)
            if node.name:
                node.set_attr("name", self._fresh_name(node.type_key, taken))
            specs.append((parent_path, dumps(Block([pair]), top_level=True)))
        self._insert_copies(specs, "interface.cmd.paste")

    def _insert_copies(self, specs: list[tuple[IndexPath, str]],
                       label: str) -> None:
        """Create each ``(parent_path, node_text)`` as a new child, appended in
        order (per-parent indices assigned so multiple inserts don't collide),
        as one undo step; the created nodes become the selection."""
        if not specs:
            return
        counters: dict[IndexPath, int] = {}
        children: list = []
        new_paths: list[IndexPath] = []
        for parent_path, node_text in specs:
            if parent_path not in counters:
                parent = self.node_at(parent_path) or self.window
                counters[parent_path] = len(parent.children())
            index = counters[parent_path]
            counters[parent_path] += 1
            children.append(CreateNodeCommand(self.doc, self.wi, parent_path,
                                              index, node_text))
            new_paths.append(parent_path + (index,))
        command = children[0] if len(children) == 1 else CompoundCommand(
            children, label)
        command.redo(self)
        self._record(command)
        self.tree.refresh()
        self.schedule_render(0)
        self._select_paths(new_paths)

    # ---------------------------------------------------------------- palette
    def palette_add(self, type_key: str) -> None:
        if self.window is None or not self.editable:
            return
        parent_path: IndexPath = ()
        if self.selection:
            node = self.node_at(self.selection[0])
            if node is not None:
                parent_path = (self.selection[0] if node.is_container
                               else self.selection[0][:-1])
        parent_rect = (self._result.rects.get(parent_path)
                       if self._result is not None else None)
        origin = ((parent_rect.x + 10, parent_rect.y + 10)
                  if parent_rect is not None else (10.0, 10.0))
        self.create_node(type_key, parent_path, origin)

    def palette_place(self, type_key: str) -> None:
        if self.window is not None and self.editable:
            self.canvas.begin_place(type_key)

    # ------------------------------------------------------------ context menu
    def _canvas_context(self, event, path: IndexPath | None) -> None:
        if path is not None and path not in self.selection:
            self._select_paths([path])
        self._show_context(event.x_root, event.y_root, path)

    def _tree_context(self, event, payload) -> None:
        if payload[0] == "node":
            _k, doc, wi, path = payload
            if doc is not self.doc or wi != self.wi:
                self.open_window(doc, wi)
            self._select_paths([path], from_tree=True)
            self._show_context(event.x_root, event.y_root, path)
        elif payload[0] == "window":
            _k, doc, wi = payload
            self.open_window(doc, wi)
            self._show_context(event.x_root, event.y_root, None)

    def _show_context(self, x: int, y: int, path: IndexPath | None) -> None:
        menu = tk.Menu(self, tearoff=0)
        node = self.node_at(path) if path is not None else self.window
        if node is None:
            return
        target_container = (node if node.is_container else None)
        if self.editable:
            add = tk.Menu(menu, tearoff=0)
            from ...core.guitypes.schema import WIDGETS
            for spec in sorted(WIDGETS.values(), key=lambda s: s.type_key):
                add.add_command(
                    label=f"{spec.icon} {spec.type_key}",
                    command=lambda tk_=spec.type_key: self._context_add(
                        tk_, path, target_container is not None))
            menu.add_cascade(label="➕ " + self.t("common.add"), menu=add)
            if path is not None:
                menu.add_command(label="⧉ " + self.t("interface.gui.duplicate"),
                                 command=lambda: self.duplicate_node(path))
                menu.add_separator()
                menu.add_command(label="▲ " + self.t("interface.gui.raise"),
                                 command=lambda: self.reorder_node(path, +1))
                menu.add_command(label="▼ " + self.t("interface.gui.lower"),
                                 command=lambda: self.reorder_node(path, -1))
                moved = list(self.selection) if path in self.selection else [path]
                self._add_reparent_menu(menu, moved)
                menu.add_separator()
                menu.add_command(label="🗑 " + self.t("interface.gfx.delete"),
                                 command=lambda: self.delete_nodes(moved))
            else:
                menu.add_separator()
                menu.add_command(
                    label="🗑 " + self.t("interface.gui.delete_window"),
                    command=lambda d=self.doc, w=self.wi: self.delete_window(d, w))
        menu.add_command(label="📋 " + self.t("interface.gui.copy_pdx"),
                         command=lambda: self._copy_pdx(node))
        menu.tk_popup(x, y)

    def _add_reparent_menu(self, menu: tk.Menu, moved: list[IndexPath]) -> None:
        """`Move to…` → every container the moved node(s) may go into (the window
        root and each container widget, minus the moved subtrees / current parents)."""
        if self.window is None:
            return
        excluded = set(moved)
        current_parents = {p[:-1] for p in moved}
        targets: list[tuple[IndexPath, str]] = [
            ((), "▣ " + (self.window.name or self.window.type_key))]

        def walk(node, path: IndexPath) -> None:
            for i, child in enumerate(node.children()):
                cpath = path + (i,)
                inside_moved = any(cpath[:len(e)] == e for e in excluded)
                if child.is_container and not inside_moved:
                    targets.append(
                        (cpath, "   " * len(cpath)
                         + (child.name or child.type_key)))
                walk(child, cpath)

        walk(self.window, ())
        # a move to the node's current parent is a no-op — drop it
        targets = [(p, lbl) for p, lbl in targets if p not in current_parents]
        if not targets:
            return
        sub = tk.Menu(menu, tearoff=0)
        for tpath, label in targets:
            sub.add_command(label=label,
                            command=lambda t=tpath: self.reparent_nodes(moved, t))
        menu.add_cascade(label="↳ " + self.t("interface.gui.move_to"), menu=sub)

    def _context_add(self, type_key: str, path: IndexPath | None,
                     into: bool) -> None:
        parent_path: IndexPath = () if path is None else (
            path if into else path[:-1])
        rect = (self._result.rects.get(parent_path)
                if self._result is not None else None)
        origin = (rect.x + 10, rect.y + 10) if rect is not None else (10, 10)
        self.create_node(type_key, parent_path, origin)

    def _copy_pdx(self, node) -> None:
        self.clipboard_clear()
        self.clipboard_append(node.snapshot())

    # ---------------------------------------------------------------- problems
    def _validate(self) -> None:
        self._problems.delete(*self._problems.get_children())
        self._problem_paths.clear()
        issues: list[tuple[str, str, str, IndexPath | None]] = []
        if self.doc is not None and self.window is not None:
            catalog = (self.service.sprite_catalog()
                       if self.editor.resolver_ready() else None)
            fonts = set(self.service.font_names())
            names_seen: dict[str, IndexPath] = {}

            def walk(node, path: IndexPath) -> None:
                name = node.name
                if name:
                    if name in names_seen:
                        issues.append(("warning", name,
                                       self.t("interface.gui.issue.dup_name"),
                                       path))
                    names_seen.setdefault(name, path)
                if catalog is not None:
                    for key in ("spriteType", "quadTextureSprite"):
                        sprite = node.get_attr(key)
                        if sprite and catalog.get(sprite) is None:
                            issues.append((
                                "error", name or node.type_key,
                                self.t("interface.gui.issue.missing_sprite",
                                       detail=sprite), path))
                for key in ("font", "buttonFont"):
                    font = node.get_attr(key)
                    if font and fonts and font not in fonts:
                        issues.append((
                            "warning", name or node.type_key,
                            self.t("interface.gui.issue.unknown_font",
                                   detail=font), path))
                for i, child in enumerate(node.children()):
                    walk(child, path + (i,))

            walk(self.window, ())
            if self._result is not None:
                for prob in self._result.problems:
                    node = self.node_at(prob.path)
                    issues.append((
                        "warning",
                        (node.name or node.type_key) if node else "?",
                        self.t(f"interface.gui.issue.{prob.code}",
                               detail=prob.detail), prob.path))
        errors = 0
        for i, (severity, subject, msg, path) in enumerate(issues):
            if severity == "error":
                errors += 1
            iid = str(i)
            self._problems.insert("", "end", iid=iid,
                                  values=("⛔" if severity == "error" else "⚠",
                                          subject, msg),
                                  tags=(severity,))
            if path is not None:
                self._problem_paths[iid] = path
        n = len(issues)
        label = f"⚠ {n}" if not errors else f"⛔ {errors} · ⚠ {n - errors}"
        self._btn_problems.configure(text=label)

    def _problem_open(self, _event=None) -> None:
        sel = self._problems.selection()
        if sel and sel[0] in self._problem_paths:
            self._select_paths([self._problem_paths[sel[0]]])

    # ------------------------------------------------------------------ saving
    def flush_pending(self) -> None:
        self.inspector.flush_pending()

    def save_all(self) -> None:
        self.inspector.flush_pending()
        for doc in self.mod_docs:
            if str(doc.ref.path) in self._dirty:
                try:
                    self.service.save(doc)
                    self._dirty.discard(str(doc.ref.path))
                except Exception as exc:                  # noqa: BLE001
                    messagebox.showerror("ANKA", self.t("focuses.err.save",
                                                        error=str(exc)))
        if self._problems_visible:
            self._validate()


def _fmt(value: float) -> str:
    return str(int(round(value)))


def _resize_component(old_raw: str, new_px: float, parent_dim: float) -> str:
    """Preserve the unit of the original size component: percent stays
    percent, pixels stay pixels."""
    if old_raw and "%" in old_raw and parent_dim > 0:
        pct = new_px / parent_dim * 100.0
        suffix = "%%" if old_raw.endswith("%%") else "%"
        return f"{pct:.1f}".rstrip("0").rstrip(".") + suffix
    return str(int(round(new_px)))
