# TODO: Редактор событий (Events)

План согласован. Пишу для себя-исполнителя: конкретные файлы, API, порядок, чек-листы.
Справка: https://hoi4.paradoxwikis.com/Event_modding · ваниль: 128 файлов `events/*.txt`,
6214 country_event, 677 news_event, 33 unit_leader_event, 7 operative_leader_event,
6 state_event, 434 неймспейса.

## Решения, принятые при согласовании

1. Полные формы — для всех видов: одна модель `Event`, одна форма; вид фиксируется при
   создании (`country_event` / `news_event` / `unit_leader_event` /
   `operative_leader_event` / `state_event`).
2. **Условные варианты текста** — гибкий переиспользуемый редактор списков вариантов
   для `title` И `desc` (и картинки — см. ниже). Форматы, которые обязан понимать
   round-trip:
   ```
   title = my.1.t                                  # простой ключ
   title = { text = my.1.t.a trigger = { tag = ENG } }   # вариант с триггером
   title = { text = my.1.t.b }                     # вариант-fallback (без триггера)
   desc  = my.1.d
   desc  = { text = my.1.d.a trigger = {...} }
   picture = GFX_report_event_x                    # + условная форма тоже существует:
   picture = { picture = GFX_y trigger = {...} }   # ключ внутри — picture, не text!
   ```
   Порядок вариантов значим (игра берёт первый подошедший) → ▲/▼ у вариантов.
3. Опции: и кнопки ▲/▼, и drag&drop (перетаскивание строк в списке опций).

## Архитектура (всё переиспользуем из decisions/focuses)

- `editors/common`: BaseDialog, TextPromptDialog, SinglePick/MultiPick, IconPickerDialog
  (prefixes=...), PdxPreviewDialog, ScriptEditorDialog + BlockTreeEditor (owner-протокол:
  `t, palette, loc_language, loc_get/loc_set, value_options, resolver_ready`).
- `services/_locutil.LocCatalog(vanilla_filter="event", default="anka_events_l_{lang}.yml")`.
- `ui/widgets/tooltip.attach_help` + локали `help.event.*` / `help.script.*`.
- Паттерны: dirty-set документов, save_all/on_leave, copy_to_mod (точное имя файла),
  quick-scan подсчётом глубины скобок (отступам ванилы не верить!), сохранение
  раскрытости дерева, освобождение minsize при сворачивании панели.

## Шаг 1. `services/event_service.py`

Константы:
```python
EVENT_KINDS = ("country_event", "news_event", "unit_leader_event",
               "operative_leader_event", "state_event")
EVENT_FLAG_DEFAULTS = {
    "is_triggered_only": False, "fire_only_once": False, "hidden": False,
    "major": False, "minor_flavor": False, "fire_for_sender": True,
}
EVENT_SCRIPT_FIELDS: trigger (trigger), immediate (effect+trigger),
                     mean_time_to_happen (trigger; факт. days/months + modifier)
OPTION_KNOWN_KEYS = ("name", "trigger", "ai_chance", "original_recipient_only")
```

Модели (block-backed, как Decision):
- `TextVariant`: обёртка над item `title`/`desc`/`picture` (Scalar ИЛИ Block).
  API: `key` ("title"|"desc"|"picture"), `text` (loc-ключ или спрайт), `trigger_script`
  get/set, `is_conditional`. Хранит ссылку на родительский event-блок + сам item;
  порядок = порядок items в блоке.
- `Event`: обёртка над `Pair(kind, Block)`.
  - `id` (get/set из `id =` внутри блока), `kind` (pair.key), `namespace` = id до точки;
  - `variants(key)` -> list[TextVariant]; `add_variant(key, text, after=None)`,
    `remove_variant`, `move_variant(±1)`; `first_text(key)` для подписи в дереве;
  - `picture` get/set простой + варианты через `variants("picture")`;
  - get_flag/set_flag (tri-state: `fire_for_sender` default True → пишем только `= no`);
  - `timeout_days` raw; get_script/set_script (общий `_BlockView` — вынести из
    decision_service в `services/_pdxview.py`? НЕТ — скопировать класс, 40 строк,
    без нового рефакторинга decisions в этом шаге; TODO-заметка на будущее);
  - `options()` -> list[Option]; add/remove/duplicate/move_option(idx, delta) —
    перестановка соответствующих Pair("option", ...) в items.
- `Option`: обёртка над Pair("option", Block).
  - `name_key` get/set; `trigger_script`, `ai_chance_script` get/set;
  - `effects_script` get: dumps(Block(items минус OPTION_KNOWN_KEYS-пары)); set:
    парсим текст, заменяем в блоке все не-известные элементы новыми, известные
    оставляем на местах (известные — в начале, эффекты — после, порядок эффектов
    сохраняем как в тексте).

