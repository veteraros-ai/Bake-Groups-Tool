# Bake Groups Tool for Blender 1.0.1

This release updates the installed Blender port with:

- Improved HP analysis, preservation of existing HP groups with Keep HP, and safer handling of invalid transforms.
- Standard FBX export with an optional LP Triangle flag and per-subgroup HP smoothing.
- Marmoset Toolbag package export and an installable Bake Groups Bridge for importing and smoothing HP inside Toolbag.
- Context-sensitive Bake Guide with English, Russian, Japanese, and Simplified Chinese content. Its overview now shows the current Blender manager layout.
- Guide navigation fixes, including mouse-wheel zoom after opening a card from Help.

Install `Bake_Tools_Blender-1.0.1-win64.zip` in Blender 4.2+ on Windows x64. The ZIP includes the required Qt runtime; no separate PySide6 install is needed.

The Blender release is tagged `blender-1.0.1` so it remains distinct from the Maya 1.4.x releases in the same repository.
