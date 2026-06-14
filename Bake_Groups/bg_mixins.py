# -*- coding: utf-8 -*-
from __future__ import print_function, division, absolute_import

import sys

if sys.version_info[0] >= 3:
    from importlib import reload

modules_to_reload = [
    'bg_core',
    'bg_worker_hp',
    'bg_worker_lp',
    'bg_gt_matcher',
    'bg_final_export',
    'bg_localization'
]

for mod_name in modules_to_reload:
    if mod_name in sys.modules:
        try:
            reload(sys.modules[mod_name])
            print("bg_mixins: reloaded {}".format(mod_name))
        except Exception as e:
            print("bg_mixins: failed to reload {}: {}".format(mod_name, e))
    else:
        print("bg_mixins: {} not loaded yet".format(mod_name))

import bg_core
import bg_worker_hp
from bg_worker_hp import HPGroupingWorker
import bg_worker_lp
import maya.api.OpenMaya as om
from bg_worker_lp import LPMatchingWorker
import bg_gt_matcher
import bg_final_export
import bg_localization as bg_l10n

try:
    import bg_math_core
    HAS_MATH_CORE = True
except ImportError:
    print("WARNING: bg_math_core (.pyd) not found! High-poly matching will use slow path.")
    HAS_MATH_CORE = False

# Maya 2020-2026 PySide Compatibility Layer
try:
    from PySide6 import QtWidgets, QtCore, QtGui
    QAction = QtGui.QAction
except ImportError:
    from PySide2 import QtWidgets, QtCore, QtGui
    QAction = QtWidgets.QAction

import maya.cmds as cmds
import maya.api.OpenMaya as om
import uuid
import re
import math
import os
import contextlib
from bg_ui_widgets import SubgroupButton


# ============================================================================
# HP ANALYSIS MIXIN
# ============================================================================
class HPAnalysisMixin:
    """Methods for High-poly analysis and auto-grouping."""

    def gather_hp_worker_params(self):
        return {
            'threshold_pct': self.spin_collision_pct.value(),
            'strategy': self.combo_hp_strategy.currentIndex(),
            'use_symmetry': self.chk_use_symmetry.isChecked(),
            'compound_link_verts': self.spin_compound_link_verts.value(),
            'compound_link_dist_pct': self.spin_compound_link_dist.value(),
            'ignore_floaters': self.chk_ignore_floaters.isChecked()
        }

    def _is_in_zbrush_display_layer(self, mesh_transform):
        if not mesh_transform or not cmds.objExists(mesh_transform):
            return False

        nodes_to_check = [mesh_transform]
        nodes_to_check.extend(cmds.listRelatives(mesh_transform, shapes=True, fullPath=True, type='mesh') or [])

        parent = cmds.listRelatives(mesh_transform, parent=True, fullPath=True)
        while parent:
            nodes_to_check.append(parent[0])
            parent = cmds.listRelatives(parent[0], parent=True, fullPath=True)

        for node in nodes_to_check:
            for layer in (cmds.listConnections(node, type="displayLayer") or []):
                if layer and "zbrush" in layer.lower():
                    return True
        return False

    def _find_zbrush_candidates_outside_layer(self, hp_main):
        shapes = cmds.listRelatives(hp_main, allDescendents=True, fullPath=True, type='mesh') or []
        valid_shapes = [s for s in shapes if not cmds.getAttr(s + ".intermediateObject")]
        hp_meshes = sorted(set(cmds.listRelatives(s, parent=True, fullPath=True)[0] for s in valid_shapes))

        threshold_pct = int(getattr(self, 'zbrush_triangle_threshold', 50))
        candidates = []
        for mesh in hp_meshes:
            ratio, triangle_faces, total_faces = self._get_triangle_face_ratio(mesh)
            if total_faces > 0 and ratio >= float(threshold_pct) and not self._is_in_zbrush_display_layer(mesh):
                candidates.append((mesh, ratio, triangle_faces, total_faces))
        return candidates

    def _confirm_zbrush_candidates_before_hp_analysis(self, hp_main):
        candidates = self._find_zbrush_candidates_outside_layer(hp_main)
        if not candidates:
            return True

        threshold_pct = int(getattr(self, 'zbrush_triangle_threshold', 50))
        preview_names = [item[0].split('|')[-1] for item in candidates[:12]]
        if len(candidates) > 12:
            preview_names.append("... and {} more".format(len(candidates) - 12))

        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(bg_l10n.text("Possible ZBrush Meshes"))
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setText(
            "Found {} HP mesh(es) with {}%+ triangular faces that are not in a ZBrush display layer.".format(
                len(candidates), threshold_pct
            )
        )
        box.setInformativeText(
            "These meshes may be processed as regular HP unless you place them into the ZBrush layer.\n\n{}".format(
                "\n".join(preview_names)
            )
        )
        select_btn = box.addButton(bg_l10n.text("Select"), QtWidgets.QMessageBox.ActionRole)
        skip_btn = box.addButton(bg_l10n.text("Skip"), QtWidgets.QMessageBox.AcceptRole)
        bg_l10n.localize_widget_tree(box)
        box.setDefaultButton(select_btn)
        box.exec_()

        if box.clickedButton() == select_btn:
            meshes = [item[0] for item in candidates if cmds.objExists(item[0])]
            if meshes:
                cmds.select(meshes, replace=True)
                self.log("Analyze HP paused: selected {} possible ZBrush mesh(es).".format(len(meshes)), "orange")
            return False

        return box.clickedButton() == skip_btn

    def run_hp_analysis(self, _):
        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not pair:
            return
        self.last_debug_lines = []

        hp_main, lp_main, _ = self.core.resolve_main_nodes(pair)
        if not hp_main:
            cmds.warning("HighPoly root not found.")
            return

        if not self.validate_frozen_transforms([hp_main], [hp_main], bg_l10n.text("Analyze HP")):
            return

        if not self._confirm_zbrush_candidates_before_hp_analysis(hp_main):
            return

        worker_params = self.gather_hp_worker_params()
        threshold_pct = worker_params['threshold_pct']
        group_limit = int(getattr(self, 'hp_group_limit', 12))
        locked = pair.get('locked', [])
        # GT Matcher/manual links must be preserved exactly.
        # Previous code deleted any custom cluster whose name contained _lp/_low;
        # that made valid GT links look like they were not saved.
        custom_clusters = {}
        for _name, _uuids in (pair.get('custom_grouping', {}) or {}).items():
            _clean = []
            for _uid in (_uuids or []):
                if _uid and _uid not in _clean:
                    _clean.append(_uid)
            if _clean:
                custom_clusters[_name] = _clean
        pair['custom_grouping'] = custom_clusters

        locked_subgroups = pair.get('locked', [])

        # If keeping HP structure, just use existing subgroups
        if self.cb_keep_hp_structure.isChecked():
            self.hp_data_cache.clear()
            groups_found = False
            for child in (cmds.listRelatives(hp_main, children=True, fullPath=True, type='transform') or []):
                if not cmds.listRelatives(child, shapes=True):
                    grp_name = child.split('|')[-1]
                    if not grp_name.endswith(bg_core.BakeConfig.SUFFIX_HP):
                        child = cmds.rename(child, "{}{}".format(grp_name, bg_core.BakeConfig.SUFFIX_HP))
                    if not cmds.objExists(child + "." + bg_core.BakeConfig.ATTR_BAKE_GROUP):
                        cmds.addAttr(child, ln=bg_core.BakeConfig.ATTR_BAKE_GROUP, dt="string")
                        cmds.setAttr("{}.{}".format(child, bg_core.BakeConfig.ATTR_BAKE_GROUP), "HP", type="string")

                    sub_meshes = self.prepare_meshes(child, flatten=True)
                    if sub_meshes:
                        groups_found = True
                        for m in sub_meshes:
                            data = bg_core.MeshDataManager.get_mesh_data(m)
                            if data:
                                self.hp_data_cache[m] = data

            if not groups_found:
                self.log("No existing HP subgroups found to keep.", "orange")
            else:
                self.refresh_left_panel()
                if hasattr(self, 'refresh_subgroup_color_preview'):
                    self.refresh_subgroup_color_preview(reset_indices=True)
                self.log("Kept existing HP subgroup structure.", "lightblue")
            return

        # Flatten and prepare
        with bg_core.undo_chunk("PrepareHPAnalysis"):
            all_layers = cmds.ls(type="displayLayer")
            zb_layers = [l for l in all_layers if "zbrush" in l.lower()]
            for layer in zb_layers:
                members = cmds.editDisplayLayerMembers(layer, query=True, fullNames=True) or []
                for member in members:
                    if cmds.objExists(member):
                        internal_meshes = cmds.listRelatives(member, allDescendents=True, type='mesh', fullPath=True) or []
                        if internal_meshes:
                            mesh_transforms = list(set([cmds.listRelatives(m, parent=True, fullPath=True)[0] for m in internal_meshes]))
                            cmds.editDisplayLayerMembers(layer, mesh_transforms, noRecurse=True)

            children = cmds.listRelatives(hp_main, children=True, fullPath=True, type='transform') or []
            for child in children:
                if not cmds.objExists(child):
                    continue
                match = re.search(r'(_HP|_LP)(\d*)$', child.split('|')[-1])
                grp_ui_name = child.split('|')[-1][:match.start()] + match.group(2) if match else child.split('|')[-1]
                is_subgroup = not cmds.listRelatives(child, shapes=True)

                if is_subgroup and grp_ui_name not in locked_subgroups:
                    sub_transforms = cmds.listRelatives(child, allDescendents=True, type='transform', fullPath=True) or []
                    for t in sub_transforms:
                        if cmds.listRelatives(t, shapes=True, type='mesh'):
                            current_p = cmds.listRelatives(t, parent=True, fullPath=True)
                            if current_p and current_p[0] != hp_main:
                                try:
                                    cmds.parent(t, hp_main, absolute=True)
                                except:
                                    pass
                    try:
                        temp_name = cmds.rename(child, "DELETE_ME_" + str(uuid.uuid4())[:8])
                        cmds.delete(temp_name)
                    except:
                        pass

            final_meshes = []
            current_children = cmds.listRelatives(hp_main, children=True, fullPath=True, type='transform') or []
            for child in current_children:
                if not cmds.objExists(child):
                    continue
                if not (not cmds.listRelatives(child, shapes=True)):
                    try:
                        final_meshes.append(cmds.ls(child, long=True)[0])
                    except:
                        pass

        final_meshes = [m for m in final_meshes if not m.split('|')[-1].endswith(bg_core.BakeConfig.SUFFIX_LP)]
        final_meshes = [m for m in final_meshes if not re.search(r'(_lp|_low)$', m.split('|')[-1], re.IGNORECASE)]

        if not final_meshes:
            return self.log("No unlocked HP meshes found to analyze.", "red")

        self.hp_data_cache.clear()

        def is_mesh_zbrush_main_thread(name, data):
            if data and data.get("is_zbrush"):
                return True
            if "_zbrush" in name.lower():
                return True
            if cmds.objExists(name):
                layers = cmds.listConnections(name, type="displayLayer") or []
                for l in layers:
                    if "zbrush" in l.lower():
                        return True
                shapes = cmds.listRelatives(name, shapes=True, fullPath=True) or []
                for s in shapes:
                    layers = cmds.listConnections(s, type="displayLayer") or []
                    for l in layers:
                        if "zbrush" in l.lower():
                            return True
            return False

        for m in final_meshes:
            data = bg_core.MeshDataManager.get_mesh_data(m)
            if data:
                current_name = m
                is_zb = is_mesh_zbrush_main_thread(current_name, data)
                data["is_zbrush"] = is_zb

                if is_zb and not current_name.lower().endswith("_zbrush"):
                    if cmds.objExists(current_name):
                        try:
                            short_name = current_name.split('|')[-1]
                            new_short = cmds.rename(current_name, short_name + "_Zbrush")
                            current_name = cmds.ls(new_short, long=True)[0]
                        except Exception as e:
                            self.log("Failed to rename {}: {}".format(m, e), "red")

                data["name"] = current_name

                if cmds.objExists(current_name):
                    bbox = cmds.xform(current_name, q=True, ws=True, bb=True)
                    data["bbox"] = bbox
                    dx = abs(bbox[3] - bbox[0])
                    dy = abs(bbox[4] - bbox[1])
                    dz = abs(bbox[5] - bbox[2])
                    data["radius"] = math.sqrt((dx/2)**2 + (dy/2)**2 + (dz/2)**2)
                    data["bbox_vol"] = dx * dy * dz
                else:
                    data["bbox"] = [-1, -1, -1, 1, 1, 1]
                    data["radius"] = 1.0
                    data["bbox_vol"] = 8.0

                try:
                    data['uuid'] = cmds.ls(current_name, uuid=True)[0]
                except Exception:
                    pass

                self.hp_data_cache[current_name] = data

        if not self.hp_data_cache:
            return self.log("No valid HP mesh data.", "red")

        custom_mapping = pair.get('custom_grouping', {})
        if custom_mapping:
            self.log("Loaded {} custom cluster(s) from GT / manual links.".format(len(custom_mapping)), "lightblue")

        final_lp_meshes = self.prepare_meshes(lp_main, flatten=True)
        final_lp_meshes = [m for m in final_lp_meshes if not m.split('|')[-1].endswith(bg_core.BakeConfig.SUFFIX_HP)]

        self.lp_data_cache.clear()
        lp_prebuilt_verts_cache = {}

        def build_virtual_lp_shells_for_hp_worker(lp_node):
            """Create analysis-only LP shell records for combined LP meshes.
            The Maya scene is not split; only worker cache gets virtual shell keys.
            """
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
                shells = []
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
                    bbox_vol = max(size[0] * size[1] * size[2], 1e-6)
                    diag = math.sqrt(size[0] * size[0] + size[1] * size[1] + size[2] * size[2])

                    shell_key = "{}::shell_{:03d}".format(lp_node, shell_index)
                    verts_flat = []
                    for v in shell_verts:
                        pnt = points[v]
                        verts_flat.extend([pnt.x, pnt.y, pnt.z])

                    shells.append((shell_key, {
                        "name": shell_key,
                        "node": shell_key,
                        "real_node": lp_node,
                        "is_virtual_lp_shell": True,
                        "min": mn,
                        "max": mx,
                        "bbox": [mn[0], mn[1], mn[2], mx[0], mx[1], mx[2]],
                        "size": size,
                        "center": center,
                        "diag": diag,
                        "radius": diag * 0.5,
                        "bbox_vol": bbox_vol,
                        "vtx": len(shell_verts),
                        "edges": 0,
                        "faces": len(shell_faces),
                        "hash": "empty",
                        "uv_count": 0,
                        "uv_shell_count": 0,
                        "uv_signature": "empty",
                        "variance": 999.0,
                    }, verts_flat))
                    shell_index += 1

                return shells
            except Exception as e:
                self.log("LP shell cache failed for {}: {}".format(lp_node.split('|')[-1], e), "orange")
                return []

        for m in final_lp_meshes:
            shell_records = build_virtual_lp_shells_for_hp_worker(m)
            if len(shell_records) > 1:
                for shell_key, shell_data, shell_verts in shell_records:
                    self.lp_data_cache[shell_key] = shell_data
                    lp_prebuilt_verts_cache[shell_key] = shell_verts
                continue

            data = bg_core.MeshDataManager.get_mesh_data(m)
            if data:
                if cmds.objExists(m):
                    data["bbox"] = cmds.xform(m, q=True, ws=True, bb=True)
                self.lp_data_cache[m] = data

        # Progress dialog
        self.progress_dlg = QtWidgets.QProgressDialog(bg_l10n.text("Extracting Data & Fingerprinting..."), bg_l10n.text("Cancel"), 0, 100, self)
        self.progress_dlg.setWindowModality(QtCore.Qt.WindowModal)
        self.progress_dlg.setMinimumDuration(0)

        hp_verts_cache = {}
        lp_verts_cache = {}
        hp_holes_cache = {}

        def build_hp_hole_cache_entry(mesh_path, mesh_center=None):
            """Collect open-border points and classify border normals.

            The worker still accepts the old flat-list format, but the dict gives
            it enough context to tell a real open part from a decal/floater back.
            Orientation is measured against the approximate mesh interior:
            positive dot means adjacent polygon normals point toward the mesh.
            """
            try:
                sel = om.MSelectionList()
                sel.add(mesh_path)
                dag_path = sel.getDagPath(0)
                if dag_path.hasFn(om.MFn.kTransform):
                    dag_path.extendToShape()

                mesh_fn = om.MFnMesh(dag_path)

                def _as_vec(value):
                    try:
                        return om.MVector(float(value[0]), float(value[1]), float(value[2]))
                    except Exception:
                        return None

                center_vec = _as_vec(mesh_center)
                if center_vec is None:
                    points = mesh_fn.getPoints(om.MSpace.kWorld)
                    if len(points) > 0:
                        acc = om.MVector()
                        for point in points:
                            acc += om.MVector(point.x, point.y, point.z)
                        center_vec = acc / float(len(points))
                    else:
                        center_vec = om.MVector()

                boundary_verts = []
                boundary_edges = []
                vertex_to_edges = {}

                edge_iter = om.MItMeshEdge(dag_path)
                while not edge_iter.isDone():
                    if edge_iter.onBoundary():
                        p0 = edge_iter.point(0, om.MSpace.kWorld)
                        p1 = edge_iter.point(1, om.MSpace.kWorld)
                        v0 = int(edge_iter.vertexId(0))
                        v1 = int(edge_iter.vertexId(1))
                        try:
                            faces = [int(face_id) for face_id in edge_iter.getConnectedFaces()]
                        except Exception:
                            faces = []

                        edge_id = len(boundary_edges)
                        boundary_edges.append({
                            "v0": v0,
                            "v1": v1,
                            "p0": om.MVector(p0.x, p0.y, p0.z),
                            "p1": om.MVector(p1.x, p1.y, p1.z),
                            "faces": faces
                        })
                        vertex_to_edges.setdefault(v0, []).append(edge_id)
                        vertex_to_edges.setdefault(v1, []).append(edge_id)
                        boundary_verts.extend([p0.x, p0.y, p0.z, p1.x, p1.y, p1.z])
                    edge_iter.next()

                if not boundary_edges:
                    return None

                visited = set()
                loops = []
                for start_edge in range(len(boundary_edges)):
                    if start_edge in visited:
                        continue
                    stack = [start_edge]
                    component = []
                    visited.add(start_edge)
                    while stack:
                        edge_id = stack.pop()
                        component.append(edge_id)
                        edge = boundary_edges[edge_id]
                        for vertex_id in (edge["v0"], edge["v1"]):
                            for linked_edge in vertex_to_edges.get(vertex_id, []):
                                if linked_edge not in visited:
                                    visited.add(linked_edge)
                                    stack.append(linked_edge)

                    unique_points = {}
                    loop_points = []
                    for edge_id in component:
                        edge = boundary_edges[edge_id]
                        unique_points[edge["v0"]] = edge["p0"]
                        unique_points[edge["v1"]] = edge["p1"]
                        loop_points.extend([
                            edge["p0"].x, edge["p0"].y, edge["p0"].z,
                            edge["p1"].x, edge["p1"].y, edge["p1"].z
                        ])

                    loop_center = om.MVector()
                    for point in unique_points.values():
                        loop_center += point
                    if unique_points:
                        loop_center /= float(len(unique_points))

                    weighted_dot = 0.0
                    weight_sum = 0.0
                    samples = 0
                    for edge_id in component:
                        edge = boundary_edges[edge_id]
                        edge_vec = edge["p1"] - edge["p0"]
                        edge_len = max(edge_vec.length(), 0.000001)
                        edge_center = (edge["p0"] + edge["p1"]) * 0.5
                        inside_vec = center_vec - edge_center
                        inside_len = inside_vec.length()
                        if inside_len <= 0.000001:
                            continue
                        inside_vec /= inside_len

                        for face_id in edge["faces"]:
                            try:
                                normal = mesh_fn.getPolygonNormal(face_id, om.MSpace.kWorld)
                            except Exception:
                                continue
                            normal_vec = om.MVector(normal.x, normal.y, normal.z)
                            normal_len = normal_vec.length()
                            if normal_len <= 0.000001:
                                continue
                            normal_vec /= normal_len
                            weighted_dot += float(normal_vec * inside_vec) * edge_len
                            weight_sum += edge_len
                            samples += 1

                    avg_dot = weighted_dot / weight_sum if weight_sum > 0.0 else 0.0
                    if samples < 2:
                        orientation = "unknown"
                    elif avg_dot >= 0.18:
                        orientation = "inward"
                    elif avg_dot <= -0.18:
                        orientation = "outward"
                    else:
                        orientation = "unknown"

                    loops.append({
                        "points": loop_points,
                        "center": (loop_center.x, loop_center.y, loop_center.z),
                        "orientation": orientation,
                        "score": avg_dot,
                        "samples": samples,
                        "edge_count": len(component)
                    })

                inward_weight = 0.0
                outward_weight = 0.0
                total_samples = 0
                score_sum = 0.0
                for loop in loops:
                    samples = max(int(loop.get("samples", 0)), 1)
                    score = float(loop.get("score", 0.0) or 0.0)
                    total_samples += samples
                    score_sum += score * samples
                    if loop.get("orientation") == "inward":
                        inward_weight += abs(score) * samples
                    elif loop.get("orientation") == "outward":
                        outward_weight += abs(score) * samples

                if inward_weight > outward_weight * 1.20 and inward_weight > 0.01:
                    orientation = "inward"
                elif outward_weight > inward_weight * 1.20 and outward_weight > 0.01:
                    orientation = "outward"
                else:
                    orientation = "unknown"

                return {
                    "points": boundary_verts,
                    "loops": loops,
                    "orientation": orientation,
                    "orientation_score": (score_sum / float(total_samples)) if total_samples else 0.0
                }
            except Exception:
                return None

        maya_main_window = QtWidgets.QApplication.activeWindow()
        if maya_main_window:
            maya_main_window.setEnabled(False)

        try:
            total_items = len(self.hp_data_cache) + len(self.lp_data_cache)
            step = 0

            for m_path, data in self.hp_data_cache.items():
                if self.progress_dlg.wasCanceled():
                    break
                self.progress_dlg.setLabelText(bg_l10n.text("Fingerprinting HP: {name}").format(name=data["name"].split('|')[-1]))

                verts = bg_core.GeoMatcher.get_world_vertices(m_path, density_pct=100)
                hp_verts_cache[m_path] = verts

                # Boundary holes detection
                if not data.get("is_zbrush"):
                    hole_entry = build_hp_hole_cache_entry(m_path, data.get("center"))
                    if hole_entry:
                        hp_holes_cache[m_path] = hole_entry

                if not data.get("is_zbrush") and HAS_MATH_CORE:
                    try:
                        fp_string = bg_math_core.generate_fingerprint_data(verts, data["center"])
                        
                        #print("DEBUG HASH | Объект: {} | Хэш: {}".format(data["name"], fp_string))
                        
                        data["hash"] = fp_string
                        data["variance"] = 0.0
                        data["mean_radius"] = data.get("radius", 0.0)
                        
                        data["hash"] = fp_string
                        data["variance"] = 0.0
                        data["mean_radius"] = data.get("radius", 0.0)
                        
                    except Exception as e:
                        print("bg_mixins: Ошибка генерации фингерпринта для {}: {}".format(data["name"], e))
                        data["hash"] = "empty"
                        data["variance"] = 999.0
                        data["mean_radius"] = 0.0
                else:
                    data["hash"] = "empty"
                    data["variance"] = 999.0
                    data["mean_radius"] = 0.0

                step += 1
                self.progress_dlg.setValue(int((step / total_items) * 100))
                self.progress_dlg.repaint()

            for m_path in self.lp_data_cache.keys():
                if self.progress_dlg.wasCanceled():
                    break
                self.progress_dlg.setLabelText(bg_l10n.text("Caching LP: {name}").format(name=m_path.split('|')[-1]))
                if m_path in lp_prebuilt_verts_cache:
                    lp_verts_cache[m_path] = lp_prebuilt_verts_cache[m_path]
                else:
                    lp_verts_cache[m_path] = bg_core.GeoMatcher.get_world_vertices(m_path, density_pct=100)
                lp_data = self.lp_data_cache.get(m_path)
                if lp_data is not None and HAS_MATH_CORE and lp_verts_cache.get(m_path):
                    try:
                        lp_data["hash"] = bg_math_core.generate_fingerprint_data(
                            lp_verts_cache[m_path],
                            lp_data.get("center", [0.0, 0.0, 0.0])
                        )
                        lp_data["variance"] = 0.0
                    except Exception:
                        lp_data["hash"] = "empty"
                        lp_data["variance"] = 999.0
                elif lp_data is not None:
                    lp_data.setdefault("hash", "empty")
                    lp_data.setdefault("variance", 999.0)
                step += 1
                self.progress_dlg.setValue(int((step / total_items) * 100))
                self.progress_dlg.repaint()

        finally:
            if maya_main_window:
                maya_main_window.setEnabled(True)

        if self.progress_dlg.wasCanceled():
            return self.log("HP Analysis canceled.", "orange")

        self.progress_dlg.setLabelText(bg_l10n.text("Starting Multithreaded Processing..."))

        self.hp_worker = HPGroupingWorker(
            self.hp_data_cache, self.lp_data_cache,
            hp_verts_cache, lp_verts_cache, hp_holes_cache,
            threshold_pct, group_limit,
            custom_clusters_dict=custom_clusters,
            locked_names=locked,
            strategy=worker_params['strategy'],
            use_symmetry=worker_params['use_symmetry'],
            bolt_elongation=self.spin_bolt_elong.value(),
            bolt_symmetry=self.spin_bolt_sym.value(),
            wire_elongation=self.spin_wire_elong.value(),
            compound_link_verts=worker_params['compound_link_verts'],
            compound_link_dist_pct=worker_params['compound_link_dist_pct'],
            detect_floaters=not worker_params.get('ignore_floaters', True),
            floater_radius=0.1
        )

        self.hp_worker.progress_value.connect(self.progress_dlg.setValue)
        self.hp_worker.progress_text.connect(self.progress_dlg.setLabelText)
        self.hp_worker.finished.connect(lambda groups, logs: self.on_hp_finished(groups, logs, hp_main, pair))
        self.progress_dlg.canceled.connect(self.hp_worker.stop)
        self.hp_worker.start()

    def on_hp_finished(self, groups, logs, hp_main, pair):
        self.progress_dlg.close()
        worker = getattr(self, 'hp_worker', None)
        summary_lines = list(getattr(worker, 'summary_lines', []) or [])
        explain_lines = list(getattr(worker, 'debug_lines', []) or [])

        debug_report = []
        if summary_lines:
            debug_report.append("=== Summary ===")
            debug_report.extend(summary_lines)
            debug_report.append("")
        debug_report.append("=== Worker Log ===")
        debug_report.extend([str(msg) for msg in (logs or [])])
        debug_report.append("")
        debug_report.append("=== Explain Details ===")
        debug_report.extend(explain_lines)
        self.last_debug_lines = debug_report

        if summary_lines:
            for msg in summary_lines:
                self.log(msg, "lightblue")
            for msg in logs:
                if str(msg).startswith("[Warning]") or str(msg).startswith("[Optimization]"):
                    self.log(msg, "orange")
        else:
            for msg in logs:
                self.log(msg, "orange")
        locked_subgroups = pair.get('locked', [])

        # GT/manual links are applied inside HPGroupingWorker before size
        # categorization and spatial packing. Do not create one subgroup per
        # GT link here; otherwise bg_gt_matcher clusters multiply final groups.

        with bg_core.undo_chunk("ApplyHPGroups"):
            for child in (cmds.listRelatives(hp_main, children=True, fullPath=True, type='transform') or []):
                match = re.search(r'(_HP|_LP)(\d*)$', child.split('|')[-1])
                grp_ui_name = child.split('|')[-1][:match.start()] + match.group(2) if match else child.split('|')[-1]
                if grp_ui_name not in locked_subgroups:
                    if not cmds.listRelatives(child, shapes=True):
                        try:
                            cmds.delete(cmds.rename(child, "DELETE_ME_" + str(uuid.uuid4())[:8]))
                        except:
                            pass

            for grp_name, meshes in groups.items():
                safe_name = bg_core.BakeConfig.trim_name(grp_name, bg_core.BakeConfig.SUFFIX_HP)

                existing_grp = None
                for child in (cmds.listRelatives(hp_main, children=True, fullPath=True, type='transform') or []):
                    if child.split('|')[-1] == safe_name:
                        existing_grp = child
                        break

                if existing_grp:
                    new_grp = existing_grp
                else:
                    new_grp = cmds.parent(cmds.group(em=True, name=safe_name), hp_main)[0]
                    cmds.addAttr(new_grp, ln=bg_core.BakeConfig.ATTR_BAKE_GROUP, dt="string")
                    cmds.setAttr("{}.{}".format(new_grp, bg_core.BakeConfig.ATTR_BAKE_GROUP), "HP", type="string")

                m_move = [m['name'] for m in meshes if cmds.objExists(m['name'])]
                if m_move:
                    cmds.parent(m_move, new_grp, absolute=True)

        self.refresh_left_panel()
        if hasattr(self, 'refresh_subgroup_color_preview'):
            self.refresh_subgroup_color_preview(reset_indices=True)
        self.log("HP Auto-Grouping complete. Processed {} groups.".format(len(groups)), "lightblue")
        if hasattr(self, 'record_user_action'):
            self.record_user_action("Analyze HP finished", "groups={}".format(len(groups)))


