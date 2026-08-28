"""Regression coverage for two-stage, shape-aware Find Sim / Find All."""

from __future__ import annotations

from math import radians
from uuid import uuid4

import addon_utils
import bpy


addon_utils.enable("Bake_Tools_Blender", default_set=False, persistent=False)

from Bake_Tools_Blender.addon.bake_tools_blender import find_similar as find_similar_module  # noqa: E402


CUBE_VERTICES = (
    (-.5, -.5, -.5), (.5, -.5, -.5), (.5, .5, -.5), (-.5, .5, -.5),
    (-.5, -.5, .5), (.5, -.5, .5), (.5, .5, .5), (-.5, .5, .5),
)
CUBE_FACES = (
    (0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1),
    (1, 5, 6, 2), (2, 6, 7, 3), (4, 0, 3, 7),
)
PYRAMID_VERTICES = (
    (-.5, -.5, 0), (.5, -.5, 0), (.5, .5, 0), (-.5, .5, 0), (0, 0, 1),
)
PYRAMID_FACES = (
    (0, 1, 2, 3), (0, 4, 1), (1, 4, 2), (2, 4, 3), (3, 0, 4),
)


def mesh_object(name, vertices, faces, location, parent):
    mesh = bpy.data.meshes.new(name + "_Mesh")
    mesh.from_pydata(vertices, (), faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.parent = parent
    obj.location = location
    return obj


def select(*objects):
    for obj in tuple(bpy.context.selected_objects):
        obj.select_set(False)
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0] if objects else None


hp_root = bpy.data.objects.new("FindSim_HP", None)
lp_root = bpy.data.objects.new("FindSim_LP", None)
bpy.context.scene.collection.objects.link(hp_root)
bpy.context.scene.collection.objects.link(lp_root)

a1 = mesh_object("A_01", CUBE_VERTICES, CUBE_FACES, (0, 0, 0), hp_root)
b1 = mesh_object("B_01", PYRAMID_VERTICES, PYRAMID_FACES, (2, 0, 0), hp_root)
a2 = mesh_object("A_02", CUBE_VERTICES, CUBE_FACES, (10, 0, 0), hp_root)
b2 = mesh_object("B_02", PYRAMID_VERTICES, PYRAMID_FACES, (12, 0, 0), hp_root)
a3 = mesh_object("A_BadLayout", CUBE_VERTICES, CUBE_FACES, (20, 0, 0), hp_root)
b3 = mesh_object("B_BadLayout", PYRAMID_VERTICES, PYRAMID_FACES, (22.2, 0, 0), hp_root)

# Same vertex count and comparable bbox, but a different topology.
unrelated_vertices = (
    (-.5, -.5, 0), (.5, -.5, 0), (.5, .5, 0), (-.5, .5, 0),
    (0, 0, 1), (0, 0, 2), (0, 0, 3), (0, 0, 4),
)
unrelated_faces = ((0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4), (4, 5, 6, 7))
topology_impostor = mesh_object(
    "TopologyImpostor", unrelated_vertices, unrelated_faces, (40, 0, 0), hp_root
)

# Same topology counts, but non-uniformly distorted geometry.
stretched_vertices = tuple((x * 2.0, y, z) for x, y, z in CUBE_VERTICES)
shape_impostor = mesh_object(
    "ShapeImpostor", stretched_vertices, CUBE_FACES, (50, 0, 0), hp_root
)

# Raw mesh matches A, while the evaluated modifier result does not.
modifier_impostor = mesh_object(
    "ModifierImpostor", CUBE_VERTICES, CUBE_FACES, (60, 0, 0), hp_root
)
subdivision = modifier_impostor.modifiers.new("Evaluated Subdivision", "SUBSURF")
subdivision.levels = 2

# Object transforms may rotate and uniformly scale a genuine repeated part.
transformed_copy = mesh_object(
    "TransformedCopy", CUBE_VERTICES, CUBE_FACES, (70, 0, 0), hp_root
)
transformed_copy.rotation_euler.z = radians(37.0)
transformed_copy.scale = (2.0, 2.0, 2.0)

lp = mesh_object("FindSim_Low", CUBE_VERTICES, CUBE_FACES, (0, 0, 0), lp_root)

state = bpy.context.scene.bake_tools_settings
pair = state.pairs.add()
pair.item_id = uuid4().hex
pair.name = "FindSim"
pair.hp_root = hp_root
pair.hp_object = hp_root.name
pair.hp_root_kind = "OBJECT"
pair.lp_root = lp_root
pair.lp_object = lp_root.name
pair.lp_root_kind = "OBJECT"
state.active_pair = 0
state.active_pair_id = pair.item_id

select(a1)
evaluated_names = []
original_evaluated_signature = find_similar_module._evaluated_signature


def counted_evaluated_signature(obj, depsgraph):
    evaluated_names.append(obj.name)
    return original_evaluated_signature(obj, depsgraph)


find_similar_module._evaluated_signature = counted_evaluated_signature
try:
    found, side = find_similar_module.find_similar(bpy.context, state, pair, "ALL")
finally:
    find_similar_module._evaluated_signature = original_evaluated_signature
found_names = {obj.name for obj in found}
assert side == "HP"
assert {"A_01", "A_02", "A_BadLayout", "TransformedCopy"} <= found_names
assert "TopologyImpostor" not in found_names
assert "ShapeImpostor" not in found_names
assert "ModifierImpostor" not in found_names
assert "B_01" not in found_names
assert "B_01" not in evaluated_names
assert "B_02" not in evaluated_names
assert "B_BadLayout" not in evaluated_names
assert len(evaluated_names) < len(tuple(obj for obj in hp_root.children if obj.type == "MESH"))

select(a1, b1)
found, side = find_similar_module.find_similar(bpy.context, state, pair, "SIM")
found_names = {obj.name for obj in found}
assert {"A_01", "B_01", "A_02", "B_02"} <= found_names
assert "A_BadLayout" not in found_names
assert "B_BadLayout" not in found_names

# Selecting a hierarchy container must not silently expand it into every child.
select(hp_root)
try:
    find_similar_module.find_similar(bpy.context, state, pair, "SIM")
except ValueError as exc:
    assert "No meshes" in str(exc)
else:
    raise AssertionError("Find Sim accepted a selected Empty as a mesh target")

print(
    "BAKE_TOOLS_FIND_SIMILAR_REGRESSION_OK "
    "shape_false_positives=0 evaluated_modifiers=1 direct_selection=1 "
    "fast_prefilter=1 evaluated_shortlist={}".format(len(evaluated_names))
)
