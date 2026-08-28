# Bake Tools Blender 0.9.4

- После успешного `Create Pair` и `Create by Material` временные поля `Pick HP` / `Pick LP` очищаются. Созданная глава продолжает хранить собственные Object/Collection pointers.
- `Combine` выполняет Blender Join для двух и более выделенных meshes, сохраняет активный объект как основной и переносит Bake Tools membership/scope.
- `Separate` разделяет meshes по независимым loose parts, создаёт имена `_PartN` и сохраняет membership/scope.
- `Find ZBrush` анализирует долю треугольных полигонов evaluated HP meshes. ПКМ по кнопке открывает неблокирующий threshold slider и команды Find/Add/Select.
- Maya Display Layer заменён дополнительной недеструктивной Collection `BakeTools_ZBrush_Layer` и сохраняемыми Object pointers. Исходные Blender Collections объектов не меняются.
- `Check Mesh` выделяет и показывает отчёт по точным world-space дубликатам, незарегистрированным ZBrush-кандидатам и meshes с несколькими значимыми loose parts.
- Добавлен headless `mesh_tools_smoke_test.py`; обновлены тесты очистки временных roots и GUI context menu.

Ограничение: Check Mesh намеренно не удаляет дубликаты и не применяет transforms автоматически. В Blender такие действия могут разрушать instances, modifiers и намеренные Empty-иерархии; 0.9.4 выполняет безопасный анализ, выбор и отчёт.