# ============================================================================
# LP MATCHING MIXIN
# ============================================================================

class LPMatchingMixin:
    """Methods for low-poly matching to HP groups."""

    def run_lp_matching(self):
        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not pair:
            return self.log("No active pair selected.", "orange")
        hp_main, lp_main, _ = self.core.resolve_main_nodes(pair)
        if not hp_main or not lp_main:
            return

        if not self.validate_frozen_transforms([hp_main, lp_main], [hp_main, lp_main], bg_l10n.text("Assign LP Meshes")):
            return

        final_lp_meshes = self.prepare_meshes(lp_main, flatten=True)
        final_lp_meshes = [m for m in final_lp_meshes if not m.split('|')[-1].endswith(bg_core.BakeConfig.SUFFIX_HP)]
        if not final_lp_meshes:
            return self.log("No valid LP meshes found.", "red")

        self.lp_data_cache.clear()
        for m in final_lp_meshes:
            data = bg_core.MeshDataManager.get_mesh_data(m)
            if data:
                self.lp_data_cache[m] = data

        if not self.lp_data_cache:
            return self.log("No valid LP mesh data.", "red")

        hp_groups = {}
        for child in (cmds.listRelatives(hp_main, children=True, fullPath=True, type='transform') or []):
            is_hp_attr = cmds.objExists(child + "." + bg_core.BakeConfig.ATTR_BAKE_GROUP) and cmds.getAttr(child + "." + bg_core.BakeConfig.ATTR_BAKE_GROUP) == "HP"
            if is_hp_attr or child.split('|')[-1].endswith(bg_core.BakeConfig.SUFFIX_HP):
                if not cmds.listRelatives(child, shapes=True):
                    b_grp = re.sub(r'(_HP|_hp)$', '', child.split('|')[-1])
                    m_paths = [m for m in (cmds.listRelatives(child, children=True, fullPath=True, type='transform') or []) if cmds.listRelatives(m, shapes=True, type='mesh')]
                    if m_paths:
                        hp_groups[b_grp] = m_paths

        if not hp_groups:
            return self.log("No HP subgroups found! Run HP Analysis first.", "orange")

        self.hp_data_cache.clear()
        all_hp_paths = []
        for paths in hp_groups.values():
            for p in paths:
                if p not in self.hp_data_cache:
                    data = bg_core.MeshDataManager.get_mesh_data(p)
                    if data:
                        self.hp_data_cache[p] = data
                if p not in all_hp_paths:
                    all_hp_paths.append(p)

        total_cache_steps = len(all_hp_paths) + (len(self.lp_data_cache) * 2)

        self.progress_dlg_lp = QtWidgets.QProgressDialog(bg_l10n.text("Extracting scene geometry..."), bg_l10n.text("Cancel"), 0, total_cache_steps, self)
        self.progress_dlg_lp.setWindowModality(QtCore.Qt.WindowModal)
        self.progress_dlg_lp.setMinimumDuration(0)
        self.progress_dlg_lp.setValue(0)

        # LP Matching cache counters/window state.
        # These must exist before the try/finally below, otherwise Assign LP Meshes
        # can crash in finally with NameError if an exception happens during caching.
        current_step = 0
        maya_main_window = QtWidgets.QApplication.activeWindow()
        if maya_main_window:
            maya_main_window.setEnabled(False)

