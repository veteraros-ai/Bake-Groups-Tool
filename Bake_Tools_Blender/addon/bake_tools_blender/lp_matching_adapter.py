"""Blender main-thread capture and atomic commit for Assign LP."""

from __future__ import annotations

from .analysis_adapter import _add_refs, _capture_membership, _restore_membership, _snapshot_mesh
from .domain.lp_matching import LPMatchGroup, LPMatchSettings
from .object_repository import ObjectRepository


def _dominant_material_key(obj, depsgraph):
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=False, depsgraph=depsgraph)
    try:
        materials = tuple(obj.data.materials) if getattr(obj, "data", None) is not None else ()
        counts = {}
        for polygon in mesh.polygons:
            index = int(polygon.material_index)
            counts[index] = counts.get(index, 0) + 1
        if not counts:
            return ""
        index = max(counts, key=lambda value: (counts[value], -value))
        if 0 <= index < len(materials) and materials[index] is not None:
            return materials[index].name_full
        return "__slot_{:03d}".format(index)
    finally:
        evaluated.to_mesh_clear()


def capture_lp_matching_input(context, pair, state):
    """Capture immutable HP groups and rematchable LP meshes on Blender's main thread."""
    depsgraph = context.evaluated_depsgraph_get()
    sample_cap = 4096 if state.optimization == "OPTIMAL" else 1024
    groups = []
    hp_seen = set()
    for subgroup in pair.subgroups:
        hp_objects = tuple(
            obj for obj in ObjectRepository.valid_members(subgroup, "HP")
            if ObjectRepository.classify(pair, obj) == "HP"
        )
        if not hp_objects:
            continue
        snapshots = []
        for obj in hp_objects:
            key = obj.name_full
            if key in hp_seen:
                raise ValueError("HP mesh belongs to multiple subgroups: {}".format(obj.name))
            hp_seen.add(key)
            snapshots.append(_snapshot_mesh(obj, depsgraph, sample_cap))
        groups.append(LPMatchGroup(name=subgroup.name, hp_meshes=tuple(snapshots)))
    if not groups:
        raise ValueError("No HP subgroups found; run Analyze HP first")

    locked_lp = {
        obj.as_pointer()
        for owner_pair in state.pairs
        for subgroup in owner_pair.subgroups if subgroup.locked
        for obj in ObjectRepository.valid_members(subgroup, "LP")
    }
    all_lp_objects = tuple(
        obj for obj in ObjectRepository.meshes_under_root(pair, "LP")
        if not obj.name.lower().endswith(("_hp", "_high"))
    )
    lp_objects = tuple(obj for obj in all_lp_objects if obj.as_pointer() not in locked_lp)
    snapshots = tuple(_snapshot_mesh(obj, depsgraph, sample_cap) for obj in lp_objects)
    object_by_key = {obj.name_full: obj for obj in lp_objects}
    material_key_by_lp = {
        obj.name_full: _dominant_material_key(obj, depsgraph) for obj in lp_objects
    }
    settings = LPMatchSettings(
        optimization=state.optimization,
        threshold_coefficient=1.5,
        bbox_padding=1.05,
    )
    return (
        tuple(groups), snapshots, settings, material_key_by_lp, object_by_key,
        len(all_lp_objects) - len(lp_objects),
    )


def apply_lp_matching_result(state, pair, result, object_by_key):
    """Replace unlocked LP membership only after a complete validated plan exists."""
    planned_keys = [key for assignment in result.assignments for key in assignment.lp_keys]
    if len(planned_keys) != len(set(planned_keys)):
        raise ValueError("Assign LP result assigns one LP object more than once")
    missing = [key for key in planned_keys if key not in object_by_key]
    if missing:
        raise ValueError("Assign LP result references missing objects: {}".format(", ".join(missing[:5])))
    by_name = {subgroup.name: subgroup for subgroup in pair.subgroups}
    unknown = [assignment.group_name for assignment in result.assignments if assignment.group_name not in by_name]
    if unknown:
        raise ValueError("Assign LP result references missing subgroups: {}".format(", ".join(unknown)))

    backups = [(owner_pair, _capture_membership(owner_pair)) for owner_pair in state.pairs]
    try:
        # Maya deletes/rebuilds only unlocked LP containers. Locked LP membership
        # survives and was excluded from the matching input upstream.
        for subgroup in pair.subgroups:
            if not subgroup.locked:
                subgroup.lp_members.clear()

        for key in planned_keys:
            obj = object_by_key[key]
            for owner_pair in state.pairs:
                ObjectRepository.remove_member_from_pair(owner_pair, obj)

        for assignment in result.assignments:
            subgroup = by_name[assignment.group_name]
            _add_refs(subgroup.lp_members, (object_by_key[key] for key in assignment.lp_keys))

        for owner_pair in state.pairs:
            ObjectRepository.sync_counts(owner_pair)
        ObjectRepository.sync_pair_visibility(state, pair)
    except Exception:
        for owner_pair, backup in backups:
            _restore_membership(owner_pair, backup)
        for owner_pair in state.pairs:
            ObjectRepository.sync_pair_visibility(state, owner_pair)
        raise
