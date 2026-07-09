"""Read/write land order-of-battle (OOB) files for the OOB editor.

Source: ``history/units/*.txt`` — files of ``division_template = { ... }`` blocks
and a ``units = { division = {...} }`` deployment list (plus air/naval variants
we ignore here: this editor is land-only, i.e. files that declare at least one
division template).

Like the other editors the model is *block-backed*: `DivisionTemplate` /
`Division` are thin views over parsed PDX nodes, so unknown fields and the whole
deployment survive load/save untouched.

Division template shape (5×5 regiments + up to 5 support) obeys the invariant the
in-game designer enforces — verified against all 804 vanilla land templates:

* each column is filled top-down with no vertical gap (a stack);
* columns are left-anchored (no empty column before a used one);
* no *valley* — a column is never shorter than both a left AND a right neighbour
  (this is exactly the "filled, empty, filled in a row" the user forbids);
* each support company type appears at most once.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..core.pdx import Block, Pair, Scalar, dump_file, dumps, parse_file
from ..core.pdx import parse as pdx_parse
from ..domain.mod import ModContext
from ._fsutil import ensure_filename_case
from ._layerdocs import layered_docs
from ._pdxview import BlockView

MAX_COLS = 5
MAX_ROWS = 5
MAX_SUPPORT = 5

_NEW_FILE = "anka_units.txt"


# ---------------------------------------------------------------------------
# shape invariant helpers (pure functions on the column model)
# ---------------------------------------------------------------------------

def has_valley(heights: list[int]) -> bool:
    """True if some column is strictly shorter than a column on its left AND a
    column on its right — the forbidden ``filled, empty, filled`` row hole."""
    n = len(heights)
    for j in range(n):
        left = any(heights[i] > heights[j] for i in range(j))
        right = any(heights[k] > heights[j] for k in range(j + 1, n))
        if left and right:
            return True
    return False


def can_add_regiment(cols: list[list[str]], col_index: int) -> bool:
    """Whether a battalion may be appended to the bottom of column `col_index`
    (an existing column, or the next new one at ``len(cols)``)."""
    if col_index < 0 or col_index > len(cols) or col_index >= MAX_COLS:
        return False
    heights = [len(c) for c in cols]
    if col_index == len(cols):
        heights.append(1)                       # brand-new (left-anchored) column
    else:
        if len(cols[col_index]) >= MAX_ROWS:
            return False
        heights[col_index] += 1
    return not has_valley(heights)


def can_remove_regiment(cols: list[list[str]], col_index: int) -> bool:
    """Whether the bottom battalion of column `col_index` may be removed while
    keeping the shape valid (columns left-anchored, no valley)."""
    if not (0 <= col_index < len(cols)) or not cols[col_index]:
        return False
    heights = [len(c) for c in cols]
    heights[col_index] -= 1
    if heights[col_index] == 0 and col_index != len(cols) - 1:
        return False                            # would leave an empty middle column
    heights = [h for h in heights if h > 0] if heights[col_index] == 0 else heights
    return not has_valley(heights)


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------

class DivisionTemplate(BlockView):
    """A view over one ``division_template = { ... }`` pair."""

    def __init__(self, pair: Pair):
        self.pair = pair

    @property
    def block(self) -> Block:
        return self.pair.value

    @property
    def name(self) -> str:
        v = self.block.get("name")
        return v.raw if isinstance(v, Scalar) else ""

    @name.setter
    def name(self, value: str) -> None:
        self.block.set("name", Scalar(value, quoted=True))

    @property
    def names_group(self) -> str:
        """``division_names_group`` — the name group this template's divisions draw
        their names from (overrides the automatic for_countries/division_types match)."""
        v = self.block.get("division_names_group")
        return v.raw if isinstance(v, Scalar) else ""

    @names_group.setter
    def names_group(self, value: str) -> None:
        value = value.strip()
        if value:
            self.block.set("division_names_group", Scalar(value))
        else:
            self.block.remove("division_names_group")

    # --- regiments as a column model ---------------------------------------
    def columns(self) -> list[list[str]]:
        """Regiments as a list of columns (each a top-down list of unit types),
        reconstructed from the ``{ x y }`` cells and compacted."""
        reg = self.block.get_block("regiments")
        if reg is None:
            return []
        cols: dict[int, dict[int, str]] = {}
        for p in reg.pairs():
            if not isinstance(p.value, Block):
                continue
            try:
                x = int(p.value.get_scalar("x"))
                y = int(p.value.get_scalar("y"))
            except (TypeError, ValueError):
                continue
            cols.setdefault(x, {})[y] = p.key
        out: list[list[str]] = []
        for x in sorted(cols):
            col = cols[x]
            out.append([col[y] for y in sorted(col)])
        return out

    def set_columns(self, cols: list[list[str]]) -> None:
        reg = Block()
        for c, col in enumerate(cols):
            for r, unit in enumerate(col):
                reg.add(unit, Block([Pair("x", Scalar(str(c))),
                                     Pair("y", Scalar(str(r)))]))
        self.block.set("regiments", reg)

    def support(self) -> list[str]:
        sup = self.block.get_block("support")
        if sup is None:
            return []
        items: list[tuple[int, str]] = []
        for i, p in enumerate(sup.pairs()):
            if not isinstance(p.value, Block):
                continue
            try:
                y = int(p.value.get_scalar("y"))
            except (TypeError, ValueError):
                y = i
            items.append((y, p.key))
        return [k for _y, k in sorted(items, key=lambda t: t[0])]

    def set_support(self, units: list[str]) -> None:
        if not units:
            self.block.remove("support")
            return
        sup = Block()
        for i, unit in enumerate(units):
            sup.add(unit, Block([Pair("x", Scalar("0")), Pair("y", Scalar(str(i)))]))
        self.block.set("support", sup)

    def unit_count(self) -> int:
        return sum(len(c) for c in self.columns()) + len(self.support())

    def __repr__(self) -> str:
        return f"DivisionTemplate({self.name!r})"


class Division(BlockView):
    """A view over one deployed ``division = { ... }`` pair in ``units``."""

    def __init__(self, pair: Pair):
        self.pair = pair

    @property
    def block(self) -> Block:
        return self.pair.value

    @property
    def location(self) -> str:
        return self.get_raw("location")

    @location.setter
    def location(self, value: str) -> None:
        self.set_raw("location", value)

    @property
    def template(self) -> str:
        v = self.block.get("division_template")
        return v.raw if isinstance(v, Scalar) else ""

    @template.setter
    def template(self, value: str) -> None:
        self.block.set("division_template", Scalar(value, quoted=True))

    @property
    def custom_name(self) -> str:
        v = self.block.get("name")
        return v.raw if isinstance(v, Scalar) else ""

    @custom_name.setter
    def custom_name(self, value: str) -> None:
        value = value.strip()
        if value:
            self.block.set("name", Scalar(value, quoted=True))
        else:
            self.block.remove("name")

    # --- ordered naming ----------------------------------------------------
    # ``division_name = { is_name_ordered = yes name_order = N }`` makes the division
    # take its name from the matching names_divisions group's ``ordered = { N = ... }``
    # entry (fallback_name with %d = N otherwise). It is mutually exclusive with a
    # literal ``name`` — a division has one or the other.
    @property
    def is_name_ordered(self) -> bool:
        b = self.block.get_block("division_name")
        return b is not None and b.get_scalar("is_name_ordered", "").lower() == "yes"

    @property
    def name_order(self) -> int | None:
        b = self.block.get_block("division_name")
        if b is None:
            return None
        v = b.get("name_order")
        return v.as_int() if isinstance(v, Scalar) else None

    def set_ordered_name(self, order: int | None) -> None:
        """`order` None clears ordered naming; otherwise write the ``division_name``
        block and drop any literal ``name`` (the two are mutually exclusive)."""
        if order is None:
            self.block.remove("division_name")
            return
        block = self.block.get_block("division_name")
        if block is None:
            block = Block()
            self.block.set("division_name", block)
        block.set("is_name_ordered", Scalar("yes"))
        block.set("name_order", Scalar(str(int(order))))
        self.block.remove("name")

    def display_name(self) -> str:
        if self.custom_name:
            return self.custom_name
        if self.is_name_ordered and self.name_order is not None:
            return f"#{self.name_order}"
        dn = self.block.get_block("division_name")
        if dn is not None:
            order = dn.get_scalar("name_order")
            if order:
                return f"#{order}"
        return self.template or "—"

    # --- starting factors (0..1) -------------------------------------------
    # ``start_experience_factor`` (default 0), ``start_manpower_factor`` and
    # ``start_equipment_factor`` (default 1). Written only when they differ from the
    # game default, keeping files tidy.
    def _factor(self, key: str, default: float) -> float:
        v = self.block.get(key)
        return v.as_float(default) if isinstance(v, Scalar) else default

    def _set_factor(self, key: str, value: float, default: float) -> None:
        value = max(0.0, min(1.0, value))
        if abs(value - default) < 1e-6:
            self.block.remove(key)
        else:
            self.block.set(key, Scalar(f"{value:g}"))

    @property
    def start_experience_factor(self) -> float:
        return self._factor("start_experience_factor", 0.0)

    @start_experience_factor.setter
    def start_experience_factor(self, value: float) -> None:
        self._set_factor("start_experience_factor", value, 0.0)

    @property
    def start_manpower_factor(self) -> float:
        return self._factor("start_manpower_factor", 1.0)

    @start_manpower_factor.setter
    def start_manpower_factor(self, value: float) -> None:
        self._set_factor("start_manpower_factor", value, 1.0)

    @property
    def start_equipment_factor(self) -> float:
        return self._factor("start_equipment_factor", 1.0)

    @start_equipment_factor.setter
    def start_equipment_factor(self, value: float) -> None:
        self._set_factor("start_equipment_factor", value, 1.0)


# Division experience-level bands by ``start_experience_factor`` (0..1). Keys map to
# oob.exp.<key> localisation. Boundaries per the HOI4 division experience tiers.
_EXPERIENCE_BANDS = ((0.1, "green"), (0.3, "trained"), (0.75, "regular"),
                     (0.9, "veteran"), (1.01, "elite"))


def experience_level_key(factor: float) -> str:
    for hi, key in _EXPERIENCE_BANDS:
        if factor < hi:
            return key
    return "elite"


# ---------------------------------------------------------------------------
# refs & documents
# ---------------------------------------------------------------------------

@dataclass
class OobDocRef:
    rel_file: str                     # e.g. "history/units/GER_1936.txt"
    source_root: Path
    is_vanilla: bool
    edited: bool = False
    templates: list[str] = field(default_factory=list)    # quick scan

    @property
    def path(self) -> Path:
        return self.source_root / self.rel_file

    @property
    def name(self) -> str:
        return Path(self.rel_file).stem


@dataclass
class OobDocument:
    ref: OobDocRef
    root: Block

    def templates(self) -> list[DivisionTemplate]:
        return [DivisionTemplate(p) for p in self.root.pairs()
                if p.key == "division_template" and isinstance(p.value, Block)]

    def find_template(self, name: str) -> DivisionTemplate | None:
        for t in self.templates():
            if t.name == name:
                return t
        return None

    def _units_block(self) -> Block | None:
        return self.root.get_block("units")

    def divisions(self) -> list[Division]:
        units = self._units_block()
        if units is None:
            return []
        return [Division(p) for p in units.pairs()
                if p.key == "division" and isinstance(p.value, Block)]


@dataclass
class Issue:
    severity: str
    code: str
    subject: str = ""
    detail: str = ""
    rel_file: str = ""


# ---------------------------------------------------------------------------
# service
# ---------------------------------------------------------------------------

_TEMPLATE_OPEN = re.compile(r"^\s*division_template\s*=\s*\{")
_NAME_RE = re.compile(r'^\s*name\s*=\s*"?([^"\n]+?)"?\s*$')
# air/naval OOB markers — used to keep those files out of the land editor.
_AIR_NAVAL_RE = re.compile(r"^\s*(air_wings|fleet)\s*=\s*\{", re.MULTILINE)


class OobService:
    def __init__(self, context: ModContext):
        self.ctx = context
        self._doc_cache: dict[Path, tuple[float, OobDocument]] = {}

    # --- listing -----------------------------------------------------------
    def list_docs(self, include_vanilla: bool = False) -> list[OobDocRef]:
        return layered_docs(
            self.ctx, "history/units", self._scan_dir,
            include_vanilla=include_vanilla,
            set_edited=lambda r: setattr(r, "edited", True),
            sort_key=lambda r: (r.is_vanilla, r.rel_file.lower()))

    def _scan_dir(self, root: Path, is_vanilla: bool) -> list[OobDocRef]:
        folder = root / "history/units"
        if not folder.is_dir():
            return []
        refs: list[OobDocRef] = []
        for file in sorted(folder.glob("*.txt")):
            if not file.is_file():
                continue
            try:
                text = file.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            templates = self._quick_scan(text)
            if not templates:
                # Land OOB only. Vanilla: require templates (skips air/naval and
                # keeps the huge vanilla list clean). Mod: also show a template-less
                # file (freshly created or deploy-only) unless it is an air/naval OOB
                # — otherwise a new empty file the user just created stays invisible.
                if is_vanilla or _AIR_NAVAL_RE.search(text):
                    continue
            refs.append(OobDocRef(
                rel_file=f"history/units/{file.name}", source_root=root,
                is_vanilla=is_vanilla, templates=templates))
        return refs

    @staticmethod
    def _quick_scan(text: str) -> list[str]:
        """Division-template names for the tree (no full parse)."""
        names: list[str] = []
        depth = 0
        in_t = False
        got = False
        for line in text.splitlines():
            code = line.split("#", 1)[0]
            if depth == 0 and _TEMPLATE_OPEN.match(code):
                in_t, got = True, False
            elif in_t and depth == 1 and not got:
                m = _NAME_RE.match(code)
                if m:
                    names.append(m.group(1).strip())
                    got = True
            depth += code.count("{") - code.count("}")
            if depth < 0:
                depth = 0
            if depth == 0:
                in_t = False
        return names

    # --- load / save -------------------------------------------------------
    def load(self, ref: OobDocRef) -> OobDocument:
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
        doc = OobDocument(ref=ref, root=parse_file(path))
        self._doc_cache[path] = (mtime, doc)
        return doc

    def save(self, doc: OobDocument) -> Path:
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

    def copy_to_mod(self, ref: OobDocRef) -> OobDocRef:
        if not ref.is_vanilla:
            return ref
        target = ensure_filename_case(self.ctx.mod.path / ref.rel_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(ref.path.read_bytes())
        return OobDocRef(rel_file=ref.rel_file, source_root=self.ctx.mod.path,
                         is_vanilla=False, edited=True, templates=list(ref.templates))

    def delete(self, ref: OobDocRef) -> None:
        if ref.is_vanilla:
            raise PermissionError("Refusing to delete vanilla files")
        ref.path.unlink(missing_ok=True)
        self._doc_cache.pop(ref.path, None)

    def new_document(self, filename: str) -> OobDocument:
        """A fresh empty mod OOB file (``units = {}``)."""
        if not filename.endswith(".txt"):
            filename += ".txt"
        rel = f"history/units/{filename}"
        ref = OobDocRef(rel_file=rel, source_root=self.ctx.mod.path, is_vanilla=False)
        if ref.path.exists():
            return self.load(ref)
        return OobDocument(ref=ref, root=Block([Pair("units", Block())]))

    # --- template CRUD -----------------------------------------------------
    def add_template(self, doc: OobDocument, name: str) -> DivisionTemplate:
        block = Block()
        block.set("name", Scalar(name, quoted=True))
        block.set("regiments", Block([Pair("infantry", Block([
            Pair("x", Scalar("0")), Pair("y", Scalar("0"))]))]))
        pair = Pair("division_template", block)
        # insert templates before the units block for a tidy file
        idx = len(doc.root.items)
        for i, it in enumerate(doc.root.items):
            if isinstance(it, Pair) and it.key == "units":
                idx = i
                break
        doc.root.items.insert(idx, pair)
        return DivisionTemplate(pair)

    def remove_template(self, doc: OobDocument, template: DivisionTemplate) -> None:
        doc.root.items = [it for it in doc.root.items if it is not template.pair]

    def rename_template(self, template: DivisionTemplate, new_name: str) -> str:
        old = template.name
        template.name = new_name
        return old

    def duplicate_template(self, doc: OobDocument, template: DivisionTemplate,
                           new_name: str) -> DivisionTemplate:
        copy_root = pdx_parse(dumps(Block([template.pair])))
        pair = next(p for p in copy_root.pairs())
        new = DivisionTemplate(pair)
        new.name = new_name
        idx = doc.root.items.index(template.pair) + 1
        doc.root.items.insert(idx, pair)
        return new

    def import_templates(self, doc: OobDocument, src: OobDocument) -> int:
        """Copy division templates from another OOB into `doc` (skipping names
        that already exist). Returns how many were imported."""
        existing = {t.name for t in doc.templates()}
        copied = 0
        idx = len(doc.root.items)
        for i, it in enumerate(doc.root.items):
            if isinstance(it, Pair) and it.key == "units":
                idx = i
                break
        for t in src.templates():
            if t.name in existing:
                continue
            copy_root = pdx_parse(dumps(Block([t.pair])))
            pair = next(p for p in copy_root.pairs())
            doc.root.items.insert(idx, pair)
            idx += 1
            existing.add(t.name)
            copied += 1
        return copied

    # --- division CRUD -----------------------------------------------------
    def _ensure_units(self, doc: OobDocument) -> Block:
        units = doc._units_block()
        if units is None:
            units = Block()
            doc.root.set("units", units)
        return units

    def add_division(self, doc: OobDocument, template: str,
                     location: str = "") -> Division:
        block = Block()
        block.set("division_template", Scalar(template, quoted=True))
        if location:
            block.set("location", Scalar(location))
        pair = Pair("division", block)
        self._ensure_units(doc).items.append(pair)
        return Division(pair)

    def remove_division(self, doc: OobDocument, division: Division) -> None:
        units = doc._units_block()
        if units is not None and division.pair in units.items:
            units.items.remove(division.pair)

    # --- validation --------------------------------------------------------
    def validate(self, docs: list[OobDocument],
                 known_units: set[str] | None = None) -> list[Issue]:
        issues: list[Issue] = []
        for doc in docs:
            rel = doc.ref.rel_file
            seen: set[str] = set()
            names = {t.name for t in doc.templates()}
            for t in doc.templates():
                if not t.name:
                    issues.append(Issue("error", "template_no_name", rel_file=rel))
                    continue
                if t.name in seen:
                    issues.append(Issue("error", "duplicate_template", t.name,
                                        rel_file=rel))
                seen.add(t.name)
                cols = t.columns()
                if not cols and not t.support():
                    issues.append(Issue("warning", "empty_template", t.name,
                                        rel_file=rel))
                if has_valley([len(c) for c in cols]):
                    issues.append(Issue("warning", "bad_shape", t.name, rel_file=rel))
                sup = t.support()
                if len(sup) != len(set(sup)):
                    issues.append(Issue("warning", "duplicate_support", t.name,
                                        rel_file=rel))
                if known_units is not None:
                    for unit in [u for c in cols for u in c] + sup:
                        if unit not in known_units:
                            issues.append(Issue("warning", "unknown_unit", t.name,
                                                unit, rel_file=rel))
            for d in doc.divisions():
                if d.template and d.template not in names:
                    issues.append(Issue("warning", "division_bad_template",
                                        d.display_name(), d.template, rel_file=rel))
        return issues
