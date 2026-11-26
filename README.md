# Bake Groups Tool

Bake Groups Tool prepares high-poly and low-poly geometry for baking and FBX export in Maya and Blender.

The package is intended for artists working with bake groups, ZBrush HP meshes, final triangulated low meshes, and Marmoset-friendly HP/LP naming.

## Package Structure

```text
_Documentation/
  Bake Groups User Manual EN.md
  Bake Groups User Manual JA.md
  Bake Groups User Manual RU.md
  Bake Groups User Manual ZH-CN.md
  User_Manual_Bake_Groups_EN.md
  User_Manual_Bake_Groups_RU.md
  Analyze_HP_Flow_RU.drawio

Bake_Groups/
  Python tool files
  localization files
  icons
  bg_math_core.cpp source file
  bg_math_core.pyd builds for Maya 2022-2027

Bake_Tools_Blender/
  Blender 4.2+ add-on source
  bundled-PySide6 release builder
  manuals, privacy notice and update manifest
```

## Supported Maya Versions

The package includes `bg_math_core.pyd` builds for:

- Maya 2022
- Maya 2023
- Maya 2024
- Maya 2025
- Maya 2026
- Maya 2027

## Blender 1.0.2

The Blender build supports Blender 4.2+ on Windows x64. Install the
published `Bake_Tools_Blender-1.0.2-win64.zip` from Blender Preferences without
unpacking it. The release archive bundles the matching PySide6 Essentials and
shiboken6 runtime; the large binary runtime is intentionally not committed to Git.
The built-in Bake Guide opens from Help. The original artist-authored PureRef
manual remains bundled as `Bake_Tools_Blender/docs/Manual.pur` and opens from About.

Blender telemetry is disabled until the artist explicitly opts in. It sends one
pseudonymous installation/update event per version and never sends scene data,
object names or file paths. See `Bake_Tools_Blender/PRIVACY.md`.

## Maya 1.5.0

This release introduces the Mesh Check tab, material-aware HP chapter assignment,
Color HP palettes and presets, and expanded context help. FBX export preserves
placement and face orientation for mirrored LP meshes.

For a first installation, extract `Bake_Groups_Maya_1.5.0_Installer.zip` and
drag `Install_Bake_Groups.py` into the Maya viewport. The installer copies
the plugin to Maya's user scripts, adds or updates its Shelf button, and saves
the Shelf. See `Instal.txt` for English, Russian, Japanese and Simplified Chinese
instructions. Existing installations can use the built-in updater.

## Installation

Copy the `Bake_Groups` folder into:

```text
C:\Users\<USER>\Documents\maya\scripts\Bake_Groups
```

Then open the user manual for your preferred language:

- `_Documentation/Bake Groups User Manual EN.md`
- `_Documentation/Bake Groups User Manual RU.md`
- `_Documentation/Bake Groups User Manual JA.md`
- `_Documentation/Bake Groups User Manual ZH-CN.md`

## Building bg_math_core

The compiled `bg_math_core.pyd` files can be rebuilt from the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\build_math_core.ps1
```

By default, the script builds Maya 2022-2027 into `Bake_Groups\bin\<version>`.

## Current Notes

- `PCA Shape` is the default HP analysis strategy.
- `Ignore Floaters` is enabled by default in the Algorithm section to skip the floater/decal pass during Analyze HP.
- ZBrush geometry should be placed into a display layer with `zbrush` in its name so smoothing is skipped correctly during export.
