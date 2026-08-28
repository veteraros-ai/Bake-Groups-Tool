"""Blender-native modelling helpers for the original Bake Tools tool row."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import struct

import bpy
from mathutils import Matrix

from .object_repository import ObjectRepository


ZBRUSH_COLLECTION_NAME = "BakeTools_ZBrush_Layer"
ZBRUSH_MARKER = "bake_tools_zbrush"
# Maya compares world-space points with a tolerance of 0.001 centimetres.
# Blender world coordinates are expressed in scene units, whose physical size
# is ``unit_settings.scale_length`` metres.  Using 0.001 Blender units (the old
# port behavior) therefore made the check 100x too loose in a default metric
# scene and collapsed distinct nearby vertices into the same digest.
MAYA_DUPLICATE_TOLERANCE_METERS = 0.00001


def is_zbrush_object(state, obj):
    """Return whether *obj* was explicitly registered as a ZBrush mesh.

    The Blender object marker is the single source of truth for the user's
    ZBrush decision.  The scene registry and managed collection are indexes
    used for selection and persistence; stale entries in either must not turn
    an otherwise ordinary mesh into ZBrush geometry and suppress subdivision.
    A subgroup whose *name* contains ``ZBrush`` is likewise not a marker.
    """
    if obj is None or obj.type != "MESH":
        return False
    return bool(obj.get(ZBRUSH_MARKER, False))


def duplicate_check_tolerance(scene):
    """Return Maya's 0.001 cm duplicate tolerance in Blender scene units."""
    unit_scale = float(getattr(getattr(scene, "unit_settings", None), "scale_length", 1.0) or 1.0)
    return max(1.0e-9, MAYA_DUPLICATE_TOLERANCE_METERS / max(unit_scale, 1.0e-12))


def _deselect_all(context):
    for obj in tuple(context.selected_objects):
        try:
            obj.select_set(False)
        except (ReferenceError, RuntimeError):
            pass


def _selected_meshes(context):
    meshes = [
        obj for obj in context.selected_objects
        if obj.type == "MESH" and obj.library is None and obj.name in context.view_layer.objects
    ]
    active = context.view_layer.objects.active
    if active in meshes:
        meshes.remove(active)
        meshes.insert(0, active)
    return meshes


def _add_ref(refs, obj):
    if any(ref.target == obj for ref in refs if ref.target is not None):
        return False
    ref = refs.add()
    ref.target = obj
    ref.last_name = obj.name
    return True


def _prune_project_refs(state):
    for pair in state.pairs:
        for refs in (pair.hp_scope_members, pair.lp_scope_members):
            for index in range(len(refs) - 1, -1, -1):
                if refs[index].target is None:
                    refs.remove(index)
        ObjectRepository.prune_missing(pair)
    for index in range(len(state.zbrush_members) - 1, -1, -1):
        if state.zbrush_members[index].target is None:
            state.zbrush_members.remove(index)


def _membership_records(state, obj):
    records = []
    for pair in state.pairs:
        subgroup, side = ObjectRepository.membership(pair, obj)
        if subgroup is not None:
            records.append((pair, subgroup, side))
    return records


def _copy_scope_membership(state, sources, results):
    source_set = set(sources)
    for pair in state.pairs:
        for refs in (pair.hp_scope_members, pair.lp_scope_members):
            if not any(ref.target in source_set for ref in refs if ref.target is not None):
                continue
            for obj in results:
                _add_ref(refs, obj)


def zbrush_collection(scene, create=False):
    collection = bpy.data.collections.get(ZBRUSH_COLLECTION_NAME)
    if collection is None and create:
        collection = bpy.data.collections.new(ZBRUSH_COLLECTION_NAME)
        collection["bake_tools_managed"] = True
    if collection is not None and collection.name not in scene.collection.children:
        scene.collection.children.link(collection)
    return collection


def zbrush_objects(context, state):
    _prune_project_refs(state)
    result = []
    seen = set()
    scene_objects = set(context.scene.objects)
    for ref in state.zbrush_members:
        obj = ref.target
        if obj is None or obj not in scene_objects or obj.type != "MESH":
            continue
        key = obj.as_pointer()
        if key not in seen:
            seen.add(key)
            result.append(obj)
    return result


