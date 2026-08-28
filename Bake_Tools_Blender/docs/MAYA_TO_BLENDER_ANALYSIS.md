# Аудит переноса Bake Groups: Maya → Blender

Дата аудита: 19 августа 2026 года.

> Обновление 0.8.3 от 20 августа 2026: отдельный PySide6 renderer удалён из рабочего пути. Целевой интерфейс теперь полностью нативный `VIEW_3D → UI → Bake Tools`; исторические упоминания Qt ниже описывают состояние порта на дату исходного аудита.

Целевая среда текущей сборки: Blender 5.1, Windows x64, Python 3.13, PySide6 6.11.1.

## 1. Что именно сравнивалось

Оригинал Maya:

- `bg_main_window.py`, `bg_ui_widgets.py`, `bg_gt_matcher.py` — интерфейс и GT Matcher;
- `bg_core.py`, `bg_mixins.py` — состояние, управление сценой и основные сценарии;
- `bg_worker_hp.py`, `bg_worker_lp.py` — анализ HP и распределение LP;
- `bg_cage.py` — построение и коррекция cage;
- `bg_final_export.py` — подготовка и FBX-экспорт;
- `bg_math_core.cpp` — ускоренная математика;
- `bg_localization.py`, `bg_update.py`, `bg_telemetry.py` — сервисные функции;
- `launcher.py`, `install_shelf.py` — запуск, shelf и dock/workspace control.

Текущий Blender-порт:

- `qt_window.py` — отдельное PySide6-окно;
- `ui.py` — нативная Blender-панель запуска и ранний UI-прототип;
- `operators.py` — несколько Blender-операторов и общий оператор-заглушка;
- `properties.py` — прототип `PropertyGroup`;
- `dependencies.py`, `icons.py`, `__init__.py` — runtime, ресурсы и регистрация аддона.

Методика: граф исходного кода использовался для навигации между подсистемами, а выводы проверялись непосредственно по исходным файлам. Граф содержит 773 сущности и 1 819 связей. В оригинале около 22 173 строк Python/C++; в Blender-порте около 1 369 строк собственного Python без встроенного PySide6.

## 2. Итог аудита

Текущая версия Blender — это перенос интерфейсного каркаса и упаковки, но не перенос производственной логики Bake Groups.

Уже существуют:

- устанавливаемый Blender-аддон;
- отдельное PySide6-окно с основными зонами Maya-интерфейса;
- оригинальные PNG-ресурсы и цветовая семантика кнопок;
- черновые представления глав, сабгрупп, TOC, Matcher, Export Settings, Log и About;
- прототип Blender `PropertyGroup`;
- встроенный PySide6 runtime для Windows x64;
- несколько минимальных Blender-операторов.

Пока отсутствуют:

- единая модель состояния между Qt, нативной Blender-панелью и сценой;
- создание реальной структуры HP/LP в сцене;
- почти все операции с мешами;
- HP clustering, LP matching и GT matching;
- cage и финальный preview;
- реальный FBX-экспорт;
- сохранение/восстановление сессии и миграции схемы;
- рабочие undo, события сцены, локализация, update и support package.

Главный архитектурный разрыв: `qt_window.py` хранит главы и сабгруппы непосредственно в `QTreeWidget`, тогда как `properties.py` хранит отдельное состояние в `Scene.bake_tools_settings`. Эти два состояния сейчас не синхронизированы. Кнопки Qt также почти не вызывают Blender-операторы: большинство обработчиков только меняет виджет или добавляет строку в Log.

До переноса алгоритмов нужно устранить именно этот разрыв, иначе каждая функция будет реализована дважды и данные будут расходиться.

## 3. Карта архитектуры оригинала

