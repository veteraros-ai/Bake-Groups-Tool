"""Blender-native Cage workflow matching the Maya Bake Groups tool.

Cages are history-free, topology-preserving copies of subgroup LP meshes.
Incremental expansion uses the source LP normal field, so +d followed by -d is
reversible and manual sculpting is preserved.  Managed collections replace the
Maya transform hierarchy without reparenting artist objects.
"""

from __future__ import annotations

import json
import math
import re

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from .object_repository import ObjectRepository


CAGE_ROOT = "BakeTools_Cages"
CAGE_MARKER = "bake_tools_cage"
CAGE_PAIR_ID = "bake_tools_pair_id"
CAGE_SUBGROUP_ID = "bake_tools_subgroup_id"
CAGE_SOURCE = "bake_tools_cage_source"
_SAFE = re.compile(r"[^0-9A-Za-zА-Яа-яЁё._-]+", re.UNICODE)


def _safe(value, fallback="Cage"):
    return _SAFE.sub("_", str(value or "").strip()).strip("._") or fallback


def _link_child(parent, child):
    if child.name not in parent.children:
        parent.children.link(child)


def _collection(scene, name, parent=None):
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
        collection["bake_tools_managed"] = True
    if parent is None:
        _link_child(scene.collection, collection)
    else:
        _link_child(parent, collection)
    return collection


def cage_objects(pair, subgroup_ids=()):
    pair_id = str(pair.item_id)
    scope = set(subgroup_ids or ())
    return tuple(
        obj for obj in bpy.data.objects
        if obj.type == "MESH"
        and bool(obj.get(CAGE_MARKER, False))
        and str(obj.get(CAGE_PAIR_ID, "")) == pair_id
        and (not scope or str(obj.get(CAGE_SUBGROUP_ID, "")) in scope)
    )


def _subgroups(pair, subgroup_ids=()):
    scope = set(subgroup_ids or ())
    groups = tuple(group for group in pair.subgroups if not scope or group.item_id in scope)
    return groups or tuple(pair.subgroups)


def _source(cage):
    name = str(cage.get(CAGE_SOURCE, ""))
    return bpy.data.objects.get(name) if name else None


def _ensure_object_mode(targets=()):
    active = bpy.context.object
    if active is not None and active.mode != "OBJECT" and (not targets or active in targets):
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except RuntimeError:
            pass


def _new_cage(context, pair, subgroup, source, subgroup_collection):
    # A cage starts as an exact, topology-preserving copy of the authored LP
    # mesh.  Copying the evaluated depsgraph object baked every source modifier
    # (Subdivision, Displace, Geometry Nodes, etc.) into the cage, which was
    # both very slow and looked like an unexplained initial inflation.  A raw
    # Mesh datablock copy is Blender's equivalent of Maya's history-free LP
    # duplicate and keeps vertex indices stable for later normal expansion.
    mesh = source.data.copy()
    mesh.name = "{}_cage_mesh".format(source.name)
    cage = bpy.data.objects.new("{}_cage".format(source.name), mesh)
    source_world = source.matrix_world.copy()
    cage[CAGE_MARKER] = True
    cage[CAGE_PAIR_ID] = str(pair.item_id)
    cage[CAGE_SUBGROUP_ID] = str(subgroup.item_id)
    cage[CAGE_SOURCE] = str(source.name)
    cage.color = (0.62, 0.28, 0.80, 1.0)
    subgroup_collection.objects.link(cage)
    # Assign after linking: Blender can re-evaluate an unlinked object's
    # transform on first collection insertion.  Post-link assignment guarantees
    # the Cage stays at the LP world position instead of appearing at origin.
    cage.matrix_world = source_world
    return cage


def apply_display(state, pair=None):
    targets = cage_objects(pair) if pair is not None else tuple(
        obj for obj in bpy.data.objects if bool(obj.get(CAGE_MARKER, False))
    )
    wire = bool(state.cage_wire)
    for cage in targets:
        cage.display_type = "WIRE" if wire else "SOLID"
        cage.show_wire = wire
        cage.show_all_edges = wire
        cage.color = (0.62, 0.28, 0.80, 1.0) if wire else (0.5, 0.5, 0.5, 1.0)
    return len(targets)


def sync_visibility(state, pair=None):
    pairs = (pair,) if pair is not None else tuple(state.pairs)
    active_id = str(state.active_pair_id)
    for owner in pairs:
        groups = {group.item_id: group for group in owner.subgroups}
        pair_gate = bool(
            state.final_view and owner.cage_visible and owner.visible
            and (not state.chapter_isolated or owner.item_id == active_id)
        )
        for cage in cage_objects(owner):
            subgroup = groups.get(str(cage.get(CAGE_SUBGROUP_ID, "")))
            visible = bool(pair_gate and state.groups_visible and subgroup and subgroup.visible)
            cage.hide_viewport = not visible
            cage.hide_render = not visible
            try:
                cage.hide_set(not visible)
            except RuntimeError:
                pass