def mark_zbrush_objects(context, state, objects):
    collection = zbrush_collection(context.scene, create=True)
    added = []
    for obj in objects:
        if obj is None or obj.type != "MESH" or obj.name not in context.scene.objects:
            continue
        if _add_ref(state.zbrush_members, obj):
            added.append(obj)
        obj[ZBRUSH_MARKER] = True
        if obj.name not in collection.objects:
            collection.objects.link(obj)
    return added


def add_selected_to_zbrush(context, state):
    pair = None
    if state.active_pair_id:
        pair = next((item for item in state.pairs if item.item_id == state.active_pair_id), None)
    if pair is None:
        raise ValueError("Create or select a chapter before adding ZBrush meshes")
    hp_root = ObjectRepository.root(pair, "HP")
    selected = [
        obj for obj in ObjectRepository.selected_meshes(context)
        if ObjectRepository.is_in_root(obj, hp_root)
    ]
    if not selected:
        raise ValueError("Select HP mesh objects under the active chapter root")
    added = mark_zbrush_objects(context, state, selected)
    ObjectRepository.select_objects(context, selected)
    return selected, added


def select_zbrush_objects(context, state):
    return ObjectRepository.select_objects(context, zbrush_objects(context, state))


def _triangle_ratio(obj, depsgraph):
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=False, depsgraph=depsgraph)
    try:
        total = len(mesh.polygons)
        triangles = sum(1 for polygon in mesh.polygons if len(polygon.vertices) == 3)
        return ((triangles / float(total)) * 100.0 if total else 0.0), triangles, total
    finally:
        evaluated.to_mesh_clear()


def find_zbrush_candidates(context, state, pair, progress=None):
    threshold = int(state.zbrush_triangle_threshold)
    depsgraph = context.evaluated_depsgraph_get()
    found = []
    best = 0.0
    objects = tuple(ObjectRepository.meshes_under_root(pair, "HP"))
    for index, obj in enumerate(objects):
        ratio, _triangles, total = _triangle_ratio(obj, depsgraph)
        # Maya Find ZBrush is intentionally topology-only.  Names such as
        # ZBrush_Huge_001 describe a subgroup, not proof that this mesh came
        # from ZBrush, and must never bypass the triangular-face threshold.
        if total and ratio >= threshold:
            found.append(obj)
            best = max(best, ratio)
        if progress:
            progress.update(int((index + 1) * 100 / max(1, len(objects))), "Checking ZBrush topology: {}".format(obj.name))
    ObjectRepository.select_objects(context, found)
    return found, best


def _ensure_membership_for_results(state, records, results):
    for pair, subgroup, side in records:
        refs = getattr(subgroup, "{}_members".format(side.lower()))
        for obj in results:
            _add_ref(refs, obj)
    for pair in state.pairs:
        ObjectRepository.sync_counts(pair)


def combine_selected(context, state):
    meshes = _selected_meshes(context)
    if len(meshes) < 2:
        raise ValueError("Select at least two editable mesh objects to combine")
    primary = meshes[0]
    first_name = primary.name
    records = _membership_records(state, primary)
    if not records:
        records = next((_membership_records(state, obj) for obj in meshes[1:] if _membership_records(state, obj)), [])
    was_zbrush = any(bool(obj.get(ZBRUSH_MARKER)) or any(ref.target == obj for ref in state.zbrush_members) for obj in meshes)
    _copy_scope_membership(state, meshes, (primary,))

    if context.object is not None and context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    _deselect_all(context)
    for obj in meshes:
        obj.hide_set(False)
        obj.select_set(True)
    context.view_layer.objects.active = primary
    result = bpy.ops.object.join()
    if "FINISHED" not in result:
        raise RuntimeError("Blender Join operation failed")
    primary.name = "{}_Combined".format(first_name)

    _prune_project_refs(state)
    _ensure_membership_for_results(state, records, (primary,))
    if was_zbrush:
        mark_zbrush_objects(context, state, (primary,))
    ObjectRepository.select_objects(context, (primary,))
    return primary, len(meshes)