| Подсистема | Главные исходники | Назначение | Состояние в Blender |
|---|---|---|---|
| Запуск и dock | `launcher.py`, `install_shelf.py`, `BakeManagerUI` | shelf, `workspaceControl`, восстановление окна | Отдельное окно запускается; dock отсутствует по выбранному дизайну |
| UI и взаимодействие | `bg_main_window.py`, `bg_ui_widgets.py` | splitter, сабгруппы, меню, лог, undo UI, locale | Внешний вид перенесён частично; большая часть действий — заглушки |
| Сессия | `BakeSessionModel`, `MayaCore` | JSON, `fileInfo`, UUID, восстановление ссылок | Есть только несвязанный прототип `PropertyGroup` |
| HP preflight | `HPAnalysisMixin` | frozen transforms, ZBrush, combined meshes, duplicates, empty groups | Частично: отдельный Check Mesh ищет world-space duplicates, ZBrush candidates и meaningful loose parts; destructive cleanup и Analyze gate не перенесены |
| HP analysis | `HPAnalysisMixin`, `HPGroupingWorker` | 3 стратегии, collision, floaters, symmetry, manual links | Отсутствует |
| LP matching | `LPMatchingMixin`, `LPMatchingWorker` | подбор LP, material repair, распределение по сабгруппам | Отсутствует |
| GT Matcher | `GTWidget` | surface matching, link/unlink/new, relocate | Есть статический UI; вычисления отсутствуют |
| Управление группами | `GroupManagementMixin` | create/add/rename/delete/lock/isolate | Есть визуальные строки; реальная сцена не изменяется |
| TOC/books | `TOCMixin` | главы, books, move, find lost, split materials | Дерево и меню частично; модель и действия отсутствуют |
| Scene tools | `SceneInteractionMixin` | pick, combine, separate, ZBrush, Find Sim/All, Create by Mat | Pick Object/Collection, Combine, Separate, Find ZBrush и Create by Material работают; Find Sim/All остаётся UI-contract |
| Final View | `FinalViewMixin` | smooth preview, final names, selection, cage config | Есть отдельная UI-страница; сцена не меняется |
| Cage | `CageProcessor`, часть `FinalViewMixin` | inflate, fit, gap, overlap resolve, display, export | Только свойства/кнопки |
| Export | `ExportMixin`, `FinalExportProcessor` | preflight, LP/HP/cage, books, materials, rollback | Только UI и сообщения Log |
| Высокопроизводительная математика | `bg_math_core.cpp` | nearest distance, collision, symmetry, PCA, fingerprint | Перенесён полный API как Blender cp313/x64 `bg_math_core_blender.pyd`; distance/min-distance подключены к сервисам, есть Python fallback |
| Workers | `HPGroupingWorker`, `LPMatchingWorker` | длительные вычисления и progress/cancel | Не перенесены |
| Сервисы | localization/update/telemetry/manual | языки, обновления, support, manual | About-заглушка и пункт Language без перевода |

## 4. Матрица функционального соответствия

Статусы:

- **Готово** — функция выполняет полезное действие в Blender и сохраняет результат;
- **Частично** — часть сценария работает, но нет полного поведения оригинала;
- **Только UI** — элемент отображается, но не выполняет производственную операцию;
- **Нет** — подсистема отсутствует.

