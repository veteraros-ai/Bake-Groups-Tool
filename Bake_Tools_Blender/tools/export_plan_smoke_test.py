"""Verify Maya-compatible export scope/file/material planning without writing FBX."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import addon_utils
import bpy


addon_utils.enable("Bake_Tools_Blender", default_set=False, persistent=False)

from Bake_Tools_Blender.addon.bake_tools_blender.export_service import build_export_plan  # noqa: E402


for obj in tuple(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)


material = bpy.data.materials.new("MatA")


def empty(name):
    obj = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def mesh(name, parent, use_material=False):
    data = bpy.data.meshes.new(name + "_Mesh")
    data.from_pydata(((0, 0, 0), (1, 0, 0), (0, 1, 0)), (), ((0, 1, 2),))
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    obj.parent = parent
    if use_material:
        data.materials.append(material)
    return obj


state = bpy.context.scene.bake_tools_settings
for index in range(2):
    hp_root = empty("Part{}_HP".format(index + 1))
    lp_root = empty("Part{}_LP".format(index + 1))
    mesh("Part{}_High".format(index + 1), hp_root)
    mesh("Part{}_Low".format(index + 1), lp_root, True)
    pair = state.pairs.add()
    pair.item_id = uuid4().hex
    pair.name = "Part{}".format(index + 1)
    pair.book = "MatA"
    pair.hp_root = hp_root; pair.hp_root_kind = "OBJECT"; pair.hp_object = hp_root.name
    pair.lp_root = lp_root; pair.lp_root_kind = "OBJECT"; pair.lp_object = lp_root.name
    cage = mesh("Part{}_Cage".format(index + 1), None)
    cage["bake_tools_cage"] = True; cage["bake_tools_pair_id"] = pair.item_id

state.active_pair = 0; state.active_pair_id = state.pairs[0].item_id
state.export_directory = str(Path(bpy.app.tempdir) / "BakeToolsPlan")
state.export_scope = "BOOK"; state.export_include_hp = True
state.export_include_lp = True; state.export_include_cage = True
state.export_lp_one_file = False; state.export_files = "ONE"
assert state.export_lp_triangulate is True

plan = build_export_plan(state, state.pairs[0], state.export_directory)
assert plan.triangulate_lp is True
assert len(plan.tasks) == 4
assert {Path(task.filepath).name for task in plan.tasks} == {
    "Part1.fbx", "Part1_Cage.fbx", "Part2.fbx", "Part2_Cage.fbx",
}
for task in plan.tasks:
    if task.name.endswith("Cage"):
        assert len(task.objects) == 1 and not task.lp_objects
    else:
        assert len(task.objects) == 2 and len(task.lp_objects) == 1

# A book whose name is an LP material is merged to three side files, like Maya.
state.export_by_material = True
plan = build_export_plan(state, state.pairs[0], state.export_directory)
assert {Path(task.filepath).name for task in plan.tasks} == {
    "MatA_HP.fbx", "MatA_LP.fbx", "MatA_Cage.fbx",
}
assert sorted(len(task.objects) for task in plan.tasks) == [2, 2, 2]

# A regular container book keeps chapters separate in By Material mode.
for pair in state.pairs:
    pair.book = "Book_01"
plan = build_export_plan(state, state.pairs[0], state.export_directory)
assert len(plan.tasks) == 6
assert "Part1_HP.fbx" in {Path(task.filepath).name for task in plan.tasks}

# LP-one-file is extracted from the chapter loop; HP and cage remain separate.
state.export_by_material = False; state.export_lp_one_file = True
plan = build_export_plan(state, state.pairs[0], state.export_directory)
names = {Path(task.filepath).name for task in plan.tasks}
assert names == {"Book_01_LP.fbx", "Part1_HP.fbx", "Part1_Cage.fbx", "Part2_HP.fbx", "Part2_Cage.fbx"}
state.export_lp_triangulate = False
plan = build_export_plan(state, state.pairs[0], state.export_directory)
assert plan.triangulate_lp is False
print("BAKE_TOOLS_EXPORT_PLAN_OK one_file=1 by_material=1 lp_combined=1 cage_separate=1 lp_triangle_toggle=1")
