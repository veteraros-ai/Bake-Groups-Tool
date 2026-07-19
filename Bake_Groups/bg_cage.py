# -*- coding: utf-8 -*-
"""Two-step cage generation for Substance baking.

A cage is a duplicate of the low-poly mesh whose vertices are pushed outward
along their averaged normals. Topology and vertex order are preserved
(Substance requirement).

Step 1 (uniform inflate): every vertex is pushed by the same distance so the
artist can inflate the shell until it fully covers the HP.

Step 2 (fit to HP): each polygon plane is moved along its normal to sit just
outside (by ``gap``) the outermost point of the subgroup's OWN (target) HP under
that face, while also clearing any OTHER subgroup's (obstacle) HP so the cage
never intersects it. Shared vertices take the most-outward requirement, so no
polygon cuts into HP.
"""
from __future__ import print_function, division, absolute_import

import math

import maya.cmds as cmds
import maya.api.OpenMaya as om

CAGE_ROOT = "Cage_BG"
SUFFIX_CAGE = "_cage"


class CageProcessor(object):
    # Global defaults (percent of the LP bounding-box diagonal).
    DEFAULT_INFLATE_PCT = 5.0   # step 1: uniform inflation to cover the HP
    DEFAULT_GAP_PCT = 0.5       # kept gap between the fitted cage and the HP

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _shape_dag(transform):
        sel = om.MSelectionList()
        sel.add(transform)
        dag = sel.getDagPath(0)
        dag.extendToShape()
        return dag

    @staticmethod
    def _fn_mesh(transform):
        dag = CageProcessor._shape_dag(transform)
        return om.MFnMesh(dag), dag

    @staticmethod
    def _bbox_diag(transform):
        bb = cmds.exactWorldBoundingBox(transform)
        return math.sqrt((bb[3] - bb[0]) ** 2 + (bb[4] - bb[1]) ** 2 + (bb[5] - bb[2]) ** 2)

    @staticmethod
    def resolve_amounts(lp_transform, params, ref_diag=None):
        """Return absolute (inflate, gap) world units.

        ``params`` carries percent-of-diagonal values by default; an
        ``absolute`` unit mode uses the raw values directly. ``ref_diag`` is a
        shared reference diagonal (e.g. the whole chapter) so percent amounts
        resolve to the SAME absolute distance for every subgroup - otherwise big
        meshes would inflate more than small ones."""
        diag = ref_diag if ref_diag else (CageProcessor._bbox_diag(lp_transform) or 1.0)
        if params.get('unit', 'percent') == 'absolute':
            inflate = float(params.get('inflate', diag * 0.05))
            gap = float(params.get('gap', diag * 0.005))
        else:
            inflate = diag * float(params.get('inflate', CageProcessor.DEFAULT_INFLATE_PCT)) / 100.0
            gap = diag * float(params.get('gap', CageProcessor.DEFAULT_GAP_PCT)) / 100.0
        if inflate < 0:
            inflate = 0.0
        if gap < 0:
            gap = 0.0
        return inflate, gap

    # ------------------------------------------------------------------
    # HP probes
    # ------------------------------------------------------------------
    @staticmethod
    def _closest_signed(fn, px, py, pz):
        """Closest point on the mesh (in WORLD space) to (px,py,pz) and the
        signed distance along the surface normal (>0 outside, <0 inside).
        Uses MFnMesh in kWorld, which honours the object's transform - unlike
        MMeshIntersector, whose matrix arg is ignored for the returned point."""
        try:
            p = om.MPoint(px, py, pz)
            cp, fid = fn.getClosestPoint(p, om.MSpace.kWorld)
            nrm = fn.getPolygonNormal(fid, om.MSpace.kWorld)
            signed = nrm.x * (px - cp.x) + nrm.y * (py - cp.y) + nrm.z * (pz - cp.z)
            return (cp.x, cp.y, cp.z), signed
        except Exception:
            return None, None

    @staticmethod
    def _build_hp_probes(hp_transforms):
        probes = []
        for hp in hp_transforms or []:
            if not hp or not cmds.objExists(hp):
                continue
            try:
                sel = om.MSelectionList()
                sel.add(hp)
                dag = sel.getDagPath(0)
                dag.extendToShape()
                if not dag.hasFn(om.MFn.kMesh):
                    continue
                fn = om.MFnMesh(dag)
                try:
                    accel = fn.autoUniformGridParams()
                except Exception:
                    accel = None
                probes.append({'fn': fn, 'accel': accel})
            except Exception:
                continue
        return probes

    @staticmethod
    def _vertex_adjacency(dag, vtx_count):
        adj = [[] for _ in range(vtx_count)]
        try:
            it = om.MItMeshVertex(dag)
            while not it.isDone():
                idx = it.index()
                if 0 <= idx < vtx_count:
                    adj[idx] = list(it.getConnectedVertices())
                it.next()
        except Exception:
            pass
        return adj

    # ------------------------------------------------------------------
    # Core: offset fields
    # ------------------------------------------------------------------
    @staticmethod
    def _read_points_normals(lp_transform):
        fn, dag = CageProcessor._fn_mesh(lp_transform)
        raw_normals = fn.getVertexNormals(True, om.MSpace.kWorld)
        raw_points = fn.getPoints(om.MSpace.kWorld)
        n = len(raw_points)
        # Copy into plain tuples immediately: the arrays are tied to the MFnMesh
        # lifetime and can be invalidated once ``fn`` is garbage-collected.
        points = [(raw_points[i].x, raw_points[i].y, raw_points[i].z) for i in range(n)]
        normals = [(raw_normals[i].x, raw_normals[i].y, raw_normals[i].z) for i in range(n)]
        return dag, points, normals, n

    @staticmethod
    def uniform_offsets(lp_transform, amount):
        """Step 1: every vertex pushed out by the same distance along its normal."""
        _dag, points, normals, n = CageProcessor._read_points_normals(lp_transform)
        return points, normals, [float(amount)] * n

    @staticmethod
    def inflate_existing_cage(cage_transform, delta, lp_transform=None):
        """Push every vertex of an ALREADY-BUILT cage mesh outward (or inward,
        for a negative delta) by ``delta`` along a STABLE direction, added on
        top of the vertex's current position - so it never recomputes topology
        and any manual brush sculpting already on the cage is preserved and only
        nudged by the incremental amount.

        The direction field is taken from the LP mesh's vertex normals
        (``lp_transform``), NOT the cage's own normals. The cage is a 1:1
        topological duplicate of the LP, so LP vertex ``i`` matches cage vertex
        ``i``, and LP normals never change - whereas the cage's own normals
        drift and self-intersect as it inflates, which made inflate->deflate
        non-reversible (the cage exploded, never returning to its start). With a
        fixed LP direction field, +d then -d returns exactly to the start.
        Falls back to the cage's own normals only if the LP is unavailable or
        its vertex count no longer matches. No-op if delta is negligible."""
        if not cmds.objExists(cage_transform) or abs(delta) < 1e-9:
            return
        fn, _dag = CageProcessor._fn_mesh(cage_transform)
        pts = fn.getPoints(om.MSpace.kWorld)
        n = len(pts)
        nrm = None
        if lp_transform and cmds.objExists(lp_transform):
            try:
                lp_fn, _d = CageProcessor._fn_mesh(lp_transform)
                lp_nrm = lp_fn.getVertexNormals(True, om.MSpace.kWorld)
                if len(lp_nrm) == n:
                    nrm = lp_nrm
            except Exception:
                nrm = None
        if nrm is None:
            nrm = fn.getVertexNormals(True, om.MSpace.kWorld)  # fallback (drifts)
        out = om.MPointArray()
        for i in range(n):
            out.append(om.MPoint(
                pts[i].x + nrm[i].x * delta,
                pts[i].y + nrm[i].y * delta,
                pts[i].z + nrm[i].z * delta))
        fn.setPoints(out, om.MSpace.kWorld)

    # ------------------------------------------------------------------
    # Cage <-> HP intersection islands (manual normal-move touch-up)
    # ------------------------------------------------------------------
    @staticmethod
    def find_hp_intersection_islands(cage_transform, hp_transforms):
        """Find the cage polygons that pierce the HP, grouped into connected
        islands. A cage edge is 'piercing' when a ray along it hits the HP
        within the edge span (same test as the reference tool). Returns a list
        of {'faces': [ids], 'verts': [ids], 'normal': (x,y,z) object-space}.
        The averaged object-space normal is stored so a later slider can push
        the island straight out along it (reversibly, via setPoints)."""
        islands = []
        if not cmds.objExists(cage_transform):
            return islands
        try:
            sel = om.MSelectionList(); sel.add(cage_transform)
            dag = sel.getDagPath(0); dag.extendToShape()
        except Exception:
            return islands

        hp_fns = []
        for hp in hp_transforms or []:
            if not hp or not cmds.objExists(hp):
                continue
            try:
                s = om.MSelectionList(); s.add(hp); d = s.getDagPath(0); d.extendToShape()
                if not d.hasFn(om.MFn.kMesh):
                    continue
                fn = om.MFnMesh(d)
                hp_fns.append((fn, fn.autoUniformGridParams()))
            except Exception:
                continue
        if not hp_fns:
            return islands

        intersecting = set()
        edge_it = om.MItMeshEdge(dag)
        while not edge_it.isDone():
            p0 = edge_it.point(0, om.MSpace.kWorld)
            p1 = edge_it.point(1, om.MSpace.kWorld)
            ray = p1 - p0
            length = ray.length()
            if length > 1e-5:
                ray.normalize()
                src = om.MFloatPoint(p0.x, p0.y, p0.z)
                dirv = om.MFloatVector(ray.x, ray.y, ray.z)
                for fn, accel in hp_fns:
                    try:
                        if fn.closestIntersection(src, dirv, om.MSpace.kWorld, length, False, accelParams=accel):
                            for f in edge_it.getConnectedFaces():
                                intersecting.add(f)
                            break
                    except Exception:
                        pass
            edge_it.next()
        if not intersecting:
            return islands

        visited = set()
        poly_it = om.MItMeshPolygon(dag)
        for fid in intersecting:
            if fid in visited:
                continue
            island_faces = []
            queue = [fid]
            visited.add(fid)
            while queue:
                cur = queue.pop(0)
                island_faces.append(cur)
                poly_it.setIndex(cur)
                for conn in poly_it.getConnectedFaces():
                    if conn in intersecting and conn not in visited:
                        visited.add(conn)
                        queue.append(conn)
            avg = om.MVector()
            verts = set()
            for f in island_faces:
                poly_it.setIndex(f)
                avg += poly_it.getNormal(om.MSpace.kObject)
                verts.update(poly_it.getVertices())
            if avg.length() > 1e-5:
                avg.normalize()
            islands.append({'faces': island_faces, 'verts': sorted(verts),
                            'normal': (avg.x, avg.y, avg.z)})
        return islands

    @staticmethod
    def move_islands(cage_transform, islands, delta):
        """Push every island's vertices by ``delta`` along that island's fixed
        object-space normal. setPoints-based (no history), reversible: +d then
        -d returns exactly. Shared border vertices move too, so neighbouring
        faces stretch - the same visual result as polyMoveFacet translate, but
        compatible with the cage's history-free Expansion pipeline."""
        if not cmds.objExists(cage_transform) or abs(delta) < 1e-9 or not islands:
            return
        fn, _dag = CageProcessor._fn_mesh(cage_transform)
        pts = fn.getPoints(om.MSpace.kObject)
        n = len(pts)
        for isl in islands:
            nx, ny, nz = isl.get('normal', (0.0, 0.0, 0.0))
            for vi in isl.get('verts', []):
                if 0 <= vi < n:
                    pts[vi] = om.MPoint(pts[vi].x + nx * delta,
                                        pts[vi].y + ny * delta,
                                        pts[vi].z + nz * delta)
        fn.setPoints(pts, om.MSpace.kObject)

    @staticmethod
    def fit_offsets(lp_transform, target_hp, obstacle_hp, inflate, gap):
        """Step 2 (shrink-wrap the inflated hull inward to the HP).

        Start from the already-inflated cage (a smooth shell fully outside the
        HP) and, for every vertex, cast outward along its normal but ONLY within
        the inflation distance. The outermost TARGET-HP crossing inside that
        segment is where the HP surface sits; the vertex is pulled in to that
        surface + ``gap``. If the ray finds no HP inside the segment the vertex
        stays on the smooth hull. Because the probe is bounded by the hull, rays
        can never run away to distant geometry (which produced spikes), and we
        only ever move inward, so the cage never balloons toward other parts.
        A light upward smoothing removes low-poly ripples without cutting the HP.
        ``obstacle_hp`` is unused now: shrinking inward toward the own HP keeps
        the hull's existing clearance of other subgroups. Returns
        (points, normals, offsets)."""
        dag, points, normals, n = CageProcessor._read_points_normals(lp_transform)
        target_probes = CageProcessor._build_hp_probes(target_hp)
        probe_len = max(inflate, gap * 2.0, 1e-5)

        offsets = [inflate] * n  # default: stay on the smooth inflated hull
        fitted_flags = [False] * n

        for i in range(n):
            vx, vy, vz = points[i]
            nx, ny, nz = normals[i]
            src = om.MFloatPoint(vx, vy, vz)
            dirv = om.MFloatVector(nx, ny, nz)
            best_t = None
            for pr in target_probes:
                try:
                    res = pr['fn'].allIntersections(
                        src, dirv, om.MSpace.kWorld, probe_len, False,
                        accelParams=pr['accel'], tolerance=1e-6)
                    params = res[1] if res else None
                    if not params:
                        continue
                    for k in range(len(params)):
                        t = float(params[k])
                        # Outermost HP crossing within the hull segment.
                        if 0.0 < t <= inflate and (best_t is None or t > best_t):
                            best_t = t
                except Exception:
                    pass
            if best_t is not None:
                # Place the vertex at the HP surface + gap, but never past the
                # Expansion hull (Expansion is the MAX; LP is the min; Fit lives
                # between them). Where the HP reaches the hull the gap is capped -
                # raise Expansion for more room there.
                off = best_t + gap
                if off > inflate:
                    off = inflate
                if off < gap:
                    off = gap
                offsets[i] = off
                fitted_flags[i] = True

        offsets = CageProcessor._smooth_up(dag, n, offsets, fitted_flags, inflate)
        offsets = CageProcessor._resolve_face_penetration(
            dag, points, normals, offsets, target_probes, gap, inflate)
        return points, normals, offsets

    @staticmethod
    def _resolve_face_penetration(dag, points, normals, offsets, probes, gap, ceil, iters=4):
        """Post-fit safety pass: vertices sit outside the HP, but a polygon can
        still cut through a convex HP bump between them. Sample each polygon
        (current vertex positions, edge midpoints, centroid); if any sample is
        inside the HP or closer than ``gap``, push that face's vertices outward
        by the worst deficit - but never past ``ceil`` (the Expansion hull).
        Iterate until clean (or ``iters`` exhausted)."""
        n = len(points)
        eps = max(gap * 0.02, 1e-6)

        def pos(vi):
            px, py, pz = points[vi]
            nx, ny, nz = normals[vi]
            o = offsets[vi]
            return (px + nx * o, py + ny * o, pz + nz * o)

        for _ in range(max(1, iters)):
            moved = False
            itp = om.MItMeshPolygon(dag)
            while not itp.isDone():
                verts = [vi for vi in itp.getVertices() if 0 <= vi < n]
                if len(verts) >= 3:
                    cur = [pos(vi) for vi in verts]
                    cx = sum(p[0] for p in cur) / len(cur)
                    cy = sum(p[1] for p in cur) / len(cur)
                    cz = sum(p[2] for p in cur) / len(cur)
                    samples = list(cur)
                    samples.append((cx, cy, cz))
                    for k in range(len(cur)):
                        a = cur[k]
                        b = cur[(k + 1) % len(cur)]
                        samples.append(((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5, (a[2] + b[2]) * 0.5))

                    worst = 0.0
                    for (sx, sy, sz) in samples:
                        for pr in probes:
                            _cp, signed = CageProcessor._closest_signed(pr['fn'], sx, sy, sz)
                            if signed is not None:
                                deficit = gap - signed
                                if deficit > worst:
                                    worst = deficit
                    if worst > eps:
                        for vi in verts:
                            o = offsets[vi] + worst
                            offsets[vi] = ceil if o > ceil else o
                        moved = True
                itp.next()
            if not moved:
                break
        return offsets

    # ------------------------------------------------------------------
    # Cross-subgroup cage overlap resolution
    # ------------------------------------------------------------------
    @staticmethod
    def resolve_cage_overlaps(entries, floor_gap, iters=3):
        """Shrink cages where they overlap OTHER subgroups' cages (never wanted).

        Works in world space on the finished cage meshes. For each cage vertex
        that sits inside another subgroup's cage, move it inward toward its own
        HP by the penetration depth, but never closer than ``floor_gap`` to its
        own HP (so a cage never enters its own HP just to dodge a neighbour).
        ``entries`` is a list of {'cage': transform, 'own_hp': [hp meshes]}.
        Returns the number of vertices moved."""
        entries = [e for e in entries if e.get('cage') and cmds.objExists(e['cage'])]
        if len(entries) < 2:
            return 0
        for e in entries:
            e['own_probes'] = CageProcessor._build_hp_probes(e.get('own_hp') or [])
            # World positions kept in Python as the source of truth so we never
            # re-read them via MFnMesh (whose point cache can be stale after a
            # setPoints). Intersectors are rebuilt from the written geometry.
            fn, _dag = CageProcessor._fn_mesh(e['cage'])
            raw = fn.getPoints(om.MSpace.kWorld)
            e['pos'] = [(raw[k].x, raw[k].y, raw[k].z) for k in range(len(raw))]

        def _write(cage, pos):
            fn, _d = CageProcessor._fn_mesh(cage)
            out = om.MPointArray()
            for (x, y, z) in pos:
                out.append(om.MPoint(x, y, z))
            fn.setPoints(out, om.MSpace.kWorld)

        def _cage_fn(cage):
            try:
                fn, _d = CageProcessor._fn_mesh(cage)
                return fn
            except Exception:
                return None

        total_moved = 0
        for _ in range(max(1, iters)):
            for e in entries:
                _write(e['cage'], e['pos'])
            cage_fns = [_cage_fn(e['cage']) for e in entries]

            any_moved = False
            for i, e in enumerate(entries):
                new_pos = list(e['pos'])
                for k, (px, py, pz) in enumerate(e['pos']):
                    depth = 0.0
                    for j, ofn in enumerate(cage_fns):
                        if j == i or ofn is None:
                            continue
                        _cp, signed = CageProcessor._closest_signed(ofn, px, py, pz)
                        if signed is not None and signed < 0.0 and -signed > depth:
                            depth = -signed
                    if depth <= floor_gap * 0.1:
                        continue
                    own_cp = None
                    own_clear = None
                    for pr in e['own_probes']:
                        cp, sd = CageProcessor._closest_signed(pr['fn'], px, py, pz)
                        if cp is not None and (own_clear is None or sd < own_clear):
                            own_clear = sd
                            own_cp = cp
                    if own_cp is None:
                        continue
                    max_inward = own_clear - floor_gap
                    if max_inward <= 1e-6:
                        continue
                    dx, dy, dz = own_cp[0] - px, own_cp[1] - py, own_cp[2] - pz
                    dl = (dx * dx + dy * dy + dz * dz) ** 0.5 or 1.0
                    move = depth if depth < max_inward else max_inward
                    new_pos[k] = (px + dx / dl * move, py + dy / dl * move, pz + dz / dl * move)
                    any_moved = True
                    total_moved += 1
                e['pos'] = new_pos
            if not any_moved:
                break

        for e in entries:
            _write(e['cage'], e['pos'])
        return total_moved

    @staticmethod
    def _smooth_up(dag, n, offsets, fitted_flags, ceil, iters=2):
        """Ease the fitted offset field toward neighbours, but only UPWARD (a
        vertex is never pulled tighter than its own fit) so coverage is kept and
        inward dips/ripples are removed. Untouched (hull) vertices are left."""
        if iters <= 0 or n == 0:
            return offsets
        adj = CageProcessor._vertex_adjacency(dag, n)
        base = list(offsets)
        cur = list(offsets)
        for _ in range(iters):
            nxt = list(cur)
            for i in range(n):
                if not fitted_flags[i]:
                    continue
                nb = [cur[j] for j in adj[i] if 0 <= j < n]
                if not nb:
                    continue
                avg = sum(nb) / float(len(nb))
                val = max(base[i], avg)   # never below own fit
                if val > ceil:
                    val = ceil
                nxt[i] = val
            cur = nxt
        return cur

    # ------------------------------------------------------------------
    # Cage mesh creation / update
    # ------------------------------------------------------------------
    # Cage look: 'wire' = unshaded purple wireframe envelope (see-through);
    # 'solid' = opaque shaded gray so the cage reads as a solid shell.
    CAGE_COLOR_RGB = (0.62, 0.28, 0.80)
    CAGE_MAT_NAME = "BG_CageGray_MAT"
    CAGE_SG_NAME = "BG_CageGray_SG"

    @staticmethod
    def _ensure_cage_material():
        """Return the shared gray shading group used by the 'solid' cage look."""
        sg = CageProcessor.CAGE_SG_NAME
        if not cmds.objExists(sg):
            mat = CageProcessor.CAGE_MAT_NAME
            if not cmds.objExists(mat):
                mat = cmds.shadingNode('lambert', asShader=True, name=mat)
                cmds.setAttr(mat + ".color", 0.5, 0.5, 0.5, type='double3')
            sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=sg)
            cmds.connectAttr(mat + ".outColor", sg + ".surfaceShader", force=True)
        return sg

    @staticmethod
    def apply_cage_display(cage_transform, mode='solid'):
        shapes = cmds.listRelatives(cage_transform, shapes=True, fullPath=True) or []
        if mode == 'solid':
            sg = CageProcessor._ensure_cage_material()
            for shp in shapes:
                try:
                    cmds.setAttr(shp + ".overrideEnabled", 0)  # normal shaded look
                except Exception:
                    pass
            try:
                cmds.sets(shapes, edit=True, forceElement=sg)
            except Exception:
                pass
        else:  # 'wire'
            for shp in shapes:
                try:
                    cmds.setAttr(shp + ".overrideEnabled", 1)
                    cmds.setAttr(shp + ".overrideShading", 0)      # wireframe only
                    cmds.setAttr(shp + ".overrideTexturing", 0)
                    cmds.setAttr(shp + ".overrideRGBColors", 1)
                    cmds.setAttr(shp + ".overrideColorRGB", *CageProcessor.CAGE_COLOR_RGB)
                except Exception:
                    pass

    @staticmethod
    def set_chapter_cage_display(base_name, mode):
        """Apply a display mode ('wire' / 'solid') to every cage in the chapter."""
        for cage in CageProcessor.get_chapter_cage_meshes(base_name):
            CageProcessor.apply_cage_display(cage, mode)

    @staticmethod
    def _apply_offsets(cage_transform, points, normals, offsets):
        fn, _ = CageProcessor._fn_mesh(cage_transform)
        new_pts = om.MPointArray()
        count = min(len(points), fn.numVertices)
        for i in range(count):
            vx, vy, vz = points[i]
            nx, ny, nz = normals[i]
            o = offsets[i]
            new_pts.append(om.MPoint(vx + nx * o, vy + ny * o, vz + nz * o))
        fn.setPoints(new_pts, om.MSpace.kWorld)

    @staticmethod
    def index_existing_cages(chapter_grp):
        """One scan of the chapter -> {cage_short_name: cage_full_path}. Lets a
        rebuild loop over N subgroups avoid re-scanning the whole cage hierarchy
        per subgroup (the old find_cage-per-mesh was O(N^2))."""
        index = {}
        if not cmds.objExists(chapter_grp):
            return index
        for tr in cmds.listRelatives(chapter_grp, allDescendents=True, fullPath=True, type='transform') or []:
            short = tr.split('|')[-1]
            if short.endswith(SUFFIX_CAGE) and cmds.listRelatives(tr, shapes=True, type='mesh'):
                index.setdefault(short, tr)
        return index

    @staticmethod
    def update_or_create_cage(lp_transform, target_hp, obstacle_hp, params, chapter_grp, lp_main, ref_diag=None,
                              existing_index=None):
        """Create the cage for ``lp_transform`` if missing, otherwise adjust the
        existing cage node in place (no delete/recreate, so node identity, name
        and parenting survive). The cage is placed under a folder chain that
        mirrors the LP subgroup grouping.

        Step 1 (``fitted`` False): uniform inflation by ``inflate`` (this is the
        ONLY step reachable from the UI; it is used once, at creation time -
        subsequent Expansion changes push the existing mesh incrementally via
        ``inflate_existing_cage`` instead of going through here again).
        Step 2 (``fitted`` True, hidden): fit each polygon plane to the TARGET
        HP with a ``gap``, clearing OBSTACLE HP. Returns (cage_path, created)."""
        if not cmds.objExists(lp_transform):
            return None, False

        inflate, gap = CageProcessor.resolve_amounts(lp_transform, params, ref_diag)
        if params.get('fitted'):
            points, normals, offsets = CageProcessor.fit_offsets(
                lp_transform, target_hp, obstacle_hp, inflate, gap)
        else:
            points, normals, offsets = CageProcessor.uniform_offsets(lp_transform, inflate)

        cage_name = lp_transform.split('|')[-1] + SUFFIX_CAGE
        folders = CageProcessor.relative_folders_for_lp(lp_transform, lp_main)
        target_folder = CageProcessor.ensure_relative_folders(chapter_grp, folders)

        if existing_index is not None:
            existing = existing_index.get(cage_name)
            if existing and not cmds.objExists(existing):
                existing = None
        else:
            existing = CageProcessor.find_cage(chapter_grp, cage_name)
        if existing:
            # Reparent into the mirrored folder if the LP grouping moved.
            parent = cmds.listRelatives(existing, parent=True, fullPath=True)
            if not parent or parent[0] != target_folder:
                existing = cmds.parent(existing, target_folder, absolute=True)[0]
                existing = cmds.ls(existing, long=True)[0]
            # Topology changed on the LP (re-analysed)? Fall back to recreate.
            fn, _ = CageProcessor._fn_mesh(existing)
            if fn.numVertices != len(points):
                cmds.delete(existing)
                existing = None

        created = False
        if existing:
            cage = existing
        else:
            dup = cmds.duplicate(lp_transform, returnRootsOnly=True)[0]
            try:
                cmds.delete(dup, constructionHistory=True)
            except Exception:
                pass
            dup = cmds.parent(dup, target_folder, absolute=True)[0]
            dup = cmds.rename(dup, cage_name)
            cage = cmds.ls(dup, long=True)[0]
            created = True

        CageProcessor._apply_offsets(cage, points, normals, offsets)
        CageProcessor.apply_cage_display(cage, params.get('display_mode', 'solid'))
        return cage, created

    # ------------------------------------------------------------------
    # Scene organisation (mirrors the LP subgroup grouping)
    # ------------------------------------------------------------------
    @staticmethod
    def cage_group_for_chapter(base_name):
        return "|{}|{}".format(CAGE_ROOT, base_name)

    @staticmethod
    def ensure_chapter_group(base_name):
        """Return the '{Cage_BG}|{base}' transform, creating the hierarchy."""
        root = "|{}".format(CAGE_ROOT)
        if not cmds.objExists(root):
            root = cmds.group(empty=True, world=True, name=CAGE_ROOT)
            root = cmds.ls(root, long=True)[0]
        chapter = "{}|{}".format(root, base_name)
        if not cmds.objExists(chapter):
            chapter = cmds.group(empty=True, parent=root, name=base_name)
            chapter = cmds.ls(chapter, long=True)[0]
        return chapter

    @staticmethod
    def relative_folders_for_lp(lp_transform, lp_main):
        """Folder short-names between lp_main and the LP mesh (mesh excluded),
        so the cage can replicate the same subgroup nesting."""
        lp_full = (cmds.ls(lp_transform, long=True) or [None])[0]
        main_full = (cmds.ls(lp_main, long=True) or [None])[0]
        if not lp_full or not main_full or not lp_full.startswith(main_full + "|"):
            return []
        rest = lp_full[len(main_full) + 1:]
        parts = rest.split("|")
        return parts[:-1]

    @staticmethod
    def ensure_relative_folders(chapter_grp, folder_shorts):
        parent = chapter_grp
        for short in folder_shorts:
            child_path = parent + "|" + short
            if not cmds.objExists(child_path):
                new = cmds.group(empty=True, parent=parent, name=short)
                child_path = cmds.ls(new, long=True)[0]
            parent = child_path
        return parent

    @staticmethod
    def find_cage(chapter_grp, cage_name):
        if not cmds.objExists(chapter_grp):
            return None
        for tr in cmds.listRelatives(chapter_grp, allDescendents=True, fullPath=True, type='transform') or []:
            if tr.split('|')[-1] == cage_name:
                return tr
        return None

    @staticmethod
    def prune_orphans(chapter_grp, expected_names):
        """Delete cage meshes whose source LP no longer exists, then remove any
        folders left empty. Emptiness is decided by string-prefix over ONE fresh
        scan of the remaining nodes, instead of an allDescendents query per
        folder (which was one hierarchy scan per node)."""
        if not cmds.objExists(chapter_grp):
            return
        for tr in cmds.listRelatives(chapter_grp, allDescendents=True, fullPath=True, type='transform') or []:
            if not cmds.objExists(tr):
                continue
            short = tr.split('|')[-1]
            if short.endswith(SUFFIX_CAGE) and cmds.listRelatives(tr, shapes=True, type='mesh'):
                if short not in expected_names:
                    cmds.delete(tr)
        remaining = cmds.listRelatives(chapter_grp, allDescendents=True, fullPath=True, type='transform') or []
        mesh_paths = [tr for tr in remaining if cmds.listRelatives(tr, shapes=True, type='mesh')]
        for tr in remaining:
            if tr in mesh_paths:
                continue  # a mesh itself
            prefix = tr + '|'
            if any(mp.startswith(prefix) for mp in mesh_paths):
                continue  # folder still contains a mesh somewhere below
            try:
                cmds.delete(tr)
            except Exception:
                pass

    @staticmethod
    def clear_chapter_cage(base_name):
        chapter = CageProcessor.cage_group_for_chapter(base_name)
        if cmds.objExists(chapter):
            children = cmds.listRelatives(chapter, children=True, fullPath=True) or []
            if children:
                cmds.delete(children)

    @staticmethod
    def get_chapter_cage_meshes(base_name):
        chapter = CageProcessor.cage_group_for_chapter(base_name)
        if not cmds.objExists(chapter):
            return []
        out = []
        for tr in cmds.listRelatives(chapter, allDescendents=True, fullPath=True, type='transform') or []:
            if cmds.listRelatives(tr, shapes=True, type='mesh', fullPath=True):
                out.append(tr)
        return out
