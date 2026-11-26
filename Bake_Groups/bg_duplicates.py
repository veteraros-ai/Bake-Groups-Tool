"""Conservative world-space duplicate mesh detection for Maya."""

from __future__ import division

from collections import defaultdict
import math


def _cancelled(callback, done, total):
    if callback is None:
        return False
    return callback(done, total) is False


def _transform_paths(root, cmds):
    paths = cmds.ls(root, dag=True, type="transform", long=True, allPaths=True) or []
    if not paths:
        raise RuntimeError("No transform paths found under {!r}".format(root))
    return sorted(set(paths))


def _record(path, cmds, om):
    shape_paths = cmds.listRelatives(path, shapes=True, noIntermediate=True,
                                     fullPath=True) or []
    meshes = [shape for shape in shape_paths if cmds.nodeType(shape) == "mesh"]
    if not meshes:
        return None
    points, faces, shape_info = [], [], []
    for shape in meshes:
        selection = om.MSelectionList()
        selection.add(shape)
        dag = selection.getDagPath(0)
        fn = om.MFnMesh(dag)
        shape_points = fn.getPoints(om.MSpace.kWorld)
        offset = len(points)
        points.extend((float(p.x), float(p.y), float(p.z)) for p in shape_points)
        counts, connects = fn.getVertices()
        cursor = 0
        for count in counts:
            faces.append(tuple(offset + int(v) for v in connects[cursor:cursor + count]))
            cursor += count
        shape_info.append({"path": shape, "vertex_start": offset,
                           "face_start": len(faces) - len(counts),
                           "face_count": len(counts), "instance": dag.instanceNumber()})
    if not points:
        return None
    coords = tuple(zip(*points))
    bounds = tuple((min(axis), max(axis)) for axis in coords)
    return {"path": path, "shape_paths": meshes, "shape_info": shape_info,
            "points": points, "faces": faces, "bounds": bounds,
            "vertex_count": len(points), "face_count": len(faces)}


def _bbox_candidate(a, b, tolerance):
    if a["vertex_count"] != b["vertex_count"] or a["face_count"] != b["face_count"]:
        return False
    return all(abs(a["bounds"][axis][side] - b["bounds"][axis][side]) <= tolerance
               for axis in range(3) for side in range(2))


def _match_points(a, b, tolerance):
    """Return a vertex-index map for a one-to-one tolerance match, else None."""
    cell = tolerance if tolerance > 0 else 1e-12
    buckets = defaultdict(list)
    for index, point in enumerate(b):
        key = tuple(int(math.floor(value / cell)) for value in point)
        buckets[key].append(index)
    candidates_by_vertex = []
    tol2 = tolerance * tolerance
    for i, point in enumerate(a):
        key = tuple(int(math.floor(value / cell)) for value in point)
        candidates = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for j in buckets.get((key[0] + dx, key[1] + dy, key[2] + dz), ()):
                        q = b[j]
                        d2 = sum((point[k] - q[k]) ** 2 for k in range(3))
                        if d2 <= tol2 + 1e-24:
                            candidates.append((d2, j))
        if not candidates:
            return None
        candidates_by_vertex.append([j for _, j in sorted(candidates)])
    # Augmenting paths avoid a greedy assignment consuming the only valid match
    # for a later vertex when several vertices lie within the same tolerance.
    owner, assigned = {}, {}
    for vertex in sorted(range(len(a)), key=lambda idx: len(candidates_by_vertex[idx])):
        queue, seen_left, seen_right = [vertex], {vertex}, set()
        parent_right, free = {}, None
        cursor = 0
        while cursor < len(queue) and free is None:
            current = queue[cursor]
            cursor += 1
            for candidate in candidates_by_vertex[current]:
                if candidate in seen_right:
                    continue
                seen_right.add(candidate)
                parent_right[candidate] = current
                if candidate not in owner:
                    free = candidate
                    break
                next_left = owner[candidate]
                if next_left not in seen_left:
                    seen_left.add(next_left)
                    queue.append(next_left)
        if free is None:
            return None
        candidate = free
        while True:
            current = parent_right[candidate]
            previous = assigned.get(current)
            owner[candidate] = current
            assigned[current] = candidate
            if previous is None:
                break
            candidate = previous
    return assigned


