# TODO: Редактор карты (`editors/map/`)

План согласован. Пишется для будущего меня: максимум проверенных фактов и точных
имён, чтобы не перепроверять. Стиль и паттерны — как в остальных редакторах
(см. README «Как устроен редактор»). Реализуем **фазами**, каждая фаза = отдельный
коммит с headless-smoke.

## Согласованный объём

**ОБЯЗАТЕЛЬНО:**
- Ландшафт = **террейн-категория провинции** (поле в `definition.csv`), выпадашка.
  `terrain.bmp` (визуальную палитру) НЕ трогаем.
- Границы **провинций** — пиксельная правка `provinces.bmp` (+ `definition.csv`).
- Границы **штатов** — добавление/удаление провинций в `state.provinces`.
- Сами **штаты** — владелец/контролёр, категория, manpower, VP, корки, ресурсы,
  **постройки (авто-парс из `common/buildings`)**, impassable, local_supplies.

**ВКЛЮЧИТЬ СРАЗУ (экстры):** проливы/каналы (`adjacencies.csv`); зоны снабжения
(`map/supplyareas/` — принадлежность штата).

**НЕ ДЕЛАЕМ (работа Nudge):** `positions.txt` (3D-позиции построек/юнитов/текста/
городов), меши/`show_on_map`-объекты, `ambient_object.txt`, `railways.txt`,
`supply_nodes.txt`, `unitstacks.txt`, `cities.txt`, `weatherpositions.txt`,
strategic regions (можно позже). При правке границ провинций эти файлы могут
устареть — предупредить пользователя, регенерирует Nudge.

## Факты, проверенные на файлах игры (НЕ перепроверять)

### map/
- `default.map` — PDX: `definitions="definition.csv" provinces="provinces.bmp"
  terrain="terrain.bmp" rivers="rivers.bmp" heightmap="heightmap.bmp"
  adjacencies="adjacencies.csv" continent="continent.txt"` и т.д. Диапазонов
  sea/lake НЕТ — тип провинции берётся из `definition.csv`.
- `provinces.bmp` — **5632×2048, 24-bit RGB**, каждая провинция = уникальный цвет.
  Границы там, где соприкасаются разные цвета. numpy 2.4.3 доступен.
- **Цвета провинций** обязаны быть **уникальны** во всей карте; единственное жёсткое
  ограничение движка — уникальность. `(0,0,0)` зарезервирован (провинция 0 /
  «нет провинции») — не выдавать. Пространство 16.7M цветов, свободных всегда с
  запасом (ваниль занимает 13412).
- `definition.csv` — **13412 строк**, разделитель `;`, БЕЗ заголовка:
  `id;r;g;b;type;coastal;terrain;continent`. Пример: `1;230;81;119;lake;false;lakes;7`.
  - `type` ∈ `land|sea|lake`; `coastal` ∈ `true|false`; `terrain` = имя категории;
    `continent` = int (0 для морей). Строка `0;0;0;0;land;false;unknown;0` — служебная
    (провинция 0), сохранять как есть.
- `adjacencies.csv` — заголовок `From;To;Type;Through;start_x;start_y;stop_x;stop_y;
  adjacency_rule_name;Comment`. `Type` ∈ `sea|impassable|...`; `Through` = провинция-
  посредник (для проливов) или `-1`; координаты `-1` если не заданы;
  `adjacency_rule_name` = напр. `PANAMA_CANAL`/`SUEZ_CANAL`/`KIEL_CANAL` (правила в
  `map/adjacency_rules.txt`). Последняя строка обычно `-1;-1;...` терминатор — проверить
  и сохранить.
- `map/supplyareas/<id>-<Name>.txt` — PDX: `supply_area = { id name="SUPPLYAREA_<id>"
  value states={ ids } }`.

### Террейн-категории (`common/terrain/00_terrain.txt`, блок `categories`)
Все: `unknown, ocean, lakes, forest, hills, mountain, plains, urban, jungle, marsh,
desert, water_fjords, water_shallow_sea, water_deep_ocean`.
- Наземные (для выпадашки land-провинции): `plains, forest, hills, mountain, marsh,
  desert, urban, jungle` (+ `unknown`).
