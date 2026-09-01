# Детальный план переноса Bake Groups в Blender

Дата актуализации: 28 августа 2026 года.

Этот план построен по зависимостям оригинального Maya-кода и текущему gap-анализу. В каждом этапе сначала фиксируется контракт, затем реализация, затем проверяемый критерий готовности.

## Публичный статус 1.0.0

- Этап 0: UI-contract реализован для normal, final/export, Matcher, TOC, Cage, log и About; golden captures добавлены.
- Этап 1: `ManagerView` + `BlenderStateStore` + `ManagerController` + undoable operators реализованы для состояния UI, chapter/book/subgroup, visibility/lock и реального HP/LP membership.
- `ObjectRepository`: roots хранятся как Object или Collection pointers, members — как Object pointers; Add Selected эксклюзивно переносит meshes между сабгруппами без Blender reparenting.
- Lifecycle: добавлены load/undo/redo invalidation и headless Blender smoke test. Встроенные Blender popup сканируются через `region_popup` с context override каждого editor; RMB и header-click дополнительно перехватываются безопасным same-thread `WH_GETMESSAGE` guard до отрисовки GHOST popup. Guard выполняет только native HWND alpha/click-through, снимается при Close/Open/Unregister и не входит в Qt или Blender API из callback.
- Qt window manager: `QApplication`, единственный экземпляр основного окна, weak registry диалогов, Blender timer и составные suppression reasons перенесены в отдельный `qt_window_manager.py`; BQt full-wrap/QDockWidget не используются.
- UI lifecycle: вкладка `Bake Tools` содержит нативные Open/Close-команды, поэтому загрузка сцены менеджер не запускает. Frameless PySide6 HWND остаётся безопасным owned Qt popup и строго следует за фактическими границами `VIEW_3D/UI` без `SetParent/WS_CHILD`; нативная граница Sidebar остаётся доступной для изменения ширины, а узкий режим переключает две страницы интерфейса. Close/Open атомарно сбрасывает layered alpha, click-through и popover guard, после чего принудительно обновляет dock rect. Popover guard ограничен реальным `VIEW_3D/HEADER` и не реагирует на Sidebar launcher. Context bridge сохраняет Object/Collection selection между VIEW_3D и Outliner.
- Localization: EN/RU/JA/zh-CN применяются к статическим и динамическим Qt-элементам, контекстным меню, tooltip, placeholder и диалогам без пересоздания менеджера.
- `Color HP` и `Keep HP`: реализованы обратимая HP-only палитра, стабильные цвета строк сабгрупп/пользовательские цвета и импорт существующей Blender hierarchy в membership без reparent.
- Переключение TOC повторяет Maya: новая глава изолируется, повторный клик снимает изоляцию; активный subgroup index не переносится между главами.
- Create Pair: Qt ResolveNameDialog оригинала предлагает HP base, LP base или Custom name.
- Create by Material: для evaluated LP строятся связные material/island proxies; HP проходит Maya-подобные direct ownership, floater attachment, обратный LP audit и `Review_Unmatched`, а главы получают недеструктивный scoped membership.
- `AnalysisService`: evaluated snapshot, LP-guided context, Maya semantic/GT-подобные hard islands, отдельная ZBrush-ветка, robust size/bolt thresholds, C++ narrow phase, adjacent/floater linking и atomic HP-membership commit; locked и LP-side сохраняются.
- `LPMatchingService`: evaluated snapshots, bbox/topology fast pass, KD nearest-surface fallback, material-slot repair и atomic unlocked LP-membership commit; lock/unmatched покрыты тестом.
- Scene tools: Combine/Separate работают через Blender Join/Separate с сохранением membership; Find ZBrush использует только triangle ratio, а ручная ZBrush-метка хранится отдельно; Check Mesh ищет дубликаты, ZBrush-кандидаты, meaningful loose parts и неприменённые/унаследованные трансформы. Freeze применяется только к реальным `MESH`, запекает полную исходную `matrix_world` в Mesh data, очищает обычные и delta-каналы, ставит origin в мировой `(0,0,0)` и не сдвигает мировую геометрию. Object/Collection HP/LP roots сохраняют импортированные axis/unit transforms без изменений.
- Final/Export UI: Smooth +/-/Set действует одной undoable-операцией на все выделенные сабгруппы; новый subgroup получает Maya-дефолт Smooth 1. HP/LP visibility окрашена зелёным/блекло-красным, а существующий Cage заменяет LP-toggle на независимый `Cage Vis/Cage Hid`. Cage копирует raw LP mesh и актуальный `matrix_world`, создаётся с нулевым inflate и не запекает evaluated-модификаторы. Expansion/Normal используют общий world-space BBox уникальных HP+LP мешей главы и кубическую точную шкалу возле нуля.
- Export naming: вход в Export Settings финализирует только распределённые HP/LP по Maya-шаблону `{Chapter}_{Subgroup}_high/low_{NNN}`. Двухфазный rename избегает Blender `.001`, обновляет Object data и Cage source reference; весь export scope перепроверяется непосредственно перед FBX.
- Export directory: основное поле пути редактируемое, а компактный Qt-диалог принимает полный вставленный путь, имеет Paste/Browse и остаётся non-modal относительно Blender.
- Export flags: `LP Triangle` включён по умолчанию и управляет обратимой временной триангуляцией только LP; `Cage` недоступен и не влияет на план, пока в выбранном scope нет Cage.
- Tooltip parity: имя сабгруппы намеренно не имеет tooltip, как в Maya; Eye/Add/Lock/Delete и основные кнопки используют исходный Maya help-каталог и общую локализацию EN/RU/JA/zh-CN.
- Native math: полный pybind11 API `bg_math_core.cpp` пересобран как `bg_math_core_blender.pyd` для Blender CPython 3.13/x64; min-distance подключён к Analyze HP, average-distance — к Assign LP, Python fallback и ABI diagnostics покрыты тестами.
- `HP-LP Matcher`: перенесены loose-shell sampling, Fast/Balanced/Accurate scoring, Find Groups, выбор результатов, Link/Unlink/New, chapter persistence, membership-safe Relocate и hard-island связь с Analyze HP. Обычный синий selection хранится отдельно для каждой главы и восстанавливается после refresh/focus transfer; зелёный цвет обозначает только сохранённый `Link`.
- `Find Sim/Find All`: восстановлен быстрый оригинальный Maya prefilter по raw vertex count/world BBox; только его shortlist проходит evaluated topology/connectivity, нормализованные radial/edge/area profiles и C++ fingerprint с Python fallback. Find Sim выполняет one-to-one layout matching, Find All пропускает только layout, а selection соответствует Maya direct mesh-transform contract.
- Window integration: Blender popup по-прежнему скрывает owned frameless manager до dispatch, но MMB-orbit теперь немедленно выполняет native alpha/style recovery на первом drag-сообщении. Согласование составных причин остаётся в Blender timer; callback не вызывает Qt/RNA/relayout, поэтому исчезла 15–30-секундная задержка без возврата прежних зависаний.
- Следующая точка: эталонное численное сравнение Matcher на production-сценах Maya/Blender и Cage hardening.

