"""Read/write country data for the Countries editor.

Sources inside a mod (falling back to the base game for vanilla tags):
  * ``common/country_tags/*.txt`` — ``TAG = "countries/File.txt"`` mapping
  * ``common/countries/colors.txt`` — per-tag map & UI colors
  * ``common/countries/<File>.txt`` — the country definition (graphical culture, ...)
  * ``gfx/flags/<TAG>.tga`` — flag asset
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config.constants import GAME_DIRS, HOI4_LANGUAGES
from ..core.localisation import LocFile
from ..core.pdx import Block, Pair, Scalar, dump_file, dumps, parse_file
from ..core.pdx import parse as _pdx_parse
from ..domain.mod import ModContext
from ._fsutil import ensure_filename_case as _ensure_filename_case
from ._locutil import loc_write_target as _loc_write_target


@dataclass
class CountryRef:
    tag: str
    name: str                       # human-ish name derived from the country filename
    rel_file: str                   # e.g. "countries/Germany.txt"
    is_vanilla: bool
    source_root: Path               # mod path or game path that owns the file
    edited: bool = False            # vanilla tag that the mod overrides/edits

    @property
    def country_file(self) -> Path:
        return self.source_root / "common" / self.rel_file


@dataclass
class CountryColor:
    rgb: tuple[int, int, int] | None = None
    extras: list = field(default_factory=list)  # preserved sub-entries (color_ui, ...)


# Country tags: a leading letter then letters/digits (HOI4 dynamic tags like
# D01). MUST stay in sync with the new-country dialog's validation.
_TAG_RE = re.compile(r"^[A-Z][A-Z0-9]{1,2}$")


def _invert_condition(cond: str) -> str:
    """Invert a tech condition for an ``else`` branch (dlc:X <-> notdlc:X)."""
    if cond.startswith("dlc:"):
        return "notdlc:" + cond[4:]
    if cond.startswith("notdlc:"):
        return "dlc:" + cond[7:]
    return cond




class CountryService:
    def __init__(self, context: ModContext):
        self.ctx = context

    # --- tags ------------------------------------------------------------
    def list_tags(self, include_vanilla: bool = False) -> list[CountryRef]:
        # Layered sources, lowest→highest priority: base game, dependency mods, then
        # the edited mod. Dependency countries are part of the active mod set (the base
        # this mod builds on), so they are always listed — read-only, like vanilla —
        # while pure base-game tags stay behind `include_vanilla`.
        refs: dict[str, CountryRef] = {}          # deps + edited mod (mod overrides)
        vanilla: dict[str, CountryRef] = {}       # base game only
        for root, is_mod in self.ctx.override_layers(GAME_DIRS.COUNTRY_TAGS):
            target = vanilla if root == self.ctx.game_path else refs
            for r in self._read_tag_dir(root, is_vanilla=not is_mod):
                target[r.tag] = r                 # later layer wins (mod over deps)

        # Vanilla tags the mod edits (history/colors/characters/flags) are shown even
        # when vanilla is hidden, flagged as edited.
        for tag in self._mod_touched_tags():
            if tag not in refs and tag in vanilla:
                ref = vanilla[tag]
                ref.edited = True
                refs[tag] = ref

        if include_vanilla:
            for tag, ref in vanilla.items():
                refs.setdefault(tag, ref)
        return sorted(refs.values(), key=lambda r: r.tag)

    def _mod_touched_tags(self) -> set[str]:
        """Tags the mod modifies via files other than country_tags."""
        mod = self.ctx.mod.path
        tags: set[str] = set()

        hist = mod / GAME_DIRS.HISTORY_COUNTRIES
        if hist.is_dir():
            for f in hist.glob("*.txt"):
                tags.add(f.stem.split(" ")[0].split("-")[0].strip())

        chars = mod / GAME_DIRS.CHARACTERS
        if chars.is_dir():
            for f in chars.glob("*.txt"):
                if _TAG_RE.match(f.stem):
                    tags.add(f.stem)

        # colors.txt: the mod copy is seeded with EVERY vanilla entry (it replaces the
        # vanilla file wholesale), so mere presence there means nothing. Only tags
        # whose colors actually differ from vanilla count as edited.
        colors = self._colors_block(mod)
        if colors is not None:
            vanilla_colors = self._colors_block(self.ctx.game_path)
            for p in colors.pairs():
                if not _TAG_RE.match(p.key) or not isinstance(p.value, Block):
                    continue
                ventry = (vanilla_colors.get_block(p.key)
                          if vanilla_colors is not None else None)
                if ventry is None or self._entry_colors(p.value) != self._entry_colors(ventry):
                    tags.add(p.key)

        flags = mod / GAME_DIRS.GFX_FLAGS
        if flags.is_dir():
            for f in flags.glob("*.tga"):
                head = f.stem.split("_")[0]
                if _TAG_RE.match(head):
                    tags.add(head)

        return {t for t in tags if _TAG_RE.match(t)}

    def _read_tag_dir(self, root: Path, is_vanilla: bool) -> list[CountryRef]:
        tag_dir = root / GAME_DIRS.COUNTRY_TAGS
        if not tag_dir.is_dir():
            return []
        out: list[CountryRef] = []
        for file in sorted(tag_dir.glob("*.txt")):
            try:
                block = parse_file(file)
            except Exception:
                continue
            for pair in block.pairs():
                tag = pair.key
                if not _TAG_RE.match(tag) or not isinstance(pair.value, Scalar):
                    continue
                rel = pair.value.raw.strip('"')
                name = Path(rel).stem.replace("_", " ")
                out.append(CountryRef(tag, name, rel, is_vanilla, root))
        return out

    # --- colors ----------------------------------------------------------
    def _colors_block(self, root: Path) -> Block | None:
        path = root / GAME_DIRS.COUNTRY_COLORS
        if not path.exists():
            return None
        try:
            return parse_file(path)
        except Exception:
            return None

    def get_color(self, ref: CountryRef) -> CountryColor:
        for root in self.ctx.search_roots(GAME_DIRS.COUNTRY_COLORS):
            block = self._colors_block(root)
            if block is None:
                continue
            entry = block.get_block(ref.tag)
            if entry is None:
                continue
            # The editor edits the *map* color, so show `color` first; `color_ui`
            # is only a fallback (they differ for many vanilla countries).
            rgb = self._color_from(entry.get_block("color")) or self._color_from(entry.get_block("color_ui"))
            return CountryColor(rgb=rgb)
        # Some countries store color directly in the country file.
        try:
            cblock = parse_file(ref.country_file)
            return CountryColor(rgb=self._color_from(cblock.get_block("color")))
        except Exception:
            return CountryColor()

    @classmethod
    def _entry_colors(cls, entry: Block) -> tuple:
        """Comparable (color, color_ui) pair of a colors.txt entry."""
        return (cls._color_from(entry.get_block("color")),
                cls._color_from(entry.get_block("color_ui")))

    @staticmethod
    def _color_from(node: Block | None) -> tuple[int, int, int] | None:
        """Parse an rgb/hsv color block into 0-255 RGB."""
        if node is None:
            return None
        vals = node.array_values()
        if len(vals) < 3:
            return None
        try:
            nums = [float(v) for v in vals[:3]]
        except ValueError:
            return None
        if (node.tag or "rgb").lower() == "hsv":
            import colorsys
            r, g, b = colorsys.hsv_to_rgb(*nums)
            return (round(r * 255), round(g * 255), round(b * 255))
        return tuple(int(n) for n in nums)  # type: ignore[return-value]

    def set_color(self, tag: str, rgb: tuple[int, int, int], ref: CountryRef | None = None) -> list[Path]:
        """Set a country's map color.

        For a mod-owned country the color lives in its *own* definition file, which HOI4
        reads as the country's default — this keeps the mod fully independent of vanilla
        (no ``colors.txt`` is created, nothing is copied from the base game).

        Only a vanilla country whose color is overridden in the base game's ``colors.txt``
        needs a mod ``colors.txt`` — and since that file *replaces* vanilla wholesale, we
        seed it with all vanilla colors so other countries keep theirs.
        """
        rgb_block = Block([Scalar(str(c)) for c in rgb], tag="rgb")
        if ref is not None and self._is_in_mod(ref.country_file) and ref.country_file.exists():
            block = parse_file(ref.country_file)
            block.set("color", rgb_block)
            dump_file(block, ref.country_file)
            return [ref.country_file]
        return [self._write_colors_txt(tag, rgb)]

    def _write_colors_txt(self, tag: str, rgb: tuple[int, int, int]) -> Path:
        path = self.ctx.mod.path / GAME_DIRS.COUNTRY_COLORS
        # A mod's colors.txt *replaces* the vanilla one, so the first time we create it
        # we must seed it with every vanilla color — otherwise all other countries would
        # lose their colors. Once it exists in the mod we just keep extending it.
        block = self._colors_block(self.ctx.mod.path)
        if block is None:
            block = self._colors_block(self.ctx.game_path) or Block()
        entry = block.get_block(tag)
        rgb_block = Block([Scalar(str(c)) for c in rgb], tag="rgb")
        ui_block = Block([Scalar(str(c)) for c in rgb], tag="rgb")
        if entry is None:
            entry = Block()
            entry.add("color", rgb_block)
            entry.add("color_ui", ui_block)
            block.add(tag, entry)
        else:
            # Update BOTH: a stale color_ui would keep the old color in the game UI
            # (and in ANKA, which reads the entry back) — the edit looked lost.
            entry.set("color", rgb_block)
            entry.set("color_ui", ui_block)
        path.parent.mkdir(parents=True, exist_ok=True)
        dump_file(block, path)
        return path

    # --- country definition file ----------------------------------------
    def load_country(self, ref: CountryRef) -> Block:
        return parse_file(ref.country_file)

    def graphical_culture(self, ref: CountryRef) -> str:
        try:
            return self.load_country(ref).get_scalar("graphical_culture", "") or ""
        except Exception:
            return ""

    def flag_path(self, tag: str, suffix: str | None = None) -> Path | None:
        # Resolve across all content layers, highest priority first: the edited
        # mod overrides dependency submods, which override the base game. (The old
        # code only looked at the mod and the base game, so submod flags were
        # invisible.)
        name = f"{tag}_{suffix}" if suffix else tag
        for root in self.ctx.search_roots(GAME_DIRS.GFX_FLAGS):
            candidate = root / GAME_DIRS.GFX_FLAGS / f"{name}.tga"
            if candidate.exists():
                return candidate
        return None

    # --- localisation: names per language & ideology --------------------
    @staticmethod
    def _loc_key(tag: str, ideology: str | None) -> str:
        """Base localisation key: ``TAG`` or ``TAG_<ideology>`` (cosmetic variant)."""
        return tag if not ideology else f"{tag}_{ideology}"

    def get_localisation(self, tag: str, language: str, ideology: str | None = None) -> dict[str, str]:
        """Read name / definite (DEF) / adjective (ADJ) for a tag+language+ideology.

        HOI4 keys: ``TAG``, ``TAG_DEF``, ``TAG_ADJ`` for the base, and
        ``TAG_<ideology>`` + ``_DEF`` / ``_ADJ`` for ruling-party cosmetic names.
        """
        base = self._loc_key(tag, ideology)
        # Party names are per-ideology and live in their own file (parties_l_*.yml).
        party_key = f"{tag}_{ideology}_party" if ideology else f"{tag}_party"
        result = {"name": "", "definite": "", "adjective": "",
                  "party": "", "party_long": "", "party_desc": ""}
        suffixes = {"_DEF": "definite", "_ADJ": "adjective"}
        for root in self.ctx.override_roots(GAME_DIRS.LOCALISATION):  # mod overrides game
            loc_dir = root / GAME_DIRS.LOCALISATION / language
            if not loc_dir.is_dir():
                continue
            for yml in loc_dir.glob("*.yml"):
                name = yml.name.lower()
                if not any(s in name for s in ("countries", "anka", "part")):
                    continue
                try:
                    loc = LocFile.load(yml)
                except Exception:
                    continue
                if base in loc:
                    result["name"] = loc.get(base, "")
                for suffix, field_name in suffixes.items():
                    if f"{base}{suffix}" in loc:
                        result[field_name] = loc.get(f"{base}{suffix}", "")
                if party_key in loc:
                    result["party"] = loc.get(party_key, "")
                if f"{party_key}_long" in loc:
                    result["party_long"] = loc.get(f"{party_key}_long", "")
                if f"{party_key}_desc" in loc:
                    result["party_desc"] = loc.get(f"{party_key}_desc", "")
        return result

    def get_names(self, tag: str) -> dict[str, str]:
        """Map language -> base display name (for quick listings)."""
        names: dict[str, str] = {}
        for root in self.ctx.override_roots(GAME_DIRS.LOCALISATION):
            loc_dir = root / GAME_DIRS.LOCALISATION
            if not loc_dir.is_dir():
                continue
            for yml in loc_dir.glob("**/*.yml"):
                if "countries" not in yml.name.lower() and "anka" not in yml.name.lower():
                    continue
                try:
                    loc = LocFile.load(yml)
                except Exception:
                    continue
                if tag in loc:
                    names[loc.language] = loc.get(tag, "")
        return names

    def set_localisation(self, tag: str, language: str, ideology: str | None,
                         name: str, definite: str = "", adjective: str = "",
                         party: str = "", party_long: str = "", party_desc: str = "") -> Path:
        """Write country name/DEF/ADJ (and optional party names, in their own file,
        mirroring vanilla's layout).

        Keys that vanilla already defines are edited inside a mod-side *copy of the
        original file* (same relative path => engine override, no loc key collision);
        brand-new keys land in ANKA's files. See `_locutil.loc_write_target`."""
        base = self._loc_key(tag, ideology)
        country_path = _loc_write_target(
            self.ctx.mod.path, self.ctx.game_path, language, base,
            f"{GAME_DIRS.LOCALISATION}/{language}/anka_countries_l_{language}.yml",
            name_hints=("countries",), dep_roots=self.ctx.dependency_paths)
        loc = LocFile.load(country_path) if country_path.exists() else LocFile(language)
        if name:
            loc.set(base, name)
        if definite or name:
            loc.set(f"{base}_DEF", definite or name)
        if adjective:
            loc.set(f"{base}_ADJ", adjective)
        loc.save(country_path)

        # Party localisation — separate file, written only when something is provided.
        if ideology and (party or party_long or party_desc):
            party_key = f"{tag}_{ideology}_party"
            party_path = _loc_write_target(
                self.ctx.mod.path, self.ctx.game_path, language, party_key,
                f"{GAME_DIRS.LOCALISATION}/{language}/anka_parties_l_{language}.yml",
                name_hints=("part",), dep_roots=self.ctx.dependency_paths)
            ploc = LocFile.load(party_path) if party_path.exists() else LocFile(language)
            if party:
                ploc.set(party_key, party)
            if party_long:
                ploc.set(f"{party_key}_long", party_long)
            if party_desc:
                ploc.set(f"{party_key}_desc", party_desc)
            ploc.save(party_path)
        return country_path

    # Backwards-compatible thin wrapper used by country creation.
    def set_name(self, tag: str, language: str, name: str, definite: str | None = None) -> Path:
        return self.set_localisation(tag, language, None, name, definite or name)

    # --- history: capital, stability, war support, manpower -------------
    def get_history(self, tag: str) -> dict[str, str]:
        """Read a few scalar history values for display (capital, stability, ...)."""
        path = self._find_history_file(tag)
        out: dict[str, str] = {}
        if path is None:
            return out
        try:
            block = parse_file(path)
        except Exception:
            return out
        for key in ("capital", "set_stability", "set_war_support", "add_manpower", "set_research_slots"):
            value = block.get_scalar(key)
            if value is not None:
                out[key] = value
        return out

    def set_history_values(self, tag: str, name: str, **values) -> Path:
        """Set scalar history keys (capital / set_stability / set_war_support /
        add_manpower), copying the file into the mod first if it is vanilla."""
        block = self._history_block(tag)
        for key, value in values.items():
            if value is None or value == "":
                continue
            block.set(key, Scalar(str(value)))
        return self._write_history(tag, name, block)

    def _history_block(self, tag: str) -> Block:
        source = self._find_history_file(tag)
        return parse_file(source) if source else Block()

    # --- raw history effects (Additional effects tab) -------------------
    def get_history_text(self, tag: str) -> str:
        """The whole country-history block serialized — the on-start effects
        (capital, set_technology, set_politics, add_ideas, date blocks, ...).
        Shared with the per-field tabs: they read/write the same file."""
        return dumps(self._history_block(tag)).rstrip("\n")

    def set_history_text(self, tag: str, name: str, text: str) -> Path:
        """Replace the whole country-history block from edited script text
        (strict parse; copies a vanilla file into the mod first)."""
        root = _pdx_parse(text, recover=False) if text.strip() else Block()
        return self._write_history(tag, name, Block(root.items))

    def _write_history(self, tag: str, name: str, block: Block) -> Path:
        target = self._mod_history_path(tag, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target = _ensure_filename_case(target)
        dump_file(block, target)
        return target

    # --- popularities (dynamic ideologies) ------------------------------
    def get_popularities(self, tag: str) -> dict[str, int]:
        block = self._history_block(tag).get_block("set_popularities")
        if block is None:
            return {}
        out: dict[str, int] = {}
        for pair in block.pairs():
            if isinstance(pair.value, Scalar) and pair.value.as_int() is not None:
                out[pair.key] = pair.value.as_int()
        return out

    def set_popularities(self, tag: str, name: str, popularities: dict[str, int]) -> Path:
        block = self._history_block(tag)
        pop = Block()
        for ideology, value in popularities.items():
            pop.add(ideology, Scalar(str(int(value))))
        block.set("set_popularities", pop)
        return self._write_history(tag, name, block)

    # --- set_politics ----------------------------------------------------
    def get_politics(self, tag: str) -> dict[str, str]:
        block = self._history_block(tag).get_block("set_politics")
        if block is None:
            return {}
        keys = ("ruling_party", "last_election", "election_frequency", "elections_allowed")
        return {k: (block.get_scalar(k, "") or "").strip('"') for k in keys if block.has(k)}

    def set_politics(self, tag: str, name: str, ruling_party: str, last_election: str,
                     election_frequency: str | int, elections_allowed: bool) -> Path:
        block = self._history_block(tag)
        politics = block.get_block("set_politics") or Block()
        politics.set("ruling_party", Scalar(ruling_party))
        politics.set("last_election", Scalar(last_election.strip('"'), quoted=True))
        politics.set("election_frequency", Scalar(str(election_frequency)))
        politics.set("elections_allowed", Scalar("yes" if elections_allowed else "no"))
        block.set("set_politics", politics)
        return self._write_history(tag, name, block)

    # --- order of battle (OOB) ------------------------------------------
    def list_oob_files(self) -> list[str]:
        """OOB file stems available in history/units (mod + game)."""
        names: set[str] = set()
        for root in self.ctx.override_roots("history/units"):
            folder = root / "history/units"
            if folder.is_dir():
                names.update(f.stem for f in folder.glob("*.txt"))
        return sorted(names)

    # OOB kinds map to their history keys.
    OOB_KEYS = {"land": "set_oob", "naval": "set_naval_oob", "air": "set_air_oob"}

    def get_oob(self, tag: str, kind: str = "land") -> str:
        value = self._history_block(tag).get_scalar(self.OOB_KEYS[kind], "")
        return (value or "").strip('"')

    def set_oob(self, tag: str, name: str, oob_name: str, kind: str = "land") -> Path:
        block = self._history_block(tag)
        key = self.OOB_KEYS[kind]
        if oob_name:
            block.set(key, Scalar(oob_name, quoted=True))
        else:
            block.remove(key)
        return self._write_history(tag, name, block)

    # --- starting technologies ------------------------------------------
    def get_technology_entries(self, tag: str) -> list[tuple[str, str]]:
        """All starting techs as ``(tech, condition)`` pairs.

        Condition is ``""`` (unconditional), ``"dlc:<Name>"`` or ``"notdlc:<Name>"``,
        gathered recursively from ``set_technology`` blocks nested inside
        ``if``/``else`` DLC checks — the reason a country like the USSR has most of its
        techs behind ``has_dlc`` guards.
        """
        out: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        self._walk_technology(self._history_block(tag), "", out, seen)
        return out

    def get_technologies(self, tag: str) -> list[str]:
        return [t for t, _ in self.get_technology_entries(tag)]

    def _walk_technology(self, block: Block, cond: str, out: list, seen: set) -> None:
        for item in block.items:
            if not isinstance(item, Pair) or not isinstance(item.value, Block):
                continue
            if item.key == "set_technology":
                for tp in item.value.pairs():
                    if isinstance(tp.value, Scalar) and tp.value.as_int() == 1:
                        entry = (tp.key, cond)
                        if entry not in seen:
                            seen.add(entry)
                            out.append(entry)
            elif item.key in ("if", "else_if"):
                branch = self._dlc_condition(item.value.get_block("limit")) or cond
                self._walk_technology(item.value, branch, out, seen)
            elif item.key == "else":
                self._walk_technology(item.value, _invert_condition(cond), out, seen)

    @staticmethod
    def _dlc_condition(limit: Block | None) -> str:
        if limit is None:
            return ""
        dlc = limit.get_scalar("has_dlc")
        if dlc:
            return f"dlc:{dlc.strip(chr(34))}"
        for neg in ("NOT", "not"):
            neg_block = limit.get_block(neg)
            if neg_block is not None:
                inner = neg_block.get_scalar("has_dlc")
                if inner:
                    return f"notdlc:{inner.strip(chr(34))}"
        return ""

    def set_technology_entries(self, tag: str, name: str, entries: list[tuple[str, str]]) -> Path:
        """Write techs back, grouping by condition: unconditional into a top-level
        ``set_technology`` and each DLC condition into an ``if = { limit = {...} }``."""
        block = self._history_block(tag)
        # Drop the tech statements we manage, then rebuild them.
        block.items = [it for it in block.items
                       if not (isinstance(it, Pair) and it.key == "set_technology")
                       and not (isinstance(it, Pair) and it.key == "if" and self._is_anka_tech_if(it.value))]

        groups: dict[str, list[str]] = {}
        for tech, cond in entries:
            groups.setdefault(cond, []).append(tech)

        # Unconditional first.
        if groups.get(""):
            block.set("set_technology", Block([Pair(t, Scalar("1")) for t in groups[""]]))
        for cond, techs in groups.items():
            if not cond:
                continue
            kind, _, dlc = cond.partition(":")
            limit = Block()
            if kind == "dlc":
                limit.add("has_dlc", Scalar(dlc, quoted=True))
            else:  # notdlc
                limit.add("NOT", Block([Pair("has_dlc", Scalar(dlc, quoted=True))]))
            if_block = Block()
            if_block.add("limit", limit)
            if_block.add("set_technology", Block([Pair(t, Scalar("1")) for t in techs]))
            block.add("if", if_block)
        return self._write_history(tag, name, block)

    @staticmethod
    def _is_anka_tech_if(value) -> bool:
        """An ``if`` block we authored: a limit with has_dlc and a single set_technology."""
        return (isinstance(value, Block) and value.get_block("set_technology") is not None
                and value.get_block("limit") is not None and len(value.items) == 2)

    # --- country creation ------------------------------------------------
    def create_country(
        self,
        tag: str,
        name: str,
        rgb: tuple[int, int, int] = (128, 128, 128),
        graphical_culture: str = "western_european_gfx",
        capital: int | None = None,
    ) -> dict[str, Path]:
        """Create a brand-new country: tag mapping, definition file, history file,
        color and English name. Returns the paths written."""
        tag = tag.upper()
        safe_name = "".join(c for c in name if c.isalnum() or c in " _-").strip() or tag
        written: dict[str, Path] = {}

        # 1) country_tags entry
        tags_file = self.ctx.mod.path / GAME_DIRS.COUNTRY_TAGS / "zz_anka_countries.txt"
        tags_block = parse_file(tags_file) if tags_file.exists() else Block()
        rel = f"countries/{safe_name}.txt"
        tags_block.set(tag, Scalar(rel, quoted=True))
        tags_file.parent.mkdir(parents=True, exist_ok=True)
        dump_file(tags_block, tags_file)
        written["tags"] = tags_file

        # 2) definition file
        def_file = self.ctx.mod.path / "common" / rel
        def_block = Block()
        def_block.add("graphical_culture", Scalar(graphical_culture))
        def_block.add("graphical_culture_2d", Scalar(graphical_culture.replace("_gfx", "_2d")))
        def_block.add("color", Block([Scalar(str(c)) for c in rgb], tag="rgb"))
        def_file.parent.mkdir(parents=True, exist_ok=True)
        dump_file(def_block, def_file)
        written["definition"] = def_file
        # The definition file's `color` is the country's default — no colors.txt needed,
        # keeping new countries fully independent of vanilla.

        # 4) history file
        hist = Block()
        if capital is not None:
            hist.add("capital", Scalar(str(capital)))
        hist.add("set_research_slots", Scalar("3"))
        hist.add("set_stability", Scalar("0.5"))
        hist.add("set_war_support", Scalar("0.5"))
        politics = Block()
        politics.add("ruling_party", Scalar("neutrality"))
        politics.add("last_election", Scalar("1936.1.1", quoted=True))
        politics.add("election_frequency", Scalar("48"))
        politics.add("elections_allowed", Scalar("no"))
        hist.add("set_politics", politics)
        pop = Block()
        pop.add("neutrality", Scalar("100"))
        hist.add("set_popularities", pop)
        hist_path = self._mod_history_path(tag, safe_name)
        hist_path.parent.mkdir(parents=True, exist_ok=True)
        dump_file(hist, hist_path)
        written["history"] = hist_path

        # 5) English name
        written["loc"] = self.set_name(tag, "english", name)
        return written

    def add_recruit_character(self, tag: str, name: str, char_id: str) -> Path:
        """Append ``recruit_character = <char_id>`` to the country history (idempotent)."""
        block = self._history_block(tag)
        if char_id not in [v.raw for v in block.get_all("recruit_character") if isinstance(v, Scalar)]:
            block.add("recruit_character", Scalar(char_id))
        return self._write_history(tag, name, block)

    def remove_recruit_character(self, tag: str, name: str, char_id: str) -> Path:
        """Remove a ``recruit_character = <char_id>`` line from the country history."""
        block = self._history_block(tag)
        block.items = [
            it for it in block.items
            if not (isinstance(it, Pair) and it.key == "recruit_character"
                    and isinstance(it.value, Scalar) and it.value.raw == char_id)
        ]
        return self._write_history(tag, name, block)

    # --- deletion --------------------------------------------------------
    def delete_country(self, ref: CountryRef) -> list[Path]:
        """Delete a *mod* country's files (tag mapping, definition, history, color,
        localisation, flags). Vanilla countries are refused — there is nothing of ours
        to remove. Returns the paths that were changed or deleted."""
        if ref.is_vanilla:
            raise ValueError("Cannot delete a vanilla country")
        tag = ref.tag
        touched: list[Path] = []

        # 1) remove the tag from every mod country_tags file
        tag_dir = self.ctx.mod.path / GAME_DIRS.COUNTRY_TAGS
        if tag_dir.is_dir():
            for file in tag_dir.glob("*.txt"):
                try:
                    block = parse_file(file)
                except Exception:
                    continue
                if block.has(tag):
                    block.remove(tag)
                    if list(block.pairs()):
                        dump_file(block, file)
                    else:
                        file.unlink(missing_ok=True)
                    touched.append(file)

        # 2) definition file (only if it lives in the mod)
        if self._is_in_mod(ref.country_file) and ref.country_file.exists():
            ref.country_file.unlink()
            touched.append(ref.country_file)

        # 3) history file
        for candidate in (self.ctx.mod.path / GAME_DIRS.HISTORY_COUNTRIES).glob(f"{tag} *.txt"):
            candidate.unlink(missing_ok=True)
            touched.append(candidate)
        simple = self.ctx.mod.path / GAME_DIRS.HISTORY_COUNTRIES / f"{tag}.txt"
        if simple.exists():
            simple.unlink()
            touched.append(simple)

        # 4) colors.txt entry
        colors_path = self.ctx.mod.path / GAME_DIRS.COUNTRY_COLORS
        if colors_path.exists():
            block = self._colors_block(self.ctx.mod.path)
            if block is not None and block.has(tag):
                block.remove(tag)
                dump_file(block, colors_path)
                touched.append(colors_path)

        # 5) localisation TAG / TAG_DEF in mod files
        loc_dir = self.ctx.mod.path / GAME_DIRS.LOCALISATION
        if loc_dir.is_dir():
            for yml in loc_dir.glob("**/*.yml"):
                try:
                    loc = LocFile.load(yml)
                except Exception:
                    continue
                if tag in loc or f"{tag}_DEF" in loc:
                    loc.remove(tag).remove(f"{tag}_DEF")
                    loc.save(yml)
                    touched.append(yml)

        # 6) flag assets (large + medium + small, base + ideology variants)
        flags_dir = self.ctx.mod.path / GAME_DIRS.GFX_FLAGS
        for sub in ("", "medium", "small"):
            folder = flags_dir / sub if sub else flags_dir
            if not folder.is_dir():
                continue
            for flag in list(folder.glob(f"{tag}.tga")) + list(folder.glob(f"{tag}_*.tga")):
                flag.unlink(missing_ok=True)
                touched.append(flag)

        # 7) characters file + leader portraits owned by this tag
        char_file = self.ctx.mod.path / GAME_DIRS.CHARACTERS / f"{tag}.txt"
        if char_file.exists():
            char_file.unlink()
            touched.append(char_file)
        portrait_dir = self.ctx.mod.path / "gfx" / "leaders" / tag
        if portrait_dir.is_dir():
            for p in portrait_dir.glob("*"):
                p.unlink(missing_ok=True)
            try:
                portrait_dir.rmdir()
            except OSError:
                pass
            touched.append(portrait_dir)
        return touched

    # --- helpers ---------------------------------------------------------
    def _is_in_mod(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.ctx.mod.path.resolve())
            return True
        except (ValueError, OSError):
            return False

    def _find_history_file(self, tag: str) -> Path | None:
        for root in self.ctx.search_roots(GAME_DIRS.HISTORY_COUNTRIES):
            folder = root / GAME_DIRS.HISTORY_COUNTRIES
            if not folder.is_dir():
                continue
            matches = list(folder.glob(f"{tag} - *.txt")) + list(folder.glob(f"{tag}.txt"))
            if matches:
                return matches[0]
        return None

    def _game_history_file(self, tag: str) -> Path | None:
        folder = self.ctx.game_path / GAME_DIRS.HISTORY_COUNTRIES
        if not folder.is_dir():
            return None
        matches = list(folder.glob(f"{tag} - *.txt")) + list(folder.glob(f"{tag}.txt"))
        return matches[0] if matches else None

    def _mod_history_path(self, tag: str, name: str) -> Path:
        folder = self.ctx.mod.path / GAME_DIRS.HISTORY_COUNTRIES
        # Overriding a vanilla country: mirror its EXACT filename (case included). The
        # Clausewitz engine matches override paths case-sensitively, so "SOV - Soviet
        # Union.txt" would NOT override the real "SOV - Soviet union.txt".
        vanilla = self._game_history_file(tag)
        if vanilla is not None:
            return folder / vanilla.name
        # Brand-new mod country: reuse an existing mod file or build one from the name.
        if folder.is_dir():
            for candidate in folder.glob(f"{tag} - *.txt"):
                return candidate
            simple = folder / f"{tag}.txt"
            if simple.exists():
                return simple
        return folder / f"{tag} - {name}.txt"
