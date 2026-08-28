"""Headless regression test for Maya-style LP material chapter creation."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy


ADDON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_ROOT.parent))

import Bake_Tools_Blender as addon  # noqa: E402
from Bake_Tools_Blender.addon.bake_tools_blender.material_distribution import (  # noqa: E402
    inspect_picked_lp,
)
from Bake_Tools_Blender.addon.bake_tools_blender.analysis_adapter import (  # noqa: E402
    _snapshot_material_regions,
)
from Bake_Tools_Blender.addon.bake_tools_blender.object_repository import (  # noqa: E402
    ObjectRepository,
)


def empty(name):
    obj = bpy.data.objects.new(name, None)
    bpy.context.collection.objects.link(obj)
    return obj


def cube(name, location, parent, material):
    bpy.ops.mesh.primitive_cube_add(location=location)
    obj = bpy.context.active_object
    obj.name = name
    obj.parent = parent
    obj.data.materials.append(material)
    for polygon in obj.data.polygons:
        polygon.material_index = 0
    return obj


def main():
    addon.register()
    state = bpy.context.scene.bake_tools_settings
    red = bpy.data.materials.new("T_Red_M")
    blue = bpy.data.materials.new("M_Blue_MAT")
    hp_root = empty("Asset_HP")
    lp_root = empty("Asset_LP")
    hp_red = cube("HP_Red", (-3.0, 0.0, 0.0), hp_root, red)
    hp_blue = cube("HP_Blue", (3.0, 0.0, 0.0), hp_root, blue)
    lp_red = cube("LP_Red", (-3.0, 0.0, 0.0), lp_root, red)
    lp_blue = cube("LP_Blue", (3.0, 0.0, 0.0), lp_root, blue)

    state.hp_root_kind = "OBJECT"
    state.lp_root_kind = "OBJECT"
    state.hp_root = hp_root
    state.lp_root = lp_root
    state.hp_object = hp_root.name
    state.lp_object = lp_root.name

    summary = inspect_picked_lp(state)
    assert summary.count == 2
    assert summary.names == ("M_Blue_MAT", "T_Red_M")

    assert "FINISHED" in bpy.ops.bake_tools.create_pairs_by_material()
    assert len(state.pairs) == 2
    assert {pair.name for pair in state.pairs} == {"Blue", "Red"}
    assert {pair.book for pair in state.pairs} == {"Book_01"}
    assert all(pair.scope_by_members for pair in state.pairs)
    scope = {
        pair.name: (
            {obj.name for obj in ObjectRepository.meshes_under_root(pair, "HP")},
            {obj.name for obj in ObjectRepository.meshes_under_root(pair, "LP")},
        )
        for pair in state.pairs
    }
    assert scope["Red"] == ({hp_red.name}, {lp_red.name})
    assert scope["Blue"] == ({hp_blue.name}, {lp_blue.name})

    state.pairs.clear()
    state.active_pair_id = ""
    state.hp_root_kind = "OBJECT"
    state.lp_root_kind = "OBJECT"
    state.hp_root = hp_root
    state.lp_root = lp_root
    state.hp_object = hp_root.name
    state.lp_object = lp_root.name
    assert "FINISHED" in bpy.ops.bake_tools.create_pair(
        hp_base="Asset", lp_base="Asset", name_choice="CUSTOM",
        custom_name="Asset", material_slots=True,
    )
    assert len(state.pairs) == 1
    assert state.pairs[0].material_slots is True
    assert state.pairs[0].scope_by_members is False
    assert state.pairs[0].book == ""

    # One Blender mesh with polygon-level material assignments becomes two
    # virtual LP regions for the one-chapter material-aware Analyze path.
    multi = cube("LP_Multi", (0.0, 4.0, 0.0), lp_root, red)
    multi.data.materials.append(blue)
    for polygon in multi.data.polygons[len(multi.data.polygons) // 2:]:
        polygon.material_index = 1
    regions = _snapshot_material_regions(multi, bpy.context.evaluated_depsgraph_get(), 4096)
    assert len(regions) == 2
    assert {region.face_count for region in regions} == {3}

    addon.unregister()
    print("BAKE_TOOLS_MATERIAL_DISTRIBUTION_OK")


if __name__ == "__main__":
    main()