# ---------------------------------------------------------
        # (Main Thread Safe)
        # ---------------------------------------------------------
        def get_flat_verts_safe(mesh_path, density):
            try:
                verts = bg_core.GeoMatcher.get_world_vertices(mesh_path, density_pct=density)
                return verts if verts else []
            except Exception:
                return []

        def collect_scene_edge_lengths(mesh_paths):
            """Собирает длины всех рёбер со списка мешей для глобальной статистики."""
            all_lengths = []
            for path in mesh_paths:
                try:
                    sel = om.MSelectionList()
                    sel.add(path)
                    dag_path = sel.getDagPath(0)
                    edge_iter = om.MItMeshEdge(dag_path)
                    while not edge_iter.isDone():
                        p1 = edge_iter.point(0, om.MSpace.kWorld)
                        p2 = edge_iter.point(1, om.MSpace.kWorld)
                        all_lengths.append(om.MVector(p2 - p1).length())
                        edge_iter.next()
                except Exception:
                    pass
            return all_lengths

        def build_augmented_vertex_cache(
                mesh_path,
                threshold,
                spacing,
                max_virtual_verts=8,
                max_total_samples=5000):

            try:
                sel = om.MSelectionList()
                sel.add(mesh_path)

                dag_path = sel.getDagPath(0)

                mesh_fn = om.MFnMesh(dag_path)

                verts_flat = []

                # ----------------------------------------
                # ORIGINAL VERTS
                # ----------------------------------------

                points = mesh_fn.getPoints(om.MSpace.kWorld)

                for p in points:
                    verts_flat.extend([p.x, p.y, p.z])

                total_samples = len(points)

                # ----------------------------------------
                # EDGE AUGMENTATION
                # ----------------------------------------

                edge_iter = om.MItMeshEdge(dag_path)

                while not edge_iter.isDone():

                    if total_samples >= max_total_samples:
                        break

                    p1 = edge_iter.point(0, om.MSpace.kWorld)
                    p2 = edge_iter.point(1, om.MSpace.kWorld)

                    vec = om.MVector(p2 - p1)

                    edge_length = vec.length()

                    if edge_length >= threshold:

                        divisions = int(edge_length / spacing)

                        divisions = max(2, divisions)
                        divisions = min(divisions, max_virtual_verts)

                        for i in range(1, divisions):

                            if total_samples >= max_total_samples:
                                break

                            t = float(i) / float(divisions)

                            pos = om.MPoint(
                                p1.x + vec.x * t,
                                p1.y + vec.y * t,
                                p1.z + vec.z * t
                            )

                            verts_flat.extend([pos.x, pos.y, pos.z])

                            total_samples += 1

                    edge_iter.next()

                return verts_flat

            except Exception:
                return get_flat_verts_safe(mesh_path, 100)
        # ---------------------------------------------------------

        # --- GLOBAL EDGE ANALYSIS ---
        self.progress_dlg_lp.setLabelText(bg_l10n.text("Analyzing Global Edge Statistics..."))
        
        all_scene_meshes = list(all_hp_paths) + list(self.lp_data_cache.keys())
        all_edge_lengths = collect_scene_edge_lengths(all_scene_meshes)
        
        # Защита от пустых списков
        if all_edge_lengths:
            global_avg = sum(all_edge_lengths) / len(all_edge_lengths)
        else:
            global_avg = 1.0

        # Константы для плотности виртуальных вершин
        EDGE_LENGTH_MULTIPLIER = 2.0
        TARGET_SPACING_MULTIPLIER = 1.5
        
        long_edge_threshold = global_avg * EDGE_LENGTH_MULTIPLIER
        target_spacing = max(global_avg * TARGET_SPACING_MULTIPLIER, 0.001) # Защита от деления на ноль

        hp_verts_cache = {}
        lp_verts_cache_fast = {}
        lp_verts_cache_full = {}
        

        try:
            for p in all_hp_paths:
                if self.progress_dlg_lp.wasCanceled():
                    break
                mesh_name = p.split('|')[-1]
                self.progress_dlg_lp.setLabelText(bg_l10n.text("Caching HP Geometry: {name}").format(name=mesh_name))
                
                # Заменяем get_flat_verts_safe на build_augmented_vertex_cache
                hp_verts_cache[p] = build_augmented_vertex_cache(p, long_edge_threshold, target_spacing)
                
                current_step += 1
                self.progress_dlg_lp.setValue(current_step)
                self.progress_dlg_lp.repaint()

            max_samples = 25
            for m in self.lp_data_cache.keys():
                if self.progress_dlg_lp.wasCanceled():
                    break
                mesh_name = m.split('|')[-1]
                
                # --- FAST PASS (Реальные вершины, ограничение плотности) ---
                self.progress_dlg_lp.setLabelText(bg_l10n.text("Caching LP Fast-Pass: {name}").format(name=mesh_name))
                lp_verts_cache_fast[m] = build_augmented_vertex_cache(
                m,
                long_edge_threshold * 1.5,
                target_spacing * 2.0,
                max_virtual_verts=2
)
                current_step += 1
                self.progress_dlg_lp.setValue(current_step)

                # --- FULL PASS (Интеграция интерполяции на основе Global Stats) ---
                self.progress_dlg_lp.setLabelText(bg_l10n.text("Caching LP Full-Pass (Augmented): {name}").format(name=mesh_name))
                
                # Заменяем старый get_densified_mesh_points
                lp_verts_cache_full[m] = build_augmented_vertex_cache(m, long_edge_threshold, target_spacing)
                
                # -------------------------------------------------
                # LP fingerprint generation (AFTER augmented cache)
                # -------------------------------------------------

                augmented_verts = lp_verts_cache_full.get(m, [])

                if augmented_verts and HAS_MATH_CORE:
                    try:
                        center = self.lp_data_cache[m].get("center", [0, 0, 0])

                        fp_string = bg_math_core.generate_fingerprint_data(
                            augmented_verts,
                            center
                        )

                        self.lp_data_cache[m]["hash"] = fp_string
                        self.lp_data_cache[m]["variance"] = 0.0

                    except Exception as e:
                        print("LP fingerprint error for {}: {}".format(m, e))
                        self.lp_data_cache[m]["hash"] = "empty"
                        self.lp_data_cache[m]["variance"] = 999.0

                else:
                    self.lp_data_cache[m]["hash"] = "empty"
                    self.lp_data_cache[m]["variance"] = 999.0

                current_step += 1
                self.progress_dlg_lp.setValue(current_step)
                self.progress_dlg_lp.repaint()

        finally:
            if maya_main_window:
                maya_main_window.setEnabled(True)

        if self.progress_dlg_lp.wasCanceled():
            return self.log("Matching canceled during geometry caching.", "orange")

        self.progress_dlg_lp.setLabelText(bg_l10n.text("Initializing C++ Matching Kernel..."))
        self.progress_dlg_lp.setMaximum(100)
        self.progress_dlg_lp.setValue(0)

        # Воркер запускается уже с обогащенным кэшем lp_verts_cache_full
        self.lp_worker = LPMatchingWorker(
            hp_groups=hp_groups,
            hp_data_cache=self.hp_data_cache,
            lp_data_cache=self.lp_data_cache,
            hp_verts_cache=hp_verts_cache,
            lp_verts_cache_fast=lp_verts_cache_fast,
            lp_verts_cache_full=lp_verts_cache_full,
            lp_threshold_coef=1.5
        )

        self.lp_worker.progress_value.connect(self.progress_dlg_lp.setValue)
        self.lp_worker.progress_text.connect(self.progress_dlg_lp.setLabelText)
        self.lp_worker.finished.connect(lambda matches: self.on_lp_finished(matches, lp_main))
        self.progress_dlg_lp.canceled.connect(self.lp_worker.stop)

        self.lp_worker.start()

    def on_lp_finished(self, matches, lp_main):
        # ... (Код завершения остается абсолютно без изменений) ...
        self.progress_dlg_lp.close()
        total_matched = 0
        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        locked_subgroups = pair.get('locked', []) if pair else []

        with bg_core.undo_chunk("ApplyLPMatching"):
            for child in (cmds.listRelatives(lp_main, children=True, fullPath=True, type='transform') or []):
                match_re = re.search(r'(_HP|_LP)(\d*)$', child.split('|')[-1])
                grp_ui_name = child.split('|')[-1][:match_re.start()] + match_re.group(2) if match_re else child.split('|')[-1]
                if grp_ui_name not in locked_subgroups:
                    if not cmds.listRelatives(child, shapes=True):
                        try:
                            cmds.delete(cmds.rename(child, "DELETE_ME_" + str(uuid.uuid4())[:8]))
                        except:
                            pass

            for grp_name, lp_paths in matches.items():
                s_name = bg_core.BakeConfig.trim_name(grp_name, bg_core.BakeConfig.SUFFIX_LP)
                target_grp = None
                for child in (cmds.listRelatives(lp_main, children=True, fullPath=True, type='transform') or []):
                    if child.split('|')[-1] == s_name:
                        target_grp = child
                        break

                if not target_grp:
                    target_grp = cmds.parent(cmds.group(em=True, name=s_name), lp_main)[0]
                    cmds.addAttr(target_grp, ln=bg_core.BakeConfig.ATTR_BAKE_GROUP, dt="string")
                    cmds.setAttr("{}.{}".format(target_grp, bg_core.BakeConfig.ATTR_BAKE_GROUP), "LP", type="string")

                if lp_paths:
                    vp = [p for p in lp_paths if cmds.objExists(p)]
                    if vp:
                        cmds.parent(vp, target_grp, absolute=True)
                        total_matched += len(vp)

        self.refresh_left_panel()
        self.log("LP Matched: {} out of {} objects.".format(total_matched, len(self.lp_data_cache)), "lightgreen")
        if hasattr(self, 'record_user_action'):
            self.record_user_action("Assign LP finished", "matched={}/{}".format(total_matched, len(self.lp_data_cache)))


