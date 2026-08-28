# Bake Tools Blender 0.8.8

> Отозванная версия: native `SetParent/WS_CHILD` вызвал зависание интерактивного Blender. Не использовать; исправлено в 0.8.9.

- Менеджер переведён из owned Qt popup в frameless `WS_CHILD` внутри Blender.
- Удалены верхняя системная область, отдельная кнопка закрытия и перехват Blender resize-border.
- Открытие и закрытие выполняются только из вкладки `Bake Tools` в Sidebar.
- Менеджер следует за resize/move/minimize и переносом Blender на другой монитор.
- Удалены modal/nested Qt event loops: диалоги используют `show()`, меню — `popup()`.
- Добавлены GUI-регрессии native style, parent availability, nonmodal dialog, Sidebar geometry и Blender-side Close.
