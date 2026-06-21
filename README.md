# Bake Groups Tool

Bake Groups Tool is a Maya helper for preparing high-poly and low-poly geometry for baking and FBX export.

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
```

## Supported Maya Versions

The package includes `bg_math_core.pyd` builds for:

- Maya 2022
- Maya 2023
- Maya 2024
- Maya 2025
- Maya 2026
- Maya 2027

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