# ============================================================================
# FINAL VIEW MIXIN (Preview, Final Group UI)
# ============================================================================
class FinalViewMixin:
    """Methods for final group view, smooth preview, and final mesh management."""

    def toggle_preview_smoothing(self):
        self.is_preview_active = not getattr(self, 'is_preview_active', False)
        is_on = self.is_preview_active

        if is_on:
            self.btn_preview.setText(bg_l10n.text("Smooth ON"))
            self.btn_preview.setStyleSheet("background-color: #2ecc71; font-weight: bold; color: white;")
        else:
            self.btn_preview.setText(bg_l10n.text("Smooth View"))
            self.btn_preview.setStyleSheet("background-color: #3498db; font-weight: bold; color: white;")

        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not pair:
            return
        hp_main, _, _ = self.core.resolve_main_nodes(pair)

        widgets = getattr(self, 'final_mesh_widgets', [])
        if not widgets:
            return

        cmds.select(clear=True)
        
        # Замораживаем вьюпорт для быстрого переключения атрибутов
        cmds.refresh(suspend=True)

        try:
            if not is_on:
                for item in widgets:
                    self.apply_preview_to_mesh(item['full_prefix'], item['combo'], hp_main, is_batch=True, fast_off=True)
            else:
                for item in widgets:
                    self.apply_preview_to_mesh(item['full_prefix'], item['combo'], hp_main, is_batch=True)
        finally:
            # Снимаем заморозку
            cmds.refresh(suspend=False)
            
            # Тот самый рабочий метод корректного обновления вьюпорта
            def safe_refresh():
                try:
                    cmds.refresh()
                except Exception:
                    pass
            
            # Даем Maya 100 мс на укладку памяти перед отрисовкой
            QtCore.QTimer.singleShot(100, safe_refresh)

    def disable_preview_smoothing_for_export(self):
        if not getattr(self, 'is_preview_active', False):
            return
        self.toggle_preview_smoothing()

    def update_single_preview(self, full_prefix, combo):
        if not getattr(self, 'is_preview_active', False):
            return
        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not pair:
            return
        hp_main, _, _ = self.core.resolve_main_nodes(pair)
        self.apply_preview_to_mesh(full_prefix, combo, hp_main)

    def apply_preview_to_mesh(self, full_prefix, combo, hp_main, is_batch=False, fast_off=False):
        if not hp_main or not cmds.objExists(hp_main):
            return

        level = combo.currentIndex()
        is_on = getattr(self, 'is_preview_active', False)
        full_prefix_lower = full_prefix.lower()

        all_shapes = cmds.listRelatives(hp_main, allDescendents=True, fullPath=True, type='mesh') or []
        valid_shapes = [s for s in all_shapes if not cmds.getAttr(s + ".intermediateObject")]
        hp_transforms = list(set([cmds.listRelatives(s, parent=True, fullPath=True)[0] for s in valid_shapes]))
        hp_meshes = [m for m in hp_transforms if m.split('|')[-1].lower().startswith(full_prefix_lower + "_high")]

        if not hp_meshes:
            return

        if not is_batch:
            cmds.select(clear=True)
            cmds.refresh(suspend=True)

        try:
            for hp in hp_meshes:
                is_in_zb_layer = False
                layers = cmds.listConnections(hp, type="displayLayer") or []
                hp_shapes = cmds.listRelatives(hp, shapes=True, fullPath=True) or []
                for s in hp_shapes:
                    layers.extend(cmds.listConnections(s, type="displayLayer") or [])
                for layer in layers:
                    if layer and "zbrush" in layer.lower():
                        is_in_zb_layer = True
                        break

                for shape in hp_shapes:
                    if cmds.getAttr(shape + ".intermediateObject"):
                        continue
                    try:
                        if fast_off or not is_on or level == 0:
                            cmds.setAttr(shape + ".displaySmoothMesh", 0)
                        elif not is_in_zb_layer and is_on and level > 0:
                            cmds.setAttr(shape + ".smoothLevel", level)
                            cmds.setAttr(shape + ".displaySmoothMesh", 2)
                    except Exception as e:
                        cmds.warning("Could not update smoothing for {}: {}".format(shape, e))
        finally:
            if not is_batch:
                cmds.refresh(suspend=False)

    def set_final_low_visibility(self, base_name, visible):
        global_lp_root = "LP_Combine_BG"
        chapter_grp_path = "{}|{}".format(global_lp_root, base_name)
        self.is_final_low_visible = bool(visible)

        if not cmds.objExists(global_lp_root):
            self.is_final_low_visible = False
            visible = False
        else:
            all_chapters = cmds.listRelatives(global_lp_root, children=True, fullPath=True) or []
            current_chapter_full = cmds.ls(chapter_grp_path, long=True)
            current_chapter_full = current_chapter_full[0] if current_chapter_full else ""

            if visible and current_chapter_full:
                cmds.setAttr(global_lp_root + ".visibility", True)
                for chapter_folder in all_chapters:
                    cmds.setAttr(chapter_folder + ".visibility", chapter_folder == current_chapter_full)
            else:
                for chapter_folder in all_chapters:
                    cmds.setAttr(chapter_folder + ".visibility", False)
                cmds.setAttr(global_lp_root + ".visibility", False)
                self.is_final_low_visible = False

        if getattr(self, 'is_final_view', False) and hasattr(self, 'btn_toggle_lp'):
            self.btn_toggle_lp.blockSignals(True)
            self.btn_toggle_lp.setChecked(self.is_final_low_visible)
            self.btn_toggle_lp.setText(bg_l10n.text("Low Visible" if self.is_final_low_visible else "Low Hidden"))
            self.btn_toggle_lp.setStyleSheet("background-color: #4a5d4a;" if self.is_final_low_visible else "background-color: #8c4242;")
            self.btn_toggle_lp.blockSignals(False)

        self.sync_final_view_isolation(base_name)

    def sync_final_view_isolation(self, base_name):
        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not pair or not getattr(self, 'is_final_view', False):
            return
        hp_main, _, _ = self.core.resolve_main_nodes(pair)
        if not hp_main or not cmds.objExists(hp_main):
            return

        chapter_grp_path = "LP_Combine_BG|{}".format(base_name)
        hp_all_desc = cmds.listRelatives(hp_main, allDescendents=True, fullPath=True, type='transform') or []
        model_panels = cmds.getPanel(type='modelPanel') or []
        for panel in model_panels:
            if not cmds.isolateSelect(panel, query=True, state=True):
                continue
            iso_set = cmds.isolateSelect(panel, q=True, viewObjects=True)
            set_name = iso_set[0] if isinstance(iso_set, list) else iso_set
            if cmds.objExists(set_name):
                cmds.sets(clear=set_name)

            for child in hp_all_desc:
                short_name = child.split('|')[-1]
                is_final_mesh = short_name.startswith(base_name) and "_high" in short_name
                if is_final_mesh and cmds.listRelatives(child, shapes=True):
                    cmds.isolateSelect(panel, addDagObject=child)
            if getattr(self, 'is_final_low_visible', False) and cmds.objExists(chapter_grp_path):
                cmds.isolateSelect(panel, addDagObject=chapter_grp_path)

    def toggle_final_view(self):
        self.is_final_view = not getattr(self, 'is_final_view', False)

        if hasattr(self, 'btn_fs'):
            self.btn_fs.setVisible(not self.is_final_view)
        if hasattr(self, 'btn_add'):
            self.btn_add.setVisible(not self.is_final_view)
        if hasattr(self, 'btn_combine_bake'):
            self.btn_combine_bake.setVisible(not self.is_final_view)

        if self.is_final_view:
            self.btn_toggle_view.setText(bg_l10n.text("Back"))
            self.btn_preview.setVisible(True)
            self.btn_process_final.setVisible(True)
        else:
            self.btn_toggle_view.setText(bg_l10n.text("Final Group"))
            self.btn_preview.setVisible(False)
            self.btn_process_final.setVisible(False)
            if getattr(self, 'is_preview_active', False):
                self.toggle_preview_smoothing()

        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if pair:
            hp_main, lp_main, _ = self.core.resolve_main_nodes(pair)
            base_name = pair.get('base', '')
            chapter_grp_path = "LP_Combine_BG|{}".format(base_name)

            if not hasattr(self, 'saved_subgroup_vis'):
                self.saved_subgroup_vis = {}

            # HP subgroups visibility
            if hp_main and cmds.objExists(hp_main):
                hp_subgroups = cmds.listRelatives(hp_main, children=True, fullPath=True, type='transform') or []
                if self.is_final_view:
                    for sg in hp_subgroups:
                        self.saved_subgroup_vis[sg] = cmds.getAttr(sg + ".visibility")
                        cmds.setAttr(sg + ".visibility", True)
                else:
                    for sg in hp_subgroups:
                        saved_state = self.saved_subgroup_vis.get(sg, True)
                        cmds.setAttr(sg + ".visibility", saved_state)

            # LP subgroups visibility
            if lp_main and cmds.objExists(lp_main):
                lp_subgroups = cmds.listRelatives(lp_main, children=True, fullPath=True, type='transform') or []
                if self.is_final_view:
                    for sg in lp_subgroups:
                        self.saved_subgroup_vis[sg] = cmds.getAttr(sg + ".visibility")
                        cmds.setAttr(sg + ".visibility", False)
                else:
                    for sg in lp_subgroups:
                        saved_state = self.saved_subgroup_vis.get(sg, True)
                        cmds.setAttr(sg + ".visibility", saved_state)

            # LP_Combine_BG
            global_lp_root = "LP_Combine_BG"
            if self.is_final_view:
                self.set_final_low_visibility(base_name, False)
            elif cmds.objExists(global_lp_root):
                self.set_final_low_visibility(base_name, False)
            else:
                self.is_final_low_visible = False

            if not self.is_final_view:
                self.sync_toggle_buttons(hp_main, lp_main)

            # Isolate select logic
            if hp_main and cmds.objExists(hp_main):
                model_panels = cmds.getPanel(type='modelPanel') or []
                hp_all_desc = cmds.listRelatives(hp_main, allDescendents=True, fullPath=True, type='transform') or []
                for panel in model_panels:
                    if cmds.isolateSelect(panel, query=True, state=True):
                        iso_set = cmds.isolateSelect(panel, q=True, viewObjects=True)
                        set_name = iso_set[0] if isinstance(iso_set, list) else iso_set
                        if cmds.objExists(set_name):
                            cmds.sets(clear=set_name)

                        if self.is_final_view:
                            self.sync_final_view_isolation(base_name)
                        else:
                            cmds.isolateSelect(panel, addDagObject=hp_main)
                            if lp_main and cmds.objExists(lp_main):
                                cmds.isolateSelect(panel, addDagObject=lp_main)

        self.refresh_left_panel()

    def render_final_view(self):
        self.final_mesh_widgets = []
        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not pair:
            return
        pair.setdefault('final_smooth_states', {})
        self.final_smooth_states = dict(pair.get('final_smooth_states') or {})

        hp_main, lp_main, _ = self.core.resolve_main_nodes(pair)
        if not hp_main or not lp_main:
            self.subgroups_layout.addWidget(QtWidgets.QLabel(bg_l10n.text("HP or LP root groups not found.")))
            return

        base_name = pair.get('base', '')
        subgroup_names = set()

        chapter_grp_path = "LP_Combine_BG|{}".format(base_name)
        lp_root = chapter_grp_path if cmds.objExists(chapter_grp_path) else lp_main

        lp_children = cmds.listRelatives(lp_root, children=True, fullPath=True) or []
        for lp_path in lp_children:
            short_name = lp_path.split('|')[-1]
            if short_name.startswith(base_name) and short_name.endswith("_low"):
                display_name = short_name.replace(base_name + "_", "").replace("_low", "")
                subgroup_names.add(display_name)

        hp_all_desc = cmds.listRelatives(hp_main, allDescendents=True, fullPath=True, type='transform') or []
        for hp_path in hp_all_desc:
            short_name = hp_path.split('|')[-1]
            if short_name.startswith(base_name) and "_high" in short_name and cmds.listRelatives(hp_path, shapes=True):
                display_name = short_name.replace(base_name + "_", "").split("_high")[0]
                subgroup_names.add(display_name)

        if not subgroup_names:
            self.subgroups_layout.addWidget(QtWidgets.QLabel(bg_l10n.text("No finalized meshes found. Run 'Combine Fin' first.")))
            return

        self.final_selected_names = set()
        sorted_subgroup_names = sorted(subgroup_names)
        if hasattr(self, 'cb_color_subgroups') and self.cb_color_subgroups.isChecked() and hasattr(self, 'ensure_subgroup_color_indices'):
            self.ensure_subgroup_color_indices(sorted_subgroup_names)

        for display_name in sorted_subgroup_names:
            hp_nodes = (cmds.ls("*{}_high*".format(display_name), long=True) or [])
            full_prefix = base_name + "_" + display_name

            frame = QtWidgets.QFrame()
            if hasattr(self, 'subgroup_row_style'):
                frame.setStyleSheet(self.subgroup_row_style(display_name, False))
            row_layout = QtWidgets.QHBoxLayout(frame)
            row_layout.setContentsMargins(4, 4, 4, 4)
            row_layout.setSpacing(6)

            is_vis = self.is_visible(hp_nodes[0]) if hp_nodes else False
            btn_vis = QtWidgets.QPushButton(bg_l10n.text("Vis" if is_vis else "Hid"))
            btn_vis.setFixedSize(30, 24)
            btn_vis.setStyleSheet("background-color: #4a5d4a;" if is_vis else "background-color: #8c4242;")
            btn_vis.setFocusPolicy(QtCore.Qt.NoFocus)
            btn_vis.clicked.connect(lambda checked=False, h=hp_nodes, b=btn_vis: self.toggle_final_hp_vis(h, b))
            row_layout.addWidget(btn_vis)

            btn_name = SubgroupButton(display_name)
            btn_name.setCheckable(True)
            if hasattr(self, 'subgroup_name_style'):
                btn_name.setStyleSheet(self.subgroup_name_style(display_name, False))
            else:
                btn_name.setStyleSheet("background-color: transparent; text-align: left; padding-left: 5px;")
            btn_name.clicked.connect(lambda checked=False, n=display_name: self.set_final_row_selected(n, checked))
            btn_name.doubleClicked.connect(lambda checked=False, n=display_name: self.select_final_hp_nodes(n))
            btn_name.rightClicked.connect(lambda checked=False, n=display_name: self.show_final_row_context_menu(n))
            row_layout.addWidget(btn_name, stretch=1)

            combo = QtWidgets.QComboBox()
            combo.addItems(["Smooth 0", "Smooth 1", "Smooth 2", "Smooth 3"])
            combo.setFixedWidth(85)
            combo.setStyleSheet("background-color: #444; color: white;")
            combo.setFocusPolicy(QtCore.Qt.NoFocus)

            cached_level = self.final_smooth_states.get(display_name, 2)
            combo.setCurrentIndex(cached_level)
            combo.currentIndexChanged.connect(lambda idx, prefix=full_prefix, name=display_name, c=combo:
                                               self.on_final_smooth_combo_changed(name, idx, prefix, c))
            row_layout.addWidget(combo)

            btn_smooth_up = QtWidgets.QPushButton(bg_l10n.text("+"))
            btn_smooth_up.setFixedSize(24, 24)
            btn_smooth_up.setStyleSheet("background-color: #425c42; font-weight: bold;")
            btn_smooth_up.setFocusPolicy(QtCore.Qt.NoFocus)
            btn_smooth_up.clicked.connect(lambda checked=False, c=combo, delta=1: self.adjust_final_smooth_level(c, delta))
            row_layout.addWidget(btn_smooth_up)

            btn_smooth_down = QtWidgets.QPushButton(bg_l10n.text("-"))
            btn_smooth_down.setFixedSize(24, 24)
            btn_smooth_down.setStyleSheet("background-color: #8c6239; font-weight: bold;")
            btn_smooth_down.setFocusPolicy(QtCore.Qt.NoFocus)
            btn_smooth_down.clicked.connect(lambda checked=False, c=combo, delta=-1: self.adjust_final_smooth_level(c, delta))
            row_layout.addWidget(btn_smooth_down)

            self.subgroups_layout.addWidget(frame)

            self.final_mesh_widgets.append({
                'subgroup_name': display_name,
                'full_prefix': full_prefix,
                'frame': frame,
                'name_button': btn_name,
                'combo': combo,
                'btn_vis': btn_vis,
                'btn_smooth_up': btn_smooth_up,
                'btn_smooth_down': btn_smooth_down,
                'hp_nodes': hp_nodes
            })
        bg_l10n.localize_widget_tree(self.subgroups_widget)

    def set_final_row_selected(self, name, checked=True):
        if not hasattr(self, 'final_selected_names'):
            self.final_selected_names = set()

        if checked:
            self.final_selected_names.add(name)
        else:
            self.final_selected_names.discard(name)

        for widget_data in getattr(self, 'final_mesh_widgets', []):
            btn = widget_data.get('name_button')
            if not btn:
                continue
            is_selected = widget_data.get('subgroup_name') in self.final_selected_names
            btn.blockSignals(True)
            btn.setChecked(is_selected)
            btn.blockSignals(False)
            if is_selected:
                if hasattr(self, 'subgroup_name_style'):
                    btn.setStyleSheet(self.subgroup_name_style(widget_data.get('subgroup_name'), True))
                else:
                    btn.setStyleSheet("background-color: #3a5375; font-weight: bold; text-align: left; padding-left: 5px;")
            else:
                if hasattr(self, 'subgroup_name_style'):
                    btn.setStyleSheet(self.subgroup_name_style(widget_data.get('subgroup_name'), False))
                else:
                    btn.setStyleSheet("background-color: transparent; text-align: left; padding-left: 5px;")
            frame = widget_data.get('frame')
            if frame and hasattr(self, 'subgroup_row_style'):
                frame.setStyleSheet(self.subgroup_row_style(widget_data.get('subgroup_name'), is_selected))

    def adjust_final_smooth_level(self, combo, delta):
        if not combo:
            return
        new_index = max(0, min(combo.count() - 1, combo.currentIndex() + int(delta)))
        if new_index != combo.currentIndex():
            combo.setCurrentIndex(new_index)

    def show_final_row_context_menu(self, old_name):
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(bg_core.BakeConfig.STYLE_CONTEXT_MENU)
        rename_action = menu.addAction("Rename Final Group")
        bg_l10n.localize_menu(menu)
        action = menu.exec_(QtGui.QCursor.pos())
        if action == rename_action:
            new_name, ok = QtWidgets.QInputDialog.getText(self, bg_l10n.text("Rename Final Group"), bg_l10n.text("New Name:"), text=old_name)
            if ok and new_name and new_name != old_name:
                self.rename_final_subgroup(old_name, new_name.strip().replace(".", "_"))

    def select_final_hp_nodes(self, display_name):
        hp_nodes = cmds.ls("*{}_high*".format(display_name), long=True) or []
        if hp_nodes:
            cmds.select(hp_nodes, replace=True)

    def save_final_smooth_states(self):
        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not pair:
            return
        pair['final_smooth_states'] = dict(getattr(self, 'final_smooth_states', {}) or {})
        bg_core.BakeSessionModel.save(self.root_pairs)

    def on_final_smooth_combo_changed(self, triggered_name, new_level, full_prefix, combo_widget):
        self.final_smooth_states[triggered_name] = new_level
        self.update_single_preview(full_prefix, combo_widget)

        selected_names = set(getattr(self, 'final_selected_names', set()))

        if triggered_name in selected_names:
            for widget_data in getattr(self, 'final_mesh_widgets', []):
                name = widget_data['subgroup_name']
                if name in selected_names and name != triggered_name:
                    target_combo = widget_data['combo']
                    self.final_smooth_states[name] = new_level
                    target_combo.blockSignals(True)
                    target_combo.setCurrentIndex(new_level)
                    target_combo.blockSignals(False)
                    self.update_single_preview(widget_data['full_prefix'], target_combo)
            self.save_final_smooth_states()
        else:
            self.final_selected_names = set([triggered_name])
            self.set_final_row_selected(triggered_name, True)
            self.save_final_smooth_states()

    def toggle_final_hp_vis(self, hp_nodes, btn):
        valid_nodes = [n for n in hp_nodes if cmds.objExists(n)]
        if not valid_nodes:
            return
        current_state = cmds.getAttr(valid_nodes[0] + ".visibility")
        new_state = not current_state
        for node in valid_nodes:
            cmds.setAttr(node + ".visibility", new_state)
            if new_state:
                parent = cmds.listRelatives(node, parent=True, fullPath=True)
                if parent:
                    cmds.setAttr(parent[0] + ".visibility", True)
                    grandparent = cmds.listRelatives(parent[0], parent=True, fullPath=True)
                    if grandparent:
                        cmds.setAttr(grandparent[0] + ".visibility", True)
        if new_state:
            btn.setText(bg_l10n.text("Vis"))
            btn.setStyleSheet("background-color: #4a5d4a;")
        else:
            btn.setText(bg_l10n.text("Hid"))
            btn.setStyleSheet("background-color: #8c4242;")

    def rename_final_subgroup(self, old_name, new_name):
        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not pair:
            return
        hp_main, lp_main, _ = self.core.resolve_main_nodes(pair)
        base_name = pair.get('base', '')

        with bg_core.undo_chunk("RenameFinalSubgroup"):
            hp_meshes = cmds.listRelatives(hp_main, allDescendents=True, fullPath=True, type='transform') or []
            for hp in hp_meshes:
                if not cmds.listRelatives(hp, shapes=True):
                    continue
                short = hp.split('|')[-1]
                if short.startswith(old_name + "_high"):
                    cmds.rename(hp, short.replace(old_name, new_name, 1))

            chapter_grp_path = "LP_Combine_BG|{}".format(base_name)
            lp_root = chapter_grp_path if cmds.objExists(chapter_grp_path) else lp_main
            lp_meshes = cmds.listRelatives(lp_root, children=True, fullPath=True) or []
            for lp in lp_meshes:
                short = lp.split('|')[-1]
                if short.startswith(old_name + "_low"):
                    cmds.rename(lp, short.replace(old_name, new_name, 1))

        self.refresh_left_panel()


