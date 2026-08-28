"""Headless acceptance test for real Assign LP membership."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy


ADDON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_ROOT.parent))

import Bake_Tools_Blender as addon  # noqa: E402


def make_root(name):
    root = bpy.data.objects.new(name, None)
    bpy.context.collection.objects.link(root)
    return root


def make_cube(name, location, parent, scale=1.0):
    bpy.ops.mesh.primitive_cube_add(size=2.0 * scale, location=location)
    obj = bpy.context.active_object
    obj.name = name
    obj.parent = parent
    return obj


def select_only(*objects):
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0] if objects else None


def names(refs):
    return {ref.target.name for ref in refs if ref.target is not None}


def snapshot(key, x):
    from Bake_Tools_Blender.addon.bake_tools_blender.domain.analysis import MeshSnapshot

    vertices = tuple(
        (x + dx, dy, dz)
        for dx in (-1.0, 1.0) for dy in (-1.0, 1.0) for dz in (-1.0, 1.0)
    )
    return MeshSnapshot(
        key=key, name=key,
        bbox_min=(x - 1.0, -1.0, -1.0), bbox_max=(x + 1.0, 1.0, 1.0),
        center=(x, 0.0, 0.0), dimensions=(2.0, 2.0, 2.0),
        diagonal=12.0 ** 0.5, bbox_volume=8.0,
        vertex_count=8, edge_count=12, face_count=6, vertices=vertices,
    )


def main():
    addon.register()
    hp_root = make_root("Gun_HP")
    lp_root = make_root("Gun_LP")
    hp_left = make_cube("HP_Left", (-3.0, 0.0, 0.0), hp_root, 1.0)
    hp_right = make_cube("HP_Right", (3.0, 0.0, 0.0), hp_root, 1.0)
    hp_locked = make_cube("HP_Locked", (9.0, 0.0, 0.0), hp_root, 1.0)
    lp_left = make_cube("LP_Left", (-3.0, 0.0, 0.0), lp_root, 0.9)
    lp_right = make_cube("LP_Right", (3.0, 0.0, 0.0), lp_root, 0.9)
    lp_locked = make_cube("LP_Locked", (9.1, 0.0, 0.0), lp_root, 0.7)
    lp_unmatched = make_cube("LP_Unmatched", (30.0, 0.0, 0.0), lp_root, 0.5)

    select_only(hp_root)
    assert "FINISHED" in bpy.ops.bake_tools.pick_object(role="HP")
    select_only(lp_root)
    assert "FINISHED" in bpy.ops.bake_tools.pick_object(role="LP")
    assert "FINISHED" in bpy.ops.bake_tools.create_pair(name_choice="CUSTOM", custom_name="Gun")

    select_only(hp_left)
    assert "FINISHED" in bpy.ops.bake_tools.add_subgroup(name="Left_001")
    state = bpy.context.scene.bake_tools_settings
    pair = state.pairs[0]
    left = pair.subgroups[0]
    select_only(hp_right)
    assert "FINISHED" in bpy.ops.bake_tools.add_subgroup(name="Right_001")
    right = pair.subgroups[1]
    select_only(hp_locked)
    assert "FINISHED" in bpy.ops.bake_tools.add_subgroup(name="Locked_001")
    locked = pair.subgroups[2]

    # Seed deliberately wrong membership and a locked LP that must survive.
    select_only(lp_left)
    assert "FINISHED" in bpy.ops.bake_tools.subgroup_action(
        action="ADD_SELECTED", subgroup_id=right.item_id
    )
    select_only(lp_locked)
    assert "FINISHED" in bpy.ops.bake_tools.subgroup_action(
        action="ADD_SELECTED", subgroup_id=locked.item_id
    )
    locked.locked = True

    # Exercise the same dispatcher used by the PySide Assign LP button.
    assert "FINISHED" in bpy.ops.bake_tools.action(action="ASSIGN_LP")

    assert names(left.hp_members) == {"HP_Left"}
    assert names(right.hp_members) == {"HP_Right"}
    assert names(locked.hp_members) == {"HP_Locked"}
    assert names(left.lp_members) == {"LP_Left"}
    assert names(right.lp_members) == {"LP_Right"}
    assert names(locked.lp_members) == {"LP_Locked"}
    assert all(lp_unmatched.name not in names(group.lp_members) for group in pair.subgroups)
    assert "Assign LP: matched 2 of 3 mesh(es); unmatched 1; preserved locked 1" in state.log_text
    assert "FAST LP_Left -> Left_001" in state.debug_text
    assert "math backend=C++ 0.1.0 (Blender)" in state.debug_text
    assert "UNMATCHED LP_Unmatched" in state.debug_text
    first_membership = tuple((group.name, names(group.lp_members)) for group in pair.subgroups)
    assert "FINISHED" in bpy.ops.bake_tools.action(action="ASSIGN_LP")
    assert tuple((group.name, names(group.lp_members)) for group in pair.subgroups) == first_membership

    # Material order A/B maps to M01/M02 and repairs intentionally crossed
    # geometric matches, mirroring the Maya post-match material pass.
    from Bake_Tools_Blender.addon.bake_tools_blender.domain.lp_matching import (
        LPMatchGroup, LPMatchSettings,
    )
    from Bake_Tools_Blender.addon.bake_tools_blender.lp_matching_service import LPMatchingService

    material_result = LPMatchingService().match(
        (
            LPMatchGroup("M01_Left", (snapshot("HP_M01", 0.0),)),
            LPMatchGroup("M02_Right", (snapshot("HP_M02", 10.0),)),
        ),
        (snapshot("LP_A", 10.0), snapshot("LP_B", 0.0)),
        LPMatchSettings(),
        material_key_by_lp={"LP_A": "A", "LP_B": "B"},
    )
    material_assignments = {
        assignment.group_name: set(assignment.lp_keys)
        for assignment in material_result.assignments
    }
    assert material_assignments == {"M01_Left": {"LP_A"}, "M02_Right": {"LP_B"}}
    assert material_result.material_repairs == 2

    addon.unregister()
    print("BAKE_TOOLS_ASSIGN_LP_SMOKE_OK")


if __name__ == "__main__":
    main()
