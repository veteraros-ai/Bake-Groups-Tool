# Bake Tools Blender 0.8.3

- Отдельное PySide6-окно и автоматический launcher удалены.
- Полный менеджер находится в `View3D → Sidebar (N) → Bake Tools` и прокручивается вместе с Blender UI.
- Компактный блок `Manager connected / HP / LP` заменён основной рабочей панелью с Pick, Create Pair, Analyze, сабгруппами, Matcher, TOC и Log.
- Pick HP/LP и Add Selected выполняются из этой же нативной панели.
- `Create Pair` открывает нативный Blender-диалог `Create Chapter`: HP base, LP base или Custom name.
- Release больше не включает PySide6/Qt runtime.

Версия следует принятой схеме: `0.8.2 → 0.8.3`.
