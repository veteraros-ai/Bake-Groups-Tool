"""Regression for topology-only ZBrush, transform freeze and Cage placement."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from uuid import uuid4

import bpy


ADDON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_ROOT.parent))
import Bake_Tools_Blender as addon  # noqa: E402


def mesh_object(name, vertices, faces, parent):
    mesh = bpy.data.meshes.new(name + "_mesh")
    mesh.from_pydata(vertices, [], faces); mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.parent = parent
    return obj


def world_points(obj):
    return tuple(obj.matrix_world @ vertex.co for vertex in obj.data.vertices)


def identity_basis(obj, tolerance=1.0e-6):
    return all(
        abs(float(obj.matrix_basis[row][column]) - (1.0 if row == column else 0.0)) <= tolerance
        for row in range(4) for column in range(4)
    )


def frozen_at_world_origin(obj, tolerance=1.0e-6):
    return (
        identity_basis(obj, tolerance)
        and all(
            abs(float(obj.matrix_world[row][column]) - (1.0 if row == column else 0.0)) <= tolerance
            for row in range(4) for column in range(4)
        )
        and obj.matrix_world.translation.length <= tolerance
        and obj.location.length <= tolerance
        and obj.rotation_euler.to_matrix().is_identity
        and all(abs(float(value) - 1.0) <= tolerance for value in obj.scale)
    )


def main():
    addon.register()
    from Bake_Tools_Blender.addon.bake_tools_blender.cage_service import create_cages
    from Bake_Tools_Blender.addon.bake_tools_blender.mesh_tools import (
        apply_check_transforms, check_active_pair, encode_check_payload,
        find_zbrush_candidates,
    )

    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    state = bpy.context.scene.bake_tools_settings
    hp_root = bpy.data.objects.new("HP_Root", None); bpy.context.collection.objects.link(hp_root)
    lp_root = bpy.data.objects.new("LP_Root", None); bpy.context.collection.objects.link(lp_root)
    hp_root.location = (3.0, -2.0, 1.0); hp_root.rotation_euler.z = math.radians(12)
    lp_root.location = (-4.0, 1.0, 2.0); lp_root.scale = (1.25, 0.8, 1.1)

    named_quad = mesh_object(
        "ZBrush_Name_Only", [(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)],
        [(0, 1, 2, 3)], hp_root,
    )
    topology_tri = mesh_object(
        "Ordinary_Triangles", [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)],
        [(0, 1, 2), (0, 3, 1), (1, 3, 2), (2, 3, 0)], hp_root,
    )
    inherited_only = mesh_object(
        "Inherited_Only", [(0, 0, 0), (0.5, 0, 0), (0.5, 0.5, 0), (0, 0.5, 0)],
        [(0, 1, 2, 3)], hp_root,
    )
    low = mesh_object(
        "LP", [(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
               (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)],
        [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2),
         (2, 6, 7, 3), (4, 0, 3, 7)], lp_root,
    )
    named_quad.location = (2.0, 0.5, -0.25); named_quad.scale = (0.7, 1.4, 1.0)
    topology_tri.rotation_euler.x = math.radians(23); topology_tri.location = (-1.0, 2.0, 0.0)
    low.location = (1.5, -0.5, 0.75); low.rotation_euler.y = math.radians(31)

    pair = state.pairs.add(); pair.item_id = uuid4().hex; pair.name = "Chapter"
    pair.hp_root = hp_root; pair.hp_object = hp_root.name; pair.hp_root_kind = "OBJECT"
    pair.lp_root = lp_root; pair.lp_object = lp_root.name; pair.lp_root_kind = "OBJECT"
    subgroup = pair.subgroups.add(); subgroup.item_id = uuid4().hex; subgroup.name = "ZBrush_Huge_001"
    ref = subgroup.hp_members.add(); ref.target = named_quad; ref.last_name = named_quad.name
    ref = subgroup.hp_members.add(); ref.target = topology_tri; ref.last_name = topology_tri.name
    ref = subgroup.hp_members.add(); ref.target = inherited_only; ref.last_name = inherited_only.name
    ref = subgroup.lp_members.add(); ref.target = low; ref.last_name = low.name
    state.active_pair = 0; state.active_pair_id = pair.item_id

    found, best = find_zbrush_candidates(bpy.context, state, pair)
    assert found == [topology_tri] and best == 100.0

    before = {
        obj.name: world_points(obj)
        for obj in (named_quad, topology_tri, inherited_only, low)
    }
    root_world_before = {
        hp_root: hp_root.matrix_world.copy(),
        lp_root: lp_root.matrix_world.copy(),
    }
    result = check_active_pair(bpy.context, state, pair)
    assert set(result.transform_objects) == {named_quad, topology_tri, inherited_only, low}
    state.mesh_check_payload = encode_check_payload(result, pair)
    fixed, skipped = apply_check_transforms(bpy.context, state, pair)
    assert not skipped and len(fixed) == 4
    not_frozen = [obj for obj in fixed if not frozen_at_world_origin(obj)]
    assert not not_frozen, [
        (
            obj.name, tuple(obj.location), tuple(obj.rotation_euler), tuple(obj.scale),
            tuple(obj.matrix_world.translation), tuple(obj.delta_location), tuple(obj.delta_scale),
        )
        for obj in not_frozen
    ]
    for obj in (named_quad, topology_tri, inherited_only, low):
        distances = tuple((a - b).length for a, b in zip(world_points(obj), before[obj.name]))
        assert all(distance < 1.0e-6 for distance in distances), (obj.name, max(distances))
    assert all(
        all(abs(float(root.matrix_world[row][column] - original[row][column])) < 1.0e-7
            for row in range(4) for column in range(4))
        for root, original in root_world_before.items()
    )

    cages = create_cages(bpy.context, state, pair)
    assert len(cages) == 1
    assert all((a - b).length < 1.0e-6 for a, b in zip(world_points(cages[0]), world_points(low)))

    assert subgroup.smooth_level == 1
    payload = json.dumps({"subgroups": [subgroup.item_id], "mode": "UP", "level": 0})
    assert "FINISHED" in bpy.ops.bake_tools.action(action="SUBGROUP_SMOOTH_BATCH", value=payload)
    assert subgroup.smooth_level == 2
    addon.unregister()
    print("BAKE_TOOLS_TRANSFORM_ZBRUSH_CAGE_OK topology_only=1 freeze_preserves_world=1 pivot_world_zero=1 inherited_transform=1 roots_unchanged=1 cage_origin=1 batch_smooth=1")


if __name__ == "__main__":
    main()
