"""Compare world geometry of Blender and external HP FBX exports."""

from pathlib import Path
import sys

import bpy
from mathutils import Vector


arguments = sys.argv[sys.argv.index("--") + 1:]
baseline_path, external_path = map(Path, arguments[:2])


def read(path):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=str(path))
    objects = tuple(obj for obj in bpy.data.objects if obj not in before and obj.type == "MESH")
    result = {}
    for obj in objects:
        corners = tuple(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
        low = tuple(min(point[axis] for point in corners) for axis in range(3))
        high = tuple(max(point[axis] for point in corners) for axis in range(3))
        result[obj.name] = (len(obj.data.polygons), len(obj.data.vertices), low, high)
    for obj in objects:
        bpy.data.objects.remove(obj, do_unlink=True)
    return result


baseline = read(baseline_path)
external = read(external_path)
missing = sorted(set(baseline) - set(external))
extra = sorted(set(external) - set(baseline))
different = []
largest = []
for name in set(baseline) & set(external):
    left, right = baseline[name], external[name]
    maximum = max(abs(a - b) for part_left, part_right in
                  zip(left[2:], right[2:]) for a, b in zip(part_left, part_right))
    size = max(left[3][axis] - left[2][axis] for axis in range(3))
    largest.append((maximum, maximum / max(size, 1e-12), name))
    if left[:2] != right[:2] or any(abs(a - b) > 1e-6 for part_left, part_right in
                                   zip(left[2:], right[2:]) for a, b in zip(part_left, part_right)):
        different.append((name, left, right))
print("HP_FBX_DIFF_STATS", len(different), sorted(largest, reverse=True)[:12])
print("HP_FBX_PARITY", len(baseline), len(external),
      "missing", missing[:10], "extra", extra[:10], "different", different[:10])
assert not missing and not extra and not different
