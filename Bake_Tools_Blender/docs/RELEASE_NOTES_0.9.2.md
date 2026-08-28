# Bake Tools Blender 0.9.2

- Менеджер больше не выходит в `VIEW_3D/WINDOW` и не перекрывает navigation gizmo, zoom/pan/camera/grid controls.
- Геометрия Qt-окна следует за фактической шириной `VIEW_3D/UI`, а не за фиксированными 500–520 px.
- Слева оставлена доступная полоса нативной границы Sidebar: ширина регулируется обычным drag Blender.
- При широкой Sidebar сохраняется исходный двухколоночный интерфейс Maya; при узкой доступно переключение `Main` / `Matcher / TOC`.
- Удалены конфликтующие minimum width главных колонок и добавлены regression-проверки узкого/широкого layout.
- Сохранена безопасная модель frameless owned top-level window; `SetParent/WS_CHILD`, modal loops и recurring Qt dock timer не возвращались.
