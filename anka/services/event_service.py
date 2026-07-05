"""Read/write events for the Events editor.

Source: ``events/*.txt`` — files of ``add_namespace = ns`` declarations and
``country_event/news_event/... = { ... }`` blocks (additive; an event id is
``<namespace>.<number>``).

Like decisions/focuses, the model is *block-backed*: `Event` / `Option` /
`TextVariant` are thin views over parsed PDX nodes, so unknown keys survive
load/save round-trips untouched. Title, description and picture may each occur
several times as *conditional variants* (``desc = { text = X trigger = {...} }``);
their order matters — the game shows the first variant whose trigger holds.
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
from ._locutil import LocCatalog

EVENT_KINDS = ("country_event", "news_event", "unit_leader_event",
               "operative_leader_event", "state_event")

# One-letter badges for the tree ("id · [C] · title").
EVENT_KIND_LETTERS = {"country_event": "C", "news_event": "N",
                      "unit_leader_event": "U", "operative_leader_event": "O",
                      "state_event": "S"}

# Tri-state boolean flags on an event (absent = game default).
EVENT_FLAG_DEFAULTS = {
    "is_triggered_only": False,
    "fire_only_once": False,
    "hidden": False,
    "major": False,
    "minor_flavor": False,
    "fire_for_sender": True,       # default yes => only ``= no`` is written
}

# Script blocks on the event itself (the visual editor kinds each one offers).
EVENT_SCRIPT_FIELDS = ("trigger", "immediate", "mean_time_to_happen")

# Keys the option form manages itself; everything else in an option block is
# treated as its effects.
OPTION_KNOWN_KEYS = ("name", "trigger", "ai_chance", "original_recipient_only")

OPTION_FLAG_DEFAULTS = {"original_recipient_only": False}

# Variant-carrying event fields and the key used *inside* the conditional form
# (``title/desc = { text = ... }`` but ``picture = { picture = ... }``).
VARIANT_KEYS = ("title", "desc", "picture")
_VARIANT_INNER = {"title": "text", "desc": "text", "picture": "picture"}

_OPTION_LETTERS = "abcdefghijklmnopqrstuvwxyz"

_EVENT_ID_RE = re.compile(r"^[\w.]+$")


def option_letter(i: int) -> str:
    """Auto-suffix for option loc keys: a, b, c ... (numeric past 'z')."""
    return _OPTION_LETTERS[i] if 0 <= i < len(_OPTION_LETTERS) else f"o{i + 1}"


class _BlockView:
    """Shared scalar/flag/script accessors over a PDX block.

    (Deliberate copy of the 40-line helper in decision_service — extracting a
    shared services/_pdxview.py is a separate refactor, see TODO note there.)
    """

    block: Block
    FLAG_DEFAULTS: dict[str, bool] = EVENT_FLAG_DEFAULTS

    def get_raw(self, key: str) -> str:
        v = self.block.get(key)
        return v.raw if isinstance(v, Scalar) else ""

    def set_raw(self, key: str, value: str) -> None:
        value = value.strip()
        if value:
            self.block.set(key, Scalar(value))
        else:
            self.block.remove(key)

    def get_flag(self, name: str) -> bool:
        v = self.block.get(name)
        if isinstance(v, Scalar):
            return v.as_bool()
        return self.FLAG_DEFAULTS.get(name, False)

    def set_flag(self, name: str, value: bool) -> None:
        if value == self.FLAG_DEFAULTS.get(name, False):
            self.block.remove(name)
        else:
            self.block.set(name, bool(value))

    def get_script(self, key: str) -> str:
        v = self.block.get(key)
        if isinstance(v, Block):
            return dumps(v, top_level=False)
        if isinstance(v, Scalar):
            return v.raw
        return ""

    def set_script(self, key: str, text: str) -> None:
        """Parse `text` strictly and store under `key`; empty removes the key."""
        if not text.strip():
            self.block.remove(key)
            return
        parsed = pdx_parse(text, recover=False)
        if len(parsed.items) == 1 and isinstance(parsed.items[0], Scalar):
            self.block.set(key, parsed.items[0])
        else:
            self.block.set(key, Block(parsed.items))


class TextVariant:
    """One ``title``/``desc``/``picture`` item of an event.

    Wraps either the simple form (``title = my.1.t``, a Scalar) or the
    conditional form (``title = { text = my.1.t trigger = {...} }``, a Block).
    Variant order = item order inside the event block (the game uses the first
    variant whose trigger is true)."""

    def __init__(self, event_block: Block, pair: Pair):
        self.event_block = event_block
        self.pair = pair

    @property
    def key(self) -> str:
        return self.pair.key            # "title" | "desc" | "picture"

    @property
    def _inner_key(self) -> str:
        return _VARIANT_INNER.get(self.pair.key, "text")

    @property
    def is_conditional(self) -> bool:
        return isinstance(self.pair.value, Block)

    @property
    def text(self) -> str:
        """The loc key (title/desc) or sprite name (picture)."""
        v = self.pair.value
        if isinstance(v, Scalar):
            return v.raw
        if isinstance(v, Block):
            return v.get_scalar(self._inner_key, "") or ""
        return ""

    @text.setter
    def text(self, value: str) -> None:
        value = value.strip()
        v = self.pair.value
        if isinstance(v, Block):
            v.set(self._inner_key, Scalar(value))
        else:
            self.pair.value = Scalar(value)

    @property
    def trigger_script(self) -> str:
        v = self.pair.value
        if isinstance(v, Block):
            t = v.get("trigger")
            if isinstance(t, Block):
                return dumps(t, top_level=False)
        return ""

    @trigger_script.setter
    def trigger_script(self, text: str) -> None:
        """Set/replace the variant trigger; empty text collapses a
        ``{ text = X }``-only block back to the plain scalar form."""
        text = text.strip()
        if not text:
            v = self.pair.value
            if isinstance(v, Block):
                v.remove("trigger")
                if all(isinstance(it, Pair) and it.key == self._inner_key
                       for it in v.items):
                    self.pair.value = Scalar(self.text)
            return
        parsed = pdx_parse(text, recover=False)
        if not isinstance(self.pair.value, Block):
            self.pair.value = Block([Pair(self._inner_key, Scalar(self.text))])
        self.pair.value.set("trigger", Block(parsed.items))

    def __repr__(self) -> str:
        return f"TextVariant({self.key} = {self.text!r})"


class Option(_BlockView):
    """A view over one ``option = { ... }`` pair of an event."""

    FLAG_DEFAULTS = OPTION_FLAG_DEFAULTS

    def __init__(self, pair: Pair):
        self.pair = pair

    @property
    def block(self) -> Block:
        return self.pair.value

    @property
    def name_key(self) -> str:
        return self.get_raw("name")

    @name_key.setter
    def name_key(self, value: str) -> None:
        self.set_raw("name", value)

    @property
    def trigger_script(self) -> str:
        return self.get_script("trigger")

    @trigger_script.setter
    def trigger_script(self, text: str) -> None:
        self.set_script("trigger", text)

    @property
    def ai_chance_script(self) -> str:
        return self.get_script("ai_chance")

    @ai_chance_script.setter
    def ai_chance_script(self, text: str) -> None:
        self.set_script("ai_chance", text)

    def _effect_items(self) -> list:
        return [it for it in self.block.items
                if not (isinstance(it, Pair) and it.key in OPTION_KNOWN_KEYS)]

    @property
    def effects_script(self) -> str:
        """Everything in the option block except the known form keys."""
        return dumps(Block(self._effect_items()), top_level=False)

    @effects_script.setter
    def effects_script(self, text: str) -> None:
        """Replace the option's effects; known keys keep their (leading) spot,
        effect order follows the text."""
        parsed = pdx_parse(text, recover=False) if text.strip() else Block()
        known = [it for it in self.block.items
                 if isinstance(it, Pair) and it.key in OPTION_KNOWN_KEYS]
        self.block.items = known + list(parsed.items)

    def has_effects(self) -> bool:
        return bool(self._effect_items())

    def __repr__(self) -> str:
        return f"Option({self.name_key!r})"


class Event(_BlockView):
    """A view over one ``<kind> = { ... }`` pair in an events document."""

    FLAG_DEFAULTS = EVENT_FLAG_DEFAULTS

    def __init__(self, pair: Pair):
        self.pair = pair

    @property
    def block(self) -> Block:
        return self.pair.value

    @property
    def kind(self) -> str:
        return self.pair.key

    @property
    def id(self) -> str:
        return self.get_raw("id")

    @id.setter
    def id(self, value: str) -> None:
        self.set_raw("id", value)

    @property
    def namespace(self) -> str:
        eid = self.id
        return eid.rsplit(".", 1)[0] if "." in eid else eid

    # --- text variants ------------------------------------------------------
    def variants(self, key: str) -> list[TextVariant]:
        return [TextVariant(self.block, p) for p in self.block.pairs()
                if p.key == key]

    def first_text(self, key: str) -> str:
        for v in self.variants(key):
            if v.text:
                return v.text
        return ""

    def _variant_insert_index(self, key: str) -> int:
        """Where a new `key` item goes: after the last same-key item, else after
        the conventional predecessors (id → title → desc → picture), else at
        the top — so variants stay grouped in vanilla order."""
        order = ("id", "title", "desc", "picture")
        anchors = order[:order.index(key) + 1] if key in order else ("id",)
        best = 0
        for i, item in enumerate(self.block.items):
            if isinstance(item, Pair) and item.key in anchors:
                best = i + 1
        return best

    def add_variant(self, key: str, text: str,
                    after: TextVariant | None = None) -> TextVariant:
        pair = Pair(key, Scalar(text.strip()))
        if after is not None and after.pair in self.block.items:
            idx = self.block.items.index(after.pair) + 1
        else:
            idx = self._variant_insert_index(key)
        self.block.items.insert(idx, pair)
        return TextVariant(self.block, pair)

    def remove_variant(self, variant: TextVariant) -> None:
        self.block.items = [it for it in self.block.items
                            if it is not variant.pair]

    def move_variant(self, variant: TextVariant, delta: int) -> bool:
        """Swap the variant with its same-key neighbour; other items stay put."""
        positions = [i for i, it in enumerate(self.block.items)
                     if isinstance(it, Pair) and it.key == variant.key]
        try:
            j = next(k for k, i in enumerate(positions)
                     if self.block.items[i] is variant.pair)
        except StopIteration:
            return False
        target = j + delta
        if not 0 <= target < len(positions):
            return False
        a, b = positions[j], positions[target]
        self.block.items[a], self.block.items[b] = \
            self.block.items[b], self.block.items[a]
        return True

    # --- picture convenience (simple form; variants go through variants()) --
    @property
    def picture(self) -> str:
        return self.first_text("picture")

    @picture.setter
    def picture(self, value: str) -> None:
        value = value.strip()
        existing = self.variants("picture")
        if not value:
            for v in existing:
                self.remove_variant(v)
        elif existing:
            existing[0].text = value
        else:
            self.add_variant("picture", value)

    # --- options -------------------------------------------------------------
    def options(self) -> list[Option]:
        return [Option(p) for p in self.block.pairs()
                if p.key == "option" and isinstance(p.value, Block)]

    def add_option(self, name_key: str = "") -> Option:
        block = Block()
        if name_key:
            block.set("name", Scalar(name_key))
        pair = Pair("option", block)
        self.block.items.append(pair)
        return Option(pair)

    def remove_option(self, option: Option) -> None:
        self.block.items = [it for it in self.block.items
                            if it is not option.pair]

    def duplicate_option(self, option: Option, new_name_key: str) -> Option:
        copy_root = pdx_parse(dumps(Block([option.pair])))
        pair = next(p for p in copy_root.pairs())
        new = Option(pair)
        new.name_key = new_name_key
        idx = self.block.items.index(option.pair) + 1
        self.block.items.insert(idx, pair)
        return new

    def move_option(self, option: Option, delta: int) -> bool:
        """Swap the option with a neighbouring option pair (▲/▼ and drag&drop)."""
        positions = [i for i, it in enumerate(self.block.items)
                     if isinstance(it, Pair) and it.key == "option"]
        try:
            j = next(k for k, i in enumerate(positions)
                     if self.block.items[i] is option.pair)
        except StopIteration:
            return False
        target = j + delta
        if not 0 <= target < len(positions):
            return False
        a, b = positions[j], positions[target]
        self.block.items[a], self.block.items[b] = \
            self.block.items[b], self.block.items[a]
        return True

    def move_option_to(self, option: Option, index: int) -> bool:
        """Drag&drop: place the option at position `index` among the options."""
        positions = [i for i, it in enumerate(self.block.items)
                     if isinstance(it, Pair) and it.key == "option"]
        try:
            j = next(k for k, i in enumerate(positions)
                     if self.block.items[i] is option.pair)
        except StopIteration:
            return False
        index = max(0, min(index, len(positions) - 1))
        if index == j:
            return False
        step = 1 if index > j else -1
        while j != index:
            self.move_option(option, step)
            j += step
        return True

    def __repr__(self) -> str:
        return f"Event({self.kind} {self.id!r})"


# ---------------------------------------------------------------------------
# refs & documents
# ---------------------------------------------------------------------------

@dataclass
class EventDocRef:
    rel_file: str                     # e.g. "events/Baltic.txt"
    source_root: Path
    is_vanilla: bool
    edited: bool = False              # vanilla file overridden by the mod
    namespaces: list[str] = field(default_factory=list)          # quick scan
    events: list[tuple[str, str]] = field(default_factory=list)  # (kind, id)

    @property
    def path(self) -> Path:
        return self.source_root / self.rel_file

    @property
    def name(self) -> str:
        return Path(self.rel_file).stem


@dataclass
class EventDocument:
    ref: EventDocRef
    root: Block

    def namespaces(self) -> list[str]:
        return [p.value.raw for p in self.root.pairs()
                if p.key == "add_namespace" and isinstance(p.value, Scalar)]

    def events(self) -> list[Event]:
        return [Event(p) for p in self.root.pairs()
                if p.key in EVENT_KINDS and isinstance(p.value, Block)]

    def find(self, event_id: str) -> Event | None:
        for e in self.events():
            if e.id == event_id:
                return e
        return None

    def add_namespace(self, ns: str) -> None:
        """Declare a namespace after the existing declarations (top of file)."""
        if ns in self.namespaces():
            return
        last = -1
        for i, item in enumerate(self.root.items):
            if isinstance(item, Pair) and item.key == "add_namespace":
                last = i
        self.root.items.insert(last + 1, Pair("add_namespace", Scalar(ns)))


@dataclass
class Issue:
    severity: str        # "error" | "warning"
    code: str            # locale key suffix: events.issue.<code>
    subject: str = ""    # event id
    detail: str = ""
    rel_file: str = ""   # owning document (for tree navigation)


# ---------------------------------------------------------------------------
# service
# ---------------------------------------------------------------------------

_NS_RE = re.compile(r"^\s*add_namespace\s*=\s*([\w.]+)")
_EVENT_OPEN_RE = re.compile(
    r"^\s*(country_event|news_event|unit_leader_event|operative_leader_event"
    r"|state_event)\s*=\s*\{(.*)$")
_ID_LINE_RE = re.compile(r"^\s*id\s*=\s*([\w.]+)")
_ID_INLINE_RE = re.compile(r"\bid\s*=\s*([\w.]+)")

_NEW_FILE = "anka_events.txt"

# Default pictures for the creation template (vanilla generic sprites).
_DEFAULT_PICTURES = {
    "country_event": "GFX_report_event_generic_sign_treaty",
    "news_event": "GFX_news_event_generic_sign_treaty1",
}


def bad_event_id(eid: str) -> bool:
    """Event ids must be ``<namespace>.<number-ish suffix>`` — at least a dot
    with non-empty sides."""
    if not eid or "." not in eid or not _EVENT_ID_RE.match(eid):
        return True
    ns, _, num = eid.rpartition(".")
    return not ns or not num


class EventService:
    def __init__(self, context: ModContext):
        self.ctx = context
        self._doc_cache: dict[Path, tuple[float, EventDocument]] = {}
        self.loc = LocCatalog(context.mod.path, context.game_path,
                              vanilla_filter="event",
                              default_pattern="anka_events_l_{lang}.yml")

    # --- listing -----------------------------------------------------------
    def list_docs(self, include_vanilla: bool = False) -> list[EventDocRef]:
        mod_refs = self._scan_dir(self.ctx.mod.path, False)
        vanilla_refs = self._scan_dir(self.ctx.game_path, True)
        mod_files = {r.rel_file.lower() for r in mod_refs}
        for ref in mod_refs:
            if any(v.rel_file.lower() == ref.rel_file.lower() for v in vanilla_refs):
                ref.edited = True
        out = list(mod_refs)
        if include_vanilla:
            out.extend(v for v in vanilla_refs if v.rel_file.lower() not in mod_files)
        return sorted(out, key=lambda r: (r.is_vanilla, r.rel_file.lower()))

    def _scan_dir(self, root: Path, is_vanilla: bool) -> list[EventDocRef]:
        folder = root / GAME_DIRS.EVENTS
        if not folder.is_dir():
            return []
        refs: list[EventDocRef] = []
        for file in sorted(folder.glob("*.txt")):
            if not file.is_file():
                continue
            try:
                text = file.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            namespaces, events = self._quick_scan(text)
            refs.append(EventDocRef(
                rel_file=f"{GAME_DIRS.EVENTS}/{file.name}", source_root=root,
                is_vanilla=is_vanilla, namespaces=namespaces, events=events))
        return refs

    @staticmethod
    def _quick_scan(text: str) -> tuple[list[str], list[tuple[str, str]]]:
        """Cheap (namespaces, [(kind, id)]) scan for the list panel (no full
        parse). Depth is tracked by brace counting — vanilla indentation is too
        inconsistent (tabs, double tabs, spaces) to rely on."""
        namespaces: list[str] = []
        events: list[tuple[str, str]] = []
        depth = 0
        pending: list | None = None        # [kind, id | None]
        for line in text.splitlines():
            code = line.split("#", 1)[0]
            if depth == 0:
                m = _NS_RE.match(code)
                if m:
                    namespaces.append(m.group(1))
                m = _EVENT_OPEN_RE.match(code)
                if m:
                    pending = [m.group(1), None]
                    inline = _ID_INLINE_RE.search(m.group(2))
                    if inline:
                        pending[1] = inline.group(1)
            elif depth == 1 and pending is not None and pending[1] is None:
                m = _ID_LINE_RE.match(code)
                if m:
                    pending[1] = m.group(1)
            depth += code.count("{") - code.count("}")
            if depth < 0:
                depth = 0
            if depth == 0 and pending is not None:
                events.append((pending[0], pending[1] or ""))
                pending = None
        return namespaces, events

    # --- load / save --------------------------------------------------------
    def load(self, ref: EventDocRef) -> EventDocument:
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
        doc = EventDocument(ref=ref, root=parse_file(path))
        self._doc_cache[path] = (mtime, doc)
        return doc

    def save(self, doc: EventDocument) -> Path:
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

    def copy_to_mod(self, ref: EventDocRef) -> EventDocRef:
        if not ref.is_vanilla:
            return ref
        target = ensure_filename_case(self.ctx.mod.path / ref.rel_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(ref.path.read_bytes())
        return EventDocRef(rel_file=ref.rel_file, source_root=self.ctx.mod.path,
                           is_vanilla=False, edited=True,
                           namespaces=list(ref.namespaces),
                           events=list(ref.events))

    def delete(self, ref: EventDocRef) -> None:
        if ref.is_vanilla:
            raise PermissionError("Refusing to delete vanilla files")
        ref.path.unlink(missing_ok=True)
        self._doc_cache.pop(ref.path, None)

    # --- namespaces -----------------------------------------------------------
    def namespaces(self, include_vanilla: bool = False) -> list[str]:
        names: set[str] = set()
        for ref in self.list_docs(include_vanilla):
            names.update(ref.namespaces)
        return sorted(names)

    def event_options(self) -> list[tuple[str, str]]:
        """(display, id) picker rows for every event of mod + vanilla (mod
        first). Used by script editors for event-firing effects."""
        opts: list[tuple[str, str]] = []
        seen: set[str] = set()
        for ref in self.list_docs(True):
            for kind, eid in ref.events:
                if eid and eid not in seen:
                    seen.add(eid)
                    letter = EVENT_KIND_LETTERS.get(kind, "?")
                    opts.append((f"{eid} · [{letter}] · {ref.name}", eid))
        return opts

    def next_id(self, ns: str) -> str:
        """``ns.N`` with N = max used number + 1 over ALL docs (vanilla + mod) —
        colliding with a vanilla id would override that event in game."""
        prefix = ns + "."
        highest = 0
        for ref in self.list_docs(True):
            for _kind, eid in ref.events:
                if not eid.startswith(prefix):
                    continue
                suffix = eid[len(prefix):]
                if suffix.isdigit():
                    highest = max(highest, int(suffix))
        return f"{ns}.{highest + 1}"

    def create_namespace(self, ns: str) -> EventDocument:
        """Materialize a namespace immediately: write ``add_namespace`` into
        ANKA's events file so the namespace is visible in the tree at once."""
        doc = self.mod_target_doc(ns)
        doc.add_namespace(ns)
        self.save(doc)
        return doc

    def remove_namespace(self, ns: str) -> int:
        """Delete a namespace from the mod: every mod event with this namespace
        AND the ``add_namespace`` declarations. Returns the number of events
        removed. Vanilla files are never touched."""
        removed = 0
        for ref in self.list_docs(False):
            if ns not in ref.namespaces and \
                    not any(_ns_of(eid) == ns for _k, eid in ref.events):
                continue
            doc = self.load(ref)
            before = len(doc.root.items)
            doc.root.items = [
                it for it in doc.root.items
                if not (isinstance(it, Pair) and (
                    (it.key == "add_namespace" and isinstance(it.value, Scalar)
                     and it.value.raw == ns)
                    or (it.key in EVENT_KINDS and isinstance(it.value, Block)
                        and _ns_of(Event(it).id) == ns)))]
            removed += sum(1 for _k, eid in ref.events if _ns_of(eid) == ns)
            if len(doc.root.items) != before:
                self.save(doc)
        return removed

    def count_mod_events(self, ns: str) -> int:
        """How many mod events live in `ns` (for delete confirmation)."""
        return sum(1 for ref in self.list_docs(False)
                   for _k, eid in ref.events if _ns_of(eid) == ns)

    # --- event CRUD ------------------------------------------------------------
    def mod_target_doc(self, ns: str) -> EventDocument:
        """The mod document new events of `ns` should go to: the first mod file
        already declaring that namespace, else ANKA's events file."""
        for ref in self.list_docs(False):
            if ns in ref.namespaces:
                return self.load(ref)
        rel = f"{GAME_DIRS.EVENTS}/{_NEW_FILE}"
        ref = EventDocRef(rel_file=rel, source_root=self.ctx.mod.path,
                          is_vanilla=False)
        if ref.path.exists():
            return self.load(ref)
        return EventDocument(ref=ref, root=Block())

    def create_event(self, doc: EventDocument, ns: str, kind: str) -> Event:
        if kind not in EVENT_KINDS:
            raise ValueError(f"Unknown event kind: {kind!r}")
        doc.add_namespace(ns)
        eid = self.next_id(ns)
        block = Block()
        block.set("id", Scalar(eid))
        block.add("title", Scalar(f"{eid}.t"))
        block.add("desc", Scalar(f"{eid}.d"))
        picture = _DEFAULT_PICTURES.get(kind)
        if picture:
            block.add("picture", Scalar(picture))
        block.set("is_triggered_only", True)
        option = Block()
        option.set("name", Scalar(f"{eid}.a"))
        block.add("option", option)
        pair = Pair(kind, block)
        doc.root.items.append(pair)
        return Event(pair)

    def remove_event(self, doc: EventDocument, event: Event) -> None:
        doc.root.items = [it for it in doc.root.items if it is not event.pair]

    def duplicate_event(self, doc: EventDocument, event: Event,
                        languages: tuple[str, ...] = ("english",)) -> Event:
        """Copy an event under a fresh id; loc keys derived from the old id are
        rewritten to the new id and their texts copied for `languages`."""
        new_id = self.next_id(event.namespace)
        copy_root = pdx_parse(dumps(Block([event.pair])))
        pair = next(p for p in copy_root.pairs())
        new = Event(pair)
        old_id = event.id
        new.id = new_id
        for old_key, apply in _derived_loc_keys(new, old_id):
            new_key = new_id + old_key[len(old_id):]
            apply(new_key)
            for lang in dict.fromkeys(languages):
                value = self.loc.get(old_key, lang)
                if value is not None:
                    self.loc.set(new_key, lang, value)
        doc.root.items.append(pair)
        return new

    def rename_event(self, event: Event, new_id: str) -> str:
        """Change the id and carry derived loc keys (title/desc variants and
        option names that start with the old id) over to the new id."""
        if bad_event_id(new_id):
            raise ValueError(f"Invalid event id: {new_id!r}")
        old = event.id
        event.id = new_id
        for old_key, apply in _derived_loc_keys(event, old):
            new_key = new_id + old_key[len(old):]
            apply(new_key)
            self.loc.rename_key(old_key, new_key)
        return old

    # --- localisation ----------------------------------------------------------
    def name_of(self, key: str, language: str) -> str:
        text = self.loc.get(key, language)
        if text is None and language != "english":
            text = self.loc.get(key, "english")
        return text if text is not None else key

    def set_loc(self, key: str, language: str, text: str) -> None:
        self.loc.set(key, language, text)

    def has_any_name(self, key: str, preferred: str = "english") -> bool:
        from ..config.constants import HOI4_LANGUAGES
        order = [preferred, "english"] + [l for l in HOI4_LANGUAGES
                                          if l not in (preferred, "english")]
        return any(self.loc.has(key, lang) for lang in order)

    # --- validation ---------------------------------------------------------------
    def validate(self, docs: list[EventDocument], language: str | None = None,
                 sprite_exists=None) -> list[Issue]:
        issues: list[Issue] = []
        seen: dict[str, str] = {}
        known_ns = set(self.namespaces(include_vanilla=True))
        for doc in docs:
            known_ns.update(doc.namespaces())
            rel = doc.ref.rel_file
            for event in doc.events():
                eid = event.id
                if bad_event_id(eid):
                    issues.append(Issue("error", "bad_id", eid, rel_file=rel))
                    continue
                if eid in seen:
                    issues.append(Issue("error", "duplicate_id", eid,
                                        seen[eid], rel_file=rel))
                else:
                    seen[eid] = rel
                if event.namespace not in known_ns:
                    issues.append(Issue("error", "unknown_namespace", eid,
                                        event.namespace, rel_file=rel))
                hidden = event.get_flag("hidden")
                options = event.options()
                if not hidden and not options:
                    issues.append(Issue("warning", "no_options", eid,
                                        rel_file=rel))
                for i, option in enumerate(options):
                    if not option.name_key:
                        issues.append(Issue("warning", "option_no_name", eid,
                                            f"#{i + 1}", rel_file=rel))
                if language and not hidden:
                    title = event.first_text("title")
                    if title and not self.has_any_name(title, preferred=language):
                        issues.append(Issue("warning", "missing_loc", eid,
                                            title, rel_file=rel))
                if event.kind in ("country_event", "news_event") and not hidden \
                        and not event.variants("picture"):
                    issues.append(Issue("warning", "missing_picture", eid,
                                        rel_file=rel))
                if sprite_exists is not None:
                    for v in event.variants("picture"):
                        if v.text and not sprite_exists(v.text):
                            issues.append(Issue("warning", "missing_sprite",
                                                eid, v.text, rel_file=rel))
                if event.get_flag("is_triggered_only") and \
                        event.get_script("mean_time_to_happen").strip():
                    issues.append(Issue("warning", "mtth_with_triggered_only",
                                        eid, rel_file=rel))
                if event.get_flag("major") and event.kind == "country_event":
                    issues.append(Issue("warning", "major_non_news", eid,
                                        rel_file=rel))
        return issues


def _ns_of(eid: str) -> str:
    return eid.rsplit(".", 1)[0] if "." in eid else eid


def _derived_loc_keys(event: Event, base_id: str):
    """(old_key, apply(new_key)) for every loc key of `event` derived from
    `base_id` — title/desc variant texts and option names. `apply` writes the
    renamed key back into the block."""
    prefix = base_id + "."
    out = []
    for key in ("title", "desc"):
        for variant in event.variants(key):
            text = variant.text
            if text == base_id or text.startswith(prefix):
                out.append((text, lambda nk, v=variant: setattr(v, "text", nk)))
    for option in event.options():
        name = option.name_key
        if name == base_id or name.startswith(prefix):
            out.append((name, lambda nk, o=option: setattr(o, "name_key", nk)))
    return out