| Возможность оригинала | Maya-реализация | Blender-реализация | Статус | Что не хватает |
|---|---|---|---|---|
| Установка аддона | shelf/runtime launcher | корневой `__init__.py`, release ZIP | Готово для Windows x64 | Проверка других ОС и clean-install тест |
| Отдельное окно | `BakeManagerUI` | `BakeToolsWindow` | Частично | lifecycle при reload/unregister, DPI, несколько Blender windows |
| Визуальная структура | `init_ui`, custom widgets | `_build_ui`, QSS, assets | Частично | несколько точных состояний, disabled/tooltips, локализация |
| Pick HP/LP | `pick_node` | `_pick`, `BAKE_TOOLS_OT_pick_object` | Частично | валидация mesh/root, стабильная ссылка, синхронизация Qt/store |
| Create Pair | `create_pair_smart`, `create_root_pair_from_picked` | `_create_pair`, `BAKE_TOOLS_OT_create_pair` | Только UI | collections/objects, UUID, naming conflict, materials, undo |
| Create by materials | `create_root_pairs_by_material_from_picked` | нет | Нет | вся логика ownership/audit/books |
| Сабгруппы | `GroupManagementMixin`, `refresh_left_panel` | строки Qt, прототип `BakeToolsSubgroup` | Только UI | общая HP/LP-пара, membership, add/delete/lock/rename в сцене |
| TOC и books | `TOCMixin` | `QTreeWidget` и меню | Только UI | единая модель, books, active chapter, move, find lost, split |
| Visibility HP/LP/groups | `toggle_root_vis`, `set_all_subgroups_vis` | bool/log/иконки | Только UI | реальная видимость объектов/коллекций и восстановление |
| Isolate | `isolateSelect`, subgroup isolate | меню-заглушки | Нет | политика local view/view layer и точное восстановление |
| Color HP | override/color sets | выбор цвета строки Qt | Только UI | viewport representation и reversible scene state |
| Keep HP | анализ с сохранением структуры | checkbox | Только UI | алгоритмическая ветка и структура коллекций |
| Combine | `tool_combine`, `MayaCore.combine_subgroups` | Blender Join + membership/scope transfer | Да | дополнительные fixtures сложных modifiers/shape keys |
| Separate | `tool_separate`, `_separate_mesh_transforms` | split by loose parts + membership/scope transfer | Да | дополнительные fixtures custom attributes |
| Find ZBrush | display layers + triangle heuristic | triangle ratio + `BakeTools_ZBrush_Layer` + Object pointers | Да | ZBrush-specific Analyze grouping semantics |
| Pre-analysis checks | `run_pre_analysis_checks` | action queued | Нет | frozen transforms, combined shells, duplicates, confirmations |
| Analyze HP | `run_hp_analysis`, `HPGroupingWorker` | evaluated snapshot → C++/Python math boundary → pure service → atomic HP membership | Prototype | material shells, hole/surface proxy, GT constraints, progress/cancel, Maya fixtures |
| Assign LP | `run_lp_matching`, `LPMatchingWorker` | evaluated snapshots → pure matching service → atomic LP membership | Prototype 0.8.5 | loose shells, confidence UI, progress/cancel, Maya numeric fixtures |
| GT Matcher | `GTWidget` | поля/list/buttons | Только UI | sampling, scoring, session links, relocate |
| Find Sim / Find All | `find_similar_meshes_ui`, mode toggle | переключение подписи | Только UI | topology/layout algorithms и Blender selection |
| Final/Export Settings | `toggle_final_view`, `render_final_view` | stacked page | Только UI | final naming, scene view, selection, smooth states |
| Smooth View | preview methods | текст/ячейка | Только UI | temporary Subdivision state и rollback |
| Cage | `CageProcessor` | свойства и кнопки | Только UI | duplicate topology, BVH fit, display, overrides, cleanup |
| Export preflight | `_export_preflight` | нет | Нет | ошибки/предупреждения, naming/material/LP distribution checks |
| FBX export | `ExportMixin`, `FinalExportProcessor` | action queued | Нет | Blender FBX adapter, axes/units, triangulation rollback |
| Session save/load | `BakeSessionModel` | Scene properties не подключены к Qt | Нет | schema, UUID, migrations, handlers, JSON sidecar |
| Undo | Maya chunks + custom snapshots | некоторые `bl_options={'UNDO'}` | Нет для Qt | все Qt mutations должны идти через Blender operators |
| Scene events | `scriptJob` | только Qt event timer | Нет | load/save/undo/depsgraph handlers и msgbus cleanup |
| Progress/cancel | QThread signals/dialogs | нет | Нет | modal/cooperative job system и cancel-safe commits |
| Debug/support package | log menu + snapshots | сохранение видимого текста | Частично | scene/environment snapshots, user actions, support ZIP |
| Localization | `bg_localization.py` | Language пишет строку в Log | Нет | translation catalog, live refresh, persistent preference |
| About/update/manual | `bg_update.py` | простой About | Только UI | version info, manual, optional updater/rollback |
| Telemetry | `bg_telemetry.py` | нет | Нет | переносить только после отдельного решения о privacy/opt-in |

## 5. Что можно переносить почти напрямую

Следующие части не зависят от Maya, если на вход подать обычные Python/NumPy-данные:

- статистику, scoring и пороги;
- часть bbox/point-cloud математики;
- алгоритм кластеризации `HPGroupingWorker` после удаления Qt/Maya-зависимостей;
- логику `LPMatchingWorker` после замены имен узлов на стабильные `ObjectRef`;
- схему chapter/book/subgroup/cage settings;
- naming rules и большую часть export preflight;
- локализационные ключи и тексты;
- логику GT matching после создания Blender mesh snapshot/intersector.

Нельзя копировать напрямую:

- любой код `maya.cmds`, `maya.api.OpenMaya`, MEL/FBX plugin;
- `MayaQWidgetDockableMixin`, `workspaceControl`, `scriptJob`;
- Maya DAG-path и transform/shape assumptions;
- display layers, shadingEngine membership и `isolateSelect`;
- QThread, если worker продолжает обращаться к Blender API;
- скомпилированный Maya `.pyd`.

## 6. Различия Maya и Blender, влияющие на проект

