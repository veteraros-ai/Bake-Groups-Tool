"""Pure-Python LP-to-HP-subgroup matcher based on the Maya fast worker."""

from __future__ import annotations

from collections import defaultdict
from math import sqrt
import re

from . import native_core
from .domain.lp_matching import LPAssignment, LPMatchResult


_EPSILON = 1.0e-9
_MATERIAL_GROUP = re.compile(r"^(M\d{2,})(?:_|\.)", re.IGNORECASE)


def _distance_squared(left, right):
    return sum((left[axis] - right[axis]) ** 2 for axis in range(3))


def _distance(left, right):
    return sqrt(_distance_squared(left, right))


def _sample(points, maximum):
    points = tuple(points)
    if len(points) <= maximum:
        return points
    step = len(points) / float(maximum)
    return tuple(points[min(len(points) - 1, int(index * step))] for index in range(maximum))


class _KDNode:
    __slots__ = ("point", "axis", "left", "right")

    def __init__(self, point, axis, left, right):
        self.point = point
        self.axis = axis
        self.left = left
        self.right = right


def _build_kd(points, depth=0):
    if not points:
        return None
    axis = depth % 3
    ordered = sorted(points, key=lambda point: (point[axis], point))
    middle = len(ordered) // 2
    return _KDNode(
        ordered[middle],
        axis,
        _build_kd(ordered[:middle], depth + 1),
        _build_kd(ordered[middle + 1:], depth + 1),
    )


def _nearest_squared(node, point, best=float("inf")):
    if node is None:
        return best
    distance = _distance_squared(node.point, point)
    best = min(best, distance)
    delta = point[node.axis] - node.point[node.axis]
    near, far = (node.left, node.right) if delta < 0.0 else (node.right, node.left)
    best = _nearest_squared(near, point, best)
    if delta * delta < best:
        best = _nearest_squared(far, point, best)
    return best


def _average_nearest_distance(source, target, sample_cap, tree_cache, cache_key):
    source_points = _sample(source.vertices, sample_cap)
    target_points = _sample(target.vertices, sample_cap)
    if not source_points or not target_points:
        return _distance(source.center, target.center)
    native_distance = native_core.calculate_avg_distance(source_points, target_points)
    if native_distance is not None:
        return native_distance
    tree_key = cache_key, sample_cap
    tree = tree_cache.get(tree_key)
    if tree is None:
        tree = _build_kd(target_points)
        tree_cache[tree_key] = tree
    total = sum(sqrt(_nearest_squared(tree, point)) for point in source_points)
    return total / float(len(source_points))


def _expanded_bbox_overlap(left, right, padding):
    center_distance = _distance(left.center, right.center)
    radius_sum = (left.diagonal + right.diagonal) * 0.5 * padding
    if center_distance > radius_sum:
        return False
    for axis in range(3):
        left_half = (left.bbox_max[axis] - left.center[axis]) * padding
        right_half = (right.bbox_max[axis] - right.center[axis]) * padding
        left_min, left_max = left.center[axis] - left_half, left.center[axis] + left_half
        right_min, right_max = right.center[axis] - right_half, right.center[axis] + right_half
        if min(left_max, right_max) - max(left_min, right_min) <= 0.0:
            return False
    return True


def _fast_topology_confirm(lp_mesh, hp_mesh):
    if lp_mesh.vertex_count != hp_mesh.vertex_count or lp_mesh.edge_count != hp_mesh.edge_count:
        return False
    dimensions_match = all(
        abs(lp_mesh.dimensions[axis] - hp_mesh.dimensions[axis])
        / max(lp_mesh.dimensions[axis], _EPSILON) < 0.001
        for axis in range(3)
    )
    return dimensions_match and _distance_squared(lp_mesh.center, hp_mesh.center) <= (
        lp_mesh.diagonal * lp_mesh.diagonal * 0.001
    )


def _material_slot_from_group(name):
    match = _MATERIAL_GROUP.match(str(name or ""))
    return match.group(1).upper() if match else ""


