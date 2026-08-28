# Архитектура Blender-релиза 1.0.0

## Поток данных

```text
Blender VIEW_3D / Outliner
    │ active Object/Collection
    ▼
BlenderContextBridge
    │ stable cross-editor selection
    ▼
Bake Tools tab → frameless owned PySide6 manager
    │ lifecycle / unique instance / suppression reasons
    ▼
QtWindowManager
    │ UI intent
    ▼
ManagerController
    │ operator call
    ▼
Blender Operators ── undo/save ──► Scene.bake_tools_settings
                                      │
                                      ▼
                               BlenderStateStore
                                      │ immutable snapshot
                                      ▼
                                  ManagerView
```

Для анализа геометрии write path расширен отдельной границей:

```text
Analyze HP Operator (main thread)
    → evaluated MeshSnapshot[] (без RNA references)
    → AnalysisService (pure Python)
    → immutable AnalysisResult
    → validate + atomic HP-membership commit (main thread)
```

Qt-окно не создаёт главы, TOC-элементы или сабгруппы как собственные данные. Оно читает RNA через `BlenderStateStore`, а изменения выполняет только через `ManagerController` и undoable Blender Operators. Нативная N-панель содержит явную команду запуска: открытие сцены и восстановление активной вкладки больше не создают Qt-окно.

`qt_window_manager.py` централизует один `QApplication`, уникальный основной виджет по `objectName`, слабый реестр дочерних диалогов, регистрацию Blender timer и составные причины native suppression. Это точечный BQt-подобный слой управления, но без оборачивания Blender в `QMainWindow`, `QDockWidget`, `SetParent` или `WS_CHILD`. `blender_bridge.py` отвечает только за Blender context, Win32 owner/popup detection, transient z-order и Sidebar geometry.

Popup lifecycle разделён на быстрый и согласующий пути. `WH_GETMESSAGE` перед dispatch скрывает только native HWND и при MMB-drag после закрывающего события сразу возвращает alpha/style, не вызывая Qt, Blender RNA или `SetWindowPos`. Следующий обычный `_pump_events` согласует `native_restore_pending` с составными suppression reasons. Поэтому modal viewport orbit не может задержать визуальное восстановление окна, а Workspace/Sidebar/dialog suppression остаются независимыми.

## Реализованные границы

- `properties.py` — сериализуемое состояние сцены, стабильные ID глав/сабгрупп, UI-, Matcher-, Cage- и Export-настройки;
- `object_repository.py` — устойчивые Object/Collection pointers, рекурсивная классификация относительно HP/LP roots, эксклюзивное membership, selection, visibility и Maya-подобная изоляция активной главы;
- `mesh_tools.py` — Blender-native Join/Separate, evaluated geometry audit и ZBrush Collection/Object-pointer registry;
- `find_similar.py` — evaluated topology/connectivity, нормализованный C++/Python shape fingerprint и one-to-one layout для Find Sim/All;
- `smooth_preview.py` — обратимые служебные Subdivision Surface modifiers по subgroup smooth level;
- `export_service.py` — export scope/preflight plan и cleanup-safe FBX transaction;
- `progress.py` — host-neutral progress/cancel events; Qt лишь отображает их неблокирующим `QProgressDialog`;
- `native_core.py` — lazy optional-extension boundary, plain-coordinate conversion, ABI/load diagnostics и Python fallback;
- `native/bg_math_core_blender.cpp/.pyd` — pybind11 spatial-grid kernel, собранный под Blender CPython 3.13 x64;
- `domain/models.py` — immutable `ManagerView`, `ChapterView`, `SubgroupView`, не импортирующие `bpy`/Qt;
- `domain/analysis.py` — immutable geometry/settings/result DTO без `bpy`/Qt;
- `analysis_service.py` — LP-guided matching, adjacent/floater links, categorization и collision-safe packing;
- `analysis_adapter.py` — evaluated mesh extraction и rollback-safe применение HP-membership;
- `material_distribution.py` — фактически используемые LP-материалы, связные material/island proxy regions, Maya-подобный HP ownership/audit и non-destructive chapter scopes;
- `lp_matching_service.py` — pure LP → HP subgroup matching и material-slot repair;
- `lp_matching_adapter.py` — evaluated LP capture и rollback-safe LP-membership commit;
- `matcher.py` — LP loose-shell extraction, Maya scoring/modes, persistent proposal/link adapter и membership-safe Relocate;
- `store.py` — единственный read adapter из Blender RNA в view model;
- `operators.py` — единственный write path для UI: главы, книги, сабгруппы, eye/lock, smooth/cage overrides и настройки;
- `ui.py` — нативные Open/Close-команды `VIEW_3D/UI`, исключающие автозапуск и системную кнопку закрытия;
- `qt_window.py` — оригинальная двухколоночная UI, узкий responsive fallback, асинхронные меню/диалоги, runtime-localization, Export/About и frameless lifecycle;
- `localization.py` + `localization/*.json` — исходные Maya-словари, Blender-specific override и безопасное повторное применение языка к существующим/динамическим Qt-виджетам;
- `blender_bridge.py` — safe `GWLP_HWNDPARENT` ownership, screen coordinates, transient z-order и слежение за реальной шириной Sidebar при resize/move/multi-monitor; `SetParent/WS_CHILD` не используется;
- `color_preview.py` — обратимый Maya-подобный HP preview через Object Color с сохранением/восстановлением shading mode;
- `structure_adapter.py` — импорт дочерних Empty/Collection-групп в Keep HP membership без reparent;
- `controller.py` — перевод Qt intent в Blender Operators;
- `dependencies.py`/`vendor` — bundled PySide6 без изменения Python художника;
- `sync.py` — сохраняет корректный selection context между VIEW_3D и Outliner после `load_post`, `undo_post`, `redo_post`.

