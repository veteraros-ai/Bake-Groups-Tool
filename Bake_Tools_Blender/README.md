# Bake Tools Blender

Перенос `Bake Groups` из Maya в Blender.

Публичная версия 1.0.0: полный Blender-порт Bake Groups Tool для Windows x64 с автономным PySide6-интерфейсом, C++ ускорителем с Python fallback, проверяемыми обновлениями и opt-in статистикой установки.

Ключевой принцип переноса — сначала воспроизвести исходный интерфейс Bake Group Manager Pro: два сплиттера, левый рабочий столбец, TOC/GT справа, состояния, иконки, тёмную палитру, цвета кнопок, локализацию и сценарии взаимодействия. Пиксельная копия не является целью; важны узнаваемая структура и поведение. Лишь затем переносить операции со сценой и геометрией.

Документы:

- [Основной визуальный мануал PureRef](docs/Manual.pur)
- [Детальный поэтапный план переноса](docs/PORTING_PLAN.md)
- [Аудит оригинала, текущего порта и различий Maya/Blender](docs/MAYA_TO_BLENDER_ANALYSIS.md)
- [Фактическая архитектура Blender-порта](docs/ARCHITECTURE.md)
- [Анализ блокировки и native window integration](docs/WINDOW_INTEGRATION_ANALYSIS.md)

Структура:

```
Bake_Tools_Blender/
├── addon/bake_tools_blender/  # реализация Blender add-on
│   ├── domain/                # immutable view models без Qt
│   ├── analysis_service.py    # pure-data HP matching/grouping
│   ├── analysis_adapter.py    # evaluated snapshot и atomic Blender commit
│   ├── object_repository.py   # roots, HP/LP membership и scene visibility
│   ├── mesh_tools.py          # Combine/Separate, ZBrush set и Mesh Check
│   ├── find_similar.py        # Maya-подобные Find Sim / Find All
│   ├── smooth_preview.py      # временный viewport Subdivision preview
│   ├── export_service.py      # preflight, export plan и FBX transaction
│   ├── progress.py            # единый progress/cancel event channel
│   ├── diagnostics.py         # Debug Log и privacy-safe Support Package
│   ├── about_update.py        # Maya-подобное About/Update Qt-окно
│   ├── update_service.py      # background check и staged rollback
│   ├── native_core.py         # безопасный lazy-loader и Python fallback boundary
│   ├── native/                # Blender cp313/x64 C++ source + bundled .pyd
│   ├── store.py               # Scene PropertyGroup → ManagerView
│   ├── ui.py                  # явный launcher в VIEW_3D Sidebar
│   ├── qt_window.py           # оригинальная двухколоночная PySide6 UI
│   ├── color_preview.py       # обратимая раскраска HP по сабгруппам
│   ├── structure_adapter.py   # Blender hierarchy → Keep HP membership
│   ├── controller.py          # Qt intent → undoable Blender operators
│   └── sync.py                # load/undo/redo и selection context
├── assets/                    # PNG-ресурсы UI
└── docs/                      # аудит и план переноса
```

Оригинальные PNG-иконки входят в пакет и используются интерфейсом Blender-версии.

## Быстрый запуск

1. Установите release ZIP через Preferences → Add-ons либо скопируйте корневую папку `Bake_Tools_Blender` в пользовательскую папку `scripts/addons`.
2. В Preferences → Add-ons включите `Bake Tools Blender`.
3. В 3D View откройте Sidebar (`N`) → вкладка `Bake Tools` → `Open Bake Group Manager`. Менеджер не запускается при открытии `.blend`, не имеет собственной системной шапки и следует за окном Blender при resize/move/смене монитора. Закрытие выполняется там же кнопкой `Close Bake Group Manager`.

Кнопки Pick HP/LP читают последнюю реальную selection Blender: active Object из 3D View/Outliner либо active Collection из Outliner. Create Pair считает только фактически назначенные полигонам LP-материалы. При нескольких материалах появляется исходный выбор Maya: сохранить одну material-aware главу либо сразу создать несколько глав. Затем для обычной главы предлагается выбрать имя из HP/LP base name либо ввести Custom; после создания временные поля HP/LP очищаются. Create Group, TOC, книги, membership, видимость, lock, smooth/cage overrides и Export Settings сохраняются в `Scene.bake_tools_settings`. `Analyze HP` строит HP-membership, а `Assign LP` отдельным проходом распределяет LP meshes по существующим HP-сабгруппам. Find Sim/All, Smooth View, Cage, HP-LP Matcher и FBX Export подключены к сцене Blender.

