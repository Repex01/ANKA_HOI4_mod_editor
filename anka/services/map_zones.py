"""Map zones: supply areas (``map/supplyareas``) and strategic regions
(``map/strategicregions``).

Both are one-object-per-file PDX docs with an ``id`` and a member list block
(``states = { ... }`` / ``provinces = { ... }``); everything else (``value``,
``weather``, naval terrain) is preserved untouched. Mod-first with vanilla
override by exact file name; edits are copy-on-write into the mod.

`StrategicRegionService.sync_provinces` keeps regions consistent when the map
editor creates/removes provinces: every province must belong to exactly one
strategic region, so new provinces produced by splitting inherit the source
province's region automatically.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..core.pdx import Block, Scalar, dump_file, parse_file
from ..domain.mod import ModContext
from ._fsutil import ensure_filename_case

_ID_RE = re.compile(r"^[ \t]*id[ \t]*=[ \t]*(\d+)", re.M)


@dataclass
class ZoneDocRef:
    rel_file: str
    source_root: Path
    is_vanilla: bool
    zone_id: int | None = None

    @property
    def path(self) -> Path:
        return self.source_root / self.rel_file


class ZoneDocument:
    def __init__(self, ref: ZoneDocRef, root: Block, top_key: str,
                 members_key: str):
        self.ref = ref
        self.root = root
        self._top_key = top_key
        self._members_key = members_key

    @property
    def body(self) -> Block | None:
        return self.root.get_block(self._top_key)

    @property
    def zone_id(self) -> int:
        body = self.body
        if body is None:
            return 0
        v = body.get("id")
        return v.as_int(0) if isinstance(v, Scalar) else 0

    @property
    def name(self) -> str:
        body = self.body
        return body.get_scalar("name", "") if body is not None else ""

    @property
    def members(self) -> list[int]:
        body = self.body
        block = body.get_block(self._members_key) if body is not None else None
        if block is None:
            return []
        return [int(v) for v in block.array_values() if v.isdigit()]

    def set_members(self, ids: list[int]) -> None:
        body = self.body
        if body is None:
            return
        block = body.get_block(self._members_key)
        if block is None:
            block = Block()
            body.set(self._members_key, block)
        block.items = [Scalar(str(i)) for i in ids]


class _ZoneService:
    """Shared plumbing: dir scan (mod-first), mtime-cached load, copy-on-write."""

    DIR = ""            # override
    TOP_KEY = ""
    MEMBERS_KEY = ""

    def __init__(self, context: ModContext):
        self.ctx = context
        self._doc_cache: dict[str, tuple[float, ZoneDocument]] = {}

    def list_docs(self) -> list[ZoneDocRef]:
        refs: list[ZoneDocRef] = []
        seen: set[str] = set()
        mod_dir = self.ctx.mod.path / self.DIR
        if mod_dir.is_dir():
            for file in sorted(mod_dir.glob("*.txt")):
                refs.append(ZoneDocRef(f"{self.DIR}/{file.name}",
                                       self.ctx.mod.path, False,
                                       self._quick_id(file)))
                seen.add(file.name.lower())
        game_dir = self.ctx.game_path / self.DIR
        if game_dir.is_dir():
            for file in sorted(game_dir.glob("*.txt")):
                if file.name.lower() in seen:
                    continue
                refs.append(ZoneDocRef(f"{self.DIR}/{file.name}",
                                       self.ctx.game_path, True,
                                       self._quick_id(file)))
        return refs

    @staticmethod
    def _quick_id(file: Path) -> int | None:
        try:
            m = _ID_RE.search(file.read_text(encoding="utf-8-sig",
                                             errors="replace"))
        except OSError:
            return None
        return int(m.group(1)) if m else None

    def load(self, ref: ZoneDocRef) -> ZoneDocument:
        key = str(ref.path).lower()
        try:
            mtime = ref.path.stat().st_mtime
        except OSError:
            mtime = 0.0
        cached = self._doc_cache.get(key)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        doc = ZoneDocument(ref, parse_file(ref.path), self.TOP_KEY,
                           self.MEMBERS_KEY)
        self._doc_cache[key] = (mtime, doc)
        return doc

    def save_doc(self, doc: ZoneDocument) -> None:
        if doc.ref.is_vanilla:
            raise PermissionError("Refusing to write a vanilla zone file")
        target = ensure_filename_case(doc.ref.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        dump_file(doc.root, target)
        try:
            self._doc_cache[str(doc.ref.path).lower()] = \
                (target.stat().st_mtime, doc)
        except OSError:
            pass

    def to_mod(self, doc: ZoneDocument) -> ZoneDocument:
        """Copy-on-write: reopen a vanilla document over its mod copy."""
        if not doc.ref.is_vanilla:
            return doc
        target = self.ctx.mod.path / doc.ref.rel_file
        target.parent.mkdir(parents=True, exist_ok=True)
        target = ensure_filename_case(target)
        if not target.exists():
            target.write_bytes(doc.ref.path.read_bytes())
        return self.load(ZoneDocRef(doc.ref.rel_file, self.ctx.mod.path, False,
                                    doc.ref.zone_id))

    # --------------------------------------------------------------- membership
    def member_map(self) -> dict[int, int]:
        """member id → zone id over all documents."""
        out: dict[int, int] = {}
        for ref in self.list_docs():
            try:
                doc = self.load(ref)
            except Exception:
                continue
            for m in doc.members:
                out.setdefault(m, doc.zone_id)
        return out

    def doc_for_zone(self, zone_id: int) -> ZoneDocument | None:
        for ref in self.list_docs():
            if ref.zone_id == zone_id:
                return self.load(ref)
        return None

    def doc_for_member(self, member_id: int) -> ZoneDocument | None:
        for ref in self.list_docs():
            try:
                doc = self.load(ref)
            except Exception:
                continue
            if member_id in doc.members:
                return doc
        return None

    def move_member(self, member_id: int, zone_id: int) -> list[ZoneDocument]:
        """Put a member into `zone_id`, removing it from its previous zone.
        Returns the touched (saved) documents."""
        touched: list[ZoneDocument] = []
        old = self.doc_for_member(member_id)
        if old is not None and old.zone_id == zone_id:
            return touched
        if old is not None:
            old = self.to_mod(old)
            members = old.members
            members.remove(member_id)
            old.set_members(members)
            self.save_doc(old)
            touched.append(old)
        new = self.doc_for_zone(zone_id)
        if new is not None:
            new = self.to_mod(new)
            members = new.members
            if member_id not in members:
                members.append(member_id)
                new.set_members(members)
                self.save_doc(new)
                touched.append(new)
        return touched


class SupplyAreaService(_ZoneService):
    """``supply_area = { id name value states={...} }``."""

    DIR = "map/supplyareas"
    TOP_KEY = "supply_area"
    MEMBERS_KEY = "states"

    def value_of(self, doc: ZoneDocument) -> int:
        body = doc.body
        if body is None:
            return 0
        v = body.get("value")
        return v.as_int(0) if isinstance(v, Scalar) else 0

    def set_value(self, doc: ZoneDocument, value: int) -> ZoneDocument:
        doc = self.to_mod(doc)
        if doc.body is not None:
            doc.body.set("value", Scalar(str(int(value))))
            self.save_doc(doc)
        return doc

    def create_area(self, area_id: int, value: int = 1) -> ZoneDocument:
        root = Block()
        body = Block()
        body.set("id", Scalar(str(area_id)))
        body.set("name", Scalar(f"SUPPLYAREA_{area_id}", quoted=True))
        body.set("value", Scalar(str(int(value))))
        body.set("states", Block())
        root.set(self.TOP_KEY, body)
        rel = f"{self.DIR}/{area_id}-anka_SupplyArea.txt"
        ref = ZoneDocRef(rel, self.ctx.mod.path, False, area_id)
        doc = ZoneDocument(ref, root, self.TOP_KEY, self.MEMBERS_KEY)
        self.save_doc(doc)
        return doc

    def free_id(self) -> int:
        ids = [ref.zone_id for ref in self.list_docs() if ref.zone_id]
        return max(ids, default=0) + 1


class StrategicRegionService(_ZoneService):
    """``strategic_region = { id name provinces={...} weather={...} }``."""

    DIR = "map/strategicregions"
    TOP_KEY = "strategic_region"
    MEMBERS_KEY = "provinces"

    def sync_provinces(self, added: dict[int, int],
                       removed: list[int]) -> list[ZoneDocument]:
        """Keep regions consistent with province changes.

        `added`: new province id → source province id (the new province joins
        the region of its source, e.g. after a split). `removed`: province ids
        deleted from the map. Returns the touched (saved) documents."""
        touched: list[ZoneDocument] = []
        by_region: dict[int, tuple[ZoneDocument, list[int], set[int]]] = {}

        def slot(doc: ZoneDocument):
            entry = by_region.get(doc.zone_id)
            if entry is None:
                entry = (doc, doc.members, set())
                by_region[doc.zone_id] = entry
            return entry

        for new_pid, source_pid in added.items():
            doc = self.doc_for_member(source_pid)
            if doc is None:
                continue
            _doc, members, _dirty = slot(doc)
            if new_pid not in members:
                members.append(new_pid)
                by_region[doc.zone_id][2].add(new_pid)
        for pid in removed:
            doc = self.doc_for_member(pid)
            if doc is None:
                continue
            _doc, members, dirty = slot(doc)
            if pid in members:
                members.remove(pid)
                dirty.add(pid)
        for doc, members, dirty in by_region.values():
            if not dirty:
                continue
            doc = self.to_mod(doc)
            doc.set_members(members)
            self.save_doc(doc)
            touched.append(doc)
        return touched