- Водные (`is_water = yes`): `ocean, lakes, water_fjords, water_shallow_sea,
  water_deep_ocean`.
- У категории: `color = { r g b }` (для окраски по террейну), `is_water`,
  `movement_cost`, `combat_width`, `buildings_max_level`.

### Штаты (`history/states/<id>-<Name>.txt`)
```
state = {
    id = 1
    name = "STATE_1"                # loc-ключ STATE_<id>
    manpower = 322900
    state_category = town           # common/state_category
    resources = { oil = 5 steel = 10 }   # опционально
    history = {
        owner = FRA
        controller = FRA            # опционально
        add_core_of = COR           # повторяется!
        add_core_of = FRA
        victory_points = { 3838 1 } # пары <province_id> <value>, блок повторяется
        buildings = {
            infrastructure = 2      # ПОСТРОЙКА ШТАТА
            industrial_complex = 1
            3838 = { naval_base = 3 }   # ПОСТРОЙКА ПРОВИНЦИИ (ключ = province_id)
        }
        ... (любые эффекты истории — сохранять нетронутыми)
    }
    provinces = { 3838 9851 11804 }
    local_supplies = 0.0
    impassable = yes                # опционально
    buildings_max_level = {...}     # опционально, редко
}
```

### Постройки (`common/buildings/*.txt`, блок `buildings = { <name> = {...} }`)
- **Дискриминатор scope:** `level_cap = { state_max = N }` → постройка **штата**;
  `level_cap = { province_max = N }` → постройка **провинции**. Также встречается
  `shares_slots = yes` (делит слоты со зданиями штата).
- Прочее полезное: `only_costal = yes` (только приморские провинции — напр.
  `naval_base`), `icon_frame` (кадр в `GFX_buildings`/иконка), `max_level` (реже),
  `infrastructure = yes`. Ванильные: `infrastructure, arms_factory,
  industrial_complex, air_base, supply_node, rail_way, naval_facility, naval_base,
  bunker, coastal_bunker, dockyard, synthetic_refinery, fuel_silo, radar_station,
  rocket_site, nuclear_reactor, anti_air_building, floating_harbor, ...` (+ моды).
  Список НЕ хардкодить — парсить.
- `common/state_category/*.txt` → блок `state_categories = { <name> = { color
  buildings_max_level } }`. Ванильные: `wasteland, enclave, tiny_island,
  small_island, pastoral, rural, town, large_town, city, large_city, metropolis,
  megalopolis, large_island`.
- `common/resources/*.txt` → блок `resources = { <name> = {...} }`:
  `oil, aluminium, rubber, tungsten, steel, chromium, coal` (+ моды).

## Инварианты целостности (проверять в валидации, беречь при правках)
1. Каждый **land**-цвет в `provinces.bmp` обязан иметь строку в `definition.csv`
   (и наоборот — «сирота» с обеих сторон).
2. Каждая **land**-провинция должна принадлежать **ровно одному** штату; провинция
   не в двух штатах.
3. **sea/lake**-провинции НЕ входят в штаты.
4. Цвета провинций уникальны; id уникальны; при создании — брать свободные.
5. Постройка провинции ссылается на провинцию, входящую в штат; уровень ≤ max
   (state_max/province_max); `only_costal`-постройка — только в coastal-провинции.
6. VP-провинция входит в штат.
7. Правка `definition.csv`/штатов — copy-on-write в мод по тому же rel_file
   (override), `ensure_filename_case`, .txt без BOM, .csv как есть (LF/CRLF — как в
   ванили; проверить, обычно CRLF — сохранить стиль).

---

## Фаза 1. Сервисы чтения + read-only карта с режимами и выбором

### `services/map_service.py` — `MapService(context)`
- Чтение `default.map` (пути к файлам, на будущее).
- `provinces.bmp` → numpy `uint8[H,W,3]` (Pillow → `np.asarray`). Кодировать пиксели
  в `uint32` (`r<<16 | g<<8 | b`) для быстрых масок.
- `definition.csv` → список `ProvinceDef(id, r, g, b, type, coastal, terrain,
  continent)` + карты `color_code→id`, `id→ProvinceDef`, `id→bbox` (мин/макс x,y —
  для быстрого зума к провинции; считать один раз numpy-агрегатом).