# ============================================================================
# EXPORT MIXIN
# ============================================================================
class ExportMixin:
    """Methods for final export, batch export, and combine."""

    def show_export_context_menu(self, point):
        menu = QtWidgets.QMenu(self)
        try:
            menu.setStyleSheet(bg_core.BakeConfig.STYLE_CONTEXT_MENU)
        except AttributeError:
            menu.setStyleSheet("QMenu { background-color: #242424; color: #ddd; border: 1px solid #444; } QMenu::item:selected { background-color: #3e3e3e; }")

        menu_book = menu.addMenu("Export Book")
        action_book_sep = menu_book.addAction("Separate HP and LP")
        action_book_both = menu_book.addAction("HP+LP in single file")

        menu_lp = menu.addMenu("Export LP")
        action_lp_chapter = menu_lp.addAction("Chapter")
        action_lp_book = menu_lp.addAction("Book")

        menu_hp = menu.addMenu("Export HP")
        action_hp_chapter = menu_hp.addAction("Chapter")
        action_hp_book = menu_hp.addAction("Book")

        action_book_sep.triggered.connect(lambda: self.batch_export_book(mode='separate'))
        action_book_both.triggered.connect(lambda: self.batch_export_book(mode='both'))
        action_lp_chapter.triggered.connect(self.export_active_lp_only)
        action_lp_book.triggered.connect(self.batch_export_lp_book)
        action_hp_chapter.triggered.connect(lambda: self.export_final_group_ui(mode='hp'))
        action_hp_book.triggered.connect(lambda: self.batch_export_book(mode='hp'))
        bg_l10n.localize_menu(menu)

        target_button = getattr(self, 'btn_process_final', None) or getattr(self, 'btn_export', None)
        if target_button:
            menu.exec_(target_button.mapToGlobal(point))
        else:
            menu.exec_(QtGui.QCursor.pos())

    def export_final_group_ui(self, mode='separate'):
        if isinstance(mode, bool):
            mode = 'separate'
        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not pair:
            return self.log("No active pair selected for export.", "red")

        hp_main, lp_main, _ = self.core.resolve_main_nodes(pair)
        base_name = pair.get('base', 'Chapter_Export')
        widgets = getattr(self, 'final_mesh_widgets', [])

        export_dirs = cmds.fileDialog2(fileMode=3, caption=bg_l10n.text("Select Export Directory for {name}").format(name=base_name))
        if not export_dirs:
            return
        export_dir = export_dirs[0]
        self.disable_preview_smoothing_for_export()

        with self.suspend_subgroup_color_preview():
            with self.suspend_isolation():
                self.log("Suspended viewport isolation for export...", "lightblue")
                if mode == 'separate':
                    exp_hp = bg_final_export.FinalExportProcessor.export_chapter(
                        base_name, hp_main, lp_main, widgets, parent_window=self, mode='hp', export_dir=export_dir,
                        smooth_states=pair.get('final_smooth_states', {})
                    )
                    exp_lp = bg_final_export.FinalExportProcessor.export_chapter(
                        base_name, hp_main, lp_main, widgets, parent_window=self, mode='lp', export_dir=export_dir,
                        smooth_states=pair.get('final_smooth_states', {})
                    )
                    if exp_hp or exp_lp:
                        cmds.inViewMessage(amg="Chapter exported: Separate HP and LP", pos='midCenter', fade=True)
                else:
                    exported_name = bg_final_export.FinalExportProcessor.export_chapter(
                        base_name, hp_main, lp_main, widgets, parent_window=self, mode=mode, export_dir=export_dir,
                        smooth_states=pair.get('final_smooth_states', {})
                    )
                    if exported_name:
                        cmds.inViewMessage(amg="Chapter exported: {}.fbx".format(exported_name), pos='midCenter', fade=True)

    def batch_export_book(self, mode='separate'):
        if not self.active_root_id:
            return cmds.warning("No active chapter selected. Select a chapter to identify the book.")
        active_pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not active_pair:
            return
        active_book = active_pair.get('book')
        if not active_book:
            return cmds.warning("Active chapter is not in any book. Please group it into a book first.")

        book_pairs = [p for p in self.root_pairs if p.get('book') == active_book]
        if not book_pairs:
            return

        export_dirs = cmds.fileDialog2(fileMode=3, caption=bg_l10n.text("Select Folder for Book Export ({name})").format(name=active_book))
        if not export_dirs:
            return
        export_dir = export_dirs[0]
        self.disable_preview_smoothing_for_export()

        success_count = 0
        with self.suspend_subgroup_color_preview():
            with self.suspend_isolation():
                self.log("Batch Exporting Book (Isolation Disabled)...", "lightblue")
                for pair in book_pairs:
                    hp_main, lp_main, _ = self.core.resolve_main_nodes(pair)
                    base_name = pair.get('base', 'Chapter_Export')
                    if mode == 'separate':
                        exp_hp = bg_final_export.FinalExportProcessor.export_chapter(
                            base_name, hp_main, lp_main, [], parent_window=self, mode='hp', export_dir=export_dir,
                            smooth_states=pair.get('final_smooth_states', {})
                        )
                        exp_lp = bg_final_export.FinalExportProcessor.export_chapter(
                            base_name, hp_main, lp_main, [], parent_window=self, mode='lp', export_dir=export_dir,
                            smooth_states=pair.get('final_smooth_states', {})
                        )
                        if exp_hp or exp_lp:
                            success_count += 1
                    else:
                        exported = bg_final_export.FinalExportProcessor.export_chapter(
                            base_name, hp_main, lp_main, [], parent_window=self, mode=mode, export_dir=export_dir,
                            smooth_states=pair.get('final_smooth_states', {})
                        )
                        if exported:
                            success_count += 1
        cmds.inViewMessage(amg="Export of book '{}' completed: {} chapters!".format(active_book, success_count), pos='midCenter', fade=True)

    def export_active_lp_only(self):
        with self.suspend_isolation():
            self.log("Exporting Active LP Only (Isolation Disabled)...", "lightblue")
        if not self.active_root_id:
            cmds.warning("No active chapter to export.")
            return
        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not pair:
            return
        export_dir = cmds.fileDialog2(fileMode=3, dialogStyle=2, caption=bg_l10n.text("Select folder for LP export"))
        if not export_dir:
            return
        export_dir = export_dir[0]
        self.disable_preview_smoothing_for_export()
        with self.suspend_subgroup_color_preview():
            if self._export_lp_for_pair(pair, export_dir):
                cmds.inViewMessage(amg="LP meshes successfully exported!", pos='midCenter', fade=True)
            else:
                cmds.warning("Combined LP meshes not found. Press 'Combine Fin' first.")
        cmds.select(clear=True)

    def batch_export_all_lp_only(self):
        with self.suspend_isolation():
            self.log("Batch Exporting All LP (Isolation Disabled)...", "lightblue")
        if not self.root_pairs:
            cmds.warning("Chapter list is empty.")
            return
        confirm = cmds.confirmDialog(
            title=bg_l10n.text('Batch LP Export'),
            message=bg_l10n.text('Export combined LP meshes for ALL chapters in the list?'),
            button=[bg_l10n.text('Yes'), bg_l10n.text('No')], defaultButton=bg_l10n.text('Yes'), cancelButton=bg_l10n.text('No'), dismissString=bg_l10n.text('No')
        )
        if confirm != bg_l10n.text('Yes'):
            return
        export_dir = cmds.fileDialog2(fileMode=3, dialogStyle=2, caption=bg_l10n.text("Select folder for batch LP export"))
        if not export_dir:
            return
        export_dir = export_dir[0]
        self.disable_preview_smoothing_for_export()
        success_count = 0
        with self.suspend_subgroup_color_preview():
            for pair in self.root_pairs:
                if self._export_lp_for_pair(pair, export_dir):
                    success_count += 1
        cmds.inViewMessage(amg="Batch LP export completed! Successful: {} chapters".format(success_count), pos='midCenter', fade=True)
        cmds.select(clear=True)

    def batch_export_lp_book(self):
        if not self.active_root_id:
            return cmds.warning("No active chapter selected. Select a chapter to identify the book.")
        active_pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not active_pair:
            return
        active_book = active_pair.get('book')
        if not active_book:
            return cmds.warning("Active chapter is not in any book. Please group it into a book first.")
        book_pairs = [p for p in self.root_pairs if p.get('book') == active_book]
        if not book_pairs:
            return
        export_dirs = cmds.fileDialog2(fileMode=3, caption=bg_l10n.text("Select Folder for Book LP Export ({name})").format(name=active_book))
        if not export_dirs:
            return
        export_dir = export_dirs[0]
        self.disable_preview_smoothing_for_export()
        success_count = 0
        with self.suspend_subgroup_color_preview():
            for pair in book_pairs:
                if self._export_lp_for_pair(pair, export_dir):
                    success_count += 1
        cmds.inViewMessage(amg="LP Export of book '{}' completed: {} chapters!".format(active_book, success_count), pos='midCenter', fade=True)
        cmds.select(clear=True)

    def _export_lp_for_pair(self, pair, export_dir):
        base_name = pair.get('base', 'Chapter_Export')
        chapter_grp_path = "LP_Combine_BG|{}".format(base_name)
        _, lp_main, _ = self.core.resolve_main_nodes(pair)
        search_root = chapter_grp_path if cmds.objExists(chapter_grp_path) else lp_main
        if not search_root or not cmds.objExists(search_root):
            return False
        to_export = []
        lp_children = cmds.listRelatives(search_root, children=True, fullPath=True) or []
        for child in lp_children:
            short_name = child.split('|')[-1]
            is_subgroup = not cmds.listRelatives(child, shapes=True)
            if "_low" in short_name and not is_subgroup:
                to_export.append(child)
        if not to_export:
            return False
        cmds.select(to_export, replace=True)
        export_name = "{}_LP".format(base_name)
        export_path = "{}/{}.fbx".format(export_dir.rstrip('/\\'), export_name).replace('\\', '/')
        try:
            bg_final_export.FinalExportProcessor.export_selected_fbx(export_path)
            return True
        except Exception as e:
            cmds.warning("Failed to export LP for {}: {}".format(base_name, e))
            return False

    def combine_all_subgroups_ui(self):
        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not pair:
            return
        hp_main, lp_main, _ = self.core.resolve_main_nodes(pair)
        base_name = pair.get('base', 'Unknown')
        if not hp_main or not lp_main:
            cmds.warning("Bake Groups: HP or LP root objects not found.")
            return
        result = bg_final_export.FinalExportProcessor.combine_all_subgroups(base_name, hp_main, lp_main, parent_window=self)
        self.refresh_left_panel()
        if not result or not result.get('success'):
            cmds.warning("Combine Fin failed. See script editor for details.")
            return

        if result.get('lp', 0) == 0:
            cmds.warning("Combine Fin: no LP subgroups were combined. Run Assign LP Meshes first or check LP subgroup names.")
        else:
            self.set_final_low_visibility(base_name, False)
        if hasattr(self, 'record_user_action'):
            self.record_user_action(
                "Combine Fin",
                "hp={} | lp={}".format(result.get('hp', 0), result.get('lp', 0))
            )

        cmds.inViewMessage(
            amg="Combine Fin complete: HP {} / LP {}".format(result.get('hp', 0), result.get('lp', 0)),
            pos='midCenter',
            fade=True
        )


