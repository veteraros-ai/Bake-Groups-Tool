# Bake Tools Blender 0.8.6

## LP materials at Create Pair

- Added Blender-native inspection of materials actually used by LP polygons.
- Restored the Maya `Multiple LP Materials` decision dialog with `Create as one chapter`, `Create several chapters`, and `Cancel`.
- The one-chapter path persists `material_slots` on the chapter. Analyze HP then captures separate virtual LP material regions from a multi-material mesh.
- A regular single-material chapter is automatically placed in a cleaned material-named book, matching Maya.

## Several chapters

- LP objects are bucketed by their material signature.
- HP objects are assigned to the closest LP material bucket using overlap, bounding-box gap, and center distance.
- Chapters are placed in the next `Book_NN` and receive exclusive HP/LP object scopes.
- Blender hierarchy, collections, constraints, and rigs are not reparented. Scoped membership replaces Maya's destructive transform reparenting while preserving the same downstream chapter boundary.

## Validation

- Added `tools/material_distribution_smoke_test.py` for material counting, both decision outcomes, automatic material books, material-region capture, and scoped HP/LP membership.
