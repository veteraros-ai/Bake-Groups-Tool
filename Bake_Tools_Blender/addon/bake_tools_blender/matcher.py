"""Blender adapter for Maya's HP -> LP Matcher.

The matcher never reparents Blender objects.  Auto proposals and manual links
are stored on the active chapter; Relocate applies a saved link to the most
likely existing Bake Tools subgroup through membership metadata.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
import bisect
from math import sqrt
import re
from uuid import uuid4

from .analysis_adapter import _snapshot_mesh
from .domain.analysis import MeshSnapshot
from .lp_matching_service import _build_kd, _nearest_squared, _sample
from .object_repository import ObjectRepository


_EPS = 1.0e-9


@dataclass(frozen=True, slots=True)
class _LPShell:
    owner: object
    snapshot: MeshSnapshot


def _add_ref(refs, obj):
    ref = refs.add()
    ref.target = obj
    ref.last_name = obj.name


def _valid_hp_selection(context, pair):
    return tuple(
        obj for obj in ObjectRepository.selected_meshes(context)
        if ObjectRepository.classify(pair, obj) == "HP"
    )


def _snapshot_lp_shells(obj, depsgraph, sample_cap):
    """Expose Blender loose parts as Maya-style virtual LP shells."""
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=False, depsgraph=depsgraph)
    try:
        if not mesh.polygons:
            return (_LPShell(obj, _snapshot_mesh(obj, depsgraph, sample_cap)),)
        vertex_faces = defaultdict(list)
        for polygon in mesh.polygons:
            for vertex in polygon.vertices:
                vertex_faces[int(vertex)].append(int(polygon.index))
        visited = set()
        components = []
        for start in range(len(mesh.polygons)):
            if start in visited:
                continue
            stack = [start]
            visited.add(start)
            faces = []
            vertices = set()
            while stack:
                face_index = stack.pop()
                polygon = mesh.polygons[face_index]
                faces.append(face_index)
                for vertex in polygon.vertices:
                    vertex = int(vertex)
                    vertices.add(vertex)
                    for neighbour in vertex_faces[vertex]:
                        if neighbour not in visited:
                            visited.add(neighbour)
                            stack.append(neighbour)
            components.append((faces, vertices))
        matrix = evaluated.matrix_world
        mesh.calc_loop_triangles()
        result = []
        for index, (faces, vertex_indices) in enumerate(components):
            points = tuple(tuple(matrix @ mesh.vertices[value].co) for value in sorted(vertex_indices))
            if not points:
                continue
            face_set = set(faces)
            triangles = tuple(
                tuple(tuple(matrix @ mesh.vertices[value].co) for value in triangle.vertices)
                for triangle in mesh.loop_triangles if int(triangle.polygon_index) in face_set
            )
            sampled = _sample_triangles(triangles, sample_cap) or _sample(points, sample_cap)
            minimum = tuple(min(point[axis] for point in points) for axis in range(3))
            maximum = tuple(max(point[axis] for point in points) for axis in range(3))
            dimensions = tuple(maximum[axis] - minimum[axis] for axis in range(3))
            center = tuple((minimum[axis] + maximum[axis]) * 0.5 for axis in range(3))
            snapshot = MeshSnapshot(
                key="{}::shell_{:03d}".format(obj.name_full, index),
                name="{}::shell_{:03d}".format(obj.name, index),
                bbox_min=minimum, bbox_max=maximum, center=center, dimensions=dimensions,
                diagonal=sqrt(sum(value * value for value in dimensions)),
                bbox_volume=dimensions[0] * dimensions[1] * dimensions[2],
                vertex_count=len(vertex_indices), edge_count=0,
                face_count=len(faces), vertices=tuple(sampled),
            )
            result.append(_LPShell(obj, snapshot))
        return tuple(result) or (_LPShell(obj, _snapshot_mesh(obj, depsgraph, sample_cap)),)
    finally:
        evaluated.to_mesh_clear()


def _triangle_area(triangle):
    a, b, c = triangle
    ab = tuple(b[axis] - a[axis] for axis in range(3))
    ac = tuple(c[axis] - a[axis] for axis in range(3))
    cross = (
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    )
    return sqrt(sum(value * value for value in cross)) * 0.5


def _sample_triangles(triangles, count):
    weighted = []
    cumulative = []
    total = 0.0
    for triangle in triangles:
        area = _triangle_area(triangle)
        if area <= 1.0e-10:
            continue
        total += area
        weighted.append(triangle)
        cumulative.append(total)
    if not weighted:
        return ()
    samples = []
    count = max(1, int(count))
    for index in range(count):
        area_position = ((index + 0.5) / float(count)) * total
        triangle = weighted[min(bisect.bisect_left(cumulative, area_position), len(weighted) - 1)]
        a, b, c = triangle
        r1 = (index * 0.7548776662466927 + 0.5) % 1.0
        r2 = (index * 0.5698402909980532 + 0.25) % 1.0
        root = sqrt(r1)
        weights = (1.0 - root, root * (1.0 - r2), root * r2)
        samples.append(tuple(a[axis] * weights[0] + b[axis] * weights[1] + c[axis] * weights[2] for axis in range(3)))
    return tuple(samples)


def _surface_snapshot(obj, depsgraph, sample_cap, is_zbrush=False):
    base = _snapshot_mesh(obj, depsgraph, sample_cap, is_zbrush=is_zbrush)
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=False, depsgraph=depsgraph)
    try:
        mesh.calc_loop_triangles()
        matrix = evaluated.matrix_world
        triangles = tuple(
            tuple(tuple(matrix @ mesh.vertices[value].co) for value in triangle.vertices)
            for triangle in mesh.loop_triangles
        )
        samples = _sample_triangles(triangles, sample_cap)
        return replace(base, vertices=samples or base.vertices)
    finally:
        evaluated.to_mesh_clear()


def _bbox_gap(left, right):
    total = 0.0
    for axis in range(3):
        if left.bbox_max[axis] < right.bbox_min[axis]:
            delta = right.bbox_min[axis] - left.bbox_max[axis]
        elif right.bbox_max[axis] < left.bbox_min[axis]:
            delta = left.bbox_min[axis] - right.bbox_max[axis]
        else:
            delta = 0.0
        total += delta * delta
    return sqrt(total)


def _overlap_ratio(left, right):
    spans = tuple(
        max(0.0, min(left.bbox_max[a], right.bbox_max[a]) - max(left.bbox_min[a], right.bbox_min[a]))
        for a in range(3)
    )
    intersection = spans[0] * spans[1] * spans[2]
    return intersection / max(min(left.bbox_volume, right.bbox_volume), _EPS)


def _coverage(source, target, tolerance, tree_cache):
    points = tuple(source.vertices)
    target_points = tuple(target.vertices)
    if not points or not target_points:
        distance = sqrt(sum((source.center[a] - target.center[a]) ** 2 for a in range(3)))
        return (1.0 if distance <= tolerance else 0.0), distance
    tree = tree_cache.get(target.key)
    if tree is None:
        tree = _build_kd(target_points)
        tree_cache[target.key] = tree
    distances = tuple(sqrt(_nearest_squared(tree, point)) for point in points)
    return (
        sum(1 for value in distances if value <= tolerance) / float(len(distances)),
        sum(distances) / float(len(distances)),
    )


def _score(hp, lp, mode, tolerance_pct, strict, tree_cache):
    overlap = _overlap_ratio(hp, lp)
    gap = _bbox_gap(hp, lp)
    is_zbrush = bool(hp.is_zbrush)
    if mode == "FAST":
        if overlap > 0.02:
            return 1000.0 + overlap
        if strict and not is_zbrush:
            return None
        allowed = max(min(hp.diagonal, lp.diagonal) * (1.5 if is_zbrush else 0.65), hp.diagonal * 0.35, 0.001)
        return 1.0 / (gap + 0.000001) if gap <= allowed else None

    multiplier = 0.9 if mode == "BALANCED" else 0.65
    if is_zbrush:
        multiplier *= 1.75
    allowed = max(min(hp.diagonal, lp.diagonal) * multiplier, hp.diagonal * 0.35, 0.001)
    if strict and not is_zbrush:
        allowed = min(allowed, min(hp.diagonal, lp.diagonal) * 0.75)
    if overlap <= 0.0 and gap > allowed:
        return None
    effective_pct = max(tolerance_pct * (0.75 if mode == "ACCURATE" else 1.0), 0.0125 if mode == "ACCURATE" else 0.02)
    tolerance = max(min(hp.diagonal, lp.diagonal) * effective_pct, 0.0001)
    lp_coverage, lp_distance = _coverage(lp, hp, tolerance, tree_cache)
    hp_coverage, hp_distance = _coverage(hp, lp, tolerance, tree_cache)
    if lp_coverage < (0.24 if mode == "ACCURATE" else 0.16) and hp_coverage < (0.38 if mode == "ACCURATE" else 0.28):
        return None
    average = min(lp_distance, hp_distance)
    distance_score = max(0.0, 1.0 - min(average / max(tolerance * 3.0, _EPS), 1.0))
    bbox_score = min(overlap * 2.0, 1.0) if overlap > 0.0 else max(0.0, 1.0 - min(gap / max(tolerance * 6.0, _EPS), 1.0))
    diagonal_score = min(hp.diagonal, lp.diagonal) / max(hp.diagonal, lp.diagonal, _EPS)
    volume_score = min(hp.bbox_volume, lp.bbox_volume) / max(hp.bbox_volume, lp.bbox_volume, _EPS)
    size_score = diagonal_score * 0.65 + volume_score * 0.35
    score = (lp_coverage * 0.46 + hp_coverage * 0.24 + distance_score * 0.15 + size_score * 0.10 + bbox_score * 0.05) * 1000.0
    if overlap > 0.5 and diagonal_score < 0.35 and lp_coverage < 0.2:
        score -= 220.0
    if mode == "ACCURATE" and lp_coverage < 0.35 and hp_coverage < 0.55:
        score -= 120.0
    return score if score > 0.0 else None


def _cluster_objects(cluster, side):
    return tuple(ref.target for ref in getattr(cluster, "{}_members".format(side.lower())) if ref.target is not None)


def _set_cluster(cluster, *, name, title, hp_objects, lp_objects, linked=False, already_grouped=False, score=0.0):
    cluster.item_id = uuid4().hex
    cluster.name = name
    cluster.title = title
    cluster.linked = linked
    cluster.already_grouped = already_grouped
    cluster.score = score
    for obj in hp_objects:
        _add_ref(cluster.hp_members, obj)
    for obj in lp_objects:
        _add_ref(cluster.lp_members, obj)


def _proposal_name(lp_name, grouped=False):
    base = re.sub(r"(?:_LP|_lp|_low)$", "", lp_name).replace(" ", "_")
    return (base or "Matcher_Group") + ("_Group" if grouped else "")


def find_groups(context, state, pair, progress=None):
    hp_objects = tuple(ObjectRepository.meshes_under_root(pair, "HP"))
    lp_objects = tuple(ObjectRepository.meshes_under_root(pair, "LP"))
    if not hp_objects or not lp_objects:
        raise ValueError("Active chapter needs both HP and LP mesh roots")
    linked_saved = []
    for cluster in pair.matcher_clusters:
        if cluster.linked:
            linked_saved.append((cluster.name, _cluster_objects(cluster, "HP"), _cluster_objects(cluster, "LP")))
    remembered_zbrush = {ref.target.as_pointer() for ref in state.zbrush_members if ref.target is not None}
    depsgraph = context.evaluated_depsgraph_get()
    mode = str(state.matcher_mode)
    sample_cap = 160 if mode == "ACCURATE" else 90
    hp_snapshots = []
    hp_by_key = {}
    for index, obj in enumerate(hp_objects):
        if progress:
            progress.update(4 + int(index / max(len(hp_objects), 1) * 22), "Sampling HP: {}".format(obj.name))
        snapshot = _surface_snapshot(obj, depsgraph, sample_cap, is_zbrush=obj.as_pointer() in remembered_zbrush or bool(obj.get("bake_tools_zbrush")))
        hp_snapshots.append(snapshot)
        hp_by_key[snapshot.key] = obj
    shells = []
    for index, obj in enumerate(lp_objects):
        if progress:
            progress.update(28 + int(index / max(len(lp_objects), 1) * 20), "Preparing LP: {}".format(obj.name))
        shells.extend(_snapshot_lp_shells(obj, depsgraph, 220 if mode == "ACCURATE" else 120))
    tree_cache = {}
    shell_hp = {shell.snapshot.key: [] for shell in shells}
    total = max(len(hp_snapshots), 1)
    for index, hp in enumerate(hp_snapshots):
        if progress:
            progress.update(50 + int(index / total * 36), "Matching HP to LP: {} / {}".format(index + 1, total))
        scores = []
        for shell in shells:
            score = _score(hp, shell.snapshot, mode, state.matcher_tolerance / 100.0, state.strict_geo_check, tree_cache)
            if score is not None:
                scores.append((score, shell))
        if not scores:
            continue
        scores.sort(key=lambda item: item[0], reverse=True)
        if mode == "ACCURATE" and len(scores) > 1 and scores[1][0] > 0.0 and scores[0][0] / scores[1][0] < 1.08:
            continue
        shell_hp[scores[0][1].snapshot.key].append((hp, scores[0][0]))

    by_owner = {}
    for shell in shells:
        bucket = by_owner.setdefault(shell.owner, {"shells": 0, "hp": {}, "score": 0.0, "verts": 0, "faces": 0, "volume": 0.0})
        bucket["shells"] += 1
        bucket["verts"] += shell.snapshot.vertex_count
        bucket["faces"] += shell.snapshot.face_count
        bucket["volume"] += shell.snapshot.bbox_volume
        for hp, score in shell_hp[shell.snapshot.key]:
            bucket["hp"][hp.key] = hp
            bucket["score"] += score
    raw_proposals = [(obj, data) for obj, data in by_owner.items() if len(data["hp"]) >= state.matcher_min_hp_lp]
    proposals = []
    consumed = set()
    tolerance = max(float(state.matcher_tolerance) / 100.0, 0.0001)
    for index, (obj, data) in enumerate(raw_proposals):
        if obj.as_pointer() in consumed:
            continue
        owners = [obj]
        merged = dict(data)
        merged["hp"] = dict(data["hp"])
        consumed.add(obj.as_pointer())
        for other, other_data in raw_proposals[index + 1:]:
            if other.as_pointer() in consumed:
                continue
            volume_delta = abs(data["volume"] - other_data["volume"]) / max(data["volume"], other_data["volume"], _EPS)
            if data["verts"] == other_data["verts"] and data["faces"] == other_data["faces"] and volume_delta <= tolerance:
                owners.append(other)
                consumed.add(other.as_pointer())
                merged["hp"].update(other_data["hp"])
                merged["shells"] += other_data["shells"]
                merged["score"] += other_data["score"]
                merged["volume"] += other_data["volume"]
        proposals.append((tuple(owners), merged))
    old_by_set = {frozenset(obj.name_full for obj in hp): (name, hp, lp) for name, hp, lp in linked_saved if hp}
    pair.matcher_clusters.clear()
    displayed = set()
    used_names = set()
    for owners, data in sorted(proposals, key=lambda item: item[1]["volume"], reverse=True):
        hp_objs = tuple(hp_by_key[key] for key in data["hp"] if key in hp_by_key)
        hp_set = frozenset(item.name_full for item in hp_objs)
        saved = old_by_set.get(hp_set)
        name = saved[0] if saved else _proposal_name(owners[0].name, len(owners) > 1)
        base_name = name
        suffix = 2
        while name in used_names:
            name = "{}_{:02d}".format(base_name, suffix)
            suffix += 1
        used_names.add(name)
        already = any(
            frozenset(member.name_full for member in ObjectRepository.valid_members(subgroup, "HP")) == hp_set
            for subgroup in pair.subgroups if hp_set
        )
        title_root = owners[0].name if len(owners) == 1 else "Group of {} identical LPs".format(len(owners))
        title = "{} [Shells: {} | V: {} | HP: {}]".format(title_root, data["shells"], data["verts"], len(hp_objs))
        if already:
            title += " [Already grouped]"
        cluster = pair.matcher_clusters.add()
        _set_cluster(cluster, name=name, title=title, hp_objects=hp_objs, lp_objects=owners, linked=bool(saved), already_grouped=already, score=data["score"] / max(len(hp_objs), 1))
        displayed.add(name)
    for name, hp_objs, lp_objs in linked_saved:
        if name in displayed or not hp_objs:
            continue
        cluster = pair.matcher_clusters.add()
        _set_cluster(cluster, name=name, title="[Saved] {} [HP: {}]".format(name, len(hp_objs)), hp_objects=hp_objs, lp_objects=lp_objs, linked=True)
    if progress:
        progress.update(100, "Matcher complete")
    return len(proposals)


def select_clusters(context, pair, cluster_ids):
    ids = set(cluster_ids)
    objects = []
    for cluster in pair.matcher_clusters:
        if cluster.item_id in ids:
            objects.extend(_cluster_objects(cluster, "HP"))
    return ObjectRepository.select_objects(context, dict.fromkeys(objects))


def _next_name(pair, prefix):
    existing = {cluster.name for cluster in pair.matcher_clusters}
    index = 1
    while "{}_{:03d}".format(prefix, index) in existing:
        index += 1
    return "{}_{:03d}".format(prefix, index)


def new_cluster(context, pair):
    selected = _valid_hp_selection(context, pair)
    name = _next_name(pair, "new_group")
    cluster = pair.matcher_clusters.add()
    _set_cluster(cluster, name=name, title="[Saved] {} [Manual: {} HP]".format(name, len(selected)), hp_objects=selected, lp_objects=(), linked=True)
    return name, len(selected)


def link_clusters(context, pair, cluster_ids):
    ids = set(cluster_ids)
    targets = [cluster for cluster in pair.matcher_clusters if cluster.item_id in ids]
    selected = _valid_hp_selection(context, pair)
    if targets:
        first = targets[0]
        current = set(_cluster_objects(first, "HP"))
        if selected and set(selected) != current:
            first.hp_members.clear()
            for obj in selected:
                _add_ref(first.hp_members, obj)
            first.linked = True
            first.title = "[Saved] {} [Manual: {} HP]".format(first.name, len(selected))
            return 1, len(selected), True
        linked = 0
        meshes = 0
        for cluster in targets:
            if _cluster_objects(cluster, "HP"):
                cluster.linked = True
                linked += 1
                meshes += len(_cluster_objects(cluster, "HP"))
        return linked, meshes, False
    if not selected:
        raise ValueError("Select a matcher row or HP meshes under the active HP root")
    name = _next_name(pair, "Custom_Link")
    cluster = pair.matcher_clusters.add()
    _set_cluster(cluster, name=name, title="[Saved] {} [Manual: {} HP]".format(name, len(selected)), hp_objects=selected, lp_objects=(), linked=True)
    return 1, len(selected), True


def unlink_clusters(pair, cluster_ids):
    ids = set(cluster_ids)
    count = 0
    for cluster in pair.matcher_clusters:
        if cluster.item_id in ids and cluster.linked:
            cluster.linked = False
            count += 1
    if not ids:
        for cluster in pair.matcher_clusters:
            if cluster.linked:
                cluster.linked = False
                count += 1
    return count


def relocate_clusters(state, pair):
    corrections = 0
    skipped = 0
    for cluster in pair.matcher_clusters:
        if not cluster.linked:
            continue
        meshes = _cluster_objects(cluster, "HP")
        buckets = defaultdict(list)
        for obj in meshes:
            subgroup, side = ObjectRepository.membership(pair, obj)
            if subgroup is not None and side == "HP":
                buckets[subgroup.item_id].append(obj)
        if not buckets:
            skipped += 1
            continue
        target_id = max(buckets, key=lambda value: len(buckets[value]))
        target = next(group for group in pair.subgroups if group.item_id == target_id)
        for obj in meshes:
            owner, side = ObjectRepository.membership(pair, obj)
            if owner == target and side == "HP":
                continue
            for owner_pair in state.pairs:
                ObjectRepository.remove_member_from_pair(owner_pair, obj)
            _add_ref(target.hp_members, obj)
            corrections += 1
    for owner_pair in state.pairs:
        ObjectRepository.sync_counts(owner_pair)
    ObjectRepository.sync_pair_visibility(state, pair)
    return corrections, skipped


def linked_semantic_map(pair):
    result = {}
    for cluster in pair.matcher_clusters:
        if not cluster.linked:
            continue
        for obj in _cluster_objects(cluster, "HP"):
            result[obj.as_pointer()] = cluster.name
    return result