class LPMatchingService:
    """Match every LP snapshot to the nearest plausible HP subgroup."""

    def match(self, groups, lp_meshes, settings, material_key_by_lp=None, progress=None):
        groups = tuple(sorted(groups, key=lambda item: item.name.lower()))
        lp_meshes = tuple(lp_meshes)
        material_key_by_lp = dict(material_key_by_lp or {})
        if not groups:
            raise ValueError("No HP subgroups found; run Analyze HP first")
        if any(not group.hp_meshes for group in groups):
            raise ValueError("LP matching groups must contain HP meshes")
        lp_by_key = {mesh.key: mesh for mesh in lp_meshes}
        if len(lp_by_key) != len(lp_meshes):
            raise ValueError("LP snapshots must have unique keys")
        hp_keys = [mesh.key for group in groups for mesh in group.hp_meshes]
        if len(hp_keys) != len(set(hp_keys)):
            raise ValueError("One HP mesh belongs to more than one subgroup")

        fast_cap = 192 if settings.optimization == "OPTIMAL" else 96
        full_cap = 768 if settings.optimization == "OPTIMAL" else 320
        tree_cache = {}
        debug = [
            "Assign LP (Blender service)",
            "math backend={}".format(native_core.backend_name()),
            "input: {} subgroup(s), {} LP mesh(es)".format(len(groups), len(lp_meshes)),
            "threshold={} bbox_padding={} samples={}/{}".format(
                settings.threshold_coefficient, settings.bbox_padding, fast_cap, full_cap
            ),
        ]
        group_by_key = {}
        first_pass_misses = []
        for index, lp_mesh in enumerate(lp_meshes):
            group = self._best_match(lp_mesh, groups, settings, fast_cap, tree_cache)
            if group is None:
                first_pass_misses.append(lp_mesh)
            else:
                group_by_key[lp_mesh.key] = group.name
                debug.append("FAST {} -> {}".format(lp_mesh.name, group.name))
            if progress:
                progress.update(5 + int((index + 1) * 45 / max(1, len(lp_meshes))), "Fast LP match: {}".format(lp_mesh.name))
        for index, lp_mesh in enumerate(first_pass_misses):
            group = self._best_match(lp_mesh, groups, settings, full_cap, tree_cache)
            if group is not None:
                group_by_key[lp_mesh.key] = group.name
                debug.append("PRECISE {} -> {}".format(lp_mesh.name, group.name))
            if progress:
                progress.update(50 + int((index + 1) * 30 / max(1, len(first_pass_misses))), "Precise LP match: {}".format(lp_mesh.name))

        if progress:
            progress.update(82, "Checking LP material slots")
        material_repairs = self._repair_material_slots(
            group_by_key, groups, lp_by_key, material_key_by_lp,
            settings, full_cap, tree_cache, debug,
        )
        assignments = defaultdict(list)
        for key, group_name in group_by_key.items():
            assignments[group_name].append(key)
        assignment_records = tuple(
            LPAssignment(group_name=name, lp_keys=tuple(sorted(keys)))
            for name, keys in sorted(assignments.items(), key=lambda item: item[0].lower())
        )
        unmatched = tuple(mesh.key for mesh in lp_meshes if mesh.key not in group_by_key)
        warnings = ()
        if unmatched:
            warnings = ("{} LP mesh(es) could not be matched".format(len(unmatched)),)
            debug.extend("UNMATCHED {}".format(lp_by_key[key].name) for key in unmatched)
        debug.append("result: {}/{} matched, {} material repair(s)".format(
            len(group_by_key), len(lp_meshes), material_repairs
        ))
        if progress:
            progress.update(98, "Applying LP assignments")
        return LPMatchResult(
            assignments=assignment_records,
            processed_lp=len(lp_meshes),
            matched_lp=len(group_by_key),
            unmatched_lp_keys=unmatched,
            material_repairs=material_repairs,
            warnings=warnings,
            debug_lines=tuple(debug),
        )

    @staticmethod
    def _best_match(lp_mesh, groups, settings, sample_cap, tree_cache):
        best_group = None
        best_distance = float("inf")
        for group in groups:
            for hp_mesh in group.hp_meshes:
                if not _expanded_bbox_overlap(lp_mesh, hp_mesh, settings.bbox_padding):
                    continue
                if _fast_topology_confirm(lp_mesh, hp_mesh):
                    return group
                average = _average_nearest_distance(
                    lp_mesh, hp_mesh, sample_cap, tree_cache, hp_mesh.key
                )
                if average < best_distance:
                    best_distance = average
                    best_group = group
        if best_group is None:
            return None
        threshold = max(lp_mesh.diagonal, _EPSILON) * settings.threshold_coefficient
        if "zbrush" in best_group.name.lower():
            threshold *= 3.0
        return best_group if best_distance < threshold else None

    def _repair_material_slots(
        self, group_by_key, groups, lp_by_key, material_key_by_lp,
        settings, sample_cap, tree_cache, debug,
    ):
        matched_materials = sorted({
            material_key_by_lp.get(key, "") for key in group_by_key
            if material_key_by_lp.get(key, "")
        }, key=str.lower)
        if len(matched_materials) <= 1:
            return 0
        slot_by_material = {
            material: "M{:02d}".format(index)
            for index, material in enumerate(matched_materials, 1)
        }
        compatible = defaultdict(list)
        for group in groups:
            slot = _material_slot_from_group(group.name)
            if slot:
                compatible[slot].append(group)
        if not compatible:
            return 0
        repairs = 0
        for key, old_name in tuple(group_by_key.items()):
            old_slot = _material_slot_from_group(old_name)
            desired_slot = slot_by_material.get(material_key_by_lp.get(key, ""), "")
            if not old_slot or not desired_slot or old_slot == desired_slot:
                continue
            candidates = tuple(compatible.get(desired_slot, ()))
            if not candidates:
                continue
            lp_mesh = lp_by_key[key]
            target = self._best_match(lp_mesh, candidates, settings, sample_cap, tree_cache)
            if target is None:
                target = min(
                    candidates,
                    key=lambda group: min(
                        _distance(lp_mesh.center, hp.center) for hp in group.hp_meshes
                    ),
                )
            group_by_key[key] = target.name
            repairs += 1
            debug.append("MATERIAL_REPAIR {}: {} -> {}".format(lp_mesh.name, old_name, target.name))
        return repairs
