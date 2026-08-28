"""Read-only hierarchy/transform summary for an FBX passed after ``--``."""

from __future__ import annotations

from pathlib import Path
import sys

import bpy


def _identity(matrix, tolerance=1.0e-6):
    return all(
        abs(float(matrix[row][column]) - (1.0 if row == column else 0.0)) <= tolerance
        for row in range(4) for column in range(4)
    )


path = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=str(path))
bpy.context.view_layer.update()
objects = tuple(bpy.context.scene.objects)
meshes = tuple(obj for obj in objects if obj.type == "MESH")
roots = tuple(obj for obj in objects if obj.parent is None)
inherited = tuple(
    obj for obj in meshes if _identity(obj.matrix_basis) and not _identity(obj.matrix_world)
)
print("BAKE_TOOLS_FBX_HIERARCHY file={}".format(path.name))
print("objects={} meshes={} roots={} inherited_world={}".format(
    len(objects), len(meshes), len(roots), len(inherited)
))
for root in roots[:20]:
    print(
        "ROOT name={!r} type={} children={} loc={} rot={} scale={} world_identity={}".format(
            root.name, root.type, len(root.children), tuple(round(v, 6) for v in root.location),
            tuple(round(v, 6) for v in root.rotation_euler),
            tuple(round(v, 6) for v in root.scale), _identity(root.matrix_world),
        )
    )