# ============================================================================
# GROUP MANAGEMENT MIXIN
# ============================================================================
class GroupManagementMixin:
    """Methods for creating, deleting, renaming subgroups, and UI interactions."""

    def create_subgroup_pair(self):
        if not self.active_root_id:
            return
        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not pair:
            return
        hp_main, lp_main, _ = self.core.resolve_main_nodes(pair)
        if not hp_main or not lp_main:
            return

        with bg_core.undo_chunk("CreateSubgroupPair"):
            sel = cmds.ls(selection=True, long=True) or []
            suffix = self.input_suffix.text().strip().replace(".", "_")

            if not suffix:
                idx = 1
                while True:
                    if not cmds.objExists("Group_{:02d}{}".format(idx, bg_core.BakeConfig.SUFFIX_HP)):
                        suffix = "Group_{:02d}".format(idx)
                        break
                    idx += 1

            hp_name = "{}{}".format(suffix, bg_core.BakeConfig.SUFFIX_HP)
            lp_name = "{}{}".format(suffix, bg_core.BakeConfig.SUFFIX_LP)

            if not cmds.objExists(hp_name):
                new_hp = cmds.parent(cmds.group(em=True, name=hp_name), hp_main)[0]
                cmds.addAttr(new_hp, ln=bg_core.BakeConfig.ATTR_BAKE_GROUP, dt="string")
                cmds.setAttr("{}.{}".format(new_hp, bg_core.BakeConfig.ATTR_BAKE_GROUP), "HP", type="string")

            if not cmds.objExists(lp_name):
                new_lp = cmds.parent(cmds.group(em=True, name=lp_name), lp_main)[0]
                cmds.addAttr(new_lp, ln=bg_core.BakeConfig.ATTR_BAKE_GROUP, dt="string")
                cmds.setAttr("{}.{}".format(new_lp, bg_core.BakeConfig.ATTR_BAKE_GROUP), "LP", type="string")

            if sel:
                to_hp = [o for o in sel if cmds.objExists(o) and self.core.is_descendant_of(o, hp_main)]
                to_lp = [o for o in sel if cmds.objExists(o) and self.core.is_descendant_of(o, lp_main)]
                moved_nodes = []
                if to_hp:
                    parented = cmds.parent(to_hp, hp_name, absolute=True)
                    if parented:
                        moved_nodes.extend(cmds.ls(parented, long=True) or parented)
                if to_lp:
                    parented = cmds.parent(to_lp, lp_name, absolute=True)
                    if parented:
                        moved_nodes.extend(cmds.ls(parented, long=True) or parented)
                cmds.select(clear=True)
            else:
                moved_nodes = []

            self.active_subgroup_name = suffix
            self.input_suffix.clear()
            self.refresh_left_panel()
            if moved_nodes and hasattr(self, 'recolor_moved_subgroup_nodes'):
                self.recolor_moved_subgroup_nodes(moved_nodes, suffix)
            if hasattr(self, 'record_user_action'):
                self.record_user_action(
                    "Create Group",
                    "name={} | hp_moved={} | lp_moved={}".format(suffix, len(to_hp) if sel else 0, len(to_lp) if sel else 0)
                )

    def add_to_groups_ui(self, hp_node, lp_node, hp_main, lp_main):
        sel = cmds.ls(sl=True, long=True)
        if not sel:
            return
        hp_target = cmds.ls(hp_node, l=True)[0] if hp_node and cmds.objExists(hp_node) else None
        lp_target = cmds.ls(lp_node, l=True)[0] if lp_node and cmds.objExists(lp_node) else None

        moved_count = 0
        skipped_count = 0
        moved_names = []
        moved_nodes = []
        source_groups = set()
        target_ui_name = None
        for obj in sel:
            if not cmds.objExists(obj):
                continue
            is_hp = self.core.is_descendant_of(obj, hp_main)
            is_lp = self.core.is_descendant_of(obj, lp_main)
            target_node = None
            if is_hp and hp_target:
                target_node = hp_target
            elif is_lp and lp_target:
                target_node = lp_target
            else:
                self.log("Skip {}: not in active HP/LP root.".format(obj.split('|')[-1]), "orange")
                skipped_count += 1
                continue
            current_parent = cmds.listRelatives(obj, parent=True, fullPath=True)
            if current_parent and current_parent[0] == target_node:
                continue
            source_name = current_parent[0].split('|')[-1] if current_parent else "World"
            try:
                parented = cmds.parent(obj, target_node)
                if parented:
                    moved_nodes.extend(cmds.ls(parented, long=True) or parented)
                moved_count += 1
                moved_names.append(obj.split('|')[-1])
                source_groups.add(source_name)
                if not target_ui_name:
                    target_short = target_node.split('|')[-1]
                    target_ui_name = target_short
                    for suffix in (bg_core.BakeConfig.SUFFIX_HP, bg_core.BakeConfig.SUFFIX_LP):
                        if target_ui_name.endswith(suffix):
                            target_ui_name = target_ui_name[:-len(suffix)]
                            break
            except Exception as e:
                self.log("Skip parenting for {}: {}".format(obj.split('|')[-1], e), "orange")
                skipped_count += 1
        self.refresh_left_panel()
        if moved_nodes and target_ui_name and hasattr(self, 'recolor_moved_subgroup_nodes'):
            self.recolor_moved_subgroup_nodes(moved_nodes, target_ui_name)
        if hasattr(self, 'record_user_action') and (moved_count or skipped_count):
            target_name = hp_target.split('|')[-1] if hp_target else (lp_target.split('|')[-1] if lp_target else "Unknown")
            moved_preview = self._format_debug_names(moved_names, limit=20) if hasattr(self, '_format_debug_names') else ", ".join(moved_names[:20])
            source_preview = self._format_debug_names(sorted(source_groups), limit=10) if hasattr(self, '_format_debug_names') else ", ".join(sorted(source_groups))
            self.record_user_action(
                "Add to Group",
                "target={} | moved={} | skipped={} | from={} | meshes={}".format(
                    target_name, moved_count, skipped_count, source_preview, moved_preview
                )
            )

    def add_to_selected_subgroup_ui(self):
        if not self.active_subgroup_name:
            cmds.warning("First, select a subgroup from the list on the left.")
            return
        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not pair:
            return
        hp_main, lp_main, _ = self.core.resolve_main_nodes(pair)
        hp_grp = "|".join([hp_main, self.active_subgroup_name + bg_core.BakeConfig.SUFFIX_HP])
        lp_grp = "|".join([lp_main, self.active_subgroup_name + bg_core.BakeConfig.SUFFIX_LP])
        if cmds.objExists(hp_grp) and cmds.objExists(lp_grp):
            self.add_to_groups_ui(hp_grp, lp_grp, hp_main, lp_main)

    def safe_delete_subgroup_ui(self, hp_node, lp_node, root_hp, root_lp):
        if not self.confirm_action("Delete subgroup? Children will be unparented to Root."):
            return
        with bg_core.undo_chunk("DeleteSubgroup"):
            for node, root in [(hp_node, root_hp), (lp_node, root_lp)]:
                if node and cmds.objExists(node):
                    children = cmds.listRelatives(node, children=True, fullPath=True) or []
                    if children:
                        if root and cmds.objExists(root):
                            cmds.parent(children, root)
                        else:
                            cmds.parent(children, world=True)
                    try:
                        cmds.delete(cmds.rename(node, "DELETE_ME_" + str(uuid.uuid4())[:8]))
                    except:
                        pass
            self.refresh_left_panel()
            if hasattr(self, 'record_user_action'):
                deleted_name = hp_node.split('|')[-1].replace(bg_core.BakeConfig.SUFFIX_HP, '') if hp_node else (lp_node.split('|')[-1].replace(bg_core.BakeConfig.SUFFIX_LP, '') if lp_node else "Unknown")
                self.record_user_action("Delete Group", deleted_name)

    def rename_subgroup_ui(self, old_name, hp_node, lp_node):
        new_name, ok = QtWidgets.QInputDialog.getText(self, bg_l10n.text("Rename Subgroup"), bg_l10n.text("New name for {name}:").format(name=old_name),
                                                      QtWidgets.QLineEdit.Normal, old_name)
        if ok and new_name and new_name.strip() and new_name != old_name:
            new_name = new_name.strip().replace(" ", "_").replace(".", "_")
            with bg_core.undo_chunk("RenameSubgroup"):
                try:
                    if hp_node and cmds.objExists(hp_node):
                        cmds.rename(hp_node, "{}{}".format(new_name, bg_core.BakeConfig.SUFFIX_HP))
                    if lp_node and cmds.objExists(lp_node):
                        cmds.rename(lp_node, "{}{}".format(new_name, bg_core.BakeConfig.SUFFIX_LP))

                    pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
                    if pair and old_name in pair.get('locked', []):
                        pair['locked'].remove(old_name)
                        pair['locked'].append(new_name)
                        bg_core.BakeSessionModel.save(self.root_pairs)

                    if self.active_subgroup_name == old_name:
                        self.active_subgroup_name = new_name

                    self.refresh_left_panel()
                    if hasattr(self, 'record_user_action'):
                        self.record_user_action("Rename Group", "{} -> {}".format(old_name, new_name))
                except Exception as e:
                    cmds.warning("Rename failed: {}".format(e))

    def toggle_lock(self, ui_name):
        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not pair:
            return
        locked_list = pair.get('locked', [])
        if ui_name in locked_list:
            locked_list.remove(ui_name)
        else:
            locked_list.append(ui_name)
        pair['locked'] = locked_list
        bg_core.BakeSessionModel.save(self.root_pairs)
        self.refresh_left_panel()
        if hasattr(self, 'record_user_action'):
            state = "locked" if ui_name in locked_list else "unlocked"
            self.record_user_action("Toggle Group Lock", "{} {}".format(ui_name, state))

    def set_active_subgroup(self, name):
        self.active_subgroup_name = name
        self.refresh_left_panel()

    def optimize_subgroups(self):
        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not pair:
            cmds.warning("No active group for optimization.")
            return
        hp_main, lp_main, _ = self.core.resolve_main_nodes(pair)
        deleted_count = 0
        for main_node in [hp_main, lp_main]:
            if not main_node or not cmds.objExists(main_node):
                continue
            subgroups = cmds.listRelatives(main_node, children=True, fullPath=True, type='transform') or []
            for sub in subgroups:
                meshes = cmds.listRelatives(sub, allDescendents=True, type='mesh')
                if not meshes:
                    cmds.delete(sub)
                    deleted_count += 1
        if deleted_count > 0:
            cmds.inViewMessage(amg="Deleted empty subgroups: {}".format(deleted_count), pos='midCenter', fade=True)
            self.refresh_left_panel()
            if hasattr(self, 'record_user_action'):
                self.record_user_action("Optimize Groups", "deleted_empty={}".format(deleted_count))
        else:
            cmds.inViewMessage(amg="No empty subgroups found", pos='midCenter', fade=True)

    def select_subgroup_by_selected_mesh(self):
        selection = cmds.ls(sl=True, long=True)
        if not selection:
            cmds.warning("Nothing selected in the scene.")
            return
        target_node = selection[0]
        current = target_node
        subgroup_transform = None
        while True:
            parent = cmds.listRelatives(current, parent=True, fullPath=True)
            if not parent:
                if cmds.attributeQuery(bg_core.BakeConfig.ATTR_BAKE_GROUP, node=current, exists=True):
                    subgroup_transform = current
                break
            parent_short = parent[0].split('|')[-1]
            if parent_short.endswith(bg_core.BakeConfig.SUFFIX_HP) or parent_short.endswith(bg_core.BakeConfig.SUFFIX_LP):
                subgroup_transform = current
                break
            if cmds.attributeQuery(bg_core.BakeConfig.ATTR_BAKE_GROUP, node=current, exists=True):
                subgroup_transform = current
                break
            current = parent[0]

        if subgroup_transform:
            cmds.select(subgroup_transform, replace=True)
            subgroup_name = subgroup_transform.split('|')[-1]
            match = re.search(r'(_HP|_hp|HP|hp|_LP|_lp|LP|lp)(\d*)$', subgroup_name)
            ui_name = subgroup_name[:match.start()] + match.group(2) if match else subgroup_name
            activated = False
            if hasattr(self, 'subgroups_layout') and self.subgroups_layout:
                for i in range(self.subgroups_layout.count()):
                    item = self.subgroups_layout.itemAt(i)
                    if item and item.widget():
                        btn = item.widget()
                        if isinstance(btn, QtWidgets.QPushButton) and btn.text().startswith(ui_name):
                            btn.click()
                            activated = True
                            break
            if not activated and hasattr(self, 'select_subgroup'):
                self.select_subgroup(ui_name)
            cmds.inViewMessage(amg="Subgroup '{}' successfully activated".format(ui_name), pos='midCenter', fade=True)
        else:
            cmds.warning("Selected object is not linked to Bake Groups metadata.")