## Общие правила проекта

1. Интерфейс переносится первым и остаётся узнаваемым по структуре, расположению, цветовой семантике и взаимодействию.
2. UI не хранит рабочие данные и не изменяет сцену напрямую.
3. `Scene.bake_tools_settings` — единственный источник истины для нативной панели и операторов.
4. Все изменения Blender scene выполняются через `bpy.types.Operator`.
5. Domain/math код не импортирует `bpy` или PySide6.
6. Worker не получает Blender objects и не вызывает Blender API.
7. Любая многошаговая операция либо завершается полностью, либо не изменяет сцену.
8. Каждый этап сопровождается headless-тестом и минимум одним визуальным/интерактивным тестом.

## Определение статусов

- **UI-ready** — интерфейс и interaction contract готовы, но scene behavior может быть заглушкой.
- **Prototype** — работает на простом тесте, но не покрывает edge cases/persistence/undo.
- **Feature complete** — функциональность оригинала воспроизведена в рамках принятой Blender-модели.
- **Release ready** — пройдены regression, reload, undo, save/load и clean-install tests.

## Этап 0. Зафиксировать интерфейсный контракт

Этот этап остаётся первым. Большая часть каркаса уже существует, но перед логикой нужно прекратить расхождение UI и данных.

### 0.1. Окно и layout

