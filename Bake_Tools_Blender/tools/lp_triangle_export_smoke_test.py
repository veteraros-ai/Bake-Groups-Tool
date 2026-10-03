"""Check LP Triangle changes FBX topology without changing source LP or UVs."""

from pathlib import Path
import sys
import tempfile

import addon_utils
import bpy


addon_utils.enable("Bake_Tools_Blender", default_set=False, persistent=False)
from Bake_Tools_Blender.addon.bake_tools_blender.export_service import build_export_plan, execute_export


state = bpy.context.scene.bake_tools_settings
for obj in tuple(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
root = bpy.data.objects.new("LP_Root", None)
bpy.context.scene.collection.objects.link(root)
mesh = bpy.data.meshes.new("LP_Quad_Data")
mesh.from_pydata(((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)), (), ((0, 1, 2, 3),))
mesh.uv_layers.new(name="BakeUV")
lp = bpy.data.objects.new("LP_Quad", mesh)
bpy.context.scene.collection.objects.link(lp)
lp.parent = root
pair = state.pairs.add()
pair.item_id = "lp-triangle-test"
pair.name = "LP Triangle Test"
pair.lp_root = root
subgroup = pair.subgroups.add()
subgroup.item_id = "lp-group"
subgroup.name = "LP_Group"
subgroup.lp_members.add().target = lp
state.active_pair_id = pair.item_id
state.export_scope = "CHAPTER"
state.export_include_hp = False
state.export_include_lp = True
state.export_include_cage = False
state.export_files = "SEPARATE"

with tempfile.TemporaryDirectory(prefix="bake_groups_lp_triangle_") as directory:
    for enabled, expected in ((False, 1), (True, 2)):
        state.export_lp_triangulate = enabled
        output_dir = Path(directory) / str(enabled)
        paths = execute_export(bpy.context, build_export_plan(state, pair, str(output_dir)))
        assert len(paths) == 1, paths
        before = set(bpy.data.objects)
        bpy.ops.import_scene.fbx(filepath=paths[0])
        imported = [obj for obj in bpy.data.objects if obj not in before and obj.type == "MESH"]
        assert len(imported) == 1
        assert len(imported[0].data.polygons) == expected, (enabled, len(imported[0].data.polygons))
        assert imported[0].data.uv_layers, "LP UVs lost"
        bpy.data.objects.remove(imported[0], do_unlink=True)
        assert len(mesh.polygons) == 1 and not lp.modifiers[:], "LP source was modified"
        print("LP_TRIANGLE_PASS", enabled, expected)
