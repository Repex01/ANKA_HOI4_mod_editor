"""Read/write the post-AAT doctrine system (``common/doctrines``).

Four kinds of database objects, each in its own subdirectory: ``folders`` (tab
categories), ``grand_doctrines`` (XP-unlocked folder roots that list *tracks*
and pair each track with a *milestone*), ``subdoctrines`` (XP-unlocked track
roots with a sequence of *rewards*) and ``tracks`` (slot definitions with
mastery conditions). Milestones and rewards are **anonymous blocks** — they
live in ``Block.items`` (not ``pairs()``) and are paired with tracks by order.

Views are block-backed like the technology model: unknown keys round-trip.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config.constants import GAME_DIRS
from ..core.pdx import Block, Pair, Scalar, dump_file, dumps, parse_file
from ..core.pdx import parse as pdx_parse
from ..domain.mod import ModContext
from ._fsutil import ensure_filename_case
from ._layerdocs import layered_docs
from .technology_service import Issue

KINDS = ("folders", "grand_doctrines", "subdoctrines", "tracks")
_ID_RE = re.compile(r"^\w+$")

# Scalar/script fields per kind (everything else in a grand/sub body is
# "activation effects" and edited as free script).
_SCRIPTS = {
    "folders": ("allowed",),
    "grand_doctrines": ("available", "visible", "ai_will_do"),
    "subdoctrines": ("available", "visible", "ai_will_do", "mastery"),
    "tracks": ("active", "mastery"),
}


class _Entry:
    """Shared plumbing: a view over one ``<id> = { ... }`` pair."""

    def __init__(self, pair: Pair):
        self.pair = pair

    @property
    def block(self) -> Block:
        return self.pair.value

    @property
    def id(self) -> str:
        return self.pair.key

    @id.setter
    def id(self, value: str) -> None:
        self.pair.key = value

    def get(self, key: str) -> str | None:
        return self.block.get_scalar_ci(key)

    def set(self, key: str, value: str | None) -> None:
        if value is None or value == "":
            self.block.remove_ci(key)
        else:
            self.block.set_ci(key, value)

    def get_num(self, key: str) -> float | None:
        v = self.block.get_ci(key)
        return v.as_float() if isinstance(v, Scalar) else None

    def set_num(self, key: str, value: float | None) -> None:
        if value is None:
            self.block.remove_ci(key)
        else:
            value = round(float(value), 3)
            self.block.set_ci(key, int(value) if value == int(value) else value)

    def get_script(self, key: str) -> str:
        v = self.block.get_ci(key)
        if isinstance(v, Block):
            return dumps(v, top_level=False)
        if isinstance(v, Scalar):
            return v.raw
        return ""

    def set_script(self, key: str, text: str) -> None:
        if not text.strip():
            self.block.remove_ci(key)
            return
        parsed = pdx_parse(text, recover=False)
        if len(parsed.items) == 1 and isinstance(parsed.items[0], Scalar):
            self.block.set_ci(key, parsed.items[0])
        else:
            self.block.set_ci(key, Block(parsed.items))

    def _get_array(self, key: str) -> list[str]:
        blk = self.block.get_block_ci(key)
        return blk.array_values() if blk is not None else []

    def _set_array(self, key: str, values: list[str]) -> None:
        if not values:
            self.block.remove_ci(key)
            return
        blk = Block()
        for v in values:
            blk.add_value(v)
        self.block.set_ci(key, blk)

    @staticmethod
    def _anon_blocks(container: Block | None) -> list[Block]:
        """Anonymous ``{ ... }`` children — Block items without a key."""
        if container is None:
            return []
        return [it for it in container.items if isinstance(it, Block)]

    @staticmethod
    def _list_items(container: Block | None) -> list[tuple[str, Block]]:
        """Ordered child blocks, named or anonymous: milestones are anonymous
        (paired with tracks by order), vanilla rewards are named (the name is
        part of the reward's loc key ``<subdoctrine>_<reward>``)."""
        if container is None:
            return []
        out: list[tuple[str, Block]] = []
        for it in container.items:
            if isinstance(it, Block):
                out.append(("", it))
            elif isinstance(it, Pair) and isinstance(it.value, Block):
                out.append((it.key, it.value))
        return out

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.id!r})"


class DoctrineFolder(_Entry):
    KIND = "folders"


class GrandDoctrine(_Entry):
    KIND = "grand_doctrines"

    @property
    def folder(self) -> str:
        return self.get("folder") or ""

    @property
    def tracks(self) -> list[str]:
        return self._get_array("tracks")

    @tracks.setter
    def tracks(self, values: list[str]) -> None:
        self._set_array("tracks", values)

    def milestones(self) -> list[tuple[str, Block]]:
        return self._list_items(self.block.get_block_ci("milestones"))

    def milestones_container(self, create: bool = False) -> Block | None:
        blk = self.block.get_block_ci("milestones")
        if blk is None and create:
            blk = Block()
            self.block.set_ci("milestones", blk)
        return blk


