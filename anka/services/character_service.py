"""Characters — leaders, advisors, generals and admirals are all *characters*.

A character lives in ``common/characters/<TAG>.txt`` under ``characters = { <id> = {...} }``
and may carry any combination of roles: ``country_leader``, ``advisor``,
``field_marshal``/``corps_commander`` (general) and ``navy_leader`` (admiral), plus
``portraits`` (army/navy/civilian → large/small sprite names). A character becomes active
for a country via ``recruit_character`` in that country's history.

Responsibilities:
  * read every character (mod + game), resolving display names and portrait sprites;
  * report recruitment status across all countries (for the "unrecruited only" picker);
  * create / update / delete characters in the mod (the Characters editor);
Recruiting into history is delegated to `CountryService` so each service owns one tree.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config.constants import GAME_DIRS
from ..core.gfx import SpriteResolver
from ..core.localisation import LocFile
from ..core.pdx import Block, Pair, Scalar, dump_file, parse_file
from ..domain.mod import ModContext

GENERAL_ROLES = ("field_marshal", "corps_commander")
ROLE_KEYS = ("country_leader", "advisor", "field_marshal", "corps_commander", "navy_leader")
ADVISOR_SLOTS = ("political_advisor", "army_chief", "navy_chief", "air_chief",
                 "high_command", "theorist")
PORTRAIT_CATEGORIES = ("civilian", "army", "navy")


@dataclass
class CharacterModel:
    char_id: str
    tag: str
    name: str                       # resolved display name
    name_key: str                   # raw `name =` value
    roles: list[str] = field(default_factory=list)
    portraits: dict[str, dict[str, str]] = field(default_factory=dict)  # cat -> {size: sprite}
    in_mod: bool = False
    recruited_by: list[str] = field(default_factory=list)
    raw: Block | None = None        # source block, for prefilling the editor

    @property
    def roles_label(self) -> str:
        return ", ".join(self.roles) if self.roles else "—"

    def sprites(self) -> list[tuple[str, str, str]]:
        return [(cat, size, sprite)
                for cat, sizes in self.portraits.items()
                for size, sprite in sizes.items()]


class CharacterService:
    def __init__(self, context: ModContext):
        self.ctx = context
        self._chars: dict[str, CharacterModel] | None = None
        self._recruited: dict[str, list[str]] | None = None
        self._names: dict[str, str] | None = None
        self._sprites = SpriteResolver([context.mod.path, context.game_path])

    # --- file location ---------------------------------------------------
    def characters_file(self, tag: str) -> Path:
        return self.ctx.mod.path / GAME_DIRS.CHARACTERS / f"{tag}.txt"

    # --- scanning --------------------------------------------------------
    def all_characters(self, refresh: bool = False) -> list[CharacterModel]:
        if self._chars is None or refresh:
            self._chars = self._scan_all()
            recruited = self.recruited_map(refresh)
            for cid, model in self._chars.items():
                model.recruited_by = recruited.get(cid, [])
        return list(self._chars.values())

    def get(self, char_id: str) -> CharacterModel | None:
        self.all_characters()
        return self._chars.get(char_id) if self._chars else None

    def _scan_all(self) -> dict[str, CharacterModel]:
        names = self._name_map()
        result: dict[str, CharacterModel] = {}
        for root, in_mod in ((self.ctx.game_path, False), (self.ctx.mod.path, True)):
            folder = root / GAME_DIRS.CHARACTERS
            if not folder.is_dir():
                continue
            for file in folder.glob("*.txt"):
                try:
                    block = parse_file(file)
                except Exception:
                    continue
                chars = block.get_block("characters")
                if chars is None:
                    continue
                tag = file.stem.split("_")[0][:3].upper()
                for pair in chars.pairs():
                    if not isinstance(pair.value, Block):
                        continue
                    result[pair.key] = self._to_model(pair.key, pair.value, tag, in_mod, names)
        return result

    def _to_model(self, char_id, block: Block, tag, in_mod, names) -> CharacterModel:
        name_key = (block.get_scalar("name", "") or char_id).strip('"')
        roles = [r for r in ROLE_KEYS if block.get_block(r) is not None]
        portraits: dict[str, dict[str, str]] = {}
        pblock = block.get_block("portraits")
        if pblock is not None:
            for cat_pair in pblock.pairs():
                if isinstance(cat_pair.value, Block):
                    sizes = portraits.setdefault(cat_pair.key, {})
                    for size_pair in cat_pair.value.pairs():
                        if isinstance(size_pair.value, Scalar):
                            sizes[size_pair.key] = size_pair.value.raw
        # owner tag: prefer the id prefix when it looks like a TAG
        prefix = char_id.split("_")[0]
        owner = prefix.upper() if re.match(r"^[A-Za-z]{2,3}$", prefix) else tag
        return CharacterModel(
            char_id=char_id, tag=owner,
            name=names.get(name_key, name_key.replace("_", " ")),
            name_key=name_key, roles=roles, portraits=portraits, in_mod=in_mod, raw=block,
        )

    # --- recruitment status ---------------------------------------------
    def recruited_map(self, refresh: bool = False) -> dict[str, list[str]]:
        """char_id -> tags that recruit it, scanning every country's history."""
        if self._recruited is not None and not refresh:
            return self._recruited
        recruited: dict[str, list[str]] = {}
        for root in (self.ctx.game_path, self.ctx.mod.path):
            folder = root / GAME_DIRS.HISTORY_COUNTRIES
            if not folder.is_dir():
                continue
            for file in folder.glob("*.txt"):
                tag = file.stem.split(" ")[0]
                try:
                    text = file.read_text(encoding="utf-8-sig", errors="ignore")
                except OSError:
                    continue
                for m in re.finditer(r"recruit_character\s*=\s*([\w.\-]+)", text):
                    recruited.setdefault(m.group(1), [])
                    if tag not in recruited[m.group(1)]:
                        recruited[m.group(1)].append(tag)
        self._recruited = recruited
        return recruited

    def available_characters(self) -> list[CharacterModel]:
        """Characters recruited by no country (assignable elsewhere)."""
        self.all_characters()
        return [c for c in self._chars.values() if not c.recruited_by]

    def recruited_by_tag(self, tag: str) -> list[CharacterModel]:
        self.all_characters()
        return [c for c in self._chars.values() if tag in c.recruited_by]

    # --- portraits -------------------------------------------------------
    def resolve_sprite(self, sprite: str) -> Path | None:
        return self._sprites.resolve(sprite)

    # --- name localisation ----------------------------------------------
    def _name_map(self) -> dict[str, str]:
        if self._names is not None:
            return self._names
        names: dict[str, str] = {}
        for root in (self.ctx.game_path, self.ctx.mod.path):
            loc_dir = root / GAME_DIRS.LOCALISATION
            if not loc_dir.is_dir():
                continue
            for yml in loc_dir.glob("**/*characters*l_english.yml"):
                try:
                    loc = LocFile.load(yml)
                except Exception:
                    continue
                for entry in loc.entries():
                    names.setdefault(entry.key, entry.value)
        self._names = names
        return names

    # --- creation / editing ---------------------------------------------
    def create_or_update(
        self,
        tag: str,
        char_id: str,
        name: str,
        *,
        portraits: dict[str, str | Path] | None = None,   # category -> source image
        country_leader: dict | None = None,
        advisor: dict | None = None,
        general: dict | None = None,                       # includes "role": field_marshal|corps_commander
        admiral: dict | None = None,
    ) -> str:
        """Create or update a character with the given roles. Returns the char id."""
        tag = tag.upper()
        file = self.characters_file(tag)
        root = parse_file(file) if file.exists() else Block([Pair("characters", Block())])
        chars = root.get_block("characters") or Block()
        if root.get_block("characters") is None:
            root.add("characters", chars)

        character = chars.get_block(char_id) or Block()
        is_new = chars.get_block(char_id) is None
        character.set("name", Scalar(char_id))

        if portraits:
            pblock = character.get_block("portraits") or Block()
            for category, source in portraits.items():
                _dds, _gfx, sprite = self.ctx.icons.add_character_portrait(source, char_id, tag, category)
                cat_block = pblock.get_block(category) or Block()
                cat_block.set("large", Scalar(sprite))
                if pblock.get_block(category) is None:
                    pblock.add(category, cat_block)
            character.set("portraits", pblock)

        self._apply_role(character, "country_leader", country_leader, self._build_country_leader)
        self._apply_role(character, "advisor", advisor, self._build_advisor)
        # general role: remove both, then add the chosen one
        character.remove("field_marshal")
        character.remove("corps_commander")
        if general:
            role = general.get("role", "corps_commander")
            character.set(role, self._build_general(general))
        self._apply_role(character, "navy_leader", admiral, self._build_navy_leader)

        if is_new:
            chars.add(char_id, character)
        file.parent.mkdir(parents=True, exist_ok=True)
        dump_file(root, file)

        # localisation of the display name
        loc_path = (self.ctx.mod.path / GAME_DIRS.LOCALISATION / "english"
                    / "anka_characters_l_english.yml")
        loc = LocFile.load(loc_path) if loc_path.exists() else LocFile("english")
        loc.set(char_id, name)
        loc.save(loc_path)

        # Incremental cache update — no full rescan. Rebuild just this character's model.
        if self._names is not None:
            self._names[char_id] = name
        if self._chars is not None:
            model = self._to_model(char_id, character, tag, True, {})
            model.name = name
            model.recruited_by = (self._recruited or {}).get(char_id, [])
            self._chars[char_id] = model
        return char_id

    @staticmethod
    def _apply_role(character: Block, key: str, data: dict | None, builder) -> None:
        if data is None:
            character.remove(key)
        else:
            character.set(key, builder(data))

    @staticmethod
    def _traits_block(traits) -> Block:
        items = traits if isinstance(traits, (list, tuple)) else str(traits).split()
        return Block([Scalar(t) for t in items if t])

    def _build_country_leader(self, data: dict) -> Block:
        b = Block()
        b.add("ideology", Scalar(data.get("ideology", "despotism")))
        b.add("expire", Scalar(data.get("expire", "1965.1.1.1"), quoted=True))
        b.add("traits", self._traits_block(data.get("traits", [])))
        b.add("id", Scalar("-1"))
        return b

    def _build_advisor(self, data: dict) -> Block:
        b = Block()
        b.add("slot", Scalar(data.get("slot", "political_advisor")))
        b.add("idea_token", Scalar(data.get("idea_token") or "advisor"))
        if data.get("cost"):
            b.add("cost", Scalar(str(data["cost"])))
        b.add("traits", self._traits_block(data.get("traits", [])))
        return b

    def _build_general(self, data: dict) -> Block:
        b = Block()
        b.add("traits", self._traits_block(data.get("traits", [])))
        for key in ("skill", "attack_skill", "defense_skill", "planning_skill", "logistics_skill"):
            b.add(key, Scalar(str(data.get(key, 1))))
        return b

    def _build_navy_leader(self, data: dict) -> Block:
        b = Block()
        b.add("traits", self._traits_block(data.get("traits", [])))
        for key in ("skill", "attack_skill", "defense_skill", "maneuvering_skill", "coordination_skill"):
            b.add(key, Scalar(str(data.get(key, 1))))
        return b

    # --- deletion --------------------------------------------------------
    def delete(self, char_id: str) -> bool:
        """Remove a character from its mod file (and loc). Returns True if removed."""
        model = self.get(char_id)
        if model is None or not model.in_mod:
            return False
        file = self.characters_file(model.tag)
        if not file.exists():
            return False
        root = parse_file(file)
        chars = root.get_block("characters")
        if chars is None or not chars.has(char_id):
            return False
        chars.remove(char_id)
        if list(chars.pairs()):
            dump_file(root, file)
        else:
            file.unlink(missing_ok=True)
        loc_path = (self.ctx.mod.path / GAME_DIRS.LOCALISATION / "english"
                    / "anka_characters_l_english.yml")
        if loc_path.exists():
            loc = LocFile.load(loc_path)
            if char_id in loc:
                loc.remove(char_id).save(loc_path)
        # Incremental cache update.
        if self._chars is not None:
            self._chars.pop(char_id, None)
        if self._names is not None:
            self._names.pop(char_id, None)
        return True

    # --- recruitment cache updates (called after CountryService writes) --
    def mark_recruited(self, char_id: str, tag: str) -> None:
        if self._recruited is not None:
            self._recruited.setdefault(char_id, [])
            if tag not in self._recruited[char_id]:
                self._recruited[char_id].append(tag)
        model = self._chars.get(char_id) if self._chars else None
        if model and tag not in model.recruited_by:
            model.recruited_by.append(tag)

    def mark_unrecruited(self, char_id: str, tag: str) -> None:
        if self._recruited is not None and char_id in self._recruited:
            self._recruited[char_id] = [t for t in self._recruited[char_id] if t != tag]
        model = self._chars.get(char_id) if self._chars else None
        if model and tag in model.recruited_by:
            model.recruited_by.remove(tag)
