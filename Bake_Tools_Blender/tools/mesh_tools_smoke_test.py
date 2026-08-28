"""Regression coverage for Combine, Separate, ZBrush layer and Mesh Check."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import bpy


ADDON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_ROOT.parent))

import Bake_Tools_Blender as addon  # noqa: E402


def empty(name):
    obj = bpy.data.objects.new(name, None)
    bpy.context.collection.objects.link(obj)
    return obj


def mesh_object(name, vertices, faces, parent):
    mesh = bpy.data.meshes.new(name + "_Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.parent = parent
    return obj


def cube(name, center, parent):
    x, y, z = center
    vertices = [
        (x + dx, y + dy, z + dz)
        for dx, dy, dz in (
            (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
            (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
        )
    ]
    faces = [
        (0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1),
        (1, 5, 6, 2), (2, 6, 7, 3), (4, 0, 3, 7),
    ]
    return mesh_object(name, vertices, faces, parent)


def two_loose_cubes(name, parent):
    first = cube(name + "_A_TMP", (0, 0, 0), parent)
    second = cube(name + "_B_TMP", (6, 0, 0), parent)
    vertices = [tuple(vertex.co) for vertex in first.data.vertices]
    vertices += [tuple(vertex.co) for vertex in second.data.vertices]
    faces = [tuple(poly.vertices) for poly in first.data.polygons]
    faces += [tuple(index + 8 for index in poly.vertices) for poly in second.data.polygons]
    result = mesh_object(name, vertices, faces, parent)
    bpy.data.objects.remove(first, do_unlink=True)
    bpy.data.objects.remove(second, do_unlink=True)
    return result


def tetra(name, center, parent):
    x, y, z = center
    vertices = [
        (x, y, z + 1), (x - 1, y - 1, z - 1),
        (x + 1, y - 1, z - 1), (x, y + 1, z - 1),
    ]
    faces = [(0, 1, 2), (0, 2, 3), (0, 3, 1), (1, 3, 2)]
    return mesh_object(name, vertices, faces, parent)


def select_only(*objects):
    for obj in tuple(bpy.context.selected_objects):
        obj.select_set(False)
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0] if objects else None


def main():
    addon.register()
    hp_root = empty("Tools_HP")
    lp_root = empty("Tools_LP")
    duplicate_a = cube("Duplicate_A", (0, 0, 0), hp_root)
    duplicate_b = cube("Duplicate_B", (0, 0, 0), hp_root)
    # 0.0002 BU is a distinct 0.2 mm offset in a default metric Blender scene.
    # The old fixed 0.001 BU digest incorrectly grouped this with Duplicate_A/B.
    near_not_duplicate = cube("Near_Not_Duplicate", (0.0002, 0, 0), hp_root)
    loose = two_loose_cubes("Loose_Combined", hp_root)
    zbrush = tetra("Sculpt_ZBrush_Candidate", (12, 0, 0), hp_root)
    zbrush_named_quad = cube("Tools_ZBrush_Huge_001_high_001", (16, 0, 0), hp_root)
    cube("LP_Reference", (0, 0, 0), lp_root)

    select_only(hp_root)
    assert "FINISHED" in bpy.ops.bake_tools.pick_object(role="HP")
    select_only(lp_root)
    assert "FINISHED" in bpy.ops.bake_tools.pick_object(role="LP")
    assert "FINISHED" in bpy.ops.bake_tools.create_pair(
        name_choice="CUSTOM", custom_name="Tools"
    )
    state = bpy.context.scene.bake_tools_settings
    assert state.hp_root is None and state.lp_root is None
    assert state.hp_object == "" and state.lp_object == ""

    assert "FINISHED" in bpy.ops.bake_tools.action(action="CHECK")
    assert "Duplicates: 1 group(s)" in state.mesh_check_report
    assert "Meshes with independent loose parts: 1" in state.mesh_check_report
    assert "Possible ZBrush meshes outside BakeTools layer: 1" in state.mesh_check_report
    payload = json.loads(state.mesh_check_payload)
    assert payload["pair_id"] == state.active_pair_id
    assert len(payload["duplicates"]) == 1
    assert set(payload["duplicates"][0]) == {duplicate_a.name, duplicate_b.name}
    assert all(near_not_duplicate.name not in group for group in payload["duplicates"])
    # Automatic ZBrush detection is topology-only: a ZBrush-like name on a
    # quad mesh must not turn it into a candidate.
    assert payload["zbrush"] == [zbrush.name]
    assert payload["combined"] == [loose.name]

    assert "FINISHED" in bpy.ops.bake_tools.action(action="CHECK_SELECT_DUPLICATES")
    assert set(bpy.context.selected_objects) == {duplicate_a, duplicate_b}

    assert "FINISHED" in bpy.ops.bake_tools.action(action="CHECK_ADD_ZBRUSH")
    assert {ref.target for ref in state.zbrush_members} == {zbrush}
    layer = bpy.data.collections.get("BakeTools_ZBrush_Layer")
    assert layer is not None and zbrush.name in layer.objects
    assert zbrush_named_quad.name not in layer.objects

    assert "FINISHED" in bpy.ops.bake_tools.action(action="CHECK_SEPARATE_COMBINED")
    checked_parts = [
        obj for obj in bpy.context.selected_objects
        if obj.name.startswith("Loose_Combined_Part")
    ]
    assert len(checked_parts) == 2

    assert "FINISHED" in bpy.ops.bake_tools.action(action="CHECK_REMOVE_DUPLICATES")
    assert sum(bpy.data.objects.get(name) is not None for name in ("Duplicate_A", "Duplicate_B")) == 1

    # Manual layer membership remains available independently of the
    # topology detector.
    select_only(zbrush_named_quad)
    assert "FINISHED" in bpy.ops.bake_tools.action(action="ZBRUSH_ADD_SELECTED")
    assert {ref.target for ref in state.zbrush_members} == {zbrush, zbrush_named_quad}
    select_only()
    assert "FINISHED" in bpy.ops.bake_tools.action(action="ZBRUSH_SELECT_LAYER")
    assert set(bpy.context.selected_objects) == {zbrush, zbrush_named_quad}

    select_only()
    assert "FINISHED" in bpy.ops.bake_tools.action(action="FIND_ZBRUSH")
    assert set(bpy.context.selected_objects) == {zbrush}

    combine_a = cube("Combine_A", (20, 0, 0), hp_root)
    combine_b = cube("Combine_B", (24, 0, 0), hp_root)
    select_only(combine_a, combine_b)
    assert "FINISHED" in bpy.ops.bake_tools.action(action="COMBINE")
    combined = bpy.context.view_layer.objects.active
    assert combined is not None and combined.name.startswith("Combine_A_Combined")
    assert bpy.data.objects.get("Combine_B") is None

    loose_manual = two_loose_cubes("Loose_Manual", hp_root)
    select_only(loose_manual)
    assert "FINISHED" in bpy.ops.bake_tools.action(action="SEPARATE")
    parts = [
        obj for obj in bpy.context.selected_objects
        if obj.name.startswith("Loose_Manual_Part")
    ]
    assert len(parts) == 2

    addon.unregister()
    print("BAKE_TOOLS_MESH_TOOLS_OK")


if __name__ == "__main__":
    main()