def separate_selected(context, state, progress=None):
    sources = _selected_meshes(context)
    if not sources:
        raise ValueError("Select at least one editable mesh object to separate")
    final = []
    separated_sources = 0
    for source_index, source in enumerate(sources):
        if source.name not in context.view_layer.objects:
            continue
        base_name = source.name
        records = _membership_records(state, source)
        was_zbrush = bool(source.get(ZBRUSH_MARKER)) or any(
            ref.target == source for ref in state.zbrush_members if ref.target is not None
        )
        _deselect_all(context)
        source.hide_set(False)
        source.select_set(True)
        context.view_layer.objects.active = source
        if source.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        result = bpy.ops.mesh.separate(type="LOOSE")
        bpy.ops.object.mode_set(mode="OBJECT")
        if "FINISHED" not in result:
            continue
        parts = [obj for obj in context.selected_objects if obj.type == "MESH"]
        if len(parts) < 2:
            continue
        parts.sort(key=lambda obj: (obj != source, obj.name.lower()))
        separated_sources += 1
        for index, part in enumerate(parts, 1):
            part.name = "{}_Part{}".format(base_name, index)
        _copy_scope_membership(state, (source,), parts)
        _ensure_membership_for_results(state, records, parts)
        if was_zbrush:
            mark_zbrush_objects(context, state, parts)
        final.extend(parts)
        if progress:
            progress.update(int((source_index + 1) * 100 / max(1, len(sources))), "Separating: {}".format(base_name))

    _prune_project_refs(state)
    ObjectRepository.select_objects(context, final)
    if not final:
        raise ValueError("Selected meshes do not contain independent loose parts")
    return final, separated_sources


@dataclass(frozen=True, slots=True)
class MeshFacts:
    vertex_count: int
    edge_count: int
    face_count: int
    bbox_key: tuple[int, ...]
    vertex_digest: str
    triangle_ratio: float
    meaningful_components: int


@dataclass(frozen=True, slots=True)
class MeshCheckResult:
    report: str
    issue_objects: tuple
    transform_objects: tuple
    duplicate_groups: tuple[tuple, ...]
    zbrush_candidates: tuple
    combined_meshes: tuple

    @property
    def issue_count(self):
        return len(self.issue_objects)

    def payload(self, pair_id):
        return {
            "pair_id": str(pair_id or ""),
            "transforms": [obj.name for obj in self.transform_objects],
            "duplicates": [[obj.name for obj in group] for group in self.duplicate_groups],
            "zbrush": [obj.name for obj in self.zbrush_candidates],
            "combined": [obj.name for obj in self.combined_meshes],
            "issues": [obj.name for obj in self.issue_objects],
        }


def _mesh_facts(obj, depsgraph, tolerance=0.001):
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=False, depsgraph=depsgraph)
    try:
        matrix = evaluated.matrix_world
        scale = 1.0 / max(float(tolerance), 1e-9)
        digest = hashlib.sha1()
        minimum = [float("inf")] * 3
        maximum = [float("-inf")] * 3
        for vertex in mesh.vertices:
            point = matrix @ vertex.co
            quantized = tuple(int(round(float(point[axis]) * scale)) for axis in range(3))
            digest.update(struct.pack("<qqq", *quantized))
            for axis in range(3):
                minimum[axis] = min(minimum[axis], float(point[axis]))
                maximum[axis] = max(maximum[axis], float(point[axis]))
        if not mesh.vertices:
            minimum = maximum = [0.0, 0.0, 0.0]
        bbox_key = tuple(
            int(round(value * scale)) for value in (minimum + maximum)
        )

        parent = list(range(len(mesh.vertices)))

        def find(index):
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        def union(a, b):
            root_a, root_b = find(a), find(b)
            if root_a != root_b:
                parent[root_b] = root_a

        for edge in mesh.edges:
            union(int(edge.vertices[0]), int(edge.vertices[1]))
        component_faces = {}
        for polygon in mesh.polygons:
            if polygon.vertices:
                root = find(int(polygon.vertices[0]))
                component_faces[root] = component_faces.get(root, 0) + 1
        min_faces = max(6, int(len(mesh.polygons) * 0.0002))
        meaningful = sum(1 for count in component_faces.values() if count >= min_faces)
        triangles = sum(1 for polygon in mesh.polygons if len(polygon.vertices) == 3)
        ratio = (triangles / float(len(mesh.polygons)) * 100.0) if mesh.polygons else 0.0
        return MeshFacts(
            vertex_count=len(mesh.vertices),
            edge_count=len(mesh.edges),
            face_count=len(mesh.polygons),
            bbox_key=bbox_key,
            vertex_digest=digest.hexdigest(),
            triangle_ratio=ratio,
            meaningful_components=meaningful,
        )
    finally:
        evaluated.to_mesh_clear()


