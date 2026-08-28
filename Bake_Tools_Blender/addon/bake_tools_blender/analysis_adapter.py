"""Blender main-thread adapter for the pure HP analysis service."""

from __future__ import annotations

import re
from uuid import uuid4

import bpy

from .domain.analysis import AnalysisSettings, MeshSnapshot
from .object_repository import ObjectRepository


_SEMANTIC_GROUP_RE = re.compile(
    r"(?:^|_)(zbrush_(?:huge|large|medium|small|bolts)|bolts|huge|large|medium|small)"
    r"_(\d{3})(?=_high(?:_|$))",
    re.IGNORECASE,
)
_SEMANTIC_CATEGORY_CASE = {
    "zbrush_huge": "ZBrush_Huge",
    "zbrush_large": "ZBrush_Large",
    "zbrush_medium": "ZBrush_Medium",
    "zbrush_small": "ZBrush_Small",
    "zbrush_bolts": "ZBrush_Bolts",
    "bolts": "Bolts",
    "huge": "Huge",
    "large": "Large",
    "medium": "Medium",
    "small": "Small",
}


def _semantic_group_from_name(name):
    """Return a stable imported Bake Groups island, if the name contains one."""
    match = _SEMANTIC_GROUP_RE.search(str(name or ""))
    if match is None:
        return ""
    category = _SEMANTIC_CATEGORY_CASE.get(match.group(1).lower(), "")
    return "{}_{:03d}".format(category, int(match.group(2))) if category else ""


def _snapshot_mesh(obj, depsgraph, sample_cap, *, is_zbrush=False, semantic_group=""):
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=False, depsgraph=depsgraph)
    try:
        matrix = evaluated.matrix_world
        vertex_count = len(mesh.vertices)
        if vertex_count > sample_cap:
            step = vertex_count / float(sample_cap)
            sample_indices = {
                min(vertex_count - 1, int(index * step)) for index in range(sample_cap)
            }
        else:
            sample_indices = set(range(vertex_count))
        minimum = [float("inf"), float("inf"), float("inf")]
        maximum = [float("-inf"), float("-inf"), float("-inf")]
        sampled = []
        for index, vertex in enumerate(mesh.vertices):
            point = tuple(matrix @ vertex.co)
            for axis in range(3):
                minimum[axis] = min(minimum[axis], point[axis])
                maximum[axis] = max(maximum[axis], point[axis])
            if index in sample_indices:
                sampled.append(point)
        if vertex_count:
            bbox_min = tuple(minimum)
            bbox_max = tuple(maximum)
        else:
            center = tuple(evaluated.matrix_world.translation)
            bbox_min = center
            bbox_max = center
        dimensions = tuple(bbox_max[axis] - bbox_min[axis] for axis in range(3))
        diagonal = sum(value * value for value in dimensions) ** 0.5
        center = tuple((bbox_min[axis] + bbox_max[axis]) * 0.5 for axis in range(3))
        return MeshSnapshot(
            key=obj.name_full,
            name=obj.name,
            bbox_min=bbox_min,
            bbox_max=bbox_max,
            center=center,
            dimensions=dimensions,
            diagonal=diagonal,
            bbox_volume=dimensions[0] * dimensions[1] * dimensions[2],
            vertex_count=vertex_count,
            edge_count=len(mesh.edges),
            face_count=len(mesh.polygons),
            vertices=tuple(sampled),
            is_zbrush=bool(is_zbrush),
            semantic_group=str(semantic_group or ""),
        )
    finally:
        evaluated.to_mesh_clear()


