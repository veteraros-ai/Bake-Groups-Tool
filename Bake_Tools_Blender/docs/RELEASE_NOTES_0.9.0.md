# Bake Tools Blender 0.9.0

- Переключение главы в TOC теперь изолирует её HP/LP roots; повторный клик по активной главе показывает все видимые главы, как `activate_root/isolateSelect` в Maya.
- Индекс активной сабгруппы сбрасывается при смене главы, а старые Qt-строки немедленно скрываются и отсоединяются до `deleteLater()`.
- Удалён текст `No active bake group` из пустой рабочей области.
- Color HP использует `Object.color` в Solid/Object Color, вызывает redraw и полностью восстанавливает прежний тип shading и color type.
- Overlay больше не закрывает Blender Sidebar и резервирует полосу для нативных правых controls/popovers.
- Иконка Create Pair возвращена к исходному размеру Maya 52 × 52 px.
- Сохранена безопасная owned-window модель: `GWLP_HWNDPARENT` без `SetParent/WS_CHILD` и без вложенных Qt event loops.