- `province_at(x, y) -> id` (по цвету пикселя).
- `mask_of(id) -> np.bool[H,W]` (или bbox+локальная маска — для подсветки/заливки).
- Рендер: `render_view(mode, bbox, scale) -> PIL.Image`, режимы:
  - `provinces` — исходные цвета;
  - `terrain` — цвет категории террейна провинции (из TerrainService);
  - `owner` — цвет страны-владельца штата провинции (CountryService.get_color) +
    серый для нейтральных/морей;
  - `state` — детерминированный цвет по id штата (хеш).
  Рендер строит перекрашенную картинку через LUT `id→rgb` по numpy (быстро),
  кропит bbox, ресайзит под scale (NEAREST). Кэшировать полноразмерные LUT-слои,
  инвалидировать при правках.
- Мутации (для фазы 4): `set_pixels(mask/coords, id)`, `create_province(type,
  terrain, continent, coastal) -> id` (свободный id + свободный цвет), `set_def(id,
  **fields)`, `remove_province(id)`.
- Сохранение (фаза 4): `save()` → `provinces.bmp` (Pillow, BMP 24-bit, БЕЗ RLE) и
  `definition.csv` в мод (`ensure_filename_case`). Провести бенчмарк: сборка LUT-
  картинки 5632×2048 должна быть < ~1 c.

### Справочники
- `services/terrain_service.py` — `TerrainService`: `categories() -> {name:
  TerrainCat(name,color,is_water,movement_cost)}`, `land_terrains()`,
  `color_of(name)`.
- `services/building_service.py` — `BuildingService`: парс `common/buildings`,
  `BuildingDef(name, scope: 'state'|'province', max_level, only_costal, icon_frame)`;
  `state_buildings()`, `province_buildings()`.
- `services/state_category_service.py` (или в state_service) — список категорий +
  цвета. `ResourceService` — список ресурсов. (Можно объединить мелкие в один
  модуль `map_refs.py`.)

### Пул свободных цветов провинций (`MapService.free_colors(n)`)
Новым провинциям нужны уникальные, не занятые никем RGB.
- **Заранее сгенерированный список для ванили:** отдельным скриптом
  (`python -m anka.services.mapgen_colors` или util) один раз построить упорядоченный
  список из ~20–30k RGB, которых НЕТ в ванильном `definition.csv`, и положить как
  data-файл (`anka/config/data/free_province_colors.bin` — упакованные uint8×3, или
  .txt). Генератор — детерминированный обход RGB-куба с «разбросом» (напр. шаг по
  золотому сечению в HSV → RGB, либо перемешанный ван-дер-корпут), чтобы цвета были
  визуально различимы и стабильны от запуска к запуску. Исключить `(0,0,0)`.
- **В рантайме:** `free_colors(n)` = взять из пула, исключив цвета, уже занятые
  **модом** (и, на всякий, ванилью) — множество из `definition.csv` строится numpy
  быстро. Если пул исчерпан — доген­ерировать тем же детерминированным генератором,
  пропуская занятые. Возвращать список кортежей. Использует и ручное «создать
  провинцию», и авто-генерация (Фаза 4b).

### `editors/map/` — каркас (заменить заглушку `map` в `_stubs.py`)
- Canvas карты (tk.Canvas + PIL→ImageTk): зум (Ctrl+колесо), пан (drag средней/правой),
  режимы окраски (радиокнопки/комбобокс), асинхронная перерисовка вьюпорта в фоне
  (как в focuses/canvas — не блокировать UI). Клик по провинции → выбор (подсветка
  контуром/оверлеем) → выбрать её штат.
- Правая панель — хост инспекторов (провинция / штат), как в других редакторах.
- Тулбар: режим карты, ☰ панель, 💾, ⚠ проблемы. Индикатор координат/провинции под
  курсором.
- **Smoke:** MapService читает 13412 провинций; `province_at` совпадает с
  `definition.csv` на выборке; render_view всех режимов не падает и укладывается в
  бюджет времени; TerrainService/BuildingService дают ванильные списки.

## Фаза 2. Инспектор штата (закрывает штаты/границы штатов/постройки/корки/владение)

