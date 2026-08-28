"""Pure-Python HP grouping service.

The Maya worker mixes scene queries, geometry extraction and grouping in one
QThread.  This port keeps the worker contract but makes the calculation host
agnostic: it consumes immutable snapshots and returns an immutable plan.
"""

from __future__ import annotations

from collections import defaultdict
from math import floor, sqrt
from statistics import mean, median

from . import native_core
from .domain.analysis import AnalysisGroup, AnalysisResult, AnalysisSettings, MeshSnapshot


_EPSILON = 1.0e-9


def _distance(a, b):
    return sqrt(sum((a[index] - b[index]) ** 2 for index in range(3)))


def _bbox_gap(a, b):
    squared = 0.0
    for axis in range(3):
        if a.bbox_max[axis] < b.bbox_min[axis]:
            delta = b.bbox_min[axis] - a.bbox_max[axis]
        elif b.bbox_max[axis] < a.bbox_min[axis]:
            delta = a.bbox_min[axis] - b.bbox_max[axis]
        else:
            delta = 0.0
        squared += delta * delta
    return sqrt(squared)


def _bbox_overlaps(a, b, padding=0.0):
    return all(
        a.bbox_max[axis] + padding >= b.bbox_min[axis]
        and b.bbox_max[axis] + padding >= a.bbox_min[axis]
        for axis in range(3)
    )


def _volume_similarity(a, b):
    high = max(a.bbox_volume, b.bbox_volume, _EPSILON)
    return max(0.0, 1.0 - abs(a.bbox_volume - b.bbox_volume) / high)


def _shape_signature(mesh):
    high = max(mesh.dimensions) if mesh.dimensions else 0.0
    if high <= _EPSILON:
        return 0.0, 0.0, 0.0
    return tuple(sorted(round(value / high, 3) for value in mesh.dimensions))


def _shape_similarity(a, b):
    left = _shape_signature(a)
    right = _shape_signature(b)
    return max(0.0, 1.0 - sum(abs(left[i] - right[i]) for i in range(3)) / 3.0)


def _topology_similarity(a, b):
    values = []
    for left, right in zip(a.topology, b.topology):
        values.append(min(left, right) / float(max(left, right, 1)))
    return sum(values) / len(values)


def _sample_vertices(mesh, maximum):
    vertices = mesh.vertices
    if len(vertices) <= maximum:
        return vertices
    step = len(vertices) / float(maximum)
    return tuple(vertices[min(len(vertices) - 1, int(index * step))] for index in range(maximum))


def _near_vertex_hits(a, b, threshold, required, sample_cap):
    """Count proximity hits with a compact spatial hash and an early exit."""
    if threshold <= 0.0 or not a.vertices or not b.vertices:
        return 0
    left = _sample_vertices(a, sample_cap)
    right = _sample_vertices(b, sample_cap)
    # The common owner-test asks only whether one close pair exists.  This is
    # exactly a global minimum-distance query and maps to the C++ spatial grid
    # without changing the pure-Python service contract.
    if required <= 1:
        native_distance = native_core.calculate_min_distance(left, right)
        if native_distance is not None:
            return 1 if native_distance <= threshold else 0
    if len(left) > len(right):
        left, right = right, left
    inverse = 1.0 / threshold
    grid = defaultdict(list)
    for point in right:
        cell = tuple(floor(value * inverse) for value in point)
        grid[cell].append(point)
    limit_sq = threshold * threshold
    hits = 0
    for point in left:
        base = tuple(floor(value * inverse) for value in point)
        found = False
        for x in range(base[0] - 1, base[0] + 2):
            for y in range(base[1] - 1, base[1] + 2):
                for z in range(base[2] - 1, base[2] + 2):
                    for other in grid.get((x, y, z), ()):
                        if sum((point[i] - other[i]) ** 2 for i in range(3)) <= limit_sq:
                            found = True
                            break
                    if found:
                        break
                if found:
                    break
            if found:
                break
        if found:
            hits += 1
            if hits >= required:
                return hits
    return hits