- сохранить узкую вертикальную структуру в нативной прокручиваемой панели Blender;
- зафиксировать compact rows и порядок секций для ширины N-panel;
- воспроизвести нормальный режим, Export Settings, Cage Settings, пустую/заполненную сцену;
- сохранить исходные цвета Analyze, Assign, Find Groups, Relocate, Find Sim/All, Export;
- проверить high-DPI 100/125/150/200%;
- проверить long localized labels и узкое окно.

### 0.2. Структура данных в представлении

- одна TOC-глава — одна сущность, без отдельных HP/LP-строк;
- одна сабгруппа — одна строка с общими HP/LP members;
- TOC показывает books → chapters; сабгруппы показываются только в левой рабочей области;
- строка сабгруппы: visibility, name, Add Selected, lock, delete;
- eye/lock states приходят из view model, а не хранятся только на кнопке;
- контекстные меню повторяют доступность действий оригинала.

### 0.3. Interaction contract

Создать перечень UI intents, например:

```text
PickRoot(HP|LP)
CreateChapter
ActivateChapter(id)
CreateSubgroup(name)
AddSelectionToSubgroup(id)
SetSubgroupVisible(id, state)
SetSubgroupLocked(id, state)
AnalyzeHP
AssignLP
ToggleFindMode(SIM|ALL)
EnterExportSettings / LeaveExportSettings
```

Каждая кнопка вызывает Blender Operator. Панель не создаёт chapter/subgroup как локальные UI-данные.

### 0.4. Проверка этапа

- golden screenshots для 4 основных состояний;
- native UI smoke test проверяет регистрацию Panel/UIList, object names и доступность действий;
- повторный render view model не теряет eye/lock и selection;
- сворачивание/раскрытие панели не создаёт вторую модель.

Критерий завершения: UI можно полностью перерисовать из одного immutable `ManagerViewModel`.

## Этап 1. Архитектурный фундамент и единое состояние

### 1.1. Domain model

Ввести dataclasses/обычные классы без `bpy`:

- `ObjectRef(uuid, expected_type, last_name)`;
- `Chapter(id, name, hp_root, lp_root, book_id, settings)`;
- `Subgroup(id, name, hp_members, lp_members, locked, visible)`;
- `Book(id, name, chapter_ids, visible)`;
- `CageSettings` и `ExportSettings`;
- `BakeProject(schema_version, chapters, books, preferences)`.

Инварианты:

- UUID уникальны;
- chapter всегда одна HP/LP-пара;
- subgroup не раздваивается на HP/LP entities;
- один member не находится в двух сабгруппах одной роли без явного решения;
- active IDs могут отсутствовать только при пустом store.

### 1.2. Store и controller

- `ChapterStore` реализует query + commands;
- store публикует `StoreChanged`;
- `WindowController` преобразует store в view model;
- native panel и Qt используют один store;
- лог получает structured events, а не случайные строки из view.

### 1.3. Blender operators

- отдельный operator для каждого mutating intent;
- Qt передаёт только primitive IDs/parameters;
- operator находит controller/store через registry;
- ошибки показываются через `report()` и Log;
- UI-only действия не получают `UNDO`, scene mutations получают.

### 1.4. Удалить/изолировать прототипы

- перестать использовать `QTreeWidget` как database;
- существующие `Scene.bake_tools_settings` либо мигрировать в store, либо оставить только RNA mirror;
- общий `BAKE_TOOLS_OT_action` постепенно заменить типизированными operators;
- нативный старый UI оставить launcher/fallback, а не второй независимый manager.

### 1.5. Проверка этапа

- команда, вызванная из Qt, видна в native UI и наоборот;
- закрытие Qt не уничтожает state;
- undo/redo простого изменения обновляет оба UI;
- unit tests проверяют domain invariants без запуска Blender.

Критерий завершения: в проекте существует ровно один источник состояния.

## Этап 2. Persistence, UUID и lifecycle

### 2.1. Идентификация Blender data-blocks

- генерировать `uuid.uuid4()` и писать `bake_tools_uuid` на objects/collections/materials, которыми управляет аддон;
- resolver ищет по UUID, проверяет тип и возвращает lost/ambiguous result;
- duplicate UUID detection с безопасным repair;
- хранить last known name только для диагностики;
- не менять linked library data без разрешения.

### 2.2. Схема данных