### Block-backed модель штата (расширить `services/state_service.py`)
- Сохранить существующие `StateInfo`/`list_states`/`get`/`set_owner`/`set_cores`
  (их использует редактор стран — НЕ ломать сигнатуры).
- Добавить `StateDocument`/`StateDef(BlockView)` над `Pair("state", Block)`:
  геттеры/сеттеры `id, name_key, manpower, state_category, provinces (list[int]),
  owner, controller, cores (list[str] через add_core_of, повтор!), victory_points
  (list[(prov,val)]), resources (dict), local_supplies, impassable`,
  `state_buildings (dict name→level)`, `province_buildings (dict prov→dict
  name→level)`. Блок `history` и неизвестные эффекты — беречь нетронутыми.
- `load/save/copy_to_mod` по паттерну (mod-first, `ensure_filename_case`, без BOM).
  Штат живёт в одном файле `<id>-<Name>.txt`.
- CRUD провинций в штате: `add_province/remove_province`; при добавлении провинции,
  принадлежащей другому штату — предупреждать (owner_conflict-аналог) и убирать из
  прежнего (опционально).
- Loc имени: `LocCatalog(vanilla_filter="state")` или переиспользовать имена штатов
  (`STATE_<id>` в `*state_names*`); писать в мод-копию/`anka_state_names_l_*.yml`.

### UI инспектора штата (`InspectorBase`)
- Заголовок: id + имя (loc, редактируемое) + файл + 🔒 для ванили + «Копировать в мод».
- Владелец/контролёр (комбобокс стран), категория (комбобокс), manpower, local_supplies.
- Корки: чипы add_core_of (+/−, комбобокс стран).
- VP: список (province, value) с добавить/удалить.
- Ресурсы: строки name→amount (комбобокс ресурсов + число).
- **Постройки штата:** для каждого `state_building` — спинбокс 0..max_level.
- **Постройки провинций:** выбрать провинцию штата → спинбоксы province_buildings
  (только `only_costal` в coastal-провинции — иначе скрыть/задизейблить).
- Список провинций: добавить/удалить (кнопки + **клик по карте** в режиме
  «назначение провинции штату»); удаление — с подтверждением.
- Кнопки: предпросмотр (PdxPreviewDialog), удалить штат (мод-only).
- **Smoke:** round-trip парс всех ванильных штатов (модель→сериализация эквивалентна);
  CRUD provinces/buildings/cores/VP в temp-моде; постройки штата vs провинции
  парсятся корректно (infrastructure=state, naval_base=province).

## Фаза 3. Инспектор провинции (закрывает «ландшафт/террейн»)
- Для выбранной провинции: id (ro), тип `land/sea/lake` (комбо), coastal (чекбокс),
  **террейн-категория (комбо; land-список для land, water-список для sea/lake)**,
  континент (int). Правки идут в `MapService.set_def` → пометка dirty definition.csv.
- Показать площадь (кол-во пикселей), bbox, соседние провинции (по пиксельной
  смежности — вычислять по маске границ; кэш).
- Кнопка «перейти к штату» (для land).
- **Smoke:** правка террейна/типа round-trip через definition.csv; сериализация csv
  байт-стабильна на неизменённых строках.

## Фаза 4. Пиксельное редактирование `provinces.bmp` (границы провинций) — ТЯЖЁЛАЯ
Дизайн под numpy-вьюпорт (никаких попиксельных питон-циклов на 11М пикселей):
- Рабочие данные: `codes = uint32[H,W]` (кодированные цвета). Все операции — на срезах.
- Выбор активной «кисти-провинции» (целевой id/цвет). Инструменты:
  - **Кисть**: диск радиуса R в координатах карты → `codes[disk] = target_code`
    (с учётом зума: экран→карта). Перерисовать только затронутый bbox вьюпорта.
  - **Заливка (flood fill)**: залить связную область одного цвета в target
    (numpy/`scipy`-free: BFS по маске или `PIL.ImageDraw.floodfill` на срезе).
  - **Пипетка**: выбрать провинцию под курсором как активную.
  - **Создать провинцию**: `create_province(...)` → свободный цвет+id+строка
    definition, затем красить.