def _transform_candidates(pair):
    """Return HP/LP mesh transforms once each, never chapter containers.

    FBX roots commonly carry the import axis conversion and unit scale.  They
    are hierarchy containers, not mesh transforms.  Freezing such an Empty can
    re-evaluate the entire HP/LP branch and move it as a whole, even when each
    child is compensated afterwards.  Maya freezes the mesh transform/shape
    boundary, so the Blender port must leave Object/Collection roots untouched.
    """
    result = []
    seen = set()
    for side in ("HP", "LP"):
        for obj in ObjectRepository.meshes_under_root(pair, side):
            if obj is None or obj.as_pointer() in seen:
                continue
            seen.add(obj.as_pointer())
            result.append(obj)
    return tuple(result)


def _matrix_has_transform(matrix, tolerance=1.0e-6):
    return any(
        abs(float(matrix[row][column]) - (1.0 if row == column else 0.0)) > tolerance
        for row in range(4) for column in range(4)
    )


def _has_unapplied_transform(obj, tolerance=1.0e-6):
    # A child may have an identity matrix_basis while inheriting its complete
    # displacement from an HP/LP root.  The Maya contract is stricter than
    # Blender's local Transform panel: both the authored local values and the
    # final world-space origin must be frozen.
    return (
        _matrix_has_transform(obj.matrix_basis, tolerance)
        or _matrix_has_transform(obj.matrix_world, tolerance)
    )


def _hierarchy_depth(obj):
    depth = 0
    parent = obj.parent
    while parent is not None:
        depth += 1
        parent = parent.parent
    return depth


def apply_check_transforms(context, state, pair):
    """Freeze checked object transforms without moving visible geometry.

    Bake each mesh's complete original ``matrix_world`` into its raw vertices,
    then make the object world matrix identity.  Consequently its origin is at
    world (0, 0, 0), Location/Rotation are zero and Scale is one, while every
    vertex remains at the same world-space position.  ``matrix_parent_inverse``
    is rebuilt so the contract also holds for parented meshes.

    Blender's multi-object ``transform_apply`` cannot safely do this when both
    parented meshes are selected.  We therefore snapshot first, process
    parent-to-child and restore direct child world matrices after each mutation.
    HP/LP Empty roots are deliberately excluded and keep their imported axis
    conversion, unit scale and hierarchy state unchanged.
    """
    payload = decode_check_payload(state, pair)
    names = {str(name) for name in payload.get("transforms", ())}
    targets = [
        obj for obj in _transform_candidates(pair)
        if obj.name in names and _has_unapplied_transform(obj)
    ]
    original_world = {obj: obj.matrix_world.copy() for obj in targets}
    child_world = {
        child: child.matrix_world.copy()
        for obj in targets for child in obj.children
    }
    freezable = {
        obj for obj in targets
        if obj.library is None and (obj.data is None or obj.data.library is None)
    }
    fixed = []
    skipped = []
    for obj in sorted(targets, key=_hierarchy_depth):
        if obj.library is not None or (obj.data is not None and obj.data.library is not None):
            skipped.append(obj)
            continue
        old_world = original_world[obj]
        if obj.type == "MESH" and obj.data is not None and obj.data.users > 1:
            obj.data = obj.data.copy()
        if obj.type == "MESH" and obj.data is not None:
            obj.data.transform(old_world, shape_keys=True)
            obj.data.update()
        # Clear delta channels before assigning matrix_basis.  Assigning an
        # identity basis while deltas are still active makes Blender hide their
        # inverse in Location/Rotation/Scale instead of truly freezing them.
        obj.delta_location = (0.0, 0.0, 0.0)
        obj.delta_rotation_euler = (0.0, 0.0, 0.0)
        obj.delta_rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        obj.delta_scale = (1.0, 1.0, 1.0)
        if obj.parent is None:
            parent_world = Matrix.Identity(4)
        elif obj.parent in freezable:
            # Its data-block has already been reset above, but Blender may not
            # publish the new evaluated matrix until the next view-layer update.
            parent_world = Matrix.Identity(4)
        else:
            parent_world = child_world.get(obj.parent, obj.parent.matrix_world)
        obj.matrix_parent_inverse = parent_world.inverted_safe()
        obj.matrix_basis = Matrix.Identity(4)
        for child in obj.children:
            matrix = child_world.get(child)
            if matrix is not None:
                child.matrix_world = matrix
        fixed.append(obj)
    context.view_layer.update()
    ObjectRepository.select_objects(context, fixed + skipped)
    return tuple(fixed), tuple(skipped)


