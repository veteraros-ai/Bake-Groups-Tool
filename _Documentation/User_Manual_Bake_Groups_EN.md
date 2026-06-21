# Bake Groups - User Manual

## 1. What The Tool Does

Bake Groups helps prepare HP/LP geometry for baking and final FBX export in Maya.

In practical artist terms, the tool helps organize a scene into clear bake groups: match high-poly to low-poly, apply Smooth to exported HP geometry, skip ZBrush meshes, build final triangulated low meshes with correct names, and export them so Marmoset can pick up HP/LP pairs automatically.

Main workflow:

```text
Prepare scene -> Create Pair -> Find ZBrush -> Analyze HP
-> adjust subgroups -> Assign LP -> Combine Fin -> Final Group -> Export
```

## 2. Terms

### HP / High Poly

Detailed geometry used for baking normal, curvature, and other maps. In the tool it lives inside the HP root.

### LP / Low Poly

Game-ready or final low-poly geometry. In the tool it lives inside the LP root and later gets combined into final `_low` meshes.

### HP Root And LP Root

The main groups that contain high-poly and low-poly geometry. After creating a pair, the tool renames them to:

```text
<ChapterName>_HP
<ChapterName>_LP
```

### Chapter

One working HP/LP pair. Usually one asset, one model section, or one logical group of parts.

### Book

A set of chapters in `TABLE OF CONTENTS`. Books are used for batch export.

### Subgroup

A paired group inside HP and LP roots. Example:

```text
Bolts_001_HP
Bolts_001_LP
```

The HP in `Bolts_001_HP` should bake against the LP in `Bolts_001_LP`.

### ZBrush Mesh

Heavy triangulated HP geometry from ZBrush or a similar source. It usually should not be smoothed like regular subdivision HP. Put real ZBrush meshes into a display layer with `zbrush` in its name.

### Final Group

The final checking mode after `Combine Fin`. Here you review final groups, set Smooth levels, and export.

## 3. Launching

After installation, click the `BAKE GROUPS` shelf button. The main window opens.

Bottom-right buttons:

- `Save` - save the session;
- `Load` - load a session;
- `Language` - choose interface language.

The selected language is saved in Maya preferences and restored next time.

## 4. Prepare The Scene First

Before analysis, always do this:

1. Split compound meshes with Maya `Mesh > Separate` or the `Separate` button.
2. Freeze transforms with `Modify > Freeze Transformations`.

Practical rule:

```text
One physical part = one separate transform mesh.
Translate = 0, Rotate = 0, Scale = 1.
```

This matters because `Analyze HP` and `Assign LP` rely on the real size, position, and bounding box of each transform.

## 5. Create A Working Chapter

1. Select the HP root and click `Pick HP`.
2. Select the LP root and click `Pick LP`.
3. Click `Create Pair`.

The tool creates a chapter, renames the roots to `<ChapterName>_HP` and `<ChapterName>_LP`, adds the chapter to `TABLE OF CONTENTS`, and prepares the meshes inside the roots.

If your HP hierarchy is already organized manually, enable `Keep HP` before creating the pair or before analysis. If `Keep HP` is off, the tool may flatten and rebuild the HP structure.

## 6. Visibility And Navigation

### TABLE OF CONTENTS

The right panel lists chapters and books. Clicking a chapter activates it and shows its subgroups on the left.

### Auto-Isolate

`Auto-Isolate` automatically isolates the active chapter in the Maya viewport.

### HP Visible / HP Hidden

Shows or hides the active chapter HP root.

### LP Visible / LP Hidden

Shows or hides the LP root. In `Final Group` mode this controls final combined low meshes and becomes `Low Visible / Low Hidden`.

### Groups Vis / Groups Hidden

Shows or hides all subgroups in the active chapter.

## 7. Find ZBrush Geometry

Click `Find ZBrush`. The tool selects HP meshes in the active chapter with a high percentage of triangular faces.

Right-click `Find ZBrush` to open the `Triangular faces` threshold slider. The default is 50%. The context menu also has `Find ZBrush now`.

Important:

- `Find ZBrush` only selects candidates;
- display layer links are not changed;
- real ZBrush meshes must be placed manually into a display layer with `zbrush` in its name.

During export, Smooth is skipped for meshes in a ZBrush display layer.

## 8. Algorithm Settings

The `Algorithm` section controls how `Analyze HP` groups HP meshes.

Main settings:

- `HP Clustering Strategy` - grouping strategy;
- `Spatial Volume Match` - grouping by volume and position;
- `PCA Shape Alignment` - shape and orientation comparison;
- `Topology Fingerprint` - topology comparison;
- `Calculate Symmetry Score (.pyd)` - use the C++ core for symmetry scoring;
- `HP Collision (%)` - allowed overlap;
- `HP Link Vtx` and `HP Link Dist (%)` - detect compound HP elements by nearby vertices;
- `Ignore Floaters` - skip the floater/decal pass when small meshes with holes are being pulled into the wrong large group;
- `Bolt Elongation (<)`, `Bolt Symmetry (<)`, `Wire Elongation (>)` - bolt and wire heuristics.

Default settings are usually enough for normal work.

## 9. Analyze HP

Before clicking `Analyze HP`, check that:

- meshes were split with `Separate` or `Mesh > Separate`;
- transforms are frozen;
- ZBrush meshes are in a ZBrush display layer;
- the correct chapter is active.

`Analyze HP` analyzes HP and creates subgroups. At the start, the tool checks for possible ZBrush candidates. If it finds meshes that look like ZBrush but are not in a ZBrush layer, it shows a warning:

- `Skip` - continue analysis;
- `Select` - select suspicious meshes and stop analysis.

Usually it is better to click `Select`, review the meshes, and place real ZBrush geometry into the correct layer.

