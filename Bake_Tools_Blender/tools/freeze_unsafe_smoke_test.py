"""Freeze must preserve ordinary meshes and skip constrained evaluation."""

import json

import addon_utils
import bpy
from mathutils import Vector


addon_utils.enable("Bake_Tools_Blender", default_set=False, persistent=False)
from Bake_Tools_Blender.addon.bake_tools_blender.mesh_tools import apply_check_transforms


state = bpy.context.scene.bake_tools_settings
for obj in tuple(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
root = bpy.data.objects.new("HP_Root", None)
bpy.context.scene.collection.objects.link(root)

objects = []
for name, position in (("Ordinary_HP", (1, 2, 3)), ("Constrained_HP", (4, 5, 6))):
    bpy.ops.mesh.primitive_cube_add(location=position)
    obj = bpy.context.object
    obj.name = name
    obj.parent = root
    objects.append(obj)
ordinary, constrained = objects
constraint = constrained.constraints.new("COPY_LOCATION")
constraint.target = root
bpy.context.view_layer.update()
before = tuple(ordinary.matrix_world @ vertex.co for vertex in ordinary.data.vertices)
constrained_before = constrained.matrix_world.copy()

pair = state.pairs.add()
pair.item_id = "freeze-unsafe-test"
pair.name = "Freeze Test"
pair.hp_root = root
state.mesh_check_payload = json.dumps({
    "pair_id": pair.item_id, "transforms": [ordinary.name, constrained.name]
})
fixed, skipped = apply_check_transforms(bpy.context, state, pair)
assert fixed == (ordinary,) and skipped == (constrained,), (fixed, skipped)
after = tuple(ordinary.matrix_world @ vertex.co for vertex in ordinary.data.vertices)
assert all((left - right).length < 1e-6 for left, right in zip(before, after))
assert all(abs(constrained.matrix_world[row][column] - constrained_before[row][column]) < 1e-6
           for row in range(4) for column in range(4))
print("FREEZE_UNSAFE_PASS")