### 6.1. Узлы, иерархия и идентичность

Maya разделяет transform и mesh shape и адресует DAG-узел полным путём. Blender разделяет `Object` и `Mesh` data-block; один mesh может использоваться несколькими objects, а object может входить сразу в несколько collections.

Решение:

- chapter, HP root, LP root, subgroup и cage представить collections;
- реальные меши оставить objects, не использовать пустые objects только для имитации каждого Maya transform;
- каждому управляемому object/collection присваивать собственный `bake_tools_uuid` через ID custom property;
- хранить `PointerProperty` как быстрый локальный доступ, но UUID считать источником восстановления;
- явно определить поведение linked/library objects, instances и shared mesh data.

Не использовать имя или `ID.session_uid` как долговременный внешний ключ: имя меняется, а проекту нужен собственный schema-controlled UUID.

### 6.2. Parenting и collections

В Maya `parent()` одновременно задаёт DAG-иерархию. В Blender inclusion в collection не меняет transform и не является parenting.

Решение: членство в chapter/subgroup хранить через collections и metadata. Object parenting применять только там, где оно действительно нужно для transform hierarchy. Перемещение между группами должно link/unlink collections, а не безусловно переподчинять object.

### 6.3. Исходная и вычисленная геометрия

Maya-код часто читает `MFnMesh` в world space. В Blender `Object.data` не включает modifiers, constraints и evaluated Geometry Nodes. Для анализа видимой геометрии нужен evaluated depsgraph; временный mesh после `to_mesh()` необходимо освобождать.

Решение: единый `MeshSnapshotBuilder` с явным флагом `evaluation_mode`:

- `ORIGINAL` — топология исходного mesh для операций, где индексы должны сохраняться;
- `EVALUATED` — фактическая геометрия viewport для bbox/matching/export preflight;
- snapshot всегда содержит world-space vertices, triangles, normals, bbox, material indices и обратную ссылку на object UUID;
- все преобразования координат выполнять явно через `matrix_world`.

Обязательные тесты: unapplied transforms, negative/non-uniform scale, modifiers, Geometry Nodes, linked duplicate mesh data.

### 6.4. MFnMesh/intersectors против BVH/BMesh

`MFnMesh.getClosestPoint`, Maya intersectors и mesh iterators заменяются на:

- `mathutils.bvhtree.BVHTree` для nearest/ray/overlap;
- `bmesh` для connectivity, islands, split/dissolve и временных изменений;
- NumPy для point-cloud/PCA/fingerprint и пакетных расстояний.

Результаты не будут бит-в-бит совпадать из-за triangulation, normals и epsilon. Нужны допустимые численные отклонения, а не сравнение точных координат.

### 6.5. Компоненты и режимы редактирования

Maya возвращает object и component strings из одного API. Blender разделяет Object/Edit Mode и хранит component selection в BMesh.

Решение: SceneAdapter должен нормализовать текущую выборку в `SelectionSnapshot`, а функции не должны сами переключать mode. Если переключение неизбежно, исходный mode, active object и selection восстанавливаются в `finally`.

### 6.6. Материалы

Maya использует shadingEngine membership и face components. Blender использует material slots и `polygon.material_index`.

Решение:

- material key строить из устойчивого UUID material + slot index;
- multi-material split выполнять по polygon indices через BMesh/mesh copy;
- сохранять UV layers, color attributes, custom normals и material order;
- не считать одинаковые имена материалов одинаковыми data-blocks.

### 6.7. История, modifiers и smooth preview

Maya construction history не соответствует Blender modifier stack. Preview smoothing в Blender должен быть временным `Subdivision Surface` modifier либо отдельной evaluated-копией.

Решение: не применять modifier к рабочему mesh. Помечать preview modifier собственным UUID/name, хранить предыдущее состояние и гарантированно удалять/восстанавливать его при Back, export, undo и закрытии окна.

### 6.8. Visibility и isolate

Maya visibility/display layers/isolateSelect не имеют одного прямого аналога. В Blender есть `hide_set`, `hide_viewport`, collection/view-layer exclusion и Local View; они имеют разный scope.

Решение:

- обычная видимость chapter/subgroup — `Object.hide_set()` с сохранённым состоянием;
- долгосрочное скрытие служебных collections — `hide_viewport`/view-layer collection;
- isolate реализовать собственным snapshot/restore, не полагаться на Local View из отдельного Qt-окна;
- никогда не затирать пользовательское скрытие без сохранения предыдущего состояния.