- `schema_version` с первой production schema;
- JSON-compatible serialization;
- migrations `v1 → v2 → ...` без пропуска;
- unknown fields сохранять или явно логировать;
- corrupt data не перезаписывать автоматически.

### 2.3. Хранилище

- основная копия в `.blend` через Scene custom property/PropertyGroup;
- резервный sidecar `<blend>_BakeGroups.json` после `save_post`;
- загрузка из `.blend`, sidecar только как fallback/recovery;
- unsaved `.blend` работает без sidecar;
- manual Save Session вызывает тот же serializer.

### 2.4. Lifecycle

- handlers: `load_post`, `save_post`, `undo_post`, `redo_post`;
- msgbus subscriptions имеют owner и регистрируются заново после load;
- `unregister()` удаляет handlers, timers, msgbus и Qt window;
- hot reload не оставляет старые callbacks/classes.

### 2.5. Проверка этапа

- rename objects/collections не ломает ссылки;
- save/reopen восстанавливает books/chapters/subgroups/cage/export settings;
- удалённый object показывается как Lost;
- duplicate UUID repair детерминирован;
- rollback миграции сохраняет исходный payload.

## Этап 3. BlenderSceneAdapter и базовые примитивы

### 3.1. Scene/context gateway

Один модуль отвечает за:

- active/selected objects;
- mode snapshot/restore;
- object/collection lookup;
- link/unlink collection membership;
- selection changes;
- visibility snapshot/restore;
- transform validation;
- context override для редких `bpy.ops`.

### 3.2. Организация сцены

Принять коллекционную схему:

```text
BakeTools
├── Chapters
│   └── <Chapter UUID/name>
│       ├── HP
│       │   └── <Subgroup collections>
│       └── LP
│           └── <Subgroup collections>
└── Cages
    └── <Chapter UUID/name>
```

Пользовательские source objects могут быть linked в эти collections без обязательного parenting. Нужна политика для objects, уже входящих в другие collections.

Статус 1.0: при входе в Export Settings membership синхронизируется в управляемую иерархию `BakeTools/BakeTools_Chapters/<Chapter>/HP|LP/<Subgroup>`. Это дополнительные links: пользовательские Collections, parenting, rigs и transforms сохраняются.

### 3.3. Трансформации

- различать object transform и baked mesh coordinates;
- frozen-transform preflight адаптировать к Blender: location/rotation/scale, delta transforms, parents;
- не применять transforms автоматически без подтверждения;
- любые временные world-space копии удалять после операции.

### 3.4. Проверка этапа

Fixtures: обычный object, parented object, non-uniform/negative scale, instanced mesh data, linked object, hidden object, Edit Mode.

## Этап 4. Рабочие главы, сабгруппы и TOC

Источник Maya: `SceneInteractionMixin.create_root_pair*`, `GroupManagementMixin`, `TOCMixin`, visibility methods в `BakeManagerUI`.

### 4.1. Pick/Create

- Pick принимает mesh object или collection/root по определённым правилам;
- проверка HP/LP overlap и одинаковых roots;
- resolve names dialog;
- create one chapter / create by materials;
- создать collections/metadata/store record атомарно;
- подготовка meshes без разрушения пользовательской структуры.

### 4.2. Сабгруппы

- create pair of HP/LP subgroup collections;
- Add Selected классифицирует selection по роли;
- для selection вне главы роль берётся из единственной видимой HP/LP-секции, а при обеих видимых секциях запрашивается у художника; первая такая операция фиксирует chapter scope Object pointers без reparent;
- rename синхронизирует metadata/display names;
- delete с confirm и понятной политикой: unlink members или delete data;
- lock блокирует автоматическое перераспределение;
- isolate lock через массовое изменение lock states;
- optimize удаляет только действительно пустые subgroup records/collections.

### 4.3. TOC/books

- activate chapter;
- multi-selection;
- create/rename/delete book;
- group/extract/add to existing book;
- move selected meshes to chapter;
- select chapter contents;
- Find Lost возвращает кандидатов с confidence, не перемещает молча;
- split chapter by materials.

### 4.4. Visibility

- HP, LP, all subgroups, chapter, book;
- subgroup eye и right-click isolate;
- snapshot пользовательской видимости;
- cage visibility отдельно и только в Export Settings.

### 4.5. Проверка этапа

