"""Verify Maya final HP/LP naming on entry to Export Settings."""

from __future__ import annotations

from uuid import uuid4

import addon_utils
import bpy


addon_utils.enable("Bake_Tools_Blender", default_set=False, persistent=False)


def empty(name):
    obj = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def mesh(name, parent):
    data = bpy.data.meshes.new(name + "_Mesh")
    data.from_pydata(((0, 0, 0), (1, 0, 0), (0, 1, 0)), (), ((0, 1, 2),))
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    obj.parent = parent
    return obj


def add_ref(refs, obj):
    ref = refs.add(); ref.target = obj; ref.last_name = obj.name


for obj in tuple(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)

state = bpy.context.scene.bake_tools_settings
pair = state.pairs.add(); pair.item_id = uuid4().hex; pair.name = "Gun.01"
pair.hp_root = empty("Gun_HP"); pair.hp_root_kind = "OBJECT"
pair.lp_root = empty("Gun_LP"); pair.lp_root_kind = "OBJECT"
subgroup = pair.subgroups.add(); subgroup.item_id = uuid4().hex; subgroup.name = "Large.02"

hp_z = mesh("Zeta_HP", pair.hp_root); hp_a = mesh("Alpha_HP", pair.hp_root)
lp_z = mesh("Zeta_LP", pair.lp_root); lp_a = mesh("Alpha_LP", pair.lp_root)
for obj in (hp_z, hp_a): add_ref(subgroup.hp_members, obj)
for obj in (lp_z, lp_a): add_ref(subgroup.lp_members, obj)
state.active_pair = 0; state.active_pair_id = pair.item_id

cage = mesh("Existing_Cage", None)
cage["bake_tools_cage"] = True; cage["bake_tools_pair_id"] = pair.item_id
cage["bake_tools_cage_source"] = lp_a.name

assert "FINISHED" in bpy.ops.bake_tools.action(action="EXPORT_SETTINGS")
assert state.final_view
assert {hp_a.name, hp_z.name} == {
    "Gun_01_Large_02_high_001", "Gun_01_Large_02_high_002",
}
assert {lp_a.name, lp_z.name} == {
    "Gun_01_Large_02_low_001", "Gun_01_Large_02_low_002",
}
assert hp_a.data.name == hp_a.name and lp_a.data.name == lp_a.name
assert cage["bake_tools_cage_source"] == lp_a.name
assert all(ref.target is not None and ref.last_name == ref.target.name for ref in subgroup.hp_members)
assert all(ref.target is not None and ref.last_name == ref.target.name for ref in subgroup.lp_members)

# Re-entering is idempotent and verifies both sides again without creating
# Blender's automatic .001 suffixes.
first_names = {obj.as_pointer(): obj.name for obj in (hp_a, hp_z, lp_a, lp_z)}
assert "FINISHED" in bpy.ops.bake_tools.action(action="EXPORT_SETTINGS")
assert not state.final_view
assert "FINISHED" in bpy.ops.bake_tools.action(action="EXPORT_SETTINGS")
assert state.final_view
assert first_names == {obj.as_pointer(): obj.name for obj in (hp_a, hp_z, lp_a, lp_z)}
assert "; finalized naming: HP 2, LP 2, renamed 0" in state.log_text
print("BAKE_TOOLS_EXPORT_NAMING_OK hp=2 lp=2 persistent=1 cage_link=1")