class Subdoctrine(_Entry):
    KIND = "subdoctrines"

    @property
    def tracks(self) -> list[str]:
        """The ``track`` field: a scalar or a block list."""
        v = self.block.get_ci("track")
        if isinstance(v, Scalar):
            return [v.raw]
        if isinstance(v, Block):
            return v.array_values()
        return []

    @tracks.setter
    def tracks(self, values: list[str]) -> None:
        if not values:
            self.block.remove_ci("track")
        elif len(values) == 1:
            self.block.set_ci("track", values[0])
        else:
            blk = Block()
            for v in values:
                blk.add_value(v)
            self.block.set_ci("track", blk)

    @property
    def xor(self) -> list[str]:
        return self._get_array("xor")

    @xor.setter
    def xor(self, values: list[str]) -> None:
        self._set_array("xor", values)

    def rewards(self) -> list[tuple[str, Block]]:
        return self._list_items(self.block.get_block_ci("rewards"))

    def rewards_container(self, create: bool = False) -> Block | None:
        blk = self.block.get_block_ci("rewards")
        if blk is None and create:
            blk = Block()
            self.block.set_ci("rewards", blk)
        return blk


class DoctrineTrack(_Entry):
    KIND = "tracks"


_VIEWS = {"folders": DoctrineFolder, "grand_doctrines": GrandDoctrine,
          "subdoctrines": Subdoctrine, "tracks": DoctrineTrack}


@dataclass
class DoctrineDocRef:
    kind: str
    rel_file: str
    source_root: Path
    is_vanilla: bool
    edited: bool = False
    ids: list[str] = field(default_factory=list)

    @property
    def path(self) -> Path:
        return self.source_root / self.rel_file

    @property
    def name(self) -> str:
        return Path(self.rel_file).stem


@dataclass
class DoctrineDocument:
    ref: DoctrineDocRef
    root: Block
    entries: list[_Entry] = field(default_factory=list)

    def find(self, eid: str) -> _Entry | None:
        for e in self.entries:
            if e.id == eid:
                return e
        return None