- полный CRUD проходит undo/redo;
- save/reload сохраняет структуру;
- одна глава/сабгруппа никогда не отображается двумя HP/LP-строками;
- Delete не удаляет пользовательский mesh без явного подтверждения.

Критерий завершения: инструмент полезен как ручной manager ещё до Analyze HP.

## Этап 5. MeshSnapshot и математическое ядро

### 5.1. Snapshot schema

`MeshSnapshot` должен содержать:

- object UUID и source revision token;
- original/evaluated mode;
- world-space vertices;
- polygon loops и triangulated faces;
- normals;
- bbox/min/max/center/diagonal;
- connected components/islands;
- material indices/keys;
- UV/color/custom-attribute metadata, необходимую для сохранения операций.

### 5.2. Cache/invalidation

- cache key: UUID + mesh/update revision + matrix + evaluation mode;
- invalidate через depsgraph changes и явные mutation events;
- memory budget/LRU;
- `to_mesh_clear()` гарантируется в `finally`.

### 5.3. NumPy reference math

По `bg_math_core.cpp` реализовать и протестировать в таком порядке:

1. bbox, distance helpers;
2. average/min/bidirectional nearest distance;
3. PCA shape metrics;
4. symmetry;
5. fingerprint;
6. collision grid/BVH overlap;
7. vertex ownership/collision resolution;
8. point-to-triangle surface match.

### 5.4. Native acceleration

- сначала профилировать NumPy/BVH;
- если нужно, pybind11 build для Blender 5.1 Python 3.13;
- API native module повторяет reference implementation;
- всегда иметь NumPy fallback;
- CI/release matrix отдельно для Windows/macOS/Linux.

### 5.5. Проверка этапа

- fixtures из Maya сохраняются как JSON/NPZ без proprietary scene dependencies;
- reference vs Maya с заданными tolerances;
- native vs NumPy results;
- determinism повторных запусков;
- benchmark на маленькой, средней и тяжёлой сцене.

## Этап 6. Preflight и Analyze HP

Источник Maya: `HPAnalysisMixin`, `HPGroupingWorker`.

### 6.1. Preflight

- invalid/lost roots;
- non-mesh inputs;
- unapplied transforms;
- combined mesh shells;
- duplicate meshes;
- empty collections/groups;
- ZBrush candidates;
- multi-material LP context;
- existing locked/manual clusters.

Все исправления должны быть отдельными confirmable operators.

### 6.2. Worker pipeline

1. На main thread собрать snapshots и settings.
2. Проверить memory estimate.
3. Запустить cooperative/pure-data job.
4. Поддержать progress, cancel и debug summary.
5. Получить `HPAnalysisResult` без scene references.
6. Сверить source revision tokens.
7. Одним undoable commit создать/обновить сабгруппы.

### 6.3. Стратегии

- Spatial Volume Match;
- Vertex Proximity;
- Topology Fingerprint;
- Optimization Optimal/Speed;
- collision threshold;
- ignore/detect floaters;
- adjacent vertex link;
- symmetry;
- manual GT links как hard constraints;
- locked subgroups не перераспределять.

### 6.4. Проверка этапа

- одинаковые fixtures дают стабильные группы;
- cancel не оставляет частичных collections;
- изменение сцены во время анализа делает result stale и блокирует commit;
- locked/manual groups сохраняются;
- fallback без native math работает корректно.

Статус 0.9.7: основной snapshot → pure result → atomic commit и кооперативный progress/cancel готовы. Stale-result guard, material/hole/surface контекст, GT constraints и численное сравнение с Maya остаются до feature-complete.

## Этап 7. Assign LP

Источник Maya: `LPMatchingMixin`, `LPMatchingWorker`.

### 7.1. Matching

- построить HP subgroup targets;
- выделить LP objects/shells/material partitions;
- score bbox/center/size/surface;
- назначить best match с conflict resolution;
- material slot repair;
- unmatched LP оставить видимым и перечислить в отчёте.

### 7.2. Commit

- link LP members в соответствующие subgroup collections;
- не разрушать исходный object без необходимости;
- записать confidence/debug mapping;
- поддержать повторный Assign LP идемпотентно.

### 7.3. Проверка этапа

- один LP object, loose shells, multi-material object;
- одинаковые names, different materials;
- no match/ambiguous match;
- locked subgroups;
- undo/save/reload/re-run.