def check_active_pair(context, state, pair, progress=None):
    objects = []
    seen = set()
    sides = {}
    for side in ("HP", "LP"):
        for obj in ObjectRepository.meshes_under_root(pair, side):
            key = obj.as_pointer()
            sides.setdefault(key, side)
            if key not in seen:
                seen.add(key)
                objects.append(obj)
    if not objects:
        raise ValueError("No HP/LP mesh objects found in the active chapter")

    transform_objects = tuple(
        obj for obj in _transform_candidates(pair) if _has_unapplied_transform(obj)
    )

    depsgraph = context.evaluated_depsgraph_get()
    duplicate_tolerance = duplicate_check_tolerance(context.scene)
    facts = {}
    for index, obj in enumerate(objects):
        facts[obj] = _mesh_facts(obj, depsgraph, tolerance=duplicate_tolerance)
        if progress:
            progress.update(int((index + 1) * 75 / max(1, len(objects))), "Checking mesh: {}".format(obj.name))
    buckets = {}
    for obj, info in facts.items():
        key = (
            sides.get(obj.as_pointer(), ""),
            info.vertex_count, info.edge_count, info.face_count,
            info.bbox_key, info.vertex_digest,
        )
        buckets.setdefault(key, []).append(obj)
    duplicate_groups = tuple(
        tuple(group) for group in buckets.values() if len(group) > 1
    )
    tagged = {obj.as_pointer() for obj in zbrush_objects(context, state)}
    threshold = int(state.zbrush_triangle_threshold)
    zbrush_candidates = tuple(
        obj for obj in ObjectRepository.meshes_under_root(pair, "HP")
        if facts[obj].triangle_ratio >= threshold
        and obj.as_pointer() not in tagged
    )
    combined = tuple(
        obj for obj, info in facts.items() if info.meaningful_components >= 2
    )

    issues = []
    issue_seen = set()
    for obj in list(transform_objects) + [item for group in duplicate_groups for item in group] + list(zbrush_candidates) + list(combined):
        key = obj.as_pointer()
        if key not in issue_seen:
            issue_seen.add(key)
            issues.append(obj)

    lines = [
        "Mesh Check — chapter: {}".format(pair.name),
        "Duplicate tolerance: {:.9g} Blender unit(s) (Maya 0.001 cm equivalent)".format(
            duplicate_tolerance
        ),
    ]
    if transform_objects:
        lines.append("Unapplied transforms (Location/Rotation/Scale): {}".format(len(transform_objects)))
        lines.append("  " + ", ".join(obj.name for obj in transform_objects[:16]))
    else:
        lines.append("Unapplied transforms: none")
    if duplicate_groups:
        lines.append("Duplicates: {} group(s) / {} mesh(es)".format(
            len(duplicate_groups), sum(len(group) for group in duplicate_groups)
        ))
        for index, group in enumerate(duplicate_groups[:8], 1):
            lines.append("  D{}: {}".format(index, ", ".join(obj.name for obj in group[:8])))
    else:
        lines.append("Duplicates: none")
    if zbrush_candidates:
        lines.append("Possible ZBrush meshes outside BakeTools layer: {} (threshold {}%)".format(
            len(zbrush_candidates), threshold
        ))
        lines.append("  " + ", ".join(obj.name for obj in zbrush_candidates[:16]))
    else:
        lines.append("Possible ZBrush meshes outside BakeTools layer: none")
    if combined:
        lines.append("Meshes with independent loose parts: {}".format(len(combined)))
        for obj in combined[:16]:
            lines.append("  {}: {} meaningful part(s)".format(obj.name, facts[obj].meaningful_components))
    else:
        lines.append("Meshes with independent loose parts: none")
    lines.append("Result: {} issue mesh(es) found".format(len(issues)))
    if progress:
        progress.update(100, "Mesh Check complete")
    return MeshCheckResult(
        report="\n".join(lines),
        issue_objects=tuple(issues),
        transform_objects=transform_objects,
        duplicate_groups=duplicate_groups,
        zbrush_candidates=zbrush_candidates,
        combined_meshes=combined,
    )


