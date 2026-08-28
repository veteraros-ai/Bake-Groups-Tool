"""LP material inspection and non-destructive chapter distribution for Blender.

Maya can reparent transforms into newly created ``*_HP``/``*_LP`` roots.  The
Blender port keeps the artist's hierarchy intact and stores an explicit object
scope on every material-created chapter instead.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re

import bpy

from . import native_core


@dataclass(frozen=True, slots=True)
class MaterialSummary:
    names: tuple[str, ...]

    @property
    def count(self):
        return len(self.names)


@dataclass(frozen=True, slots=True)
class MaterialBucket:
    signature: tuple[str, ...]
    label: str
    lp_objects: tuple
    hp_objects: tuple


@dataclass(frozen=True, slots=True)
class MaterialDistributionDiagnostics:
    lp_proxy_count: int = 0
    container_count: int = 0
    direct_hp: int = 0
    container_hp: int = 0
    floater_assigned: int = 0
    floater_reassigned: int = 0
    lp_audit_checked: int = 0
    lp_audit_candidates: int = 0
    lp_audit_assigned: int = 0
    lp_audit_reassigned: int = 0
    lp_audit_container_conflicts: int = 0
    low_confidence_hp: int = 0
    review_hp: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MaterialDistributionResult:
    buckets: tuple[MaterialBucket, ...]
    diagnostics: MaterialDistributionDiagnostics


def picked_root(state, role):
    key = role.lower()
    kind = getattr(state, "{}_root_kind".format(key), "")
    if kind == "COLLECTION":
        return kind, getattr(state, "{}_collection".format(key), None)
    return "OBJECT", getattr(state, "{}_root".format(key), None)


def target_objects(root):
    if root is None:
        return ()
    if isinstance(root, bpy.types.Collection):
        return tuple(root.all_objects)
    result = [root]
    stack = list(root.children)
    while stack:
        obj = stack.pop()
        result.append(obj)
        stack.extend(obj.children)
    return tuple(result)


def target_meshes(root):
    return tuple(obj for obj in target_objects(root) if obj.type == "MESH")


def used_material_names(obj):
    """Return only materials actually assigned to polygons, not unused slots."""
    mesh = getattr(obj, "data", None)
    if mesh is None:
        return ()
    slots = tuple(mesh.materials)
    used_indices = {int(poly.material_index) for poly in mesh.polygons}
    names = {
        slots[index].name_full
        for index in used_indices
        if 0 <= index < len(slots) and slots[index] is not None
    }
    return tuple(sorted(names, key=str.casefold))


def inspect_root_materials(root):
    names = {
        material
        for obj in target_meshes(root)
        for material in used_material_names(obj)
    }
    return MaterialSummary(tuple(sorted(names, key=str.casefold)))


def inspect_picked_lp(state):
    _kind, root = picked_root(state, "LP")
    return inspect_root_materials(root)


def clean_material_name(name):
    value = str(name or "").split("|")[-1].split(":")[-1]
    value = re.sub(r"^(?:T|M|MAT)_", "", value, flags=re.IGNORECASE)
    value = re.sub(r"_(?:M|MAT|MATERIAL)$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_")
    return value or "Material"


def next_book_name(state):
    existing = {pair.book for pair in state.pairs if pair.book}
    number = 1
    while "Book_{:02d}".format(number) in existing:
        number += 1
    return "Book_{:02d}".format(number)


def _unique_name(existing, requested):
    base = (requested or "Material").strip() or "Material"
    if base not in existing:
        existing.add(base)
        return base
    number = 2
    while "{}_{:02d}".format(base, number) in existing:
        number += 1
    result = "{}_{:02d}".format(base, number)
    existing.add(result)
    return result


def _progress(progress, value, label):
    if progress is not None:
        progress.update(value, label)


def _median(values, fallback=1.0):
    ordered = sorted(float(value) for value in values if float(value) > 0.0)
    if not ordered:
        return float(fallback)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) * 0.5


def _bounds_data(points, name=""):
    if not points:
        return None
    minimum = tuple(min(point[axis] for point in points) for axis in range(3))
    maximum = tuple(max(point[axis] for point in points) for axis in range(3))
    dimensions = tuple(maximum[axis] - minimum[axis] for axis in range(3))
    center = tuple((minimum[axis] + maximum[axis]) * 0.5 for axis in range(3))
    return {
        "name": name,
        "min": minimum,
        "max": maximum,
        "center": center,
        "diag": math.sqrt(sum(value * value for value in dimensions)),
        "volume": max(dimensions[0] * dimensions[1] * dimensions[2], 0.0),
    }


def _sample_sequence(values, cap):
    values = tuple(values)
    if len(values) <= cap:
        return values
    step = len(values) / float(max(cap, 1))
    return tuple(values[min(len(values) - 1, int(index * step))] for index in range(cap))


def _mesh_data(obj, depsgraph, max_points=48):
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=False, depsgraph=depsgraph)
    try:
        matrix = evaluated.matrix_world
        points = tuple(tuple(matrix @ vertex.co) for vertex in mesh.vertices)
        data = _bounds_data(points, obj.name_full)
        if data is None:
            return None
        data["sample_points"] = _sample_sequence(points, max_points)
        return data
    finally:
        evaluated.to_mesh_clear()


def _material_name(materials, index):
    if 0 <= index < len(materials) and materials[index] is not None:
        return materials[index].name_full
    return "No_Material"


def _component_sample_points(world_points, face_vertices, shell_faces, shell_vertices, max_points=160):
    ordered_vertices = sorted(shell_vertices)
    vertex_budget = max(16, int(max_points * 0.55))
    result = list(_sample_sequence((world_points[index] for index in ordered_vertices), vertex_budget))
    face_budget = max_points - len(result)
    if face_budget > 0:
        sampled_faces = _sample_sequence(shell_faces, face_budget)
        for face_index in sampled_faces:
            vertices = face_vertices[face_index]
            if not vertices:
                continue
            inv = 1.0 / float(len(vertices))
            result.append(tuple(
                sum(world_points[vertex][axis] for vertex in vertices) * inv
                for axis in range(3)
            ))
    return tuple(result[:max_points])


def _lp_proxy_records(obj, depsgraph):
    """Build Maya-equivalent connected LP shell/material-region proxies."""
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=False, depsgraph=depsgraph)
    try:
        matrix = evaluated.matrix_world
        world_points = tuple(tuple(matrix @ vertex.co) for vertex in mesh.vertices)
        if not world_points or not mesh.polygons:
            return (), (), ()
        materials = tuple(mesh.materials)
        face_vertices = [tuple(polygon.vertices) for polygon in mesh.polygons]
        face_materials = [
            _material_name(materials, int(polygon.material_index)) for polygon in mesh.polygons
        ]
        signature = tuple(sorted(set(face_materials), key=str.casefold)) or ("No_Material",)
        labels = tuple(clean_material_name(name) for name in signature)
        use_material_regions = len(signature) > 1
        vertex_to_faces = {}
        for face_index, vertices in enumerate(face_vertices):
            for vertex_index in vertices:
                vertex_to_faces.setdefault(vertex_index, []).append(face_index)

        node_data = _bounds_data(world_points, obj.name_full)
        if node_data is None:
            return signature, labels, ()
        node_data["sample_points"] = _sample_sequence(world_points, 48)

        visited = set()
        proxies = []
        for start_face in range(len(face_vertices)):
            if start_face in visited:
                continue
            start_material = face_materials[start_face]
            stack = [start_face]
            visited.add(start_face)
            shell_faces = []
            shell_vertices = set()
            while stack:
                face_index = stack.pop()
                shell_faces.append(face_index)
                for vertex_index in face_vertices[face_index]:
                    shell_vertices.add(vertex_index)
                    for neighbor in vertex_to_faces.get(vertex_index, ()):
                        if neighbor in visited:
                            continue
                        if use_material_regions and face_materials[neighbor] != start_material:
                            continue
                        visited.add(neighbor)
                        stack.append(neighbor)
            points = tuple(world_points[index] for index in sorted(shell_vertices))
            data = _bounds_data(points, "{}::mat_proxy_{:03d}".format(obj.name_full, len(proxies)))
            if data is None:
                continue
            data["sample_points"] = _component_sample_points(
                world_points, face_vertices, shell_faces, shell_vertices
            )
            proxies.append({
                "lp_object": obj,
                "signature": signature,
                "labels": labels,
                "data": data,
                "node_data": node_data,
                "signature_size": len(signature),
                "face_count": len(shell_faces),
                "material_key": start_material,
                "is_container": False,
            })
        return signature, labels, tuple(proxies)
    finally:
        evaluated.to_mesh_clear()


def _intersection_volume(left, right):
    overlap = [
        max(0.0, min(left["max"][axis], right["max"][axis]) - max(left["min"][axis], right["min"][axis]))
        for axis in range(3)
    ]
    return overlap[0] * overlap[1] * overlap[2]


def _bbox_gap_distance(left, right):
    total = 0.0
    for axis in range(3):
        if left["max"][axis] < right["min"][axis]:
            delta = right["min"][axis] - left["max"][axis]
        elif right["max"][axis] < left["min"][axis]:
            delta = left["min"][axis] - right["max"][axis]
        else:
            delta = 0.0
        total += delta * delta
    return math.sqrt(total)


def _bbox_points(data):
    minimum, maximum, center = data["min"], data["max"], data["center"]
    return (
        (minimum[0], minimum[1], minimum[2]), (minimum[0], minimum[1], maximum[2]),
        (minimum[0], maximum[1], minimum[2]), (minimum[0], maximum[1], maximum[2]),
        (maximum[0], minimum[1], minimum[2]), (maximum[0], minimum[1], maximum[2]),
        (maximum[0], maximum[1], minimum[2]), (maximum[0], maximum[1], maximum[2]),
        center,
    )


def _point_sample_distance(left, right):
    left_points = left.get("sample_points") or _bbox_points(left)
    right_points = right.get("sample_points") or _bbox_points(right)
    try:
        native = native_core.calculate_min_distance(left_points, right_points)
        if native is not None and math.isfinite(native):
            return native
    except (AttributeError, RuntimeError, TypeError, ValueError):
        pass
    return math.sqrt(min(
        sum((point[axis] - target[axis]) ** 2 for axis in range(3))
        for point in left_points for target in right_points
    ))


def _average_sample_distance(source, target):
    source_points = _sample_sequence(source.get("sample_points") or _bbox_points(source), 96)
    target_points = _sample_sequence(target.get("sample_points") or _bbox_points(target), 128)
    try:
        native = native_core.calculate_avg_distance(source_points, target_points)
        if native is not None and math.isfinite(native):
            return native
    except (AttributeError, RuntimeError, TypeError, ValueError):
        pass
    total = 0.0
    for point in source_points:
        total += math.sqrt(min(
            sum((point[axis] - target_point[axis]) ** 2 for axis in range(3))
            for target_point in target_points
        ))
    return total / float(max(len(source_points), 1))


def _quick_owner_score(hp_data, proxy):
    lp_data = proxy["data"]
    intersection = _intersection_volume(hp_data, lp_data)
    gap = _bbox_gap_distance(hp_data, lp_data)
    hp_diag = max(hp_data["diag"], 1.0e-6)
    lp_diag = max(lp_data["diag"], 1.0e-6)
    min_diag, max_diag = max(min(hp_diag, lp_diag), 1.0e-6), max(hp_diag, lp_diag, 1.0e-6)
    if intersection <= 0.0 and gap > max(min_diag * 4.0, max_diag * 0.06):
        return None
    center_distance = math.dist(hp_data["center"], lp_data["center"])
    overlap_hp = intersection / max(hp_data["volume"], 1.0e-6)
    score = (1600.0 if intersection > 0.0 else 0.0) + overlap_hp * 400.0 + (min_diag / max_diag) * 30.0
    score -= (gap / max(min_diag, max_diag * 0.01, 1.0e-6)) * 100.0
    score -= (center_distance / max_diag) * 20.0
    return score


def _hp_owner_score(hp_data, proxy, scene_diag):
    lp_data = proxy["data"]
    intersection = _intersection_volume(hp_data, lp_data)
    gap = _bbox_gap_distance(hp_data, lp_data)
    sample_distance = _point_sample_distance(hp_data, lp_data)
    hp_volume = max(hp_data["volume"], 1.0e-6)
    lp_volume = max(lp_data["volume"], 1.0e-6)
    hp_diag = max(hp_data["diag"], 1.0e-6)
    lp_diag = max(lp_data["diag"], 1.0e-6)
    min_diag, max_diag = max(min(hp_diag, lp_diag), 1.0e-6), max(hp_diag, lp_diag, 1.0e-6)
    overlap_hp = intersection / hp_volume
    overlap_lp = intersection / lp_volume
    size_ratio = min_diag / max_diag
    near_scale = max(hp_diag * 0.25, lp_diag * 0.02, scene_diag * 0.001, 1.0e-6)
    sample_norm = sample_distance / near_scale
    gap_norm = gap / max(hp_diag * 0.25, lp_diag * 0.02, scene_diag * 0.001, 1.0e-6)
    is_container = bool(proxy.get("is_container"))
    score = overlap_hp * 1800.0
    score += max(0.0, 1.0 - min(sample_norm, 1.0)) * 420.0
    score += size_ratio * 120.0 + overlap_lp * 80.0
    if intersection > 0.0:
        score += 260.0
    score -= gap_norm * 140.0
    if lp_diag < hp_diag * 0.22 and overlap_hp < 0.12:
        score -= 520.0
    if is_container:
        score -= 180.0
        if overlap_hp < 0.16 and sample_norm > 0.75:
            score -= 260.0
    confident = (
        (overlap_hp >= 0.10 and sample_norm <= 2.2)
        or (overlap_hp >= 0.04 and size_ratio >= 0.22 and sample_norm <= 1.25)
        or (not is_container and sample_norm <= 0.55 and gap <= max(hp_diag * 0.12, scene_diag * 0.0005))
        or (is_container and overlap_hp >= 0.22)
    )
    strong = (
        overlap_hp >= 0.22
        or (sample_norm <= 0.45 and size_ratio >= 0.12)
        or (is_container and overlap_hp >= 0.35)
    )
    return {
        "score": score, "confident": confident, "strong": strong,
        "has_overlap": intersection > 0.0, "proxy": proxy,
    }


def _hp_parent_score(child, parent, scene_diag):
    child_diag, parent_diag = max(child["diag"], 1.0e-6), max(parent["diag"], 1.0e-6)
    if parent_diag <= child_diag * 1.25:
        return None
    intersection = _intersection_volume(child, parent)
    overlap_child = intersection / max(child["volume"], 1.0e-6)
    gap = _bbox_gap_distance(child, parent)
    if intersection <= 0.0 and gap > max(child_diag * 1.2, parent_diag * 0.08, scene_diag * 0.003):
        return None
    sample_distance = _point_sample_distance(child, parent)
    near_limit = max(child_diag * 0.55, parent_diag * 0.035, scene_diag * 0.001, 1.0e-6)
    if sample_distance > near_limit and gap > max(child_diag * 0.25, parent_diag * 0.01, scene_diag * 0.0005) and overlap_child < 0.12:
        return None
    score = max(0.0, 1.0 - min(sample_distance / near_limit, 1.0)) * 130.0
    score += min(overlap_child, 1.0) * 95.0
    score += min(parent_diag / child_diag, 8.0) * 4.0
    if gap <= max(child_diag * 0.15, parent_diag * 0.008):
        score += 25.0
    return score


def _audit_quick_score(proxy, hp_data, scene_diag):
    lp_data = proxy["data"]
    intersection = _intersection_volume(lp_data, hp_data)
    gap = _bbox_gap_distance(lp_data, hp_data)
    lp_diag, hp_diag = max(lp_data["diag"], 1.0e-6), max(hp_data["diag"], 1.0e-6)
    if intersection <= 0.0 and gap > max(lp_diag * 0.75, hp_diag * 1.10, scene_diag * 0.004):
        return None
    overlap_lp = intersection / max(lp_data["volume"], 1.0e-6)
    overlap_hp = intersection / max(hp_data["volume"], 1.0e-6)
    center_distance = math.dist(lp_data["center"], hp_data["center"])
    gap_norm = gap / max(min(lp_diag, hp_diag), scene_diag * 0.001, 1.0e-6)
    score = overlap_lp * 700.0 + overlap_hp * 180.0
    if intersection > 0.0:
        score += 160.0
    score -= gap_norm * 70.0
    score -= (center_distance / max(lp_diag, hp_diag, scene_diag * 0.01, 1.0e-6)) * 25.0
    if proxy.get("is_container"):
        score -= 220.0
    return score


def _audit_score(proxy, hp_data, scene_diag):
    lp_data = proxy["data"]
    intersection = _intersection_volume(lp_data, hp_data)
    gap = _bbox_gap_distance(lp_data, hp_data)
    lp_diag, hp_diag = max(lp_data["diag"], 1.0e-6), max(hp_data["diag"], 1.0e-6)
    if intersection <= 0.0 and gap > max(lp_diag * 0.55, hp_diag * 0.85, scene_diag * 0.003):
        return None
    average_distance = _average_sample_distance(lp_data, hp_data)
    overlap_lp = intersection / max(lp_data["volume"], 1.0e-6)
    overlap_hp = intersection / max(hp_data["volume"], 1.0e-6)
    near_limit = max(lp_diag * 0.12, hp_diag * 0.08, scene_diag * 0.001, 1.0e-6)
    average_norm = average_distance / near_limit
    gap_norm = gap / max(lp_diag * 0.20, hp_diag * 0.20, scene_diag * 0.001, 1.0e-6)
    score = overlap_lp * 1200.0 + overlap_hp * 260.0
    score += max(0.0, 1.0 - min(average_norm, 1.0)) * 620.0
    if intersection > 0.0:
        score += 220.0
    score -= gap_norm * 120.0
    if hp_diag < lp_diag * 0.08 and overlap_lp < 0.10:
        score -= 260.0
    if proxy.get("is_container"):
        score -= 320.0
    if proxy.get("is_container"):
        strong = overlap_lp >= 0.35 and average_norm <= 1.0
    else:
        strong = (
            overlap_lp >= 0.16
            or (overlap_lp >= 0.055 and average_norm <= 1.25)
            or (average_norm <= 0.62 and gap <= max(lp_diag * 0.10, hp_diag * 0.10, scene_diag * 0.0005))
        )
    return {"score": score, "strong": strong}


def _best_bucket_audit_score(bucket, hp_data, scene_diag):
    quick = []
    for proxy in bucket.get("lp_proxies", ()) if bucket else ():
        score = _audit_quick_score(proxy, hp_data, scene_diag)
        if score is not None:
            quick.append((score, proxy))
    best = 0.0
    for _score, proxy in sorted(quick, key=lambda item: item[0], reverse=True)[:16]:
        result = _audit_score(proxy, hp_data, scene_diag)
        if result:
            best = max(best, float(result["score"]))
    return best


def _contains_point(data, point, padding=0.0):
    return all(data["min"][axis] - padding <= point[axis] <= data["max"][axis] + padding for axis in range(3))


def _mark_container_proxies(proxies):
    sources = {}
    for proxy in proxies:
        sources.setdefault(proxy["lp_object"].as_pointer(), {
            "data": proxy["node_data"], "signature_size": proxy["signature_size"]
        })
    median_volume = _median((info["data"]["volume"] for info in sources.values()))
    centers = tuple((pointer, info["data"]["center"]) for pointer, info in sources.items())
    containers = set()
    for pointer, info in sources.items():
        if info["signature_size"] <= 1:
            continue
        data = info["data"]
        padding = max(data["diag"] * 0.005, 1.0e-6)
        inside = sum(1 for other, center in centers if other != pointer and _contains_point(data, center, padding))
        ratio = inside / float(max(len(centers) - 1, 1))
        if data["volume"] >= max(median_volume * 6.0, 1.0e-6) and (inside >= 8 or ratio >= 0.08):
            containers.add(pointer)
    for proxy in proxies:
        proxy["is_container"] = proxy["lp_object"].as_pointer() in containers
    return len(containers)


def analyze_material_distribution(hp_root, lp_root, progress=None, context=None):
    """Port Maya's full Create-by-Material HP ownership/audit pipeline."""
    context = context or bpy.context
    depsgraph = context.evaluated_depsgraph_get()
    lp_objects = tuple(sorted(target_meshes(lp_root), key=lambda obj: obj.name_full.casefold()))
    hp_objects = tuple(sorted(target_meshes(hp_root), key=lambda obj: obj.name_full.casefold()))
    buckets_by_signature = {}
    all_proxies = []
    for index, obj in enumerate(lp_objects):
        _progress(progress, 5 + int(index * 25 / max(len(lp_objects), 1)), "Scanning LP materials: {}".format(obj.name))
        signature, labels, proxies = _lp_proxy_records(obj, depsgraph)
        if not signature:
            continue
        bucket = buckets_by_signature.setdefault(signature, {
            "signature": signature, "labels": labels, "lp": [], "hp": [], "lp_proxies": [],
        })
        bucket["lp"].append(obj)
        for proxy in proxies:
            proxy["bucket"] = bucket
            bucket["lp_proxies"].append(proxy)
            all_proxies.append(proxy)
    if not buckets_by_signature:
        return MaterialDistributionResult((), MaterialDistributionDiagnostics())

    used_labels = set()
    buckets = []
    for _signature, bucket in sorted(
        buckets_by_signature.items(), key=lambda item: " ".join(item[1]["labels"]).casefold()
    ):
        raw_label = "_".join(bucket["labels"]) or "Material"
        bucket["label"] = _unique_name(used_labels, raw_label)
        buckets.append(bucket)
    container_count = _mark_container_proxies(all_proxies)

    hp_records = {}
    for index, obj in enumerate(hp_objects):
        _progress(progress, 30 + int(index * 5 / max(len(hp_objects), 1)), "Reading HP geometry: {}".format(obj.name))
        data = _mesh_data(obj, depsgraph, 48)
        if data is not None:
            hp_records[obj] = {
                "data": data, "bucket": None, "score": 0.0, "strong": False,
                "source": "unassigned", "has_overlap": False,
            }
    scene_diag = _median(
        [proxy["data"]["diag"] for proxy in all_proxies]
        + [record["data"]["diag"] for record in hp_records.values()]
    )
    median_hp_diag = _median(
        (record["data"]["diag"] for record in hp_records.values()), scene_diag
    )

    direct_count = container_direct_count = low_confidence = 0
    for index, (obj, record) in enumerate(hp_records.items()):
        _progress(progress, 35 + int(index * 22 / max(len(hp_records), 1)), "Resolving HP ownership: {}".format(obj.name))
        quick = []
        for proxy in all_proxies:
            score = _quick_owner_score(record["data"], proxy)
            if score is not None:
                quick.append((score, proxy))
        scored = [
            _hp_owner_score(record["data"], proxy, scene_diag)
            for _score, proxy in sorted(quick, key=lambda item: item[0], reverse=True)[:48]
        ]
        scored.sort(key=lambda item: item["score"], reverse=True)
        non_containers = [item for item in scored if item["confident"] and not item["proxy"].get("is_container")]
        containers = [item for item in scored if item["confident"] and item["proxy"].get("is_container")]
        best = non_containers[0] if non_containers else (containers[0] if containers else None)
        if best is None:
            continue
        record.update({
            "bucket": best["proxy"]["bucket"], "score": best["score"],
            "strong": best["strong"], "source": "container" if best["proxy"].get("is_container") else "lp",
            "has_overlap": best["has_overlap"],
        })
        direct_count += 1
        low_confidence += int(not best["has_overlap"])
        container_direct_count += int(bool(best["proxy"].get("is_container")))

    _progress(progress, 58, "Attaching HP floaters to large owners")
    assigned = [(obj, record) for obj, record in hp_records.items() if record["bucket"] is not None]
    stable_parents = [
        (obj, record) for obj, record in assigned
        if record["strong"] and record["data"]["diag"] >= median_hp_diag * 0.35
    ]
    floater_assigned = floater_reassigned = 0
    for index, (obj, record) in enumerate(hp_records.items()):
        _progress(progress, 58 + int(index * 12 / max(len(hp_records), 1)), "Attaching HP floaters: {}".format(obj.name))
        hp_diag = record["data"]["diag"]
        if record["strong"] and record["source"] == "lp" and hp_diag >= median_hp_diag * 0.70:
            continue
        parents = []
        for parent_obj, parent_record in stable_parents:
            if parent_obj == obj:
                continue
            score = _hp_parent_score(record["data"], parent_record["data"], scene_diag)
            if score is not None:
                parents.append((score, parent_record))
        if not parents:
            continue
        parent_score, parent = max(parents, key=lambda item: item[0])
        current_score = float(record["score"])
        can_relink = record["bucket"] is None or not record["strong"] or record["source"] == "container" or current_score < 75.0
        if not can_relink or (parent_score < 72.0 and record["bucket"] is not None) or parent_score < 54.0:
            continue
        old_bucket = record["bucket"]
        record.update({"bucket": parent["bucket"], "source": "floater", "strong": False, "score": max(current_score, parent_score)})
        if old_bucket is None:
            floater_assigned += 1
        elif old_bucket is not record["bucket"]:
            floater_reassigned += 1

    _progress(progress, 70, "Checking LP meshes for missing HP")
    audit_checked = audit_candidates = audit_assigned = audit_reassigned = audit_container_conflicts = 0
    best_audit = {}
    audit_proxies = sorted(all_proxies, key=lambda proxy: (1 if proxy.get("is_container") else 0, proxy["data"]["volume"]))
    for index, proxy in enumerate(audit_proxies):
        _progress(progress, 70 + int(index * 10 / max(len(audit_proxies), 1)), "Auditing LP region: {}".format(proxy["lp_object"].name))
        target_bucket = proxy.get("bucket")
        if target_bucket is None:
            continue
        audit_checked += 1
        quick = []
        for obj, record in hp_records.items():
            if record["bucket"] is target_bucket:
                continue
            score = _audit_quick_score(proxy, record["data"], scene_diag)
            if score is not None:
                quick.append((score, obj, record))
        for _score, obj, record in sorted(quick, key=lambda item: item[0], reverse=True)[:24]:
            result = _audit_score(proxy, record["data"], scene_diag)
            if not result or not result["strong"]:
                continue
            audit_candidates += 1
            current = best_audit.get(obj)
            if current is None or result["score"] > current["score"]:
                best_audit[obj] = {"score": result["score"], "bucket": target_bucket, "proxy": proxy}
    for obj, candidate in best_audit.items():
        record = hp_records[obj]
        target_bucket, current_bucket = candidate["bucket"], record["bucket"]
        if target_bucket is current_bucket:
            continue
        if candidate["proxy"].get("is_container"):
            audit_container_conflicts += 1
            continue
        current_score = _best_bucket_audit_score(current_bucket, record["data"], scene_diag)
        needs_repair = (
            current_bucket is None or record["source"] in {"container", "unassigned"}
            or not record["strong"] or candidate["score"] >= current_score + 85.0
            or (current_score <= 1.0 and candidate["score"] >= 260.0)
        )
        if not needs_repair:
            continue
        old_bucket = current_bucket
        record.update({"bucket": target_bucket, "source": "lp_audit", "strong": False, "score": max(record["score"], candidate["score"])})
        if old_bucket is None:
            audit_assigned += 1
        else:
            audit_reassigned += 1

    review = []
    for obj, record in hp_records.items():
        if record["bucket"] is None:
            review.append(obj)
        else:
            record["bucket"]["hp"].append(obj)
    if review:
        review_label = _unique_name(used_labels, "Review_Unmatched")
        buckets.append({
            "signature": ("Review_Unmatched",), "label": review_label,
            "lp": [], "hp": review, "lp_proxies": [],
        })

    result_buckets = tuple(MaterialBucket(
        signature=tuple(bucket["signature"]), label=bucket["label"],
        lp_objects=tuple(bucket["lp"]), hp_objects=tuple(bucket["hp"]),
    ) for bucket in buckets)
    diagnostics = MaterialDistributionDiagnostics(
        lp_proxy_count=len(all_proxies), container_count=container_count,
        direct_hp=direct_count, container_hp=container_direct_count,
        floater_assigned=floater_assigned, floater_reassigned=floater_reassigned,
        lp_audit_checked=audit_checked, lp_audit_candidates=audit_candidates,
        lp_audit_assigned=audit_assigned, lp_audit_reassigned=audit_reassigned,
        lp_audit_container_conflicts=audit_container_conflicts,
        low_confidence_hp=low_confidence,
        review_hp=tuple(obj.name_full for obj in review),
    )
    return MaterialDistributionResult(result_buckets, diagnostics)


def build_material_buckets(hp_root, lp_root, progress=None, context=None):
    """Compatibility wrapper returning buckets from the audited Maya pipeline."""
    return analyze_material_distribution(hp_root, lp_root, progress, context).buckets


def add_object_refs(refs, objects):
    for obj in objects:
        ref = refs.add()
        ref.target = obj
        ref.last_name = obj.name
