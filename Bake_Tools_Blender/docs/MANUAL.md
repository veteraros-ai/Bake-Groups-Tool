# Bake Groups Tool — Blender

## Быстрый старт

1. Откройте `View3D > Sidebar > Bake Tools` и нажмите `Open Bake Group Manager`.
2. Выберите HP объект или коллекцию и нажмите `Pick HP`.
3. Выберите LP объект или коллекцию и нажмите `Pick LP`.
4. Нажмите `Create Pair`, задайте имя главы и затем используйте `Analyze HP` и `Assign LP`.
5. `Export Settings` открывает настройки финального сглаживания, cage и экспорта.

Менеджер хранит главы, сабгруппы и HP/LP membership в данных сцены Blender. Для обращения в поддержку откройте контекстное меню Log и сохраните `Support Package`.

## Export Settings, Smooth и Cage

- `Smooth 1/2/3` применяется к HP-мешам сабгруппы. Объект, явно добавленный в ZBrush Layer, subdivision не получает; слово `ZBrush` в имени сабгруппы само по себе сглаживание не отключает.
- ПКМ по Eye изолирует сабгруппу, повторный ПКМ возвращает видимость всех сабгрупп.
- Потяните ЛКМ по пустой области списка, чтобы выделить строки прямоугольником; `Ctrl` добавляет строки к текущему выделению. Cage-команды применяются к выделенным строкам, а при пустом выделении — ко всей главе.
- `Create Cage` создаёт недеформированную topology-preserving копию LP. `Expansion` сдвигает её по стабильным нормалям исходного LP, `Sculpt Cage` включает Blender Sculpt Mode.
- `Find intersections` находит острова пересечения Cage с HP. После проверки `Normal move` сдвигает найденные острова по сохранённой нормали.
- Кнопка display переключает wire/solid, `Delete Cage` удаляет только управляемые Cage-объекты, `Export Cage` записывает отдельный `<Chapter>_Cage.fbx` в выбранную папку экспорта.

## Quick start

Open `View3D > Sidebar > Bake Tools`, pick HP and LP roots, create a chapter, then run `Analyze HP` and `Assign LP`. Use the Log context menu to save a debug log or a support package.
