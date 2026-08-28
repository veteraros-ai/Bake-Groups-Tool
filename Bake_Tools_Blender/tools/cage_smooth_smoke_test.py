"""Regression coverage for explicit ZBrush smoothing and Blender Cage objects."""

from __future__ import annotations

import sys
import json
import math
from pathlib import Path
from uuid import uuid4

import bpy
from mathutils import Vector


ADDON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_ROOT.parent))

import Bake_Tools_Blender as addon  # noqa: E402


def cube(name, location=(0.0, 0.0, 0.0)):
    bpy.ops.mesh.primitive_cube_add(location=location)
    obj = bpy.context.object
    obj.name = name
    return obj


def add_ref(collection, obj):
    ref = collection.add()
    ref.target = obj
    ref.last_name = obj.name


def main():
    addon.register()
    from Bake_Tools_Blender.addon.bake_tools_blender.cage_service import (
        CAGE_MARKER, _chapter_diagonal, create_cages, delete_cages, expand_cages,
        find_intersections, resolve_delta, sync_visibility,
    )
    from Bake_Tools_Blender.addon.bake_tools_blender.export_service import (
        _remove_temporary_modifiers, _temporary_export_smoothing,
    )
    from Bake_Tools_Blender.addon.bake_tools_blender.mesh_tools import mark_zbrush_objects
    from Bake_Tools_Blender.addon.bake_tools_blender.smooth_preview import apply_preview, clear_preview

    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    state = bpy.context.scene.bake_tools_settings
    pair = state.pairs.add()
    pair.item_id = uuid4().hex
    pair.name = "Chapter"
    subgroup = pair.subgroups.add()
    subgroup.item_id = uuid4().hex
    subgroup.name = "ZBrush_Huge_001"
    subgroup.smooth_level = 2

    # Offset the HP so some Cage edges cross its shell.  Exact coincident
    # surfaces are intentionally not treated as a penetration by BVH rays.
    ordinary_hp = cube("Ordinary_HP", (0.75, 0.0, 0.0))
    marked_hp = cube("Marked_ZBrush_HP", (3.0, 0.0, 0.0))
    stale_registry_hp = cube("Registry_Only_HP", (6.0, 0.0, 0.0))
    low = cube("Chapter_LP")
    low_parent = bpy.data.objects.new("LP_Transform_Root", None)
    bpy.context.collection.objects.link(low_parent)
    low_parent.location = (2.0, 3.0, 0.0)
    low.parent = low_parent
    low.location = (-2.0, -3.0, 0.0)
    low.rotation_euler.z = math.radians(17.0)
    low.scale = (1.2, 0.8, 1.1)
    add_ref(subgroup.hp_members, ordinary_hp)
    add_ref(subgroup.hp_members, marked_hp)
    add_ref(subgroup.hp_members, stale_registry_hp)
    add_ref(subgroup.lp_members, low)
    state.active_pair = 0
    state.active_pair_id = pair.item_id

    mark_zbrush_objects(bpy.context, state, (marked_hp,))
    # Registry/collection entries are selection indexes, not the explicit
    # ZBrush marker.  A stale index must not suppress subdivision.
    add_ref(state.zbrush_members, stale_registry_hp)
    zbrush_collection = bpy.data.collections.get("BakeTools_ZBrush_Layer")
    zbrush_collection.objects.link(stale_registry_hp)
    bpy.context.view_layer.update()
    low_source_coords = tuple(vertex.co.copy() for vertex in low.data.vertices)
    low_source_world = tuple(low.matrix_world @ coordinate for coordinate in low_source_coords)
    low_modifier = low.modifiers.new("Must not bake into Cage", "SUBSURF")
    low_modifier.levels = 2
    applied, skipped = apply_preview(state, pair, True)
    assert applied == 2 and skipped == 1
    assert any(mod.name.startswith("Bake Tools Smooth Preview") for mod in ordinary_hp.modifiers)
    assert any(mod.name.startswith("Bake Tools Smooth Preview") for mod in stale_registry_hp.modifiers)
    assert not any(mod.name.startswith("Bake Tools Smooth Preview") for mod in marked_hp.modifiers)

    clear_preview(state)
    temporary = _temporary_export_smoothing(
        (ordinary_hp, marked_hp, stale_registry_hp), (pair,), state
    )
    assert [obj for obj, _modifier in temporary] == [ordinary_hp, stale_registry_hp]
    assert not marked_hp.modifiers
    _remove_temporary_modifiers(temporary)

    cages = create_cages(bpy.context, state, pair)
    assert len(cages) == 1 and cages[0].get(CAGE_MARKER) is True
    assert len(cages[0].data.vertices) == len(low_source_coords)
    assert all(
        (vertex.co - source_co).length < 1.0e-9
        for vertex, source_co in zip(cages[0].data.vertices, low_source_coords)
    )
    assert not cages[0].modifiers
    assert all(
        (cages[0].matrix_world @ vertex.co - source_world).length < 1.0e-8
        for vertex, source_world in zip(cages[0].data.vertices, low_source_world)
    )
    chapter_objects = (ordinary_hp, marked_hp, stale_registry_hp, low)
    bbox_points = [obj.matrix_world @ Vector(corner) for obj in chapter_objects for corner in obj.bound_box]
    bbox_low = Vector(tuple(min(point[axis] for point in bbox_points) for axis in range(3)))
    bbox_high = Vector(tuple(max(point[axis] for point in bbox_points) for axis in range(3)))
    expected_diagonal = (bbox_high - bbox_low).length
    assert abs(_chapter_diagonal(pair) - expected_diagonal) < 1.0e-7
    assert abs(resolve_delta(state, pair, 1.0) - expected_diagonal * 0.01) < 1.0e-7

    state.final_view = True
    pair.cage_visible = False
    sync_visibility(state, pair)
    assert cages[0].hide_viewport
    pair.cage_visible = True
    sync_visibility(state, pair)
    assert not cages[0].hide_viewport
    original = tuple(vertex.co.copy() for vertex in cages[0].data.vertices)
    expand_cages(state, pair, 1.0)
    assert any((vertex.co - before).length > 1.0e-6 for vertex, before in zip(cages[0].data.vertices, original))
    expand_cages(state, pair, -1.0)
    assert all((vertex.co - before).length < 1.0e-5 for vertex, before in zip(cages[0].data.vertices, original))
    assert find_intersections(bpy.context, state, pair) > 0
    assert delete_cages(state, pair) == 1

    payload = json.dumps({"subgroups": [subgroup.item_id]})
    state.final_view = True
    assert "FINISHED" in bpy.ops.bake_tools.action(action="CAGE_CREATE", value=payload)
    assert "FINISHED" in bpy.ops.bake_tools.action(action="CAGE_SCULPT", value=payload)
    assert bpy.context.object.mode == "SCULPT"
    assert "FINISHED" in bpy.ops.bake_tools.action(
        action="CAGE_EXPANSION",
        value=json.dumps({"subgroups": [subgroup.item_id], "delta": 0.25}),
    )
    assert bpy.context.object.mode == "OBJECT"
    state.export_directory = str(Path(bpy.app.tempdir) / "BakeToolsCageSmoke")
    assert "FINISHED" in bpy.ops.bake_tools.action(action="CAGE_EXPORT", value=payload)
    assert (Path(state.export_directory) / "Chapter_Cage.fbx").is_file()
    assert "FINISHED" in bpy.ops.bake_tools.action(action="CAGE_DELETE", value=payload)

    addon.unregister()
    print("BAKE_TOOLS_CAGE_SMOOTH_OK explicit_zbrush_skipped=1 named_group_smoothed=2 cage_raw_copy=1 cage_reversible=1 chapter_bbox=1 cage_visibility=1")


if __name__ == "__main__":
    main()