# ============================================================================
# SCENE INTERACTION MIXIN
# ============================================================================
class SceneInteractionMixin:
    """Methods for picking nodes, creating root pair, tools, find similar, etc."""

    def _mesh_transforms_under_root(self, root_node):
        if not root_node or not cmds.objExists(root_node):
            return []

        candidates = []
        root_shapes = cmds.listRelatives(root_node, shapes=True, fullPath=True, type='mesh') or []
        if any(not cmds.getAttr(shape + ".intermediateObject") for shape in root_shapes):
            candidates.append((cmds.ls(root_node, long=True) or [root_node])[0])

        shapes = cmds.listRelatives(root_node, allDescendents=True, fullPath=True, type='mesh') or []
        valid_shapes = [shape for shape in shapes if not cmds.getAttr(shape + ".intermediateObject")]
        for shape in valid_shapes:
            parent = cmds.listRelatives(shape, parent=True, fullPath=True) or []
            if parent:
                candidates.append(parent[0])

        result = []
        seen = set()
        for node in candidates:
            long_node = (cmds.ls(node, long=True) or [node])[0]
            if long_node not in seen:
                result.append(long_node)
                seen.add(long_node)
        return result

    def _is_transform_frozen(self, node, tolerance=0.0001):
        if not node or not cmds.objExists(node):
            return True

        attr_defaults = {
            "translateX": 0.0, "translateY": 0.0, "translateZ": 0.0,
            "rotateX": 0.0, "rotateY": 0.0, "rotateZ": 0.0,
            "scaleX": 1.0, "scaleY": 1.0, "scaleZ": 1.0,
            "shearXY": 0.0, "shearXZ": 0.0, "shearYZ": 0.0,
        }

        for attr, default in attr_defaults.items():
            plug = "{}.{}".format(node, attr)
            if not cmds.objExists(plug):
                continue
            try:
                value = float(cmds.getAttr(plug))
            except Exception:
                continue
            if abs(value - default) > tolerance:
                return False
        return True

    def _unfrozen_transforms(self, nodes):
        result = []
        seen = set()
        for node in nodes or []:
            if not node or not cmds.objExists(node):
                continue
            long_node = (cmds.ls(node, long=True) or [node])[0]
            if long_node in seen:
                continue
            seen.add(long_node)
            if not self._is_transform_frozen(long_node):
                result.append(long_node)
        return result

    def validate_frozen_transforms(self, root_nodes, mesh_roots=None, action_name=""):
        roots = [(cmds.ls(node, long=True) or [node])[0] for node in (root_nodes or []) if node and cmds.objExists(node)]
        mesh_nodes = []
        for root_node in mesh_roots or []:
            mesh_nodes.extend(self._mesh_transforms_under_root(root_node))

        invalid_roots = self._unfrozen_transforms(roots)
        invalid_meshes = self._unfrozen_transforms(mesh_nodes)
        invalid = []
        seen = set()
        for node in invalid_roots + invalid_meshes:
            if node not in seen:
                invalid.append(node)
                seen.add(node)

        if not invalid:
            return True

        cmds.select(invalid, replace=True)
        preview = [node.split('|')[-1] for node in invalid[:12]]
        if len(invalid) > 12:
            preview.append("... +{} more".format(len(invalid) - 12))

        message = bg_l10n.text("Freeze Transformations required. Selected invalid root groups or meshes.")
        detail = "{}: {}".format(bg_l10n.text("Invalid transforms"), ", ".join(preview))
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(bg_l10n.text("Freeze Transformations"))
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setText(message)
        box.setInformativeText("{}\n\n{}".format(detail, bg_l10n.text("Do you want to run Freeze Transformations now?")))
        yes_btn = box.addButton(bg_l10n.text("Yes"), QtWidgets.QMessageBox.AcceptRole)
        no_btn = box.addButton(bg_l10n.text("No"), QtWidgets.QMessageBox.RejectRole)
        box.setDefaultButton(yes_btn)
        box.exec_()

        if box.clickedButton() != yes_btn:
            cmds.warning(message)
            self.log("{} {}".format(message, detail), "red")
            if hasattr(self, 'record_user_action'):
                self.record_user_action(
                    "Freeze Transformations check failed",
                    "{} | invalid={}".format(action_name or "Action", len(invalid))
                )
            return False

        try:
            with bg_core.undo_chunk("FreezeTransformations"):
                cmds.makeIdentity(invalid, apply=True, translate=True, rotate=True, scale=True, normal=False)
        except Exception as exc:
            cmds.warning(bg_l10n.text("Freeze Transformations failed: {error}").format(error=exc))
            self.log(bg_l10n.text("Freeze Transformations failed: {error}").format(error=exc), "red")
            return False

        invalid_after = self._unfrozen_transforms(roots)
        invalid_after.extend(self._unfrozen_transforms(mesh_nodes))
        invalid_after = list(dict((node, None) for node in invalid_after).keys())
        if invalid_after:
            cmds.select(invalid_after, replace=True)
            cmds.warning(bg_l10n.text("Freeze Transformations did not clear all invalid transforms."))
            self.log(bg_l10n.text("Freeze Transformations did not clear all invalid transforms."), "red")
            return False

        cmds.select(clear=True)
        self.log(bg_l10n.text("Freeze Transformations completed."), "lightgreen")
        if hasattr(self, 'record_user_action'):
            self.record_user_action(
                "Freeze Transformations completed",
                "{} | fixed={}".format(action_name or "Action", len(invalid))
            )
        return True

    def show_find_zbrush_context_menu(self, point):
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(bg_core.BakeConfig.STYLE_CONTEXT_MENU)

        panel = QtWidgets.QWidget(menu)
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        value = int(getattr(self, 'zbrush_triangle_threshold', 50))
        label = QtWidgets.QLabel(bg_l10n.text("Triangular faces: {value}%").format(value=value))
        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        slider.setRange(1, 100)
        slider.setValue(value)
        slider.setMinimumWidth(180)

        def on_value_changed(new_value):
            self.zbrush_triangle_threshold = int(new_value)
            label.setText(bg_l10n.text("Triangular faces: {value}%").format(value=int(new_value)))

        slider.valueChanged.connect(on_value_changed)
        layout.addWidget(label)
        layout.addWidget(slider)

        widget_action = QtWidgets.QWidgetAction(menu)
        widget_action.setDefaultWidget(panel)
        menu.addAction(widget_action)
        menu.addSeparator()
        run_action = menu.addAction("Find ZBrush now")
        add_to_layer_action = menu.addAction("Add mesh in ZBrush layer")
        bg_l10n.localize_menu(menu)

        btn = getattr(self, 'btn_find_zbrush', None)
        global_pos = btn.mapToGlobal(point) if btn else QtGui.QCursor.pos()
        action = menu.exec_(global_pos)
        if action == run_action:
            self.find_zbrush_meshes()
        elif action == add_to_layer_action:
            self.add_selected_zbrush_meshes_to_layer()

    def _find_zbrush_display_layers(self):
        layers = cmds.ls(type="displayLayer") or []
        return sorted([layer for layer in layers if layer and "zbrush" in layer.lower()], key=lambda item: item.lower())

    def _create_zbrush_display_layer(self):
        base_names = ["ZBrush", "ZBrush_BakeGroups"]
        for name in base_names:
            if not cmds.objExists(name):
                return cmds.createDisplayLayer(name=name, empty=True)

        idx = 1
        while cmds.objExists("ZBrush_BakeGroups_{:02d}".format(idx)):
            idx += 1
        return cmds.createDisplayLayer(name="ZBrush_BakeGroups_{:02d}".format(idx), empty=True)

    def _choose_zbrush_display_layer(self, layers):
        if not layers:
            return self._create_zbrush_display_layer()
        if len(layers) == 1:
            return layers[0]

        create_label = bg_l10n.text("Create New ZBrush Layer")
        choices = list(layers) + [create_label]
        choice, ok = QtWidgets.QInputDialog.getItem(
            self,
            bg_l10n.text("Multiple ZBrush Layers"),
            bg_l10n.text("Multiple ZBrush layers found. Choose target ZBrush layer:"),
            choices,
            0,
            False
        )
        if not ok or not choice:
            return None
        if choice == create_label:
            return self._create_zbrush_display_layer()
        return choice

    def _selected_mesh_transforms_under(self, root_node):
        selected = cmds.ls(selection=True, long=True, flatten=True) or []
        mesh_transforms = []
        seen = set()

        for item in selected:
            node = item.split(".")[0]
            if not cmds.objExists(node):
                continue

            if cmds.objectType(node) == "mesh":
                parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
                node = parents[0] if parents else node

            shapes = cmds.listRelatives(node, shapes=True, fullPath=True, type="mesh") or []
            shapes = [shape for shape in shapes if not cmds.getAttr(shape + ".intermediateObject")]
            if not shapes:
                continue

            long_node = (cmds.ls(node, long=True) or [node])[0]
            is_inside_root = long_node == root_node
            if not is_inside_root:
                try:
                    is_inside_root = bool(self.core.is_descendant_of(long_node, root_node))
                except Exception:
                    is_inside_root = long_node.startswith(root_node + "|")

            if is_inside_root and long_node not in seen:
                mesh_transforms.append(long_node)
                seen.add(long_node)

        return mesh_transforms

    def add_selected_zbrush_meshes_to_layer(self):
        if not self.active_root_id:
            return cmds.warning(bg_l10n.text("No active chapter selected."))

        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not pair:
            return

        hp_main, _, _ = self.core.resolve_main_nodes(pair)
        if not hp_main or not cmds.objExists(hp_main):
            return cmds.warning(bg_l10n.text("HP root not found for active chapter."))

        meshes = self._selected_mesh_transforms_under(hp_main)
        if not meshes:
            cmds.warning(bg_l10n.text("No selected ZBrush mesh candidates found."))
            self.log(bg_l10n.text("No selected ZBrush mesh candidates found."), "orange")
            return

        layer = self._choose_zbrush_display_layer(self._find_zbrush_display_layers())
        if not layer:
            return

        with bg_core.undo_chunk("AddZBrushMeshesToLayer"):
            cmds.editDisplayLayerMembers(layer, meshes, noRecurse=True)

        cmds.select(meshes, replace=True)
        message = bg_l10n.text("Added {count} mesh(es) to ZBrush layer: {layer}").format(
            count=len(meshes),
            layer=layer
        )
        self.log(message, "lightgreen")
        if hasattr(self, 'record_user_action'):
            self.record_user_action("Add ZBrush layer meshes", "count={} | layer={}".format(len(meshes), layer))
        cmds.inViewMessage(amg=message, pos='midCenter', fade=True)

    def _get_triangle_face_ratio(self, mesh_transform):
        try:
            shapes = cmds.listRelatives(mesh_transform, shapes=True, fullPath=True, type='mesh') or []
            shapes = [s for s in shapes if not cmds.getAttr(s + ".intermediateObject")]
            if not shapes:
                return 0.0, 0, 0

            sel = om.MSelectionList()
            sel.add(shapes[0])
            dag_path = sel.getDagPath(0)
            mesh_fn = om.MFnMesh(dag_path)
            face_vertex_counts, _ = mesh_fn.getVertices()
            total_faces = len(face_vertex_counts)
            if total_faces <= 0:
                return 0.0, 0, 0

            triangle_faces = sum(1 for count in face_vertex_counts if int(count) == 3)
            ratio = (triangle_faces / float(total_faces)) * 100.0
            return ratio, triangle_faces, total_faces
        except Exception:
            return 0.0, 0, 0

    def find_zbrush_meshes(self):
        if not self.active_root_id:
            return cmds.warning("No active chapter selected.")

        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not pair:
            return

        hp_main, _, _ = self.core.resolve_main_nodes(pair)
        if not hp_main or not cmds.objExists(hp_main):
            return cmds.warning("HP root not found for active chapter.")

        shapes = cmds.listRelatives(hp_main, allDescendents=True, fullPath=True, type='mesh') or []
        valid_shapes = [s for s in shapes if not cmds.getAttr(s + ".intermediateObject")]
        hp_meshes = sorted(set(cmds.listRelatives(s, parent=True, fullPath=True)[0] for s in valid_shapes))
        if not hp_meshes:
            return cmds.warning("No HP meshes found in active chapter.")

        tri_data = []
        for mesh in hp_meshes:
            ratio, triangle_faces, total_faces = self._get_triangle_face_ratio(mesh)
            if total_faces > 0:
                tri_data.append((mesh, ratio, triangle_faces, total_faces))

        if not tri_data:
            return cmds.warning("Could not read face topology for active HP meshes.")

        threshold_pct = int(getattr(self, 'zbrush_triangle_threshold', 50))
        found_data = [item for item in tri_data if item[1] >= float(threshold_pct)]
        found = [mesh for mesh, _, _, _ in found_data]

        if not found:
            cmds.select(clear=True)
            self.log("Find ZBrush: no HP meshes with {}%+ triangular faces.".format(threshold_pct), "orange")
            return

        cmds.select(found, replace=True)
        best_ratio = max(ratio for _, ratio, _, _ in found_data)
        self.log(
            "Find ZBrush: selected {} HP mesh(es) with {}%+ triangular faces (best {:.1f}%). Layer links were not changed.".format(
                len(found), threshold_pct, best_ratio
            ),
            "lightgreen"
        )
        if hasattr(self, 'record_user_action'):
            self.record_user_action(
                "Find ZBrush",
                "selected={} | threshold={} | best={:.1f}%".format(len(found), threshold_pct, best_ratio)
            )
        cmds.inViewMessage(amg="Find ZBrush: {} HP mesh(es) selected".format(len(found)), pos='midCenter', fade=True)

    def pick_node(self, node_type):
        sel = cmds.ls(selection=True, type='transform', long=True) or []
        if not sel:
            return cmds.warning("Select exactly 1 transform object.")
        node = sel[0]
        short_name = node.split('|')[-1]
        if node_type == "HP":
            self.picked_hp = node
            self.le_picked_hp.setText(short_name)
        else:
            self.picked_lp = node
            self.le_picked_lp.setText(short_name)

    def create_root_pair_from_picked(self):
        if not self.picked_hp or not cmds.objExists(self.picked_hp):
            return cmds.warning("Invalid HP node.")
        if not self.picked_lp or not cmds.objExists(self.picked_lp):
            return cmds.warning("Invalid LP node.")

        if not self.validate_frozen_transforms(
            [self.picked_hp, self.picked_lp],
            [self.picked_hp, self.picked_lp],
            bg_l10n.text("Create Pair")
        ):
            return

        with bg_core.undo_chunk("CreateRootPair"):
            hp_node = self.picked_hp
            lp_node = self.picked_lp
            hp_base = re.sub(r'(_HP|_hp|HP|hp).*$', '', hp_node.split('|')[-1]).strip('_')
            lp_base = re.sub(r'(_LP|_lp|LP|lp).*$', '', lp_node.split('|')[-1]).strip('_')

            f_base = hp_base
            if hp_base != lp_base:
                from bg_ui_widgets import ResolveNameDialog
                dialog = ResolveNameDialog(hp_base, lp_base, self)
                if hasattr(dialog, 'exec_'):
                    res = dialog.exec_()
                else:
                    res = dialog.exec_()
                if res == QtWidgets.QDialog.Accepted:
                    f_base = dialog.get_chosen_name()
                else:
                    return

            n_hp = "{}{}".format(f_base, bg_core.BakeConfig.SUFFIX_HP)
            n_lp = "{}{}".format(f_base, bg_core.BakeConfig.SUFFIX_LP)
            try:
                if hp_node.split('|')[-1] != n_hp:
                    hp_node = cmds.rename(hp_node, n_hp)
                if lp_node.split('|')[-1] != n_lp:
                    lp_node = cmds.rename(lp_node, n_lp)
            except Exception as e:
                cmds.warning("Rename error: {}".format(e))

            np = {"id": str(uuid.uuid4()), "base": f_base,
                  "hp_uuid": cmds.ls(hp_node, uuid=True)[0],
                  "lp_uuid": cmds.ls(lp_node, uuid=True)[0],
                  "locked": [], "book": "", "final_smooth_states": {}}
            self.root_pairs.append(np)
            bg_core.BakeSessionModel.save(self.root_pairs)
            self.picked_hp, self.picked_lp = None, None
            self.le_picked_hp.clear()
            self.le_picked_lp.clear()
            self.activate_root(np)
            self.refresh_right_panel()

            self.prepare_meshes(hp_node, flatten=not self.cb_keep_hp_structure.isChecked())
            self.prepare_meshes(lp_node, flatten=True)
            self.refresh_left_panel()
            if hasattr(self, 'record_user_action'):
                self.record_user_action(
                    "Create Pair",
                    "base={} | keep_hp={}".format(f_base, bool(self.cb_keep_hp_structure.isChecked()))
                )

    def prepare_meshes(self, root_node, flatten=True):
        if not root_node or not cmds.objExists(root_node):
            return []
        with bg_core.undo_chunk("PrepareMeshes"):
            if flatten:
                all_desc = cmds.listRelatives(root_node, allDescendents=True, fullPath=True) or []
                meshes = [m for m in all_desc if cmds.objectType(m) == 'mesh' and not cmds.getAttr("{}.intermediateObject".format(m))]
                mesh_transforms = list(set([cmds.listRelatives(m, parent=True, fullPath=True)[0] for m in meshes]))
                to_move = [t for t in mesh_transforms if cmds.listRelatives(t, parent=True, fullPath=True)[0] != root_node]
                if to_move:
                    try:
                        cmds.parent(to_move, root_node, absolute=True)
                    except:
                        pass
                all_desc = cmds.listRelatives(root_node, allDescendents=True, fullPath=True, type='transform') or []
                for child in reversed(all_desc):
                    if cmds.objExists(child) and not cmds.listRelatives(child, shapes=True) and not cmds.listRelatives(child, allDescendents=True):
                        try:
                            cmds.delete(child)
                        except:
                            pass

            fm = []
            for child in (cmds.listRelatives(root_node, children=True, fullPath=True, type='transform') or []):
                if cmds.objExists(child) and cmds.listRelatives(child, shapes=True, type='mesh'):
                    try:
                        fm.append(cmds.ls(child, long=True)[0])
                    except Exception as e:
                        cmds.warning("Prepare error: {}".format(e))
            return fm

    def tool_combine(self):
        sel = cmds.ls(sl=True, l=True)
        if not sel or len(sel) < 2:
            return cmds.warning("Select at least two objects to combine.")
        first_obj_name = sel[0].split('|')[-1]
        target_parent = self.get_parent_tool(sel[0])
        with bg_core.undo_chunk("ModelPack_Combine"):
            temp_grp = None
            try:
                if target_parent and cmds.objExists(target_parent):
                    temp_grp = cmds.group(empty=True, name="TEMP_PREVENT_DELETE")
                    temp_grp = cmds.parent(temp_grp, target_parent)[0]
                combined = cmds.polyUnite(sel, ch=False)[0]
                cmds.delete(combined, constructionHistory=True)
                cmds.xform(combined, cp=True)
                combined = cmds.rename(combined, "{}_Combined".format(first_obj_name))
                if target_parent and cmds.objExists(target_parent):
                    current_p = self.get_parent_tool(combined)
                    if current_p != target_parent:
                        combined = cmds.parent(combined, target_parent)[0]
                cmds.select(combined, r=True)
                if hasattr(self, 'record_user_action'):
                    self.record_user_action(
                        "Combine",
                        "input={} | result={}".format(len(sel), combined.split('|')[-1])
                    )
            except Exception as e:
                cmds.warning("Combine failed: {}".format(e))
            finally:
                if temp_grp and cmds.objExists(temp_grp):
                    cmds.delete(temp_grp)

    def tool_separate(self):
        sel = cmds.ls(sl=True, l=True)
        if not sel:
            return cmds.warning("Select an object to separate.")
        with bg_core.undo_chunk("ModelPack_Separate"):
            try:
                final_selection = []
                for obj in sel:
                    base_name = obj.split('|')[-1]
                    target_parent = self.get_parent_tool(obj)
                    cmds.polySeparate(obj, ch=True)
                    cmds.delete(obj, ch=True)
                    parts = cmds.listRelatives(obj, children=True, fullPath=True, type='transform') or []
                    if parts:
                        for i, part in enumerate(parts):
                            new_name = cmds.rename(part, "{}_Part{}".format(base_name, i+1))
                            if target_parent and cmds.objExists(target_parent):
                                new_name = cmds.parent(new_name, target_parent)[0]
                            else:
                                if self.get_parent_tool(new_name):
                                    new_name = cmds.parent(new_name, world=True)[0]
                            final_selection.append(new_name)
                        if cmds.objExists(obj):
                            cmds.delete(obj)
                if final_selection:
                    cmds.select(final_selection, r=True)
                    if hasattr(self, 'record_user_action'):
                        self.record_user_action(
                            "Separate",
                            "input={} | parts={}".format(len(sel), len(final_selection))
                        )
            except Exception as e:
                cmds.warning("Separate failed: {}".format(e))

    def get_parent_tool(self, obj):
        parent = cmds.listRelatives(obj, parent=True, fullPath=True)
        return parent[0] if parent else None

    def find_similar_meshes_ui(self):
        sel = cmds.ls(selection=True, long=True, type='transform') or []
        targets = [s for s in sel if cmds.listRelatives(s, shapes=True, type='mesh')]
        if not targets:
            return cmds.warning("No meshes found in current selection.")
        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not pair:
            return cmds.warning("No active pair selected.")
        hp_main, lp_main, _ = self.core.resolve_main_nodes(pair)
        search_root = hp_main if self.core.is_descendant_of(targets[0], hp_main) else lp_main
        if not self.core.is_descendant_of(targets[0], search_root):
            return cmds.warning("Selected meshes are not inside the active root group.")

        mode = getattr(self, 'find_sim_mode', 'SIM')
        final_selection = set()
        matches_per_target = []
        for t in targets:
            matches = set(self.core.find_similar_meshes_fast([t], search_root))
            matches_per_target.append(list(matches))

        if mode == 'ALL' or len(targets) == 1:
            for matches in matches_per_target:
                final_selection.update(matches)
        else:
            def get_pos(obj):
                bb = cmds.xform(obj, q=True, ws=True, bb=True)
                return [(bb[0]+bb[3])/2, (bb[1]+bb[4])/2, (bb[2]+bb[5])/2]

            def get_dist(p1, p2):
                return math.sqrt((p1[0]-p2[0])**2 + (p1[1]-p2[1])**2 + (p1[2]-p2[2])**2)

            target_positions = [get_pos(t) for t in targets]
            n_targets = len(targets)
            target_dist_matrix = [[0.0]*n_targets for _ in range(n_targets)]
            for i in range(n_targets):
                for j in range(i+1, n_targets):
                    d = get_dist(target_positions[i], target_positions[j])
                    target_dist_matrix[i][j] = target_dist_matrix[j][i] = d

            pos_cache = {}
            for matches in matches_per_target:
                for m in matches:
                    if m not in pos_cache:
                        pos_cache[m] = get_pos(m)

            for c0 in matches_per_target[0]:
                c0_pos = pos_cache[c0]
                cluster = [c0]
                is_valid_cluster = True
                for i in range(1, n_targets):
                    found_match = None
                    for ci in matches_per_target[i]:
                        if ci in cluster:
                            continue
                        ci_pos = pos_cache[ci]
                        distances_match = True
                        for k in range(i):
                            expected_dist = target_dist_matrix[i][k]
                            actual_dist = get_dist(ci_pos, pos_cache[cluster[k]])
                            tol = max(0.01, expected_dist * 0.05)
                            if abs(actual_dist - expected_dist) > tol:
                                distances_match = False
                                break
                        if distances_match:
                            found_match = ci
                            break
                    if found_match:
                        cluster.append(found_match)
                    else:
                        is_valid_cluster = False
                        break
                if is_valid_cluster:
                    final_selection.update(cluster)

        if final_selection:
            cmds.select(list(final_selection), replace=True)
            cmds.inViewMessage(amg="Found {} meshes (Mode: {})".format(len(final_selection), mode), pos='midCenter', fade=True)
        else:
            cmds.warning("No matching clusters found with this layout.")
            cmds.select(clear=True)

    def toggle_find_sim_mode(self, pos=None):
        if getattr(self, 'find_sim_mode', 'SIM') == 'SIM':
            self.find_sim_mode = 'ALL'
            self.btn_fs.setText(bg_l10n.text("Find All"))
            self.btn_fs.setStyleSheet("background-color: #27ae60; font-weight: bold;")
            cmds.inViewMessage(amg="Mode: Find ALL (Ignores Layout)", pos='midCenter', fade=True)
        else:
            self.find_sim_mode = 'SIM'
            self.btn_fs.setText(bg_l10n.text("Find Sim"))
            self.btn_fs.setStyleSheet("background-color: #3b5998; font-weight: bold;")
            cmds.inViewMessage(amg="Mode: Find SIM (Strict Layout Matching)", pos='midCenter', fade=True)


