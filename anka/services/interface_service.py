"""Interface files service: ``interface/**/*.gfx`` + ``interface/**/*.gui``.

Lists documents across content layers (`layered_docs`), loads them into typed
docs (`GfxDoc` / `GuiDoc`), saves only mod-side files, and owns the shared
read-side catalogs: the sprite catalog (all sprite kinds, for the renderer),
the legacy resolver (thumbnails), bitmap-font names and the window index used
by the scripted-GUI editor. Copy-to-mod byte-copies the original text so
comments/formatting survive until the file is actually edited and saved.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from ..core.gfx import SpriteCatalog, SpriteResolver
from ..core.guitypes import GuiDoc, SpriteView
from ..core.pdx import Block, Pair, dump_file, parse_file
from ..domain.mod import ModContext
from ._fsutil import ensure_filename_case
from ._layerdocs import layered_docs

INTERFACE_DIR = "interface"
# Sprites created by the editor (imports from the sprite picker) land here
# unless the caller passes another file.
DEFAULT_SPRITES_FILE = "interface/anka_interface.gfx"


def sprite_texture_stem(name: str) -> str:
    """Filename stem for a sprite's imported DDS: the sprite name with any
    leading ``GFX_`` prefix stripped and sanitised (``GFX_donbas_ukr`` →
    ``donbas_ukr``). Naming the texture after the (unique) sprite name — rather
    than the source image's filename — stops identically-named source images in
    different folders from overwriting each other on import."""
    base = re.sub(r"(?i)^gfx_", "", (name or "").strip())
    return re.sub(r"\W+", "_", base).strip("_") or "sprite"


_NAME_RE = re.compile(r'name\s*=\s*"?([^"\s{}]+)"?', re.IGNORECASE)
_KEY_RE = re.compile(r"^[ \t]*([A-Za-z_][\w.]*)[ \t]*=[ \t]*\{")
_BITMAPFONT_RE = re.compile(
    r'bitmapfont\s*=\s*\{[^{}]*?name\s*=\s*"?([^"\s{}]+)"?', re.IGNORECASE | re.DOTALL)


@dataclass
class Issue:
    severity: str            # "error" | "warning"
    code: str
    subject: str
    detail: str = ""
    rel_file: str = ""


@dataclass
class InterfaceDocRef:
    rel_file: str
    source_root: Path
    is_vanilla: bool
    edited: bool = False
    names: list[str] = field(default_factory=list)   # quick-scan entry names

    @property
    def path(self) -> Path:
        return self.source_root / self.rel_file


class GfxDoc:
    """A parsed ``.gfx`` document (``spriteTypes = { ... }`` wrapper)."""

    def __init__(self, ref: InterfaceDocRef, root: Block):
        self.ref = ref
        self.root = root

    def sprite_types(self, create: bool = False) -> Block | None:
        block = self.root.get_block_ci("spriteTypes")
        if block is None and create:
            block = Block()
            self.root.add("spriteTypes", block)
        return block

    def entries(self) -> list[SpriteView]:
        from ..core.guitypes.schema import SPRITE_KINDS
        out: list[SpriteView] = []
        for container in self.root.get_all_ci("spriteTypes"):
            if not isinstance(container, Block):
                continue
            for pair in container.pairs():
                if (pair.key.lower() in SPRITE_KINDS
                        and isinstance(pair.value, Block)):
                    out.append(SpriteView(pair))
        return out

    def add_entry(self, kind_key: str, name: str) -> SpriteView:
        block = Block()
        pair = Pair(kind_key, block)
        self.sprite_types(create=True).items.append(pair)
        view = SpriteView(pair)
        view.set_attr("name", name)
        return view

    def remove_entry(self, view: SpriteView) -> None:
        for container in self.root.get_all_ci("spriteTypes"):
            if isinstance(container, Block):
                container.items = [it for it in container.items
                                   if it is not view.pair]


@dataclass
class WindowInfo:
    """Summary of one top-level window for pickers/validation."""
    name: str
    ref: InterfaceDocRef
    window_index: int
    elements: dict[str, str] = field(default_factory=dict)  # name -> type_key


class InterfaceService:
    def __init__(self, context: ModContext):
        self.ctx = context
        self._doc_cache: dict[str, tuple[float, object]] = {}
        self._catalog: SpriteCatalog | None = None
        self._resolver: SpriteResolver | None = None
        self._fonts: list[str] | None = None
        self._windows: dict[str, WindowInfo] | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ listing
    def list_gfx_docs(self, include_vanilla: bool = True) -> list[InterfaceDocRef]:
        return self._list_docs("*.gfx", include_vanilla)

    def list_gui_docs(self, include_vanilla: bool = True) -> list[InterfaceDocRef]:
        return self._list_docs("*.gui", include_vanilla)

    def _list_docs(self, pattern: str,
                   include_vanilla: bool) -> list[InterfaceDocRef]:
        def scan(root: Path, is_vanilla: bool) -> list[InterfaceDocRef]:
            folder = root / INTERFACE_DIR
            if not folder.is_dir():
                return []
            refs = []
            for file in sorted(folder.rglob(pattern)):
                rel = file.relative_to(root).as_posix()
                refs.append(InterfaceDocRef(
                    rel_file=rel, source_root=root, is_vanilla=is_vanilla,
                    names=self._quick_scan(file, pattern)))
            return refs

        return layered_docs(
            self.ctx, INTERFACE_DIR, scan,
            include_vanilla=include_vanilla,
            set_edited=lambda r: setattr(r, "edited", True),
            sort_key=lambda r: (r.is_vanilla, r.rel_file.lower()))

    @staticmethod
    def _quick_scan(file: Path, pattern: str) -> list[str]:
        """Entry names for the tree without a full parse: sprite names for
        .gfx, top-level window names for .gui (brace counting)."""
        try:
            text = file.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return []
        if pattern == "*.gfx":
            return _NAME_RE.findall(text)
        names: list[str] = []
        depth = 0
        pending: str | None = None
        for line in text.splitlines():
            code = line.split("#", 1)[0]
            if depth == 1:
                m = _KEY_RE.match(code)
                if m:
                    pending = m.group(1)
            if depth == 2 and pending is not None:
                m = _NAME_RE.search(code)
                if m:
                    names.append(m.group(1))
                    pending = None
            depth += code.count("{") - code.count("}")
            if depth < 0:
                depth = 0
        return names

    # ------------------------------------------------------------------ loading
    def load_gfx(self, ref: InterfaceDocRef) -> GfxDoc:
        return self._load(ref, GfxDoc)

    def load_gui(self, ref: InterfaceDocRef) -> GuiDoc:
        return self._load(ref, GuiDoc)

    def _load(self, ref: InterfaceDocRef, doc_cls):
        key = str(ref.path).lower()
        try:
            mtime = ref.path.stat().st_mtime
        except OSError:
            mtime = 0.0
        cached = self._doc_cache.get(key)
        if cached is not None and cached[0] == mtime and isinstance(cached[1], doc_cls):
            return cached[1]
        doc = doc_cls(ref, parse_file(ref.path))
        self._doc_cache[key] = (mtime, doc)
        return doc

    def save(self, doc) -> None:
        if doc.ref.is_vanilla:
            raise PermissionError("Refusing to write a vanilla interface file")
        target = ensure_filename_case(doc.ref.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        dump_file(doc.root, target)
        try:
            self._doc_cache[str(doc.ref.path).lower()] = \
                (target.stat().st_mtime, doc)
        except OSError:
            pass
        self._windows = None
        if self._catalog is not None and isinstance(doc, GfxDoc):
            for view in doc.entries():
                self._catalog.add(view, self.ctx.mod.path)

    def copy_to_mod(self, ref: InterfaceDocRef) -> InterfaceDocRef:
        if not ref.is_vanilla:
            return ref
        target = self.ctx.mod.path / ref.rel_file
        target.parent.mkdir(parents=True, exist_ok=True)
        target = ensure_filename_case(target)
        if not target.exists():
            target.write_bytes(ref.path.read_bytes())
        return InterfaceDocRef(rel_file=ref.rel_file,
                               source_root=self.ctx.mod.path, is_vanilla=False,
                               edited=True, names=list(ref.names))

    def create_doc(self, rel_file: str, wrapper_key: str) -> InterfaceDocRef:
        """New mod-side file with an empty wrapper (``spriteTypes``/``guiTypes``)."""
        ref = InterfaceDocRef(rel_file=rel_file, source_root=self.ctx.mod.path,
                              is_vanilla=False)
        root = Block()
        root.add(wrapper_key, Block())
        doc = (GfxDoc if wrapper_key == "spriteTypes" else GuiDoc)(ref, root)
        self._doc_cache[str(ref.path).lower()] = (0.0, doc)
        return ref

    def delete_file(self, ref: InterfaceDocRef) -> None:
        if ref.is_vanilla:
            raise PermissionError("Refusing to delete a vanilla file")
        if ref.path.exists():
            ref.path.unlink()
        self._doc_cache.pop(str(ref.path).lower(), None)
        self._windows = None

    # ----------------------------------------------------------------- catalogs
    def sprite_catalog(self) -> SpriteCatalog:
        with self._lock:
            if self._catalog is None:
                self._catalog = SpriteCatalog.for_mod(
                    self.ctx.mod.path, self.ctx.game_path,
                    self.ctx.dependency_paths)
        return self._catalog

    def resolver(self) -> SpriteResolver:
        with self._lock:
            if self._resolver is None:
                self._resolver = SpriteResolver.for_mod(
                    self.ctx.mod.path, self.ctx.game_path,
                    self.ctx.dependency_paths)
        return self._resolver

    def warm(self) -> None:
        """Build the heavy catalogs (call from a background thread)."""
        self.sprite_catalog().warm()
        self.resolver().resolve("")
        self.font_names()

    def font_names(self) -> list[str]:
        if self._fonts is None:
            names: set[str] = set()
            for root in self.ctx.override_roots(INTERFACE_DIR):
                folder = root / INTERFACE_DIR
                if not folder.is_dir():
                    continue
                for gfx in folder.rglob("*.gfx"):
                    try:
                        text = gfx.read_text(encoding="utf-8-sig",
                                             errors="replace")
                    except OSError:
                        continue
                    if "bitmapfont" not in text:
                        continue
                    names.update(_BITMAPFONT_RE.findall(text))
            self._fonts = sorted(names)
        return self._fonts

    def window_index(self, refresh: bool = False) -> dict[str, WindowInfo]:
        """Window name → summary across all layers (mod wins), for the
        scripted-GUI pickers and cross-file validation."""
        if self._windows is None or refresh:
            index: dict[str, WindowInfo] = {}
            for ref in reversed(self.list_gui_docs(include_vanilla=True)):
                try:
                    doc = self.load_gui(ref)
                except Exception:
                    continue
                for w_index, window in enumerate(doc.windows()):
                    name = window.name
                    if not name:
                        continue
                    elements: dict[str, str] = {}

                    def collect(node) -> None:
                        for child in node.children():
                            if child.name:
                                elements[child.name] = child.type_key
                            collect(child)

                    collect(window)
                    index[name] = WindowInfo(name, ref, w_index, elements)
            self._windows = index
        return self._windows

    # ------------------------------------------------------------ sprite import
    def suggest_sprite_name(self, source: Path) -> str:
        """A free ``GFX_<stem>`` name for an imported texture."""
        stem = re.sub(r"\W+", "_", Path(source).stem).strip("_") or "sprite"
        base = f"GFX_{stem}"
        catalog = self.sprite_catalog()
        name, i = base, 2
        while catalog.get(name) is not None:
            name = f"{base}_{i}"
            i += 1
        return name

    def import_sprite(self, source: Path, name: str,
                      gfx_rel: str = DEFAULT_SPRITES_FILE) -> str:
        """Convert an image into a mod DDS + a registered sprite (the
        focus-icon flow generalized: no forced resize, interface folder,
        ``anka_interface.gfx`` by default). Returns the sprite name."""
        from ..core.gfx import SpriteRegistry
        from ..core.images.converter import ImageConverter

        source = Path(source)
        # Name the DDS after the sprite (unique), not the source file — otherwise
        # two provinces whose source images share a filename overwrite each other.
        rel_texture = f"gfx/interface/{sprite_texture_stem(name)}.dds"
        img = ImageConverter.load(source)
        dds = ImageConverter.save_dds(img, self.ctx.mod.path / rel_texture)

        registry = SpriteRegistry(self.ctx.mod.path / gfx_rel)
        registry.register(name, rel_texture).save()

        # keep the live catalogs in sync without a full rescan
        self.resolver().add(name, dds)
        catalog = self.sprite_catalog()
        for container in registry.root.get_all_ci("spriteTypes"):
            if not isinstance(container, Block):
                continue
            for pair in container.pairs():
                if isinstance(pair.value, Block):
                    view = SpriteView(pair)
                    if view.name == name:
                        catalog.add(view, self.ctx.mod.path)
        return name

    # --------------------------------------------------------------- validation
    def validate_gfx(self, docs: list[GfxDoc]) -> list[Issue]:
        issues: list[Issue] = []
        seen: dict[str, str] = {}
        for doc in docs:
            root = doc.ref.source_root
            for view in doc.entries():
                name = view.name
                rel = doc.ref.rel_file
                if not name:
                    issues.append(Issue("error", "no_name", view.type_key,
                                        rel_file=rel))
                    continue
                if name in seen and seen[name] != rel:
                    issues.append(Issue("warning", "duplicate_name", name,
                                        detail=seen[name], rel_file=rel))
                seen.setdefault(name, rel)
                texture = view.texture
                if texture:
                    path = root / texture.replace("\\", "/")
                    if not path.exists():
                        issues.append(Issue("error", "missing_texture", name,
                                            detail=texture, rel_file=rel))
                elif view.spec is not None and view.type_key.lower() not in (
                        "piecharttype", "linecharttype"):
                    issues.append(Issue("warning", "no_texture", name,
                                        rel_file=rel))
        return issues