If small details with holes, floaters, or decals end up in the wrong group, enable `Ignore Floaters` in `Algorithm` and run `Analyze HP` again. This skips the automatic pass that attaches such meshes to nearby large HP parts, while keeping the rest of the HP analysis active.

## 10. Review And Edit Subgroups

After analysis, the left panel shows the subgroup list.

Each subgroup row has:

- `Vis` - show or hide the subgroup;
- subgroup name - double-click selects contents, right-click renames;
- `Add` - add selected meshes to this subgroup;
- lock icon - protect the subgroup from being rebuilt;
- `X` - delete the subgroup.

### Create Group

Inside `Algorithm`, use `Group name` and `Create Group` to create paired HP/LP subgroups manually.

### Add To Active

`Add` moves selected meshes into the active subgroup. Use it for manual cleanup after analysis.

## 11. Assign LP

When HP subgroups look correct, click `Assign LP`.

The tool analyzes LP meshes and assigns them to LP subgroups based on the HP structure. Cleaner scene preparation and better HP groups give more stable results.

If LP does not assign correctly, check:

- whether `Analyze HP` was run;
- whether HP subgroups are empty;
- whether LP meshes are placed near their HP geometry;
- whether transforms are frozen.

## 12. Manual Similarity Search

### Find Sim

`Find Sim` searches for similar meshes with layout awareness. Use it for repeated elements.

### Find All

`Find All` searches more broadly and relies less on layout.

## 13. GT Matcher

The top-right panel helps manually solve complex HP/LP matching cases.

Main controls:

- `Find LP Groups` - find possible LP groups;
- `Tolerance (%)` - search tolerance;
- `Min HP/LP` - minimum match count;
- `Strict Geo Check (Resolve Overlaps)` - strict overlap check;
- `Link` - save the selected match;
- `Unlink` - remove the match;
- `New` - create a custom manual link;
- `Relocate HP` - move HP according to the saved link.

## 14. Combine Fin

When HP and LP subgroups are ready, click `Combine Fin`.

The tool:

- renames HP for final export;
- combines LP subgroups into final `_low` meshes;
- triangulates final low meshes;
- places combined low meshes into the final structure.

After `Combine Fin`, final low meshes may be hidden. In `Final Group` mode they are controlled by `Low Visible / Low Hidden`.

If the completion message says `LP 0`, final low meshes were not built. Usually `Assign LP` was not run or LP subgroups are empty.

## 15. Final Group

Click `Final Group` to enter final checking mode.

In this mode:

- `Smooth View` and `Export` appear;
- `LP Visible / LP Hidden` controls final combined low meshes;
- subgroup rows show HP Smooth levels.

If you see `No final meshes. Run Combine Fin first.`, go back and run `Combine Fin`.

`Back` returns to the normal subgroup list.

## 16. Smooth Levels

Each row in `Final Group` has a Smooth dropdown:

- `Smooth 0` - no smoothing;
- `Smooth 1` - light smoothing;
- `Smooth 2` - standard smoothing;
- `Smooth 3` - strong smoothing.

The `+` and `-` buttons increase or decrease the Smooth level.

`Smooth View` previews smoothing in the viewport. It is only a preview: export applies Smooth again and then rolls it back. ZBrush geometry in a ZBrush layer is skipped.

## 17. Export Chapter

A normal click on `Export` exports the active chapter.

Before export, check that:

- `Combine Fin` was run;
- you are in `Final Group` mode;
- Smooth levels are set;
- ZBrush meshes are in a ZBrush display layer.

HP export applies Smooth to regular HP geometry, skips ZBrush layer geometry, and exports HP with zeroed transforms. LP export uses final triangulated `_low` meshes.

## 18. Export Menu

Right-click `Export` to open export options:

- `Export Book -> Separate HP and LP` - batch export all chapters in the book as separate HP/LP files;
- `Export Book -> HP+LP single file` - batch export HP and LP into one FBX per chapter;
- `Export LP` - LP-only export;
- `Export HP` - HP-only export.

If LP export says combined LP meshes were not found, run `Combine Fin` first.

## 19. Books

Use `TABLE OF CONTENTS` to organize chapters into books.

The context menu can:

- create a book;
- rename a book;
- `Add to` - add selected chapters to a book;
- `Extract from the book` - remove chapters from a book;
- `Delete Selection` - remove the list entry.

The eye icon shows or hides a chapter or book in the viewport.

## 20. Sessions

### Save

`Save` saves the current Bake Groups session. Some data is saved automatically, but manual save is useful after major edits.

### Load

`Load` loads a JSON session. Both raw chapter lists and files with a `pairs` key are supported.

## 21. Common Issues

### Analyze HP Creates Strange Groups

Make sure meshes were split with `Separate`, transforms are frozen, and ZBrush meshes are in a ZBrush layer. If HP is already organized manually, use `Keep HP`.

### ZBrush Geometry Was Smoothed

Check the display layer. To skip Smooth, the layer name must contain `zbrush`.

### Regular HP In A ZBrush-Named Subgroup Was Not Smoothed

Smooth is skipped only by ZBrush display layer. If regular HP was not smoothed, make sure it is not accidentally in the ZBrush layer.

### Find ZBrush Does Not Find Everything

Lower the `Triangular faces` threshold with right-click on `Find ZBrush`.

### Assign LP Is Slow

On large scenes this can be normal. Before running it again, make sure LP is prepared and temporary meshes are removed.

### Final Group Is Empty

Run `Combine Fin` first. `Final Group` only shows final data after the combine step.

## 22. Quick Checklist

```text
Separate
Freeze Transformations
Pick HP
Pick LP
Create Pair
Find ZBrush
Analyze HP
Review subgroups
Assign LP
Combine Fin
Final Group
Smooth View
Export
```
