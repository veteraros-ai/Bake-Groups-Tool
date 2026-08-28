"""Import an artist-authored Blender HP hierarchy as subgroup membership."""

from __future__ import annotations

import re
from uuid import uuid4

import bpy

from .object_repository import ObjectRepository


_SIDE_SUFFIX = re.compile(r"_(?:HP|LP)(\d*)$", re.IGNORECASE)


def _ui_name(name):
    match = _SIDE_SUFFIX.search(name)
    if not match:
        return name
    return name[:match.start()] + match.group(1)


def _unique_meshes(objects):
    result = []
    seen = set()
    for obj in objects:
        if obj.type != "MESH":
            continue
        key = obj.as_pointer()
        if key not in seen:
            seen.add(key)
            result.append(obj)
    return tuple(result)


def discover_hp_structure(pair):
    """Return immediate HP hierarchy groups as ``(visible name, meshes)``."""
    root = ObjectRepository.root(pair, "HP")
    if root is None:
        return ()
    found = []
    if isinstance(root, bpy.types.Collection):
        for child in root.children:
            meshes = _unique_meshes(child.all_objects)
            if meshes:
                found.append((_ui_name(child.name), meshes))
        for child in root.objects:
            if child.type == "MESH":
                continue
            meshes = _unique_meshes(ObjectRepository.descendants(child))
            if meshes:
                found.append((_ui_name(child.name), meshes))
    else:
        for child in root.children:
            if child.type == "MESH":
                continue
            meshes = _unique_meshes(ObjectRepository.descendants(child))
            if meshes:
                found.append((_ui_name(child.name), meshes))

    # The same hierarchy can be reachable through a child Collection and an
    # Empty. Prefer the first named group and never duplicate a mesh.
    claimed = set()
    result = []
    for name, meshes in found:
        available = tuple(obj for obj in meshes if obj.as_pointer() not in claimed)
        if not available:
            continue
        claimed.update(obj.as_pointer() for obj in available)
        result.append((name or "Subgroup", available))
    return tuple(result)


def preserve_hp_structure(state, pair):
    """Synchronize discovered hierarchy into metadata without reparenting meshes."""
    discovered = discover_hp_structure(pair)
    if not discovered:
        ObjectRepository.prune_missing(pair)
        existing = sum(1 for subgroup in pair.subgroups if ObjectRepository.valid_members(subgroup, "HP"))
        meshes = sum(len(ObjectRepository.valid_members(subgroup, "HP")) for subgroup in pair.subgroups)
        return existing, meshes, False

    desired_names = {name for name, _meshes in discovered}
    for subgroup in pair.subgroups:
        subgroup.hp_members.clear()
    for index in range(len(pair.subgroups) - 1, -1, -1):
        subgroup = pair.subgroups[index]
        if subgroup.name not in desired_names and not ObjectRepository.valid_members(subgroup, "LP"):
            pair.subgroups.remove(index)

    by_name = {subgroup.name: subgroup for subgroup in pair.subgroups}
    mesh_count = 0
    for name, meshes in discovered:
        subgroup = by_name.get(name)
        if subgroup is None:
            subgroup = pair.subgroups.add()
            subgroup.item_id = uuid4().hex
            subgroup.name = name
            by_name[name] = subgroup
        for obj in meshes:
            for owner_pair in state.pairs:
                ObjectRepository.remove_member_from_pair(owner_pair, obj)
            ref = subgroup.hp_members.add()
            ref.target = obj
            ref.last_name = obj.name
            mesh_count += 1

    for owner_pair in state.pairs:
        ObjectRepository.sync_counts(owner_pair)
    return len(discovered), mesh_count, True
