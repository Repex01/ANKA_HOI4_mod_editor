# ANKA — Hearts of Iron IV mod editor

ANKA автоматизирует и упрощает работу с модами для Hearts of Iron IV: редактирование
стран, фокусов, событий, нац. духов, личностей, решений, динамических модификаторов и
локализации с автоматической генерацией нужных файлов и конвертацией графики.

## Установка

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
python main.py
```

> `tkinterdnd2` опционален: без него перетаскивание файлов из проводника отключается,
> но загрузка через диалог остаётся.

## Архитектура

Слоистая, SOLID-ориентированная. Зависимости направлены внутрь (UI → services → core/domain).

```
main.py                     точка входа
anka/
├── config/                 константы, settings.json (пути, язык, тема)
├── core/                   инфраструктура, не зависящая от UI
│   ├── pdx/                парсер/сериализатор Paradox-скрипта (lark)
│   ├── localisation/       чтение/запись .yml локализации
│   └── images/             конвертация графики (jpg/png → tga/dds), флаги
├── domain/                 доменные модели (Mod, Country, ...)
├── services/               сценарии: поиск модов, загрузка, репозитории
├── editors/                редакторы-модули (плагины), регистрируются в реестре
│   ├── base.py             EditorModule (ABC) + EditorRegistry
│   ├── countries/          редактор стран (+ флаги)
│   └── ...                 focuses, events, ideas, characters, decisions, ...
└── ui/                     tkinter: окна, темы, i18n, переиспользуемые виджеты
    ├── windows/            main_menu, settings, mod_list, mod_editor
    └── widgets/            ImageDropZone, SearchBar, ...
locales/                    переводы интерфейса (ru, en)
images/                     логотип и стандартные иконки приложения
```

### Принципы расширения
Новый редактор = подкласс `EditorModule`, зарегистрированный в реестре. Он получает
контекст мода (`ModContext`) и сам строит свой UI-таб. Ядро (парсер, конвертер, i18n,
темы) переиспользуется через сервисы — добавление функции не трогает существующий код.
