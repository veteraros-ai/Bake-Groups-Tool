# Bake Groups Tool 1.4.7 — Maya

- Help now opens an illustrated instruction card and enables the question-mark cursor. Selecting a control opens its explanation without running its command. Instructions are available in English, Russian, Japanese and Simplified Chinese.
- The Guide's What's New heading now covers versions **1.4.6 - 1.4.7**. Existing saved guides receive the updated instruction card and heading.
- **Freeze LP normals** is enabled by default. Export preserves existing LP corner normals through triangulation on temporary copies; source meshes and their construction history remain unchanged.
- Marmoset export sends base HP meshes without baking Maya Smooth Preview or forcing FBX triangulation before Toolbag subdivision. This avoids the repeated subdivision that caused excessive memory use.
- Marmoset export supports HP-only and LP-only updates, bridge connection checks, and synchronization paths that preserve existing bake settings and materials where supported.
- HP/LP visibility respects hidden subgroups. A chapter can be excluded from export through its context menu; excluded chapter names are yellow.
- Cage adjustments support undoable gestures. Cage identity follows its source LP, manual edits are retained where valid, and topology/LP-Cage pairing is checked before export. LP and Cage use a shared triangulation plan.

Includes the illustrated guide and its assets, the Toolbag bridge, native Maya 2022–2027 modules, and the external HP subdivider. Blender is not updated by this release.

## Installation

Use the plugin's updater, or extract `Bake_Groups_1.4.7.zip` and install its `Bake_Groups` folder in your Maya scripts directory. Restart Bake Groups after installation.

## Validation

Guide navigation, cancellation, saved-board migration and live language switching are tested using Maya's Qt runtimes. Export, LP normals, Cage, visibility and update-package safety are checked on disposable test scenes; production scenes are not included in the archive.

Normal preservation does not guarantee an identical tangent-space basis in every renderer. Existing Toolbag projects still require the bundled bridge for automatic synchronization.

Package SHA-256: `c5d32e84ca5f47e003fb6c7d73e2e13525dc0b0da1c27926a2263c8c6cb22883`