def _snapshot_material_regions(obj, depsgraph, sample_cap):
    """Create Maya-style virtual LP shells for every used Blender material."""
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=False, depsgraph=depsgraph)
    try:
        by_material = {}
        for polygon in mesh.polygons:
            by_material.setdefault(int(polygon.material_index), []).append(polygon)
        if len(by_material) <= 1:
            return ()

        matrix = evaluated.matrix_world
        slots = tuple(getattr(obj.data, "materials", ()))
        regions = []
        for material_index, polygons in sorted(by_material.items()):
            vertex_indices = sorted({index for polygon in polygons for index in polygon.vertices})
            if not vertex_indices:
                continue
            points = tuple(tuple(matrix @ mesh.vertices[index].co) for index in vertex_indices)
            if len(points) > sample_cap:
                step = len(points) / float(sample_cap)
                sampled = tuple(points[min(len(points) - 1, int(index * step))] for index in range(sample_cap))
            else:
                sampled = points
            bbox_min = tuple(min(point[axis] for point in points) for axis in range(3))
            bbox_max = tuple(max(point[axis] for point in points) for axis in range(3))
            dimensions = tuple(bbox_max[axis] - bbox_min[axis] for axis in range(3))
            diagonal = sum(value * value for value in dimensions) ** 0.5
            center = tuple((bbox_min[axis] + bbox_max[axis]) * 0.5 for axis in range(3))
            edges = {
                tuple(sorted((polygon.vertices[index], polygon.vertices[(index + 1) % len(polygon.vertices)])))
                for polygon in polygons
                for index in range(len(polygon.vertices))
            }
            if 0 <= material_index < len(slots) and slots[material_index] is not None:
                material_name = slots[material_index].name_full
            else:
                material_name = "Slot_{:02d}".format(material_index + 1)
            regions.append(MeshSnapshot(
                key="{}::MAT:{:03d}".format(obj.name_full, material_index),
                name="{} [{}]".format(obj.name, material_name),
                bbox_min=bbox_min,
                bbox_max=bbox_max,
                center=center,
                dimensions=dimensions,
                diagonal=diagonal,
                bbox_volume=dimensions[0] * dimensions[1] * dimensions[2],
                vertex_count=len(vertex_indices),
                edge_count=len(edges),
                face_count=len(polygons),
                vertices=sampled,
            ))
        return tuple(regions)
    finally:
        evaluated.to_mesh_clear()


def capture_analysis_input(context, pair, state):
    depsgraph = context.evaluated_depsgraph_get()
    sample_cap = 4096 if state.optimization == "OPTIMAL" else 1024
    locked_hp = {
        obj.as_pointer()
        for owner_pair in state.pairs
        for subgroup in owner_pair.subgroups if subgroup.locked
        for obj in ObjectRepository.valid_members(subgroup, "HP")
    }
    hp_objects = [
        obj for obj in ObjectRepository.meshes_under_root(pair, "HP")
        if obj.as_pointer() not in locked_hp
        and not obj.name.lower().endswith(("_lp", "_low"))
    ]
    lp_objects = list(ObjectRepository.meshes_under_root(pair, "LP"))
    remembered_zbrush = {
        ref.target.as_pointer() for ref in state.zbrush_members if ref.target is not None
    }
    from .matcher import linked_semantic_map
    matcher_groups = linked_semantic_map(pair)
    zbrush_collection = bpy.data.collections.get("BakeTools_ZBrush_Layer")
    if zbrush_collection is not None:
        remembered_zbrush.update(obj.as_pointer() for obj in zbrush_collection.objects)

    def zbrush_flag(obj, semantic_group):
        # The marker/collection is authoritative.  The name hint is a migration
        # bridge for Maya scenes whose ZBrush display-layer membership was not
        # fully recreated by a topology-only Find ZBrush pass.
        return bool(
            obj.get("bake_tools_zbrush")
            or obj.as_pointer() in remembered_zbrush
            or semantic_group.startswith("ZBrush_")
            or "_zbrush_" in obj.name.lower()
            or obj.name.lower().endswith("_zbrush")
        )

    hp_snapshots = []
    for obj in hp_objects:
        semantic_group = matcher_groups.get(obj.as_pointer()) or _semantic_group_from_name(obj.name)
        hp_snapshots.append(_snapshot_mesh(
            obj,
            depsgraph,
            sample_cap,
            is_zbrush=zbrush_flag(obj, semantic_group),
            semantic_group=semantic_group,
        ))
    hp_snapshots = tuple(hp_snapshots)
    lp_snapshots = []
    for obj in lp_objects:
        regions = _snapshot_material_regions(obj, depsgraph, sample_cap) if pair.material_slots else ()
        lp_snapshots.extend(regions or (_snapshot_mesh(obj, depsgraph, sample_cap),))
    lp_snapshots = tuple(lp_snapshots)
    object_by_key = {obj.name_full: obj for obj in hp_objects}
    settings = AnalysisSettings(
        strategy=state.hp_strategy,
        optimization=state.optimization,
        collision_pct=state.collision_pct,
        ignore_floaters=state.ignore_floaters,
        adjacent_link=state.adjacent_link,
        link_vertex=state.link_vertex,
        link_distance_pct=state.link_distance,
        use_symmetry=state.calculate_symmetry,
        group_limit=12,
        unit_scale_meters=max(float(context.scene.unit_settings.scale_length), 1.0e-12),
    )
    reserved_names = tuple(subgroup.name for subgroup in pair.subgroups if subgroup.locked)
    return hp_snapshots, lp_snapshots, settings, reserved_names, object_by_key


