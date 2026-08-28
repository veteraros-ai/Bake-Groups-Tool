"""Production-sized FBX check for world-preserving mesh-only Freeze Transforms."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from uuid import uuid4

import bpy
from mathutils import Vector


ADDON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_ROOT.parent))
import Bake_Tools_Blender as addon  # noqa: E402


def world_bounds(obj):
    points = tuple(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
    return tuple(min(point[axis] for point in points) for axis in range(3)) + tuple(
        max(point[axis] for point in points) for axis in range(3)
    )


path = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
bpy.ops.wm.read_factory_settings(use_empty=True)
addon.register()
hp_collection = bpy.data.collections.new("Imported HP Diagnostic")
bpy.context.scene.collection.children.link(hp_collection)
bpy.context.view_layer.active_layer_collection = (
    bpy.context.view_layer.layer_collection.children[hp_collection.name]
)
bpy.ops.import_scene.fbx(filepath=str(path))
bpy.context.view_layer.update()

state = bpy.context.scene.bake_tools_settings
pair = state.pairs.add()
pair.item_id = uuid4().hex
pair.name = "FBX Freeze Diagnostic"
pair.hp_root_kind = "COLLECTION"
pair.hp_collection = hp_collection
pair.lp_root_kind = "COLLECTION"
pair.lp_collection = bpy.data.collections.new("Empty LP Diagnostic")
bpy.context.scene.collection.children.link(pair.lp_collection)
state.active_pair = 0
state.active_pair_id = pair.item_id

from Bake_Tools_Blender.addon.bake_tools_blender.mesh_tools import (  # noqa: E402
    _transform_candidates, apply_check_transforms,
)

targets = _transform_candidates(pair)
before = {obj: world_bounds(obj) for obj in targets}
state.mesh_check_payload = json.dumps({
    "pair_id": pair.item_id,
    "transforms": [obj.name for obj in targets],
})
fixed, skipped = apply_check_transforms(bpy.context, state, pair)
bpy.context.view_layer.update()
max_error = max(
    (
        max(abs(after - previous) for after, previous in zip(world_bounds(obj), before[obj]))
        for obj in fixed
    ),
    default=0.0,
)
nonzero_origins = sum(obj.matrix_world.translation.length > 1.0e-6 for obj in fixed)
print(
    "BAKE_TOOLS_FBX_FREEZE file={} targets={} fixed={} skipped={} max_bbox_error={:.9g} "
    "nonzero_origins={}".format(
        path.name, len(targets), len(fixed), len(skipped), max_error, nonzero_origins,
    )
)
assert not skipped
assert len(fixed) == len(targets)
assert max_error < 1.0e-5
assert nonzero_origins == 0