# ============================================================================
# TABLE OF CONTENTS MIXIN
# ============================================================================
class TOCMixin:
    """Methods for the right panel table of contents (books, chapters, visibility)."""

    def _add_eye_widget(self, tree_item, is_vis, callback, column=1):
        eye_btn = QtWidgets.QPushButton()
        eye_btn.setFixedSize(17, 17)
        eye_btn.setFlat(True)
        eye_btn.setCursor(QtCore.Qt.PointingHandCursor)
        eye_btn.setToolTip(bg_l10n.tooltip("Toggle visibility"))
        eye_btn.setStatusTip(bg_l10n.tooltip("Toggle visibility"))
        eye_btn.setStyleSheet("background: transparent; border: none;")
        icon_path = os.path.join(os.path.dirname(__file__), 'open_eye.png' if is_vis else 'close_eye.png')
        if os.path.exists(icon_path):
            eye_btn.setIcon(QtGui.QIcon(icon_path))
            eye_btn.setIconSize(QtCore.QSize(18, 18))
        eye_btn.clicked.connect(lambda: callback(eye_btn))
        self.toc_tree.setItemWidget(tree_item, column, eye_btn)
        return eye_btn

    def toggle_chapter_visibility(self, pair_id, btn):
        pair = next((p for p in self.root_pairs if p['id'] == pair_id), None)
        if not pair:
            return
        hp_node, lp_node, _ = self.core.resolve_main_nodes(pair)
        is_vis = False
        if hp_node and cmds.objExists(hp_node):
            is_vis = is_vis or cmds.getAttr(hp_node + ".visibility")
        if lp_node and cmds.objExists(lp_node):
            is_vis = is_vis or cmds.getAttr(lp_node + ".visibility")
        new_state = not is_vis
        for p_node in [hp_node, lp_node]:
            if p_node and cmds.objExists(p_node):
                cmds.setAttr(p_node + ".visibility", new_state)
                if new_state:
                    children = cmds.listRelatives(p_node, children=True, type='transform', fullPath=True) or []
                    for child in children:
                        cmds.setAttr("{}.visibility".format(child), True)
        icon_path = os.path.join(os.path.dirname(__file__), 'open_eye.png' if new_state else 'close_eye.png')
        if os.path.exists(icon_path):
            btn.setIcon(QtGui.QIcon(icon_path))
        self.refresh_right_panel()
        if pair_id == getattr(self, 'active_root_id', None):
            self.sync_toggle_buttons(hp_node, lp_node)
            self.refresh_left_panel()

    def toggle_book_visibility(self, book_name, btn):
        pairs = [p for p in self.root_pairs if p.get('book') == book_name]
        is_vis = False
        for p in pairs:
            hp, lp, _ = self.core.resolve_main_nodes(p)
            if hp and cmds.objExists(hp) and cmds.getAttr(hp + ".visibility"):
                is_vis = True
            if lp and cmds.objExists(lp) and cmds.getAttr(lp + ".visibility"):
                is_vis = True
        new_state = not is_vis
        active_updated = False
        for p in pairs:
            hp, lp, _ = self.core.resolve_main_nodes(p)
            for p_node in [hp, lp]:
                if p_node and cmds.objExists(p_node):
                    cmds.setAttr(p_node + ".visibility", new_state)
                    if new_state:
                        children = cmds.listRelatives(p_node, children=True, type='transform', fullPath=True) or []
                        for child in children:
                            cmds.setAttr("{}.visibility".format(child), True)
            if p['id'] == getattr(self, 'active_root_id', None):
                self.sync_toggle_buttons(hp, lp)
                active_updated = True
        icon_path = os.path.join(os.path.dirname(__file__), 'open_eye.png' if new_state else 'close_eye.png')
        if os.path.exists(icon_path):
            btn.setIcon(QtGui.QIcon(icon_path))
        self.refresh_right_panel()
        if active_updated:
            self.refresh_left_panel()

    def refresh_right_panel(self):
        try:
            toc_tree = getattr(self, 'toc_tree', None)
            if not toc_tree:
                return
            toc_tree.objectName()
        except RuntimeError:
            return

        toc_tree.blockSignals(True)
        scroll_bar = toc_tree.verticalScrollBar()
        saved_scroll = scroll_bar.value() if scroll_bar else 0
        selected_keys = set()
        for item in toc_tree.selectedItems():
            data = item.data(0, QtCore.Qt.UserRole)
            if data == "BOOK":
                selected_keys.add(("BOOK", item.data(1, QtCore.Qt.UserRole)))
            else:
                selected_keys.add(("PAIR", data))

        expanded_states = {}
        for i in range(toc_tree.topLevelItemCount()):
            item = toc_tree.topLevelItem(i)
            if item.data(0, QtCore.Qt.UserRole) == "BOOK":
                book_name = item.data(1, QtCore.Qt.UserRole)
                expanded_states[book_name] = item.isExpanded()

        toc_tree.clear()
        any_healed = False
        books_dict = {}
        for pair in self.root_pairs:
            hp_node, lp_node, healed = self.core.resolve_main_nodes(pair)
            if healed:
                any_healed = True
            b = pair.get('book', '')
            if b not in books_dict:
                books_dict[b] = []
            books_dict[b].append(pair)

        for b_name in sorted(books_dict.keys(), key=lambda x: x.lower()):
            pairs = sorted(books_dict[b_name], key=lambda x: x.get('base', '').lower())
            parent_item = toc_tree.invisibleRootItem()
            if b_name:
                book_item = QtWidgets.QTreeWidgetItem()
                book_item.setText(0, b_name)
                book_item.setData(0, QtCore.Qt.UserRole, "BOOK")
                book_item.setData(1, QtCore.Qt.UserRole, b_name)
                book_item.setFlags(book_item.flags() | QtCore.Qt.ItemIsEditable)
                toc_tree.addTopLevelItem(book_item)
                if ("BOOK", b_name) in selected_keys:
                    book_item.setSelected(True)
                book_is_vis = False
                for p in pairs:
                    hp, lp, _ = self.core.resolve_main_nodes(p)
                    if (hp and cmds.objExists(hp) and cmds.getAttr(hp + ".visibility")) or \
                       (lp and cmds.objExists(lp) and cmds.getAttr(lp + ".visibility")):
                        book_is_vis = True
                        break
                self._add_eye_widget(book_item, book_is_vis,
                                     lambda btn, b=b_name: self.toggle_book_visibility(b, btn),
                                     column=1)
                parent_item = book_item
                is_expanded = expanded_states.get(b_name, True)
                book_item.setExpanded(is_expanded)

            for p in pairs:
                hp_node, lp_node, _ = self.core.resolve_main_nodes(p)
                text = p.get('base', 'Unknown')
                if not hp_node or not lp_node:
                    text += " (Lost)"
                pair_item = QtWidgets.QTreeWidgetItem()
                pair_item.setText(0, text)
                pair_item.setData(0, QtCore.Qt.UserRole, p['id'])
                if ("PAIR", p['id']) in selected_keys:
                    pair_item.setSelected(True)
                if not hp_node or not lp_node:
                    pair_item.setForeground(0, QtGui.QColor("#ff5555"))
                elif self.active_root_id == p['id']:
                    color = "#ffe555" if self.is_isolated else "#ffffff"
                    pair_item.setForeground(0, QtGui.QColor(color))
                    font = pair_item.font(0)
                    font.setBold(True)
                    pair_item.setFont(0, font)
                parent_item.addChild(pair_item)
                is_vis = True
                if hp_node and cmds.objExists(hp_node):
                    is_vis = cmds.getAttr(hp_node + ".visibility")
                elif lp_node and cmds.objExists(lp_node):
                    is_vis = cmds.getAttr(lp_node + ".visibility")
                self._add_eye_widget(pair_item, is_vis,
                                     lambda btn, p_id=p['id']: self.toggle_chapter_visibility(p_id, btn),
                                     column=1)

        toc_tree.blockSignals(False)
        def restore_toc_scroll():
            try:
                toc = getattr(self, 'toc_tree', None)
                if not toc:
                    return
                toc.objectName()
                bar = toc.verticalScrollBar()
                if bar:
                    bar.setValue(min(saved_scroll, bar.maximum()))
            except RuntimeError:
                return

        restore_toc_scroll()
        QtCore.QTimer.singleShot(0, restore_toc_scroll)
        if any_healed:
            bg_core.BakeSessionModel.save(self.root_pairs)
        if hasattr(self, 'gt_widget'):
            self.gt_widget.refresh_labels()

    def on_toc_clicked(self, item, col):
        if col == 1:
            return
        chapter_id = item.data(0, QtCore.Qt.UserRole)
        if chapter_id and chapter_id != "BOOK":
            pair = next((p for p in self.root_pairs if p['id'] == chapter_id), None)
            if pair:
                self.activate_root(pair)

    def on_toc_double_clicked(self, item, column):
        data = item.data(0, QtCore.Qt.UserRole)
        if data == "BOOK":
            if column == 0:
                self.toc_tree.editItem(item, column)
            return
        pair = next((p for p in self.root_pairs if p['id'] == item.data(0, QtCore.Qt.UserRole)), None)
        if pair:
            self.select_root_contents(pair)

    def on_toc_item_changed(self, item, col):
        if item.data(0, QtCore.Qt.UserRole) == "BOOK":
            new_name = item.text(0)
            old_name = item.data(1, QtCore.Qt.UserRole)
            if new_name != old_name:
                for p in self.root_pairs:
                    if p.get('book') == old_name:
                        p['book'] = new_name
                item.setData(1, QtCore.Qt.UserRole, new_name)
                bg_core.BakeSessionModel.save(self.root_pairs)
                self.log("Book renamed to: {}".format(new_name), "lightgreen")

    def group_selected_into_book(self):
        items = self.toc_tree.selectedItems()
        if not items:
            return
        pair_ids = []
        for item in items:
            if item.data(0, QtCore.Qt.UserRole) == "BOOK":
                for i in range(item.childCount()):
                    pair_ids.append(item.child(i).data(0, QtCore.Qt.UserRole))
            else:
                pair_ids.append(item.data(0, QtCore.Qt.UserRole))
        if not pair_ids:
            return
        idx = 1
        existing_books = [p.get('book') for p in self.root_pairs if p.get('book')]
        while "Book_{:02d}".format(idx) in existing_books:
            idx += 1
        new_book_name = "Book_{:02d}".format(idx)
        for p in self.root_pairs:
            if p['id'] in pair_ids:
                p['book'] = new_book_name
        bg_core.BakeSessionModel.save(self.root_pairs)
        self.refresh_right_panel()

    def add_selected_to_existing_book(self, target_book_name):
        items = self.toc_tree.selectedItems()
        if not items:
            return
        pair_ids = []
        for item in items:
            if item.data(0, QtCore.Qt.UserRole) == "BOOK":
                for i in range(item.childCount()):
                    pair_ids.append(item.child(i).data(0, QtCore.Qt.UserRole))
            else:
                pair_ids.append(item.data(0, QtCore.Qt.UserRole))
        if not pair_ids:
            return
        changed = False
        for p in self.root_pairs:
            if p['id'] in pair_ids:
                if p.get('book') != target_book_name:
                    p['book'] = target_book_name
                    changed = True
        if changed:
            bg_core.BakeSessionModel.save(self.root_pairs)
            self.refresh_right_panel()
            self.log("Added selected items to: {}".format(target_book_name), "lightgreen")

    def show_toc_context_menu(self, pos):
        items = self.toc_tree.selectedItems()
        if not items:
            return
        menu = QtWidgets.QMenu()
        menu.setStyleSheet("QMenu { background-color: #242424; color: #ddd; border: 1px solid #444; } QMenu::item:selected { background-color: #3e3e3e; }")

        act_sel = QAction("Select Meshes", self)
        act_sel.triggered.connect(self.select_toc_items)
        menu.addAction(act_sel)

        act_group = QAction("Group into Book (Ctrl+G)", self)
        act_group.triggered.connect(self.group_selected_into_book)
        menu.addAction(act_group)

        existing_books = sorted(list(set([p.get('book') for p in self.root_pairs if p.get('book')])))
        if existing_books:
            add_to_menu = menu.addMenu("Add to")
            add_to_menu.setStyleSheet("QMenu { background-color: #242424; color: #ddd; border: 1px solid #444; } QMenu::item:selected { background-color: #3e3e3e; }")
            for b_name in existing_books:
                action = QAction(b_name, self)
                action.triggered.connect(lambda checked=False, b=b_name: self.add_selected_to_existing_book(b))
                add_to_menu.addAction(action)

        act_ungroup = QAction("Extract from the book", self)
        act_ungroup.triggered.connect(self.ungroup_toc_items)
        menu.addAction(act_ungroup)

        menu.addSeparator()
        act_del = QAction("Delete Selection", self)
        act_del.triggered.connect(self.delete_toc_items)
        menu.addAction(act_del)

        bg_l10n.localize_menu(menu)
        menu.exec_(self.toc_tree.viewport().mapToGlobal(pos))

    def ungroup_toc_items(self):
        items = self.toc_tree.selectedItems()
        if not items:
            return
        changed = False
        for item in items:
            if item.data(0, QtCore.Qt.UserRole) == "BOOK":
                continue
            pair = next((p for p in self.root_pairs if p['id'] == item.data(0, QtCore.Qt.UserRole)), None)
            if pair and pair.get('book'):
                pair['book'] = ""
                changed = True
        if changed:
            bg_core.BakeSessionModel.save(self.root_pairs)
            self.refresh_right_panel()

    def select_toc_items(self):
        items = self.toc_tree.selectedItems()
        to_select = []
        for item in items:
            if item.data(0, QtCore.Qt.UserRole) == "BOOK":
                for i in range(item.childCount()):
                    p_id = item.child(i).data(0, QtCore.Qt.UserRole)
                    p = next((x for x in self.root_pairs if x['id'] == p_id), None)
                    if p:
                        hp_node, lp_node, _ = self.core.resolve_main_nodes(p)
                        if hp_node and cmds.objExists(hp_node):
                            to_select.extend(cmds.listRelatives(hp_node, children=True, fullPath=True) or [])
                        if lp_node and cmds.objExists(lp_node):
                            to_select.extend(cmds.listRelatives(lp_node, children=True, fullPath=True) or [])
            else:
                p = next((x for x in self.root_pairs if x['id'] == item.data(0, QtCore.Qt.UserRole)), None)
                if p:
                    hp_node, lp_node, _ = self.core.resolve_main_nodes(p)
                    if hp_node and cmds.objExists(hp_node):
                        to_select.extend(cmds.listRelatives(hp_node, children=True, fullPath=True) or [])
                    if lp_node and cmds.objExists(lp_node):
                        to_select.extend(cmds.listRelatives(lp_node, children=True, fullPath=True) or [])
        if to_select:
            cmds.select(to_select, replace=True)

    def delete_toc_items(self):
        items = self.toc_tree.selectedItems()
        if not items:
            return
        if not self.confirm_action("Remove selected from manager? (Nodes will NOT be deleted)"):
            return
        ids_to_remove = []
        for item in items:
            if item.data(0, QtCore.Qt.UserRole) == "BOOK":
                for i in range(item.childCount()):
                    ids_to_remove.append(item.child(i).data(0, QtCore.Qt.UserRole))
            else:
                ids_to_remove.append(item.data(0, QtCore.Qt.UserRole))
        self.root_pairs = [p for p in self.root_pairs if p.get('id') not in ids_to_remove]
        if self.active_root_id in ids_to_remove:
            self.active_root_id = None
            self.active_subgroup_name = None
            self.is_isolated = False
            self.refresh_left_panel()
        bg_core.BakeSessionModel.save(self.root_pairs)
        self.refresh_right_panel()

    def select_root_contents(self, pair):
        hp_node, lp_node, _ = self.core.resolve_main_nodes(pair)
        to_select = []
        if hp_node and cmds.objExists(hp_node):
            to_select.extend(cmds.listRelatives(hp_node, children=True, fullPath=True) or [])
        if lp_node and cmds.objExists(lp_node):
            to_select.extend(cmds.listRelatives(lp_node, children=True, fullPath=True) or [])
        if to_select:
            cmds.select(to_select, replace=True)
