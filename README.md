# ANKA — редактор модов Hearts of Iron IV

ANKA автоматизирует работу с модами HOI4: страны, фокусы, события, нац. духи (идеи),
личности, решения, боевые порядки (OOB), локализация — с автогенерацией файлов и
конвертацией графики.

> **Разработчику (в т.ч. ИИ-ассистенту):** сначала прочитайте
> [«Как устроен редактор»](#как-устроен-редактор-шаблон-для-нового) — там все паттерны
> (block-backed модель, дерево, инспектор, owner-протокол, скрипт-редактор, локализация)
> и подводные камни Tk. Почти любой новый редактор — копия `editors/decisions/` или
> `editors/ideas/` со заменой доменной модели.

## Установка
```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
python main.py
```
`tkinterdnd2` опционален: без него отключается перетаскивание файлов из проводника
(загрузка через диалог остаётся) — код обязан деградировать до диалога выбора файла.

## Архитектура
Слоистая, SOLID; зависимости направлены внутрь (UI → services → core/domain). Новый
редактор = подкласс `EditorModule`, зарегистрированный в реестре: получает `ModContext`,
сам строит свой UI-таб; ядро (парсер, конвертер, i18n, темы) переиспользуется через
сервисы, поэтому добавление функции не трогает существующий код.

```
main.py                     точка входа
anka/
├── app.py                  контроллер приложения + навигация по экранам
├── config/                 константы (пути, размеры, языки), settings.json
├── core/                   инфраструктура без UI
│   ├── pdx/                парсер/сериализатор Paradox-скрипта (lark): grammar, nodes, parser, writer
│   ├── localisation/       чтение/запись .yml
│   ├── images/             конвертация jpg/png → tga/dds: converter, flags, icons
│   └── gfx.py              SpriteRegistry (запись спрайтов) + SpriteResolver (имя→файл)
├── domain/                 доменные модели (Mod, ModContext)
├── services/               сценарии: mod_repository, country/state/ideology/character/technology/trait;
│                           _fsutil.ensure_filename_case (регистр имён под ванилу)
├── editors/                редакторы-плагины (регистрируются в реестре)
│   ├── base.py             EditorModule (ABC) + EditorRegistry + EditorServices
│   ├── common/             переиспользуемые компоненты: диалоги (пикеры, предпросмотр PDX,
│   │                       галерея иконок), визуальный редактор скриптов
│   │                       (BlockTreeEditor + ScriptEditorDialog), variants.py
│   ├── countries/          страны (флаги, цвета, названия, территория, политика, техи, персонажи)
│   ├── focuses/ decisions/ events/ ideas/ oob/     см. разделы ниже
│   ├── general/            стартовая вкладка «Общее» (обзор мода)
│   ├── characters/         «Личности» (создание персонажей всех ролей)
│   ├── effects/            каталог эффектов/триггеров/модификаторов (JSON + загрузчик)
│   └── _stubs.py           заглушки: dynamic_modifiers, localisation, ideologies, technologies, map
└── ui/                     tkinter: окна (main_menu, settings, mod_list, mod_editor), темы, i18n,
                            виджеты (ImageDropZone, ScrollableFrame, dnd-обёртка tkinterdnd2)
locales/                    переводы интерфейса (ru.json, en.json)
images/                     логотип и иконки приложения
```

## Редакторы

**Общая конвенция** (focuses / decisions / events / ideas / oob): дерево слева —
мод-объекты редактируемы, ваниль read-only серым, «Копировать в мод» создаёт
override-файл с **точным** именем ванильного; инспектор справа; панель проблем с
переходом к объекту по клику; под капотом — block-backed модель
(`services/<name>_service.py`), быстрый quick-scan (глубина по скобкам, т.к. ванильные
отступы ненадёжны) и collision-safe `LocCatalog` (`services/_locutil.py`). Новый объект
попадает в первый мод-файл, уже содержащий сущность, иначе в `anka_<...>.txt`
(`mod_target_doc`).

### Фокусы (`editors/focuses/`)
Canvas-редактор `common/national_focus`:
- **canvas.py** — игровая сетка: иконки (DDS через `SpriteResolver`+Pillow, асинхронная
  пачечная загрузка — большое дерево не блокирует UI), линии prerequisite (пунктир =
  ИЛИ-группа), mutually exclusive, drag с привязкой к сетке (учитывая
  `relative_position_id`), zoom (Ctrl+колесо), панорамирование, сворачиваемая мини-карта.
- **inspector.py** — id (переименование обновляет все ссылки и локализацию),
  название/описание по языкам, иконка (галерея всех `GFX_focus_*`/`GFX_goal_*`
  игры+DLC+мода или импорт своей), позиция, cost, флаги, `search_filters` (значения
  сканируются из игры), AND/OR-группы prerequisite, mutex (симметрично), 8 скриптовых
  блоков.
- **script_editor.py** — текстовый PDX-редактор с подсветкой, строгой валидацией (битый
  скрипт не попадёт в файл) и браузером каталога эффектов/триггеров со вставкой сниппетов.
- **Shared/joint фокусы**: определения читаются по всем файлам мода+игры; ветки,
  подключённые через `shared_focus =`, рисуются рядом (read-only, если чужой файл).
- Валидация: дубли id, битые ссылки, циклы, коллизии клеток, отсутствующие
  иконки/локализация.

### Решения (`editors/decisions/`)
Дерево «категория → решения». Инспекторы решения и категории: иконки `GFX_decision_*`
(галерея+импорт), cost/тайминги, флаги, скриптовые блоки через общий визуальный редактор
(`allowed`/`visible`/`available`/`target_trigger` — только триггеры;
`modifier`/`targeted_modifier` — с каталогом модификаторов), локализация по языкам.
Категория может собираться из нескольких файлов — агрегируется корректно. Валидация:
дубли id, категория без определения, битые иконки, `selectable_mission` без таймаута.

### События (`editors/events/`)
Дерево «неймспейс → событие» (подпись `id · [C/N/U/O/S] · заголовок`). Полная форма для 5
видов (`country_event`/`news_event`/`unit_leader_event`/`operative_leader_event`/
`state_event`; вид фиксируется при создании).
- **Условные варианты** `title`/`desc`/`picture` — `editors/common/variants.py`
  (`VariantListEditor`): упорядоченный список (▲/▼ — игра берёт первый с истинным
  триггером); простая (`title = key`) и условная (`title = { text = key trigger = {...} }`)
  формы конвертируются автоматически; у picture внутренний ключ — `picture`, галерея
  `GFX_report_event_*`/`GFX_news_event_*` по виду события + импорт (DDS 355×140 в
  `gfx/event_pictures/` + спрайт в `anka_events.gfx`).
- **Опции** — список с ▲/▼ и drag&drop за ≡; под-форма: ключ `name` с автогенерацией
  `<id>.a..z`, локализованный текст, trigger/ai_chance, эффекты (визуальный редактор;
  известные ключи опции сохраняют позицию, неизвестные пары трактуются как эффекты и
  сохраняют порядок текста).
- id = `неймспейс.N`; `next_id` сканирует ваниль+мод (коллизия переопределила бы
  ванильное событие); переименование переносит loc-ключи `.t*`/`.d*`/`.a..z`; новое
  событие идёт в мод-файл, объявивший неймспейс, иначе в `events/anka_events.txt` (с
  авто-`add_namespace`). `LocCatalog(vanilla_filter="event")`; `LocCatalog.rename_key`
  переносит произвольные ключи (обобщение `rename` решений).
- Валидация: дубли id, id без точки, необъявленный неймспейс, событие без опций (не
  hidden), опция без name, отсутствующие локализация/картинка/спрайт,
  `mean_time_to_happen` при `is_triggered_only`, `major` у country_event.

### Идеи / нац. духи (`editors/ideas/`)
Дерево «категория → идея». Категории = ключи в `ideas = { ... }`: имя категории
(`country`, `hidden_ideas`) либо имя слота (`economy`, `tank_manufacturer`,
`political_advisor`…) — у категории со слотами идеи группируются под слотами. Подпись —
`ключ · loc · [law/designer · N слотов]`. Инспектор идеи: id (переименование переносит
loc-ключи `<id>`/`<id>_desc`, если нет `name`-override), категория-combobox (перенос),
название/описание по языкам, `name`-override, иконка (`GFX_idea_*` + импорт 63×50 DDS;
пустой `picture` → дефолт `GFX_idea_<id>`), `cost`/`removal_cost`/`level`/`ledger`,
tri-state `default`/`cancel_if_invalid`, `traits` через MultiPick (trait_service),
скриптовые блоки (`allowed`/`available`… — триггеры; `on_add`/`on_remove` — эффекты;
`modifier`/`targeted_modifier` — с каталогом; `equipment_bonus`/`research_bonus`/`rule` —
свободные). Инспектор категории показывает `common/idea_tags` (read-only) и правит
файловые флаги `law`/`designer`/`use_list_view`.

**Создание категории** (`services/_ideagui.py`) — не только запись в `idea_tags`, но и вся
GUI-обвязка политэкрана: материализация пустых блоков под слот-ключами, спрайты пустых
слотов `GFX_idea_slot_<slot>` (копия ванильного DDS в мод), доп. кадр в
`gfx/interface/idea_categories.dds` (Pillow) с override-спрайтом `GFX_idea_categories`
(`noOfFrames`+1), расширение `max_slots.x` в мод-копии `countrypoliticsview.gui`.
GUI-артефакты при удалении категории не откатываются. `LocCatalog(vanilla_filter="idea")`
(quick==parse на 213 ванильных файлах, 6483 идеи). Валидация: дубли id, неизвестный ключ
категории, битые иконки/локализация, слот закона без `default = yes`, `removal_cost = -1`
при `cost > 0`, неизвестные трейты.

### OOB — боевые порядки (`editors/oob/`)
Наземные `history/units/*.txt` (файлы с `division_template`). Дерево «OOB-файл (страна) →
шаблоны дивизий». Инспектор шаблона как игровой конструктор: **5×5** колонок основных
полков (каждая — стек, заполняется сверху) + колонка **поддержки** (до 5 рот, каждый тип
максимум раз). Кнопки «＋» только там, где добавление сохраняет форму; удаляется лишь
нижняя рота колонки — движковый инвариант (0 нарушений на 804 ванильных шаблонах): колонки
примыкают слева, без вертикальных пропусков и без «долин» (полк не короче одновременно
левого и правого соседа — запрет «заполнено-пусто-заполнено» в ряду). Тип берётся из
`common/units` (`sub_units`, `group = support` → поддержка), наземные отфильтрованы от
флота/авиации. Инспектор файла: развёрнутые дивизии (имя/шаблон/локация, добавить/удалить/
править) + импорт шаблонов с другой страны. Модель хранит полки как `list[list[type]]`,
сериализует в `{ x y }`; `units={division…}` и прочее — нетронуты. Валидация: дубли имён,
пустой шаблон, нарушение формы, повтор поддержки, неизвестный юнит, ссылка дивизии на
отсутствующий шаблон.

### Каталог эффектов и триггеров (`editors/effects/`)
Все эффекты (553) и триггеры (596) — данные: `effects.json`/`triggers.json` (имя, скоупы,
цели, описание, пример) грузятся при старте в `Effect`/`Trigger` через `ScriptCatalog` и
переиспользуются любыми редакторами. Источник — **официальная документация игры**
(`<game>/documentation/*.md`, всегда под установленную версию), не вики. Перегенерация
после обновления игры: `python -m anka.editors.effects.generate`.

Все модули одного окна получают **один** `ModContext`; тяжёлые кэш-сервисы
(`ModContext.characters`) объявлены `cached_property` на контексте — общие и когерентные
(персонаж из «Личностей» сразу виден в «Странах» без повторного скана).

---

# Как устроен редактор (шаблон для нового)

Все «файловые» редакторы (decisions / events / ideas / oob) — три слоя: **сервис
(block-backed модель) → каркас (дерево + тулбар) → инспекторы**. Быстрее всего
скопировать ближайший: `decisions/` — простейший эталон; `ideas/` и `oob/` — с
инспектором-категорией и нестандартной моделью соответственно.

## 1. Сервис (`services/<name>_service.py`)
Block-backed: доменные объекты — тонкие вьюхи над узлами PDX-дерева (не копии), поэтому
неизвестные ключи переживают load→save без потерь.
- **Наследуйтесь от `services/_pdxview.BlockView`** — над `self.block: Block` даёт:
  `get_raw/set_raw` (скаляр), `get_flag/set_flag` (tri-state; значение, равное дефолту из
  `FLAG_DEFAULTS`, **не пишется**), `get_script/set_script` (блок ↔ текст, строгий парс).
  Задайте классовый `FLAG_DEFAULTS`. Примеры: `Decision`, `IdeaDef`, `DivisionTemplate`.
- **Ref / Document / Service**: `XxxDocRef(rel_file, source_root, is_vanilla, edited,
  <quick-scan>)` (лёгкое описание файла + результат скана для дерева);
  `XxxDocument(ref, root: Block)` (распарсенный файл + навигация); `XxxService(context)`
  (CRUD + файловые операции).
- **`_quick_scan`** — регулярки + подсчёт глубины по скобкам (НЕ по отступам: ванильные —
  смесь табов и пробелов). Инвариант `quick_scan == полный парс` на всей ваниле (см.
  smoke-тесты).
- **`list_docs(include_vanilla)`** — mod-first: мод, потом игра; переопределённую модом
  ваниль метим `edited=True`; сравнение путей регистронезависимо (`rel_file.lower()`).
- **`load(ref)`** кэширует по `mtime`; повторный `load` отдаёт тот же объект (важно для
  dirty-логики). **`save(doc)`** отказывается писать ваниль (`PermissionError`), гонит путь
  через `ensure_filename_case`, пишет `dump_file` (без BOM). **`copy_to_mod(ref)`** —
  байтовая копия ваниль→мод по тому же пути (override), возвращает новый mod-`ref`.
  **`delete(ref)`** — только мод, чистит кэш.

## 2. Локализация (`services/_locutil.LocCatalog`)
Игра грузит ВСЕ `.yml`; два определения одного ключа → «loc key collision». Пишем
collision-safe:
```python
self.loc = LocCatalog(mod_path, game_path,
                      vanilla_filter="idea",              # подстрока имён ванильных .yml
                      default_pattern="anka_ideas_l_{lang}.yml")
```
`get/has/set(key, lang, value)`: в мод-файл, где ключ уже есть → иначе в **мод-копию**
ванильного .yml, определяющего ключ (override целиком) → иначе в свой `default_pattern`.
`.yml` пишется **С BOM** (`utf-8-sig`) — обратно скриптам! `rename(old, new)` переносит
ключ и его `_desc`-двойник; `rename_key` — произвольный. Паттерн:
`name_of/desc_of/set_loc/has_any_name`.

## 3. Каркас (`editors/<name>/editor.py`)
Подкласс `EditorModule` + `@EditorRegistry.register`. Обязательные атрибуты: `id`,
`name_key`, `desc_key`, `order` (позиция в сайдбаре), `implemented=True`. Метод
`build(parent) -> ttk.Widget` строит корневой виджет. Доступно: `self.context`
(ModContext), `self.services` (EditorServices), `self.t` (переводчик), `self.palette`
(цвета темы).
- **Тулбар:** ☰ панель, ➕ объект, 🗂 категория/файл, 💾 (`save_all`), ⧉ «Копировать в
  мод» (контекстно для ваниль-выбора), ⚠ проблемы.
- **Дерево** (`ttk.Treeview`), iid `"<тип>::<rel_file>::<id>"` (напр. `c::country`,
  `i::common/ideas/SOV.txt::idea_id`); ваниль тегом `vanilla` (серый, read-only); поиск,
  сохранение раскрытости узлов при перестройке, `<Delete>` → удаление с confirm.
- **`reload_tree()`** перечитывает мод-документы целиком + ваниль из quick-scan;
  **`_refresh_tree()`** перерисовывает по текущему поиску, сохраняя открытые узлы.
- При скрытии дерева снимайте резерв ширины колонки (`columnconfigure(0, minsize=0)`),
  иначе остаётся «мёртвая» полоса.
- **Dirty:** `mark_dirty(doc)` кладёт `doc.ref.path` в `self._dirty`; `save_all()` пишет
  только грязные; `on_leave()` = `save_all()`. При простом просмотре редактор не пишет
  ничего.
- **Проблемы:** `service.validate(docs, ...)` → `Issue(severity, code, subject, detail,
  rel_file)`; текст из локали `"<name>.issue.<code>"`; клик выделяет объект в дереве.

## 4. Инспектор (`editors/<name>/inspector.py`)
Подкласс `editors/common/inspector_base.InspectorBase` — «тупая форма» над block-backed
моделью: значения грузятся в `show(...)`, правки коммитятся сразу (с дебаунсом текстовых
полей) в модель + `owner.mark_dirty(doc)`. База даёт: `self.body` (скроллируемая карточка,
2 колонки), `_debounce(key, commit, delay)`, `flush_pending()` (сбрасывать отложенные
коммиты при смене выбора и в `show`), `_guard()` (коммит только когда не грузимся и не
read-only), `_entry_row(...)`, `_set_state_all(editable)` (массовый read-only),
`_icon_preview(sprite, size)` (**держите ссылку на PhotoImage** в атрибуте, иначе Tk
соберёт картинку GC). Паттерн `show(doc, obj, editable)`: `flush_pending()` →
`_loading=True` → заполнить поля → `_loading=False`; коммиты проверяют `_guard()`.

## 5. Owner-протокол
Диалоги и `BlockTreeEditor` из `editors/common/` вызывают у owner-редактора:

| Метод / атрибут | Назначение |
|---|---|
| `t`, `palette` | переводчик, цвета |
| `value_options(vtype)` | `list[(display, value)]` для `vtype` ∈ `country/state/idea/focus/event/modifier` |
| `loc_language` | текущий язык (`"russian"`/`"english"`) |
| `loc_get/loc_set(key, lang, text)` | чтение/запись loc (tooltip-поля скрипт-редактора) |
| `resolver`, `resolver_ready()` | `SpriteResolver` + флаг «прогрет» |
| `import_icon(path, obj)` | конверт+регистрация картинки, возвращает имя спрайта |
| `mark_dirty(doc)` / `reload_tree()` / `refresh_tree_labels()` | сохранение и перерисовка |
| `known_ids()` | все id в области видимости (проверка дубля при rename) |

`value_options` кэшируйте в `self._value_options[vtype]` (кроме `"event"` — создаются/
переименовываются в сессии). Ветка `"focus"` — через `FocusService.focus_ids()` (дешёвый
скан national_focus).

## 6. Скрипт-редактор и типизированные значения (`editors/common/block_editor.py`)
`ScriptEditorDialog(master, editor, title, initial_text, on_submit, kinds, focus_id)` —
две вкладки (визуальный `BlockTreeEditor` + текст с подсветкой и строгим парсом; битый
скрипт не сохранится). `kinds ⊆ ("effect","trigger")`. Правило: условия (`allowed`,
`visible`, `available`…) — `("trigger",)`; эффекты/награды (`on_add`, `complete_effect`…) —
`("effect","trigger")`.

Чтобы поле/ключ получил **пикер значения** или блок вставлялся правильно, правьте таблицы
модуля (единая точка на все редакторы):
- **`_KEY_TYPES`** — ключ внутри блока → тип пикера (`target/tag/country → country`,
  `state → state`, `idea → idea`…).
- **`_VALUE_TYPES`** — эффект/триггер в **скалярной** форме → тип (`declare_war_on`,
  `complete_national_focus → focus`…).
- **`_LIST_ITEM_TYPES`** — эффект-список `key = { a b c }`: тип bare-элементов
  (`add_ideas → idea`); вставляется пустым блоком + кнопка быстрого добавления элемента.
- **`_BLOCK_EFFECT_TEMPLATES`** — эффекты, обязанные быть блоком, но скупо
  задокументированные: вставляются готовым скелетом
  (`declare_war_on = { target = FROM type = annex_everything }` и т.п.). Держите под API
  игры.
- **`_MODIFIER_PARENTS`** — блоки (`modifier`, `targeted_modifier`…), внутри которых пикер
  предлагает каталог статических модификаторов.

`node_from_catalog(item)` строит вставляемый `Pair` (список → пустой блок; блок-шаблон →
скелет; иначе пример из каталога; иначе `name = `). Каталог — `editors/effects/`
(`ScriptCatalog.find/search/modifiers`).

## 7. Иконки и спрайты
- **Чтение:** `SpriteResolver.for_mod(mod_path, game_path)` — карта `имя→файл` по всем
  `interface/**/*.gfx` мода+игры+DLC; `resolve(name) -> Path|None`. Стройте в фоне
  (`resolver_ready()`) — первый `resolve` парсит все .gfx.
- **Создание:** `context.icons.add_<focus|decision|idea|event|character>_icon(source, name)`
  конвертит в DDS нужного размера и регистрирует `SpriteType` в `anka_<...>.gfx`
  (`core/images/icons.py`). После — `resolver.add(sprite_name, dds_path)`, чтобы не
  перестраивать всю карту.
- **Галерея:** `IconPickerDialog(..., prefixes=("GFX_idea_",), on_import=...)`.
- **`SpriteRegistry(gfx_path)`** — идемпотентная правка одного .gfx
  (`register/find/register_sprite/save`) для сложных случаев (override-спрайты с
  `noOfFrames`, спрайты пустых слотов идей — см. `services/_ideagui.py`).

## 8. Регистрация, порядок, локали
- Импорт нового редактора в `editors/__init__.py` = само-регистрация через декоратор.
  Заглушки «в разработке» — `WipEditor` в `_stubs.py`.
- `order` (меньше — выше): `general 0 · countries 10 · focuses 20 · events 30 · ideas 40 ·
  characters 50 · decisions 60 · dynamic_modifiers 70 · localisation 80 · ideologies 90 ·
  technologies 95 · oob 100 · map 110`. «Общее» (0) открывается по умолчанию.
- **Локали** — `locales/ru.json`, `locales/en.json`: **плоский** словарь dotted-ключей
  (`"oob.new_template": "…"`), **без BOM**. Тултипы — ключи `help.<topic>`, привязка
  `ui.widgets.tooltip.attach_help(widget, self.t, "<topic>", palette)` (нет ключа → нет
  тултипа). Добавляйте ключи в ОБА файла.

## 9. Подводные камни Tk
- **Не называйте атрибуты как Tk-внутренние** (`self._name`, `self._options`):
  `Toplevel`/`Frame` уже хранят там своё; `self._name = tk.StringVar()` роняет `destroy()`
  (`unhashable type: 'StringVar'`) — диалог не закроется, колбэк после `destroy()` не
  выполнится. Используйте `self._name_var` / `self._name_lbl`.
- **PhotoImage** держите ссылкой в атрибуте (иначе GC → пустой прямоугольник); отдельная
  ссылка на каждое превью.
- **Читайте значения виджетов ДО `destroy()`** (комбобокс/энтри умирают с окном).
- **`selection_set` в тестах** шлёт `<<TreeviewSelect>>` через очередь — в headless зовите
  `root.update()` (не `update_idletasks()`).
- **`Block.__bool__` всегда True** — пустоту проверяйте `len(block)` (см. §7 форматов).
- **Повторяющиеся ключи** (`slot`, `option`, `set_technology`) — только `get_all`/`add`,
  никогда `set` (перезапишет первый и потеряет остальные).

## 10. API PDX-дерева (`core/pdx`)
`parse(text) -> Block` (строгий, `recover=False`), `parse_file(path)`,
`dumps(block, top_level=True) -> str`, `dump_file(block, path)` (без BOM). Узлы:
`Block(items, tag)`, `Pair(key, value, op)`, `Scalar(raw, quoted)`.
```python
b.get(key)            # первое значение (Value|None)        b.get_all(key)   # все значения
b.get_scalar(key, d)  # raw-строка или d                    b.get_block(key) # вложенный Block|None
b.has(key)            # есть ли ключ                         b.pairs()        # итератор Pair
b.array_values()      # bare-скаляры массива { a b c }
b.set(key, value)     # заменить первый / добавить          b.add(key, value)# добавить (дубль ок)
b.add_value(v)        # bare-элемент                         b.remove(key)    # удалить все с ключом
Scalar.of(x)          # авто-квотирование строк с пробелами  s.as_bool()/as_int()/as_float()
```
`value` в `Pair`/`get` — `Scalar | Block`. Строки с пробелами/именами шаблонов:
`Scalar(value, quoted=True)`. `dumps(block, top_level=False)` — для содержимого
скрипт-поля (без внешних скобок).

## 11. Проверка изменений (headless smoke)
Быстрая регрессия без GUI: `root = create_root(); root.withdraw()`, тема +
`EditorServices`, `ModContext` на временном моде в scratchpad, `editor.build(root)`,
`root.update()`, дальше дёргаем методы и проверяем дерево/модель. Для сервисов — прогон
`parse_file`/`dumps` и `quick_scan == parse` по каталогам реальной игры. Общий тест:
собрать ВСЕ редакторы (`EditorRegistry.all()`) в скрытом руте — ловит битые импорты,
локали, ошибки построения UI. После правок обязательно
`python -c "import anka.app, anka.editors"` и сборка редакторов. Примеры смоков — в истории
коммитов (scratchpad-каталог сессии).

---

# Форматы файлов HOI4 (справка для разработчиков)

Всё содержимое мода — файлы в дереве, повторяющем структуру игры. Игра грузит **базовую
игру + все DLC + все включённые моды**, накладывая их друг на друга (см. «Правила
переопределения»).

## 1. Paradox-скрипт (Clausewitz) — `.txt`, `.gfx`, `.gui`, `.mod`, `.asset`
Рекурсивный «ключ = значение»; на нём почти всё, кроме локализации и карты.
```
key = value                 # скаляр (число, слово, дата 1936.1.1, yes/no)
key = "строка"
key = { a b c }             # массив скаляров
block = { inner = 1  color = rgb { 10 20 30 } }   # вложенный / «тегированный» (rgb/hsv) блок
option = {...} option = {...}  # ключи МОГУТ повторяться
key >= 5   key < 3          # операторы сравнения (в триггерах)
@var = 20   x = @var        # скрипт-переменные
# комментарий до конца строки
```
Свойства (учтены в `anka/core/pdx/`):
- **Регистронезависимость ключей.** `spriteType` ≡ `SpriteType` (в `_leader_portraits.gfx`
  доминирует строчный). Резолвер спрайтов и чтение блоков сравнивают без учёта регистра.
- **Повторяющиеся ключи** — норма (`option`, `set_technology`, `recruit_character`). Модель
  хранит упорядоченный список, различает `get` (первый) и `get_all` (все).
- **Несбалансированные скобки** встречаются в ваниле — у парсера режим восстановления.
- **Кодировка: UTF-8 БЕЗ BOM.** Ведущий BOM ломает движок (`Unexpected token: ﻿…`).
  Пишется `pdx.dump_file` (без BOM), читается `utf-8-sig` (BOM снимается, если есть).

## 2. Локализация — `.yml` (псевдо-YAML, **не** настоящий YAML)
```
l_english:
 KEY:0 "Текст с $переменной$ и [ScopedLoc.GetName]"
 OTHER_KEY:1 "…"
```
- Первая строка `l_<язык>:`. Запись: ` KEY:<версия> "значение"` (ведущий пробел).
- **Кодировка: UTF-8 С BOM обязательно** — иначе игра молча не грузит файл (противоположность
  скриптам!). Пишется `LocFile.save` (`utf-8-sig`).
- Ключ должен встречаться один раз во всех загруженных файлах, иначе `loc key collisions`.
- Язык также по суффиксу имени: `*_l_<язык>.yml` (напр. `countries_l_russian.yml`).

Ключи названий страны (`CountryService`): `TAG`, `TAG_DEF` (полное), `TAG_ADJ`
(прилагательное); варианты по идеологии — `TAG_<ideology>` (+`_DEF`/`_ADJ`). Партии — в
**отдельном** `parties_l_*.yml`: `TAG_<ideology>_party`, `_party_long`, `_party_desc`.

## 3. Изображения
| Что | Формат | Размеры | Куда |
|-----|--------|---------|------|
| Флаг страны | **TGA** 32-бит, БЕЗ RLE | 82×52, 41×26 (`medium/`), 10×7 (`small/`) | `gfx/flags/[TAG].tga` (+`_<ideology>` косметические) |
| Иконка фокуса | **DDS** (ARGB8888/DXT5) | ~100×88 | `gfx/interface/goals/` + спрайт в `interface/*.gfx` |
| Иконка идеи/духа | **DDS** | ~63×50 (large), 63×38 | `gfx/interface/ideas/` |
| Портрет лидера/командира | **DDS** | 156×210 (large) | `gfx/leaders/<TAG>/` |
| Портрет-иконка советника | **DDS** | ~65×67 (= `GFX_idea_*`) | `gfx/interface/ideas/` |
| Картинка события | **DDS** | 355×140 | `gfx/event_pictures/` |

- Графика подключается **спрайтом** `SpriteType` из `interface/*.gfx` (`GFX_*` →
  `texturefile`). Путь текстуры — относительно корня контента, где лежит `.gfx` (в DLC —
  относительно папки DLC). `core.gfx.SpriteResolver` собирает `имя → файл` по всем
  `interface/**/*.gfx` мода, игры и DLC.
- ANKA принимает jpg/png/bmp/… и сама конвертирует/масштабирует в нужный TGA/DDS
  (`core.images`). Pillow ≥10 пишет TGA и DDS (несжатый ARGB8888 и DXT5) без внешних утилит.

## 4. Карта — `map/` (пиксельные BMP, не сжатые)
Все bmp — разрешение `provinces.bmp` (в ваниле 5632×2048), кроме пониженных `trees.bmp` и
`world_normal.bmp`.

| Файл | Формат | Назначение |
|------|--------|-----------|
| `provinces.bmp` | 24-бит RGB | провинция = **уникальный** цвет; границы там, где цвета соприкасаются |
| `definition.csv` | CSV `;` | `id;r;g;b;type;coastal;terrain;continent`; `type` = land/sea/lake |
| `heightmap.bmp` | 8-бит grayscale | высота (0 низ, 255 верх; уровень моря ≈95) |
| `terrain.bmp` | 8-бит индексный | тип местности (палитра-индекс) |
| `rivers.bmp` | 8-бит индексный | реки, толщина ровно 1px, только ортогонально; спец-палитра (исток/слияние/направления) |
| `trees.bmp` | 8-бит индексный | плотность лесов (пониженное разрешение) |
| `cities.bmp` | 8-бит индексный | текстуры городов |
| `world_normal.bmp` | 24-бит | карта нормалей (обычно половинное разрешение) |
| `default.map` | PDX-скрипт | ссылки на файлы карты, диапазоны sea/lake провинций |
| `adjacencies.csv` | CSV `;` | особые связи: проливы, каналы (Суэц/Панама), непроходимость |

Провинции → штаты: `history/states/<id>-<Name>.txt`
(`state = { id provinces={..} history={ owner=TAG } }`). Штаты → регионы:
`map/strategicregions/`, `map/supplyareas/`.
> Редактор карты пока не реализован; форматы задокументированы на будущее. Для BMP критичны
> точная битность и **отсутствие сжатия** — иначе игра не читает файл.

## 5. Где что лежит
| Раздел | Путь | Формат |
|--------|------|--------|
| Теги стран | `common/country_tags/*.txt` (аддитивно) | `TAG = "countries/File.txt"` |
| Определения стран | `common/countries/*.txt` | PDX (graphical_culture, `color`) |
| Цвета стран | `common/countries/colors.txt` (**заменяет** ванилу) | `TAG = { color color_ui }` |
| История стран | `history/countries/<TAG> - <Name>.txt` | PDX (capital, set_politics, set_technology, set_oob…) |
| Штаты | `history/states/*.txt` | PDX |
| Идеологии | `common/ideologies/*.txt` | `ideologies = { group = { types = {..} } }` |
| Персонажи | `common/characters/<TAG>.txt` | `characters = { id = { country_leader/advisor/… } }` |
| Трейты | `common/country_leader/*.txt` (лидеры+советники), `common/unit_leader/*.txt` (генералы/адмиралы) | `leader_traits = {..}` |
| Фокусы | `common/national_focus/*.txt` | `focus_tree`, `focus` |
| Идеи / нац. духи | `common/ideas/*.txt` | PDX |
| События | `events/*.txt` | `country_event`/`news_event`, `add_namespace` |
| Решения | `common/decisions/*.txt`, категории `common/decisions/categories/` | PDX |
| Дин. модификаторы | `common/dynamic_modifiers/*.txt` | PDX |
| Технологии | `common/technologies/*.txt` | `technologies = { tech = {..} }` |
| OOB | `history/units/*.txt` | PDX; `set_oob`/`set_naval_oob`/`set_air_oob` |
| Спрайты | `interface/*.gfx` | `spriteTypes = { SpriteType = { name texturefile } }` |
| Дескриптор мода | `descriptor.mod` (+ `<name>.mod` в `mod/`) | PDX (name, version, supported_version, tags, path, remote_file_id) |

## 6. Правила переопределения (критично!)
Движок Clausewitz накладывает контент по относительному пути:
- **Аддитивные каталоги** (большинство `common/…`, `events/`, `history/countries/`,
  `history/states/`): грузятся файлы игры И мода; чтобы переопределить объект, дают файл с
  **тем же относительным путём**.
- **Сравнение пути регистрозависимо** (даже на Windows): `SOV - Soviet Union.txt` НЕ
  переопределит ванильный `SOV - Soviet union.txt` — движок сочтёт их разными. ANKA
  зеркалит **точное** имя ванильного файла и авто-исправляет регистр на диске
  (`_fsutil.ensure_filename_case`).
- **Файлы-заменители целиком:** `common/countries/colors.txt` не сливается, а **полностью
  заменяет** ванилу. Поэтому цвет **новых** стран ANKA пишет в их файл-определение
  (`color = {..}` — игра берёт как цвет по умолчанию), а `colors.txt` трогает только при
  перекраске **ванильной** страны, засевая его всеми ванильными цветами.
- Порядок модов в плейсете тоже влияет, чей файл побеждает при коллизии.

## 7. Заметки о реализации
- **`Block.__bool__` всегда True** — иначе пустой блок был бы falsy, и
  `parent.get_block(k) or Block()` молча отсоединял бы существующий пустой блок. Пустоту
  проверяйте `len(block)` / `block.items`.
- **Общий кэш персонажей** (`ModContext.characters`) обновляется инкрементально
  (`create_or_update`/`delete`/`mark_recruited`) — полный скан только при первом обращении;
  не передавайте `refresh=True` без нужды.
- **Динамический парсинг:** идеологии, суб-идеологии, трейты, технологии, OOB и спрайты
  читаются из игры/мода на лету — хардкодить списки нельзя (моды их расширяют).
- **Авто-сохранение** настроек и форм редакторов при уходе с экрана (`on_leave`); редакторы
  пишут только при реальных изменениях (флаг dirty), чтобы просмотр ванильной страны не
  создавал файлы мода.