- Ограничение: красить можно только land-в-land осмысленно; при закраске «в ноль»
  (удалении) — предупреждать (провинция без пикселей → сирота).
- Пересчёт `id→bbox`/масок — инкрементально по dirty-области (не весь кадр).
- Сохранение: `MapService.save()` пишет bmp+csv в мод; напомнить про Nudge
  (positions устареют). Подтверждение при большом дифф.
- Производительность: держать вьюпорт (видимый кроп) как отдельный маленький
  ImageTk; полноразмерный LUT-рендер — только при смене режима/зума-к-целому.
- **Smoke (без GUI-рисования):** программно `set_pixels`/`create_province`/`save`
  на temp-копии карты (можно на уменьшенной синтетической bmp, не на 5632×2048 в
  тесте) → перечитать, проверить цвет↔id, definition-строку, что bmp читается
  Pillow как 24-bit. Плюс бенч LUT-рендера на реальной карте (вне unit-теста).

## Фаза 4b. Авто-генерация N провинций в произвольной области

Базируется на готовой технологии **`F:\Python\TestFilling`** (портировать в
`services/map/region_gen.py`, адаптировав под карту и numpy):
- `cluster.Cluster` — растущий связный кластер пикселей на numpy-массиве: множества
  `_points` (захвачено) и `_border` (фронт кандидатов, поддерживается инкрементально
  в `_claim`). Стратегии роста: `growth()` (захватить всю границу), органический
  `random_growth_in_pixels(count)`, сглаженный `growth_with_weight` /
  `random_growth_with_weight` (вес кандидата = число уже «своих» соседей → ровнее
  границы). `_refresh_border` отбрасывает «протухшие» кандидаты (захвачены соседним
  кластером). `to_mask`/`bounding_box`.
- `main.fill_using_clusters(w,h,N,...)` — сеет N точек, растит все кластеры в цикле
  случайными шагами до отсутствия изменений/`MAX_ITERATIONS`, затем `smooth()`
  (мажоритарный фильтр) чистит зубцы границ. `colors.py` — палитра стартовых цветов
  (у нас заменяется на `free_colors`).

**Функционал в редакторе:** пользователь выбирает **область** (маска: существующая
провинция целиком, или залитая/лассо-выделенная область, или прямоугольник) и число
`K`; редактор разбивает область на `K` новых провинций с естественными границами.

**Адаптация (важно):**
- Расти **строго внутри маски области**: обобщить `blank_color`/`_matches` до
  «пиксель принадлежит области И ещё не захвачен другим кластером этой операции».
  Работать на срезе по bbox области (не по всей карте 5632×2048).
- Сидов `K`: разложить по области с разбросом (рандом внутри маски или
  Poisson-disk/жадный max-min по расстоянию — чтобы провинции были соизмеримы).
- После роста и сглаживания: каждому кластеру дать `free_colors(1)` + новый id +
  строку `definition.csv` (`type/terrain/continent/coastal` **наследуются** от
  исходной провинции/области или задаются пользователем), записать пиксели в `codes`.
  Исходная провинция, если разбивали её, — заменяется (её id либо переиспользуется
  одним из кластеров, либо освобождается — решить: проще «съесть» её одним из K).
- **Производительность:** `smooth()` в прототипе — питон-двойной цикл (медленно на
  масштабе) → **векторизовать** (мажоритарный фильтр на numpy, или ограничить радиус
  и работать по bbox). Рост на Python-множествах приемлем для ограниченной области;
  крупные выделения — в worker-потоке с прогрессом и мягким кэпом по числу пикселей.
  Детерминизм — параметр `seed`.
- **UX:** превью результата (перекрашенная маска) до применения; кнопки
  «сгенерировать/применить/отмена»; выбор стратегии (органический vs ровные границы)
  и `smooth`-силы. После применения — пересчитать `id→bbox`/маски инкрементально по
  bbox области.
- **Smoke:** на маленькой синтетической маске сгенерировать K=5 провинций → проверить,
  что область полностью разбита (нет «дыр»/blank внутри), кластеры связны, цвета из
  `free_colors` уникальны, добавлены K строк `definition.csv`, `province_at` внутри
  области отдаёт новые id.