class _UnionFind:
    def __init__(self, keys):
        self.parent = {key: key for key in keys}

    def find(self, key):
        parent = self.parent[key]
        if parent != key:
            self.parent[key] = self.find(parent)
        return self.parent[key]

    def union(self, left, right):
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root

    def components(self):
        result = defaultdict(list)
        for key in self.parent:
            result[self.find(key)].append(key)
        return list(result.values())


class AnalysisService:
    """Create an HP membership plan without reading or modifying Blender."""

    def analyze(self, hp_meshes, lp_meshes, settings, reserved_names=(), progress=None):
        hp_meshes = tuple(hp_meshes)
        lp_meshes = tuple(lp_meshes)
        if not hp_meshes:
            raise ValueError("No unlocked HP meshes found to analyze")

        hp_by_key = {mesh.key: mesh for mesh in hp_meshes}
        lp_by_key = {mesh.key: mesh for mesh in lp_meshes}
        if len(hp_by_key) != len(hp_meshes) or len(lp_by_key) != len(lp_meshes):
            raise ValueError("Analysis snapshots must have unique keys")

        scene_diagonal = max((mesh.diagonal for mesh in hp_meshes), default=1.0)
        scene_diagonal = max(scene_diagonal, _EPSILON)
        sample_cap = 2048 if settings.optimization == "OPTIMAL" else 512
        collision_ratio = max(0.0, min(float(settings.collision_pct) / 100.0, 1.0))
        debug = [
            "Analyze HP (Blender service)",
            "math backend={}".format(native_core.backend_name()),
            "strategy={} optimization={} collision={}%".format(
                settings.strategy, settings.optimization, settings.collision_pct
            ),
            "input: {} HP / {} LP; locked HP excluded upstream".format(len(hp_meshes), len(lp_meshes)),
        ]
        if progress:
            progress.update(5, "Preparing HP and LP snapshots")

        owner_by_hp = {}
        for hp_index, hp in enumerate(hp_meshes):
            best = self._best_lp_owner(hp, lp_meshes, settings, collision_ratio, sample_cap)
            if best is not None:
                owner_by_hp[hp.key] = best.key
                debug.append("LP_OWNER {} -> {}".format(hp.name, best.name))
            if progress:
                progress.update(5 + int((hp_index + 1) * 30 / max(1, len(hp_meshes))), "Matching HP to LP: {}".format(hp.name))

        union = _UnionFind(hp_by_key)
        compound_links = 0
        if settings.adjacent_link and len(hp_meshes) > 1:
            link_distance = scene_diagonal * max(settings.link_distance_pct, 0.01) / 100.0
            for index, left in enumerate(hp_meshes):
                for right in hp_meshes[index + 1:]:
                    if left.is_zbrush != right.is_zbrush:
                        continue
                    if (
                        left.semantic_group and right.semantic_group
                        and left.semantic_group != right.semantic_group
                    ):
                        continue
                    left_owner = owner_by_hp.get(left.key)
                    right_owner = owner_by_hp.get(right.key)
                    if left_owner and right_owner and left_owner != right_owner:
                        continue
                    if not _bbox_overlaps(left, right, link_distance):
                        continue
                    hits = _near_vertex_hits(
                        left, right, link_distance, max(1, settings.link_vertex), sample_cap
                    )
                    if hits >= max(1, settings.link_vertex):
                        union.union(left.key, right.key)
                        compound_links += 1
                        debug.append("COMPOUND {} + {} ({} hits)".format(left.name, right.name, hits))
                if progress:
                    progress.update(35 + int((index + 1) * 15 / max(1, len(hp_meshes))), "Linking adjacent HP")

        # A semantic island imported from Maya is equivalent to a hard GT/custom
        # cluster.  LP ownership alone is *not* a hard cluster in Maya: one LP
        # owner can contribute context to several collision-safe HP groups.
        by_semantic_group = defaultdict(list)
        for hp in hp_meshes:
            if hp.semantic_group:
                by_semantic_group[hp.semantic_group].append(hp.key)
        for keys in by_semantic_group.values():
            for other in keys[1:]:
                union.union(keys[0], other)
        if by_semantic_group:
            debug.append(
                "semantic hard clusters: {} group(s), {} HP".format(
                    len(by_semantic_group), sum(len(keys) for keys in by_semantic_group.values())
                )
            )
        debug.append("ZBrush HP snapshots: {}".format(sum(1 for mesh in hp_meshes if mesh.is_zbrush)))

        similarity_links = self._link_similar_unmatched(
            hp_meshes, owner_by_hp, union, settings, debug
        )
        if progress:
            progress.update(65, "Clustering similar HP meshes")

        floater_links = 0
        if not settings.ignore_floaters:
            floater_links = self._attach_floaters(hp_meshes, owner_by_hp, union, debug)
        if progress:
            progress.update(78, "Packing subgroup components")

        components = []
        for keys in union.components():
            meshes = [hp_by_key[key] for key in keys]
            owners = sorted({owner_by_hp[key] for key in keys if key in owner_by_hp})
            components.append((meshes, owners))

        groups, warnings = self._pack_components(
            components, settings, reserved_names, hp_by_key, lp_by_key, debug
        )
        if progress:
            progress.update(98, "Validating HP ownership")
        matched = len(owner_by_hp)
        debug.append(
            "result: {} subgroup(s), {} matched, {} unmatched, {} compound / {} similarity / {} floater link(s)".format(
                len(groups), matched, len(hp_meshes) - matched, compound_links, similarity_links, floater_links
            )
        )
        return AnalysisResult(
            groups=tuple(groups),
            processed_hp=len(hp_meshes),
            matched_hp=matched,
            unmatched_hp=len(hp_meshes) - matched,
            compound_components=sum(1 for keys in union.components() if len(keys) > 1),
            compound_links=compound_links,
            floater_links=floater_links,
            warnings=tuple(warnings),
            debug_lines=tuple(debug),
        )

    @staticmethod
    def _link_similar_unmatched(hp_meshes, owner_by_hp, union, settings, debug):
        """Maya's fingerprint fallback groups similar, non-colliding singletons."""
        unmatched = [mesh for mesh in hp_meshes if mesh.key not in owner_by_hp]
        links = 0
        for index, left in enumerate(unmatched):
            for right in unmatched[index + 1:]:
                if left.is_zbrush != right.is_zbrush:
                    continue
                if left.semantic_group or right.semantic_group:
                    if left.semantic_group != right.semantic_group:
                        continue
                if _bbox_overlaps(left, right):
                    continue
                if settings.strategy == "TOPOLOGY":
                    similar = left.topology == right.topology
                elif settings.strategy == "SPATIAL":
                    similar = _volume_similarity(left, right) >= 0.95
                else:
                    similar = (
                        _volume_similarity(left, right) >= 0.95
                        and _shape_similarity(left, right) >= 0.80
                    )
                if similar and union.find(left.key) != union.find(right.key):
                    union.union(left.key, right.key)
                    links += 1
                    debug.append("SIMILAR {} + {} ({})".format(
                        left.name, right.name, settings.strategy
                    ))
        return links

    @staticmethod
    def _best_lp_owner(hp, lp_meshes, settings, collision_ratio, sample_cap):
        best = None
        best_score = -1.0
        for lp in lp_meshes:
            scale = max(hp.diagonal, lp.diagonal, _EPSILON)
            allowed_gap = scale * max(0.02, collision_ratio)
            gap = _bbox_gap(hp, lp)
            if gap > allowed_gap:
                continue
            distance_score = max(0.0, 1.0 - gap / max(allowed_gap, _EPSILON))
            center_score = max(0.0, 1.0 - _distance(hp.center, lp.center) / (scale * 1.5))
            if settings.strategy == "SPATIAL":
                strategy_score = _volume_similarity(hp, lp)
            elif settings.strategy == "TOPOLOGY":
                # HP and LP vertex counts naturally differ; proportions and aspect
                # are therefore more useful than requiring an exact fingerprint.
                strategy_score = (_topology_similarity(hp, lp) * 0.35) + (_shape_similarity(hp, lp) * 0.65)
            else:
                threshold = max(allowed_gap * 0.35, scale * 0.001)
                hits = _near_vertex_hits(hp, lp, threshold, 1, min(sample_cap, 768))
                strategy_score = 1.0 if hits else _shape_similarity(hp, lp) * 0.45
            score = distance_score * 0.45 + center_score * 0.25 + strategy_score * 0.30
            if score > best_score:
                best_score = score
                best = lp
        return best if best_score >= 0.30 else None

    @staticmethod
    def _attach_floaters(hp_meshes, owner_by_hp, union, debug):
        sizes = [mesh.diagonal for mesh in hp_meshes if mesh.diagonal > _EPSILON]
        radius = max((median(sizes) if sizes else 1.0) * 0.02, 0.0001)
        linked = 0
        ordered = sorted(hp_meshes, key=lambda mesh: mesh.diagonal)
        for floater in ordered:
            if floater.key in owner_by_hp:
                continue
            candidates = []
            for parent in hp_meshes:
                if parent.key == floater.key or parent.diagonal < floater.diagonal * 1.2:
                    continue
                if parent.is_zbrush != floater.is_zbrush:
                    continue
                if (
                    parent.semantic_group and floater.semantic_group
                    and parent.semantic_group != floater.semantic_group
                ):
                    continue
                gap = _bbox_gap(floater, parent)
                if gap <= radius:
                    candidates.append((gap, -parent.diagonal, parent))
            if candidates:
                parent = min(candidates, key=lambda item: (item[0], item[1], item[2].name))[2]
                union.union(parent.key, floater.key)
                if parent.key in owner_by_hp:
                    owner_by_hp[floater.key] = owner_by_hp[parent.key]
                linked += 1
                debug.append("FLOATER {} -> {}".format(floater.name, parent.name))
        return linked

    @staticmethod
    def _component_bbox(component):
        meshes, _owners = component
        minimum = tuple(min(mesh.bbox_min[axis] for mesh in meshes) for axis in range(3))
        maximum = tuple(max(mesh.bbox_max[axis] for mesh in meshes) for axis in range(3))
        dimensions = tuple(maximum[axis] - minimum[axis] for axis in range(3))
        diagonal = sqrt(sum(value * value for value in dimensions))
        volume = dimensions[0] * dimensions[1] * dimensions[2]
        return minimum, maximum, diagonal, volume

    @staticmethod
    def _component_metrics(component):
        meshes, _owners = component
        _minimum, _maximum, bbox_diagonal, bbox_volume = AnalysisService._component_bbox(component)
        total_volume = sum(max(mesh.bbox_volume, 0.0) for mesh in meshes)
        fill_ratio = total_volume / max(bbox_volume, _EPSILON)
        scattered = len(meshes) > 1 and fill_ratio < 0.05
        if scattered:
            equivalent_volume = max((mesh.bbox_volume for mesh in meshes), default=0.0) * 2.0
        else:
            equivalent_volume = total_volume
        equivalent_diagonal = sqrt(3.0) * (max(equivalent_volume, 0.0) ** (1.0 / 3.0))
        return equivalent_diagonal or bbox_diagonal, total_volume, fill_ratio

    @staticmethod
    def _size_thresholds(components):
        meshes = [mesh for component in components for mesh in component[0]]
        raw_diagonals = [
            mesh.diagonal for mesh in meshes
            if mesh.diagonal > 0.001 and mesh.bbox_volume > 1.0e-6
        ]
        scene_median = median(raw_diagonals or [1.0])
        valid = sorted(
            (
                mesh.diagonal for mesh in meshes
                if 0.001 < mesh.diagonal <= scene_median * 10.0
                and mesh.bbox_volume > 1.0e-6
            ),
            reverse=True,
        )
        upper_shelf = mean(valid[: min(10, len(valid))]) if valid else 1.0
        by_vertex_count = defaultdict(list)
        for mesh in meshes:
            if (
                not mesh.is_zbrush
                and mesh.bbox_volume > 1.0e-6
                and mesh.diagonal <= scene_median * 10.0
            ):
                by_vertex_count[mesh.vertex_count].append(mesh.diagonal)
        repeated = [values for values in by_vertex_count.values() if len(values) >= 2]
        largest = max(repeated, key=len, default=())
        bolt_median = mean(largest) if largest else 0.0
        if bolt_median <= 0.0 or bolt_median > upper_shelf * 0.25:
            bolt_median = valid[-1] * 1.5 if valid else 0.1
        small = bolt_median * 1.5
        large = upper_shelf * 0.6
        medium = (small + large) * 0.5
        repeated_bolt_vertices = {
            vertex_count for vertex_count, diagonals in by_vertex_count.items()
            if len(diagonals) >= 2 and mean(diagonals) <= medium * 1.15
        }
        return scene_median, upper_shelf, bolt_median, small, medium, large, repeated_bolt_vertices

    @staticmethod
    def _shape_is_bolt(mesh, settings):
        metrics = native_core.analyze_mesh_shape(mesh.vertices) if mesh.vertices else None
        if metrics is None:
            return False
        try:
            elongation = float(metrics.elongation)
            symmetry = float(metrics.symmetry_score)
        except AttributeError:
            try:
                elongation = float(metrics[0])
                symmetry = float(metrics[1])
            except (IndexError, TypeError, ValueError):
                return False
        return elongation < 3.0 and (not settings.use_symmetry or symmetry < 0.35)

    def _category(self, component, settings, thresholds):
        meshes, _owners = component
        _scene_median, _upper_shelf, _bolt_median, small, medium, large, bolt_vertices = thresholds
        diagonal, _volume, _fill_ratio = self._component_metrics(component)
        is_zbrush = bool(meshes) and any(mesh.is_zbrush for mesh in meshes)
        max_single_diagonal = max((mesh.diagonal for mesh in meshes), default=0.0)
        bolt_like = [
            mesh for mesh in meshes
            if (
                mesh.diagonal <= small
                or (mesh.vertex_count in bolt_vertices and mesh.diagonal <= medium * 1.15)
                or (mesh.diagonal <= medium and self._shape_is_bolt(mesh, settings))
            )
        ]
        mixed_bolt_item = (
            len(meshes) > 1
            and bolt_like
            and (len(bolt_like) == len(meshes) or diagonal <= medium)
            and max_single_diagonal <= large
        )
        single_bolt_item = len(meshes) == 1 and bool(bolt_like) and diagonal <= medium
        if not is_zbrush and (single_bolt_item or mixed_bolt_item or max_single_diagonal <= small):
            return "Bolts"
        if diagonal <= medium:
            category = "Medium"
        elif diagonal <= large:
            category = "Large"
        else:
            category = "Huge"
        return "ZBrush_" + category if is_zbrush else category

    @staticmethod
    def _components_collide(left, right, tolerance):
        left_meshes, _ = left
        right_meshes, _ = right
        for left_mesh in left_meshes:
            for right_mesh in right_meshes:
                if not _bbox_overlaps(left_mesh, right_mesh, tolerance):
                    continue
                native = native_core.check_mesh_collision(
                    left_mesh.vertices, right_mesh.vertices, tolerance
                ) if left_mesh.vertices and right_mesh.vertices else None
                if native is None:
                    return True
                if native:
                    return True
        return False

    @staticmethod
    def _semantic_category(name):
        return name.rsplit("_", 1)[0] if "_" in name else name

    def _pack_components(self, components, settings, reserved_names, hp_by_key, lp_by_key, debug=None):
        if not components:
            return [], []
        debug = debug if debug is not None else []
        thresholds = self._size_thresholds(components)
        scene_median, upper_shelf, bolt_median, small, medium, large, _bolt_vertices = thresholds
        debug.append(
            "thresholds: median={:.6f} upper={:.6f} bolt={:.6f} small={:.6f} medium={:.6f} large={:.6f}".format(
                scene_median, upper_shelf, bolt_median, small, medium, large
            )
        )

        by_category = defaultdict(list)
        semantic_components = []
        for component in components:
            semantic_names = {mesh.semantic_group for mesh in component[0] if mesh.semantic_group}
            if len(semantic_names) == 1 and all(mesh.semantic_group for mesh in component[0]):
                semantic_components.append((next(iter(semantic_names)), component))
            else:
                by_category[self._category(component, settings, thresholds)].append(component)

        groups = []
        warnings = []
        used_names = set(reserved_names)

        # Preserve Maya GT/custom-style semantic islands first.  This is what
        # keeps a round-tripped chapter stable instead of re-packing it purely
        # from Blender AABBs.
        for requested_name, component in sorted(semantic_components, key=lambda item: item[0]):
            name = requested_name
            suffix = 1
            while name in used_names:
                name = "{}_Auto_{:03d}".format(requested_name, suffix)
                suffix += 1
            used_names.add(name)
            meshes, owners = component
            groups.append(AnalysisGroup(
                name=name,
                hp_keys=tuple(sorted((mesh.key for mesh in meshes), key=lambda key: hp_by_key[key].name)),
                lp_owner_keys=tuple(sorted(
                    set(owners), key=lambda key: lp_by_key[key].name if key in lp_by_key else key
                )),
                category=self._semantic_category(requested_name),
            ))

        # Maya's check_mesh_collision uses 0.005 cm at the 15% default.  Convert
        # that physical tolerance to Blender units and scale gently with the UI.
        collision_factor = max(0.1, 1.0 + (float(settings.collision_pct) / 100.0 - 0.15))
        collision_tolerance = (0.00005 / max(settings.unit_scale_meters, 1.0e-12)) * collision_factor
        category_order = (
            "ZBrush_Huge", "ZBrush_Large", "ZBrush_Medium", "ZBrush_Small",
            "Huge", "Large", "Medium", "Small", "Bolts", "ZBrush_Bolts",
        )
        for category in category_order:
            buckets = []
            ordered = sorted(
                by_category.get(category, ()),
                key=lambda item: (-self._component_bbox(item)[3], item[0][0].name),
            )
            if category in {"Bolts", "ZBrush_Bolts"} and ordered:
                # Maya makes one semantic bolt subgroup per prefix/family.  Bolt
                # meshes are not split merely because their AABBs overlap.
                buckets = [ordered]
                ordered = ()
            for component in ordered:
                bucket = next(
                    (candidate for candidate in buckets if not any(
                        self._components_collide(component, existing, collision_tolerance)
                        for existing in candidate
                    )),
                    None,
                )
                if bucket is None:
                    bucket = []
                    buckets.append(bucket)
                bucket.append(component)

            for bucket_index, bucket in enumerate(buckets, 1):
                number = bucket_index
                name = "{}_{:03d}".format(category, number)
                while name in used_names:
                    number += 1
                    name = "{}_{:03d}".format(category, number)
                used_names.add(name)
                hp_keys = sorted(
                    {mesh.key for component in bucket for mesh in component[0]},
                    key=lambda key: hp_by_key[key].name,
                )
                lp_keys = sorted(
                    {key for component in bucket for key in component[1]},
                    key=lambda key: lp_by_key[key].name if key in lp_by_key else key,
                )
                groups.append(AnalysisGroup(name, tuple(hp_keys), tuple(lp_keys), category))

        debug.append(
            "category components: {}".format(
                ", ".join("{}={}".format(key, len(value)) for key, value in sorted(by_category.items()))
                or "none (semantic clusters only)"
            )
        )
        debug.append("semantic output groups: {}".format(len(semantic_components)))

        non_bolt = sum(1 for group in groups if group.category != "Bolts")
        if settings.group_limit > 0 and non_bolt > settings.group_limit:
            warnings.append(
                "{} non-bolt groups exceed target {}; collision-safe groups were preserved".format(
                    non_bolt, settings.group_limit
                )
            )
        return groups, warnings
