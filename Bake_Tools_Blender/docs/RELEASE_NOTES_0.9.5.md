# Bake Tools Blender 0.9.5

- `bg_math_core.cpp` адаптирован как отдельный pybind11-модуль `bg_math_core_blender.pyd` для Blender 5.1, CPython 3.13 и Windows x64. Maya binary и Maya API не используются.
- C++ получает только flattened evaluated world-space coordinates; `bpy`/RNA objects не передаются за native boundary.
- `Analyze HP` использует нативный spatial-grid `calculate_min_distance` для owner proximity test.
- `Assign LP` использует нативный multithreaded `calculate_avg_distance` вместо Python KD traversal.
- Полный исходный API сохранён: bidirectional/surface/min/average distance, collision, symmetry, collision resolution, vertex owner scores, PCA-like shape metrics и fingerprint.
- Добавлен lazy loader `native_core.py`: несовместимый, отсутствующий или не загружающийся `.pyd` не ломает аддон — сервис автоматически продолжает работу на Python fallback.
- На C++ boundary добавлена проверка xyz/triangle stride, NaN/Infinity и положительных tolerance/threshold.
- Исправлена старая Maya-логика `check_mesh_collision`: соседняя spatial-hash ячейка больше не считается коллизией без проверки реальной евклидовой дистанции.
- About показывает активный backend: `C++ 0.1.0 (Blender)` либо `Python fallback`.
- Добавлены `tools/build_native_core.ps1` и `tools/native_core_smoke_test.py` с проверкой Blender ABI и численного совпадения с Python brute-force.

Собранный `.pyd` включён в обычный release ZIP; художникам не нужны Visual Studio, Python SDK или pybind11. Пересборка требуется только разработчику при смене Blender Python ABI/платформы.
