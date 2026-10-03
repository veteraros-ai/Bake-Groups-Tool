"""Run with Blender 5.1 --background --factory-startup --python-exit-code 1."""

from pathlib import Path
import subprocess
import sys
import tempfile

import bpy
from mathutils import Vector


sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent))
from Bake_Tools_Blender.addon.bake_tools_blender.hp_binary import write
from Bake_Tools_Blender.addon.bake_tools_blender.external_hp_export import helper_path


bpy.ops.mesh.primitive_cube_add(location=(1.0, 2.0, 3.0))
source = bpy.context.object
source.name = "HP_External_Roundtrip"
with tempfile.TemporaryDirectory(prefix="BakeGroupsExternalRoundtrip_") as folder:
    binary = Path(folder) / "source.bghp"
    output = Path(folder) / "result.fbx"
    write(binary, (source,), {source.as_pointer(): 1})
    subprocess.run((str(helper_path()), "--input", str(binary), "--output", str(output),
                    "--level", "0", "--format", "fbx", "--input-binary"),
                   check=True, capture_output=True)
    bpy.ops.object.select_all(action="DESELECT")
    bpy.ops.import_scene.fbx(filepath=str(output))
    imported = next(obj for obj in bpy.context.selected_objects if obj.type == "MESH")
    points = tuple(imported.matrix_world @ corner.co for corner in imported.data.vertices)
    center = sum(points, Vector()) / len(points)
    assert len(imported.data.polygons) == 24, len(imported.data.polygons)
    assert (center - Vector((1.0, 2.0, 3.0))).length < 1e-4, center
    print("BAKE_TOOLS_EXTERNAL_HP_ROUNDTRIP_OK", center[:], len(imported.data.polygons))