def _canonical_cycle(face):
    face = tuple(face)
    cycles = [face[i:] + face[:i] for i in range(len(face))]
    reverse = tuple(reversed(face))
    cycles.extend(reverse[i:] + reverse[:i] for i in range(len(reverse)))
    return min(cycles) if cycles else ()


def _same_winding_cycle(a, b):
    if len(a) != len(b):
        return False
    return any(a[i:] + a[:i] == b for i in range(len(a)))


def _uv_signatures(faces, values, vertex_map=None):
    signatures = []
    for face, corners in zip(faces, values):
        mapped_face = tuple(vertex_map[v] for v in face) if vertex_map else face
        mapped_corners = tuple(sorted(
            ((vertex_map[vertex] if vertex_map else vertex), uv_values)
            for vertex, uv_values in corners))
        signatures.append((_canonical_cycle(mapped_face), mapped_corners))
    return sorted(signatures)


def _topology(a, b, mapping):
    if len(a["faces"]) != len(b["faces"]):
        return False, False
    mapped = [tuple(mapping[v] for v in face) for face in a["faces"]]
    face_keys = defaultdict(list)
    for face in b["faces"]:
        face_keys[_canonical_cycle(face)].append(face)
    winding_equal = True
    for face in mapped:
        matches = face_keys.get(_canonical_cycle(face))
        if not matches:
            return False, False
        same = next((candidate for candidate in matches
                     if _same_winding_cycle(candidate, face)), None)
        if same is not None:
            matches.remove(same)
        else:
            winding_equal = False
            matches.pop()
    if any(values for values in face_keys.values()):
        return False, False
    return True, winding_equal


def _closed_orientation(record, vertex_map=None):
    """Return outward/inward for a consistently wound, closed mesh record.

    Maya polygon vertex order is used rather than shading normals. Open,
    non-manifold, inconsistently wound, and near-zero-volume meshes are
    deliberately reported as ambiguous.
    """
    orientations = []
    for shape in record["shape_info"]:
        volume = 0.0
        for face in record["faces"][shape["face_start"]:
                                     shape["face_start"] + shape["face_count"]]:
            mapped = tuple(vertex_map[v] for v in face) if vertex_map else face
            if len(mapped) < 3:
                return None
            p0 = record["points"][face[0]]
            for index in range(1, len(face) - 1):
                p1, p2 = record["points"][face[index]], record["points"][face[index + 1]]
                volume += (p0[0] * (p1[1] * p2[2] - p1[2] * p2[1]) +
                           p0[1] * (p1[2] * p2[0] - p1[0] * p2[2]) +
                           p0[2] * (p1[0] * p2[1] - p1[1] * p2[0])) / 6.0
        if any(abs(direction) != 0 or count != 2
               for (count, direction) in _edge_balance(
                   record["faces"][shape["face_start"]:
                                 shape["face_start"] + shape["face_count"]], vertex_map).values()):
            return None
        if abs(volume) <= 1e-12:
            return None
        orientations.append(1 if volume > 0 else -1)
    if not orientations or any(value != orientations[0] for value in orientations):
        return None
    return "outward" if orientations[0] > 0 else "inward"


def _face_normal(points, face):
    """Compute a normalized world-space polygon normal from its winding."""
    x = y = z = 0.0
    for index, vertex in enumerate(face):
        current = points[vertex]
        following = points[face[(index + 1) % len(face)]]
        x += (current[1] - following[1]) * (current[2] + following[2])
        y += (current[2] - following[2]) * (current[0] + following[0])
        z += (current[0] - following[0]) * (current[1] + following[1])
    length = math.sqrt(x * x + y * y + z * z)
    if length <= 1e-12:
        return None
    return (x / length, y / length, z / length)


