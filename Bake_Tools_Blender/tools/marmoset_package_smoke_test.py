"""Build and inspect a tiny Toolbag package without changing a saved scene."""

import json
from pathlib import Path
import sys
import tempfile

import addon_utils
import bpy


addon_utils.enable("Bake_Tools_Blender", default_set=False, persistent=False)
sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent))
from Bake_Tools_Blender.addon.bake_tools_blender.marmoset_bridge import export_package


state = bpy.context.scene.bake_tools_settings
hp_root = bpy.data.objects.new("Test_HP", None)
lp_root = bpy.data.objects.new("Test_LP", None)
bpy.context.scene.collection.objects.link(hp_root)
bpy.context.scene.collection.objects.link(lp_root)
for name, root in (("BoltHigh", hp_root), ("BoltLow", lp_root)):
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.object
    obj.name = name
    obj.parent = root

pair = state.pairs.add()
pair.item_id = "test-chapter-id"
pair.name = "Test Chapter"
pair.book = "Test Book"
pair.hp_root = hp_root
pair.lp_root = lp_root
state.active_pair_id = pair.item_id
subgroup = pair.subgroups.add()
subgroup.item_id = "test-group-id"
subgroup.name = "Fasteners"
subgroup.smooth_level = 2
subgroup.hp_members.add().target = bpy.data.objects["BoltHigh"]
subgroup.lp_members.add().target = bpy.data.objects["BoltLow"]
preview = bpy.data.objects["BoltHigh"].modifiers.new("Bake Tools Smooth Preview", "SUBSURF")
preview.levels = 1
preview.show_viewport = True
preview.show_render = True

with tempfile.TemporaryDirectory(prefix="bake_groups_toolbag_test_") as directory:
    manifest_path = Path(export_package(bpy.context, state, pair, directory))
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["schema"] == "bake-groups-toolbag"
    assert data["books"] == [{"name": "Test Book", "chapters": [pair.item_id]}]
    chapter = data["chapters"][0]
    assert Path(directory, chapter["model"]).is_file()
    by_role = {entry["role"]: entry for entry in chapter["meshes"]}
    assert by_role["high"]["subdivision_level"] == 2
    assert by_role["low"]["subdivision_level"] == 0
    assert preview.show_viewport and preview.show_render, "Preview state was not restored"
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    before = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=str(Path(directory, chapter["model"])))
    imported = {obj.name: obj for obj in bpy.data.objects if obj not in before and obj.type == "MESH"}
    assert set(imported) == {"BoltHigh", "BoltLow"}, set(imported)
    assert len(imported["BoltHigh"].data.polygons) == 6, "Toolbag HP must not be pre-smoothed"
    print("MARMOSET_PACKAGE_PASS", set(imported), chapter["model"])
