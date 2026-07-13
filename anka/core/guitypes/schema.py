"""Declarative attribute schema for HOI4 interface files.

One table per format family: `SPRITE_KINDS` (.gfx entries), `WIDGETS` (.gui
elements) and the scripted-GUI field lists. The tables drive the schema-based
inspector forms, the widget palette, defaults and validation — so "every
parameter a modder can use" lives here, not in UI code. Keys HOI4 accepts are
case-insensitive and vanilla mixes casings; specs carry the canonical casing
used when *adding* a key, while edits preserve the casing already on file
(`Block.set_ci`). Attributes we do not know survive untouched through the PDX
DOM and surface in the inspector's "other attributes" section.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto


class AttrKind(Enum):
    STR = auto()            # free text
    INT = auto()
    FLOAT = auto()
    BOOL = auto()           # yes / no
    ENUM = auto()           # one of enum_values
    POSITION = auto()       # { x = .. y = .. } (negatives allowed)
    SIZE = auto()           # { x/y } or { width/height }, values may be "N%"
    SPRITE_REF = auto()     # "GFX_*" name from any .gfx
    FONT_REF = auto()       # bitmapfont name
    LOC_TEXT = auto()       # literal text or [LocKey] / loc key
    SOUND_REF = auto()      # interface sound name
    TEXTURE = auto()        # path to an image file (dds/tga/png)
    COLOR = auto()          # 0xAARRGGBB scalar or { r g b } block
    BACKGROUND = auto()     # nested background = { quadTextureSprite = ... }
    SCRIPT = auto()         # opaque block edited via the script dialog


@dataclass(frozen=True)
class AttrSpec:
    name: str                              # canonical casing
    kind: AttrKind
    enum_values: tuple[str, ...] = ()
    default: str | None = None
    section: str = "common"                # inspector grouping


@dataclass(frozen=True)
class WidgetSpec:
    type_key: str                          # canonical casing, e.g. "iconType"
    container: bool                        # may hold child widgets
    attrs: tuple[AttrSpec, ...]
    palette_category: str                  # containers/graphics/text/controls/layout
    icon: str = "▢"                        # palette glyph


@dataclass(frozen=True)
class SpriteKindSpec:
    type_key: str
    attrs: tuple[AttrSpec, ...]
    has_animations: bool = False           # repeatable animation = { ... } blocks


def _a(name: str, kind: AttrKind = AttrKind.STR, *, enum: tuple[str, ...] = (),
       default: str | None = None, section: str = "common") -> AttrSpec:
    return AttrSpec(name, kind, enum_values=enum, default=default, section=section)


# --------------------------------------------------------------------------- enums
ORIENTATIONS: tuple[str, ...] = (
    "upper_left", "upper_right", "lower_left", "lower_right",
    "center", "center_up", "center_down", "center_left", "center_right",
    "center_middle", "left", "right", "up", "down",
)

TEXT_FORMATS: tuple[str, ...] = ("left", "center", "centre", "right", "up", "justified")

GRID_FORMATS: tuple[str, ...] = (
    "UPPER_LEFT", "UPPER_RIGHT", "LOWER_LEFT", "LOWER_RIGHT",
    "CENTER_UP", "CENTER_DOWN", "CENTER_LEFT", "CENTER_RIGHT", "CENTER",
)

ANIMATION_EASINGS: tuple[str, ...] = ("decelerated", "linear", "accelerated")

SPRITE_ANIM_TYPES: tuple[str, ...] = ("scrolling", "rotating", "pulsing")

SPRITE_BLEND_MODES: tuple[str, ...] = ("add", "multiply", "overlay")

LOAD_TYPES: tuple[str, ...] = ("INGAME", "FRONTEND")

VERTICAL_ALIGNMENTS: tuple[str, ...] = ("top", "center", "bottom")


# ------------------------------------------------------------ shared widget attrs
_NAME = _a("name")
_POSITION = _a("position", AttrKind.POSITION, section="layout")
_SIZE = _a("size", AttrKind.SIZE, section="layout")
_ORIENTATION = _a("orientation", AttrKind.ENUM, enum=ORIENTATIONS,
                  default="upper_left", section="layout")
_ORIGO = _a("origo", AttrKind.ENUM, enum=ORIENTATIONS, section="layout")
_CENTERPOSITION = _a("centerposition", AttrKind.BOOL, section="layout")
_SCALE = _a("scale", AttrKind.FLOAT, section="graphics")
_FRAME = _a("frame", AttrKind.INT, section="graphics")
_SPRITE = _a("spriteType", AttrKind.SPRITE_REF, section="graphics")
_QUAD_SPRITE = _a("quadTextureSprite", AttrKind.SPRITE_REF, section="graphics")
_ALWAYSTRANSPARENT = _a("alwaystransparent", AttrKind.BOOL, section="behavior")
_PDX_TOOLTIP = _a("pdx_tooltip", AttrKind.LOC_TEXT, section="behavior")
_PDX_TOOLTIP_DELAYED = _a("pdx_tooltip_delayed", AttrKind.LOC_TEXT, section="behavior")
_HINT_TAG = _a("hint_tag", AttrKind.STR, section="behavior")

_TOOLTIPS = (_PDX_TOOLTIP, _PDX_TOOLTIP_DELAYED, _HINT_TAG)

_TEXT_COMMON = (
    _a("text", AttrKind.LOC_TEXT, section="text"),
    _a("font", AttrKind.FONT_REF, section="text"),
    _a("maxWidth", AttrKind.INT, section="text"),
    _a("maxHeight", AttrKind.INT, section="text"),
    _a("format", AttrKind.ENUM, enum=TEXT_FORMATS, default="left", section="text"),
    _a("fixedsize", AttrKind.BOOL, section="text"),
    _a("borderSize", AttrKind.SIZE, section="text"),
    _a("text_color_code", AttrKind.STR, section="text"),
    _a("vertical_alignment", AttrKind.ENUM, enum=VERTICAL_ALIGNMENTS, section="text"),
    _a("truncate", AttrKind.STR, section="text"),
    _a("multiline", AttrKind.BOOL, section="text"),
)

_WINDOW_ANIMATION = (
    _a("show_position", AttrKind.POSITION, section="animation"),
    _a("hide_position", AttrKind.POSITION, section="animation"),
    _a("show_animation_type", AttrKind.ENUM, enum=ANIMATION_EASINGS, section="animation"),
    _a("hide_animation_type", AttrKind.ENUM, enum=ANIMATION_EASINGS, section="animation"),
    _a("animation_type", AttrKind.ENUM, enum=ANIMATION_EASINGS, section="animation"),
    _a("animation_time", AttrKind.INT, section="animation"),
    _a("fade_time", AttrKind.INT, section="animation"),
    _a("fade_type", AttrKind.ENUM, enum=ANIMATION_EASINGS, section="animation"),
    _a("show_sound", AttrKind.SOUND_REF, section="sound"),
    _a("hide_sound", AttrKind.SOUND_REF, section="sound"),
)

_SCROLL_ATTRS = (
    _a("verticalScrollbar", AttrKind.STR, section="scroll"),
    _a("horizontalScrollbar", AttrKind.STR, section="scroll"),
    _a("autohide_scrollbars", AttrKind.BOOL, default="yes", section="scroll"),
    _a("scroll_wheel_factor", AttrKind.INT, default="40", section="scroll"),
    _a("vertical_scroll_step", AttrKind.INT, section="scroll"),
    _a("smooth_scrolling", AttrKind.BOOL, section="scroll"),
    _a("drag_scroll", AttrKind.STR, section="scroll"),
    _a("margin", AttrKind.SIZE, section="scroll"),
)


# ----------------------------------------------------------------------- widgets
def _widget(type_key: str, *, container: bool, category: str, icon: str,
            attrs: tuple[AttrSpec, ...]) -> WidgetSpec:
    return WidgetSpec(type_key, container, attrs, category, icon)


_BUTTON_ATTRS = (
    _NAME, _POSITION, _SIZE, _ORIENTATION, _ORIGO, _CENTERPOSITION,
    _QUAD_SPRITE, _SPRITE, _FRAME, _SCALE,
    _a("buttonText", AttrKind.LOC_TEXT, section="text"),
    _a("buttonFont", AttrKind.FONT_REF, section="text"),
    _a("format", AttrKind.ENUM, enum=TEXT_FORMATS, section="text"),
    _a("shortcut", AttrKind.STR, section="behavior"),
    _a("clicksound", AttrKind.SOUND_REF, section="sound"),
    _a("oversound", AttrKind.SOUND_REF, section="sound"),
    _a("web_link", AttrKind.STR, section="behavior"),
    _a("rotation", AttrKind.FLOAT, section="graphics"),
    _ALWAYSTRANSPARENT,
    _a("pdx_disabled_tooltip", AttrKind.LOC_TEXT, section="behavior"),
    _a("pdx_disabled_tooltip_delayed", AttrKind.LOC_TEXT, section="behavior"),
) + _TOOLTIPS

_WIDGET_LIST: tuple[WidgetSpec, ...] = (
    _widget("containerWindowType", container=True, category="containers", icon="▣",
            attrs=(
                _NAME, _POSITION, _SIZE, _ORIENTATION, _ORIGO,
                _a("background", AttrKind.BACKGROUND, section="graphics"),
                _a("clipping", AttrKind.BOOL, default="yes", section="layout"),
                _a("moveable", AttrKind.BOOL, section="behavior"),
                _a("fullScreen", AttrKind.BOOL, section="layout"),
                _a("priority", AttrKind.INT, section="layout"),
                _a("click_to_front", AttrKind.BOOL, section="behavior"),
                _a("dontRender", AttrKind.STR, section="behavior"),
                _ALWAYSTRANSPARENT,
            ) + _SCROLL_ATTRS + _WINDOW_ANIMATION),
    _widget("windowType", container=True, category="containers", icon="▤",
            attrs=(
                _NAME, _POSITION, _SIZE, _ORIENTATION, _ORIGO,
                _a("backGround", AttrKind.BACKGROUND, section="graphics"),
                _a("moveable", AttrKind.BOOL, section="behavior"),
                _a("fullScreen", AttrKind.BOOL, section="layout"),
                _a("dontRender", AttrKind.STR, section="behavior"),
                _a("horizontalBorder", AttrKind.STR, section="layout"),
                _a("verticalBorder", AttrKind.STR, section="layout"),
            )),
    # NB: no `size` — an iconType is sized by its sprite (× scale), not a size block.
    _widget("iconType", container=False, category="graphics", icon="🖼",
            attrs=(
                _NAME, _POSITION, _ORIENTATION, _ORIGO, _CENTERPOSITION,
                _SPRITE, _QUAD_SPRITE, _FRAME, _SCALE,
                _a("rotation", AttrKind.FLOAT, section="graphics"),
                _a("preserve_aspect_ratio", AttrKind.BOOL, section="graphics"),
                _ALWAYSTRANSPARENT,
            ) + _TOOLTIPS),
    _widget("buttonType", container=False, category="controls", icon="🔘",
            attrs=_BUTTON_ATTRS),
    _widget("guiButtonType", container=False, category="controls", icon="🔘",
            attrs=_BUTTON_ATTRS),
    _widget("checkBoxType", container=False, category="controls", icon="☑",
            attrs=_BUTTON_ATTRS),
    # NB: no `size` — an instantTextBoxType is sized by its maxWidth/maxHeight box.
    _widget("instantTextBoxType", container=False, category="text", icon="𝐓",
            attrs=(_NAME, _POSITION, _ORIENTATION, _ORIGO,
                   _ALWAYSTRANSPARENT,
                   _a("scrollbarType", AttrKind.STR, section="scroll"),
                   ) + _TEXT_COMMON + _TOOLTIPS),
    _widget("textBoxType", container=False, category="text", icon="𝐓",
            attrs=(_NAME, _POSITION, _SIZE, _ORIENTATION, _ORIGO,
                   _ALWAYSTRANSPARENT) + _TEXT_COMMON),
    _widget("editBoxType", container=False, category="controls", icon="✎",
            attrs=(_NAME, _POSITION, _SIZE, _ORIENTATION, _ORIGO,
                   _ALWAYSTRANSPARENT,
                   _a("ignore_tab_navigation", AttrKind.BOOL, section="behavior"),
                   _a("only_numbers", AttrKind.BOOL, section="behavior"),
                   ) + _TEXT_COMMON),
    _widget("gridBoxType", container=False, category="layout", icon="▦",
            attrs=(
                _NAME, _POSITION, _SIZE, _ORIENTATION,
                _a("slotsize", AttrKind.SIZE, section="grid"),
                _a("format", AttrKind.ENUM, enum=GRID_FORMATS,
                   default="UPPER_LEFT", section="grid"),
                _a("max_slots_horizontal", AttrKind.INT, section="grid"),
                _a("max_slots_vertical", AttrKind.INT, section="grid"),
                _a("max_slots", AttrKind.INT, section="grid"),
                _a("add_horizontal", AttrKind.BOOL, section="grid"),
                _a("add_vertical", AttrKind.BOOL, section="grid"),
            )),
    _widget("listboxType", container=True, category="layout", icon="≣",
            attrs=(
                _NAME, _POSITION, _SIZE, _ORIENTATION,
                _a("backGround", AttrKind.STR, section="graphics"),
                _a("spacing", AttrKind.INT, section="layout"),
                _a("scrollbartype", AttrKind.STR, section="scroll"),
                _a("borderSize", AttrKind.SIZE, section="layout"),
                _a("horizontal", AttrKind.BOOL, section="layout"),
                _a("priority", AttrKind.INT, section="layout"),
                _ALWAYSTRANSPARENT,
            )),
    _widget("smoothListBoxType", container=True, category="layout", icon="≣",
            attrs=(
                _NAME, _POSITION, _SIZE, _ORIENTATION,
                _a("spacing", AttrKind.INT, section="layout"),
                _a("scrollbartype", AttrKind.STR, section="scroll"),
                _a("borderSize", AttrKind.SIZE, section="layout"),
                _a("horizontal", AttrKind.BOOL, section="layout"),
                _ALWAYSTRANSPARENT,
            )),
    _widget("OverlappingElementsBoxType", container=False, category="layout", icon="⧉",
            attrs=(
                _NAME, _POSITION, _SIZE, _ORIENTATION,
                _a("spacing", AttrKind.FLOAT, section="layout"),
                _a("format", AttrKind.ENUM, enum=TEXT_FORMATS, section="layout"),
                _a("horizontal", AttrKind.BOOL, section="layout"),
                _a("bordersize", AttrKind.SIZE, section="layout"),
                _ALWAYSTRANSPARENT,
            )),
    _widget("scrollbarType", container=True, category="controls", icon="↕",
            attrs=(
                _NAME, _POSITION, _SIZE, _ORIENTATION,
                _a("slider", AttrKind.STR, section="scroll"),
                _a("track", AttrKind.STR, section="scroll"),
                _a("leftbutton", AttrKind.STR, section="scroll"),
                _a("rightbutton", AttrKind.STR, section="scroll"),
                _a("borderSize", AttrKind.SIZE, section="layout"),
                _a("minValue", AttrKind.FLOAT, section="scroll"),
                _a("maxValue", AttrKind.FLOAT, section="scroll"),
                _a("stepSize", AttrKind.FLOAT, section="scroll"),
                _a("startValue", AttrKind.FLOAT, section="scroll"),
                _a("horizontal", AttrKind.BOOL, section="scroll"),
                _a("priority", AttrKind.INT, section="layout"),
                _a("setTrackFrameOnChange", AttrKind.BOOL, section="scroll"),
            )),
    _widget("extendedScrollbarType", container=True, category="controls", icon="↕",
            attrs=(
                _NAME, _POSITION, _SIZE, _ORIENTATION,
                _a("slider", AttrKind.STR, section="scroll"),
                _a("track", AttrKind.STR, section="scroll"),
                _a("increaseButton", AttrKind.STR, section="scroll"),
                _a("decreaseButton", AttrKind.STR, section="scroll"),
                _a("borderSize", AttrKind.SIZE, section="layout"),
                _a("minValue", AttrKind.FLOAT, section="scroll"),
                _a("maxValue", AttrKind.FLOAT, section="scroll"),
                _a("stepSize", AttrKind.FLOAT, section="scroll"),
                _a("startValue", AttrKind.FLOAT, section="scroll"),
                _a("horizontal", AttrKind.BOOL, section="scroll"),
            )),
    _widget("dropDownBoxType", container=True, category="controls", icon="▾",
            attrs=(
                _NAME, _POSITION, _SIZE, _ORIENTATION,
                _a("expandedOnTop", AttrKind.BOOL, section="behavior"),
                _a("switch_frame_on_expand", AttrKind.BOOL, section="behavior"),
            )),
    _widget("positionType", container=False, category="layout", icon="✛",
            attrs=(_NAME, _POSITION)),
    _widget("browserType", container=False, category="controls", icon="🌐",
            attrs=(_NAME, _POSITION, _SIZE, _ORIENTATION)),
)

WIDGETS: dict[str, WidgetSpec] = {w.type_key.lower(): w for w in _WIDGET_LIST}

# background = { ... } nested block mini-schema (not a widget, has no children).
BACKGROUND_ATTRS: tuple[AttrSpec, ...] = (
    _NAME, _QUAD_SPRITE, _SPRITE, _POSITION,
    _a("clicksound", AttrKind.SOUND_REF, section="sound"),
    _ALWAYSTRANSPARENT,
)


def widget_spec(type_key: str) -> WidgetSpec | None:
    return WIDGETS.get(type_key.lower())


# ----------------------------------------------------------------------- sprites
ANIMATION_ATTRS: tuple[AttrSpec, ...] = (
    _a("animationmaskfile", AttrKind.TEXTURE),
    _a("animationtexturefile", AttrKind.TEXTURE),
    _a("animationtexturescale", AttrKind.SIZE),
    _a("animationtype", AttrKind.ENUM, enum=SPRITE_ANIM_TYPES),
    _a("animationblendmode", AttrKind.ENUM, enum=SPRITE_BLEND_MODES),
    _a("animationrotation", AttrKind.FLOAT),
    _a("animationrotationoffset", AttrKind.POSITION),
    _a("animationlooping", AttrKind.BOOL),
    _a("animationtime", AttrKind.FLOAT),
    _a("animationdelay", AttrKind.FLOAT),
    _a("animationframes", AttrKind.SCRIPT),
)

_SPRITE_COMMON = (
    _NAME,
    _a("texturefile", AttrKind.TEXTURE),
    _a("effectFile", AttrKind.STR),
    _a("loadType", AttrKind.ENUM, enum=LOAD_TYPES),
    _a("legacy_lazy_load", AttrKind.BOOL),
    _a("transparencecheck", AttrKind.BOOL),
    _a("alwaystransparent", AttrKind.BOOL),
    _a("clicksound", AttrKind.SOUND_REF),
)

_SPRITE_KIND_LIST: tuple[SpriteKindSpec, ...] = (
    SpriteKindSpec("spriteType",
                   _SPRITE_COMMON + (_a("noOfFrames", AttrKind.INT, default="1"),),
                   has_animations=True),
    SpriteKindSpec("corneredTileSpriteType",
                   _SPRITE_COMMON + (
                       _a("size", AttrKind.SIZE),
                       _a("borderSize", AttrKind.SIZE),
                       _a("tilingCenter", AttrKind.BOOL),
                       _a("noOfFrames", AttrKind.INT),
                       _a("animation_rate_spf", AttrKind.FLOAT),
                   ),
                   has_animations=True),
    SpriteKindSpec("frameAnimatedSpriteType",
                   _SPRITE_COMMON + (
                       _a("noOfFrames", AttrKind.INT, default="1"),
                       _a("animation_rate_fps", AttrKind.FLOAT),
                       _a("looping", AttrKind.BOOL),
                       _a("play_on_show", AttrKind.BOOL),
                       _a("pause_on_loop", AttrKind.FLOAT),
                   )),
    SpriteKindSpec("textSpriteType",
                   _SPRITE_COMMON + (_a("noOfFrames", AttrKind.INT, default="1"),)),
    SpriteKindSpec("progressBarType", (
        _NAME,
        _a("textureFile1", AttrKind.TEXTURE),
        _a("textureFile2", AttrKind.TEXTURE),
        _a("size", AttrKind.SIZE),
        _a("color", AttrKind.COLOR),
        _a("colortwo", AttrKind.COLOR),
        _a("effectFile", AttrKind.STR),
        _a("horizontal", AttrKind.BOOL, default="yes"),
        _a("steps", AttrKind.INT, default="100"),
    )),
    SpriteKindSpec("maskedShieldType", (
        _NAME,
        _a("textureFile1", AttrKind.TEXTURE),
        _a("textureFile2", AttrKind.TEXTURE),
        _a("effectFile", AttrKind.STR),
    )),
    SpriteKindSpec("pieChartType", (
        _NAME,
        _a("size", AttrKind.INT),
    )),
    SpriteKindSpec("circularProgressBarType", (
        _NAME,
        _a("textureFile1", AttrKind.TEXTURE),
        _a("textureFile2", AttrKind.TEXTURE),
        _a("size", AttrKind.INT),
        _a("rotation", AttrKind.FLOAT),
        _a("amount", AttrKind.FLOAT),
    )),
    SpriteKindSpec("LineChartType", (
        _NAME,
        _a("size", AttrKind.SIZE),
        _a("linewidth", AttrKind.INT),
        _a("color", AttrKind.COLOR),
    )),
    SpriteKindSpec("arrowType", (
        _NAME,
        _a("texture", AttrKind.TEXTURE),
        _a("normal", AttrKind.TEXTURE),
        _a("specular", AttrKind.TEXTURE),
        _a("effect", AttrKind.STR),
        _a("rotation", AttrKind.FLOAT),
        _a("linewidth", AttrKind.FLOAT),
        _a("steps", AttrKind.INT),
    )),
)

SPRITE_KINDS: dict[str, SpriteKindSpec] = {
    s.type_key.lower(): s for s in _SPRITE_KIND_LIST
}


def sprite_kind(type_key: str) -> SpriteKindSpec | None:
    return SPRITE_KINDS.get(type_key.lower())


# ------------------------------------------------------------------ scripted GUI
CONTEXT_TYPES: tuple[str, ...] = (
    "player_context", "selected_country_context", "selected_state_context",
    "decision_category", "diplomatic_action", "diplomacy_target_context",
    "national_focus_context", "country_mapicon", "state_mapicon",
)

PARENT_WINDOW_TOKENS: tuple[str, ...] = (
    "top_bar", "decision_tab", "technology_tab", "trade_tab", "construction_tab",
    "production_tab", "deployment_tab", "logistics_tab", "diplomacy_tab",
    "national_focus", "politics_tab", "selected_country_view",
    "selected_state_view", "selected_country_view_info",
    "selected_country_view_diplomacy", "army_ledger", "navy_ledger",
    "civilian_ledger", "air_ledger", "tech_infantry_folder",
    "tech_support_folder", "tech_armor_folder", "tech_artillery_folder",
    "tech_land_doctrine_folder", "tech_naval_folder",
    "tech_naval_doctrine_folder", "tech_air_techs_folder",
    "tech_air_doctrine_folder", "tech_electronics_folder",
    "tech_industry_folder",
)

AI_TEST_SCOPES: tuple[str, ...] = (
    "test_self_country", "test_enemy_countries", "test_ally_countries",
    "test_neighbouring_countries", "test_neighbouring_ally_countries",
    "test_neighbouring_enemy_countries",
    "test_self_owned_states", "test_enemy_owned_states", "test_ally_owned_states",
    "test_self_controlled_states", "test_enemy_controlled_states",
    "test_ally_controlled_states", "test_neighbouring_states",
    "test_neighbouring_enemy_states", "test_neighbouring_ally_states",
    "test_our_neighbouring_states", "test_our_neighbouring_states_against_allies",
    "test_our_neighbouring_states_against_enemies", "test_contested_states",
    "test_if_only_major", "test_if_only_coastal",
)

# Scalar fields of a scripted_gui entry (shown as plain form rows).
SGUI_SCALARS: tuple[AttrSpec, ...] = (
    _a("context_type", AttrKind.ENUM, enum=CONTEXT_TYPES,
       default="player_context"),
    _a("window_name", AttrKind.STR),
    _a("parent_window_token", AttrKind.ENUM, enum=PARENT_WINDOW_TOKENS),
    _a("parent_window_name", AttrKind.STR),
    _a("parent_scripted_gui", AttrKind.STR),
    _a("dirty", AttrKind.STR),
    _a("map_mode", AttrKind.STR),
    _a("ai_test_interval", AttrKind.INT),
    _a("ai_test_variance", AttrKind.FLOAT),
    _a("ai_max_weight_taken_per_test", AttrKind.INT),
)

# Script-block fields (edited via the script dialog / structured sub-editors).
SGUI_SCRIPTS: tuple[str, ...] = (
    "visible", "effects", "triggers", "properties", "dynamic_lists",
    "ai_enabled", "ai_check", "ai_check_scope", "ai_test_scopes", "ai_weights",
)

SGUI_CLICK_MODIFIERS: tuple[str, ...] = ("right", "alt", "control", "shift")

# properties = { <element> = { image/frame/x/y } }
SGUI_PROPERTY_KEYS: tuple[str, ...] = ("image", "frame", "x", "y")

# dynamic_lists = { <gridbox> = { ... } }
SGUI_DYNAMIC_LIST_KEYS: tuple[str, ...] = (
    "array", "value", "index", "change_scope", "entry_container",
    "country_scope_entry_container", "ai_weights",
)