### 6.9. Viewport color preview

Maya override colors/color sets нельзя механически перенести. В Blender цвет зависит от shading mode.

Решение: отдельный preview adapter. Предпочтительно временные материалы или object color вместе с контролируемым viewport shading. Все изменения должны иметь reversible snapshot и не попадать в экспорт.

### 6.10. Undo

Maya объединяет команды через `undoInfo` и дополнительно хранит custom snapshots. Blender формирует undo steps через operators. Прямые изменения сцены из Qt callback могут оказаться вне корректной undo-транзакции.

Решение: Qt вызывает только зарегистрированные `bpy.types.Operator`; operator вызывает controller/service. Все mutating operators получают `REGISTER`/`UNDO`, а многошаговые операции применяют результат атомарно после успешного расчёта. Временные export-копии удаляются в `finally`, а не через пользовательский Undo.

### 6.11. Context и `bpy.ops`

Blender operators зависят от активного window/area/mode и могут не пройти `poll()`, особенно когда команда запущена из отдельного Qt-окна.

Решение: SceneAdapter использует Data API/BMesh везде, где возможно. `bpy.ops` оставляется для ограниченного числа задач — экспорт, возможно join/separate — и вызывается через сохранённый Blender window/context override с предварительным `poll()`.

### 6.12. Потоки и длительные вычисления

Maya-версия использует `QThread`. Blender Python API не thread-safe; worker не должен читать или изменять `bpy` data.

Решение:

1. Главный поток создаёт immutable `MeshSnapshot`.
2. Worker получает только Python/NumPy arrays и UUID.
3. Worker возвращает `AnalysisResult`, не Blender objects.
4. Главный поток проверяет, что входные objects не изменились, и применяет результат одним operator commit.

Первый безопасный вариант — cooperative modal/timer job. Позже допустим отдельный process для тяжёлой чистой математики; Windows spawn, cancel и упаковка тестируются отдельно.

### 6.13. События сцены

Maya `scriptJob` заменяется комбинацией:

- `bpy.app.handlers.load_post`, `save_post`, `undo_post`, `redo_post`, `depsgraph_update_post`;
- `bpy.msgbus` для RNA property notifications;
- `bpy.app.timers` только для коротких UI/cooperative callbacks.

Каждый handler, subscriber и timer обязан иметь явный owner и удаляться в `unregister()`/window shutdown. Message bus заново регистрируется после загрузки файла.

### 6.14. Сохранение данных

Maya хранит JSON и дублирует данные через `fileInfo`. Blender custom properties и `PropertyGroup` сохраняются в `.blend`, но sidecar нужен для диагностики и восстановления.

Решение:

- основной store — versioned JSON-compatible schema в Scene custom property или PropertyGroup;
- объектные ссылки — собственные UUID;
- sidecar `<blend>_BakeGroups.json` — резервная копия после сохранения `.blend`;
- schema version и последовательные migrations обязательны с первой рабочей версии;
- загрузка должна уметь показать lost references, а не молча удалить их.

### 6.15. FBX, оси и единицы

Maya экспортирует через `fbxmaya`; Blender использует свой FBX exporter и другую систему параметров. Нужно отдельно определить forward/up axes, unit scaling, apply transforms, triangulation, smoothing, materials и selection scope.

Решение: `BlenderFbxExporter` с фиксированным profile, сохранением пользовательского состояния и regression fixtures. Экспорт проверяется повторным импортом в Blender и, по возможности, в целевом движке/DCC.

### 6.16. Qt runtime и окно

Отдельное PySide6-окно соответствует выбранному UX, но это не нативный Blender UI. Сейчас Qt events прокачиваются timer с интервалом 0,02 секунды.

Риски:

- лишняя нагрузка 50 callbacks/sec;
- stale QWidget после reload аддона;
- несколько Blender windows/context;
- конфликт Qt DLL/платформ на macOS/Linux;
- callbacks, которые обходят Blender operator/undo model.

Решение: один `WindowController`, явный shutdown, более спокойный/адаптивный event pump, никакого прямого изменения сцены из view-класса.

### 6.17. Бинарная математика

`bg_math_core.cpp` экспортирует:

- bidirectional/surface/average/min distance;
- collision detection;
- symmetry;
- collision ownership;
- vertex owner scores;
- PCA shape metrics;
- geometry fingerprint.

