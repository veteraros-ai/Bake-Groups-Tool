# Bake Tools Blender 0.8.9

- Полностью удалён небезопасный `SetParent/WS_CHILD`, вызывавший зависание Blender в 0.8.8.
- Восстановлена поддерживаемая Qt-модель frameless top-level window с Blender native owner.
- Менеджер снова отображается, оставаясь без системной шапки и отдельной кнопки закрытия.
- Open/Close выполняются через вкладку `Bake Tools`.
- Сохранены nonmodal `show()`/`popup()` вместо вложенных `exec()`.
- Проверены показ, Hide/Show, resize/move Blender и штатный Win32 `WM_CLOSE` при открытом менеджере.
