# Bake Groups Tool

Bake Groups Tool is a Maya helper for preparing high-poly and low-poly geometry for baking and FBX export.

The package is intended for artists working with bake groups, ZBrush HP meshes, final triangulated low meshes, and Marmoset-friendly HP/LP naming.

## Package Structure

```text
_Documentation/
  Installation EN.md
  Установка RU.md
  Bake Groups User Manual EN.md
  Руководство пользователя Bake Groups RU.md

Bake_Groups/
  Python tool files
  localization files
  icons
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

Then follow the shelf installation steps in:

- `_Documentation/Installation EN.md`
- `_Documentation/Установка RU.md`

## Current Notes

- `PCA Shape` is the default HP analysis strategy.
- `Ignore Floaters` can be enabled in the Algorithm section to skip the floater/decal pass during Analyze HP.
- ZBrush geometry should be placed into a display layer with `zbrush` in its name so smoothing is skipped correctly during export.