def _visible_orientation(record, details, vertex_map=None):
    """Classify visible normals against geometric outward on a closed mesh."""
    winding = _closed_orientation(record, vertex_map)
    if winding is None or not details.get("normals_known", True):
        return None
    outward_sign = 1.0 if winding == "outward" else -1.0
    dots = []
    for shape in record["shape_info"]:
        start, end = shape["face_start"], shape["face_start"] + shape["face_count"]
        for face_index in range(start, end):
            face = record["faces"][face_index]
            normal = _face_normal(record["points"], face)
            if normal is None or face_index >= len(details["normals"]):
                return None
            corners = details["normals"][face_index]
            if len(corners) != len(face):
                return None
            for vertex, visible in corners:
                if vertex not in face:
                    return None
                dots.append(sum(visible[axis] * normal[axis] * outward_sign
                                for axis in range(3)))
    if not dots:
        return None
    # Smooth shading can deviate from the face normal, but every corner must
    # still point consistently to one side of the closed surface.
    if all(value > 1e-5 for value in dots):
        return "outward"
    if all(value < -1e-5 for value in dots):
        return "inward"
    return None


def _edge_balance(faces, vertex_map=None):
    edges = defaultdict(lambda: [0, 0])
    for face in faces:
        mapped = tuple(vertex_map[v] for v in face) if vertex_map else face
        for index, left in enumerate(mapped):
            right = mapped[(index + 1) % len(mapped)]
            edge = (min(left, right), max(left, right))
            edges[edge][0] += 1
            edges[edge][1] += 1 if left < right else -1
    return edges


def _safe_attributes(record_a, record_b, cmds):
    """Fail closed for custom, connected, locked, or referenced transforms."""
    try:
        for record in (record_a, record_b):
            for path in [record["path"]] + record["shape_paths"]:
                if cmds.referenceQuery(path, isNodeReferenced=True):
                    return False
                if cmds.listAttr(path, userDefined=True):
                    return False
                if path == record["path"]:
                    for attr in ("translate", "rotate", "scale", "visibility"):
                        plug = path + "." + attr
                        if (cmds.getAttr(plug, lock=True) or
                                cmds.connectionInfo(plug, isDestination=True) or
                                cmds.connectionInfo(plug, isSource=True)):
                            return False
        return True
    except Exception:
        return False


def _detail_signatures(record, cmds, om):
    """Load expensive per-face diagnostics only after geometry has matched."""
    if "details" in record:
        return record["details"]
    normals, uv_faces, material_faces = [], [], []
    uv_known, materials_known, normals_known = True, True, True
    for shape in record["shape_info"]:
        selection = om.MSelectionList()
        selection.add(shape["path"])
        dag = selection.getDagPath(0)
        fn = om.MFnMesh(dag)
        try:
            opposite = bool(cmds.getAttr(shape["path"] + ".opposite"))
        except Exception:
            opposite, normals_known = False, False
        normal_sign = -1.0 if opposite else 1.0
        face_vertices = [record["faces"][shape["face_start"] + face_id]
                         for face_id in range(fn.numPolygons)]
        for face_id, vertices in enumerate(face_vertices):
            values = []
            for vertex_id in vertices:
                local_vertex_id = vertex_id - shape["vertex_start"]
                normal = fn.getFaceVertexNormal(face_id, local_vertex_id, om.MSpace.kWorld)
                values.append((vertex_id, (normal_sign * float(normal.x),
                                           normal_sign * float(normal.y),
                                           normal_sign * float(normal.z))))
            normals.append(tuple(values))
        try:
            uv_sets = fn.getUVSetNames()
        except Exception:
            uv_sets, uv_known = [], False
        for face_id, vertices in enumerate(face_vertices):
            corners = []
            for corner, vertex_id in enumerate(vertices):
                per_corner = []
                for uv_set in uv_sets:
                    try:
                        coordinate = fn.getPolygonUV(face_id, corner, uv_set)
                        per_corner.append((uv_set, tuple(round(float(value), 8)
                                                         for value in coordinate)))
                    except RuntimeError:
                        # Unassigned corners are represented distinctly from an empty UV set.
                        per_corner.append((uv_set, None))
                corners.append((vertex_id, tuple(per_corner)))
            uv_faces.append(tuple(corners))
        try:
            shaders, assignments = fn.getConnectedShaders(shape["instance"])
            shader_names = [om.MFnDependencyNode(shader).name() for shader in shaders]
            material_faces.extend(tuple([shader_names[int(index)]]) if int(index) >= 0 else ()
                                  for index in assignments)
        except Exception:
            # An unavailable material assignment is ambiguous, never a positive match.
            material_faces.extend([None] * fn.numPolygons)
            materials_known = False
    record["details"] = {"normals": normals, "uv": uv_faces,
                         "materials": material_faces, "uv_known": uv_known,
                         "materials_known": materials_known,
                         "normals_known": normals_known}
    return record["details"]


