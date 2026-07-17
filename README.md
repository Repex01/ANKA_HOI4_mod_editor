# ANKA — Hearts of Iron IV Mod Editor

ANKA is a free, open-source desktop editor for **Hearts of Iron IV** mods. It replaces
hand-editing Paradox script files with visual editors: draw focus trees on a canvas,
paint provinces directly on the map, generate states automatically, and let ANKA keep
ids, localisation keys and cross-references consistent for you.

Works on top of your existing mod folder — ANKA reads the base game plus your mod
(and its dependencies) with the same override rules HOI4 uses, and only ever writes
into the mod.

## Features

- **Countries** — tags, colors, flags (auto-converted to every required TGA size),
  names per ideology and language, territory, politics, starting technologies,
  recruited characters, national spirits.
- **National focuses** — visual focus-tree canvas with drag & drop, prerequisites /
  mutually-exclusive links, templates (chains, branches, grids), icon gallery and
  custom icon import, live validation.
- **Map** — province bitmap editor (brush / fill / picker with undo-redo), state
  inspector (owner, cores, claims, victory points, buildings, resources), automatic
  state & province generation with organic borders, heightmap editor (raise / lower /
  level / smooth brushes, regenerates `world_normal.bmp`), visual terrain painting
  with the `terrain.bmp` palette, strategic regions, supply areas, straits & canals,
  map validation.
- **Events, decisions, ideas, on_actions, dynamic modifiers, scripted localisation** —
  form-based editors with a visual script editor (catalog of effects / triggers /
  modifiers with inline documentation) and raw-text mode with syntax checking.
- **Characters & traits** — leaders, advisors, generals; skills, portraits, traits.
- **Technologies** — tech-tree canvas incl. custom research folders (generates the
  `.gui` layout), doctrines, equipment editor.
- **Order of battle (OOB)** — division templates on a 5×5 grid, deployed divisions,
  division name groups.
- **GUI / interface** — WYSIWYG `.gui` window designer, sprite (`.gfx`) manager with
  image import & resize to standard HOI4 sizes, scripted GUIs.
- **Quality of life** — everything is validated (problems panel per editor), vanilla
  content is read-only until copied into the mod, undo/redo where it matters,
  UI in English, Ukrainian and Russian.

## Download

Grab the latest Windows build (`ANKA.exe`, single file, no install) from the
[Releases](https://github.com/Veselator/ANKA_HOI4_mod_editor/releases) page, or run
from source (below). On first start, point ANKA at your HOI4 install folder and your
`Documents/Paradox Interactive/Hearts of Iron IV/mod` folder.

> Note: to create a *new* mod, create it once in the Paradox launcher first — then it
> appears in ANKA's mod list.

## Running from source

Requires **Python 3.12+** with Tkinter (included in the standard Windows installer).

```bash
git clone https://github.com/Veselator/ANKA_HOI4_mod_editor.git
cd ANKA_HOI4_mod_editor
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
python main.py
```

### Dependencies

| Library | Used for |
|---|---|
| [Pillow](https://pypi.org/project/Pillow/) ≥ 10.3 | image processing, native TGA / DDS read & write |
| [lark](https://pypi.org/project/lark/) ≥ 1.1 | Paradox-script (PDX) grammar parsing |
| [numpy](https://pypi.org/project/numpy/) ≥ 1.26 | map editor: province / heightmap bitmap processing |
| [tkinterdnd2](https://pypi.org/project/tkinterdnd2/) ≥ 0.4 | OS-native drag & drop (*optional* — file dialogs still work without it) |

## Tech overview

- Pure Python + Tkinter desktop app, layered architecture (UI → services →
  core/domain); each editor is a plugin (`EditorModule`) registered in a registry.
- Own Paradox-script parser/serializer (lark grammar) that preserves formatting and
  comments of untouched code; `.yml` localisation reader/writer with per-language
  catalogs.
- Map tooling is numpy end-to-end (the vanilla map is 11.5 M pixels): province bitmap,
  heightmap and terrain layers render asynchronously, edits are undoable pixel deltas.
- Content resolution is layered like the game: base game → dependency mods → your mod;
  files are copied into the mod on first edit (copy-on-write).
- Builds into a single ~30 MB `ANKA.exe` with PyInstaller; a GitHub Actions workflow
  produces a Linux binary.

For architecture details, editor-authoring patterns and contribution notes, see
[Development.md](Development.md) (Russian).

## Links

- Author's YouTube: <https://www.youtube.com/@-veselator2599>
- Support the project: <https://ko-fi.com/veselatorl>
- Bug reports / feature requests: [feedback form](https://forms.gle/QciUiKJmpSjsEKgY8)
  or GitHub issues.

## License

See the repository for license information.
