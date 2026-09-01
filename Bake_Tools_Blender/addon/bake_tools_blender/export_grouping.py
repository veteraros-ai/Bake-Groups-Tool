"""Non-destructive Blender Collection mirror of chapter subgroup membership.

Maya stores each subgroup as a DAG transform.  Bake Tools' Blender data model
keeps Object pointers instead so artist parenting, rigs and source Collections
remain untouched.  Export Settings materializes a supplementary managed
Collection hierarchy, giving the Outliner the same useful grouping without
moving or duplicating scene geometry.
"""

from __future__ import annotations

import re

import bpy

from .object_repository import ObjectRepository


_SAFE_NAME = re.compile(r"[^0-9A-Za-zА-Яа-яЁё._-]+", re.UNICODE)
_MANAGED = "bake_tools_managed"
_ROLE = "bake_tools_collection_role"
_PAIR = "bake_tools_pair_id"
_SUBGROUP = "bake_tools_subgroup_id"
_SIDE = "bake_tools_side"


def _safe(value, fallback):
    result = _SAFE_NAME.sub("_", str(value or "").strip()).strip("._")
    return result or fallback


def _matches(collection, role, pair_id="", subgroup_id="", side=""):
    return bool(
        collection.get(_MANAGED, False)
        and str(collection.get(_ROLE, "")) == role
        and str(collection.get(_PAIR, "")) == str(pair_id or "")
        and str(collection.get(_SUBGROUP, "")) == str(subgroup_id or "")
        and str(collection.get(_SIDE, "")) == str(side or "")
    )


def _ensure_link(parent, child):
    if child.name not in parent.children:
        parent.children.link(child)


def _managed_collection(parent, name, role, pair_id="", subgroup_id="", side=""):
    collection = next(
        (
            item for item in bpy.data.collections
            if _matches(item, role, pair_id, subgroup_id, side)
        ),
        None,
    )
    if collection is None:
        collection = bpy.data.collections.new(name)
    collection[_MANAGED] = True
    collection[_ROLE] = role
    collection[_PAIR] = str(pair_id or "")
    collection[_SUBGROUP] = str(subgroup_id or "")
    collection[_SIDE] = str(side or "")
    collection.name = name
    _ensure_link(parent, collection)
    return collection


def _sync_objects(collection, objects):
    expected = {obj.as_pointer(): obj for obj in objects if obj is not None and obj.type == "MESH"}
    for obj in tuple(collection.objects):
        if obj.as_pointer() not in expected:
            collection.objects.unlink(obj)
    for obj in expected.values():
        if obj.name not in collection.objects:
            collection.objects.link(obj)
    return len(expected)


def _remove_collection(collection):
    if not collection.get(_MANAGED, False):
        return
    for parent in bpy.data.collections:
        if collection.name in parent.children:
            parent.children.unlink(collection)
    for scene in bpy.data.scenes:
        if collection.name in scene.collection.children:
            scene.collection.children.unlink(collection)
    bpy.data.collections.remove(collection)


def synchronize_export_grouping(scene, pair):
    """Mirror one chapter's current HP/LP subgroup membership in Collections."""
    if scene is None or pair is None:
        return {"hp": 0, "lp": 0, "collections": 0, "removed": 0}

    # Scoped chapters must include every subgroup member on the corresponding
    # side.  This repairs older scenes and keeps externally added meshes inside
    # the chapter boundary before export planning starts.
    if getattr(pair, "scope_by_members", False):
        for subgroup in pair.subgroups:
            for side in ("HP", "LP"):
                for obj in ObjectRepository.valid_members(subgroup, side):
                    ObjectRepository.assign_scope_side(pair, obj, side)

    pair_id = str(pair.item_id)
    base = _safe(pair.name, "Chapter")
    root = _managed_collection(scene.collection, "BakeTools", "ROOT")
    chapters = _managed_collection(root, "BakeTools_Chapters", "CHAPTERS")
    chapter = _managed_collection(
        chapters, "BakeTools_{}".format(base), "CHAPTER", pair_id=pair_id
    )

    expected_keys = set()
    counts = {"HP": 0, "LP": 0}
    collection_count = 3
    for side in ("HP", "LP"):
        side_collection = _managed_collection(
            chapter, "BakeTools_{}_{}".format(base, side), "SIDE",
            pair_id=pair_id, side=side,
        )
        collection_count += 1
        for subgroup in pair.subgroups:
            subgroup_id = str(subgroup.item_id)
            expected_keys.add((subgroup_id, side))
            subgroup_collection = _managed_collection(
                side_collection,
                "BakeTools_{}_{}_{}".format(base, _safe(subgroup.name, "Group"), side),
                "SUBGROUP", pair_id=pair_id, subgroup_id=subgroup_id, side=side,
            )
            counts[side] += _sync_objects(
                subgroup_collection, ObjectRepository.valid_members(subgroup, side)
            )
            collection_count += 1

    stale = tuple(
        collection for collection in bpy.data.collections
        if collection.get(_MANAGED, False)
        and str(collection.get(_ROLE, "")) == "SUBGROUP"
        and str(collection.get(_PAIR, "")) == pair_id
        and (
            str(collection.get(_SUBGROUP, "")),
            str(collection.get(_SIDE, "")),
        ) not in expected_keys
    )
    for collection in stale:
        _remove_collection(collection)

    return {
        "hp": counts["HP"],
        "lp": counts["LP"],
        "collections": collection_count,
        "removed": len(stale),
    }