def _pair(a, b, tolerance, cmds, om):
    mapping = _match_points(a["points"], b["points"], tolerance)
    if mapping is None:
        return None
    geometry, winding = _topology(a, b, mapping)
    if not geometry:
        return None
    details_a = _detail_signatures(a, cmds, om)
    details_b = _detail_signatures(b, cmds, om)
    normals_a, normals_b_data = details_a["normals"], details_b["normals"]

    def normal_dot_products(faces, values, vertex_map=None):
        signatures = []
        for face, corner_values in zip(faces, values):
            mapped_face = tuple(vertex_map[v] for v in face) if vertex_map else face
            corners = []
            for vertex, normal in corner_values:
                mapped_vertex = vertex_map[vertex] if vertex_map else vertex
                corners.append((mapped_vertex, normal))
            signatures.append((_canonical_cycle(mapped_face), tuple(sorted(corners))))
        return sorted(signatures)

    signature_a = normal_dot_products(a["faces"], normals_a, mapping)
    signature_b = normal_dot_products(b["faces"], normals_b_data)
    dots = []
    signatures_valid = len(signature_a) == len(signature_b)
    if signatures_valid:
        for (_, corners_a), (_, corners_b) in zip(signature_a, signature_b):
            if len(corners_a) != len(corners_b):
                signatures_valid = False
                break
            for (va, na), (vb, nb) in zip(corners_a, corners_b):
                if va != vb:
                    signatures_valid = False
                    break
                dots.append(sum(na[k] * nb[k] for k in range(3)))
            if not signatures_valid:
                break
    if not signatures_valid:
        dots = []
    normals_equal = bool(dots) and all(value >= 1.0 - 1e-5 for value in dots)
    normals_opposed = bool(dots) and all(value <= -1.0 + 1e-5 for value in dots)
    normal_relation = ("equal" if normals_equal else
                       "opposed" if normals_opposed else
                       "partial" if dots else "unknown")

    uv_equal = (details_a["uv_known"] and details_b["uv_known"] and
                _uv_signatures(a["faces"], details_a["uv"], mapping) ==
                _uv_signatures(b["faces"], details_b["uv"]))
    materials_equal = False
    if details_a["materials_known"] and details_b["materials_known"]:
        material_a = sorted((_canonical_cycle(tuple(mapping[v] for v in polygon)), material)
                            for polygon, material in zip(a["faces"], details_a["materials"]))
        material_b = sorted((_canonical_cycle(polygon), material)
                            for polygon, material in zip(b["faces"], details_b["materials"]))
        materials_equal = material_a == material_b
    orientation_a = _visible_orientation(a, details_a, mapping)
    orientation_b = _visible_orientation(b, details_b)
    orientation_keeper = (a["path"] if orientation_a == "outward" and orientation_b == "inward"
                          else b["path"] if orientation_b == "outward" and orientation_a == "inward"
                          else None)
    return {"a": a["path"], "b": b["path"], "geometry": True,
            "winding_equal": winding, "normals_equal": normals_equal,
            "normal_relation": normal_relation,
            "orientation_a": orientation_a, "orientation_b": orientation_b,
            "orientation_keeper": orientation_keeper,
            "uv_equal": uv_equal, "materials_equal": materials_equal,
            "safe_attributes": (_safe_attributes(a, b, cmds) and
                                details_a["uv_known"] and details_b["uv_known"] and
                                details_a["materials_known"] and
                                details_b["materials_known"])}