## HP/LP membership

1. Выберите HP root: Empty/Object в сцене или Collection в Outliner, затем нажмите Pick HP.
2. Аналогично выберите LP root и создайте Pair. Если Blender сохранил старый active Object при выборе коллекции, нажмите ПКМ по Pick и выберите `Pick Active Collection`.
3. Выделите mesh-объекты под HP/LP roots. Можно выделить родительский Empty — его mesh descendants будут развёрнуты автоматически.
4. Create Group создаст сабгруппу и сразу добавит selection. Кнопка `+` переносит новый selection в существующую сабгруппу.

Object-root включает его mesh descendants. Collection-root включает objects из самой коллекции и всех вложенных коллекций. Rename обоих типов сохраняется через Blender pointers. Объект может принадлежать только одной сабгруппе проекта. Выбор вне активных roots пропускается. Double-click по имени сабгруппы выбирает её HP+LP members. Удаление сабгруппы удаляет только metadata и никогда не удаляет meshes. Lock защищает HP-membership от `Analyze HP`; это metadata Bake Tools, а не Blender transform lock.

## Analyze HP

`Analyze HP` собирает immutable snapshot evaluated-мешей на главном потоке, а расчёт выполняется чистым Python-сервисом без `bpy` и Qt. LP meshes используются как пространственные владельцы HP, adjacent-link объединяет детали по sampled vertices, а выключенный `Ignore Floaters` включает отдельное присоединение мелких деталей — та же инверсия, что в Maya. Итоговый commit заменяет только unlocked HP-membership; locked HP и существующие LP members сохраняются.

Это первый перенос алгоритма, а не полная численная копия Maya worker: material-shell LP и нативные point-cloud distance/min-distance функции уже подключены, но пока нет hole/surface proxy, GT hard constraints, фонового worker и revision-token проверки. Кооперативный progress/cancel уже работает между расчётными стадиями на главном Blender thread. Остальные функции C++ API доступны через `native_core.py` для следующих этапов. Эти отличия перечислены в архитектурном документе и не скрываются под статусом feature complete.

## Original UI и Blender integration 1.0.0

Версия 1.0.0 использует bundled PySide6, чтобы сохранить исходные размеры, цвета, сплиттеры, контекстные меню и режимы оригинала. Blender не умеет помещать Qt-виджеты прямо в `bpy.types.Panel`, поэтому менеджер остаётся поддерживаемым Qt top-level window и получает Blender только как native owner. Его левая и правая границы вычисляются из реальной ширины `VIEW_3D/UI`: 8 px слева остаются Blender для штатного resize Sidebar, 28 px справа — для вкладок. При ширине от 470 px действует исходный двухколоночный layout; в более узкой Sidebar появляется переключение `Main` / `Matcher / TOC` фиксированной высотой 24 px. Менеджер читает `active_panel_category` Sidebar и становится прозрачным/click-through вне `Bake Tools`, сохраняя event pump для автоматического возврата. Модальные HWND Blender и menu mode подавляют весь менеджер независимо от обнаружения transient-window. `SetParent/WS_CHILD` запрещён: этот эксперимент внутренней разработки вызывал deadlock Blender.

## Find Sim / Find All, Smooth View и Export

