"""Verify exported FBX geometry, not only temporary modifier creation."""

from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import bpy


ADDON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_ROOT.parent))

import Bake_Tools_Blender as addon  # noqa: E402


def add_ref(collection, obj):
    ref = collection.add()
    ref.target = obj
    ref.last_name = obj.name


def main():
    addon.register()
    from Bake_Tools_Blender.addon.bake_tools_blender.export_service import (
        build_export_plan,
        execute_export,
    )

    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    state = bpy.context.scene.bake_tools_settings
    pair = state.pairs.add()
    pair.item_id = uuid4().hex
    pair.name = "SmoothExport"
    subgroup = pair.subgroups.add()
    subgroup.item_id = uuid4().hex
    subgroup.name = "ZBrush_Huge_001"
    subgroup.smooth_level = 1

    bpy.ops.mesh.primitive_cube_add()
    ordinary = bpy.context.object
    ordinary.name = "Ordinary_In_ZBrush_Subgroup"
    bpy.ops.mesh.primitive_cube_add(location=(3.0, 0.0, 0.0))
    marked = bpy.context.object
    marked.name = "Explicit_ZBrush_Mesh"
    marked["bake_tools_zbrush"] = True
    add_ref(subgroup.hp_members, ordinary)
    add_ref(subgroup.hp_members, marked)
    state.active_pair = 0
    state.active_pair_id = pair.item_id
    state.export_scope = "CHAPTER"
    state.export_include_hp = True
    state.export_include_lp = False
    state.export_include_cage = False
    state.export_files = "SEPARATE"
    state.preview_smoothing = False

    directory = Path(bpy.app.tempdir) / "BakeToolsExportSmoothingGeometry"
    plan = build_export_plan(state, pair, str(directory))
    paths = execute_export(bpy.context, plan)
    assert len(paths) == 1 and Path(paths[0]).is_file()
    assert not ordinary.get("bake_tools_zbrush", False)
    assert marked.get("bake_tools_zbrush", False)
    assert not any(modifier.name.startswith("Bake Tools Export Smooth") for modifier in ordinary.modifiers)
    assert not any(modifier.name.startswith("Bake Tools Export Smooth") for modifier in marked.modifiers)

    bpy.data.objects.remove(ordinary, do_unlink=True)
    bpy.data.objects.remove(marked, do_unlink=True)
    bpy.ops.import_scene.fbx(filepath=paths[0])
    imported = [obj for obj in bpy.context.selected_objects if obj.type == "MESH"]
    assert len(imported) == 2
    ordinary_export = next(obj for obj in imported if obj.name == "Ordinary_In_ZBrush_Subgroup")
    marked_export = next(obj for obj in imported if obj.name == "Explicit_ZBrush_Mesh")
    print(
        "BAKE_TOOLS_EXPORT_SMOOTH_GEOMETRY_STATE ordinary_polygons={} marked_polygons={}".format(
            len(ordinary_export.data.polygons), len(marked_export.data.polygons)
        )
    )
    assert len(ordinary_export.data.polygons) > 6, "Exported FBX did not apply subgroup smoothing"
    assert len(marked_export.data.polygons) == 6, "Explicit ZBrush mesh must not be subdivided"

    addon.unregister()
    print("BAKE_TOOLS_EXPORT_SMOOTH_GEOMETRY_OK named_zbrush_group=1 explicit_marker=0")


if __name__ == "__main__":
    main()
