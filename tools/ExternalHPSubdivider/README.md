# External HP FBX Export

Native HP subdivision helper bundled with Bake Groups. Standard FBX export writes a compact, UV-free geometry stream directly from Maya API, subdivides it in this helper, and produces FBX. The older FBX plus per-mesh TSV input remains available as a compatibility path. Level zero retains unsmoothed ZBrush meshes, their per-corner normals, and hard edges. The optional OBJ writer uses `std::to_chars` with 17 significant digits and a 4 MiB buffer.

## Build on Windows

With CMake and a Visual Studio C++ x64 toolchain installed:

```powershell
cmake -S native -B build -A x64
cmake --build build --config Release --parallel
```

The executable is created in `build/Release/bg_obj_subdivider.exe` for the Visual Studio generator. The plugin bundles its runtime copy at `Bake_Groups/bin/bg_obj_subdivider.exe`.

The plugin invokes it with a temporary `.bghp` geometry stream containing world-space positions, polygon indices, materials, normals for level-zero meshes, and individual Smooth levels. It contains no UV. No HP scene copies or intermediate FBX are created on this path. Temporary inputs are deleted after conversion; only the resulting FBX remains in the selected export folder. If the direct path fails, the plugin tries Maya's FBX export path and reports a warning.

`--input-binary` selects the geometry stream; `--format fbx` selects FBX output. `--input` without `--input-binary` retains FBX input support. OBJ output remains available with `--format obj`.

The vendored FBX reader is ufbx and the FBX writer is ufbx_write; their licenses are in `native/third_party/ufbx/LICENSE` and `native/third_party/ufbx_write/LICENSE`.

## Historical OBJ writer benchmark

Measured on the same 600-HP suspension package and MSVC Release build, three runs per level:

| Smooth level | Original median | Buffered writer median | Faster |
|---|---:|---:|---:|
| 1 | 7.58 s | 1.65 s | 4.6x |
| 2 | 30.89 s | 6.63 s | 4.7x |

The timings cover the native helper. Coordinates and normals parsed to exactly equal double values, face-index lines matched exactly, and MTL contents were identical. The one-byte OBJ size difference came from the different MTL filename in the benchmark output name.

## Full-path benchmark after direct transfer

Measured in Maya 2027 on the 600-HP suspension package using `tests/benchmark_external_hp_maya.py`. These are one-run wall-clock measurements of Maya preparation plus the native helper, excluding scene import and OBJ validation. Both paths produced the same face count.

| Regular HP Smooth | Direct transfer + helper | FBX compatibility path | Face count |
|---|---:|---:|---:|
| 1 | 3.07 s | 6.68 s | 1,489,108 |
| 2 | 6.90 s | 11.44 s | 4,984,936 |

The direct stream was about 38 MB; the compatibility FBX was about 23 MB. The direct path is faster despite the larger transfer file because it avoids Maya duplicates, subgroup unions, Freeze Transformations, and FBX serialization. Timing differences can vary by scene and disk.