def scan(root_node, tolerance=0.001, progress_callback=None, excluded_paths=()):
    """Find coincident world-space meshes below *root_node*.

    A detected pair must have a one-to-one vertex match and equivalent polygon
    connectivity. No translation, rotation, or reflection is applied.
    """
    result = {"status": "failed", "groups": [], "pairs": [], "errors": []}
    if not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance) or tolerance < 0:
        result["errors"].append("tolerance must be a finite non-negative number")
        return result
    try:
        import maya.cmds as cmds
        import maya.api.OpenMaya as om
    except Exception as exc:
        result["errors"].append("Maya API unavailable: {}".format(exc))
        return result
    try:
        excluded = set(excluded_paths)
        transforms = [path for path in _transform_paths(root_node, cmds) if path not in excluded]
        records = []
        for index, path in enumerate(transforms):
            if _cancelled(progress_callback, index, len(transforms)):
                result["status"] = "cancelled"
                return result
            record = _record(path, cmds, om)
            if record is not None:
                records.append(record)
        buckets = defaultdict(list)
        for record in records:
            buckets[record["vertex_count"], record["face_count"]].append(record)
        adjacency = defaultdict(set)
        checked = 0
        for bucket in buckets.values():
            bucket.sort(key=lambda item: item["bounds"][0][0])
            for index, a in enumerate(bucket):
                if _cancelled(progress_callback, checked, len(records)):
                    result["status"] = "cancelled"
                    return result
                checked += 1
                limit = a["bounds"][0][0] + tolerance
                for j in range(index + 1, len(bucket)):
                    b = bucket[j]
                    if b["bounds"][0][0] > limit:
                        break
                    if j % 32 == 0 and _cancelled(progress_callback, checked, len(records)):
                        result["status"] = "cancelled"
                        return result
                    if not _bbox_candidate(a, b, tolerance):
                        continue
                    pair = _pair(a, b, tolerance, cmds, om)
                    if pair is not None:
                        result["pairs"].append(pair)
                        adjacency[a["path"]].add(b["path"])
                        adjacency[b["path"]].add(a["path"])
        # Only emit cliques: a tolerance chain is not evidence that every member
        # is interchangeable with every other member.
        for path in sorted(adjacency):
            neighbors = sorted(adjacency[path])
            for i, left in enumerate(neighbors):
                for right in neighbors[i + 1:]:
                    if right not in adjacency[left]:
                        result["errors"].append(
                            "non-transitive tolerance matches near {}".format(path))
                        break
        groups, visited = [], set()
        for path in sorted(adjacency):
            if path in visited:
                continue
            component, stack = set(), [path]
            while stack:
                current = stack.pop()
                if current in component:
                    continue
                component.add(current)
                stack.extend(adjacency[current] - component)
            visited.update(component)
            if len(component) > 1 and all(
                    right in adjacency[left]
                    for left in component for right in component if left != right):
                groups.append(sorted(component))
        result["groups"] = groups
        result["status"] = "failed" if result["errors"] else ("found" if groups else "passed")
    except Exception as exc:
        result["errors"].append("duplicate scan failed: {}".format(exc))
        result["status"] = "failed"
    return result
