"""Two-stage implementation of Maya Find Sim / Find All.

The original Maya helper's vertex-count and bbox-ratio search is retained as a
cheap first stage. Only its shortlist reaches the evaluated, transform-
independent shape test which removes the old false positives. This preserves
the original speed on heterogeneous scenes without treating the prefilter as a
mesh identity test:

* Find All returns every shape match;
* Find Sim additionally requires a one-to-one matching layout.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt

from mathutils import Vector

from . import native_core
from .object_repository import ObjectRepository


_EPS = 1.0e-9
_PROFILE_SAMPLES = 32
_MAX_SIZE_RATIO = 3.0  # Preserve the Maya scale contract after shape filtering.


@dataclass(frozen=True, slots=True)
class FastSignature:
    """Cheap raw-data signature matching the original Maya prefilter."""

    center: tuple[float, float, float]
    diagonal: float
    vertex_count: int


@dataclass(frozen=True, slots=True)
class SimilaritySignature:
    center: tuple[float, float, float]
    diagonal: float
    rms_radius: float
    vertex_count: int
    edge_count: int
    face_count: int
    component_count: int
    face_valence: tuple[tuple[int, int], ...]
    radial_profile: tuple[float, ...]
    edge_profile: tuple[float, ...]
    area_profile: tuple[float, ...]
    native_fingerprint: str = ""

    @property
    def topology(self):
        return (
            self.vertex_count,
            self.edge_count,
            self.face_count,
            self.component_count,
            self.face_valence,
        )


def _distance(left, right):
    return sqrt(sum((left[index] - right[index]) ** 2 for index in range(3)))


def _fast_signature(obj):
    """Return the original Find Sim inputs without evaluating modifiers."""
    points = tuple(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
    if points:
        minimum = tuple(min(point[axis] for point in points) for axis in range(3))
        maximum = tuple(max(point[axis] for point in points) for axis in range(3))
        center = tuple((minimum[axis] + maximum[axis]) * 0.5 for axis in range(3))
        diagonal = sqrt(sum((maximum[axis] - minimum[axis]) ** 2 for axis in range(3)))
    else:
        center = tuple(float(value) for value in obj.matrix_world.translation)
        diagonal = 0.0
    return FastSignature(
        center=center,
        diagonal=diagonal,
        vertex_count=len(obj.data.vertices),
    )


def _fast_similar(target, candidate, signatures):
    """Reproduce Maya's raw vertex-count + world-BBox size gate."""
    target_signature = signatures[target]
    candidate_signature = signatures[candidate]
    if target_signature.vertex_count != candidate_signature.vertex_count:
        return False
    target_diagonal = target_signature.diagonal
    candidate_diagonal = candidate_signature.diagonal
    if target_diagonal <= _EPS or candidate_diagonal <= _EPS:
        return target_diagonal <= _EPS and candidate_diagonal <= _EPS
    return max(
        target_diagonal / candidate_diagonal,
        candidate_diagonal / target_diagonal,
    ) <= _MAX_SIZE_RATIO


