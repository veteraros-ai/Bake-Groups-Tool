"""Find Sim/All, Smooth View, FBX export and progress/cancel integration test."""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
from uuid import uuid4

import addon_utils
import bpy


addon_utils.enable("Bake_Tools_Blender", default_set=False, persistent=False)

from Bake_Tools_Blender.addon.bake_tools_blender.progress import cancel_task, set_listener  # noqa: E402
from Bake_Tools_Blender.addon.bake_tools_blender import export_service  # noqa: E402


def mesh_object(name, vertices, faces, location, parent):
    mesh = bpy.data.meshes.new(name + "_Mesh")
    mesh.from_pydata(vertices, (), faces); mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.parent = parent; obj.location = location
    return obj


cube_vertices = (
    (-.5, -.5, -.5), (.5, -.5, -.5), (.5, .5, -.5), (-.5, .5, -.5),
    (-.5, -.5, .5), (.5, -.5, .5), (.5, .5, .5), (-.5, .5, .5),
)
cube_faces = ((0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (4, 0, 3, 7))
pyramid_vertices = ((-.5, -.5, 0), (.5, -.5, 0), (.5, .5, 0), (-.5, .5, 0), (0, 0, 1))
pyramid_faces = ((0, 1, 2, 3), (0, 4, 1), (1, 4, 2), (2, 4, 3), (3, 4, 0))

hp_root = bpy.data.objects.new("Workflow_HP", None); bpy.context.scene.collection.objects.link(hp_root)
lp_root = bpy.data.objects.new("Workflow_LP", None); bpy.context.scene.collection.objects.link(lp_root)
a1 = mesh_object("A_01", cube_vertices, cube_faces, (0, 0, 0), hp_root)
b1 = mesh_object("B_01", pyramid_vertices, pyramid_faces, (2, 0, 0), hp_root)
a2 = mesh_object("A_02", cube_vertices, cube_faces, (10, 0, 0), hp_root)
b2 = mesh_object("B_02", pyramid_vertices, pyramid_faces, (12, 0, 0), hp_root)
a3 = mesh_object("A_03", cube_vertices, cube_faces, (20, 0, 0), hp_root)
lp = mesh_object("Workflow_Low", cube_vertices, cube_faces, (0, 0, 0), lp_root)

state = bpy.context.scene.bake_tools_settings
pair = state.pairs.add(); pair.item_id = uuid4().hex; pair.name = "Workflow"
pair.hp_root = hp_root; pair.hp_object = hp_root.name; pair.hp_root_kind = "OBJECT"
pair.lp_root = lp_root; pair.lp_object = lp_root.name; pair.lp_root_kind = "OBJECT"
state.active_pair = 0; state.active_pair_id = pair.item_id; state.chapter_isolated = False
large = pair.subgroups.add(); large.item_id = uuid4().hex; large.name = "Large"; large.smooth_level = 2
detail = pair.subgroups.add(); detail.item_id = uuid4().hex; detail.name = "Detail"; detail.smooth_level = 1
for obj in (a1, a2, a3):
    ref = large.hp_members.add(); ref.target = obj; ref.last_name = obj.name
for obj in (b1, b2):
    ref = detail.hp_members.add(); ref.target = obj; ref.last_name = obj.name
ref = large.lp_members.add(); ref.target = lp; ref.last_name = lp.name
ref = state.zbrush_members.add(); ref.target = b1; ref.last_name = b1.name
b1["bake_tools_zbrush"] = True


def select(*objects):
    for obj in tuple(bpy.context.selected_objects): obj.select_set(False)
    for obj in objects: obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0] if objects else None


events = []
set_listener(events.append)
select(a1, b1)
state.find_mode = "SIM"
assert "FINISHED" in bpy.ops.bake_tools.action(action="FIND_SIM")
assert {obj.name for obj in bpy.context.selected_objects} == {"A_01", "B_01", "A_02", "B_02"}
assert any(event.kind == "BEGIN" and event.title == "Find Sim" for event in events)
assert any(event.kind == "END" and event.title == "Find Sim" for event in events)

select(a1)
state.find_mode = "ALL"
assert "FINISHED" in bpy.ops.bake_tools.action(action="FIND_SIM")
assert {obj.name for obj in bpy.context.selected_objects} == {"A_01", "A_02", "A_03"}

