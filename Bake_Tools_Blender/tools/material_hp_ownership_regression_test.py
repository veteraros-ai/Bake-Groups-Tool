"""Regression fixture for Maya-parity Create-by-Material HP ownership.

The red LP object has two disconnected islands whose combined object BBox spans
the blue LP.  The old Blender implementation treated that empty space as red
geometry and could assign the centre HP to the wrong chapter.  Maya builds LP
island proxies, audits ownership, and leaves genuinely remote HP for review.
"""

from __future__ import annotations

import sys
from pathlib import Path

import bpy


ADDON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_ROOT.parent))

import Bake_Tools_Blender as addon  # noqa: E402
from Bake_Tools_Blender.addon.bake_tools_blender.object_repository import ObjectRepository  # noqa: E402


_CUBE_VERTICES = (
    (-1, -1, -1), (-1, -1, 1), (-1, 1, -1), (-1, 1, 1),
    (1, -1, -1), (1, -1, 1), (1, 1, -1), (1, 1, 1),
)
_CUBE_FACES = (
    (0, 4, 6, 2), (1, 3, 7, 5), (0, 1, 5, 4),
    (2, 6, 7, 3), (0, 2, 3, 1), (4, 5, 7, 6),
)


def empty(name):
    obj = bpy.data.objects.new(name, None)
    bpy.context.collection.objects.link(obj)
    return obj


def disconnected_cubes(name, centers, parent, material):
    vertices, faces = [], []
    for center in centers:
        offset = len(vertices)
        vertices.extend(tuple(center[axis] + vertex[axis] for axis in range(3)) for vertex in _CUBE_VERTICES)
        faces.extend(tuple(offset + index for index in face) for face in _CUBE_FACES)
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(vertices, (), faces)
    mesh.materials.append(material)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.parent = parent
    return obj


def cube(name, location, parent):
    bpy.ops.mesh.primitive_cube_add(location=location)
    obj = bpy.context.active_object
    obj.name = name
    obj.parent = parent
    return obj


def main():
    addon.register()
    state = bpy.context.scene.bake_tools_settings
    red = bpy.data.materials.new("A_Red")
    blue = bpy.data.materials.new("Z_Blue")
    hp_root, lp_root = empty("Asset_HP"), empty("Asset_LP")

    red_lp = disconnected_cubes("LP_Red_Islands", ((-10, 0, 0), (10, 0, 0)), lp_root, red)
    blue_lp = disconnected_cubes("LP_Blue", ((0, 0, 0),), lp_root, blue)
    hp_left = cube("HP_Left", (-10, 0, 0), hp_root)
    hp_center = cube("HP_Center", (0, 0, 0), hp_root)
    hp_right = cube("HP_Right", (10, 0, 0), hp_root)
    hp_far = cube("HP_Far", (100, 0, 0), hp_root)

    state.hp_root_kind = state.lp_root_kind = "OBJECT"
    state.hp_root, state.lp_root = hp_root, lp_root
    state.hp_object, state.lp_object = hp_root.name, lp_root.name

    assert "FINISHED" in bpy.ops.bake_tools.create_pairs_by_material()
    assert {pair.name for pair in state.pairs} == {"A_Red", "Z_Blue", "Review_Unmatched"}
    scope = {
        pair.name: (
            {obj.name for obj in ObjectRepository.meshes_under_root(pair, "HP")},
            {obj.name for obj in ObjectRepository.meshes_under_root(pair, "LP")},
        )
        for pair in state.pairs
    }
    assert scope["A_Red"] == ({hp_left.name, hp_right.name}, {red_lp.name})
    assert scope["Z_Blue"] == ({hp_center.name}, {blue_lp.name})
    assert scope["Review_Unmatched"] == ({hp_far.name}, set())
    assert "built 3 LP match proxy region(s)" in state.log_text
    assert "review=1" in state.log_text

    addon.unregister()
    print("BAKE_TOOLS_MATERIAL_HP_OWNERSHIP_OK")


if __name__ == "__main__":
    main()