Refs/документы:
- `EventDocRef(rel_file, source_root, is_vanilla, edited, namespaces: list[str],
  events: list[tuple[kind, id]])` — из quick-scan: depth 0, ключи `add_namespace`
  (Pair со Scalar — брать из regex `^add_namespace\s*=\s*(\S+)`) и `*_event = {`
  → id из первого `id = (\S+)` внутри (regex по span до закрытия? проще: при
  quick-scan собирать глубину и внутри event-блока на depth 1 ловить первый id).
- `EventDocument(ref, root)`: `namespaces()`, `events() -> list[Event]`,
  `find(event_id)`, `add_namespace(ns)`.

Сервис `EventService`:
- `list_docs(include_vanilla)` (только events/*.txt, паттерн как в decisions);
- `load/save/copy_to_mod/delete` — копия decisions;
- `namespaces(include_vanilla)` -> sorted set; `next_id(ns)` — max(N)+1 по
  quick-scan ВСЕХ доков (ванила+мод), id вида `ns.N` (нечисловые суффиксы игнорить);
- `mod_target_doc(ns)`: первый мод-файл с этим неймспейсом, иначе
  `events/anka_events.txt` (+ автодобавление `add_namespace` при создании события);
- `create_event(doc, ns, kind)` -> Event с шаблоном:
  ```
  id = ns.N, title = ns.N.t, desc = ns.N.d, picture = GFX_report_event_generic_sign_treaty? 
  (country) / GFX_news_event_generic_sign_treaty (news); is_triggered_only = yes;
  option = { name = ns.N.a }
  ```
- `rename_event(event, new_id)`: id + автоключи loc: перенести `<old>.t*`, `<old>.d*`,
  `<old>.a..z` через LocCatalog.rename для каждого фактически используемого ключа
  (собрать из variants + options, заменить префикс old→new в text/name, если ключ
  начинался со старого id);
- локализация: `name_of/desc_of/set_loc/has_any_name` (копия decisions API, ключи
  произвольные — берём как есть из variants/options);
- `option_letter(i)` = "abcdefgh..."[i] для автоключей;
- `validate(docs, language, sprite_exists)` -> Issues:
  - duplicate_id (по всем видимым докам), bad_id (нет точки/пустой),
  - unknown_namespace (id.ns не объявлен `add_namespace` ни в одном доке),
  - no_options (не hidden и опций 0), option_no_name,
  - missing_loc (первый title-вариант), missing_picture (country/news без picture),
  - missing_sprite (picture не резолвится),
  - mtth_with_triggered_only (mean_time_to_happen + is_triggered_only),
  - major_non_news (major = yes у country_event — предупреждение, это легально,
    но чаще ошибка).

Тест шага 1 (scratchpad): скан всех 128 файлов quick==parse по количеству событий;
round-trip побайтово 5 крупнейших; CRUD; next_id на реальном namespace (baltic);
rename с переносом loc; варианты title/desc (Baltic.txt имеет условные desc — найти
файл с `desc = {` для теста, напр. поиск `desc = {\s*text`).

## Шаг 2. UI: каркас + дерево (`editors/events/editor.py`)

Копия каркаса decisions:
- Тулбар: ☰ панель, ➕ событие, 🗂 неймспейс (просто TextPromptDialog → запомнить
  выбор; неймспейс материализуется при первом событии ЛИБО сразу дописать
  add_namespace в anka_events.txt — выбрать второе, чтобы был виден в дереве),
  💾, копировать в мод (контекстно), ⚠ проблемы.
- Дерево: `n::<ns>` → `e::<rel_file>::<id>`; подпись события:
  `id · [C/N/U/O/S] · title-loc`; ванильные серым; поиск по id/подписи; открытость
  сохраняется; Delete-клавиша (событие → confirm-удаление; неймспейс → удаление
  всех событий мода этого ns + add_namespace, с подтверждением и подсчётом).
- reload_tree: мод-доки парсятся целиком; ванила — из quick-scan ref.events.
- `_grid_root.columnconfigure(0, minsize=0/280)` при сворачивании!

## Шаг 3. Инспектор события (`editors/events/inspector.py`)

`_InspectorBase` — скопировать из decisions (debounce/flush/_icon_preview(size)/
_set_state_all). (Пометка: третий копипаст → после событий вынести в
`editors/common/inspector_base.py`, отдельным коммитом.)

Секции формы:
1. Заголовок: id + ✎ rename, вид+файл, 🔒 для ванилы.
2. **VariantListEditor** (новый переиспользуемый виджет в `editors/events/variants.py`
   или сразу `editors/common/variants.py` — класть в common):
   - конструктор: (master, owner, get_variants, callbacks, value_kind)
     value_kind = "loc" (title/desc: текст-поле локализации per language) |
     "sprite" (picture: превью + галерея);
   - каждая строка: [▲][▼] [ключ/спрайт] [loc-текст entry (для loc)] [trigger ✎
     (ScriptEditorDialog, kinds=("trigger",))] [✕];
   - "➕ вариант" — автоключ: `<id>.t` занят → `<id>.t.a`, `.t.b`... (для desc `.d.*`);
   - подсказка: порядок важен, игра берёт первый вариант с истинным триггером.
   - title и desc — два экземпляра; picture — экземпляр с value_kind="sprite"
     (галерея prefixes: country → ("GFX_report_event_",), news →
     ("GFX_news_event_",), остальные — ("GFX_report_event_", "GFX_news_event_"));
     + импорт своей картинки: `IconService.add_event_picture(src, name)` → DDS
     355×140 (`EVENT_PICTURE_SIZE`, GAME_DIRS.GFX_EVENTS уже есть) + спрайт
     `GFX_report_event_<name>` в `anka_events.gfx`.
3. Язык локализации (общий combobox — как в decisions, меняет owner.loc_language).
4. Флаги (help.event.* на каждый) + timeout_days.
5. Скрипты: trigger / immediate / mean_time_to_happen (кнопки ✎ у текста, статус ●/○).
6. **Опции** (`editors/events/options.py` или в inspector):
   - список-Listbox/Frame строк: `[≡][▲][▼] a · name-loc · ✕` (≡ — drag-хэндл);
   - drag&drop: bind <ButtonPress/B1-Motion/ButtonRelease> на строку, ghost-подсветка
     позиции вставки, на release → move_option; ▲/▼ — то же самое по кнопке;
   - выбранная опция разворачивается в под-форму: name-ключ (автогенерация
     `<id>.<letter>` при добавлении), локализованный текст per language
     (мгновенное сохранение), trigger ✎, ai_chance ✎, effects ✎ (визуальный
     редактор, kinds=("effect","trigger"));
   - добавить/дублировать (копия блока + новый автоключ + копия loc-текста)/удалить
     (confirm не нужен для опции? — лёгкая операция, но необратимая → messagebox
     только если в опции есть эффекты).
7. Кнопки: 👁 предпросмотр (PdxPreviewDialog Pair(kind, block)), ⧉ дублировать
   (новый id = next_id(ns), loc-тексты скопировать), 🗑 удалить (confirm).

## Шаг 4. Валидация, локали, полировка

- Панель проблем (копия decisions) + клик → выделение события в дереве.
- Локали ru/en: `events.*` (панель, диалоги, confirm'ы) + `help.event.*`
  (is_triggered_only, fire_only_once, hidden, major, minor_flavor, fire_for_sender,
  timeout_days, trigger, immediate, mean_time_to_happen, title/desc/picture-варианты,
  option.ai_chance...).
- Убрать заглушку events из `_stubs.py`, импорт в `editors/__init__.py`.
- README: раздел «Редактор событий».
- Прогнать: smoke событий, smoke решений, smoke фокусов, запуск приложения.

## Чек-лист известных граблей (из прошлых итераций)

- [ ] Tk: не называть атрибуты виджетов `_name`; PhotoImage — держать ссылку на
      каждый превью отдельно.
- [ ] Читать значения виджетов ДО `destroy()` диалога.
- [ ] `winfo_ismapped` для тогглов не использовать — явные bool-флаги.
- [ ] Дебаунс-коммиты: flush при смене выбора/уходе с редактора; отменять при
      show() нового объекта ЧЕРЕЗ flush (не cancel), иначе теряются правки.
- [ ] Кнопки ✎ — вплотную к тексту (pack side=left), не к правому краю.
- [ ] Ванильные отступы кривые — quick-scan только подсчётом скобок; id с точками
      в regex (`[\w.]+`).
- [ ] Локализация ванильных ключей — только через LocCatalog (override-копия).
- [ ] `ensure_filename_case` при записи копий ванильных файлов.
- [ ] UTF-8 без BOM для .txt (dump_file), с BOM для .yml (LocFile).
- [ ] Первое открытие: резолвер спрайтов греть в фоновом потоке
      (`resolver_ready()`), в инспекторе не блокироваться на превью.
- [ ] После структурных операций сохранять раскрытость дерева и осмысленное
      выделение (родитель после удаления, новый объект после создания).

## Порядок работы

1. [ ] EventService + smoke-тесты сервиса.
2. [ ] Каркас UI + дерево + выбор/копирование ванилы.
3. [ ] Инспектор: заголовок, флаги, скрипты, VariantListEditor (title/desc).
4. [ ] Picture-варианты + импорт картинки (EVENT_PICTURE_SIZE=(355,140)).
5. [ ] Опции: список, ▲/▼, drag&drop, под-форма, автоключи.
6. [ ] Валидация + панель проблем + Delete-роутинг.
7. [ ] Локали + подсказки + README + заглушка.
8. [ ] Полный регресс (events/decisions/focuses/приложение) и правка найденного.
