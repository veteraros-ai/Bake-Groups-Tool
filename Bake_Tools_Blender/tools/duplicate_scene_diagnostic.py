"""Compare the legacy and Maya-unit duplicate tolerances on an opened scene."""

from __future__ import annotations

from collections import defaultdict
import sys
from pathlib import Path

import bpy


ADDON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_ROOT.parent))

import Bake_Tools_Blender as addon  # noqa: E402


def meshes_under(root):
    return tuple(
        obj for obj in (root,) + tuple(root.children_recursive)
        if obj.type == "MESH"
    )


def duplicate_summary(objects_by_side, tolerance):
    from Bake_Tools_Blender.addon.bake_tools_blender.mesh_tools import _mesh_facts

    depsgraph = bpy.context.evaluated_depsgraph_get()
    buckets = defaultdict(list)
    for side, objects in objects_by_side.items():
        for obj in objects:
            info = _mesh_facts(obj, depsgraph, tolerance=tolerance)
            key = (
                side, info.vertex_count, info.edge_count, info.face_count,
                info.bbox_key, info.vertex_digest,
            )
            buckets[key].append(obj.name)
    groups = [names for names in buckets.values() if len(names) > 1]
    return len(groups), sum(len(group) for group in groups), sum(len(group) - 1 for group in groups)


def main():
    addon.register()
    hp = bpy.data.objects.get("Suspension_02_HP")
    lp = bpy.data.objects.get("Suspension_02_LP")
    if hp is None or lp is None:
        raise RuntimeError("Suspension_02 HP/LP roots were not found")
    objects_by_side = {"HP": meshes_under(hp), "LP": meshes_under(lp)}
    from Bake_Tools_Blender.addon.bake_tools_blender.mesh_tools import duplicate_check_tolerance

    corrected = duplicate_check_tolerance(bpy.context.scene)
    print("DUPLICATE_SCENE units={} scale_length={}".format(
        bpy.context.scene.unit_settings.system,
        bpy.context.scene.unit_settings.scale_length,
    ))
    print("DUPLICATE_SCENE input HP={} LP={}".format(
        len(objects_by_side["HP"]), len(objects_by_side["LP"])
    ))
    for label, tolerance in (("legacy", 0.001), ("maya_equivalent", corrected)):
        groups, meshes, extras = duplicate_summary(objects_by_side, tolerance)
        print("DUPLICATE_SCENE {} tolerance={:.9g} groups={} meshes={} extras={}".format(
            label, tolerance, groups, meshes, extras
        ))
    addon.unregister()


if __name__ == "__main__":
    main()