## Что уже связано со сценой

- выбор HP/LP Object roots и Collection roots из Outliner;
- создание, активация, переименование и удаление chapter;
- books → chapters в TOC;
- создание, активация, переименование и удаление subgroup;
- реальный HP/LP membership: Add Selected, move между группами, select members, release-on-delete;
- Object pointers для roots и members, устойчивые к rename и `.blend` roundtrip;
- устойчивые eye/lock states;
- глобальная HP/LP visibility для root и его дочерних Blender Objects;
- Algorithm, Matcher, Export и language state;
- normal ↔ Export Settings и final subgroup controls;
- сохранение в `.blend`, автоматическое обновление окна, единый накопительный log.
- `Analyze HP`: реальные evaluated snapshots, три стратегии, LP owners, sampled vertex linking, locked preservation и повторяемый commit.
- `Assign LP`: bbox/topology fast pass, KD nearest-surface fallback, material repair, unmatched report и atomic unlocked-LP commit.
- `Create Pair`: подсчёт LP-материалов, Maya-style выбор одной/нескольких глав и scoped material membership без reparent Blender Objects.
- `Color HP`: только HP members, стабильный palette index/пользовательский цвет на сабгруппу, тот же цвет в Qt-строке и восстановление исходных цветов.
- `Keep HP`: перенос уже созданной Blender hierarchy в subgroup membership.
- `Combine/Separate`: реальная геометрическая операция с переносом membership, material/UV/attributes средствами Blender и разделением по loose parts.
- `Find ZBrush`: triangle-ratio поиск в активном HP scope; threshold и ZBrush membership сохраняются в Scene.
- `Check Mesh`: world-space duplicate fingerprint, незарегистрированные ZBrush candidates и meaningful loose-part audit с безопасным select/report результатом.
- `HP-LP Matcher`: loose-shell Find Groups, сохранённые Link/Unlink/New clusters, выбор HP из списка, Relocate membership и hard semantic islands для Analyze HP.

## Blender-аналог ZBrush display layer

Blender Collection не является прямой копией Maya Display Layer: один Object может одновременно находиться в нескольких Collections, а viewport display state хранится иначе. Поэтому `BakeTools_ZBrush_Layer` используется как дополнительная недеструктивная Collection и не удаляет объект из исходной hierarchy художника. Источником истины остаются `Scene.bake_tools_settings.zbrush_members` с Object pointers; custom marker на Object и Collection служит диагностическим/совместимым следом. Такой контракт устойчив к rename и сохраняется в `.blend`.

## FBX smoothing transaction

