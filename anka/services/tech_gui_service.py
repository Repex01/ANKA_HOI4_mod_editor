"""Tech-tree GUI model: folders, gridboxes and item layout from the ``.gui`` files.

A technology's ``folder = { position = { x y } }`` cell only becomes a screen
position through the interface files: each folder tab is a ``containerWindowType``
named after the folder (inside ``countrytechtreeview`` — or ``countrydoctrineview``
for doctrine folders) holding one ``gridboxtype`` per branch. The gridbox supplies
the pixel origin, the pixels-per-cell ``slotsize`` and the axis ``format``:

* ``format = "LEFT"`` — horizontal trees: cell **x runs down** the screen, cell
  **y runs right** (the axes swap).
* ``format = "UP"`` (and anything else) — vertical trees: x right, y down.

Which gridbox a tech belongs to mirrors the game's layout rule: a tech with no
incoming (non ``ignore_for_layout``) path inside the folder is a *branch root* and
must own a gridbox named ``<tech_id>_tree``; every other tech inherits the gridbox
of its earliest-defined incoming path source.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..core.guitypes import GuiDoc
from ..core.guitypes.views import GuiNode
from ..core.pdx import Block, Pair, Scalar, dumps
from ..core.pdx import parse as pdx_parse
from ..domain.mod import ModContext
from .interface_service import InterfaceDocRef, InterfaceService
from .technology_service import TechDocRef, Technology, TechnologyService, TechFolderDef

GUI_TECH = "interface/countrytechtreeview.gui"
GUI_DOCTRINE = "interface/countrydoctrinetreeview.gui"
ROOT_TECH_WINDOW = "countrytechtreeview"
ROOT_DOCTRINE_WINDOW = "countrydoctrineview"

DEFAULT_SLOT = (70.0, 70.0)
# Fallback item metrics (vanilla techtree_*_item): drawn box offset from the slot
# anchor and its size.
DEFAULT_ITEM_OFFSET = (-56.0, -7.0)
DEFAULT_ITEM_SIZE = (183.0, 84.0)
DEFAULT_SMALL_ITEM_SIZE = (62.0, 76.0)


@dataclass
class GridBoxInfo:
    name: str
    origin: tuple[float, float]        # pixels inside the folder container
    slotsize: tuple[float, float]
    fmt: str                           # "LEFT" | "UP" | ...
    node: GuiNode

    def cell_to_px(self, cell: tuple[int, int]) -> tuple[float, float]:
        """Pixel anchor (top-left of the slot) of a folder-position cell."""
        x, y = cell
        w, h = self.slotsize
        if self.fmt == "LEFT":
            return self.origin[0] + y * w, self.origin[1] + x * h
        return self.origin[0] + x * w, self.origin[1] + y * h

    def px_to_cell(self, px: tuple[float, float]) -> tuple[int, int]:
        w, h = self.slotsize
        dx = (px[0] - self.origin[0]) / max(w, 1)
        dy = (px[1] - self.origin[1]) / max(h, 1)
        if self.fmt == "LEFT":
            return round(dy), round(dx)
        return round(dx), round(dy)

    def delta_cells(self, dpx: tuple[float, float]) -> tuple[int, int]:
        """Pixel movement -> folder-cell delta (for drag snapping)."""
        w, h = self.slotsize
        if self.fmt == "LEFT":
            return round(dpx[1] / max(h, 1)), round(dpx[0] / max(w, 1))
        return round(dpx[0] / max(w, 1)), round(dpx[1] / max(h, 1))


@dataclass
class TextLabel:
    name: str
    pos: tuple[float, float]
    text: str                          # loc key or literal
    font: str = ""


@dataclass
class ItemLayout:
    """Geometry of the ``techtree_<folder>_item`` container (how a tech is drawn)."""

    offset: tuple[float, float] = DEFAULT_ITEM_OFFSET
    size: tuple[float, float] = DEFAULT_ITEM_SIZE
    icon_pos: tuple[float, float] | None = None
    # ``centerposition = yes`` on the Icon element: its position is the icon's
    # centre (vanilla big item) rather than its top-left corner (small item).
    icon_center: bool = False
    node: GuiNode | None = None


@dataclass
class FolderView:
    folder_id: str
    definition: TechFolderDef
    gui_ref: InterfaceDocRef | None
    container: GuiNode | None
    gridboxes: dict[str, GridBoxInfo] = field(default_factory=dict)
    labels: list[TextLabel] = field(default_factory=list)
    background_sprite: str = ""
    item: ItemLayout = field(default_factory=ItemLayout)
    small_item: ItemLayout | None = None
    tab_sprite: str = ""

    @property
    def has_gui(self) -> bool:
        """Vanilla doctrine folders survive as empty "dummy" containers — treat a
        container without gridboxes as missing GUI."""
        return self.container is not None and bool(self.gridboxes)


@dataclass
class GridAssignment:
    """Result of the per-folder layout pass."""

    by_tech: dict[str, str] = field(default_factory=dict)      # tid -> gridbox name
    missing_root: list[str] = field(default_factory=list)      # root without a gridbox
    ambiguous: dict[str, list[str]] = field(default_factory=dict)  # tid -> candidates


def _num(raw: str) -> float:
    try:
        return float(str(raw).rstrip("%"))
    except (ValueError, TypeError):
        return 0.0


class TechGuiService:
    def __init__(self, ctx: ModContext, interface: InterfaceService,
                 tech: TechnologyService):
        self.ctx = ctx
        self.interface = interface
        self.tech = tech
        self._views: dict[str, FolderView] | None = None

    # ------------------------------------------------------------------ loading
    def invalidate(self) -> None:
        self._views = None

    def gui_ref_for(self, doctrine: bool) -> InterfaceDocRef | None:
        rel = GUI_DOCTRINE if doctrine else GUI_TECH
        for ref in self.interface.list_gui_docs(include_vanilla=True):
            if ref.rel_file.lower() == rel:
                return ref
        return None

    def folder_views(self, refresh: bool = False) -> dict[str, FolderView]:
        if self._views is not None and not refresh:
            return self._views
        views: dict[str, FolderView] = {}
        docs: dict[str, tuple[InterfaceDocRef, GuiDoc] | None] = {}
        for rel in (GUI_TECH, GUI_DOCTRINE):
            ref = self.gui_ref_for(rel == GUI_DOCTRINE)
            if ref is None:
                docs[rel] = None
                continue
            try:
                docs[rel] = (ref, self.interface.load_gui(ref))
            except Exception:
                docs[rel] = None
        for folder_id, definition in self.tech.folders(refresh=refresh).items():
            rel = GUI_DOCTRINE if definition.doctrine else GUI_TECH
            loaded = docs.get(rel)
            view = FolderView(folder_id=folder_id, definition=definition,
                              gui_ref=loaded[0] if loaded else None,
                              container=None)
            if loaded is not None:
                self._fill_view(view, loaded[1],
                                ROOT_DOCTRINE_WINDOW if definition.doctrine
                                else ROOT_TECH_WINDOW)
            views[folder_id] = view
        self._views = views
        return views

    def _fill_view(self, view: FolderView, doc: GuiDoc, root_name: str) -> None:
        root = self._find_window(doc, root_name)
        if root is None:
            return
        container = self._find_child(root, view.folder_id)
        view.container = container
        if container is not None:
            self._collect_layout(view, container, (0.0, 0.0))
        # item containers live at the top of the same root window (or as siblings)
        item = (self._find_child(root, f"techtree_{view.folder_id}_item")
                or self._find_window(doc, f"techtree_{view.folder_id}_item"))
        if item is not None:
            view.item = self._item_layout(item)
        small = (self._find_child(root, f"techtree_{view.folder_id}_small_item")
                 or self._find_window(doc, f"techtree_{view.folder_id}_small_item"))
        if small is not None:
            view.small_item = self._item_layout(small, small=True)
        tabs = self._find_child(root, "folder_tabs")
        if tabs is not None:
            tab = self._find_child(tabs, f"{view.folder_id}_tab")
            if tab is not None:
                view.tab_sprite = (tab.get_attr("quadTextureSprite")
                                   or tab.get_attr("spriteType"))

    @staticmethod
    def _find_window(doc: GuiDoc, name: str) -> GuiNode | None:
        low = name.lower()
        for window in doc.windows():
            if window.name.lower() == low:
                return window
        return None

    @staticmethod
    def _find_child(node: GuiNode, name: str) -> GuiNode | None:
        """Breadth-first search by (case-insensitive) name."""
        low = name.lower()
        queue = node.children()
        while queue:
            child = queue.pop(0)
            if child.name.lower() == low:
                return child
            queue.extend(child.children())
        return None

    def _collect_layout(self, view: FolderView, node: GuiNode,
                        offset: tuple[float, float]) -> None:
        """Gather gridboxes / labels / background from the folder container,
        accumulating nested container offsets so positions are container-relative."""
        for child in node.children():
            key = child.type_key.lower()
            x, y = child.get_position()
            if key == "gridboxtype":
                sw, sh = child.get_xy("slotsize")
                slot = (_num(sw) or DEFAULT_SLOT[0], _num(sh) or DEFAULT_SLOT[1])
                fmt = (child.get_attr("format") or "").strip('"').upper()
                view.gridboxes[child.name] = GridBoxInfo(
                    name=child.name, origin=(offset[0] + x, offset[1] + y),
                    slotsize=slot, fmt=fmt, node=child)
            elif key == "instanttextboxtype":
                text = child.get_attr("text")
                if text:
                    view.labels.append(TextLabel(
                        name=child.name, pos=(offset[0] + x, offset[1] + y),
                        text=text, font=child.get_attr("font")))
            elif key == "icontype":
                sprite = child.get_attr("spriteType")
                if sprite and "techtree_bg" in sprite.lower():
                    view.background_sprite = sprite
            elif key == "containerwindowtype":
                self._collect_layout(view, child, (offset[0] + x, offset[1] + y))

    def _item_layout(self, node: GuiNode, small: bool = False) -> ItemLayout:
        x, y = node.get_position()
        w, h = node.get_size_raw()
        size = (_num(w), _num(h))
        if size[0] <= 0 or size[1] <= 0:
            size = DEFAULT_SMALL_ITEM_SIZE if small else DEFAULT_ITEM_SIZE
        icon = self._find_child(node, "Icon")
        icon_pos = icon.get_position() if icon is not None else None
        icon_center = bool(
            icon is not None
            and (icon.get_attr("centerposition") or "").strip('"').lower()
            in ("yes", "true"))
        return ItemLayout(offset=(x, y), size=size, icon_pos=icon_pos,
                          icon_center=icon_center, node=node)

    # ----------------------------------------------------------- grid assignment
    def assign_gridboxes(self, folder_id: str,
                         techs: list[tuple[TechDocRef, Technology]] | None = None,
                         ) -> GridAssignment:
        """Which gridbox each tech in a folder belongs to (game layout rules)."""
        view = self.folder_views().get(folder_id)
        gridboxes = view.gridboxes if view is not None else {}
        if techs is None:
            techs = self.tech.techs_in_folder(folder_id)
        order = {t.id: i for i, (_r, t) in enumerate(techs)}
        in_folder = {t.id: t for _r, t in techs}

        # incoming layout edges per tech, ordered by the source's definition order
        incoming: dict[str, list[str]] = {tid: [] for tid in in_folder}
        for _ref, tech in techs:
            for path in tech.paths:
                target = path.leads_to_tech
                if target in in_folder and not path.ignore_for_layout:
                    incoming[target].append(tech.id)
        for tid in incoming:
            incoming[tid].sort(key=lambda s: order.get(s, 1 << 30))

        out = GridAssignment()
        resolving: set[str] = set()

        def resolve(tid: str) -> str | None:
            if tid in out.by_tech:
                return out.by_tech[tid]
            if tid in resolving:          # path cycle — bail out
                return None
            resolving.add(tid)
            try:
                sources = incoming.get(tid) or []
                if not sources:           # branch root: owns "<tid>_tree"
                    name = f"{tid}_tree"
                    if name in gridboxes:
                        out.by_tech[tid] = name
                        return name
                    out.missing_root.append(tid)
                    return None
                candidates: list[str] = []
                for src in sources:
                    gb = resolve(src)
                    if gb is not None and gb not in candidates:
                        candidates.append(gb)
                if not candidates:
                    return None
                if len(candidates) > 1:
                    out.ambiguous[tid] = candidates
                out.by_tech[tid] = candidates[0]
                return candidates[0]
            finally:
                resolving.discard(tid)

        for tid in in_folder:
            resolve(tid)
        return out

    # --------------------------------------------------------------- generation
    def ensure_mod_gui(self, doctrine: bool) -> InterfaceDocRef | None:
        """The mod-side copy of the folder's .gui file (copied on first use)."""
        ref = self.gui_ref_for(doctrine)
        if ref is None:
            return None
        if ref.is_vanilla:
            ref = self.interface.copy_to_mod(ref)
            self.invalidate()
        return ref

    def save_gui(self, doctrine: bool) -> None:
        ref = self.gui_ref_for(doctrine)
        if ref is None or ref.is_vanilla:
            return
        self.interface.save(self.interface.load_gui(ref))

    @staticmethod
    def _clone_node(pair: Pair) -> Pair:
        """Deep-copy one widget pair via serialize→parse."""
        root = pdx_parse(dumps(Block([pair])))
        return next(p for p in root.pairs())

    def ensure_gridbox(self, folder_id: str, name: str, *,
                       origin: tuple[float, float],
                       slotsize: tuple[float, float] = DEFAULT_SLOT,
                       fmt: str = "LEFT") -> GridBoxInfo | None:
        """Add (or return) a gridbox in the folder container. The .gui must
        already be mod-side (call `ensure_mod_gui` first)."""
        view = self.folder_views().get(folder_id)
        if view is None or view.container is None:
            return None
        if name in view.gridboxes:
            return view.gridboxes[name]
        block = Block()
        block.add("name", Scalar(name, quoted=True))
        pos = Block([Pair("x", Scalar(str(int(origin[0])))),
                     Pair("y", Scalar(str(int(origin[1]))))])
        block.add("position", pos)
        block.add("slotsize", Block([Pair("width", Scalar(str(int(slotsize[0])))),
                                     Pair("height", Scalar(str(int(slotsize[1]))))]))
        block.add("format", Scalar(fmt, quoted=True))
        pair = Pair("gridboxtype", block)
        view.container.block.items.append(pair)
        info = GridBoxInfo(name=name, origin=(float(int(origin[0])),
                                              float(int(origin[1]))),
                           slotsize=slotsize, fmt=fmt,
                           node=GuiNode(pair, view.container))
        view.gridboxes[name] = info
        return info

    def rename_gridbox(self, folder_id: str, old_root: str, new_root: str) -> bool:
        """Rename ``<old_root>_tree`` to ``<new_root>_tree`` (rename_tech hook)."""
        view = self.folder_views().get(folder_id)
        if view is None:
            return False
        info = view.gridboxes.pop(f"{old_root}_tree", None)
        if info is None:
            return False
        info.node.set_attr("name", f"{new_root}_tree")
        info.name = f"{new_root}_tree"
        view.gridboxes[info.name] = info
        return True

    def remove_gridbox(self, folder_id: str, name: str) -> bool:
        view = self.folder_views().get(folder_id)
        if view is None or view.container is None:
            return False
        info = view.gridboxes.pop(name, None)
        if info is None:
            return False
        view.container.block.items = [
            it for it in view.container.block.items
            if not (isinstance(it, Pair) and it.value is info.node.block)]
        return True

    def create_folder_gui(self, folder_id: str, *, doctrine: bool = False,
                          template_folder: str = "infantry_folder",
                          tab_sprite: str | None = None) -> None:
        """Generate the interface side of a new folder: container skeleton, tab
        button and item containers, all cloned from `template_folder`. The
        technology_tags entry and the tab sprite are the caller's business."""
        ref = self.ensure_mod_gui(doctrine)
        if ref is None:
            raise RuntimeError("tech tree .gui not found")
        doc = self.interface.load_gui(ref)
        root_name = ROOT_DOCTRINE_WINDOW if doctrine else ROOT_TECH_WINDOW
        root = self._find_window(doc, root_name)
        if root is None:
            raise RuntimeError(f"window {root_name} not found in {ref.rel_file}")

        # The template folder may live in the tech-view file even when we are
        # generating a doctrine folder (vanilla doctrine containers are dummies).
        tpl_ref = self.gui_ref_for(False)
        tpl_doc = self.interface.load_gui(tpl_ref) if tpl_ref is not None else None
        tpl_root = (self._find_window(tpl_doc, ROOT_TECH_WINDOW)
                    if tpl_doc is not None else None)
        tpl = (self._find_child(tpl_root, template_folder)
               if tpl_root is not None else None)
        if tpl is None:
            raise RuntimeError(f"template folder {template_folder} not found")

        existing = self._find_child(root, folder_id)
        if existing is not None and self._has_gridboxes(existing):
            pass                                    # already a working container
        elif existing is not None:
            # vanilla "dummy for legacy reasons" container: swap in the skeleton
            existing.pair.value = self._folder_skeleton(tpl, folder_id).value
        else:
            container = self._folder_skeleton(tpl, folder_id)
            # insert before folder_tabs so tabs stay on top of the z-order
            items = root.block.items
            idx = len(items)
            for i, it in enumerate(items):
                if isinstance(it, Pair) and isinstance(it.value, Block):
                    nm = (it.value.get_scalar_ci("name") or "").strip('"')
                    if nm.lower() == "folder_tabs":
                        idx = i
                        break
            items.insert(idx, container)

        self._add_tab_button(root, tpl_root or root, template_folder, folder_id,
                             tab_sprite)
        self._add_item_containers(doc, root, tpl_doc or doc, tpl_root or root,
                                  template_folder, folder_id)
        self.interface.save(doc)
        self.invalidate()

    @staticmethod
    def _has_gridboxes(node: GuiNode) -> bool:
        queue = node.children()
        while queue:
            child = queue.pop(0)
            if child.type_key.lower() == "gridboxtype":
                return True
            queue.extend(child.children())
        return False

    def _folder_skeleton(self, tpl: GuiNode, folder_id: str) -> Pair:
        """Clone a folder container, strip its gridboxes/labels, rename it."""
        pair = self._clone_node(tpl.pair)
        node = GuiNode(pair)
        node.set_attr("name", folder_id)

        def strip(block: Block) -> None:
            kept: list = []
            for item in block.items:
                if isinstance(item, Pair) and isinstance(item.value, Block):
                    key = item.key.lower()
                    if key == "gridboxtype":
                        continue
                    if key == "instanttextboxtype":
                        continue
                    if key == "icontype":
                        name = (item.value.get_scalar_ci("name") or "").strip('"')
                        if name.startswith("highlight_"):
                            continue
                    if key == "containerwindowtype":
                        strip(item.value)
                kept.append(item)
            block.items = kept

        strip(node.block)
        return pair

    def _add_tab_button(self, root: GuiNode, tpl_root: GuiNode,
                        template_folder: str, folder_id: str,
                        tab_sprite: str | None) -> None:
        tabs = self._find_child(root, "folder_tabs")
        if tabs is None or self._find_child(tabs, f"{folder_id}_tab") is not None:
            return
        tpl_tabs = self._find_child(tpl_root, "folder_tabs")
        tpl_btn = (self._find_child(tpl_tabs, f"{template_folder}_tab")
                   if tpl_tabs is not None else None)
        if tpl_btn is not None:
            pair = self._clone_node(tpl_btn.pair)
        else:
            pair = Pair("buttonType", Block())
        btn = GuiNode(pair)
        btn.set_attr("name", f"{folder_id}_tab")
        btn.set_attr("quadTextureSprite", tab_sprite or f"GFX_{folder_id}_tab")
        # place after the rightmost existing tab
        max_x = 0.0
        step = 89.0
        for child in tabs.children():
            x, _y = child.get_position()
            max_x = max(max_x, x)
        btn.set_position(max_x + step, 0)
        tabs.block.items.append(pair)

    def _add_item_containers(self, doc: GuiDoc, root: GuiNode,
                             tpl_doc: GuiDoc, tpl_root: GuiNode,
                             template_folder: str, folder_id: str) -> None:
        gui_types = doc.gui_types(create=True)
        for suffix in ("_item", "_small_item"):
            new_name = f"techtree_{folder_id}{suffix}"
            if (self._find_window(doc, new_name) is not None
                    or self._find_child(root, new_name) is not None):
                continue
            tpl_name = f"techtree_{template_folder}{suffix}"
            tpl_item = (self._find_window(tpl_doc, tpl_name)
                        or self._find_child(tpl_root, tpl_name))
            if tpl_item is None:
                continue
            pair = self._clone_node(tpl_item.pair)
            GuiNode(pair).set_attr("name", new_name)
            gui_types.items.append(pair)