assert "FINISHED" in bpy.ops.bake_tools.action(action="SMOOTH")
assert state.preview_smoothing
assert any(mod.name.startswith("Bake Tools Smooth Preview") for mod in a1.modifiers)
assert not any(mod.name.startswith("Bake Tools Smooth Preview") for mod in b1.modifiers)  # ZBrush exclusion

export_dir = Path(tempfile.mkdtemp(prefix="BakeToolsExport_"))
try:
    state.export_directory = str(export_dir)
    state.export_scope = "CHAPTER"; state.export_files = "SEPARATE"
    state.export_include_hp = True; state.export_include_lp = True; state.export_include_cage = False
    triangulate_calls = []
    original_triangulate = export_service._temporary_triangulate

    def tracked_triangulate(objects):
        triangulate_calls.append(tuple(objects))
        return original_triangulate(objects)

    export_service._temporary_triangulate = tracked_triangulate
    state.export_lp_triangulate = False
    assert "FINISHED" in bpy.ops.bake_tools.action(action="EXPORT")
    assert not triangulate_calls
    state.export_lp_triangulate = True
    assert "FINISHED" in bpy.ops.bake_tools.action(action="EXPORT")
    assert triangulate_calls and lp in triangulate_calls[-1]
    export_service._temporary_triangulate = original_triangulate
    hp_file = export_dir / "Workflow_HP.fbx"; lp_file = export_dir / "Workflow_LP.fbx"
    assert hp_file.exists() and hp_file.stat().st_size > 0
    assert lp_file.exists() and lp_file.stat().st_size > 0
    assert not any(mod.name.startswith("Bake Tools LP Export Triangulate") for mod in lp.modifiers)
    assert all(not mod.show_render for mod in a1.modifiers if mod.name.startswith("Bake Tools Smooth Preview"))
finally:
    shutil.rmtree(export_dir)

# Cancel must be observed before Find Sim changes selection.
def cancel_listener(event):
    events.append(event)
    if event.kind == "UPDATE" and event.title == "Find Sim":
        cancel_task(event.task_id)


set_listener(cancel_listener)
select(a1, b1)
state.find_mode = "SIM"
assert "CANCELLED" in bpy.ops.bake_tools.action(action="FIND_SIM")
assert set(bpy.context.selected_objects) == {a1, b1}

# Smooth View follows the active chapter instead of leaving modifiers behind.
next_hp_root = bpy.data.objects.new("Next_HP", None); bpy.context.scene.collection.objects.link(next_hp_root)
next_lp_root = bpy.data.objects.new("Next_LP", None); bpy.context.scene.collection.objects.link(next_lp_root)
next_hp = mesh_object("Next_High", cube_vertices, cube_faces, (30, 0, 0), next_hp_root)
next_lp = mesh_object("Next_Low", cube_vertices, cube_faces, (30, 0, 0), next_lp_root)
next_pair = state.pairs.add(); next_pair.item_id = uuid4().hex; next_pair.name = "Next"
next_pair.hp_root = next_hp_root; next_pair.hp_object = next_hp_root.name; next_pair.hp_root_kind = "OBJECT"
next_pair.lp_root = next_lp_root; next_pair.lp_object = next_lp_root.name; next_pair.lp_root_kind = "OBJECT"
next_group = next_pair.subgroups.add(); next_group.item_id = uuid4().hex; next_group.name = "NextGroup"; next_group.smooth_level = 1
for obj, members in ((next_hp, next_group.hp_members), (next_lp, next_group.lp_members)):
    ref = members.add(); ref.target = obj; ref.last_name = obj.name
assert "FINISHED" in bpy.ops.bake_tools.pair_action(action="ACTIVATE", pair_id=next_pair.item_id)
assert not any(mod.name.startswith("Bake Tools Smooth Preview") for mod in a1.modifiers)
assert any(mod.name.startswith("Bake Tools Smooth Preview") for mod in next_hp.modifiers)

set_listener(None)
assert "FINISHED" in bpy.ops.bake_tools.action(action="SMOOTH")
assert not state.preview_smoothing
assert not any(mod.name.startswith("Bake Tools Smooth Preview") for obj in bpy.data.objects for mod in obj.modifiers)
print("BAKE_TOOLS_WORKFLOW_PROGRESS_OK events={} lp_triangle_toggle=1".format(len(events)))