Статус 0.9.7: основной Object-level matching, atomic commit и кооперативный progress/cancel реализованы. Loose-shell partition, confidence UI, фоновый worker и численное сравнение с Maya worker остаются до feature-complete.

## Этап 8. GT Matcher, Find Sim/All и scene tools

### 8.1. GT Matcher

Перенести `GTWidget` как service + view model:

- HP/LP query roots;
- bbox/surface prefilter;
- surface sampling and score;
- tolerance/min HP-LP/modes/strict geometry;
- link/unlink/new;
- custom clusters persisted in chapter;
- relocate HP;
- progress/cancel.

Статус 1.0.3: перечисленный workflow реализован. В Blender `Relocate` намеренно переносит metadata membership в существующую сабгруппу, а не выполняет Maya DAG reparent. Это сохраняет rigs, constraints и пользовательские Collections.

### 8.2. Find Sim/All

- SIM сохраняет strict layout matching;
- ALL игнорирует layout, как в Maya;
- результат меняет Blender selection через adapter;
- right-click toggle остаётся UI interaction, mode хранится в store/preferences.

Статус 0.9.7: SIM/ALL, Blender selection adapter, layout tolerance и ПКМ-переключение реализованы и покрыты headless test.

### 8.3. Combine/Separate

- по возможности BMesh/Data API;
- если используется operator, context override и `poll()`;
- сохранять materials, UV maps, color attributes, custom normals и world transforms;
- separate by loose parts с детерминированными names/UUID.

### 8.4. Find ZBrush

- заменить Maya display layer на служебную Blender collection/tag;
- triangle-ratio heuristic;
- Add Selected to ZBrush set;
- preflight confirmation before Analyze HP.

## Этап 9. Final View и Cage

Источник Maya: `FinalViewMixin`, `CageProcessor`.

### 9.1. Final View

- finalize subgroup naming по правилам Maya;
- HP visible, LP hidden с сохранением предыдущего состояния;
- selectable final rows;
- smooth level per subgroup;
- rubber-band/select all/context menu;
- Back полностью восстанавливает viewport state.

Статус 0.9.7: subgroup smooth level и обратимый viewport-only Smooth View реализованы через служебные SUBSURF modifiers; остальная финализация имён и Cage остаются в работе.

### 9.2. Cage data model

- одна cage mesh на LP source mesh с одинаковой topology;
- chapter cage collection повторяет относительную subgroup structure;
- metadata связывает cage с source LP UUID и topology signature;
- orphan pruning и clear chapter.

### 9.3. Cage algorithm

1. Duplicate original LP topology.
2. Uniform inflate по normal.
3. BVH probes до HP/obstacles.
4. Fit offsets с gap.
5. Resolve penetration islands/overlaps.
6. Upward-only smoothing там, где это сохраняет clearance.
7. Apply offsets и normals.

### 9.4. Cage display/sculpt

- wire/solid material/display adapter;
- subgroup visibility sync;
- per-subgroup overrides;
- sculpt action безопасно активирует cage object/mode;
- выход восстанавливает прежний mode/selection.

### 9.5. Проверка этапа

- topology equality LP/cage;
- no HP penetration в tolerance;
- transformed/scaled LP;
- thin parts, concave HP, multiple obstacles;
- rebuild idempotence;
- save/reload and export.

## Этап 10. Export Settings и FBX

Источник Maya: `ExportMixin`, `FinalExportProcessor`.

### 10.1. Export profile

Зафиксировать:

- target axes;
- unit scale;
- apply transform policy;
- mesh modifiers;
- smoothing representation;
- triangulation policy;
- materials/UV/custom normals;
- object naming;
- selection scope;
- HP/LP/cage suffixes и directory layout.

### 10.2. Preflight

- lost/empty roots;
- undistributed LP;
- duplicate final names;
- invalid materials;
- invalid cage topology;
- unwritable path;
- unsupported linked/non-local data;
- missing exporter/extension.

### 10.3. Export operations

- active chapter HP+LP;
- HP only / LP only;
- cage optional;
- batch book;
- batch all LP;
- export by material;
- merged cage per book;
- multi-material split preserving UV/material assignments.

### 10.4. Non-destructive transaction

