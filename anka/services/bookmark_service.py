"""Bookmarks (``common/bookmarks``): the scenarios shown on the start screen.

A bookmark file holds one or more ``bookmark = { ... }`` blocks. Besides the
scenario's own metadata (name, date, picture) each block lists the selectable
countries as ``"TAG" = { ... }`` entries. Countries without ``minor = yes`` are
the featured ones — the large portraits on top of the country-selection screen;
everything else is listed below. The order of the entries is the order the game
displays them in, which is why the editor lets you drag them around.

Content is read across the layers (base game → dependency mods → edited mod) and
only ever written into the edited mod: a vanilla file is copied in first, so the
base game stays untouched.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..core.pdx import Block, Pair, Scalar, dump_file, parse_file
from ..domain.mod import ModContext

DIR = "common/bookmarks"

# Keys inside a country entry that are not free-form country data.
_COUNTRY_META = ("minor",)


# Three-letter uppercase words that are script keywords, not country tags. A
# bookmark carries conditions such as ``NOT = { has_dlc = ... }``, and treating
# those as countries would list them on the selection screen — and reorder them
# out of the block they belong to.
_NOT_TAGS = {"NOT", "AND", "OR", "NOR", "ANY", "ALL"}


def _looks_like_tag(key: str) -> bool:
    """Country entries are 3-letter tags; everything else is bookmark metadata."""
    key = key.strip('"')
    if key in _NOT_TAGS:
        return False
    return len(key) == 3 and key.isupper() and key.isalnum()


@dataclass
class BookmarkCountryEntry:
    """One selectable country entry inside a bookmark.

    A tag may legitimately appear more than once: the base game lists the majors
    twice, gated on DLC, so owners see different preview focuses than everyone
    else. Entries are therefore identified by position, never by tag alone.
    """

    tag: str
    block: Block
    index: int = 0

    @property
    def uid(self) -> str:
        return f"{self.tag}#{self.index}"

    @property
    def minor(self) -> bool:
        value = self.block.get_scalar("minor", "")
        return str(value).strip('"').lower() in ("yes", "true")

    def set_minor(self, minor: bool) -> None:
        if minor:
            self.block.set("minor", Scalar("yes"))
        else:
            self.block.remove("minor")

    def _text(self, key: str) -> str:
        return str(self.block.get_scalar(key, "") or "").strip('"')

    def _set_text(self, key: str, value: str, quoted: bool = False) -> None:
        value = (value or "").strip()
        if value:
            self.block.set(key, Scalar(f'"{value}"' if quoted else value))
        else:
            self.block.remove(key)

    @property
    def history(self) -> str:
        return self._text("history")

    def set_history(self, value: str) -> None:
        self._set_text("history", value, quoted=True)

    @property
    def ideology(self) -> str:
        return self._text("ideology")

    def set_ideology(self, value: str) -> None:
        self._set_text("ideology", value)

    def _list(self, key: str) -> list[str]:
        block = self.block.get_block(key)
        return list(block.array_values()) if block is not None else []

    def _set_list(self, key: str, values: list[str]) -> None:
        values = [v for v in values if v]
        if not values:
            self.block.remove(key)
            return
        block = self.block.get_block(key)
        if block is None:
            block = Block()
            self.block.set(key, block)
        block.items = [Scalar(v) for v in values]

    @property
    def focuses(self) -> list[str]:
        return self._list("focuses")

    def set_focuses(self, values: list[str]) -> None:
        self._set_list("focuses", values)

    @property
    def ideas(self) -> list[str]:
        return self._list("ideas")

    def set_ideas(self, values: list[str]) -> None:
        self._set_list("ideas", values)


@dataclass
class BookmarkEntry:
    """One ``bookmark = { ... }`` block of a file."""

    block: Block

    def _text(self, key: str) -> str:
        return str(self.block.get_scalar(key, "") or "").strip('"')

    @property
    def name(self) -> str:
        return self._text("name")

    @property
    def date(self) -> str:
        return self._text("date")

    @property
    def default_country(self) -> str:
        return self._text("default_country")

    def set_default_country(self, tag: str) -> None:
        if tag:
            self.block.set("default_country", Scalar(f'"{tag}"'))

    def countries(self) -> list[BookmarkCountryEntry]:
        """Every country entry, in file order — duplicates included.

        Dropping a repeated tag would delete the base game's DLC variants and
        make those countries vanish from the selection screen.
        """
        out: list[BookmarkCountryEntry] = []
        for i, pair in enumerate(self.block.pairs()):
            if _looks_like_tag(pair.key) and isinstance(pair.value, Block):
                out.append(BookmarkCountryEntry(pair.key.strip('"'), pair.value,
                                                len(out)))
        return out

    def duplicate_tags(self) -> list[str]:
        """Tags with more than one entry — informational, not an error."""
        counts: dict[str, int] = {}
        for c in self.countries():
            counts[c.tag] = counts.get(c.tag, 0) + 1
        return sorted(t for t, n in counts.items() if n > 1)

    def set_entry_order(self, uids: list[str]) -> None:
        """Reorder the country entries, addressed by uid (tag#index).

        Every existing entry is written back exactly once — nothing is merged
        and nothing is dropped, so DLC duplicates survive a reorder.
        """
        by_uid = {c.uid: c for c in self.countries()}
        ordered: list[BookmarkCountryEntry] = []
        seen: set[str] = set()
        for uid in uids:
            entry = by_uid.get(uid)
            if entry is not None and uid not in seen:
                seen.add(uid)
                ordered.append(entry)
        for uid, entry in by_uid.items():          # safety net
            if uid not in seen:
                ordered.append(entry)

        head: list = []
        tail: list = []
        first_seen = False
        for item in list(self.block.items):
            is_country = (isinstance(item, Pair) and _looks_like_tag(item.key)
                          and isinstance(item.value, Block))
            if is_country:
                first_seen = True
                continue
            (tail if first_seen else head).append(item)
        self.block.items = (head
                            + [Pair(f'"{e.tag}"', e.block) for e in ordered]
                            + tail)

    def set_country_order(self, tags: list[str]) -> None:
        """Rewrite the country entries in `tags` order, keeping metadata in place.

        The rewritten list goes back where the countries were, not at the end of
        the block: a bookmark can carry trailing keys, and moving the countries
        past them changes what the game reads. Duplicates are dropped, and any
        country missing from `tags` is kept rather than silently deleted.
        """
        by_tag = {c.tag: c for c in self.countries()}
        ordered: list[str] = []
        seen: set[str] = set()
        for tag in tags:
            if tag in by_tag and tag not in seen:
                seen.add(tag)
                ordered.append(tag)
        for tag in by_tag:                    # safety net: never lose an entry
            if tag not in seen:
                ordered.append(tag)

        head: list = []
        tail: list = []
        first_seen = False
        for item in list(self.block.items):
            is_country = (isinstance(item, Pair) and _looks_like_tag(item.key)
                          and isinstance(item.value, Block))
            if is_country:
                first_seen = True
                continue
            (tail if first_seen else head).append(item)
        self.block.items = (head
                            + [Pair(f'"{t}"', by_tag[t].block) for t in ordered]
                            + tail)

    def add_country(self, tag: str, minor: bool = True) -> BookmarkCountryEntry:
        tag = tag.strip('"').upper()
        block = Block()
        block.set("history", Scalar(f'"{tag}_GATHERING_STORM_DESC"'))
        entry = BookmarkCountryEntry(tag, block)
        entry.set_minor(minor)
        self.block.items.append(Pair(f'"{tag}"', block))
        return next((c for c in self.countries() if c.block is block), entry)

    def remove_entry(self, entry: "BookmarkCountryEntry") -> None:
        """Remove exactly this entry, leaving other entries of the same tag."""
        target = entry.block
        self.block.items = [
            it for it in self.block.items
            if not (isinstance(it, Pair) and _looks_like_tag(it.key)
                    and it.value is target)
        ]

    def remove_country(self, tag: str) -> None:
        tag = tag.strip('"').upper()
        self.block.items = [
            it for it in self.block.items
            if not (isinstance(it, Pair) and _looks_like_tag(it.key)
                    and it.key.strip('"') == tag)
        ]


@dataclass
class BookmarkDocRef:
    rel_file: str
    source_root: Path
    is_vanilla: bool

    @property
    def path(self) -> Path:
        return self.source_root / self.rel_file

    @property
    def name(self) -> str:
        return Path(self.rel_file).name


@dataclass
class BookmarkDocument:
    ref: BookmarkDocRef
    root: Block
    bookmarks: list[BookmarkEntry] = field(default_factory=list)


class BookmarkService:
    def __init__(self, context: ModContext):
        self.ctx = context

    # --- discovery --------------------------------------------------------
    def _roots(self) -> list[Path]:
        """Every root that can hold bookmarks, lowest priority first.

        DLC matters here: an expansion ships its own bookmark file, and that is
        the scenario the game actually shows. Scanning only the base game would
        hide it — you would edit a file the start screen no longer reads.
        """
        roots: list[Path] = []
        for sub in ("dlc", "integrated_dlc"):
            parent = self.ctx.game_path / sub
            if parent.is_dir():
                roots.extend(p for p in sorted(parent.iterdir()) if p.is_dir())
        roots.append(self.ctx.game_path)
        roots.extend(self.ctx.dependency_paths)
        roots.append(self.ctx.mod.path)
        return roots

    def list_docs(self, include_vanilla: bool = True) -> list[BookmarkDocRef]:
        """Bookmark files across game, DLC, dependencies and mod.

        A mod file with the same name hides the lower layer's, mirroring how the
        game resolves overrides.
        """
        by_rel: dict[str, BookmarkDocRef] = {}
        mod_root = self.ctx.mod.path
        for root in self._roots():
            folder = root / DIR
            if not folder.is_dir():
                continue
            for file in sorted(folder.glob("*.txt")):
                rel = f"{DIR}/{file.name}"
                by_rel[rel] = BookmarkDocRef(rel, root, root != mod_root)
        refs = list(by_rel.values())
        if not include_vanilla:
            refs = [r for r in refs if not r.is_vanilla]
        return sorted(refs, key=lambda r: (r.is_vanilla, r.name.lower()))

    # --- loading / saving -------------------------------------------------
    def load(self, ref: BookmarkDocRef) -> BookmarkDocument:
        root = parse_file(ref.path)
        entries: list[BookmarkEntry] = []
        container = root.get_block("bookmarks")
        source = container if container is not None else root
        for pair in source.pairs():
            if pair.key == "bookmark" and isinstance(pair.value, Block):
                entries.append(BookmarkEntry(pair.value))
        return BookmarkDocument(ref, root, entries)

    def to_mod(self, doc: BookmarkDocument) -> BookmarkDocument:
        """Make the document mod-side, so saving never writes into the base game."""
        if not doc.ref.is_vanilla:
            return doc
        doc.ref = BookmarkDocRef(doc.ref.rel_file, self.ctx.mod.path, False)
        return doc

    def save_doc(self, doc: BookmarkDocument) -> Path:
        if doc.ref.is_vanilla:
            doc = self.to_mod(doc)
        target = doc.ref.path
        target.parent.mkdir(parents=True, exist_ok=True)
        dump_file(doc.root, target)
        return target