## Фаза 5. Экстры + валидация + локали + README
- **adjacencies.csv** (`services/map_service.py` или отдельный): таблица связей
  (From/To/Type/Through/rule/comment) — добавить/удалить/править; комбобокс правил из
  `adjacency_rules.txt`; сохранять терминаторную строку. Отдельная вкладка/панель.
- **Зоны снабжения** (`map/supplyareas/`): для штата показать/сменить его supply_area
  (парс `states={}` по всем файлам → карта state→area); добавить штат в зону/убрать;
  создать зону (`anka`-файл). `value` — редактируемо. Loc `SUPPLYAREA_<id>`.
- **Валидация** (панель проблем, паттерн events/decisions): все инварианты выше —
  land без штата, сирота definition↔bmp, sea в штате, дубли VP, постройка > max,
  only_costal не в coastal, провинция в двух штатах, VP/building на чужую провинцию.
  Клик → выбор объекта на карте/в дереве.
- **Локали** ru/en `map.*` + `help.map.*`; **README** раздел «Редактор карты» +
  обновить дерево/список заглушек; убрать `MapEditor` из `_stubs.py`, зарегистрировать
  реальный (order 110), импорт в `editors/__init__.py`.

## Owner-протокол редактора карты (для общих компонентов)
`t, palette`; `value_options("country"/"state")`; для инспекторов — `mark_dirty`,
перерисовка карты/списка; `known_ids` не нужен. Иконок построек: спрайт
`GFX_buildings` (кадр `icon_frame`) — опционально, можно текстом на первом этапе.

## Подводные камни
- **Производительность:** никаких `Image.getpixel`/питон-циклов по всей карте —
  только numpy на срезах. Полный рендер — LUT `id→rgb` + `np.take`.
- **BMP формат:** сохранять provinces.bmp **24-bit без RLE** (Pillow `save('BMP')`
  по умолчанию несжатый — проверить, что читается движком; сравнить байты с ванилью
  на no-op сохранении).
- **CSV:** не переставлять строки без нужды; сохранить перевод строк как в исходнике;
  строку провинции 0 не терять.
- **copy-on-write:** правка ванильной карты/штата → копия в мод по тому же rel_file
  (это override целого файла — для карты это ЗАМЕНА всего provinces.bmp/definition.csv,
  так и надо).
- **StateService двойного назначения:** не сломать `set_owner`/`set_cores`/`StateInfo`,
  используемые редактором стран (как с `IdeaInfo`).
- **Tk:** PhotoImage держать ссылкой; не звать атрибуты виджетов `_name`/`_options`;
  значения читать до `destroy()`; `selection_set`/перерисовка через `root.update()` в
  тестах.
- **Цвета провинций:** только уникальные; `(0,0,0)` не выдавать; при выдаче
  исключать занятые модом (и ванилью). Пул для ванили генерится один раз офлайн и
  лежит data-файлом — не пересчитывать на каждом запуске. Генерация должна быть
  детерминированной (стабильные цвета между запусками + параметр `seed`).
- **Авто-генерация провинций:** `smooth()` из прототипа — питон-циклы, на масштабе
  карты медленно → векторизовать/ограничить bbox; тяжёлые выделения — worker-поток с
  прогрессом; область роста — строго маска выделения, на срезе по bbox.
- **Nudge-граница:** после правки границ — предупреждение про устаревшие positions;
  ничего в `positions.txt`/мешах не трогаем.

## Порядок работы
1. [x] Фаза 1 — MapService (read) + справочники + **пул свободных цветов** + read-only
       карта с режимами и выбором.
2. [x] Фаза 2 — block-backed StateDocument + инспектор штата (постройки авто-парс).
3. [x] Фаза 3 — инспектор провинции (террейн/тип через definition.csv).
4. [ ] Фаза 4 — пиксельное редактирование provinces.bmp + save bmp/csv.
4b.[ ] Фаза 4b — авто-генерация N провинций в области (порт `TestFilling`, numpy).
5. [ ] Фаза 5 — adjacencies + supplyareas + валидация + локали + README + снять заглушку.
6. [ ] Полный регресс (map + все прочие редакторы + запуск приложения).