def _capture_membership(pair):
    records = []
    for subgroup in pair.subgroups:
        records.append({
            "item_id": subgroup.item_id,
            "name": subgroup.name,
            "visible": subgroup.visible,
            "locked": subgroup.locked,
            "smooth_level": subgroup.smooth_level,
            "cage_override": subgroup.cage_override,
            "color_index": subgroup.color_index,
            "use_custom_color": subgroup.use_custom_color,
            "custom_color": tuple(subgroup.custom_color),
            "hp": ObjectRepository.valid_members(subgroup, "HP"),
            "lp": ObjectRepository.valid_members(subgroup, "LP"),
        })
    return records


def _add_refs(refs, objects):
    for obj in objects:
        ref = refs.add()
        ref.target = obj
        ref.last_name = obj.name


def _restore_membership(pair, records):
    pair.subgroups.clear()
    for record in records:
        subgroup = pair.subgroups.add()
        subgroup.item_id = record["item_id"]
        subgroup.name = record["name"]
        subgroup.visible = record["visible"]
        subgroup.locked = record["locked"]
        subgroup.smooth_level = record["smooth_level"]
        subgroup.cage_override = record["cage_override"]
        subgroup.color_index = record["color_index"]
        subgroup.use_custom_color = record["use_custom_color"]
        subgroup.custom_color = record["custom_color"]
        _add_refs(subgroup.hp_members, record["hp"])
        _add_refs(subgroup.lp_members, record["lp"])
    ObjectRepository.sync_counts(pair)


def apply_analysis_result(state, pair, result, object_by_key):
    """Commit a fully validated plan, restoring metadata if commit fails."""
    planned_keys = [key for group in result.groups for key in group.hp_keys]
    if len(planned_keys) != len(set(planned_keys)):
        raise ValueError("Analysis result assigns one HP object more than once")
    missing = [key for key in planned_keys if key not in object_by_key]
    if missing:
        raise ValueError("Analysis result references missing objects: {}".format(", ".join(missing[:5])))
    locked_names = {subgroup.name for subgroup in pair.subgroups if subgroup.locked}
    collision = [group.name for group in result.groups if group.name in locked_names]
    if collision:
        raise ValueError("Analysis result collides with locked subgroup names: {}".format(", ".join(collision)))

    backups = [(owner_pair, _capture_membership(owner_pair)) for owner_pair in state.pairs]
    try:
        # Clear only the recalculated HP side.  LP membership remains available
        # until Assign LP produces a new LP plan, matching Maya's two-stage flow.
        for subgroup in pair.subgroups:
            if not subgroup.locked:
                subgroup.hp_members.clear()

        # Preserve the project-wide exclusive membership contract established
        # by Add Selected.  Locked memberships were excluded during capture.
        for key in planned_keys:
            obj = object_by_key[key]
            for owner_pair in state.pairs:
                if owner_pair != pair:
                    ObjectRepository.remove_member_from_pair(owner_pair, obj)

        by_name = {subgroup.name: subgroup for subgroup in pair.subgroups if not subgroup.locked}
        for group in result.groups:
            subgroup = by_name.get(group.name)
            if subgroup is None:
                subgroup = pair.subgroups.add()
                subgroup.item_id = uuid4().hex
                subgroup.name = group.name
                by_name[group.name] = subgroup
            _add_refs(subgroup.hp_members, (object_by_key[key] for key in group.hp_keys))

        # Maya removes stale HP transform groups, but LP groups with the same UI
        # identity survive.  Mirror that by deleting metadata only when both
        # sides are empty.
        for index in range(len(pair.subgroups) - 1, -1, -1):
            subgroup = pair.subgroups[index]
            if not subgroup.locked and not subgroup.hp_members and not subgroup.lp_members:
                pair.subgroups.remove(index)
        for owner_pair in state.pairs:
            ObjectRepository.sync_counts(owner_pair)
        ObjectRepository.sync_pair_visibility(state, pair)
    except Exception:
        for owner_pair, backup in backups:
            _restore_membership(owner_pair, backup)
        ObjectRepository.sync_pair_visibility(state, pair)
        raise
