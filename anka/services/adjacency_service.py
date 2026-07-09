"""Special province connections — ``map/adjacencies.csv`` (straits, canals,
impassable borders).

Header: ``From;To;Type;Through;start_x;start_y;stop_x;stop_y;
adjacency_rule_name;Comment``. ``Type`` ∈ ``sea | impassable | <empty>``
(empty = rule-only link, e.g. Gibraltar). ``Through`` is the controlling
province of a strait or ``-1``. The vanilla file ends with a ``-1;-1;...``
terminator row plus a comment line — both are preserved verbatim, as are all
untouched data rows (line-preserving storage, same approach as
``definition.csv``). Saving writes a whole-file override into the mod.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..domain.mod import ModContext
from ._fsutil import ensure_filename_case

ADJACENCY_TYPES = ("sea", "impassable", "")


@dataclass
class Adjacency:
    from_id: int
    to_id: int
    type: str                    # "sea" | "impassable" | ""
    through: int                 # province id or -1
    start_x: int = -1
    start_y: int = -1
    stop_x: int = -1
    stop_y: int = -1
    rule: str = ""               # adjacency_rule_name
    comment: str = ""
    line_idx: int = -1
    dirty: bool = False

    def csv_row(self) -> str:
        return (f"{self.from_id};{self.to_id};{self.type};{self.through};"
                f"{self.start_x};{self.start_y};{self.stop_x};{self.stop_y};"
                f"{self.rule};{self.comment}")


class AdjacencyService:
    def __init__(self, context: ModContext, *, map_service=None):
        self.ctx = context
        self._map = map_service
        self._rows: list[Adjacency] | None = None
        self._lines: list[str] = []
        self._newline = "\n"
        self._trailing_newline = True
        self.dirty = False

    # -------------------------------------------------------------------- load
    def _file(self) -> Path | None:
        name = "adjacencies.csv"
        if self._map is not None:
            name = self._map.map_filenames().get("adjacencies", name)
        for root in self.ctx.search_roots("map"):
            candidate = root / "map" / name
            if candidate.is_file():
                return candidate
        return None

    @property
    def rows(self) -> list[Adjacency]:
        if self._rows is None:
            self._load()
        return self._rows

    def _load(self) -> None:
        self._rows = []
        path = self._file()
        if path is None:
            self._lines = ["From;To;Type;Through;start_x;start_y;stop_x;stop_y;"
                           "adjacency_rule_name;Comment",
                           "-1;-1;;-1;-1;-1;-1;-1;-1"]
            return
        raw = path.read_bytes()
        self._newline = "\r\n" if b"\r\n" in raw[:4096] else "\n"
        self._trailing_newline = raw.endswith((b"\n", b"\r"))
        self._lines = raw.decode("utf-8", errors="replace").split(self._newline)
        for idx, line in enumerate(self._lines):
            if idx == 0 or not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split(";")
            if len(parts) < 9 or parts[0].strip() == "-1":
                continue        # terminator / malformed — preserved verbatim
            try:
                row = Adjacency(
                    from_id=int(parts[0]), to_id=int(parts[1]),
                    type=parts[2].strip(),
                    through=int(parts[3]) if parts[3].strip() else -1,
                    start_x=int(parts[4]), start_y=int(parts[5]),
                    stop_x=int(parts[6]), stop_y=int(parts[7]),
                    rule=parts[8].strip(),
                    comment=parts[9] if len(parts) > 9 else "",
                    line_idx=idx)
            except ValueError:
                continue
            self._rows.append(row)

    # ------------------------------------------------------------------- rules
    def rule_names(self) -> list[str]:
        """Rule names from ``adjacency_rules.txt`` (mod-first)."""
        from ..core.pdx import Block, Scalar, parse_file
        name = "adjacency_rules.txt"
        if self._map is not None:
            name = self._map.map_filenames().get("adjacency_rules", name)
        for root in self.ctx.search_roots("map"):
            path = root / "map" / name
            if not path.is_file():
                continue
            try:
                block = parse_file(path)
            except Exception:
                continue
            out = []
            for rule in block.get_all("adjacency_rule"):
                if isinstance(rule, Block):
                    n = rule.get("name")
                    if isinstance(n, Scalar):
                        out.append(n.raw)
            return out
        return []

    # --------------------------------------------------------------- mutations
    def add(self, **fields) -> Adjacency:
        """Append a data row (inserted before the terminator line)."""
        self.rows
        insert_at = next((i for i, line in enumerate(self._lines)
                          if line.split(";")[0].strip() == "-1"),
                         len(self._lines))
        row = Adjacency(**fields, line_idx=insert_at, dirty=True)
        self._lines.insert(insert_at, "")
        for other in self._rows:
            if other.line_idx >= insert_at:
                other.line_idx += 1
        row.line_idx = insert_at
        self._rows.append(row)
        self.dirty = True
        return row

    def edit(self, row: Adjacency, **fields) -> None:
        for key, value in fields.items():
            if not hasattr(row, key):
                raise AttributeError(key)
            setattr(row, key, value)
        row.dirty = True
        self.dirty = True

    def remove(self, row: Adjacency) -> None:
        self.rows
        self._rows.remove(row)
        if 0 <= row.line_idx < len(self._lines):
            del self._lines[row.line_idx]
            for other in self._rows:
                if other.line_idx > row.line_idx:
                    other.line_idx -= 1
        self.dirty = True

    # ------------------------------------------------------------------ saving
    def save(self) -> Path | None:
        if not self.dirty:
            return None
        for row in self.rows:
            if row.dirty and 0 <= row.line_idx < len(self._lines):
                self._lines[row.line_idx] = row.csv_row()
                row.dirty = False
        target = self.ctx.mod.path / "map" / "adjacencies.csv"
        target.parent.mkdir(parents=True, exist_ok=True)
        target = ensure_filename_case(target)
        text = self._newline.join(self._lines)
        if self._trailing_newline and not text.endswith(self._newline):
            text += self._newline
        target.write_bytes(text.encode("utf-8"))
        self.dirty = False
        return target
