# -*- coding: utf-8 -*-
from __future__ import print_function, division, absolute_import

import math
import bisect

import maya.cmds as cmds
import maya.api.OpenMaya as om
import bg_core
import bg_localization as bg_l10n

try:
    from PySide6 import QtWidgets, QtCore, QtGui
except ImportError:
    from PySide2 import QtWidgets, QtCore, QtGui


class GTWidget(QtWidgets.QWidget):
    def __init__(self, parent_bg, parent=None):
        super(GTWidget, self).__init__(parent)
        self.bg = parent_bg
        self.cached_hp_data = []
        self.build_ui()

    def build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(6)

        header = QtWidgets.QLabel("Batch HP->LP Matcher (Active Pair)")
        header.setStyleSheet("font-weight: bold; color: white; background-color: #333; padding: 4px; border-radius: 2px;")
        header.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(header)

        info_layout = QtWidgets.QGridLayout()
        info_layout.addWidget(QtWidgets.QLabel("HP Root:"), 0, 0)
        self.lbl_hp = QtWidgets.QLabel("None")
        self.lbl_hp.setWordWrap(True)
        self.lbl_hp.setStyleSheet("color: #75cce8; font-weight: bold;")
        info_layout.addWidget(self.lbl_hp, 0, 1)

        info_layout.addWidget(QtWidgets.QLabel("LP Root:"), 1, 0)
        self.lbl_lp = QtWidgets.QLabel("None")
        self.lbl_lp.setWordWrap(True)
        self.lbl_lp.setStyleSheet("color: #75cce8; font-weight: bold;")
        info_layout.addWidget(self.lbl_lp, 1, 1)
        layout.addLayout(info_layout)

        set_layout = QtWidgets.QGridLayout()
        set_layout.addWidget(QtWidgets.QLabel("Tolerance (%):"), 0, 0)
        self.tol_spin = QtWidgets.QDoubleSpinBox()
        self.tol_spin.setRange(0.01, 20.0)
        self.tol_spin.setValue(5.0)
        self.tol_spin.setSingleStep(0.5)
        set_layout.addWidget(self.tol_spin, 0, 1)

        set_layout.addWidget(QtWidgets.QLabel("Min HP/LP:"), 1, 0)
        self.min_hp_spin = QtWidgets.QSpinBox()
        self.min_hp_spin.setRange(1, 100)
        self.min_hp_spin.setValue(2)
        set_layout.addWidget(self.min_hp_spin, 1, 1)

        set_layout.addWidget(QtWidgets.QLabel("Match Mode:"), 2, 0)
        self.match_mode_combo = QtWidgets.QComboBox()
        self.match_mode_combo.addItem("Balanced", "BALANCED")
        self.match_mode_combo.addItem("Fast", "FAST")
        self.match_mode_combo.addItem("Accurate", "ACCURATE")
        self.match_mode_combo.setToolTip(
            "Fast: legacy bbox matcher.\n"
            "Balanced: bbox prefilter with surface coverage scoring.\n"
            "Accurate: more surface samples and stricter ambiguous matches."
        )
        set_layout.addWidget(self.match_mode_combo, 2, 1)

        self.geo_check_cb = QtWidgets.QCheckBox("Strict Geo Check (Resolve Overlaps)")
        self.geo_check_cb.setChecked(True)
        self.geo_check_cb.setToolTip(
            "ON: prefer bbox overlap, but still allows near ZBrush shells.\n"
            "OFF: also allows nearest-neighbour bbox gap matching for all HP meshes."
        )
        set_layout.addWidget(self.geo_check_cb, 3, 0, 1, 2)
        layout.addLayout(set_layout)

        match_btns_layout = QtWidgets.QHBoxLayout()
        self.match_btn = QtWidgets.QPushButton("Find LP Groups")
        self.match_btn.setStyleSheet("background-color: #d18c15; color: #1e1e1e; font-weight: bold;")
        self.match_btn.clicked.connect(self.process_batch_match)
        match_btns_layout.addWidget(self.match_btn)

        self.relocate_btn = QtWidgets.QPushButton("Relocate HP")
        self.relocate_btn.setStyleSheet("background-color: #5c85d6; color: white; font-weight: bold;")
        self.relocate_btn.setToolTip("Move linked HP meshes into the best existing HP subgroup. Does not create new subgroups.")
        self.relocate_btn.clicked.connect(self.relocate_hp)
        match_btns_layout.addWidget(self.relocate_btn)
        layout.addLayout(match_btns_layout)

        self.result_list = QtWidgets.QListWidget()
        self.result_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.result_list.itemSelectionChanged.connect(self.on_lp_selected)
        layout.addWidget(self.result_list)

        link_layout = QtWidgets.QHBoxLayout()
        self.link_btn = QtWidgets.QPushButton("Link")
        self.link_btn.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold; padding: 6px;")
        self.link_btn.clicked.connect(self.link_selected)
        link_layout.addWidget(self.link_btn)

        self.unlink_btn = QtWidgets.QPushButton("Unlink")
        self.unlink_btn.setStyleSheet("background-color: #c62828; color: white; font-weight: bold; padding: 6px;")
        self.unlink_btn.clicked.connect(self.unlink_selected)
        link_layout.addWidget(self.unlink_btn)

        self.btn_add_empty = QtWidgets.QPushButton("New")
        self.btn_add_empty.setToolTip("Create new_group_###. If HP meshes are selected, bind them immediately.")
        self.btn_add_empty.clicked.connect(self.create_empty_cluster)
        link_layout.addWidget(self.btn_add_empty)
        layout.addLayout(link_layout)
        bg_l10n.localize_widget_tree(self)

    # -------------------------------------------------------------------------
    # Generic helpers
    # -------------------------------------------------------------------------
    def _long(self, node):
        if not node or not cmds.objExists(node):
            return None
        res = cmds.ls(node, long=True) or []
        return res[0] if res else node

    def _is_under(self, node, root):
        node = self._long(node)
        root = self._long(root)
        if not node or not root:
            return False
        if node == root:
            return True
        if hasattr(self.bg.core, 'is_descendant_of'):
            try:
                return bool(self.bg.core.is_descendant_of(node, root))
            except Exception:
                pass
        return node.startswith(root + '|')

    def _safe_uuid(self, node):
        if not node or not cmds.objExists(node):
            return None
        try:
            ids = cmds.ls(node, uuid=True) or []
            return ids[0] if ids else None
        except Exception:
            return None

    def _mesh_transforms_under(self, root):
        if not root or not cmds.objExists(root):
            return []
        meshes = cmds.listRelatives(root, allDescendents=True, type='mesh', fullPath=True) or []
        result = []
        seen = set()
        for shape in meshes:
            try:
                if cmds.getAttr("{}.intermediateObject".format(shape)):
                    continue
            except Exception:
                pass
            parent = cmds.listRelatives(shape, parent=True, fullPath=True) or []
            if parent and parent[0] not in seen:
                seen.add(parent[0])
                result.append(parent[0])
        return result

    def _node_to_mesh_transforms(self, node, hp_root=None):
        """Convert transform/shape/component/group selection to mesh transforms."""
        if not node:
            return []
        if '.' in node:
            node = node.split('.', 1)[0]
        if not cmds.objExists(node):
            return []

        out = []
        obj_type = cmds.objectType(node)
        if obj_type == 'mesh':
            parent = cmds.listRelatives(node, parent=True, fullPath=True) or []
            if parent:
                out.append(parent[0])
        else:
            long_node = self._long(node)
            if cmds.listRelatives(long_node, shapes=True, type='mesh', fullPath=True):
                out.append(long_node)
            else:
                shapes = cmds.listRelatives(long_node, allDescendents=True, type='mesh', fullPath=True) or []
                for shape in shapes:
                    try:
                        if cmds.getAttr("{}.intermediateObject".format(shape)):
                            continue
                    except Exception:
                        pass
                    parent = cmds.listRelatives(shape, parent=True, fullPath=True) or []
                    if parent:
                        out.append(parent[0])

        result = []
        seen = set()
        for m in out:
            lm = self._long(m)
            if not lm or lm in seen:
                continue
            if hp_root and not self._is_under(lm, hp_root):
                continue
            seen.add(lm)
            result.append(lm)
        return result

    def _selection_to_hp_meshes(self, hp_root):
        sel = cmds.ls(selection=True, long=True, flatten=True) or []
        result = []
        seen = set()
        for obj in sel:
            for m in self._node_to_mesh_transforms(obj, hp_root):
                if m not in seen:
                    seen.add(m)
                    result.append(m)
        return result

    def _uuid_to_transform_map(self, hp_root):
        mapping = {}
        for mesh in self._mesh_transforms_under(hp_root):
            uid = self._safe_uuid(mesh)
            if uid:
                mapping[uid] = mesh
        return mapping

    def _resolve_uuid_to_node(self, uid, uuid_map=None):
        if uuid_map and uid in uuid_map and cmds.objExists(uuid_map[uid]):
            return self._long(uuid_map[uid])

        # Maya usually can resolve UUID via cmds.ls(uuid), but not always in older scenes.
        try:
            found = cmds.ls(uid, long=True) or []
            if found and cmds.objExists(found[0]):
                node = found[0]
                if cmds.objectType(node) == 'mesh':
                    parent = cmds.listRelatives(node, parent=True, fullPath=True) or []
                    return parent[0] if parent else None
                return self._long(node)
        except Exception:
            pass

        return None

    def _nodes_from_uuid_list(self, uuids, hp_root=None, uuid_map=None):
        result = []
        seen = set()
        for uid in uuids or []:
            node = self._resolve_uuid_to_node(uid, uuid_map)
            if not node or not cmds.objExists(node):
                continue
            node = self._long(node)
            if hp_root and not self._is_under(node, hp_root):
                continue
            if node not in seen:
                seen.add(node)
                result.append(node)
        return result

    def _uuid_list_from_nodes(self, nodes):
        result = []
        for node in nodes or []:
            uid = self._safe_uuid(node)
            if uid and uid not in result:
                result.append(uid)
        return result

    def _mark_item_linked(self, item, cluster_name, hp_nodes):
        item.setData(QtCore.Qt.UserRole, {cluster_name: list(hp_nodes)})
        item.setBackground(QtGui.QColor("#2e7d32"))
        item.setForeground(QtGui.QColor("white"))
        current_text = item.text().replace("[Linked] ", "")
        item.setText("[Linked] " + current_text)


    def _save_session(self, pair=None):
        """Save root_pairs and normalize custom_grouping to stable unique UUID lists."""
        if pair is not None:
            clean = {}
            for name, uuids in (pair.get('custom_grouping') or {}).items():
                if not name:
                    continue
                vals = []
                for uid in uuids or []:
                    if uid and uid not in vals:
                        vals.append(uid)
                clean[name] = vals
            pair['custom_grouping'] = clean

        try:
            bg_core.BakeSessionModel.save(self.bg.root_pairs)
        except Exception as e:
            self.bg.log("Failed to save GT links: {}".format(e), "red")
            return False
        return True

    def _hp_uuid_set_from_nodes(self, hp_nodes):
        result = set()
        for node in hp_nodes:
            uid = self._safe_uuid(node)
            if uid:
                result.add(uid)
        return result

    def _find_custom_cluster_by_uuid_set(self, pair, uuid_set):
        if not uuid_set:
            return None
        for name, uuids in (pair.get('custom_grouping') or {}).items():
            existing = set([u for u in (uuids or []) if u])
            if existing == uuid_set:
                return name
        return None

    def _is_exact_cluster_already_grouped(self, hp_nodes, hp_root):
        """
        Detect when the exact same mesh set is already isolated inside one
        existing HP subgroup. We still show it in the matcher list, because saved
        GT/manual links must stay visible after Analyze HP.
        """
        hp_nodes = [self._long(n) for n in hp_nodes if n and cmds.objExists(n)]
        hp_nodes = [n for n in hp_nodes if n]
        if not hp_nodes:
            return False

        target_set = set(hp_nodes)
        parents = set()
        for n in hp_nodes:
            p = cmds.listRelatives(n, parent=True, fullPath=True) or []
            if p:
                parents.add(p[0])

        if len(parents) != 1:
            return False

        shared_parent = list(parents)[0]
        hp_root = self._long(hp_root)
        if not shared_parent or shared_parent == hp_root:
            return False

        if cmds.listRelatives(shared_parent, shapes=True, type='mesh', fullPath=True):
            return False

        direct_mesh_children = []
        for child in cmds.listRelatives(shared_parent, children=True, fullPath=True, type='transform') or []:
            if cmds.listRelatives(child, shapes=True, type='mesh', fullPath=True):
                direct_mesh_children.append(self._long(child))

        return set(direct_mesh_children) == target_set

    def _is_zbrush_hp(self, hp_data):
        node = hp_data.get('node') or hp_data.get('name') or ''
        return bool(hp_data.get('is_zbrush') or 'zbrush' in node.lower())

    def _bbox_gap(self, a, b):
        """Distance between two AABBs. 0 means they overlap/touch."""
        gap_sq = 0.0
        for i in range(3):
            amin, amax = a['min'][i], a['max'][i]
            bmin, bmax = b['min'][i], b['max'][i]
            if amax < bmin:
                d = bmin - amax
            elif bmax < amin:
                d = amin - bmax
            else:
                d = 0.0
            gap_sq += d * d
        return math.sqrt(gap_sq)

    def _diag_from_data(self, d):
        size = d.get('size')
        if size:
            return math.sqrt(size[0] * size[0] + size[1] * size[1] + size[2] * size[2])
        mn = d.get('min', [0, 0, 0])
        mx = d.get('max', [0, 0, 0])
        return math.sqrt(sum((mx[i] - mn[i]) ** 2 for i in range(3)))

    def current_match_mode(self):
        combo = getattr(self, 'match_mode_combo', None)
        if not combo:
            return "BALANCED"
        try:
            data = combo.currentData()
            if data:
                return str(data)
        except Exception:
            pass
        return str(combo.currentText()).upper()

    def _create_match_progress(self, mode):
        if mode == "FAST":
            return None
        label = "Balanced HP->LP matching..." if mode == "BALANCED" else "Accurate HP->LP matching..."
        dlg = QtWidgets.QProgressDialog(label, "Cancel", 0, 100, self)
        dlg.setWindowModality(QtCore.Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setValue(0)
        dlg.show()
        QtWidgets.QApplication.processEvents()
        return dlg

    def _update_match_progress(self, dlg, value, text=None):
        if not dlg:
            return False
        if text:
            dlg.setLabelText(text)
        dlg.setValue(max(0, min(100, int(value))))
        QtWidgets.QApplication.processEvents()
        return dlg.wasCanceled()

    def _close_match_progress(self, dlg):
        if dlg:
            dlg.close()
            QtWidgets.QApplication.processEvents()

    def _surface_sample_count(self, role):
        mode = self.current_match_mode()
        if mode == "ACCURATE":
            return 220 if role == "lp" else 160
        return 120 if role == "lp" else 90

    def _surface_tolerance(self, hp, lp):
        hp_diag = max(float(hp.get('diag') or self._diag_from_data(hp)), 0.000001)
        lp_diag = max(float(lp.get('diag') or self._diag_from_data(lp)), 0.000001)
        tol_pct = max(float(self.tol_spin.value()) / 100.0, 0.01)
        if self.current_match_mode() == "ACCURATE":
            tol_pct = max(tol_pct * 0.75, 0.0125)
        else:
            tol_pct = max(tol_pct, 0.02)
        return max(min(hp_diag, lp_diag) * tol_pct, 0.0001)

    def _point_distance(self, a, b):
        dx = a.x - b.x
        dy = a.y - b.y
        dz = a.z - b.z
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    def _triangle_area(self, a, b, c):
        ab = om.MVector(b.x - a.x, b.y - a.y, b.z - a.z)
        ac = om.MVector(c.x - a.x, c.y - a.y, c.z - a.z)
        return max((ab ^ ac).length() * 0.5, 0.0)

    def _sample_points_evenly(self, points, max_count):
        if not points:
            return []
        points = list(points)
        max_count = max(1, int(max_count))
        if len(points) <= max_count:
            return [om.MPoint(p) for p in points]
        step = len(points) / float(max_count)
        return [om.MPoint(points[min(int(i * step), len(points) - 1)]) for i in range(max_count)]

    def _sample_triangles(self, triangles, sample_count):
        if not triangles:
            return []
        cumulative = []
        weighted = []
        total_area = 0.0
        for a, b, c in triangles:
            area = self._triangle_area(a, b, c)
            if area <= 1e-10:
                continue
            total_area += area
            cumulative.append(total_area)
            weighted.append((a, b, c))
        if not weighted or total_area <= 1e-10:
            return []
        samples = []
        count = max(1, int(sample_count))
        for idx in range(count):
            area_pos = ((idx + 0.5) / float(count)) * total_area
            tri_idx = min(bisect.bisect_left(cumulative, area_pos), len(weighted) - 1)
            a, b, c = weighted[tri_idx]
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
        return samples

    def _mesh_dag_path(self, node):
        sel = om.MSelectionList()
        sel.add(node)
        dag = sel.getDagPath(0)
        if dag.hasFn(om.MFn.kTransform):
            dag.extendToShape()
        return dag

    def _mesh_surface_samples(self, node, sample_count):
        cache_key = (node, int(sample_count))
        cache = getattr(self, '_gt_surface_sample_cache', {})
        if cache_key in cache:
            return cache[cache_key]
        try:
            dag = self._mesh_dag_path(node)
            poly_iter = om.MItMeshPolygon(dag)
            triangles = []
            while not poly_iter.isDone():
                try:
                    tri_points, _tri_ids = poly_iter.getTriangles(om.MSpace.kWorld)
                except Exception:
                    tri_points = []
                for i in range(0, len(tri_points), 3):
                    if i + 2 < len(tri_points):
                        triangles.append((tri_points[i], tri_points[i + 1], tri_points[i + 2]))
                poly_iter.next()
            samples = self._sample_triangles(triangles, sample_count)
        except Exception:
            samples = []
        cache[cache_key] = samples
        self._gt_surface_sample_cache = cache
        return samples

    def _mesh_intersector(self, node):
        cache = getattr(self, '_gt_intersector_cache', {})
        if node in cache:
            return cache[node]
        try:
            dag = self._mesh_dag_path(node)
            intersector = om.MMeshIntersector()
            intersector.create(dag.node(), dag.inclusiveMatrix())
        except Exception:
            intersector = None
        cache[node] = intersector
        self._gt_intersector_cache = cache
        return intersector

    def _closest_distance_to_data(self, point, target_data):
        intersector = target_data.get('intersector')
        if intersector:
            try:
                point_info = intersector.getClosestPoint(point)
                return self._point_distance(point, om.MPoint(point_info.point))
            except Exception:
                pass
        points = target_data.get('sample_points') or []
        if not points:
            return float('inf')
        best = float('inf')
        for candidate in points:
            dist = self._point_distance(point, candidate)
            if dist < best:
                best = dist
        return best

    def _coverage_and_distance(self, source_points, target_data, tolerance):
        if not source_points:
            return 0.0, float('inf')
        hits = 0
        total = 0.0
        checked = 0
        for point in source_points:
            dist = self._closest_distance_to_data(point, target_data)
            if dist == float('inf'):
                continue
            checked += 1
            total += dist
            if dist <= tolerance:
                hits += 1
        if checked <= 0:
            return 0.0, float('inf')
        return hits / float(checked), total / float(checked)

    def _bbox_overlap_ratio(self, hp, lp):
        hp_vol = max(hp['size'][0] * hp['size'][1] * hp['size'][2], 0.000001)
        lp_vol = max(lp.get('vol', 0.000001), 0.000001)
        min_x = max(hp['min'][0], lp['min'][0])
        min_y = max(hp['min'][1], lp['min'][1])
        min_z = max(hp['min'][2], lp['min'][2])
        max_x = min(hp['max'][0], lp['max'][0])
        max_y = min(hp['max'][1], lp['max'][1])
        max_z = min(hp['max'][2], lp['max'][2])
        if min_x < max_x and min_y < max_y and min_z < max_z:
            intersect_vol = (max_x - min_x) * (max_y - min_y) * (max_z - min_z)
            return intersect_vol / max(min(hp_vol, lp_vol), 0.000001)
        return 0.0

    def _size_similarity(self, hp, lp):
        hp_diag = max(float(hp.get('diag') or self._diag_from_data(hp)), 0.000001)
        lp_diag = max(float(lp.get('diag') or self._diag_from_data(lp)), 0.000001)
        hp_vol = max(hp['size'][0] * hp['size'][1] * hp['size'][2], 0.000001)
        lp_vol = max(lp.get('vol', 0.000001), 0.000001)
        diag_sim = min(hp_diag, lp_diag) / max(hp_diag, lp_diag)
        vol_sim = min(hp_vol, lp_vol) / max(hp_vol, lp_vol)
        return (diag_sim * 0.65) + (vol_sim * 0.35)

    def _surface_prefilter_pass(self, hp, lp):
        overlap_ratio = self._bbox_overlap_ratio(hp, lp)
        if overlap_ratio > 0.0:
            return True
        gap = self._bbox_gap(hp, lp)
        hp_diag = max(float(hp.get('diag') or self._diag_from_data(hp)), 0.000001)
        lp_diag = max(float(lp.get('diag') or self._diag_from_data(lp)), 0.000001)
        mode = self.current_match_mode()
        multiplier = 0.9 if mode == "BALANCED" else 0.65
        if self._is_zbrush_hp(hp):
            multiplier *= 1.75
        allowed_gap = max(min(hp_diag, lp_diag) * multiplier, hp_diag * 0.35, 0.001)
        if self.geo_check_cb.isChecked() and not self._is_zbrush_hp(hp):
            allowed_gap = min(allowed_gap, min(hp_diag, lp_diag) * 0.75)
        return gap <= allowed_gap

    def _surface_match_score_hp_to_lp(self, hp, lp):
        if not self._surface_prefilter_pass(hp, lp):
            return None
        hp_points = hp.get('sample_points') or []
        lp_points = lp.get('sample_points') or []
        if not hp_points or not lp_points:
            return self._match_score_hp_to_lp(hp, lp)

        tolerance = self._surface_tolerance(hp, lp)
        lp_coverage, lp_avg_dist = self._coverage_and_distance(lp_points, hp, tolerance)
        hp_coverage, hp_avg_dist = self._coverage_and_distance(hp_points, lp, tolerance)

        mode = self.current_match_mode()
        min_lp_coverage = 0.16 if mode == "BALANCED" else 0.24
        min_hp_coverage = 0.28 if mode == "BALANCED" else 0.38
        if lp_coverage < min_lp_coverage and hp_coverage < min_hp_coverage:
            return None

        avg_dist = min(lp_avg_dist, hp_avg_dist)
        dist_score = 0.0 if avg_dist == float('inf') else max(0.0, 1.0 - min(avg_dist / max(tolerance * 3.0, 0.000001), 1.0))
        overlap_ratio = self._bbox_overlap_ratio(hp, lp)
        bbox_score = min(overlap_ratio * 2.0, 1.0) if overlap_ratio > 0.0 else max(0.0, 1.0 - min(self._bbox_gap(hp, lp) / max(tolerance * 6.0, 0.000001), 1.0))
        size_score = self._size_similarity(hp, lp)

        score = (
            (lp_coverage * 0.46) +
            (hp_coverage * 0.24) +
            (dist_score * 0.15) +
            (size_score * 0.10) +
            (bbox_score * 0.05)
        ) * 1000.0

        hp_diag = max(float(hp.get('diag') or self._diag_from_data(hp)), 0.000001)
        lp_diag = max(float(lp.get('diag') or self._diag_from_data(lp)), 0.000001)
        diag_ratio = min(hp_diag, lp_diag) / max(hp_diag, lp_diag)
        if overlap_ratio > 0.5 and diag_ratio < 0.35 and lp_coverage < 0.2:
            score -= 220.0
        if mode == "ACCURATE" and lp_coverage < 0.35 and hp_coverage < 0.55:
            score -= 120.0
        if score <= 0.0:
            return None
        return score

    def _match_score_hp_to_lp(self, hp, lp):
        hp_vol = max(hp['size'][0] * hp['size'][1] * hp['size'][2], 0.000001)
        lp_vol = max(lp.get('vol', 0.000001), 0.000001)

        min_x = max(hp['min'][0], lp['min'][0])
        min_y = max(hp['min'][1], lp['min'][1])
        min_z = max(hp['min'][2], lp['min'][2])
        max_x = min(hp['max'][0], lp['max'][0])
        max_y = min(hp['max'][1], lp['max'][1])
        max_z = min(hp['max'][2], lp['max'][2])

        if min_x < max_x and min_y < max_y and min_z < max_z:
            intersect_vol = (max_x - min_x) * (max_y - min_y) * (max_z - min_z)
            overlap_ratio = intersect_vol / max(min(hp_vol, lp_vol), 0.000001)
            if overlap_ratio > 0.02:
                return 1000.0 + overlap_ratio

        gap = self._bbox_gap(hp, lp)
        is_zb = self._is_zbrush_hp(hp)
        strict = self.geo_check_cb.isChecked()
        if strict and not is_zb:
            return None

        hp_diag = max(self._diag_from_data(hp), 0.000001)
        lp_diag = max(self._diag_from_data(lp), 0.000001)
        allowed_gap = max(min(hp_diag, lp_diag) * (1.5 if is_zb else 0.65), hp_diag * 0.35, 0.001)
        if gap <= allowed_gap:
            return 1.0 / (gap + 0.000001)
        return None

    # -------------------------------------------------------------------------
    # UI / labels
    # -------------------------------------------------------------------------
    def refresh_labels(self):
        pair = next((p for p in self.bg.root_pairs if p['id'] == self.bg.active_root_id), None)
        if pair:
            hp, lp, _ = self.bg.core.resolve_main_nodes(pair)
            self.lbl_hp.setText(hp.split('|')[-1] if hp else bg_l10n.text("Not Found"))
            self.lbl_lp.setText(lp.split('|')[-1] if lp else bg_l10n.text("Not Found"))
        else:
            self.lbl_hp.setText(bg_l10n.text("None"))
            self.lbl_lp.setText(bg_l10n.text("None"))
            self.result_list.clear()

    # -------------------------------------------------------------------------
    # LP shell extraction: combined LP is analysed as virtual disconnected shells
    # -------------------------------------------------------------------------
    def get_lp_shell_bbox_data(self, lp_node):
        try:
            sel = om.MSelectionList()
            sel.add(lp_node)
            dag = sel.getDagPath(0)
            if dag.hasFn(om.MFn.kTransform):
                dag.extendToShape()

            mesh_fn = om.MFnMesh(dag)
            points = mesh_fn.getPoints(om.MSpace.kWorld)
            counts, connects = mesh_fn.getVertices()

            face_vertices = []
            vertex_to_faces = {}
            idx = 0
            for face_id, count in enumerate(counts):
                verts = list(connects[idx:idx + count])
                idx += count
                face_vertices.append(verts)
                for v in verts:
                    vertex_to_faces.setdefault(v, []).append(face_id)

            visited_faces = set()
            shell_datas = []
            shell_index = 0

            for start_face in range(len(face_vertices)):
                if start_face in visited_faces:
                    continue
                stack = [start_face]
                visited_faces.add(start_face)
                shell_faces = []
                shell_verts = set()

                while stack:
                    f = stack.pop()
                    shell_faces.append(f)
                    for v in face_vertices[f]:
                        shell_verts.add(v)
                        for nf in vertex_to_faces.get(v, []):
                            if nf not in visited_faces:
                                visited_faces.add(nf)
                                stack.append(nf)

                if not shell_verts:
                    continue

                xs = [points[v].x for v in shell_verts]
                ys = [points[v].y for v in shell_verts]
                zs = [points[v].z for v in shell_verts]
                mn = [min(xs), min(ys), min(zs)]
                mx = [max(xs), max(ys), max(zs)]
                size = [mx[i] - mn[i] for i in range(3)]
                center = [(mn[i] + mx[i]) * 0.5 for i in range(3)]
                vol = max(size[0] * size[1] * size[2], 0.000001)
                sample_points = []
                if self.current_match_mode() != "FAST":
                    triangles = []
                    for face_id in shell_faces:
                        verts = face_vertices[face_id]
                        if len(verts) < 3:
                            continue
                        p0 = points[verts[0]]
                        for i in range(1, len(verts) - 1):
                            triangles.append((p0, points[verts[i]], points[verts[i + 1]]))
                    sample_points = self._sample_triangles(triangles, self._surface_sample_count("lp"))
                    if not sample_points:
                        sample_points = self._sample_points_evenly([points[v] for v in sorted(shell_verts)], self._surface_sample_count("lp"))

                shell_datas.append({
                    'node': '{}::shell_{:03d}'.format(lp_node, shell_index),
                    'real_node': lp_node,
                    'is_virtual_shell': True,
                    'min': mn,
                    'max': mx,
                    'bbox': [mn[0], mn[1], mn[2], mx[0], mx[1], mx[2]],
                    'size': size,
                    'center': center,
                    'diag': math.sqrt(size[0] * size[0] + size[1] * size[1] + size[2] * size[2]),
                    'verts': len(shell_verts),
                    'faces': len(shell_faces),
                    'vol': vol,
                    'sample_points': sample_points,
                })
                shell_index += 1

            return shell_datas
        except Exception as e:
            self.bg.log("LP shell extraction failed for {}: {}".format(lp_node.split('|')[-1], e), "orange")
            return []

    def _build_lp_data_list(self, lp_meshes, progress_cb=None):
        lp_data_list = []
        total = max(len(lp_meshes), 1)
        for index, lp in enumerate(lp_meshes):
            if progress_cb and progress_cb(index, total, lp):
                return None
            shell_datas = self.get_lp_shell_bbox_data(lp)
            if len(shell_datas) > 1:
                lp_data_list.extend(shell_datas)
                if progress_cb and progress_cb(index + 1, total, lp):
                    return None
                continue

            data = bg_core.MeshDataManager.get_bbox_data_gt(lp)
            if not data:
                if progress_cb and progress_cb(index + 1, total, lp):
                    return None
                continue
            try:
                verts = cmds.polyEvaluate(lp, vertex=True)
                faces = cmds.polyEvaluate(lp, face=True)
                data['verts'] = verts[0] if isinstance(verts, list) else verts
                data['faces'] = faces[0] if isinstance(faces, list) else faces
                data['vol'] = max(data['size'][0] * data['size'][1] * data['size'][2], 0.000001)
                data['real_node'] = lp
                data['is_virtual_shell'] = False
                data['diag'] = self._diag_from_data(data)
                data['bbox'] = [data['min'][0], data['min'][1], data['min'][2], data['max'][0], data['max'][1], data['max'][2]]
                if self.current_match_mode() != "FAST":
                    data['sample_points'] = self._mesh_surface_samples(lp, self._surface_sample_count("lp"))
                    data['intersector'] = self._mesh_intersector(lp)
            except Exception:
                data['verts'] = 0
                data['faces'] = 0
                data['vol'] = 0.000001
                data['real_node'] = lp
                data['is_virtual_shell'] = False
                data['diag'] = self._diag_from_data(data)
            lp_data_list.append(data)
            if progress_cb and progress_cb(index + 1, total, lp):
                return None
        return lp_data_list

    def process_batch_match(self):
        pair = next((p for p in self.bg.root_pairs if p['id'] == self.bg.active_root_id), None)
        if not pair:
            return self.bg.log("No active pair for GT batch match.", "orange")

        hp_root, lp_root, _ = self.bg.core.resolve_main_nodes(pair)
        if not hp_root or not lp_root:
            return self.bg.log("Active pair roots missing.", "red")

        hp_meshes = bg_core.MeshDataManager.get_meshes_in_group(hp_root)
        lp_meshes = bg_core.MeshDataManager.get_meshes_in_group(lp_root)
        if not hp_meshes or not lp_meshes:
            return self.bg.log("Error: HP or LP meshes missing in active pair.", "red")

        match_mode = self.current_match_mode()
        progress_dlg = self._create_match_progress(match_mode)
        if self._update_match_progress(progress_dlg, 1, "Collecting HP meshes..."):
            self._close_match_progress(progress_dlg)
            return self.bg.log("GT matching canceled.", "orange")
        self._gt_surface_sample_cache = {}
        self._gt_intersector_cache = {}
        self.cached_hp_data = []
        hp_total = max(len(hp_meshes), 1)
        for hp_index, hp in enumerate(hp_meshes):
            if self._update_match_progress(progress_dlg, 2 + int((hp_index / float(hp_total)) * 24), "Sampling HP: {}".format(hp.split('|')[-1])):
                self._close_match_progress(progress_dlg)
                return self.bg.log("GT matching canceled.", "orange")
            data = bg_core.MeshDataManager.get_bbox_data_gt(hp)
            if data:
                data['diag'] = self._diag_from_data(data)
                data['is_zbrush'] = self._is_zbrush_hp(data)
                if match_mode != "FAST":
                    data['sample_points'] = self._mesh_surface_samples(hp, self._surface_sample_count("hp"))
                    data['intersector'] = self._mesh_intersector(hp)
                self.cached_hp_data.append(data)

        def lp_progress(done, total, lp_node):
            value = 28 + int((done / float(max(total, 1))) * 24)
            return self._update_match_progress(progress_dlg, value, "Preparing LP: {}".format(lp_node.split('|')[-1]))

        lp_data_list = self._build_lp_data_list(lp_meshes, lp_progress if match_mode != "FAST" else None)
        if lp_data_list is None:
            self._close_match_progress(progress_dlg)
            return self.bg.log("GT matching canceled.", "orange")
        if not lp_data_list:
            self._close_match_progress(progress_dlg)
            return self.bg.log("GT: No valid LP data.", "red")

        tolerance_pct = self.tol_spin.value() / 100.0
        self.result_list.clear()

        # Step 1: HP -> virtual LP shell/LP object.
        lp_clusters = {lp['node']: [] for lp in lp_data_list}
        ambiguous_count = 0
        match_total = max(len(self.cached_hp_data) * len(lp_data_list), 1)
        match_done = 0
        match_stride = max(1, match_total // 120)
        for hp_index, hp in enumerate(self.cached_hp_data):
            best_lp = None
            best_score = None
            second_score = None
            for lp in lp_data_list:
                match_done += 1
                if match_done % match_stride == 0:
                    value = 54 + int((match_done / float(match_total)) * 34)
                    if self._update_match_progress(progress_dlg, value, "Matching HP to LP: {} / {}".format(hp_index + 1, len(self.cached_hp_data))):
                        self._close_match_progress(progress_dlg)
                        return self.bg.log("GT matching canceled.", "orange")
                if match_mode == "FAST":
                    score = self._match_score_hp_to_lp(hp, lp)
                else:
                    score = self._surface_match_score_hp_to_lp(hp, lp)
                if score is None:
                    continue
                if best_score is None or score > best_score:
                    second_score = best_score
                    best_score = score
                    best_lp = lp
                elif second_score is None or score > second_score:
                    second_score = score
            if match_mode == "ACCURATE" and best_score is not None and second_score is not None:
                if second_score > 0.0 and (best_score / second_score) < 1.08:
                    ambiguous_count += 1
                    continue
            if best_lp:
                lp_clusters[best_lp['node']].append(hp)

        if self._update_match_progress(progress_dlg, 90, "Building LP group proposals..."):
            self._close_match_progress(progress_dlg)
            return self.bg.log("GT matching canceled.", "orange")

        # Step 2: aggregate virtual shells back to real combined LP transforms.
        by_real_lp = {}
        for lp in lp_data_list:
            real = lp.get('real_node') or lp['node']
            grp = by_real_lp.setdefault(real, {
                'lp_nodes': [],
                'real_nodes': [real],
                'hp_nodes': set(),
                'verts': 0,
                'faces': 0,
                'vol': 0.0,
                'shell_count': 0,
            })
            grp['lp_nodes'].append(lp['node'])
            grp['verts'] += int(lp.get('verts', 0) or 0)
            grp['faces'] += int(lp.get('faces', 0) or 0)
            grp['vol'] += float(lp.get('vol', 0.0) or 0.0)
            grp['shell_count'] += 1
            grp['hp_nodes'].update(h['node'] for h in lp_clusters.get(lp['node'], []))

        raw_groups = [g for g in by_real_lp.values() if len(g['hp_nodes']) >= self.min_hp_spin.value()]

        # Step 3: merge repeated identical LP transforms, but not separate shells of the same mesh.
        grouped_results = []
        processed = set()
        for i, g1 in enumerate(raw_groups):
            key1 = tuple(g1['real_nodes'])
            if key1 in processed:
                continue
            current = {
                'lp_nodes': list(g1['lp_nodes']),
                'real_nodes': list(g1['real_nodes']),
                'hp_nodes': set(g1['hp_nodes']),
                'verts': g1['verts'],
                'faces': g1['faces'],
                'vol': g1['vol'],
                'shell_count': g1['shell_count'],
            }
            processed.add(key1)

            for j in range(i + 1, len(raw_groups)):
                g2 = raw_groups[j]
                key2 = tuple(g2['real_nodes'])
                if key2 in processed:
                    continue
                if g1['verts'] == g2['verts'] and g1['faces'] == g2['faces']:
                    vol_max = max(g1['vol'], g2['vol'], 0.000001)
                    vol_diff = abs(g1['vol'] - g2['vol']) / vol_max
                    if vol_diff <= tolerance_pct:
                        current['lp_nodes'].extend(g2['lp_nodes'])
                        current['real_nodes'].extend(g2['real_nodes'])
                        current['hp_nodes'].update(g2['hp_nodes'])
                        current['vol'] += g2['vol']
                        current['shell_count'] += g2['shell_count']
                        processed.add(key2)

            current['already_grouped'] = self._is_exact_cluster_already_grouped(current['hp_nodes'], hp_root)
            grouped_results.append(current)

        if not grouped_results and not (pair.get('custom_grouping') or {}):
            self._close_match_progress(progress_dlg)
            return self.bg.log("GT: No matches found.", "orange")

        if self._update_match_progress(progress_dlg, 94, "Updating matcher results..."):
            self._close_match_progress(progress_dlg)
            return self.bg.log("GT matching canceled.", "orange")

        sorted_groups = sorted(grouped_results, key=lambda x: x['vol'], reverse=True)
        displayed_custom_names = set()
        for grp in sorted_groups:
            real_names = [n.split('|')[-1] for n in grp['real_nodes']]
            shell_count = grp.get('shell_count', len(grp['lp_nodes']))
            hp_nodes = [p for p in grp['hp_nodes'] if cmds.objExists(p)]
            hp_uuid_set = self._hp_uuid_set_from_nodes(hp_nodes)
            existing_custom = self._find_custom_cluster_by_uuid_set(pair, hp_uuid_set)

            if len(real_names) == 1:
                title = "{} [Shells: {} | V: {} | HP: {}]".format(real_names[0], shell_count, grp['verts'], len(hp_nodes))
            else:
                title = "Group of {} identical LPs [Shells: {} | V: {} | HP: {}]".format(len(real_names), shell_count, grp['verts'], len(hp_nodes))
            if grp.get('already_grouped'):
                title += " [Already grouped]"

            proposal_name = real_names[0].replace(bg_core.BakeConfig.SUFFIX_LP, '')
            proposal_name = proposal_name.replace('::', '_').replace(' ', '_')
            if len(real_names) > 1:
                proposal_name += "_Group"

            base_name = existing_custom or proposal_name
            if existing_custom:
                displayed_custom_names.add(existing_custom)

            # If a user/GT link with this LP-derived name already exists, show that
            # saved override in the row instead of the old auto proposal. Otherwise
            # clicking a linked row keeps selecting the original script-found meshes.
            saved_nodes = []
            if base_name in (pair.get('custom_grouping') or {}):
                uuid_map = self._uuid_to_transform_map(hp_root)
                saved_nodes = self._nodes_from_uuid_list(pair['custom_grouping'].get(base_name), hp_root, uuid_map)

            display_nodes = saved_nodes if saved_nodes else hp_nodes
            mapping = {base_name: display_nodes}
            proposal_mapping = {proposal_name: hp_nodes}

            item = QtWidgets.QListWidgetItem(title)
            item.setData(QtCore.Qt.UserRole, mapping)
            item.setData(QtCore.Qt.UserRole + 1, proposal_mapping)
            item.setToolTip(
                "Real LPs:\n{}\n\nVirtual LP shells:\n{}\n\nAuto proposal HPs: {}\nSaved linked HPs: {}".format(
                    "\n".join(real_names),
                    "\n".join([n.split('|')[-1].replace('::', ' ') for n in grp['lp_nodes']]),
                    len(hp_nodes),
                    len(saved_nodes) if saved_nodes else 0
                )
            )
            if saved_nodes or existing_custom:
                self._mark_item_linked(item, base_name, display_nodes)
            self.result_list.addItem(item)

        uuid_map = None
        saved_only_count = 0
        for cluster_name, uuid_list in sorted((pair.get('custom_grouping') or {}).items()):
            if cluster_name in displayed_custom_names:
                continue
            if uuid_map is None:
                uuid_map = self._uuid_to_transform_map(hp_root)
            saved_nodes = self._nodes_from_uuid_list(uuid_list, hp_root, uuid_map)
            if not saved_nodes:
                continue
            item = QtWidgets.QListWidgetItem("[Saved] {} [HP: {}]".format(cluster_name, len(saved_nodes)))
            item.setData(QtCore.Qt.UserRole, {cluster_name: saved_nodes})
            item.setData(QtCore.Qt.UserRole + 1, {})
            item.setToolTip(
                "Saved HP-LP Matcher/manual link.\n"
                "This link did not match a current auto proposal, but it is still saved and will affect Analyze HP."
            )
            self._mark_item_linked(item, cluster_name, saved_nodes)
            self.result_list.addItem(item)
            saved_only_count += 1

        self._update_match_progress(progress_dlg, 100, "Matching complete.")
        self._close_match_progress(progress_dlg)

        if saved_only_count:
            self.bg.log("GT: Found {} LP group proposal(s) and {} saved link(s).".format(len(grouped_results), saved_only_count), "lightgreen")
        else:
            self.bg.log("GT: Found {} LP group proposal(s).".format(len(grouped_results)), "lightgreen")
        if match_mode != "FAST":
            sample_label = "Balanced" if match_mode == "BALANCED" else "Accurate"
            self.bg.log("GT {} surface matching: HP={} LP shells={} ambiguous skipped={}.".format(
                sample_label,
                len(self.cached_hp_data),
                len(lp_data_list),
                ambiguous_count
            ), "lightblue")

    # -------------------------------------------------------------------------
    # Relocate and linking
    # -------------------------------------------------------------------------
    def relocate_hp(self):
        pair = next((p for p in self.bg.root_pairs if p['id'] == self.bg.active_root_id), None)
        if not pair:
            return self.bg.log("No active pair for relocation.", "orange")

        custom_grouping = pair.get('custom_grouping', {})
        if not custom_grouping:
            return self.bg.log("No custom linked clusters found. Link them first.", "orange")

        hp_main, _, _ = self.bg.core.resolve_main_nodes(pair)
        if not hp_main:
            return self.bg.log("HP root not found.", "red")

        uuid_map = self._uuid_to_transform_map(hp_main)

        def existing_hp_subgroup_for_mesh(mesh):
            current = self._long(mesh)
            hp_root = self._long(hp_main)
            while current and cmds.objExists(current):
                parent = cmds.listRelatives(current, parent=True, fullPath=True) or []
                if not parent:
                    return None
                parent_node = self._long(parent[0])
                if parent_node == hp_root:
                    return None
                if cmds.listRelatives(parent_node, shapes=True, type='mesh', fullPath=True):
                    current = parent_node
                    continue
                if self._is_under(parent_node, hp_root):
                    return parent_node
                current = parent_node
            return None

        def mesh_volume(mesh):
            try:
                data = bg_core.MeshDataManager.get_bbox_data_gt(mesh)
                if data and data.get('size'):
                    size = data['size']
                    return max(float(size[0]) * float(size[1]) * float(size[2]), 0.0)
            except Exception:
                pass
            return 0.0

        def find_existing_target_group(cluster_meshes):
            buckets = {}
            for mesh in cluster_meshes:
                subgroup = existing_hp_subgroup_for_mesh(mesh)
                if not subgroup:
                    continue
                bucket = buckets.setdefault(subgroup, {'count': 0, 'volume': 0.0})
                bucket['count'] += 1
                bucket['volume'] += mesh_volume(mesh)

            if not buckets:
                return None

            return sorted(
                buckets.items(),
                key=lambda item: (item[1]['count'], item[1]['volume']),
                reverse=True
            )[0][0]

        corrections_made = 0
        empty_links = []
        skipped_no_target = []

        with bg_core.undo_chunk("GT_RelocateHP"):
            for cluster_name, cluster_uuids in custom_grouping.items():
                cluster_meshes = []
                for uid in cluster_uuids or []:
                    node = self._resolve_uuid_to_node(uid, uuid_map)
                    if node and cmds.objExists(node) and self._is_under(node, hp_main):
                        cluster_meshes.append(node)

                # Deduplicate after UUID resolution.
                cluster_meshes = list(dict.fromkeys(cluster_meshes))
                if not cluster_meshes:
                    empty_links.append(cluster_name)
                    continue

                target_group = find_existing_target_group(cluster_meshes)
                if not target_group:
                    skipped_no_target.append(cluster_name)
                    continue

                for m in cluster_meshes:
                    # Resolve again in case a previous parent operation changed the DAG path.
                    uid = self._safe_uuid(m)
                    current = self._resolve_uuid_to_node(uid, uuid_map) if uid else self._long(m)
                    if not current or not cmds.objExists(current):
                        continue
                    current_parent = cmds.listRelatives(current, parent=True, fullPath=True) or []
                    if current_parent and current_parent[0] == target_group:
                        continue
                    try:
                        cmds.parent(current, target_group, absolute=True)
                        corrections_made += 1
                    except Exception as e:
                        self.bg.log("Relocate skip for {}: {}".format(current.split('|')[-1], e), "orange")

        if corrections_made > 0:
            self.bg.log("Relocated {} HP mesh(es) from GT/manual links.".format(corrections_made), "lightgreen")
        else:
            self.bg.log("No HP meshes needed relocation; GT/manual links were checked.", "lightblue")

        if empty_links:
            self.bg.log("Relocate warning: {} link(s) contain UUIDs not found under active HP root: {}".format(len(empty_links), ", ".join(empty_links[:5])), "orange")

        if skipped_no_target:
            self.bg.log("Relocate skipped {} cluster(s): no existing HP subgroup target found. Relocate now moves meshes only into existing subgroups.".format(len(skipped_no_target)), "orange")

        if hasattr(self.bg, 'refresh_left_panel'):
            self.bg.refresh_left_panel()

    def on_lp_selected(self):
        items = self.result_list.selectedItems()
        if not items:
            cmds.select(clear=True)
            return
        hp_to_select = []
        for item in items:
            mapping = item.data(QtCore.Qt.UserRole)
            if mapping:
                for hp_paths in mapping.values():
                    hp_to_select.extend([p for p in hp_paths if cmds.objExists(p)])
        if hp_to_select:
            cmds.select(list(dict.fromkeys(hp_to_select)), replace=True)
        else:
            cmds.select(clear=True)

    def _next_new_group_name(self, pair):
        existing = set((pair.get('custom_grouping') or {}).keys())
        idx = 1
        while True:
            name = "new_group_{:03d}".format(idx)
            if name not in existing:
                return name
            idx += 1

    def create_empty_cluster(self):
        pair = next((p for p in self.bg.root_pairs if p['id'] == self.bg.active_root_id), None)
        if not pair:
            return
        if 'custom_grouping' not in pair:
            pair['custom_grouping'] = {}

        hp_main, _, _ = self.bg.core.resolve_main_nodes(pair)
        if not hp_main:
            return self.bg.log("HP root not found for new GT cluster.", "red")

        name = self._next_new_group_name(pair)
        selected_hps = self._selection_to_hp_meshes(hp_main)
        selected_uuids = self._uuid_list_from_nodes(selected_hps)

        # New means: create an auto-named cluster. If HP meshes are selected,
        # immediately bind those meshes without requiring a separate Link click.
        pair['custom_grouping'][name] = list(selected_uuids)
        if not self._save_session(pair):
            return

        # Surface the newly created cluster in the matcher list so clicking it later
        # selects exactly the user-bound meshes, not the old script proposal.
        item = QtWidgets.QListWidgetItem("[Linked] {} [Manual: {} HP]".format(name, len(selected_hps)))
        item.setData(QtCore.Qt.UserRole, {name: list(selected_hps)})
        item.setBackground(QtGui.QColor("#2e7d32"))
        item.setForeground(QtGui.QColor("white"))
        if selected_hps:
            item.setToolTip("Manual HPs:\n" + "\n".join([m.split('|')[-1] for m in selected_hps]))
        else:
            item.setToolTip("Empty manual cluster. Use Link later to replace it with selected HP meshes.")
        self.result_list.addItem(item)
        self.result_list.setCurrentItem(item)

        if selected_hps:
            self.bg.log("Created '{}' and linked {} selected HP mesh(es).".format(name, len(selected_hps)), "lightgreen")
        else:
            self.bg.log("Created empty cluster: {}".format(name), "lightgreen")

    def link_selected(self):
        pair = next((p for p in self.bg.root_pairs if p['id'] == self.bg.active_root_id), None)
        if not pair:
            return
        if 'custom_grouping' not in pair:
            pair['custom_grouping'] = {}

        hp_main, _, _ = self.bg.core.resolve_main_nodes(pair)
        if not hp_main:
            return self.bg.log("HP root not found for GT link.", "red")

        items = self.result_list.selectedItems()
        selected_hps = self._selection_to_hp_meshes(hp_main)
        selected_uuids = self._uuid_list_from_nodes(selected_hps)
        selected_uuid_set = set(selected_uuids)

        # Scenario 1: a GT row is selected.
        # Important: selecting a row also selects the script proposal in Maya.
        # If the user then manually reselects another HP set in the viewport, the
        # QListWidget row is still selected. Therefore Link must detect that the
        # viewport selection differs and replace the row cluster with the user's set.
        if items:
            first_item = items[0]
            first_mapping = first_item.data(QtCore.Qt.UserRole) or {}
            first_cluster_name = next(iter(first_mapping.keys()), None)
            first_item_nodes = []
            for paths in first_mapping.values():
                first_item_nodes.extend([p for p in paths if cmds.objExists(p)])
            first_item_uuid_set = set(self._uuid_list_from_nodes(first_item_nodes))

            if selected_uuid_set and first_cluster_name and selected_uuid_set != first_item_uuid_set:
                # Manual override of the selected GT row. Replace, do not append.
                pair['custom_grouping'][first_cluster_name] = list(selected_uuids)
                self._mark_item_linked(first_item, first_cluster_name, selected_hps)
                self._save_session(pair)
                self.bg.log(
                    "Manual override: '{}' now uses {} user-selected HP mesh(es).".format(
                        first_cluster_name, len(selected_uuids)
                    ),
                    "lightgreen"
                )
                return

            # Otherwise accept the selected GT row proposal as-is.
            linked_count = 0
            group_count = 0
            for item in items:
                mapping = item.data(QtCore.Qt.UserRole)
                if not mapping:
                    continue
                for sg_name, hp_paths in mapping.items():
                    valid_uuids = self._uuid_list_from_nodes([p for p in hp_paths if cmds.objExists(p)])
                    if not valid_uuids:
                        continue
                    pair['custom_grouping'][sg_name] = valid_uuids
                    self._mark_item_linked(item, sg_name, hp_paths)
                    linked_count += len(valid_uuids)
                    group_count += 1

            if group_count:
                self._save_session(pair)
                self.bg.log("Linked {} group(s), {} HP mesh UUID(s).".format(group_count, linked_count), "lightgreen")
            else:
                self.bg.log("Selected GT item has no valid HP meshes to link.", "red")
            return

        # Scenario 2: no GT row selected — create/replace a cluster from viewport selection.
        if not selected_hps:
            return self.bg.log(
                "No valid HP meshes selected in viewport. Select HP transforms, shapes, components, or a group under active HP root.",
                "red"
            )

        if not selected_uuids:
            return self.bg.log("Could not resolve UUIDs for selected HP meshes.", "red")

        existing_clusters = sorted(pair.get('custom_grouping', {}).keys())
        create_new_label = bg_l10n.text("<Create new Custom_Link>")
        if existing_clusters:
            choice, ok = QtWidgets.QInputDialog.getItem(
                self,
                bg_l10n.text("Manual Cluster Target"),
                bg_l10n.text("Choose cluster to REPLACE with current viewport selection:"),
                existing_clusters + [create_new_label],
                0,
                False
            )
            if not ok:
                return
            sg_name = choice if choice and choice != create_new_label else None
        else:
            sg_name = None

        if not sg_name:
            new_idx = 1
            while "Custom_Link_{:02d}".format(new_idx) in pair['custom_grouping']:
                new_idx += 1
            sg_name = "Custom_Link_{:02d}".format(new_idx)

        # Replace, do not merge. Link means: this cluster is exactly my current selection.
        pair['custom_grouping'][sg_name] = list(selected_uuids)
        self._save_session(pair)
        self.bg.log("Manual link '{}' set to {} selected HP mesh(es).".format(sg_name, len(selected_uuids)), "lightgreen")

    def unlink_selected(self):
        pair = next((p for p in self.bg.root_pairs if p['id'] == self.bg.active_root_id), None)
        if not pair or 'custom_grouping' not in pair:
            return

        items = self.result_list.selectedItems()
        if not items:
            existing_clusters = sorted(pair.get('custom_grouping', {}).keys())
            if not existing_clusters:
                return self.bg.log("No links to unlink.", "orange")
            choice, ok = QtWidgets.QInputDialog.getItem(self, bg_l10n.text("Unlink Cluster"), bg_l10n.text("Choose cluster to unlink:"), existing_clusters, 0, False)
            if not ok or not choice:
                return
            del pair['custom_grouping'][choice]
            self._save_session(pair)
            self.bg.log("Unlinked cluster: {}".format(choice), "lightgreen")
            return

        unlinked = 0
        for item in items:
            mapping = item.data(QtCore.Qt.UserRole)
            if mapping:
                for sg_name in mapping.keys():
                    if sg_name in pair['custom_grouping']:
                        del pair['custom_grouping'][sg_name]
                        unlinked += 1
                item.setBackground(QtGui.QColor(255, 255, 255, 0))
                item.setForeground(QtGui.QColor("#E0E0E0"))
                item.setText(item.text().replace("[Linked] ", ""))

        self.result_list.clearSelection()
        self._save_session(pair)
        self.bg.log("Unlinked {} group(s). Re-run Analyze HP or Relocate HP to update structure.".format(unlinked), "lightgreen")
