# Bake Tools Blender 0.8.5

- Кнопка Assign LP выполняет реальное LP → HP subgroup matching.
- Добавлены Maya-подобные bbox prefilter, topology fast-confirm и precise nearest-surface fallback.
- Geometry calculation вынесен в pure `LPMatchingService`; Blender RNA используется только на capture/commit границах.
- Commit атомарно заменяет unlocked LP membership и восстанавливает backup при ошибке.
- Locked LP members сохраняются, unmatched LP остаются без группы и записываются в Log/Debug.
- Поддержан material-slot repair для групп с префиксами `M01`, `M02`, ...
- Qt Assign LP подключён к новому undoable Blender operator.
- Добавлен headless acceptance test для ошибочного membership, lock и unmatched.
