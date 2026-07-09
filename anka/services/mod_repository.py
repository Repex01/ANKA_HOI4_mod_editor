"""Discover and load mods from the configured sources.

Two sources, one model:
  * **local** — ``Documents/.../mod`` holds ``<name>.mod`` descriptors whose ``path=``
    field points at the content folder.
  * **workshop** — ``steamapps/workshop/content/394360/<id>`` folders each contain a
    ``descriptor.mod`` and the content inline.

Descriptors are themselves Paradox script, so the PDX parser reads them.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..config.settings import Settings
from ..core.pdx import Block, Scalar, parse_file
from ..domain.mod import Mod


class ModRepository:
    def __init__(self, settings: Settings):
        self._settings = settings

    def list_mods(self) -> list[Mod]:
        # Workshop folders are the source of truth for installed mods; discover them
        # first so that a launcher-generated local pointer (.mod with the same
        # remote_file_id) de-duplicates against the real workshop entry.
        mods: list[Mod] = []
        mods.extend(self._discover_workshop())
        mods.extend(self._discover_local())
        seen: set[str] = set()
        unique: list[Mod] = []
        for mod in mods:
            if mod.id not in seen:
                seen.add(mod.id)
                unique.append(mod)
        return unique

    # --- local -----------------------------------------------------------
    def _discover_local(self) -> list[Mod]:
        root = Path(self._settings.local_mods_path)
        if not root.is_dir():
            return []
        workshop_root = Path(self._settings.workshop_mods_path) if self._settings.workshop_mods_path else None
        mods: list[Mod] = []
        for desc in root.glob("*.mod"):
            try:
                meta = _parse_descriptor(desc)
            except Exception:
                continue
            is_local, mod_id, content = self._classify_local(desc, meta, root, workshop_root)
            if content is None:
                continue
            mods.append(
                Mod(
                    id=mod_id,
                    name=meta.get("name") or desc.stem,
                    path=content,
                    descriptor_path=desc,
                    picture=meta.get("picture", ""),
                    tags=meta.get("tags", []),
                    version=meta.get("version", ""),
                    supported_version=meta.get("supported_version", ""),
                    remote_file_id=meta.get("remote_file_id", ""),
                    is_local=is_local,
                    dependencies=meta.get("dependencies", []),
                    replace_paths=meta.get("replace_paths", []),
                )
            )
        return mods

    @staticmethod
    def _classify_local(desc: Path, meta: dict, root: Path, workshop_root: Path | None):
        """Decide whether a local .mod is genuinely local or a workshop pointer.

        The launcher drops a ``.mod`` into the local folder for every subscribed mod;
        such descriptors carry a ``remote_file_id`` and/or a ``path``/``archive`` that
        resolves inside the workshop directory. Those are workshop mods — keyed by their
        remote id so they merge with the workshop discovery rather than duplicating it.

        Returns ``(is_local, id, content_path|None)``.
        """
        remote = meta.get("remote_file_id", "")
        content = _resolve_path(meta.get("path", ""), root)

        # The `path` is decisive: a descriptor whose content lives in the local mod
        # folder is a genuine local mod, even if it also carries a remote_file_id
        # (e.g. a local working copy of a published mod).
        if content is not None and _is_under(content, root):
            return True, desc.stem, content

        in_workshop = _is_under(content, workshop_root) or _is_under(
            _resolve_path(meta.get("archive", ""), root), workshop_root
        )
        if in_workshop or remote:
            mod_id = remote or (content.name if content else desc.stem)
            # Zipped/subscribed mods extract into ``<workshop>/<remote_id>``.
            if (content is None or not _is_under(content, workshop_root)) and workshop_root is not None:
                candidate = workshop_root / mod_id
                if candidate.is_dir():
                    content = candidate
            return False, mod_id, content

        # `path` points to some other location (custom absolute local path).
        if content is not None:
            return True, desc.stem, content
        sibling = root / desc.stem
        return True, desc.stem, (sibling if sibling.is_dir() else None)

    # --- workshop --------------------------------------------------------
    def _discover_workshop(self) -> list[Mod]:
        root = Path(self._settings.workshop_mods_path)
        if not root.is_dir():
            return []
        mods: list[Mod] = []
        for folder in root.iterdir():
            if not folder.is_dir():
                continue
            desc = folder / "descriptor.mod"
            meta = {}
            if desc.exists():
                try:
                    meta = _parse_descriptor(desc)
                except Exception:
                    meta = {}
            mods.append(
                Mod(
                    id=folder.name,
                    name=meta.get("name") or folder.name,
                    path=folder,
                    descriptor_path=desc if desc.exists() else None,
                    picture=meta.get("picture", ""),
                    tags=meta.get("tags", []),
                    version=meta.get("version", ""),
                    supported_version=meta.get("supported_version", ""),
                    remote_file_id=meta.get("remote_file_id", folder.name),
                    is_local=False,
                    dependencies=meta.get("dependencies", []),
                    replace_paths=meta.get("replace_paths", []),
                )
            )
        return mods

    def get(self, mod_id: str) -> Mod | None:
        return next((m for m in self.list_mods() if m.id == mod_id), None)

    # --- dependencies ----------------------------------------------------
    def resolve_dependencies(self, mod: Mod) -> list[Mod]:
        """Map a mod's ``dependencies`` (mod *names*) to the installed mods that
        provide them, in the descriptor's load order. Names that match no installed
        mod — or that would point back at ``mod`` itself — are skipped (they simply
        contribute no override layer)."""
        if not mod.dependencies:
            return []
        by_name: dict[str, Mod] = {}
        for candidate in self.list_mods():
            by_name.setdefault(candidate.name, candidate)
        resolved: list[Mod] = []
        for name in mod.dependencies:
            target = by_name.get(name)
            if target is not None and target.id != mod.id:
                resolved.append(target)
        return resolved

    def save_dependencies(self, mod: Mod, names: list[str]) -> None:
        """Persist ``dependencies`` back to the mod's ``.mod`` descriptor and update
        the in-memory `mod`. Rewrites only the dependencies block, preserving the rest
        of the descriptor (comments, field order, formatting)."""
        desc = mod.descriptor_path
        if desc is None or not desc.exists():
            raise FileNotFoundError("mod has no writable descriptor")
        text = desc.read_text(encoding="utf-8-sig")
        if names:
            body = "\n".join(f'\t"{n}"' for n in names)
            block = "dependencies = {\n" + body + "\n}"
        else:
            block = ""
        # Values are quoted strings, so the block never contains a nested "}".
        pattern = re.compile(r"[ \t]*dependencies\s*=\s*\{[^}]*\}[ \t]*\n?",
                             re.IGNORECASE)
        if pattern.search(text):
            replacement = (block + "\n") if block else ""
            new_text = pattern.sub(replacement, text, count=1)
        elif block:
            sep = "" if text.endswith("\n") or not text else "\n"
            new_text = text + sep + block + "\n"
        else:
            new_text = text
        desc.write_text(new_text, encoding="utf-8")
        mod.dependencies = list(names)


def _resolve_path(raw: str, base: Path) -> Path | None:
    """Resolve a descriptor path/archive string to an existing directory, if any.

    `path=` may name a folder; `archive=` names a .zip — in both cases we only need the
    containing directory to test workshop membership, so a file resolves to its parent.
    """
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = base / raw
    if p.is_dir():
        return p
    if p.suffix and p.parent.is_dir():  # e.g. an archive .zip
        return p.parent
    return None


def _is_under(path: Path | None, ancestor: Path | None) -> bool:
    if path is None or ancestor is None:
        return False
    try:
        path.resolve().relative_to(ancestor.resolve())
        return True
    except (ValueError, OSError):
        return False


def _parse_descriptor(path: Path) -> dict:
    """Extract descriptor fields into a plain dict via the PDX parser.

    ``replace_path`` is special: HOI4 allows it to appear many times (one folder each),
    so every occurrence is collected into the ``replace_paths`` list rather than the
    later ones clobbering the earlier."""
    root: Block = parse_file(path)
    out: dict = {}
    replace_paths: list[str] = []
    for pair in root.pairs():
        value = pair.value
        if pair.key == "replace_path" and isinstance(value, Scalar):
            replace_paths.append(value.raw)
        elif isinstance(value, Scalar):
            out[pair.key] = value.raw
        elif isinstance(value, Block):  # tags = { ... }
            out[pair.key] = value.array_values()
    if replace_paths:
        out["replace_paths"] = replace_paths
    return out