def encode_check_payload(result, pair):
    return json.dumps(result.payload(pair.item_id), ensure_ascii=False)


def decode_check_payload(state, pair=None):
    try:
        payload = json.loads(state.mesh_check_payload or "{}")
    except (TypeError, ValueError):
        payload = {}
    if pair is not None and str(payload.get("pair_id") or "") != str(pair.item_id):
        raise ValueError("Run Mesh Check for the active chapter first")
    return payload


def _objects_from_names(context, names, mesh_only=True):
    result = []
    for name in names or ():
        obj = bpy.data.objects.get(str(name))
        if obj is not None and (not mesh_only or obj.type == "MESH") and obj.name in context.scene.objects:
            result.append(obj)
    return result


def select_check_category(context, state, pair, category):
    payload = decode_check_payload(state, pair)
    if category == "TRANSFORMS":
        names = payload.get("transforms", ())
    elif category == "DUPLICATES":
        names = [name for group in payload.get("duplicates", ()) for name in group]
    elif category == "ZBRUSH":
        names = payload.get("zbrush", ())
    elif category == "COMBINED":
        names = payload.get("combined", ())
    else:
        names = payload.get("issues", ())
    return ObjectRepository.select_objects(
        context, _objects_from_names(context, names, mesh_only=(category != "TRANSFORMS"))
    )


def remove_duplicate_copies(context, state, pair):
    payload = decode_check_payload(state, pair)
    removed = []
    kept = []
    skipped = []
    for names in payload.get("duplicates", ()):
        objects = _objects_from_names(context, names)
        if len(objects) < 2:
            continue
        objects.sort(key=lambda obj: (len(obj.name), obj.name.casefold()))
        kept.append(objects[0])
        for obj in objects[1:]:
            if obj.library is not None or obj.children:
                skipped.append(obj)
                continue
            removed.append(obj.name)
            bpy.data.objects.remove(obj, do_unlink=True)
    _prune_project_refs(state)
    ObjectRepository.sync_counts(pair)
    ObjectRepository.select_objects(context, [obj for obj in kept if obj.name in context.scene.objects])
    return tuple(removed), tuple(obj.name for obj in kept), tuple(obj.name for obj in skipped)


def add_check_zbrush_candidates(context, state, pair):
    payload = decode_check_payload(state, pair)
    objects = _objects_from_names(context, payload.get("zbrush", ()))
    added = mark_zbrush_objects(context, state, objects)
    ObjectRepository.select_objects(context, objects)
    return tuple(objects), tuple(added)


def separate_check_candidates(context, state, pair, progress=None):
    payload = decode_check_payload(state, pair)
    objects = _objects_from_names(context, payload.get("combined", ()))
    if not objects:
        return (), 0
    ObjectRepository.select_objects(context, objects)
    return separate_selected(context, state, progress)
