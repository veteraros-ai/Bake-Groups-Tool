# Bake Tools Blender 0.8.2

- Вкладка `Bake Tools` автоматически открывает менеджер при первом показе; промежуточная кнопка `Open Bake Group Manager` удалена.
- PySide6-менеджер привязан к HWND текущего процесса Blender как owned window: он следует за активацией и сворачиванием Blender и не является отдельным приложением.
- Добавлен Blender context bridge. Он запоминает последнее окно, active Object и active/selected Collection до обработки Qt click и предоставляет корректный override для `bpy.ops`.
- `Pick HP` и `Pick LP` работают через сохранённый Blender selection, в том числе когда Qt callback уже не имеет исходного `VIEW_3D`/`OUTLINER` context.
- В нативной панели остались компактные HP/LP indicators и eyedropper actions для прямой проверки связи со сценой.
- Добавлены regression-сценарии потери active context для Object roots и Collection roots.

Схема версий проекта последовательная: `0.8.1 → 0.8.2`, `0.8.9 → 0.9.0`, `0.9.9 → 1.0.0`.