Blender 5.1 использует Python 3.13. В 0.9.5 создан отдельный pybind11-модуль `bg_math_core_blender.pyd`, собранный под cp313/Windows x64 и проверяемый численно против Python brute-force. Maya `.pyd` не используется: имя модуля и ABI отделены, loader безопасно откатывается к Python. Для другой пары OS/Python/Blender требуется отдельная сборка.

## 7. Целевая архитектура Blender-порта

```text
Qt View / Native launcher
        │ UI intents + immutable view model
        ▼
WindowController / Command Router
        │ invokes
        ▼
Blender Operators ─────────────── Undo / progress / reports
        │
        ▼
Domain Services                 (без Qt и без bpy)
ChapterStore  Analysis  Matching  Cage  Export planning
        │                         ▲
        ▼                         │ snapshots/results
BlenderSceneAdapter ───────── MeshSnapshotBuilder / BVH / BMesh
        │
        ▼
bpy.data / Scene / Collections / Objects
```

Рекомендуемая структура пакета:

```text
bake_tools_blender/
├── ui_qt/                  # только widgets, menus, rendering view model
├── ui_native/              # launcher и аварийный fallback
├── controllers/            # связывает UI, operators и store
├── domain/
│   ├── models.py           # Chapter, Subgroup, ObjectRef, CageSettings
│   ├── commands.py
│   └── results.py
├── persistence/
│   ├── store.py
│   ├── schema.py
│   └── migrations.py
├── adapters/
│   ├── scene.py
│   ├── mesh_snapshot.py
│   ├── viewport.py
│   └── fbx.py
├── services/
│   ├── preflight.py
│   ├── hp_analysis.py
│   ├── lp_matching.py
│   ├── gt_matcher.py
│   ├── cage.py
│   └── export.py
├── jobs/                   # cooperative jobs / pure-data workers
├── math_core/              # NumPy reference + optional native extension
└── tests/
```

## 8. Технические решения, которые нужно зафиксировать до алгоритмов

1. Одна глава содержит общие HP/LP-ссылки; сабгруппа также является одной сущностью с двумя наборами members, а не двумя строками HP и LP.
2. Collection membership — организационная модель; object parenting не используется как универсальная замена Maya DAG group.
3. UI не является источником данных. Источник истины — `ChapterStore`.
4. Все изменения сцены идут через Blender operators.
5. Анализ работает по immutable snapshot и не обращается к `bpy` из worker.
6. Собственный UUID хранится на управляемых ID data-blocks.
7. Любое временное viewport/export состояние имеет snapshot/restore и `finally` cleanup.
8. Telemetry и self-update не входят в обязательное ядро и требуют отдельного решения.

## 9. Официальные справочные материалы

- Blender Python threading warning: https://docs.blender.org/api/main/info_gotchas_threading.html
- Blender evaluated depsgraph и temporary meshes: https://docs.blender.org/api/5.0/bpy.types.Depsgraph.html
- Blender BVHTree: https://docs.blender.org/api/main/mathutils.bvhtree.html
- Blender BMesh: https://docs.blender.org/api/main/bmesh.html
- Blender context/custom properties/operator poll: https://docs.blender.org/api/main/info_quickstart.html
- Blender application handlers: https://docs.blender.org/api/5.2/bpy.app.handlers.html
- Blender message bus lifecycle: https://docs.blender.org/api/main/bpy.msgbus.html
- Blender timers: https://docs.blender.org/api/5.0/bpy.app.timers.html
- Blender Collection link/unlink: https://docs.blender.org/api/4.4/bpy.types.CollectionObjects.html
- Blender FBX limitations: https://docs.blender.org/manual/en/4.4/addons/import_export/scene_fbx.html
- Maya workspace controls: https://help.autodesk.com/cloudhelp/ENU/MayaCRE-Tech-Docs/CommandsPython/workspaceControl.html
- Maya scriptJob: https://help.autodesk.com/cloudhelp/ENU/MayaCRE-Tech-Docs/CommandsPython/scriptJob.html
- Maya fileInfo: https://help.autodesk.com/cloudhelp/2022/ENU/Maya-Tech-Docs/CommandsPython/fileInfo.html
- Maya MFnMesh closest-point API: https://help.autodesk.com/cloudhelp/2024/ENU/MAYA-API-REF/py_ref/class_open_maya_1_1_m_fn_mesh.html