Blender 5.1 оставляет у FBX-оператора свойство `use_mesh_modifiers_render`, но в RNA оно помечено как отключённое со времён Blender 2.8. Практически FBX получает evaluated viewport modifier stack. Поэтому служебные `Bake Tools Export Smooth` и `Bake Tools LP Export Triangulate` включаются в viewport только внутри `_export_fbx()` и удаляются в `finally`.

Если на объекте уже есть `Bake Tools Smooth Preview`, export не добавляет второй Subdivision. Вместо этого оба флага `show_viewport/show_render` временно включаются и затем восстанавливаются. Уровень берётся из subgroup membership; `ZBrush` в имени сабгруппы ничего не исключает. Subdivision блокирует только явная Object-метка `bake_tools_zbrush`.

## Распределение LP-материалов

Maya получает shading engines через `MFnMesh.getConnectedShaders`, показывает `Multiple LP Materials` при количестве больше одного и записывает `pair['material_slots']` для одной главы либо физически переподчиняет меши нескольким новым root pairs. Blender считает уникальные `Material` только по индексам, реально используемым полигонами; неиспользуемые material slots не повышают счётчик.

Для одной главы `material_slots=True` сохраняется в `BakeToolsPair`, а `analysis_adapter` строит виртуальные LP material regions из полигонов evaluated mesh. Для нескольких глав `material_distribution` группирует LP Objects по material signature, но ownership HP вычисляет не по объединённому BBox главы. Как в Maya, для каждого evaluated LP строятся связные shell/material-прокси; затем выполняются direct owner scoring, привязка мелких floaters к устойчивому крупному HP-владельцу, обратный LP→HP audit и вывод неуверенных HP в `Review_Unmatched`. Point-distance narrow phase использует `bg_math_core_blender`, сохраняя Python fallback.

Каждая глава получает `hp_scope_members/lp_scope_members`; `ObjectRepository` использует этот scope как границу Analyze, Assign, selection и visibility. Исходные parenting и collections художника не изменяются. В Log сохраняются счётчики direct/container/floater/audit/review, соответствующие диагностике Maya.

## Покрытие Maya Analyze HP

В 0.8 реализован основной data flow, LP material regions и полезный fallback-алгоритм. В 0.9.4 добавлен отдельный preflight `Check Mesh`, но он пока не является автоматическим gate внутри `Analyze HP`. В 0.9.5 перенесён полный публичный API `bg_math_core.cpp`; min/average distance уже используются сервисами, остальные entry points готовы для следующих алгоритмических этапов. Ещё не перенесены disconnected loose-shell partition внутри одного material region, boundary-hole и triangle-surface proxy, ZBrush-specific grouping semantics, GT/manual hard clusters, сложная финальная оптимизация лимита, progress/cancel и source-revision guard. Поэтому `AnalysisService` имеет статус **Prototype**, а не feature complete.

`Ignore Floaters=True` намеренно пропускает floater/decal pass: оригинал передаёт в worker `detect_floaters=not ignore_floaters`.

## Следующая архитектурная связка

1. hardening Analyze/Assign: progress/cancel, stale-result guard и Maya fixtures;
2. production fixture comparison для HP-LP Matcher и confidence/ambiguity diagnostics;
3. `CageService`: create/inflate/intersection/display/delete;
4. export round-trip validation в целевых engines.

Нельзя передавать Blender RNA objects в worker threads. Worker получает только immutable массивы/DTO, а результат применяется Blender Operator в main thread.

## Проверка

`tools/smoke_test.py` запускается в factory-startup Blender 5.1 и проверяет launcher-panel, frameless Qt-конструкцию, stable IDs, Store/ViewModel, exclusive HP/LP move, object rename, select, visibility, очистку Pick roots, release-on-delete и `.blend` roundtrip. `tools/matcher_smoke_test.py` проверяет LP shells, Maya merge повторных LP, Link persistence, ViewModel, стабильный цвет строки и отсутствие tooltip у сабгруппы. `tools/native_core_smoke_test.py` проверяет импорт cp313-модуля, численное совпадение nearest-distance с Python brute-force, collision threshold и malformed-buffer guards. Остальные smoke tests покрывают Analyze/Assign, scene tools, Color/Keep, Collection roots, export и window lifecycle.