- `Find Sim` читает evaluated mesh после modifier stack, жёстко сравнивает вершины/рёбра/грани, связность и face valence, затем проверяет нормализованные radial/edge/area profiles. Нормализованный radial fingerprint вычисляется перенесённым C++ core с Python fallback. Для нескольких выбранных образцов применяется one-to-one layout с относительным допуском 2%; `Find All` использует тот же shape filter, но намеренно игнорирует layout. Название кнопки и переключение режима по ПКМ сохранены.
- `Smooth View` добавляет только служебные `SUBSURF`-модификаторы с уровнем каждой сабгруппы. Они видимы во viewport, не затрагивают пользовательские модификаторы, исключают запомненные ZBrush meshes и удаляются при выключении/деактивации аддона. При экспорте их render-состояние включается временно и восстанавливается в `finally`.
- Export поддерживает Active Chapter / Active Book / All Books, HP/LP/Cage, Separate и HP+LP one file, LP in one file и Maya-подобный `By material`. Cage всегда пишется отдельным FBX. LP триангулируется временным modifier и возвращается без изменения исходного меша.
- Progress-диалоги принадлежат менеджеру, немодальны и не запускают вложенный Qt event loop. Отмена Find/Analyze/Assign/Smooth и планирования групп не оставляет частичный scene commit; Export отменяется между файлами. Combine/Separate показывают прогресс без кнопки Cancel, потому что Blender Join/разделение нельзя безопасно прервать внутри одной mutation-транзакции.

## Scene tools

- `Combine` использует Blender Join, сохраняет активный меш как основной, переносит Bake Tools membership/scope и переименовывает результат в `_Combined`.
- `Separate` разделяет выделенные meshes по disconnected loose parts, создаёт имена `_PartN` и сохраняет membership/scope.
- `Find ZBrush` проверяет долю треугольных полигонов evaluated HP meshes. ПКМ открывает неблокирующий threshold slider и команды добавления/выбора ZBrush meshes.
- В Blender нет прямого аналога Maya Display Layer. `BakeTools_ZBrush_Layer` — дополнительная Collection: объект остаётся во всех исходных коллекциях, а сохраняемые Object pointers и custom marker переживают rename/save/load.
- `Check Mesh` ищет точные world-space дубликаты отдельно на HP и LP сторонах, незарегистрированные ZBrush-кандидаты и meshes с несколькими значимыми loose parts. Найденные объекты выделяются; полный отчёт показывается без автоматического удаления данных.

## C++ math core

`native/bg_math_core_blender.pyd` собран специально для Blender 5.1 / CPython 3.13 / Windows x64. В C++ передаются только плоские массивы координат из evaluated world-space snapshots — ни `bpy`, ни Blender RNA не пересекают native boundary. `calculate_min_distance` ускоряет vertex-owner prefilter в Analyze HP, а `calculate_avg_distance` заменяет Python KD traversal в Assign LP. Loader проверяет ABI при импорте и сохраняет полностью рабочий Python fallback.

Blender-версия валидирует xyz/triangle buffers и finite coordinates до освобождения GIL. `check_mesh_collision` также исправлен: spatial hash теперь только формирует кандидатов, после чего подтверждается реальная евклидова дистанция. Исходник и воспроизводимый сборщик находятся в `native/bg_math_core_blender.cpp` и `tools/build_native_core.ps1`.

`Color HP` назначает HP-members разных сабгруппам детерминированные цвета исходной Maya-палитры, временно переключает каждый 3D View в Solid/Object Color и восстанавливает как object colors, так и прежний shading mode при выключении. Материалы и LP не изменяются. `Keep HP` читает непосредственные дочерние Empty/Collection-группы HP-root, импортирует их mesh descendants в membership и не переподчиняет Blender Objects.

## Assign LP

Assign LP снимает evaluated world-space snapshots LP root, сравнивает их с HP members каждой сабгруппы и применяет план только после завершения расчёта. Перенесены Maya-подобные bbox prefilter, topology fast-confirm, однонаправленная средняя дистанция LP → HP, precise fallback, threshold `1.5 × LP diagonal` и material-slot repair для групп `M01/M02/...`. Unmatched LP остаются под root без membership; locked LP сохраняются; повторный запуск идемпотентно заменяет только unlocked LP side.

Для передачи другим художникам выполните в PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\tools\build_release.ps1"
```

Сборщик создаёт Windows x64 ZIP на Desktop в папке `Bake_Tools_Blender_Releases`; bundled Qt runtime входит в пакет и не требует установки PySide6 художниками.
Одновременно создаётся отдельный marketplace ZIP с внешним `INSTALLATION.txt`
рядом с папкой аддона. Кнопка `Show manual` открывает `docs/Manual.pur` в
PureRef; если файловая ассоциация недоступна, открывается папка руководства.
