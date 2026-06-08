# -*- coding: utf-8 -*-
import sys
import os
import json
import math
import bisect
from datetime import datetime
import maya.cmds as cmds
import maya.api.OpenMaya as om

try:
    from PySide6 import QtWidgets, QtCore, QtGui
except ImportError:
    from PySide2 import QtWidgets, QtCore, QtGui

try:
    import bg_math_core
    HAS_MATH = True
except ImportError:
    HAS_MATH = False

class StructureAnalyzer(QtWidgets.QMainWindow):
    def __init__(self, parent=None):
        super(StructureAnalyzer, self).__init__(parent)
        self.setWindowTitle("Bake Groups: Structure Analyzer")
        self.resize(850, 650)
        
        main_widget = QtWidgets.QWidget()
        self.setCentralWidget(main_widget)
        layout = QtWidgets.QVBoxLayout(main_widget)
        
        # UI Элементы
        self.lbl_info = QtWidgets.QLabel("Выделите корневую группу (Главу) в Outliner и нажмите кнопку.")
        self.lbl_info.setWordWrap(True)
        layout.addWidget(self.lbl_info)
        
        btn_layout = QtWidgets.QHBoxLayout()
        self.btn_analyze = QtWidgets.QPushButton("Анализировать структуру")
        self.btn_analyze.setMinimumHeight(40)
        self.btn_analyze.clicked.connect(self.run_analysis)
        btn_layout.addWidget(self.btn_analyze)
        self.btn_fill_selected = QtWidgets.QPushButton("Calculate Fill Selected")
        self.btn_fill_selected.setMinimumHeight(40)
        self.btn_fill_selected.clicked.connect(self.calculate_selected_fill)
        btn_layout.addWidget(self.btn_fill_selected)
        layout.addLayout(btn_layout)

        fill_controls = QtWidgets.QHBoxLayout()
        fill_controls.addWidget(QtWidgets.QLabel("Min overlap (%):"))
        self.spin_min_overlap = QtWidgets.QDoubleSpinBox()
        self.spin_min_overlap.setRange(0.0, 100.0)
        self.spin_min_overlap.setValue(50.0)
        self.spin_min_overlap.setSingleStep(0.5)
        fill_controls.addWidget(self.spin_min_overlap)
        fill_controls.addWidget(QtWidgets.QLabel("Definite LP >=:"))
        self.spin_definite_lp_pct = QtWidgets.QDoubleSpinBox()
        self.spin_definite_lp_pct.setRange(0.0, 100.0)
        self.spin_definite_lp_pct.setValue(96.0)
        self.spin_definite_lp_pct.setSingleStep(0.5)
        fill_controls.addWidget(self.spin_definite_lp_pct)
        fill_controls.addWidget(QtWidgets.QLabel("Select LP >=:"))
        self.spin_select_lp_pct = QtWidgets.QDoubleSpinBox()
        self.spin_select_lp_pct.setRange(0.0, 100.0)
        self.spin_select_lp_pct.setValue(70.0)
        self.spin_select_lp_pct.setSingleStep(0.5)
        fill_controls.addWidget(self.spin_select_lp_pct)
        fill_controls.addWidget(QtWidgets.QLabel("HP >=:"))
        self.spin_select_hp_pct = QtWidgets.QDoubleSpinBox()
        self.spin_select_hp_pct.setRange(0.0, 100.0)
        self.spin_select_hp_pct.setValue(70.0)
        self.spin_select_hp_pct.setSingleStep(0.5)
        fill_controls.addWidget(self.spin_select_hp_pct)
        self.btn_select_by_threshold = QtWidgets.QPushButton("Select By Threshold")
        self.btn_select_by_threshold.clicked.connect(self.select_by_threshold)
        fill_controls.addWidget(self.btn_select_by_threshold)
        self.btn_select_definite = QtWidgets.QPushButton("Select Definite")
        self.btn_select_definite.clicked.connect(self.select_definite)
        fill_controls.addWidget(self.btn_select_definite)
        fill_controls.addStretch(1)
        self.btn_select_lp = QtWidgets.QPushButton("Select LP")
        self.btn_select_lp.clicked.connect(self.select_result_lp)
        fill_controls.addWidget(self.btn_select_lp)
        self.btn_select_hp = QtWidgets.QPushButton("Select HP")
        self.btn_select_hp.clicked.connect(self.select_result_hp)
        fill_controls.addWidget(self.btn_select_hp)
        self.btn_select_both = QtWidgets.QPushButton("Select Both")
        self.btn_select_both.clicked.connect(self.select_result_both)
        fill_controls.addWidget(self.btn_select_both)
        layout.addLayout(fill_controls)

        geom_controls = QtWidgets.QHBoxLayout()
        self.chk_geometry_fill = QtWidgets.QCheckBox("Use Geometry Fill")
        self.chk_geometry_fill.setChecked(True)
        geom_controls.addWidget(self.chk_geometry_fill)
        geom_controls.addWidget(QtWidgets.QLabel("Method:"))
        self.cmb_fill_method = QtWidgets.QComboBox()
        self.cmb_fill_method.addItems(["Surface Fill", "Vertex Owner", "HP Vertex Cluster"])
        geom_controls.addWidget(self.cmb_fill_method)
        geom_controls.addWidget(QtWidgets.QLabel("Samples:"))
        self.spin_surface_samples = QtWidgets.QSpinBox()
        self.spin_surface_samples.setRange(50, 5000)
        self.spin_surface_samples.setValue(600)
        self.spin_surface_samples.setSingleStep(50)
        geom_controls.addWidget(self.spin_surface_samples)
        geom_controls.addWidget(QtWidgets.QLabel("Tolerance (%):"))
        self.spin_surface_tolerance = QtWidgets.QDoubleSpinBox()
        self.spin_surface_tolerance.setRange(0.1, 25.0)
        self.spin_surface_tolerance.setValue(4.0)
        self.spin_surface_tolerance.setSingleStep(0.5)
        geom_controls.addWidget(self.spin_surface_tolerance)
        geom_controls.addWidget(QtWidgets.QLabel("BBox Prefilter (%):"))
        self.spin_bbox_prefilter = QtWidgets.QDoubleSpinBox()
        self.spin_bbox_prefilter.setRange(0.0, 100.0)
        self.spin_bbox_prefilter.setValue(1.0)
        self.spin_bbox_prefilter.setSingleStep(0.5)
        geom_controls.addWidget(self.spin_bbox_prefilter)
        geom_controls.addWidget(QtWidgets.QLabel("Pair Floor (%):"))
        self.spin_pair_floor = QtWidgets.QDoubleSpinBox()
        self.spin_pair_floor.setRange(0.0, 100.0)
        self.spin_pair_floor.setValue(1.0)
        self.spin_pair_floor.setSingleStep(0.5)
        geom_controls.addWidget(self.spin_pair_floor)
        geom_controls.addWidget(QtWidgets.QLabel("Min Vtx:"))
        self.spin_hp_vertex_min_hits = QtWidgets.QSpinBox()
        self.spin_hp_vertex_min_hits.setRange(1, 5000)
        self.spin_hp_vertex_min_hits.setValue(5)
        self.spin_hp_vertex_min_hits.setSingleStep(1)
        geom_controls.addWidget(self.spin_hp_vertex_min_hits)
        geom_controls.addWidget(QtWidgets.QLabel("Vtx Tol (%):"))
        self.spin_hp_vertex_tolerance_pct = QtWidgets.QDoubleSpinBox()
        self.spin_hp_vertex_tolerance_pct.setRange(0.01, 25.0)
        self.spin_hp_vertex_tolerance_pct.setValue(2.0)
        self.spin_hp_vertex_tolerance_pct.setSingleStep(0.25)
        geom_controls.addWidget(self.spin_hp_vertex_tolerance_pct)
        geom_controls.addStretch(1)
        layout.addLayout(geom_controls)

        self.fill_tree = QtWidgets.QTreeWidget()
        self.fill_tree.setColumnCount(5)
        self.fill_tree.setHeaderLabels(["LP Mesh", "LP Fill %", "HP Mesh", "HP Fill %", "Info"])
        self.fill_tree.setAlternatingRowColors(True)
        layout.addWidget(self.fill_tree)
        
        self.text_log = QtWidgets.QTextEdit()
        self.text_log.setReadOnly(True)
        layout.addWidget(self.text_log)

    def log(self, msg):
        self.text_log.append(msg)
        QtWidgets.QApplication.processEvents()

    def _role(self, offset=0):
        try:
            base = QtCore.Qt.UserRole
        except AttributeError:
            base = QtCore.Qt.ItemDataRole.UserRole
        try:
            return base + offset
        except TypeError:
            return QtCore.Qt.ItemDataRole(base.value + offset)

    def _color(self, name):
        try:
            return QtGui.QBrush(getattr(QtCore.Qt, name))
        except Exception:
            return QtGui.QBrush(getattr(QtCore.Qt.GlobalColor, name))

    def _short(self, node):
        return node.split('|')[-1] if node else ""

    def _mesh_transforms_from_selection(self, nodes, expand_descendants=True):
        result = []
        seen = set()
        for node in nodes:
            if not node or not cmds.objExists(node):
                continue

            node_type = cmds.objectType(node)
            if node_type == "mesh":
                parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
                candidates = parents
            else:
                candidates = [node]

            for candidate in candidates:
                if not cmds.objExists(candidate):
                    continue
                shapes = cmds.listRelatives(candidate, shapes=True, fullPath=True, type="mesh") or []
                valid_shapes = [s for s in shapes if not cmds.getAttr(s + ".intermediateObject")]
                if valid_shapes:
                    long_name = cmds.ls(candidate, long=True)[0]
                    if long_name not in seen:
                        result.append(long_name)
                        seen.add(long_name)
                    continue

                if not expand_descendants:
                    continue

                desc_shapes = cmds.listRelatives(candidate, allDescendents=True, fullPath=True, type="mesh") or []
                for shape in desc_shapes:
                    if cmds.getAttr(shape + ".intermediateObject"):
                        continue
                    parent = cmds.listRelatives(shape, parent=True, fullPath=True) or []
                    if parent:
                        long_name = cmds.ls(parent[0], long=True)[0]
                        if long_name not in seen:
                            result.append(long_name)
                            seen.add(long_name)
        return result

    def _selected_category_groups(self, nodes):
        groups = {"LP": [], "HP": []}
        seen = set()
        for node in nodes:
            if not node or not cmds.objExists(node):
                continue

            if cmds.objectType(node) == "mesh":
                parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
                if not parents:
                    continue
                node = parents[0]

            if cmds.objectType(node) != "transform":
                continue

            long_name = cmds.ls(node, long=True)[0]
            if long_name in seen:
                continue

            direct_shapes = cmds.listRelatives(long_name, shapes=True, fullPath=True, type="mesh") or []
            valid_direct_shapes = [s for s in direct_shapes if not cmds.getAttr(s + ".intermediateObject")]
            if valid_direct_shapes:
                continue

            desc_shapes = cmds.listRelatives(long_name, allDescendents=True, fullPath=True, type="mesh") or []
            valid_desc_shapes = [s for s in desc_shapes if not cmds.getAttr(s + ".intermediateObject")]
            if not valid_desc_shapes:
                continue

            category = self._category_from_node(long_name)
            if category in groups:
                groups[category].append(long_name)
                seen.add(long_name)

        return groups

    def _category_from_node(self, node):
        chain = []
        current = node
        while current and cmds.objExists(current):
            chain.append(current)
            parent = cmds.listRelatives(current, parent=True, fullPath=True) or []
            current = parent[0] if parent else None

        for current in chain:
            try:
                if cmds.attributeQuery("BakeManagerGroup", node=current, exists=True):
                    value = cmds.getAttr(current + ".BakeManagerGroup")
                    if value in ("HP", "LP"):
                        return value
            except Exception:
                pass

        ancestry = chain[1:] if len(chain) > 1 else chain
        for current in ancestry:
            name = self._short(current).lower()
            if name.endswith("_lp") or name.endswith("lp") or "_low" in name:
                return "LP"
            if name.endswith("_hp") or name.endswith("hp") or "_high" in name:
                return "HP"

        own_name = self._short(node).lower()
        if own_name.endswith("_lp") or own_name.endswith("lp") or "_low" in own_name:
            return "LP"
        if own_name.endswith("_hp") or own_name.endswith("hp") or "_high" in own_name:
            return "HP"

        return None

    def _category_root_and_counterpart(self, node, category):
        opposite = "LP" if category == "HP" else "HP"
        current = node
        while current and cmds.objExists(current):
            if self._category_from_node(current) == category:
                parent = cmds.listRelatives(current, parent=True, fullPath=True) or []
                if parent:
                    siblings = cmds.listRelatives(parent[0], children=True, fullPath=True, type="transform") or []
                    opposite_roots = [s for s in siblings if self._category_from_node(s) == opposite]
                    if opposite_roots:
                        return current, opposite_roots[0]
            parent = cmds.listRelatives(current, parent=True, fullPath=True) or []
            current = parent[0] if parent else None
        return None, None

    def _bbox_info(self, node):
        bbox = cmds.exactWorldBoundingBox(node)
        dx = max(abs(bbox[3] - bbox[0]), 0.0001)
        dy = max(abs(bbox[4] - bbox[1]), 0.0001)
        dz = max(abs(bbox[5] - bbox[2]), 0.0001)
        return {
            "node": node,
            "name": self._short(node),
            "bbox": bbox,
            "volume": dx * dy * dz,
            "diag": math.sqrt(dx * dx + dy * dy + dz * dz)
        }

    def _bbox_overlap_volume(self, a, b):
        ba = a["bbox"]
        bb = b["bbox"]
        dx = max(0.0, min(ba[3], bb[3]) - max(ba[0], bb[0]))
        dy = max(0.0, min(ba[4], bb[4]) - max(ba[1], bb[1]))
        dz = max(0.0, min(ba[5], bb[5]) - max(ba[2], bb[2]))
        return dx * dy * dz

    def _bbox_gap(self, a, b):
        ba = a["bbox"]
        bb = b["bbox"]
        dx = max(0.0, bb[0] - ba[3], ba[0] - bb[3])
        dy = max(0.0, bb[1] - ba[4], ba[1] - bb[4])
        dz = max(0.0, bb[2] - ba[5], ba[2] - bb[5])
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    def _mesh_dag_path(self, node):
        sel = om.MSelectionList()
        sel.add(node)
        dag = sel.getDagPath(0)
        if dag.hasFn(om.MFn.kTransform):
            dag.extendToShape()
        return dag

    def _mesh_fn(self, node):
        cache = getattr(self, "_mesh_fn_cache", {})
        if node in cache:
            return cache[node]
        dag = self._mesh_dag_path(node)
        fn = om.MFnMesh(dag)
        cache[node] = fn
        self._mesh_fn_cache = cache
        return fn

    def _triangle_area(self, a, b, c):
        ab = om.MVector(b.x - a.x, b.y - a.y, b.z - a.z)
        ac = om.MVector(c.x - a.x, c.y - a.y, c.z - a.z)
        return max((ab ^ ac).length() * 0.5, 0.0)

    def _surface_samples(self, node, sample_count):
        cache_key = (node, int(sample_count))
        cache = getattr(self, "_surface_sample_cache", {})
        if cache_key in cache:
            return cache[cache_key]

        dag = self._mesh_dag_path(node)
        poly_iter = om.MItMeshPolygon(dag)
        triangles = []
        cumulative = []
        total_area = 0.0

        while not poly_iter.isDone():
            try:
                tri_points, _tri_ids = poly_iter.getTriangles(om.MSpace.kWorld)
            except Exception:
                tri_points = []

            for i in range(0, len(tri_points), 3):
                if i + 2 >= len(tri_points):
                    break
                a = tri_points[i]
                b = tri_points[i + 1]
                c = tri_points[i + 2]
                area = self._triangle_area(a, b, c)
                if area <= 1e-10:
                    continue
                triangles.append((a, b, c))
                total_area += area
                cumulative.append(total_area)
            poly_iter.next()

        if not triangles or total_area <= 1e-10:
            cache[cache_key] = []
            self._surface_sample_cache = cache
            return []

        samples = []
        count = max(1, int(sample_count))
        for idx in range(count):
            area_pos = ((idx + 0.5) / float(count)) * total_area
            tri_idx = min(bisect.bisect_left(cumulative, area_pos), len(triangles) - 1)
            a, b, c = triangles[tri_idx]

            r1 = (idx * 0.7548776662466927 + 0.5) % 1.0
            r2 = (idx * 0.5698402909980532 + 0.25) % 1.0
            sqrt_r1 = math.sqrt(r1)
            wa = 1.0 - sqrt_r1
            wb = sqrt_r1 * (1.0 - r2)
            wc = sqrt_r1 * r2
            samples.append(om.MPoint(
                (a.x * wa) + (b.x * wb) + (c.x * wc),
                (a.y * wa) + (b.y * wb) + (c.y * wc),
                (a.z * wa) + (b.z * wb) + (c.z * wc)
            ))

        cache[cache_key] = samples
        self._surface_sample_cache = cache
        return samples

    def _closest_distance_to_mesh(self, point, target_node):
        fn = self._mesh_fn(target_node)
        result = fn.getClosestPoint(point, om.MSpace.kWorld)
        closest = result[0] if isinstance(result, tuple) else result
        dx = point.x - closest.x
        dy = point.y - closest.y
        dz = point.z - closest.z
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    def _adaptive_sample_count(self, source_info, target_info):
        base_count = int(self.spin_surface_samples.value())
        source_diag = max(float(source_info.get("diag", 0.0)), 0.0001)
        target_diag = max(float(target_info.get("diag", 0.0)), 0.0001)
        multiplier = max(1.0, min(source_diag / target_diag, 8.0))
        return max(base_count, min(int(base_count * multiplier), 5000))

    def _samples_near_mesh_pct(self, source_node, target_node, tolerance, sample_count=None):
        samples = self._surface_samples(source_node, int(sample_count or self.spin_surface_samples.value()))
        if not samples:
            return 0.0

        hits = 0
        for point in samples:
            try:
                if self._closest_distance_to_mesh(point, target_node) <= tolerance:
                    hits += 1
            except Exception:
                pass
        return (hits / float(len(samples))) * 100.0

    def _geometry_fill_percents(self, lp_info, hp_info):
        min_diag = max(min(lp_info["diag"], hp_info["diag"]), 0.0001)
        tolerance = max(min_diag * (float(self.spin_surface_tolerance.value()) / 100.0), 0.0001)
        lp_sample_count = self._adaptive_sample_count(lp_info, hp_info)
        hp_sample_count = self._adaptive_sample_count(hp_info, lp_info)
        lp_pct = self._samples_near_mesh_pct(lp_info["node"], hp_info["node"], tolerance, lp_sample_count)
        hp_pct = self._samples_near_mesh_pct(hp_info["node"], lp_info["node"], tolerance, hp_sample_count)
        return lp_pct, hp_pct, "Geo"

    def _mesh_vertex_points(self, node, max_count=None):
        max_count = int(max_count or self.spin_surface_samples.value())
        cache_key = (node, max_count)
        cache = getattr(self, "_vertex_point_cache", {})
        if cache_key in cache:
            return cache[cache_key]

        fn = self._mesh_fn(node)
        points = list(fn.getPoints(om.MSpace.kWorld))
        if max_count > 0 and len(points) > max_count:
            step = len(points) / float(max_count)
            points = [points[min(int(i * step), len(points) - 1)] for i in range(max_count)]
        points = [point for point in points if self._is_finite_point(point)]

        cache[cache_key] = points
        self._vertex_point_cache = cache
        return points

    def _points_to_float_list(self, points):
        result = []
        result_extend = result.extend
        for point in points:
            result_extend([float(point.x), float(point.y), float(point.z)])
        return result

    def _is_finite_point(self, point):
        try:
            return (
                math.isfinite(float(point.x))
                and math.isfinite(float(point.y))
                and math.isfinite(float(point.z))
            )
        except Exception:
            return False

    def _vertex_owner_scores_cpp(self, lp_infos, hp_infos, prefilter_pairs):
        if not HAS_MATH or not hasattr(bg_math_core, "calculate_vertex_owner_scores"):
            return None

        max_points = int(self.spin_surface_samples.value())
        lp_index = dict((info["node"], idx) for idx, info in enumerate(lp_infos))
        hp_index = dict((info["node"], idx) for idx, info in enumerate(hp_infos))
        lp_nodes = [info["node"] for info in lp_infos]
        hp_nodes = [info["node"] for info in hp_infos]
        lp_point_sets = [
            self._points_to_float_list(self._mesh_vertex_points(info["node"], max_points))
            for info in lp_infos
        ]
        hp_point_sets = [
            self._points_to_float_list(self._mesh_vertex_points(info["node"], max_points))
            for info in hp_infos
        ]
        candidate_pairs = [
            (lp_index[lp_node], hp_index[hp_node])
            for lp_node, hp_node in prefilter_pairs
            if lp_node in lp_index and hp_node in hp_index
        ]

        rows = bg_math_core.calculate_vertex_owner_scores(lp_point_sets, hp_point_sets, candidate_pairs)
        scores = {}
        owner_by_hp = {}
        for lp_idx, hp_idx, lp_pct, hp_pct, owner_lp_idx, owner_pct in rows:
            lp_node = lp_nodes[int(lp_idx)]
            hp_node = hp_nodes[int(hp_idx)]
            scores[(lp_node, hp_node)] = [float(lp_pct), float(hp_pct)]
            if int(owner_lp_idx) >= 0 and int(owner_lp_idx) < len(lp_nodes):
                owner_by_hp[hp_node] = (lp_nodes[int(owner_lp_idx)], float(owner_pct))

        return scores, owner_by_hp

    def _nearest_owner_counts(self, source_points, owner_point_sets):
        counts = dict((owner, 0) for owner in owner_point_sets.keys())
        if not source_points or not owner_point_sets:
            return counts

        for point in source_points:
            best_owner = None
            best_dist = None
            for owner, target_points in owner_point_sets.items():
                for target in target_points:
                    dx = point.x - target.x
                    dy = point.y - target.y
                    dz = point.z - target.z
                    dist = (dx * dx) + (dy * dy) + (dz * dz)
                    if best_dist is None or dist < best_dist:
                        best_dist = dist
                        best_owner = owner
            if best_owner is not None:
                counts[best_owner] = counts.get(best_owner, 0) + 1
        return counts

    def _spatial_hash_points(self, points, cell_size):
        cell_size = max(float(cell_size), 0.000001)
        if not math.isfinite(cell_size):
            return {}
        grid = {}
        for point in points:
            if not self._is_finite_point(point):
                continue
            key = (
                int(math.floor(point.x / cell_size)),
                int(math.floor(point.y / cell_size)),
                int(math.floor(point.z / cell_size))
            )
            grid.setdefault(key, []).append(point)
        return grid

    def _near_vertex_hit_count(self, source_points, target_points, tolerance):
        if not source_points or not target_points:
            return 0

        tolerance = max(float(tolerance), 0.000001)
        if not math.isfinite(tolerance):
            return 0
        tol_sq = tolerance * tolerance
        grid = self._spatial_hash_points(target_points, tolerance)
        if not grid:
            return 0
        hits = 0

        for point in source_points:
            if not self._is_finite_point(point):
                continue
            base = (
                int(math.floor(point.x / tolerance)),
                int(math.floor(point.y / tolerance)),
                int(math.floor(point.z / tolerance))
            )
            found = False
            for dx in (-1, 0, 1):
                if found:
                    break
                for dy in (-1, 0, 1):
                    if found:
                        break
                    for dz in (-1, 0, 1):
                        bucket = grid.get((base[0] + dx, base[1] + dy, base[2] + dz), [])
                        for target in bucket:
                            vx = point.x - target.x
                            vy = point.y - target.y
                            vz = point.z - target.z
                            if (vx * vx) + (vy * vy) + (vz * vz) <= tol_sq:
                                hits += 1
                                found = True
                                break
                        if found:
                            break
        return hits

    def _hp_vertex_cluster_percents(self, source_info, target_info):
        max_points = int(self.spin_surface_samples.value())
        source_points = [p for p in self._mesh_vertex_points(source_info["node"], max_points) if self._is_finite_point(p)]
        target_points = [p for p in self._mesh_vertex_points(target_info["node"], max_points) if self._is_finite_point(p)]
        min_diag = max(min(source_info["diag"], target_info["diag"]), 0.0001)
        if not math.isfinite(min_diag):
            return 0.0, 0.0, 0, 0, 0.0
        tolerance = max(min_diag * (float(self.spin_hp_vertex_tolerance_pct.value()) / 100.0), 0.000001)
        if not math.isfinite(tolerance):
            return 0.0, 0.0, 0, 0, 0.0

        source_hits = self._near_vertex_hit_count(source_points, target_points, tolerance)
        target_hits = self._near_vertex_hit_count(target_points, source_points, tolerance)
        source_pct = (source_hits / float(max(len(source_points), 1))) * 100.0
        target_pct = (target_hits / float(max(len(target_points), 1))) * 100.0

        return source_pct, target_pct, source_hits, target_hits, tolerance

    def _vertex_owner_scores(self, lp_infos, hp_infos, prefilter_pairs):
        cpp_result = self._vertex_owner_scores_cpp(lp_infos, hp_infos, prefilter_pairs)
        if cpp_result is not None:
            self.log("<b>Fill:</b> Vertex Owner uses bg_math_core C++ acceleration.")
            return cpp_result

        self.log("<b>Fill:</b> Vertex Owner uses Python fallback. Rebuild bg_math_core.pyd for acceleration.")
        max_points = int(self.spin_surface_samples.value())
        lp_points = dict((info["node"], self._mesh_vertex_points(info["node"], max_points)) for info in lp_infos)
        hp_points = dict((info["node"], self._mesh_vertex_points(info["node"], max_points)) for info in hp_infos)
        scores = {}

        for lp_info in lp_infos:
            lp_node = lp_info["node"]
            candidate_hps = [
                hp_info["node"]
                for hp_info in hp_infos
                if (lp_node, hp_info["node"]) in prefilter_pairs
            ]
            owner_sets = dict((hp_node, hp_points.get(hp_node, [])) for hp_node in candidate_hps)
            counts = self._nearest_owner_counts(lp_points.get(lp_node, []), owner_sets)
            total = max(len(lp_points.get(lp_node, [])), 1)
            for hp_node in candidate_hps:
                scores[(lp_node, hp_node)] = [
                    (counts.get(hp_node, 0) / float(total)) * 100.0,
                    0.0
                ]

        for hp_info in hp_infos:
            hp_node = hp_info["node"]
            candidate_lps = [
                lp_info["node"]
                for lp_info in lp_infos
                if (lp_info["node"], hp_node) in prefilter_pairs
            ]
            owner_sets = dict((lp_node, lp_points.get(lp_node, [])) for lp_node in candidate_lps)
            counts = self._nearest_owner_counts(hp_points.get(hp_node, []), owner_sets)
            total = max(len(hp_points.get(hp_node, [])), 1)
            for lp_node in candidate_lps:
                pair_score = scores.setdefault((lp_node, hp_node), [0.0, 0.0])
                pair_score[1] = (counts.get(lp_node, 0) / float(total)) * 100.0

        owner_by_hp = {}
        for hp_info in hp_infos:
            hp_node = hp_info["node"]
            best_lp = None
            best_pct = -1.0
            for lp_info in lp_infos:
                pair_score = scores.get((lp_info["node"], hp_node))
                if not pair_score:
                    continue
                if pair_score[1] > best_pct:
                    best_pct = pair_score[1]
                    best_lp = lp_info["node"]
            if best_lp:
                owner_by_hp[hp_node] = (best_lp, best_pct)

        return scores, owner_by_hp

    def _bbox_fill_percents(self, lp_info, hp_info):
        overlap = self._bbox_overlap_volume(lp_info, hp_info)
        lp_pct = min(100.0, (overlap / max(lp_info["volume"], 0.0001)) * 100.0)
        hp_pct = min(100.0, (overlap / max(hp_info["volume"], 0.0001)) * 100.0)
        method = "BBox"

        if max(lp_pct, hp_pct) < 10.0:
            gap = self._bbox_gap(lp_info, hp_info)
            min_diag = max(min(lp_info["diag"], hp_info["diag"]), 0.0001)
            max_diag = max(max(lp_info["diag"], hp_info["diag"]), 0.0001)
            size_ratio = min_diag / max_diag
            max_gap = max(min_diag * 0.35, 0.001)
            near_diag_limit = getattr(self, "_near_detail_diag_limit", None)
            is_small_detail_pair = True
            if near_diag_limit is not None:
                is_small_detail_pair = max_diag <= near_diag_limit

            if is_small_detail_pair and gap <= max_gap and size_ratio >= 0.20:
                proximity = max(0.0, 1.0 - (gap / max_gap))
                proxy = min(100.0, proximity * size_ratio * 100.0)
                lp_pct = max(lp_pct, proxy)
                hp_pct = max(hp_pct, proxy)
                method = "NearSmall"

        return lp_pct, hp_pct, method

    def _passes_bbox_prefilter(self, lp_pct, hp_pct):
        return max(lp_pct, hp_pct) >= float(self.spin_bbox_prefilter.value())

    def _passes_display_filter(self, selected_category, lp_pct, hp_pct, min_overlap):
        if selected_category == "HP":
            return lp_pct >= min_overlap
        return max(lp_pct, hp_pct) >= min_overlap

    def _passes_geometry_gate(self, lp_pct, hp_pct):
        pair_floor = float(self.spin_pair_floor.value())
        if min(lp_pct, hp_pct) < pair_floor:
            return False
        lp_gate = float(self.spin_select_lp_pct.value())
        hp_gate = float(self.spin_select_hp_pct.value())
        return lp_pct >= lp_gate or hp_pct >= hp_gate

    def _is_definite_link(self, lp_pct, hp_pct, definite_lp):
        if lp_pct >= definite_lp:
            return True
        if self.chk_geometry_fill.isChecked():
            return (
                lp_pct >= float(self.spin_select_lp_pct.value())
                and hp_pct >= float(self.spin_select_hp_pct.value())
            )
        return False

    def _collect_hp_vertex_cluster_sets(self, selected, selected_groups):
        explicit_hp_meshes = []
        if selected_groups.get("HP"):
            explicit_hp_meshes = self._mesh_transforms_from_selection(selected_groups["HP"], expand_descendants=True)

        if selected_groups.get("LP"):
            lp_meshes = self._mesh_transforms_from_selection(selected_groups["LP"], expand_descendants=True)
            if explicit_hp_meshes:
                return lp_meshes, explicit_hp_meshes

            lp_root, hp_root = self._category_root_and_counterpart(selected_groups["LP"][0], "LP")
            if hp_root:
                return lp_meshes, self._mesh_transforms_from_selection([hp_root], expand_descendants=True)
            self.log("<b>HP Cluster:</b> Could not find the opposite _HP root near the selected _LP group.")
            return [], []

        selected_meshes = self._mesh_transforms_from_selection(selected, expand_descendants=False)
        if not selected_meshes:
            self.log("<b>HP Cluster:</b> Select LP mesh transforms, or select one _LP group and optional _HP group.")
            return [], []

        mesh_categories = [(m, self._category_from_node(m)) for m in selected_meshes]
        categories = sorted(set([c for _m, c in mesh_categories if c in ("LP", "HP")]))
        if categories != ["LP"]:
            self.log("<b>HP Cluster:</b> Selection must contain LP meshes only. HP candidates are collected automatically.")
            self.log("<b>HP Cluster:</b> Detected categories: {}".format(
                ", ".join(["{}={}".format(self._short(m), c or "Unknown") for m, c in mesh_categories[:20]])
            ))
            return [], []

        lp_root, hp_root = self._category_root_and_counterpart(selected_meshes[0], "LP")
        if not hp_root:
            self.log("<b>HP Cluster:</b> Could not find the opposite _HP root near the selected LP mesh.")
            return [], []

        return selected_meshes, self._mesh_transforms_from_selection([hp_root], expand_descendants=True)

    def _calculate_hp_vertex_cluster(self, selected, selected_groups):
        self.fill_tree.clear()
        self.fill_tree.setHeaderLabels(["LP Mesh", "LP BBox %", "HP Vertex Pair", "Vtx Match %", "Info"])

        lp_meshes, hp_meshes = self._collect_hp_vertex_cluster_sets(selected, selected_groups)
        if not lp_meshes or not hp_meshes:
            return

        self._mesh_fn_cache = {}
        self._vertex_point_cache = {}

        min_overlap = float(self.spin_min_overlap.value())
        min_hits = int(self.spin_hp_vertex_min_hits.value())
        lp_infos = [self._bbox_info(m) for m in lp_meshes]
        hp_infos = [self._bbox_info(m) for m in hp_meshes]
        prefilter_skipped = 0
        hit_rejected = 0
        gate_rejected = 0
        total_links = 0
        total_definite = 0
        total_bbox_candidates = 0

        for lp_info in lp_infos:
            bbox_candidates = []
            bbox_by_hp = {}
            for hp_info in hp_infos:
                bbox_lp_pct, bbox_hp_pct, bbox_method = self._bbox_fill_percents(lp_info, hp_info)
                if self._passes_bbox_prefilter(bbox_lp_pct, bbox_hp_pct):
                    bbox_candidates.append(hp_info)
                    bbox_by_hp[hp_info["node"]] = (bbox_lp_pct, bbox_hp_pct, bbox_method)
                else:
                    prefilter_skipped += 1

            total_bbox_candidates += len(bbox_candidates)
            matches = []
            for i, hp_a in enumerate(bbox_candidates):
                for hp_b in bbox_candidates[i + 1:]:
                    a_pct, b_pct, a_hits, b_hits, tolerance = self._hp_vertex_cluster_percents(hp_a, hp_b)
                    if max(a_hits, b_hits) < min_hits:
                        hit_rejected += 1
                        continue
                    if max(a_pct, b_pct) < min_overlap:
                        continue
                    if not self._passes_geometry_gate(a_pct, b_pct):
                        gate_rejected += 1
                        continue

                    a_bbox = bbox_by_hp.get(hp_a["node"], (0.0, 0.0, "BBox"))
                    b_bbox = bbox_by_hp.get(hp_b["node"], (0.0, 0.0, "BBox"))
                    lp_bbox_pct = max(a_bbox[0], b_bbox[0])
                    hp_bbox_pct = max(a_bbox[1], b_bbox[1])
                    bbox_method = "{}/{}".format(a_bbox[2], b_bbox[2])

                    matches.append((
                        hp_a, hp_b, a_pct, b_pct, a_hits, b_hits,
                        tolerance, lp_bbox_pct, hp_bbox_pct, bbox_method
                    ))

            matches.sort(
                key=lambda item: (max(item[2], item[3]), item[7], item[8], max(item[4], item[5])),
                reverse=True
            )
            if not matches and not bbox_candidates:
                continue

            definite_nodes = [
                node
                for item in matches
                for node in (item[0]["node"], item[1]["node"])
                if self._is_definite_link(item[2], item[3], float(self.spin_definite_lp_pct.value()))
            ]
            definite_nodes = list(dict.fromkeys(definite_nodes))
            all_match_nodes = []
            for item in matches:
                all_match_nodes.extend([item[0]["node"], item[1]["node"]])
            if not all_match_nodes:
                all_match_nodes = [info["node"] for info in bbox_candidates]
            all_match_nodes = list(dict.fromkeys(all_match_nodes))

            top = QtWidgets.QTreeWidgetItem([
                lp_info["name"],
                "{:.1f}".format(max([bbox_by_hp.get(hp["node"], (0.0, 0.0, "BBox"))[0] for hp in bbox_candidates] or [0.0])),
                "",
                "{:.1f}".format(max([max(m[2], m[3]) for m in matches] or [0.0])),
                "{} BBox HP candidate(s), {} HP vertex pair(s){}{}".format(
                    len(bbox_candidates),
                    len(matches),
                    " | " if definite_nodes else "",
                    "{} definite".format(len(definite_nodes)) if definite_nodes else ""
                )
            ])
            top.setData(0, self._role(0), lp_info["node"])
            top.setData(0, self._role(1), all_match_nodes)
            top.setData(0, self._role(2), max([bbox_by_hp.get(hp["node"], (0.0, 0.0, "BBox"))[0] for hp in bbox_candidates] or [0.0]))
            top.setData(0, self._role(3), max([max(m[2], m[3]) for m in matches] or [0.0]))
            top.setData(0, self._role(4), bool(definite_nodes))
            top.setData(0, self._role(5), definite_nodes)
            self.fill_tree.addTopLevelItem(top)

            for hp_a, hp_b, a_pct, b_pct, a_hits, b_hits, tolerance, lp_bbox_pct, hp_bbox_pct, bbox_method in matches:
                is_definite = self._is_definite_link(a_pct, b_pct, float(self.spin_definite_lp_pct.value()))
                info_text = (
                    "LP BBox {:.1f}% / HP BBox {:.1f}% [{}] | A {:.1f}% / B {:.1f}% | hits {} / {} | tol {:.5f}".format(
                        lp_bbox_pct, hp_bbox_pct, bbox_method, a_pct, b_pct, a_hits, b_hits, tolerance
                    )
                )
                if is_definite:
                    info_text += " [Strong HP cluster]"

                child = QtWidgets.QTreeWidgetItem([
                    "",
                    "{:.1f}".format(lp_bbox_pct),
                    "{} <-> {}".format(hp_a["name"], hp_b["name"]),
                    "{:.1f} / {:.1f}".format(a_pct, b_pct),
                    info_text
                ])
                child.setData(0, self._role(0), lp_info["node"])
                child.setData(0, self._role(1), [hp_a["node"], hp_b["node"]])
                child.setData(0, self._role(2), lp_bbox_pct)
                child.setData(0, self._role(3), max(a_pct, b_pct))
                child.setData(0, self._role(4), is_definite)
                if is_definite:
                    green_brush = self._color("green")
                    child.setForeground(1, green_brush)
                    child.setForeground(2, green_brush)
                    child.setForeground(4, green_brush)
                    total_definite += 1
                top.addChild(child)
                total_links += 1

            top.setExpanded(True)

        for i in range(self.fill_tree.columnCount()):
            self.fill_tree.resizeColumnToContents(i)

        self.log(
            "<b>HP Cluster:</b> Used {} selected LP mesh(es), collected {} HP mesh(es), kept {} LP->HP BBox candidate(s), found {} HP->HP vertex link(s).".format(
                len(lp_infos), len(hp_infos), total_bbox_candidates, total_links
            )
        )
        self.log(
            "<b>HP Cluster:</b> BBox prefilter >= {:.1f}% skipped {} pair(s); min vertex hits {} rejected {} pair(s); threshold gate rejected {} pair(s). Vtx tolerance: {:.2f}%.".format(
                float(self.spin_bbox_prefilter.value()),
                prefilter_skipped,
                min_hits,
                hit_rejected,
                gate_rejected,
                float(self.spin_hp_vertex_tolerance_pct.value())
            )
        )
        if total_definite:
            self.log("<b>HP Cluster:</b> {} strong HP cluster link(s) found.".format(total_definite))

    def calculate_selected_fill(self):
        self.fill_tree.clear()
        selected = cmds.ls(selection=True, long=True) or []
        selected_groups = self._selected_category_groups(selected)
        use_geometry = self.chk_geometry_fill.isChecked()
        fill_method = self.cmb_fill_method.currentText() if use_geometry else "BBox"
        if use_geometry and fill_method == "HP Vertex Cluster":
            self._calculate_hp_vertex_cluster(selected, selected_groups)
            return

        self.fill_tree.setHeaderLabels(["LP Mesh", "LP Fill %", "HP Mesh", "HP Fill %", "Info"])
        root_pair_mode = bool(selected_groups["LP"] and selected_groups["HP"])

        if root_pair_mode:
            selected_category = "LP"
            lp_meshes = self._mesh_transforms_from_selection(selected_groups["LP"], expand_descendants=True)
            hp_meshes = self._mesh_transforms_from_selection(selected_groups["HP"], expand_descendants=True)
            selected_meshes = lp_meshes
            self.log("<b>Fill:</b> Root pair mode: using selected _LP group(s) as LP and selected _HP group(s) as HP.")
        else:
            selected_meshes = self._mesh_transforms_from_selection(selected, expand_descendants=False)
            if not selected_meshes:
                self.log("<b>Fill:</b> Select LP/HP mesh transforms or select one _LP group and one _HP group.")
                return

            mesh_categories = [(m, self._category_from_node(m)) for m in selected_meshes]
            categories = sorted(set([c for _m, c in mesh_categories if c in ("LP", "HP")]))
            if len(categories) != 1:
                self.log("<b>Fill:</b> Selection must contain meshes from one category only: LP or HP.")
                self.log("<b>Fill:</b> Detected categories: {}".format(
                    ", ".join(["{}={}".format(self._short(m), c or "Unknown") for m, c in mesh_categories[:20]])
                ))
                return

            selected_category = categories[0]
            category_root, counterpart_root = self._category_root_and_counterpart(selected_meshes[0], selected_category)
            if not counterpart_root:
                self.log("<b>Fill:</b> Could not find the opposite HP/LP root near the selection.")
                return

            if selected_category == "LP":
                lp_meshes = selected_meshes
                hp_meshes = self._mesh_transforms_from_selection([counterpart_root])
            else:
                hp_meshes = selected_meshes
                lp_meshes = self._mesh_transforms_from_selection([counterpart_root])

        if not lp_meshes or not hp_meshes:
            self.log("<b>Fill:</b> Could not collect LP/HP mesh sets for comparison.")
            return

        self._surface_sample_cache = {}
        self._mesh_fn_cache = {}
        self._vertex_point_cache = {}
        min_overlap = float(self.spin_min_overlap.value())
        definite_lp = float(self.spin_definite_lp_pct.value())
        lp_infos = [self._bbox_info(m) for m in lp_meshes]
        hp_infos = [self._bbox_info(m) for m in hp_meshes]
        all_diags = sorted([info["diag"] for info in (lp_infos + hp_infos) if info["diag"] > 0.001], reverse=True)
        top_count = min(10, len(all_diags))
        upper_shelf_diag = sum(all_diags[:top_count]) / float(top_count) if top_count else 1.0
        self._near_detail_diag_limit = upper_shelf_diag * 0.18
        total_links = 0
        total_definite = 0
        prefilter_skipped = 0
        geometry_failed = 0
        geometry_gate_rejected = 0
        owner_rejected = 0

        bbox_scores = {}
        prefilter_pairs = set()
        for lp_info in lp_infos:
            for hp_info in hp_infos:
                bbox_lp_pct, bbox_hp_pct, bbox_method = self._bbox_fill_percents(lp_info, hp_info)
                bbox_scores[(lp_info["node"], hp_info["node"])] = (bbox_lp_pct, bbox_hp_pct, bbox_method)
                if self._passes_bbox_prefilter(bbox_lp_pct, bbox_hp_pct):
                    prefilter_pairs.add((lp_info["node"], hp_info["node"]))
                else:
                    prefilter_skipped += 1

        vertex_scores = {}
        vertex_owner_by_hp = {}
        if use_geometry and fill_method == "Vertex Owner":
            try:
                vertex_scores, vertex_owner_by_hp = self._vertex_owner_scores(lp_infos, hp_infos, prefilter_pairs)
            except Exception as exc:
                geometry_failed += 1
                self.log("<b>Fill:</b> Vertex Owner failed: {}. Using empty vertex score set.".format(exc))

        for lp_info in lp_infos:
            matches = []
            for hp_info in hp_infos:
                pair_key = (lp_info["node"], hp_info["node"])
                bbox_lp_pct, bbox_hp_pct, bbox_method = bbox_scores.get(pair_key, (0.0, 0.0, "BBox"))

                if use_geometry:
                    if pair_key not in prefilter_pairs:
                        continue
                    if fill_method == "Vertex Owner":
                        score = vertex_scores.get(pair_key)
                        if not score:
                            continue
                        lp_pct, hp_pct = score[0], score[1]
                        owner_data = vertex_owner_by_hp.get(hp_info["node"])
                        if owner_data and len(lp_infos) > 1:
                            owner_lp, owner_pct = owner_data
                            if owner_lp != lp_info["node"] and owner_pct > hp_pct + 0.01:
                                owner_rejected += 1
                                continue
                        method = "VtxOwner"
                    else:
                        try:
                            lp_pct, hp_pct, method = self._geometry_fill_percents(lp_info, hp_info)
                        except Exception as exc:
                            geometry_failed += 1
                            self.log("<b>Fill:</b> Geometry fill failed for {} / {}: {}. Pair skipped.".format(
                                lp_info["name"], hp_info["name"], exc
                            ))
                            continue
                else:
                    lp_pct, hp_pct, method = bbox_lp_pct, bbox_hp_pct, bbox_method

                if not self._passes_display_filter(selected_category, lp_pct, hp_pct, min_overlap):
                    continue
                if use_geometry and not self._passes_geometry_gate(lp_pct, hp_pct):
                    geometry_gate_rejected += 1
                    continue
                matches.append((hp_info, lp_pct, hp_pct, method))

            matches.sort(key=lambda item: (item[1], item[2], max(item[1], item[2])), reverse=True)
            if not matches:
                continue

            definite_nodes = [
                item[0]["node"]
                for item in matches
                if self._is_definite_link(item[1], item[2], definite_lp)
            ]
            lp_fill_sum = sum(item[1] for item in matches)
            lp_fill_capped = min(100.0, lp_fill_sum)

            top = QtWidgets.QTreeWidgetItem([
                lp_info["name"],
                "{:.1f} / {:.1f}".format(lp_fill_capped, lp_fill_sum),
                "",
                "",
                "{} HP match(es){}{}".format(
                    len(matches),
                    " | " if definite_nodes else "",
                    "{} definite".format(len(definite_nodes)) if definite_nodes else ""
                )
            ])
            top.setData(0, self._role(0), lp_info["node"])
            top.setData(0, self._role(1), [m[0]["node"] for m in matches])
            top.setData(0, self._role(2), lp_fill_capped)
            top.setData(0, self._role(3), max([m[2] for m in matches] or [0.0]))
            top.setData(0, self._role(4), bool(definite_nodes))
            top.setData(0, self._role(5), definite_nodes)
            self.fill_tree.addTopLevelItem(top)

            for hp_info, lp_pct, hp_pct, method in matches:
                is_lp_definite = lp_pct >= definite_lp
                is_definite = self._is_definite_link(lp_pct, hp_pct, definite_lp)
                info_text = "LP {:.1f}% / HP {:.1f}% [{}]".format(lp_pct, hp_pct, method)
                if is_lp_definite:
                    info_text += " [Definite LP owner]"
                elif is_definite:
                    info_text += " [Strong owner link]"
                child = QtWidgets.QTreeWidgetItem([
                    "",
                    "{:.1f}".format(lp_pct),
                    hp_info["name"],
                    "{:.1f}".format(hp_pct),
                    info_text
                ])
                child.setData(0, self._role(0), lp_info["node"])
                child.setData(0, self._role(1), [hp_info["node"]])
                child.setData(0, self._role(2), lp_pct)
                child.setData(0, self._role(3), hp_pct)
                child.setData(0, self._role(4), is_definite)
                if is_definite:
                    green_brush = self._color("green")
                    child.setForeground(1, green_brush)
                    child.setForeground(2, green_brush)
                    child.setForeground(4, green_brush)
                top.addChild(child)
                total_links += 1
                if is_definite:
                    total_definite += 1

            top.setExpanded(True)

        for i in range(self.fill_tree.columnCount()):
            self.fill_tree.resizeColumnToContents(i)

        self.log(
            "<b>Fill:</b> {} selected {} mesh(es), compared against {} {} mesh(es). Found {} LP->HP link(s). Mode: {}.".format(
                len(selected_meshes),
                selected_category,
                len(hp_meshes if selected_category == "LP" else lp_meshes),
                "HP" if selected_category == "LP" else "LP",
                total_links,
                "{} after BBox prefilter".format(fill_method) if use_geometry else "BBox"
            )
        )
        if use_geometry:
            self.log(
                "<b>Fill:</b> BBox prefilter >= {:.1f}% skipped {} pair(s); geometry failed on {} pair(s); geometry gate rejected {} pair(s); vertex owner rejected {} pair(s). Pair floor: {:.1f}%.".format(
                    float(self.spin_bbox_prefilter.value()),
                    prefilter_skipped,
                    geometry_failed,
                    geometry_gate_rejected,
                    owner_rejected,
                    float(self.spin_pair_floor.value())
                )
            )
        if total_definite:
            self.log(
                "<b>Fill:</b> {} definite LP owner link(s) found at LP >= {:.1f}%.".format(
                    total_definite, definite_lp
                )
            )

    def _selected_result_nodes(self, include_lp=True, include_hp=True):
        item = self.fill_tree.currentItem()
        if not item:
            return []
        return self._selected_nodes_from_item(item, include_lp=include_lp, include_hp=include_hp)

    def select_result_lp(self):
        nodes = self._selected_result_nodes(include_lp=True, include_hp=False)
        if nodes:
            cmds.select(nodes, replace=True)

    def select_result_hp(self):
        nodes = self._selected_result_nodes(include_lp=False, include_hp=True)
        if nodes:
            cmds.select(nodes, replace=True)

    def select_result_both(self):
        nodes = self._selected_result_nodes(include_lp=True, include_hp=True)
        if nodes:
            cmds.select(nodes, replace=True)

    def select_by_threshold(self):
        lp_threshold = float(self.spin_select_lp_pct.value())
        hp_threshold = float(self.spin_select_hp_pct.value())
        nodes = []
        seen = set()

        for top_index in range(self.fill_tree.topLevelItemCount()):
            top = self.fill_tree.topLevelItem(top_index)
            for child_index in range(top.childCount()):
                child = top.child(child_index)
                lp_pct = float(child.data(0, self._role(2)) or 0.0)
                hp_pct = float(child.data(0, self._role(3)) or 0.0)
                if lp_pct < lp_threshold and hp_pct < hp_threshold:
                    continue

                for node in self._selected_nodes_from_item(child, include_lp=True, include_hp=True):
                    if node not in seen:
                        nodes.append(node)
                        seen.add(node)

        if nodes:
            cmds.select(nodes, replace=True)
            self.log(
                "<b>Fill:</b> Selected {} node(s) by thresholds LP >= {:.1f}% or HP >= {:.1f}%.".format(
                    len(nodes), lp_threshold, hp_threshold
                )
            )
        else:
            self.log(
                "<b>Fill:</b> No rows match thresholds LP >= {:.1f}% or HP >= {:.1f}%.".format(
                    lp_threshold, hp_threshold
                )
            )

    def select_definite(self):
        nodes = []
        seen = set()

        for top_index in range(self.fill_tree.topLevelItemCount()):
            top = self.fill_tree.topLevelItem(top_index)
            definite_nodes = top.data(0, self._role(5)) or []
            if definite_nodes:
                lp_node = top.data(0, self._role(0))
                if lp_node and lp_node not in seen:
                    nodes.append(lp_node)
                    seen.add(lp_node)
                for node in definite_nodes:
                    if node not in seen:
                        nodes.append(node)
                        seen.add(node)
                continue

            for child_index in range(top.childCount()):
                child = top.child(child_index)
                if not bool(child.data(0, self._role(4))):
                    continue
                for node in self._selected_nodes_from_item(child, include_lp=True, include_hp=True):
                    if node not in seen:
                        nodes.append(node)
                        seen.add(node)

        if nodes:
            cmds.select(nodes, replace=True)
            self.log("<b>Fill:</b> Selected {} node(s) from definite/strong owner links.".format(len(nodes)))
        else:
            self.log("<b>Fill:</b> No definite/strong owner links found.")

    def _selected_nodes_from_item(self, item, include_lp=True, include_hp=True):
        nodes = []
        if include_lp:
            lp_node = item.data(0, self._role(0))
            if lp_node:
                nodes.append(lp_node)
        if include_hp:
            hp_nodes = item.data(0, self._role(1)) or []
            nodes.extend(hp_nodes)
        return [n for n in nodes if n and cmds.objExists(n)]

    def run_analysis(self):
        self.text_log.clear()
        selected = cmds.ls(selection=True, long=True)
        
        if not selected:
            self.log("<b>Ошибка:</b> Ничего не выделено. Выделите группу-главу.")
            return
            
        root_node = selected[0]
        root_name = root_node.split('|')[-1]
        
        if cmds.objectType(root_node) != "transform":
            self.log("<b>Ошибка:</b> Выделен не Transform-узел. Выделите группу.")
            return

        if not HAS_MATH:
            self.log("<b>Внимание:</b> bg_math_core не найден! Некоторые данные (отпечатки, симметрия) будут недоступны.")

        self.log(f"Начат анализ главы: <b>{root_name}</b>...")
        
        # 1. Сбор данных о структуре
        structure_data = self._build_hierarchy(root_node)
        
        # 2. Вычисление глобальных порогов (как в bg_worker_hp.py)
        all_meshes = self._flatten_meshes(structure_data)
        if not all_meshes:
            self.log("В группе нет мешей!")
            return
            
        thresholds = self._calculate_thresholds(all_meshes)
        
        # 3. Присвоение кластеров и поиск коллизий
        self._analyze_meshes(all_meshes, thresholds)
        
        # 4. Сохранение результатов
        self._export_data(root_name, structure_data, all_meshes)

    def _build_hierarchy(self, root_node):
        """Рекурсивно обходит иерархию и собирает данные о группах и мешах."""
        hierarchy = {"name": root_node.split('|')[-1], "type": "chapter", "children": []}
        
        children = cmds.listRelatives(root_node, children=True, fullPath=True) or []
        for child in children:
            child_name = child.split('|')[-1]
            
            # Проверяем, есть ли у ноды shape типа mesh
            shapes = cmds.listRelatives(child, shapes=True, fullPath=True)
            if shapes and cmds.objectType(shapes[0]) == "mesh":
                # Это меш, находящийся прямо в корне (без подгруппы)
                hierarchy["children"].append(self._get_mesh_data(child, child_name))
            else:
                # Это подгруппа
                subgroup = {"name": child_name, "type": "subgroup", "children": []}
                sub_children = cmds.listRelatives(child, children=True, fullPath=True) or []
                
                for sc in sub_children:
                    sc_shapes = cmds.listRelatives(sc, shapes=True, fullPath=True)
                    if sc_shapes and cmds.objectType(sc_shapes[0]) == "mesh":
                        subgroup["children"].append(self._get_mesh_data(sc, sc.split('|')[-1]))
                
                if subgroup["children"]:
                    hierarchy["children"].append(subgroup)
                    
        return hierarchy

    def _get_mesh_data(self, full_path, short_name):
        """Извлекает базовые геометрические данные меша (через API 2.0 для скорости)."""
        bbox = cmds.exactWorldBoundingBox(full_path)
        dx = max(abs(bbox[3] - bbox[0]), 0.0001)
        dy = max(abs(bbox[4] - bbox[1]), 0.0001)
        dz = max(abs(bbox[5] - bbox[2]), 0.0001)
        
        vol = dx * dy * dz
        diag = math.sqrt(dx**2 + dy**2 + dz**2)
        
        vtx_count = cmds.polyEvaluate(full_path, vertex=True)
        
        # Получаем вершины через API
        verts = []
        sel_list = om.MSelectionList()
        sel_list.add(full_path)
        dag_path = sel_list.getDagPath(0)
        dag_path.extendToShape()
        
        if dag_path.node().hasFn(om.MFn.kMesh):
            mesh_fn = om.MFnMesh(dag_path)
            points = mesh_fn.getPoints(om.MSpace.kWorld)
            verts = [coord for pt in points for coord in (pt.x, pt.y, pt.z)]

        return {
            "name": short_name,
            "type": "mesh",
            "bbox": bbox,
            "bbox_vol": vol,
            "diag": diag,
            "vtx_count": vtx_count,
            "verts": verts,
            "collisions": [],
            "cluster_category": "Unknown",
            "elongation": 1.0,
            "symmetry": 0.0,
            "fingerprint": "empty"
        }

    def _flatten_meshes(self, hierarchy):
        """Собирает все меши в один плоский список для глобального анализа."""
        meshes = []
        for child in hierarchy["children"]:
            if child["type"] == "mesh":
                meshes.append(child)
            elif child["type"] == "subgroup":
                for m in child["children"]:
                    meshes.append(m)
        return meshes

    def _calculate_thresholds(self, all_meshes):
        """Симулирует Шаг 4 из bg_worker_hp.py (Smart Size Boundaries)"""
        diags = sorted([m["diag"] for m in all_meshes if m["diag"] > 0.001], reverse=True)
        top_count = min(10, len(diags))
        upper_shelf_diag = sum(diags[:top_count]) / top_count if top_count > 0 else 1.0
        
        bolt_median_diag = diags[-1] * 1.5 if diags else 0.1
        
        small_threshold = bolt_median_diag * 1.5
        large_threshold = upper_shelf_diag * 0.6
        medium_threshold = (small_threshold + large_threshold) / 2.0
        
        return {
            "small": small_threshold,
            "medium": medium_threshold,
            "large": large_threshold
        }

    def _analyze_meshes(self, all_meshes, thresholds):
        """Анализирует каждый меш: пересечения BBox, отпечатки ядра и кластеризация."""
        self.log("Обработка математического ядра и коллизий...")
        
        # 1. Сбор математики
        for m in all_meshes:
            if HAS_MATH and m["verts"]:
                metrics = bg_math_core.analyze_mesh_shape(m["verts"])
                m["elongation"] = metrics.elongation
                m["symmetry"] = metrics.symmetry_score
                m["fingerprint"] = bg_math_core.generate_fingerprint_data(m["verts"], metrics.center)
                
            # Определение категории (Симуляция Шага 6)
            is_bolt = m["elongation"] < 2.5 and m["symmetry"] < 0.8 and m["diag"] <= thresholds["medium"]
            is_wire = m["elongation"] > 4.0
            
            if (m["diag"] <= thresholds["small"] or is_bolt) and not is_wire:
                m["cluster_category"] = "Bolt/Small"
            elif m["diag"] <= thresholds["medium"]:
                m["cluster_category"] = "Medium"
            elif m["diag"] <= thresholds["large"]:
                m["cluster_category"] = "Large"
            else:
                m["cluster_category"] = "Huge"
                
            if is_wire:
                m["cluster_category"] += " (Wire/Pipe)"

        # 2. Поиск коллизий BBox (O(N^2), но для анализатора это быстро)
        total = len(all_meshes)
        for i in range(total):
            b1 = all_meshes[i]["bbox"]
            for j in range(i + 1, total):
                b2 = all_meshes[j]["bbox"]
                # AABB Collision Check
                if not (b1[3] < b2[0] or b1[0] > b2[3] or
                        b1[4] < b2[1] or b1[1] > b2[4] or
                        b1[5] < b2[2] or b1[2] > b2[5]):
                    all_meshes[i]["collisions"].append(all_meshes[j]["name"])
                    all_meshes[j]["collisions"].append(all_meshes[i]["name"])

    def _export_data(self, root_name, structure_data, all_meshes):
        """Генерирует файлы TXT и JSON в папке проекта Maya или Документах."""
        # Убираем тяжелые 'verts' перед экспортом, чтобы файлы не весили гигабайты
        for m in all_meshes:
            m.pop("verts", None)
            m["bbox"] = [round(v, 4) for v in m["bbox"]]
            m["bbox_vol"] = round(m["bbox_vol"], 4)
            m["diag"] = round(m["diag"], 4)
            m["elongation"] = round(m["elongation"], 4)
            m["symmetry"] = round(m["symmetry"], 4)

        # Путь сохранения (Рядом с текущей сценой или в Документы)
        scene_path = cmds.file(q=True, sceneName=True)
        if scene_path:
            save_dir = os.path.dirname(scene_path)
        else:
            save_dir = os.path.expanduser("~\\Documents")
            
        timestamp = datetime.now().strftime("%H%M%S")
        base_filename = os.path.join(save_dir, f"Analysis_{root_name}_{timestamp}")
        
        # --- ГЕНЕРАЦИЯ TXT ФАЙЛА (Человекочитаемый) ---
        txt_path = base_filename + ".txt"
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(f"Глава: {structure_data['name']}\n")
            for child in structure_data["children"]:
                if child["type"] == "subgroup":
                    f.write(f"_{child['name']}\n")
                    for m in child["children"]:
                        self._write_mesh_to_txt(f, m, level=2)
                elif child["type"] == "mesh":
                    self._write_mesh_to_txt(f, child, level=1)

        # --- ГЕНЕРАЦИЯ JSON ФАЙЛА (Для машинного Diff) ---
        json_path = base_filename + ".json"
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(structure_data, f, indent=4, ensure_ascii=False)

        self.log(f"<br><b>Анализ успешно завершен!</b>")
        self.log(f"Всего мешей проанализировано: {len(all_meshes)}")
        self.log(f"Файлы сохранены в:\n<a href='file:///{save_dir}'>{save_dir}</a>")
        self.log(f"TXT: {os.path.basename(txt_path)}")
        self.log(f"JSON: {os.path.basename(json_path)}")

    def _write_mesh_to_txt(self, file_handle, mesh_data, level):
        indent = "_" * level
        info_indent = "_" * (level + 1)
        
        file_handle.write(f"{indent}{mesh_data['name']}\n")
        file_handle.write(f"{info_indent}Кластер (Worker): {mesh_data['cluster_category']}\n")
        file_handle.write(f"{info_indent}Объем BBox: {mesh_data['bbox_vol']}\n")
        file_handle.write(f"{info_indent}Отпечаток: {mesh_data['fingerprint']}\n")
        file_handle.write(f"{info_indent}Вытянутость: {mesh_data['elongation']} | Симметрия: {mesh_data['symmetry']}\n")
        
        col_str = ", ".join(mesh_data['collisions']) if mesh_data['collisions'] else "Нет"
        file_handle.write(f"{info_indent}Пересекается с BBox: [{col_str}]\n")

# --- ЗАПУСК ---
analyzer_window = None

def show_analyzer():
    global analyzer_window
    try:
        if analyzer_window:
            analyzer_window.close()
            analyzer_window.deleteLater()
    except:
        pass
        
    analyzer_window = StructureAnalyzer()
    analyzer_window.show()

if __name__ == "__main__":
    show_analyzer()
