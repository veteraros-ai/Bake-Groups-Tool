# Bake Tools Blender 0.8.4

- Возвращён оригинальный двухколоночный PySide6 UI вместо упрощённого нативного менеджера.
- Вкладка Bake Tools открывает менеджер сразу, без второй кнопки.
- Qt-окно получает Blender как native owner: оно выше только Blender, но не перекрывает другие приложения Windows.
- Добавлена псевдопристыковка к правому краю Blender с отслеживанием перемещения и размера главного окна.
- Свободное положение и состояние dock/undock сохраняются через QSettings.
- Create Pair использует оригинальный диалог Name Mismatch с HP, LP и Custom.
- Qt UI остаётся подключённым к Blender scene state, Object/Collection roots и undoable operators.
- Bundled PySide6 снова включается в Windows x64 release.