def create_cages(context, state, pair, subgroup_ids=(), progress=None):
    groups = _subgroups(pair, subgroup_ids)
    targets = [(group, obj) for group in groups for obj in ObjectRepository.valid_members(group, "LP")]
    if not targets:
        raise ValueError("Assign LP meshes to the selected subgroup(s) before Create Cage")
    _ensure_object_mode()
    context.view_layer.update()
    delete_cages(state, pair, tuple(group.item_id for group in groups))
    root = _collection(context.scene, CAGE_ROOT)
    chapter = _collection(context.scene, "BakeTools_Cage_{}".format(_safe(pair.name)), root)
    subgroup_collections = {
        group.item_id: _collection(
            context.scene,
            "BakeTools_Cage_{}_{}".format(_safe(pair.name), _safe(group.name)),
            chapter,
        )
        for group in groups
    }
    created = []
    # Qt progress handling pumps both event systems, so reporting every object
    # dominated Cage creation on large chapters.  Twenty evenly spaced updates
    # keep the UI informative without serialising hundreds of event-pump calls.
    progress_stride = max(1, int(math.ceil(len(targets) / 20.0)))
    for index, (group, source) in enumerate(targets):
        if source is None or source.type != "MESH":
            continue
        created.append(_new_cage(
            context, pair, group, source, subgroup_collections[group.item_id]
        ))
        if progress and ((index + 1) % progress_stride == 0 or index + 1 == len(targets)):
            progress.update(
                int((index + 1) * 100 / max(1, len(targets))),
                "Creating Cage: {}".format(source.name),
            )
    state.cage_inflate = 0.0
    pair.cage_visible = True
    state.cage_intersections_json = "{}"
    state.cage_status = "Created {} deflated Cage mesh(es).".format(len(created))
    apply_display(state, pair)
    sync_visibility(state, pair)
    return tuple(created)


def delete_cages(state, pair, subgroup_ids=()):
    targets = cage_objects(pair, subgroup_ids)
    _ensure_object_mode(targets)
    for cage in targets:
        mesh = cage.data
        bpy.data.objects.remove(cage, do_unlink=True)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    state.cage_intersections_json = "{}"
    state.cage_status = "Deleted {} Cage mesh(es).".format(len(targets))
    return len(targets)


def _chapter_diagonal(pair):
    """World-space diagonal of all unique HP/LP meshes owned by a chapter."""
    points = []
    objects = list(ObjectRepository.meshes_under_roots(pair))
    seen = {obj.as_pointer() for obj in objects}
    # Membership is the durable source of truth after distribution and also
    # covers migrated/test scenes whose original root pointer is unavailable.
    for subgroup in pair.subgroups:
        for obj in ObjectRepository.all_members(subgroup):
            if obj is None or obj.type != "MESH" or obj.as_pointer() in seen:
                continue
            seen.add(obj.as_pointer())
            objects.append(obj)
    for obj in objects:
        matrix = obj.matrix_world
        points.extend(matrix @ Vector(corner) for corner in obj.bound_box)
    if not points:
        return 1.0
    low = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    high = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    return max((high - low).length, 1.0e-9)


def resolve_delta(state, pair, value):
    amount = float(value)
    return amount if state.cage_unit == "ABSOLUTE" else _chapter_diagonal(pair) * amount / 100.0


def expand_cages(state, pair, value, subgroup_ids=()):
    delta = resolve_delta(state, pair, value)
    targets = cage_objects(pair, subgroup_ids)
    if not targets:
        raise ValueError("Create Cage before changing Expansion")
    _ensure_object_mode(targets)
    if abs(delta) < 1.0e-12:
        return 0
    changed = 0
    for cage in targets:
        source = _source(cage)
        source_mesh = source.data if source is not None and source.type == "MESH" else None
        use_source = source_mesh is not None and len(source_mesh.vertices) == len(cage.data.vertices)
        source_matrix = source.matrix_world if use_source else cage.matrix_world
        cage_inv = cage.matrix_world.inverted_safe()
        for index, vertex in enumerate(cage.data.vertices):
            normal = source_mesh.vertices[index].normal if use_source else vertex.normal
            world_normal = source_matrix.to_3x3() @ normal
            if world_normal.length_squared == 0.0:
                continue
            world_normal.normalize()
            world_position = cage.matrix_world @ vertex.co
            vertex.co = cage_inv @ (world_position + world_normal * delta)
        cage.data.update()
        changed += 1
    state.cage_inflate = max(0.0, float(state.cage_inflate) + float(value))
    state.cage_status = "Expanded {} Cage mesh(es) by {:.4f}.".format(changed, delta)
    return changed


def sculpt_cage(context, state, pair, subgroup_ids=()):
    targets = cage_objects(pair, subgroup_ids)
    if not targets:
        raise ValueError("Create Cage before Sculpt Cage")
    if context.object is not None and context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for cage in targets:
        cage.hide_viewport = False
        cage.hide_set(False)
    ObjectRepository.select_objects(context, targets)
    context.view_layer.objects.active = targets[0]
    bpy.ops.object.mode_set(mode="SCULPT")
    state.cage_status = "Sculpt Mode: {}".format(targets[0].name)
    return targets[0]


