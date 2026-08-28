# Bake Tools Blender 0.9.1

- Менеджер перенесён в отмеченную область Sidebar: ниже нативной строки Close и перед вертикальной полосой вкладок.
- Удалён повторяющийся Qt dock timer, вызывавший re-entrant обработку Qt/Win32 во время перемещения Blender.
- Неизменившаяся геометрия больше не отправляется повторно через `SetWindowPos`.
- На время системного `GUI_INMOVESIZE` полностью приостанавливаются Qt event pump, context capture и dock sync; после завершения manager перестраивается один раз.
- `SetWindowPos` больше не изменяет owner z-order и не отправляет предварительный `WM_WINDOWPOSCHANGING`.
- Сохранена безопасная модель owned top-level Qt window без `SetParent/WS_CHILD`.
