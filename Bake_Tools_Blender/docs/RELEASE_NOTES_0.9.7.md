# Bake Tools Blender 0.9.7

## Добавлено

- Реальные `Find Sim` и `Find All` с переключением по ПКМ, проверкой topology signature и взаимного расположения выбранного набора.
- `Smooth View` на основе обратимых служебных Subdivision Surface modifiers и уровней сабгрупп; ZBrush layer исключается.
- Реальный FBX Export: Active Chapter / Active Book / All Books, HP/LP/Cage, Separate, HP+LP one file, LP in one file и By Material.
- Единый неблокирующий progress/cancel channel для Analyze HP, Assign LP, Find, Smooth, Export, material chapters, Find ZBrush, Check Mesh, Combine и Separate.
- Локализация новых действий и progress-состояний для EN, RU, JA и zh-CN.

## Совместимость с Maya

- Cage экспортируется отдельным FBX даже в режиме HP+LP one file.
- Material-named book объединяется в `{book}_HP/LP/Cage`; обычная container book экспортирует главы отдельно.
- LP получает временный Triangulate modifier только на время FBX-записи.
- Smooth preview включается для render evaluation только на время HP-экспорта и затем восстанавливается.

## Надёжность

- Qt progress dialogs немодальны и не запускают вложенный event loop, блокирующий Blender.
- Cancel не коммитит selection Find и откатывает частичный Smooth View.
- Combine/Separate показывают прогресс без Cancel, поскольку единичные Blender mutation-операции нельзя безопасно прерывать посередине.
- При отключении аддона удаляются все служебные Smooth View modifiers.
- Pick HP/LP перед выполнением перечитывает текущий Blender context, чтобы stale active Object не подменял новый root.

## Проверено

- Blender 5.1 / Windows x64 / Python 3.13.
- Реальная запись HP и LP FBX.
- Export plan для combined, Cage separate, material merge/container и LP-one-file.
- GUI progress: non-modal, runtime-localized, cancelable.
- Регрессии Analyze HP, Assign LP, collections, material distribution, isolation, Color/Keep HP, mesh tools, native C++ core и Python fallback.