def _world_bvh(context, obj):
    depsgraph = context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=False, depsgraph=depsgraph)
    try:
        matrix = evaluated.matrix_world
        vertices = [matrix @ vertex.co for vertex in mesh.vertices]
        polygons = [tuple(polygon.vertices) for polygon in mesh.polygons]
        return BVHTree.FromPolygons(vertices, polygons, all_triangles=False) if polygons else None
    finally:
        evaluated.to_mesh_clear()


def _intersection_islands(context, cage, hp_objects):
    bvhs = [bvh for bvh in (_world_bvh(context, obj) for obj in hp_objects) if bvh is not None]
    if not bvhs:
        return []
    mesh = cage.data
    matrix = cage.matrix_world
    edge_faces = {tuple(sorted(edge.vertices)): [] for edge in mesh.edges}
    for polygon in mesh.polygons:
        for edge_key in polygon.edge_keys:
            edge_faces.setdefault(tuple(sorted(edge_key)), []).append(polygon.index)
    hit_faces = set()
    for edge in mesh.edges:
        start = matrix @ mesh.vertices[edge.vertices[0]].co
        end = matrix @ mesh.vertices[edge.vertices[1]].co
        direction = end - start
        length = direction.length
        if length <= 1.0e-10:
            continue
        direction.normalize()
        if any(
            (hit := bvh.ray_cast(start, direction, length)) and hit[0] is not None
            and hit[3] is not None and hit[3] <= length + 1.0e-7
            for bvh in bvhs
        ):
            hit_faces.update(edge_faces.get(tuple(sorted(edge.vertices)), ()))
    if not hit_faces:
        return []
    adjacency = {face: set() for face in hit_faces}
    for faces in edge_faces.values():
        linked = [face for face in faces if face in hit_faces]
        for face in linked:
            adjacency[face].update(other for other in linked if other != face)
    islands = []
    unseen = set(hit_faces)
    while unseen:
        seed = unseen.pop(); stack = [seed]; faces = []
        while stack:
            face = stack.pop(); faces.append(face)
            neighbours = adjacency[face] & unseen
            unseen.difference_update(neighbours); stack.extend(neighbours)
        vertices = sorted({index for face in faces for index in mesh.polygons[face].vertices})
        normal = Vector((0.0, 0.0, 0.0))
        for face in faces:
            normal += mesh.polygons[face].normal * max(mesh.polygons[face].area, 1.0e-9)
        if normal.length_squared:
            normal.normalize()
        islands.append({
            "faces": sorted(faces), "verts": vertices,
            "normal": [float(normal.x), float(normal.y), float(normal.z)],
        })
    return islands


def find_intersections(context, state, pair, subgroup_ids=(), progress=None):
    groups = {group.item_id: group for group in pair.subgroups}
    targets = cage_objects(pair, subgroup_ids)
    if not targets:
        raise ValueError("Create Cage before finding intersections")
    _ensure_object_mode(targets)
    payload = {}
    total_islands = 0
    for index, cage in enumerate(targets):
        subgroup = groups.get(str(cage.get(CAGE_SUBGROUP_ID, "")))
        hp = ObjectRepository.valid_members(subgroup, "HP") if subgroup is not None else ()
        islands = _intersection_islands(context, cage, hp)
        payload[cage.name] = islands
        total_islands += len(islands)
        for polygon in cage.data.polygons:
            polygon.select = False
        for island in islands:
            for face in island["faces"]:
                if face < len(cage.data.polygons):
                    cage.data.polygons[face].select = True
        if progress:
            progress.update(
                int((index + 1) * 100 / max(1, len(targets))),
                "Checking Cage: {}".format(cage.name),
            )
    state.cage_intersections_json = json.dumps(payload, separators=(",", ":"))
    ObjectRepository.select_objects(context, [cage for cage in targets if payload.get(cage.name)])
    state.cage_status = "Found {} Cage/HP intersection island(s).".format(total_islands)
    return total_islands


def move_intersections(state, pair, value, subgroup_ids=()):
    delta = resolve_delta(state, pair, value)
    try:
        payload = json.loads(state.cage_intersections_json or "{}")
    except (TypeError, ValueError):
        payload = {}
    targets = cage_objects(pair, subgroup_ids)
    if not targets:
        raise ValueError("Create Cage before Normal move")
    _ensure_object_mode(targets)
    if not any(payload.get(cage.name) for cage in targets):
        raise ValueError("Run Find Intersections before Normal move")
    moved = 0
    for cage in targets:
        islands = payload.get(cage.name, ())
        for island in islands:
            normal = Vector(island.get("normal", (0.0, 0.0, 0.0)))
            if normal.length_squared == 0.0:
                continue
            normal.normalize()
            for index in island.get("verts", ()):
                if 0 <= int(index) < len(cage.data.vertices):
                    cage.data.vertices[int(index)].co += normal * delta
                    moved += 1
        cage.data.update()
    state.cage_status = "Normal move: {} Cage vertex operation(s), {:.4f}.".format(moved, delta)
    return moved