def _quantile_profile(values, normalizer, samples=_PROFILE_SAMPLES):
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return ()
    safe = max(float(normalizer), _EPS)
    last = len(ordered) - 1
    return tuple(ordered[(index * last) // samples] / safe for index in range(samples + 1))


def _component_count(mesh):
    count = len(mesh.vertices)
    if count == 0:
        return 0
    parent = list(range(count))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left, right):
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for edge in mesh.edges:
        union(int(edge.vertices[0]), int(edge.vertices[1]))
    return len({find(index) for index in range(count)})


def _polygon_area(points, polygon):
    indices = tuple(int(index) for index in polygon.vertices)
    if len(indices) < 3:
        return 0.0
    origin = points[indices[0]]
    area = 0.0
    for index in range(1, len(indices) - 1):
        area += (points[indices[index]] - origin).cross(
            points[indices[index + 1]] - origin
        ).length * 0.5
    return area


def _evaluated_signature(obj, depsgraph):
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=False, depsgraph=depsgraph)
    try:
        vertex_count = len(mesh.vertices)
        if vertex_count:
            local_center = sum((vertex.co for vertex in mesh.vertices), Vector()) / vertex_count
        else:
            local_center = Vector()

        linear = evaluated.matrix_world.to_3x3()
        relative_points = tuple(linear @ (vertex.co - local_center) for vertex in mesh.vertices)
        radii = tuple(point.length for point in relative_points)
        rms_radius = sqrt(sum(value * value for value in radii) / vertex_count) if vertex_count else 0.0

        edge_lengths = tuple(
            (relative_points[int(edge.vertices[0])] - relative_points[int(edge.vertices[1])]).length
            for edge in mesh.edges
        )
        face_areas = tuple(_polygon_area(relative_points, polygon) for polygon in mesh.polygons)

        world_points = tuple(evaluated.matrix_world @ vertex.co for vertex in mesh.vertices)
        if world_points:
            minimum = tuple(min(point[axis] for point in world_points) for axis in range(3))
            maximum = tuple(max(point[axis] for point in world_points) for axis in range(3))
            center = tuple((minimum[axis] + maximum[axis]) * 0.5 for axis in range(3))
            diagonal = sqrt(sum((maximum[axis] - minimum[axis]) ** 2 for axis in range(3)))
        else:
            center = tuple(float(value) for value in evaluated.matrix_world.translation)
            diagonal = 0.0

        valence_counts = {}
        for polygon in mesh.polygons:
            valence = len(polygon.vertices)
            valence_counts[valence] = valence_counts.get(valence, 0) + 1

        normalized_points = tuple(tuple(point / max(rms_radius, _EPS)) for point in relative_points)
        native_fingerprint = ""
        if normalized_points and rms_radius > _EPS:
            try:
                native_fingerprint = native_core.generate_fingerprint(
                    normalized_points, (0.0, 0.0, 0.0)
                ) or ""
            except (RuntimeError, TypeError, ValueError):
                # ABI/load/validation failures must keep the Python matcher usable.
                native_fingerprint = ""

        return SimilaritySignature(
            center=center,
            diagonal=diagonal,
            rms_radius=rms_radius,
            vertex_count=vertex_count,
            edge_count=len(mesh.edges),
            face_count=len(mesh.polygons),
            component_count=_component_count(mesh),
            face_valence=tuple(sorted(valence_counts.items())),
            radial_profile=_quantile_profile(radii, rms_radius),
            edge_profile=_quantile_profile(edge_lengths, rms_radius),
            area_profile=_quantile_profile(face_areas, rms_radius * rms_radius),
            native_fingerprint=native_fingerprint,
        )
    finally:
        evaluated.to_mesh_clear()


def _profile_close(left, right, max_delta, mean_delta):
    if len(left) != len(right):
        return False
    if not left:
        return True
    deltas = tuple(abs(a - b) for a, b in zip(left, right))
    return max(deltas) <= max_delta and (sum(deltas) / len(deltas)) <= mean_delta


def _similar(target, candidate, signatures):
    target_signature = signatures[target]
    candidate_signature = signatures[candidate]
    if target_signature.topology != candidate_signature.topology:
        return False

    # RMS radius is invariant to object rotation, unlike a world-axis-aligned
    # bbox diagonal. It still preserves Maya's allowance for uniform scale.
    target_radius = target_signature.rms_radius
    candidate_radius = candidate_signature.rms_radius
    if target_radius <= _EPS or candidate_radius <= _EPS:
        if not (target_radius <= _EPS and candidate_radius <= _EPS):
            return False
    elif max(
        target_radius / candidate_radius,
        candidate_radius / target_radius,
    ) > _MAX_SIZE_RATIO:
        return False

    native_equal = bool(
        target_signature.native_fingerprint
        and target_signature.native_fingerprint == candidate_signature.native_fingerprint
    )
    if not native_equal and not _profile_close(
        target_signature.radial_profile,
        candidate_signature.radial_profile,
        max_delta=0.025,
        mean_delta=0.0075,
    ):
        return False
    if not _profile_close(
        target_signature.edge_profile,
        candidate_signature.edge_profile,
        max_delta=0.025,
        mean_delta=0.0075,
    ):
        return False
    return _profile_close(
        target_signature.area_profile,
        candidate_signature.area_profile,
        max_delta=0.05,
        mean_delta=0.015,
    )