- export copies в temporary collection;
- temporary triangulate/modifiers только на copies;
- zero-transform copies where required;
- export selection/context isolated;
- cleanup в `finally` даже после exception/cancel;
- рабочая сцена до/после сравнивается snapshot-тестом.

### 10.5. Проверка этапа

- повторный импорт FBX в чистую Blender scene;
- сравнение names, object count, bbox, triangles, materials и UV;
- проверка в целевом engine/DCC;
- failure injection на каждом шаге cleanup.

Статус 0.9.7: FBX-планирование и запись для chapter/book/all, HP/LP/Cage, separate/one, LP-one-file и Maya-подобного By Material реализованы. LP triangulate и Smooth render state временные и очищаются в `finally`; готовы smoke tests реальной записи FBX и всех вариантов export plan. Round-trip в целевом engine и export-copy collection остаются до production gate.

## Этап 11. Локализация, Log, About и support

### 11.1. Localization

- перенести ключи и EN/RU/JA/ZH-CN тексты;
- отделить labels/tooltips/status messages;
- live refresh всего открытого UI;
- language в preferences/store, не только в кнопке.

### 11.2. Structured log

- timestamp, severity, action id, chapter/subgroup IDs;
- bounded in-memory log;
- Save Debug Log;
- support package: versions, addon manifest, settings, scene summary, lost refs, recent actions;
- не включать геометрию/личные пути без явного согласия.

### 11.3. About/manual/update

- About показывает addon/Blender/Python/PySide/native core versions;
- offline manual входит в package;
- updater/rollback — отдельный опциональный этап после решения о канале поставки;
- telemetry — только opt-in и отдельное privacy решение; не блокирует core release.

## Этап 12. QA и релиз

### 12.1. Автоматические тесты

- pure domain/math unit tests системным Python;
- Blender background integration tests;
- Qt constructor/view model tests;
- save/load/migration fixtures;
- undo/redo tests;
- export round-trip tests;
- registration/unregistration/hot reload tests.

### 12.2. Test scene matrix

Минимальный набор `.blend` fixtures:

1. Simple bolt HP/LP.
2. Несколько повторяющихся деталей для SIM/ALL.
3. Multi-material LP.
4. Loose shells/combined meshes.
5. Duplicates/floaters/ZBrush-like dense meshes.
6. Parent/non-uniform/negative transforms.
7. Modifier/Geometry Nodes evaluated geometry.
8. Shared mesh data/instances.
9. Cage penetration stress scene.
10. Lost/deleted/renamed data recovery.

### 12.3. Поддерживаемая матрица

На текущем этапе release target:

- Blender 5.1;
- Windows x64;
- Python 3.13;
- native Blender UI без внешнего Qt runtime.

Другие Blender minor versions проверяются отдельно. macOS/Linux требуют отдельной regression-проверки Blender API и экспорта.

### 12.4. Release gates

- clean install из ZIP;
- нет ручного `pip` у художника;
- включение/выключение аддона без ошибок;
- reload `.blend` без stale UI;
- no orphan handlers/timers/windows;
- core workflow Create → Analyze → Assign → Cage → Export проходит на fixtures;
- рабочая сцена не меняется после failed export;
- документация и version manifest совпадают с package.

## Критический путь

```text
UI contract
  → единый Store/Controller/Operators
  → persistence + UUID
  → SceneAdapter + chapter/subgroup CRUD
  → MeshSnapshot + NumPy math
  → Analyze HP
  → Assign LP / GT Matcher
  → Final View + Cage
  → FBX Export
  → services + release hardening
```

Нельзя начинать полноценный HP worker до `MeshSnapshotBuilder`, а Cage — до устойчивой original/evaluated geometry policy. Экспорт нельзя считать готовым до non-destructive transaction и round-trip tests.

## Ближайший рабочий спринт

1. Преобразовать LP-owner hints из `AnalysisResult` в автоматический `Assign LP` plan.
2. Добавить conflict resolution, unmatched report и сохранение locked LP-membership.
3. Подключить GT/manual links как hard constraints для Analyze/Assign.
4. Добавить revision token между snapshot и commit.
5. Перевести pure calculation в cooperative background job с progress/cancel; `bpy` оставить только в capture/commit.
6. Сохранить Maya fixtures и сравнивать ownership/grouping с допусками.

Результат спринта: рабочая двухступенчатая связка Analyze HP → Assign LP без частичных изменений сцены.