class DoctrineService:
    BASE = GAME_DIRS.DOCTRINES

    def __init__(self, context: ModContext):
        self.ctx = context
        self._doc_cache: dict[Path, tuple[float, DoctrineDocument]] = {}

    def exists(self) -> bool:
        """Does any content layer ship the new doctrine system at all?"""
        return any((root / self.BASE).is_dir()
                   for root in self.ctx.override_roots(self.BASE))

    # --- listing -----------------------------------------------------------
    def list_docs(self, kind: str,
                  include_vanilla: bool = True) -> list[DoctrineDocRef]:
        rel_dir = f"{self.BASE}/{kind}"

        def scan(root: Path, is_vanilla: bool) -> list[DoctrineDocRef]:
            folder = root / rel_dir
            if not folder.is_dir():
                return []
            refs = []
            for file in sorted(folder.rglob("*.txt")):
                rel = file.relative_to(root).as_posix()
                try:
                    text = file.read_text(encoding="utf-8-sig", errors="replace")
                except OSError:
                    continue
                refs.append(DoctrineDocRef(
                    kind=kind, rel_file=rel, source_root=root,
                    is_vanilla=is_vanilla, ids=self._scan_ids(text)))
            return refs

        return layered_docs(
            self.ctx, rel_dir, scan, include_vanilla=include_vanilla,
            set_edited=lambda r: setattr(r, "edited", True),
            sort_key=lambda r: (r.is_vanilla, r.rel_file.lower()))

    @staticmethod
    def _scan_ids(text: str) -> list[str]:
        text = re.sub(r"#[^\n]*", "", text)
        ids: list[str] = []
        depth = 0
        for m in re.finditer(r"([A-Za-z0-9_.\-]+)\s*=\s*\{|\{|\}", text):
            name = m.group(1)
            if name is not None:
                if depth == 0:
                    ids.append(name)
                depth += 1
            elif m.group(0) == "{":
                depth += 1
            else:
                depth = max(0, depth - 1)
        return ids

    # --- load / save --------------------------------------------------------
    def load(self, ref: DoctrineDocRef) -> DoctrineDocument:
        path = ref.path
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        cached = self._doc_cache.get(path)
        if cached and cached[0] == mtime:
            doc = cached[1]
            doc.ref = ref
            return doc
        doc = self._build_document(ref, parse_file(path))
        self._doc_cache[path] = (mtime, doc)
        return doc

    @staticmethod
    def _build_document(ref: DoctrineDocRef, root: Block) -> DoctrineDocument:
        view = _VIEWS[ref.kind]
        doc = DoctrineDocument(ref=ref, root=root)
        doc.entries = [view(p) for p in root.pairs() if isinstance(p.value, Block)]
        return doc

    def document_from_text(self, ref: DoctrineDocRef, text: str) -> DoctrineDocument:
        doc = self._build_document(ref, pdx_parse(text))
        try:
            self._doc_cache[ref.path] = (ref.path.stat().st_mtime, doc)
        except OSError:
            self._doc_cache[ref.path] = (0.0, doc)
        return doc

    def save(self, doc: DoctrineDocument) -> Path:
        if doc.ref.is_vanilla:
            raise PermissionError("Refusing to write into the base game")
        path = ensure_filename_case(doc.ref.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        dump_file(doc.root, path)
        try:
            self._doc_cache[path] = (path.stat().st_mtime, doc)
        except OSError:
            pass
        return path

    def copy_to_mod(self, ref: DoctrineDocRef) -> DoctrineDocRef:
        if not ref.is_vanilla:
            return ref
        target = ensure_filename_case(self.ctx.mod.path / ref.rel_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(ref.path.read_bytes())
        return DoctrineDocRef(kind=ref.kind, rel_file=ref.rel_file,
                              source_root=self.ctx.mod.path, is_vanilla=False,
                              edited=True, ids=list(ref.ids))

    def create_doc(self, kind: str, file_name: str) -> DoctrineDocRef:
        if not file_name.endswith(".txt"):
            file_name += ".txt"
        rel = f"{self.BASE}/{kind}/{file_name}"
        path = self.ctx.mod.path / rel
        if path.exists():
            raise FileExistsError(file_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        dump_file(Block(), path)
        return DoctrineDocRef(kind=kind, rel_file=rel,
                              source_root=self.ctx.mod.path, is_vanilla=False)

    # --- aggregation ---------------------------------------------------------
    def entries(self, kind: str) -> dict[str, tuple[DoctrineDocRef, _Entry]]:
        """id -> (ref, view) across layers (higher layer wins)."""
        out: dict[str, tuple[DoctrineDocRef, _Entry]] = {}
        for ref in self.list_docs(kind):
            try:
                doc = self.load(ref)
            except Exception:
                continue
            for entry in doc.entries:
                out.setdefault(entry.id, (ref, entry))
        return out

    def add_entry(self, doc: DoctrineDocument, eid: str) -> _Entry:
        if not _ID_RE.match(eid):
            raise ValueError(f"Invalid id: {eid!r}")
        if eid in self.entries(doc.ref.kind):
            raise ValueError(f"Duplicate id: {eid}")
        pair = Pair(eid, Block())
        doc.root.items.append(pair)
        entry = _VIEWS[doc.ref.kind](pair)
        doc.entries.append(entry)
        return entry

    def remove_entry(self, doc: DoctrineDocument, entry: _Entry) -> None:
        doc.root.items = [it for it in doc.root.items
                          if not (isinstance(it, Pair) and it is entry.pair)]
        if entry in doc.entries:
            doc.entries.remove(entry)

    # --- validation ------------------------------------------------------------
    def validate(self) -> list[Issue]:
        issues: list[Issue] = []
        folders = self.entries("folders")
        tracks = self.entries("tracks")
        grands = self.entries("grand_doctrines")
        subs = self.entries("subdoctrines")

        for gid, (_ref, grand) in grands.items():
            if grand.folder and grand.folder not in folders:
                issues.append(Issue("error", "doc_unknown_folder", gid,
                                    grand.folder))
            g_tracks = grand.tracks
            for tid in g_tracks:
                if tid not in tracks:
                    issues.append(Issue("error", "doc_unknown_track", gid, tid))
            milestones = grand.milestones()
            if milestones and len(milestones) != len(g_tracks):
                issues.append(Issue("warning", "doc_milestone_count", gid,
                                    f"{len(milestones)} ≠ {len(g_tracks)}"))
            xp_type = grand.get("xp_type")
            if xp_type and xp_type not in ("army", "navy", "air"):
                issues.append(Issue("error", "doc_bad_xp_type", gid, xp_type))

        for sid, (_ref, sub) in subs.items():
            for tid in sub.tracks:
                if tid not in tracks:
                    issues.append(Issue("error", "doc_unknown_track", sid, tid))
            for other in sub.xor:
                if other not in subs:
                    issues.append(Issue("warning", "doc_broken_xor", sid, other))
            xp_type = sub.get("xp_type")
            if xp_type and xp_type not in ("army", "navy", "air"):
                issues.append(Issue("error", "doc_bad_xp_type", sid, xp_type))
        return issues