def _layout_clusters(targets, matches_per_target, signatures, progress=None):
    target_positions = tuple(signatures[obj].center for obj in targets)
    expected = [[0.0] * len(targets) for _ in targets]
    layout_scale = max((signatures[obj].diagonal for obj in targets), default=1.0)
    for left in range(len(targets)):
        for right in range(left + 1, len(targets)):
            value = _distance(target_positions[left], target_positions[right])
            expected[left][right] = expected[right][left] = value
            layout_scale = max(layout_scale, value)
    absolute_floor = max(_EPS, layout_scale * 0.001)

    result = set()
    anchors = matches_per_target[0]
    for anchor_index, anchor in enumerate(anchors):
        cluster = [anchor]

        def extend(target_index):
            if target_index >= len(targets):
                return True
            for candidate in matches_per_target[target_index]:
                if candidate in cluster:
                    continue
                compatible = True
                for prior_index, prior_candidate in enumerate(cluster):
                    expected_distance = expected[target_index][prior_index]
                    actual_distance = _distance(
                        signatures[candidate].center,
                        signatures[prior_candidate].center,
                    )
                    tolerance = max(absolute_floor, expected_distance * 0.02)
                    if abs(actual_distance - expected_distance) > tolerance:
                        compatible = False
                        break
                if compatible:
                    cluster.append(candidate)
                    if extend(target_index + 1):
                        return True
                    cluster.pop()
            return False

        if extend(1):
            result.update(cluster)
        if progress:
            progress.update(
                80 + int((anchor_index + 1) * 18 / max(1, len(anchors))),
                "Checking layout clusters",
            )
    return result


def find_similar(context, state, pair, mode, progress=None):
    # Maya accepts only directly selected mesh transforms. Expanding a selected
    # Blender Empty into all descendants silently changes the target assembly.
    selected = tuple(obj for obj in context.selected_objects if obj.type == "MESH")
    if not selected:
        raise ValueError("No meshes found in current selection")
    side = ObjectRepository.classify(pair, selected[0])
    if side is None or any(ObjectRepository.classify(pair, obj) != side for obj in selected):
        raise ValueError("Selected meshes must belong to one side of the active chapter")
    candidates = tuple(dict.fromkeys(ObjectRepository.meshes_under_root(pair, side)))
    if not candidates:
        raise ValueError("The active {} root contains no meshes".format(side))
    targets = tuple(obj for obj in selected if obj in candidates)
    if not targets:
        raise ValueError("Selected meshes are outside the active root")

    # Stage 1 is the original Maya Find Sim search: raw vertex count plus the
    # world-space bbox ratio. It is deliberately cheap and intentionally
    # permissive; no evaluated meshes or C++ fingerprints are built here.
    fast_signatures = {}
    total = max(1, len(candidates))
    for index, obj in enumerate(candidates):
        fast_signatures[obj] = _fast_signature(obj)
        if progress:
            progress.update(
                int((index + 1) * 20 / total),
                "Fast Find Sim scan: {}".format(obj.name),
            )

    fast_matches_per_target = [
        tuple(
            candidate
            for candidate in candidates
            if _fast_similar(target, candidate, fast_signatures)
        )
        for target in targets
    ]
    shortlist = tuple(
        dict.fromkeys(
            obj
            for matches in fast_matches_per_target
            for obj in matches
        )
    )

    # Stage 2 evaluates only the shortlist and applies the shape/topology
    # signature. False positives admitted by the original search end here.
    depsgraph = context.evaluated_depsgraph_get()
    signatures = {}
    shortlist_total = max(1, len(shortlist))
    for index, obj in enumerate(shortlist):
        signatures[obj] = _evaluated_signature(obj, depsgraph)
        if progress:
            progress.update(
                20 + int((index + 1) * 50 / shortlist_total),
                "Validating geometry: {}".format(obj.name),
            )

    matches_per_target = []
    for index, (target, fast_matches) in enumerate(zip(targets, fast_matches_per_target)):
        matches = tuple(
            candidate
            for candidate in fast_matches
            if _similar(target, candidate, signatures)
        )
        matches_per_target.append(matches)
        if progress:
            progress.update(
                70 + int((index + 1) * 10 / max(1, len(targets))),
                "Matching shape: {}".format(target.name),
            )

    if mode == "ALL" or len(targets) == 1:
        result = {obj for matches in matches_per_target for obj in matches}
    else:
        result = _layout_clusters(targets, matches_per_target, signatures, progress)

    ordered = tuple(sorted(result, key=lambda obj: obj.name.lower()))
    if progress:
        progress.update(100, "Found {} mesh(es)".format(len(ordered)))
    return ordered, side
