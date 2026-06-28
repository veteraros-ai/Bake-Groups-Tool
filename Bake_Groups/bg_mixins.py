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
            'ignore_floaters': self.chk_ignore_floaters.isChecked(),
            'material_slots': bool(getattr(self, 'chk_material_slots', None) and self.chk_material_slots.isChecked())
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

    def _duplicate_check_shape(self, mesh_transform):
        shapes = cmds.listRelatives(mesh_transform, shapes=True, fullPath=True, type='mesh') or []
        for shape in shapes:
            try:
                if not cmds.getAttr(shape + ".intermediateObject"):
                    return shape
            except Exception:
                continue
        return None

    def _duplicate_check_dag_path(self, mesh_shape):
        selection = om.MSelectionList()
        try:
            selection.add(mesh_shape)
            return selection.getDagPath(0)
        except Exception:
            return None

    def _combined_check_mesh_shells(self, mesh_transform):
        shape = self._duplicate_check_shape(mesh_transform)
        if not shape:
            return None
        dag_path = self._duplicate_check_dag_path(shape)
        if not dag_path:
            return None

        mesh_fn = om.MFnMesh(dag_path)
        face_count = mesh_fn.numPolygons
        vertex_count = mesh_fn.numVertices
        if face_count <= 0 or vertex_count <= 0:
            return None

        points = mesh_fn.getPoints(om.MSpace.kWorld)
        counts, connects = mesh_fn.getVertices()
        face_vertices = []
        vertex_to_faces = {}
        index = 0
        for face_id, count in enumerate(counts):
            verts = list(connects[index:index + count])
            index += count
            face_vertices.append(verts)
            for vertex_id in verts:
                vertex_to_faces.setdefault(vertex_id, []).append(face_id)

        visited = set()
        shells = []
        for start_face in range(len(face_vertices)):
            if start_face in visited:
                continue
            stack = [start_face]
            visited.add(start_face)
            shell_faces = []
            shell_vertices = set()
            while stack:
                face_id = stack.pop()
                shell_faces.append(face_id)
                for vertex_id in face_vertices[face_id]:
                    shell_vertices.add(vertex_id)
                    for next_face in vertex_to_faces.get(vertex_id, []):
                        if next_face not in visited:
                            visited.add(next_face)
                            stack.append(next_face)
            if not shell_vertices:
                continue
            xs = [points[v].x for v in shell_vertices]
            ys = [points[v].y for v in shell_vertices]
            zs = [points[v].z for v in shell_vertices]
            mn = [min(xs), min(ys), min(zs)]
            mx = [max(xs), max(ys), max(zs)]
            size = [mx[i] - mn[i] for i in range(3)]
            diag = math.sqrt(size[0] * size[0] + size[1] * size[1] + size[2] * size[2])
            vol = max(size[0] * size[1] * size[2], 1e-6)
            shells.append({
                "faces": len(shell_faces),
                "vertices": len(shell_vertices),
                "bbox": [mn[0], mn[1], mn[2], mx[0], mx[1], mx[2]],
                "diag": diag,
                "volume": vol
            })

        if not shells:
            return None

        try:
            overall_bbox = cmds.exactWorldBoundingBox(mesh_transform)
        except Exception:
            overall_bbox = cmds.xform(mesh_transform, query=True, boundingBox=True, worldSpace=True) or []
        if overall_bbox:
            overall_size = [
                abs(overall_bbox[3] - overall_bbox[0]),
                abs(overall_bbox[4] - overall_bbox[1]),
                abs(overall_bbox[5] - overall_bbox[2])
            ]
        else:
            xs = [point.x for point in points]
            ys = [point.y for point in points]
            zs = [point.z for point in points]
            overall_size = [max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)]
        overall_diag = math.sqrt(overall_size[0] * overall_size[0] + overall_size[1] * overall_size[1] + overall_size[2] * overall_size[2])
        overall_volume = max(overall_size[0] * overall_size[1] * overall_size[2], 1e-6)
        min_faces = max(6, int(face_count * 0.0002))
        meaningful_shells = [shell for shell in shells if shell.get("faces", 0) >= min_faces]
        max_shell_diag = max([shell.get("diag", 0.0) for shell in meaningful_shells] or [overall_diag, 1e-6])
        shell_volume_sum = sum(shell.get("volume", 0.0) for shell in meaningful_shells)
        spread_ratio = overall_diag / max(max_shell_diag, 1e-6)
        fill_ratio = min(shell_volume_sum / overall_volume, 1.0)
        return {
            "shell_count": len(shells),
            "meaningful_shell_count": len(meaningful_shells),
            "face_count": face_count,
            "vertex_count": vertex_count,
            "spread_ratio": spread_ratio,
            "fill_ratio": fill_ratio
        }

    def _combined_check_cache_key(self, mesh_transform):
        shape = self._duplicate_check_shape(mesh_transform)
        if not shape:
            return None, None
        try:
            shape_uuid = (cmds.ls(shape, uuid=True) or [shape])[0]
        except Exception:
            shape_uuid = shape
        try:
            vertex_count = int(cmds.polyEvaluate(mesh_transform, vertex=True) or 0)
            face_count = int(cmds.polyEvaluate(mesh_transform, face=True) or 0)
        except Exception:
            vertex_count = 0
            face_count = 0
        try:
            bbox = cmds.exactWorldBoundingBox(mesh_transform)
        except Exception:
            bbox = cmds.xform(mesh_transform, query=True, boundingBox=True, worldSpace=True) or []
        if not bbox:
            bbox = [0.0] * 6
        bbox_key = tuple(int(round(float(value) * 10000.0)) for value in bbox[:6])
        return shape_uuid, (shape_uuid, vertex_count, face_count, bbox_key)

    def _combined_check_mesh_shells_cached(self, mesh_transform):
        long_node = (cmds.ls(mesh_transform, long=True) or [mesh_transform])[0]
        cache_id, cache_key = self._combined_check_cache_key(long_node)
        if not cache_id or not cache_key:
            return None, "miss"

        cache = getattr(self, '_combined_mesh_check_cache', None)
        if cache is None or not isinstance(cache, dict):
            cache = {}
            self._combined_mesh_check_cache = cache

        cached = cache.get(cache_id)
        if cached and cached.get("key") == cache_key:
            cached["path"] = long_node
            return cached.get("info"), "hit"

        status = "stale" if cached else "miss"
        info = self._combined_check_mesh_shells(long_node)
        cache[cache_id] = {
            "key": cache_key,
            "info": info,
            "path": long_node,
            "name": long_node.split('|')[-1]
        }
        return info, status

    def _find_combined_mesh_candidates_under_root(self, root_node, mesh_transforms=None, progress_callback=None):
        candidates = []
        stats = {"hit": 0, "miss": 0, "stale": 0}
        for mesh_transform in (mesh_transforms if mesh_transforms is not None else self._mesh_transforms_under_root(root_node)):
            if progress_callback and progress_callback(mesh_transform):
                return None, stats
            try:
                info, cache_status = self._combined_check_mesh_shells_cached(mesh_transform)
                stats[cache_status] = stats.get(cache_status, 0) + 1
            except Exception:
                continue
            if not info:
                continue
            if info.get("meaningful_shell_count", 0) < 2:
                continue
            if (info.get("spread_ratio", 1.0) >= 1.35 or
                    info.get("fill_ratio", 1.0) <= 0.75 or
                    info.get("meaningful_shell_count", 0) >= 3):
                long_node = (cmds.ls(mesh_transform, long=True) or [mesh_transform])[0]
                candidates.append((long_node, info))
        return candidates, stats

    def _confirm_combined_meshes_before_hp_analysis(self, hp_main, lp_main=None):
        progress_dlg = None
        progress_step = [0]
        chapter_id = getattr(self, 'active_root_id', None)
        skipped_chapters = getattr(self, '_combined_check_skipped_chapters', None)
        if skipped_chapters is None:
            skipped_chapters = set()
            self._combined_check_skipped_chapters = skipped_chapters
        if chapter_id and chapter_id in skipped_chapters:
            self.log(bg_l10n.text("Combined mesh check skipped for this chapter."), "lightblue")
            return True

        def _make_progress(total):
            dlg = QtWidgets.QProgressDialog(bg_l10n.text("Checking combined meshes..."), bg_l10n.text("Cancel"), 0, max(int(total), 1), self)
            dlg.setWindowModality(QtCore.Qt.WindowModal)
            dlg.setMinimumDuration(0)
            dlg.setValue(0)
            return dlg

        def _update_progress(mesh_transform):
            if not progress_dlg:
                return False
            progress_step[0] += 1
            progress_dlg.setLabelText(
                bg_l10n.text("Checking combined meshes: {name}").format(name=mesh_transform.split('|')[-1])
            )
            progress_dlg.setValue(min(progress_step[0], progress_dlg.maximum()))
            progress_dlg.repaint()
            QtWidgets.QApplication.processEvents()
            return progress_dlg.wasCanceled()

        def _close_progress():
            if progress_dlg:
                progress_dlg.close()

        try:
            roots = [("HP", hp_main)]
            if lp_main and cmds.objExists(lp_main):
                roots.append(("LP", lp_main))

            root_meshes = []
            total_meshes = 0
            for label, root_node in roots:
                if not root_node or not cmds.objExists(root_node):
                    continue
                mesh_transforms = self._mesh_transforms_under_root(root_node)
                root_meshes.append((label, root_node, mesh_transforms))
                total_meshes += len(mesh_transforms)

            if total_meshes:
                progress_dlg = _make_progress(total_meshes)

            single_roots = []
            combined = []
            cache_stats_total = {"hit": 0, "miss": 0, "stale": 0}
            for label, root_node, mesh_transforms in root_meshes:
                if len(mesh_transforms) == 1:
                    single_roots.append((label, mesh_transforms[0]))
                result = self._find_combined_mesh_candidates_under_root(root_node, mesh_transforms, _update_progress)
                if result is None or result[0] is None:
                    _close_progress()
                    self.log(bg_l10n.text("Combined mesh check canceled."), "orange")
                    return False
                candidates, cache_stats = result
                for key, value in (cache_stats or {}).items():
                    cache_stats_total[key] = cache_stats_total.get(key, 0) + int(value or 0)
                if candidates:
                    combined.append((label, candidates))
        except Exception as exc:
            _close_progress()
            message = bg_l10n.text("Combined mesh check failed: {error}").format(error=exc)
            cmds.warning(message)
            self.log(message, "orange")
            return True
        finally:
            _close_progress()

        cache_hits = cache_stats_total.get("hit", 0)
        cache_misses = cache_stats_total.get("miss", 0)
        cache_stale = cache_stats_total.get("stale", 0)
        if cache_hits or cache_stale:
            self.log(
                bg_l10n.text("Combined mesh cache: cached={cached}, calculated={calculated}, stale={stale}.").format(
                    cached=cache_hits,
                    calculated=cache_misses + cache_stale,
                    stale=cache_stale
                ),
                "lightblue" if cache_hits else "orange"
            )

        if not single_roots and not combined:
            return True

        problem_nodes = []
        seen = set()
        preview_lines = []
        if single_roots:
            preview_lines.append(bg_l10n.text("Single mesh root candidates:"))
            for label, node in single_roots:
                if node not in seen and cmds.objExists(node):
                    problem_nodes.append(node)
                    seen.add(node)
                preview_lines.append("  {}: {}".format(label, node.split('|')[-1]))
        if combined:
            preview_lines.append(bg_l10n.text("Combined mesh candidates:"))
            for label, candidates in combined:
                preview_lines.append(
                    bg_l10n.text("{label}: {count} combined mesh candidate(s)").format(
                        label=label,
                        count=len(candidates)
                    )
                )
                for node, info in candidates[:8]:
                    if node not in seen and cmds.objExists(node):
                        problem_nodes.append(node)
                        seen.add(node)
                    preview_lines.append(
                        "  {} | shells={} | spread={:.2f} | fill={:.2f}".format(
                            node.split('|')[-1],
                            info.get("meaningful_shell_count", 0),
                            info.get("spread_ratio", 0.0),
                            info.get("fill_ratio", 0.0)
                        )
                    )
                if len(candidates) > 8:
                    preview_lines.append("  ... +{} more".format(len(candidates) - 8))

        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(bg_l10n.text("Combined Meshes Found"))
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setText(bg_l10n.text("Combined mesh candidates were found before Analyze HP."))
        box.setInformativeText(
            "{}\n\n{}".format(
                bg_l10n.text("These meshes may need to be separated before analysis. Select them, separate them now, or skip this warning and continue."),
                "\n".join(preview_lines)
            )
        )
        select_btn = box.addButton(bg_l10n.text("Select"), QtWidgets.QMessageBox.ActionRole)
        separate_btn = box.addButton(bg_l10n.text("Separate"), QtWidgets.QMessageBox.ActionRole)
        skip_chapter_btn = box.addButton(bg_l10n.text("Skip This Chapter"), QtWidgets.QMessageBox.ActionRole)
        skip_btn = box.addButton(bg_l10n.text("Skip"), QtWidgets.QMessageBox.AcceptRole)
        bg_l10n.localize_widget_tree(box)
        box.setDefaultButton(select_btn)
        box.setWindowModality(QtCore.Qt.ApplicationModal)
        box.raise_()
        box.activateWindow()
        box.exec_()

        if box.clickedButton() == select_btn:
            if problem_nodes:
                cmds.select(problem_nodes, replace=True)
                message = bg_l10n.text("Analyze HP paused: selected {count} combined mesh candidate(s).").format(
                    count=len(problem_nodes)
                )
                self.log(message, "orange")
                cmds.warning(message)
            if hasattr(self, 'record_user_action'):
                self.record_user_action(
                    "Combined mesh check failed",
                    "Analyze HP | candidates={}".format(len(problem_nodes))
                )
            return False

        if box.clickedButton() == separate_btn:
            separated = self._separate_mesh_transforms(problem_nodes, select_result=True)
            if separated:
                message = bg_l10n.text("Combined mesh cleanup: separated {count} mesh part(s).").format(
                    count=len(separated)
                )
                self.log(message, "lightgreen")
                cmds.warning(message)
                if hasattr(self, 'record_user_action'):
                    self.record_user_action(
                        "Separate Combined Meshes",
                        "parts={}".format(len(separated))
                    )
                return True
            self.log(bg_l10n.text("Combined mesh cleanup: nothing was separated."), "orange")
            return False

        if box.clickedButton() == skip_chapter_btn:
            if chapter_id:
                skipped_chapters.add(chapter_id)
            self.log(bg_l10n.text("Combined mesh check skipped for this chapter."), "lightblue")
            if hasattr(self, 'record_user_action'):
                self.record_user_action("Skip Combined Mesh Check", "chapter={}".format(chapter_id or "unknown"))
            return True

        return box.clickedButton() == skip_btn

    def _duplicate_check_bbox_key(self, mesh_transform, tolerance):
        try:
            values = cmds.exactWorldBoundingBox(mesh_transform)
        except Exception:
            values = cmds.xform(mesh_transform, query=True, boundingBox=True, worldSpace=True) or []
        if not values:
            return None
        scale = 1.0 / max(float(tolerance), 0.000001)
        return tuple(int(round(float(value) * scale)) for value in values)

    def _duplicate_check_meshes_identical(self, data_a, data_b, tolerance):
        points_a = data_a.get('points')
        points_b = data_b.get('points')
        if points_a is None or points_b is None or len(points_a) != len(points_b):
            return False
        for point_a, point_b in zip(points_a, points_b):
            if (abs(point_a.x - point_b.x) > tolerance or
                    abs(point_a.y - point_b.y) > tolerance or
                    abs(point_a.z - point_b.z) > tolerance):
                return False
        return True

    def _duplicate_check_connected_components(self, adjacency):
        visited = set()
        components = []
        for node in adjacency.keys():
            if node in visited:
                continue
            stack = [node]
            component = []
            while stack:
                current = stack.pop()
                if current in visited:
                    continue
                visited.add(current)
                component.append(current)
                for neighbor in adjacency.get(current, []):
                    if neighbor not in visited:
                        stack.append(neighbor)
            components.append(component)
        return components

    def _find_duplicate_mesh_groups_under_root(self, root_node, tolerance=0.001):
        if not root_node or not cmds.objExists(root_node):
            return []

        mesh_transforms = self._mesh_transforms_under_root(root_node)
        buckets = {}
        mesh_data = {}
        for mesh_transform in mesh_transforms:
            shape = self._duplicate_check_shape(mesh_transform)
            if not shape:
                continue
            dag_path = self._duplicate_check_dag_path(shape)
            if not dag_path:
                continue
            try:
                mesh_fn = om.MFnMesh(dag_path)
                vertex_count = mesh_fn.numVertices
                polygon_count = mesh_fn.numPolygons
                if vertex_count <= 0:
                    continue
                bbox_key = self._duplicate_check_bbox_key(mesh_transform, tolerance)
                if bbox_key is None:
                    continue
                long_node = (cmds.ls(mesh_transform, long=True) or [mesh_transform])[0]
                mesh_data[long_node] = {
                    'points': mesh_fn.getPoints(om.MSpace.kWorld)
                }
                buckets.setdefault((vertex_count, polygon_count, bbox_key), []).append(long_node)
            except Exception:
                continue

        duplicate_groups = []
        for bucket in buckets.values():
            if len(bucket) < 2:
                continue
            adjacency = dict((node, []) for node in bucket)
            for index_a in range(len(bucket)):
                node_a = bucket[index_a]
                for index_b in range(index_a + 1, len(bucket)):
                    node_b = bucket[index_b]
                    if self._duplicate_check_meshes_identical(mesh_data[node_a], mesh_data[node_b], tolerance):
                        adjacency[node_a].append(node_b)
                        adjacency[node_b].append(node_a)
            for component in self._duplicate_check_connected_components(adjacency):
                if len(component) > 1:
                    duplicate_groups.append(component)
        return duplicate_groups

    def _remove_duplicate_mesh_copies(self, found):
        to_delete = []
        skipped = []
        kept = []
        seen_delete = set()
        for _label, groups in found:
            for group in groups:
                existing = [node for node in group if node and cmds.objExists(node)]
                if len(existing) < 2:
                    continue
                existing = sorted(existing, key=lambda node: (len(node), node.lower()))
                keep_node = existing[0]
                kept.append(keep_node)
                for node in existing[1:]:
                    if node in seen_delete:
                        continue
                    child_transforms = cmds.listRelatives(node, children=True, fullPath=True, type='transform') or []
                    if child_transforms:
                        skipped.append(node)
                        continue
                    seen_delete.add(node)
                    to_delete.append(node)

        removed = 0
        if to_delete:
            with bg_core.undo_chunk("RemoveDuplicateMeshes"):
                for node in to_delete:
                    if not cmds.objExists(node):
                        continue
                    try:
                        cmds.delete(node)
                        removed += 1
                    except Exception:
                        skipped.append(node)
        return removed, kept, skipped

    def _confirm_duplicate_meshes_before_hp_analysis(self, hp_main, lp_main=None):
        try:
            roots = [("HP", hp_main)]
            if lp_main and cmds.objExists(lp_main):
                roots.append(("LP", lp_main))

            found = []
            for label, root_node in roots:
                groups = self._find_duplicate_mesh_groups_under_root(root_node)
                if groups:
                    found.append((label, groups))
        except Exception as exc:
            message = bg_l10n.text("Duplicate mesh check failed: {error}").format(error=exc)
            cmds.warning(message)
            self.log(message, "orange")
            return True

        if not found:
            return True

        duplicate_meshes = []
        seen = set()
        preview_lines = []
        for label, groups in found:
            mesh_count = sum(len(group) for group in groups)
            preview_lines.append(
                bg_l10n.text("{label}: {groups} duplicate group(s), {meshes} mesh(es)").format(
                    label=label,
                    groups=len(groups),
                    meshes=mesh_count
                )
            )
            for group_index, group in enumerate(groups[:4], 1):
                names = [node.split('|')[-1] for node in group[:5]]
                if len(group) > 5:
                    names.append("... +{} more".format(len(group) - 5))
                preview_lines.append("  {} {}: {}".format(bg_l10n.text("Group"), group_index, ", ".join(names)))
            if len(groups) > 4:
                preview_lines.append("  ... +{} more".format(len(groups) - 4))
            for group in groups:
                for node in group:
                    if node not in seen and cmds.objExists(node):
                        duplicate_meshes.append(node)
                        seen.add(node)

        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(bg_l10n.text("Duplicate Meshes Found"))
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setText(bg_l10n.text("Duplicate meshes were found before Analyze HP."))
        box.setInformativeText(
            "{}\n\n{}".format(
                bg_l10n.text("Resolve duplicates before running Analyze HP. Select the found meshes, remove extra copies, or skip this warning and continue."),
                "\n".join(preview_lines)
            )
        )
        select_btn = box.addButton(bg_l10n.text("Select"), QtWidgets.QMessageBox.ActionRole)
        destructive_role = getattr(QtWidgets.QMessageBox, "DestructiveRole", QtWidgets.QMessageBox.ActionRole)
        remove_btn = box.addButton(bg_l10n.text("Remove Extra Copies"), destructive_role)
        skip_btn = box.addButton(bg_l10n.text("Skip"), QtWidgets.QMessageBox.AcceptRole)
        bg_l10n.localize_widget_tree(box)
        box.setDefaultButton(select_btn)
        box.exec_()

        if box.clickedButton() == select_btn:
            if duplicate_meshes:
                cmds.select(duplicate_meshes, replace=True)
                message = bg_l10n.text("Analyze HP paused: selected {count} duplicate mesh(es).").format(
                    count=len(duplicate_meshes)
                )
                self.log(message, "orange")
                cmds.warning(message)
            if hasattr(self, 'record_user_action'):
                self.record_user_action(
                    "Duplicate mesh check failed",
                    "Analyze HP | duplicates={}".format(len(duplicate_meshes))
                )
            return False

        if box.clickedButton() == remove_btn:
            removed, kept, skipped = self._remove_duplicate_mesh_copies(found)
            if removed:
                message = bg_l10n.text("Duplicate mesh cleanup: removed {removed} extra copy/copies, kept {kept}.").format(
                    removed=removed,
                    kept=len([node for node in kept if node and cmds.objExists(node)])
                )
                self.log(message, "lightgreen")
                cmds.warning(message)
                if hasattr(self, 'record_user_action'):
                    self.record_user_action(
                        "Remove Duplicate Meshes",
                        "removed={} | skipped={}".format(removed, len(skipped))
                    )
            else:
                self.log(bg_l10n.text("Duplicate mesh cleanup: nothing was removed."), "orange")
            if skipped:
                names = [node.split('|')[-1] for node in skipped[:8]]
                self.log(
                    bg_l10n.text("Duplicate mesh cleanup skipped {count} mesh(es): {names}").format(
                        count=len(skipped),
                        names=", ".join(names)
                    ),
                    "orange"
                )
            return True

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

        if not self._confirm_duplicate_meshes_before_hp_analysis(hp_main, lp_main):
            return

        if not self._confirm_zbrush_candidates_before_hp_analysis(hp_main):
            return

        if not self._confirm_combined_meshes_before_hp_analysis(hp_main, lp_main):
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
        enable_lp_material_slots = bool(worker_params.get('material_slots', False))
        material_progress_dlg = None
        material_progress_step = [0]

        def _make_material_progress(total):
            if not enable_lp_material_slots:
                return None
            dlg = QtWidgets.QProgressDialog(bg_l10n.text("Analyzing LP materials..."), bg_l10n.text("Cancel"), 0, max(int(total), 1), self)
            dlg.setWindowModality(QtCore.Qt.WindowModal)
            dlg.setMinimumDuration(0)
            dlg.setValue(0)
            dlg.show()
            QtWidgets.QApplication.processEvents()
            return dlg

        def _update_material_progress(label):
            if not material_progress_dlg:
                return False
            material_progress_step[0] += 1
            material_progress_dlg.setLabelText(label)
            material_progress_dlg.setValue(min(material_progress_step[0], material_progress_dlg.maximum()))
            QtWidgets.QApplication.processEvents()
            return material_progress_dlg.wasCanceled()

        def _close_material_progress():
            if material_progress_dlg:
                material_progress_dlg.close()
                QtWidgets.QApplication.processEvents()

        def _sg_material_node(sg):
            try:
                nodes = cmds.listConnections("{}.surfaceShader".format(sg), source=True, destination=False) or []
                if nodes:
                    return nodes[0]
            except Exception:
                pass
            return sg

        def _short_node(name):
            return str(name).split('|')[-1].split(':')[-1]

        def _lp_material_cache_key(lp_meshes):
            entries = []
            for mesh in lp_meshes:
                shapes = cmds.listRelatives(mesh, shapes=True, fullPath=True, type='mesh', noIntermediate=True) or []
                shape = shapes[0] if shapes else mesh
                try:
                    shape_uuid = (cmds.ls(shape, uuid=True) or [""])[0]
                except Exception:
                    shape_uuid = ""
                try:
                    face_count = cmds.polyEvaluate(mesh, face=True)
                except Exception:
                    face_count = 0
                sgs = []
                for sg in sorted(set(cmds.listConnections(shape, type='shadingEngine') or []), key=_short_node):
                    sgs.append((sg, _sg_material_node(sg)))
                entries.append((shape_uuid, int(face_count or 0), tuple(sgs)))
            return tuple(entries)

        def _connected_shader_records(lp_node, mesh_fn, dag, total_faces, include_faces):
            try:
                shaders, face_shader_indices = mesh_fn.getConnectedShaders(dag.instanceNumber())
            except Exception:
                return []
            records = []
            for shader_index, shader_obj in enumerate(shaders):
                try:
                    sg = om.MFnDependencyNode(shader_obj).name()
                except Exception:
                    continue
                material = _sg_material_node(sg)
                rec = {
                    "key": material or sg,
                    "slot": None,
                    "material": material,
                    "shading_engines": [sg],
                    "faces": []
                }
                if include_faces:
                    rec["faces"] = [
                        face_id for face_id, assigned_index in enumerate(face_shader_indices)
                        if int(assigned_index) == shader_index and face_id < total_faces
                    ]
                    if not rec["faces"]:
                        continue
                records.append(rec)
            return records

        def _material_faces_for_sg(mesh_transform, shape, sg, total_faces):
            members = cmds.sets(sg, q=True) or []
            mesh_components = []
            mesh_long = (cmds.ls(mesh_transform, long=True) or [mesh_transform])[0]
            shape_long = (cmds.ls(shape, long=True) or [shape])[0]
            for member in members:
                for item in (cmds.ls(member, long=True) or []):
                    if item == mesh_long or item == shape_long:
                        return list(range(total_faces))
                    if item.startswith(mesh_long + ".") or item.startswith(shape_long + "."):
                        mesh_components.append(item)
            if not mesh_components:
                return []
            try:
                faces = cmds.polyListComponentConversion(mesh_components, toFace=True)
                faces_flat = cmds.ls(faces, flatten=True, long=True) or []
            except Exception:
                return []
            result = set()
            for item in faces_flat:
                match = re.search(r'\.f\[(\d+)\]', item)
                if match:
                    result.add(int(match.group(1)))
            return sorted(result)

        def _collect_lp_material_context(lp_meshes):
            material_by_key = {}
            sg_to_key = {}
            for mesh in lp_meshes:
                if _update_material_progress("Scanning LP materials: {}".format(mesh.split('|')[-1])):
                    return None, None, None
                shapes = cmds.listRelatives(mesh, shapes=True, fullPath=True, type='mesh', noIntermediate=True) or []
                if not shapes:
                    continue
                records = []
                try:
                    sel = om.MSelectionList()
                    sel.add(mesh)
                    dag = sel.getDagPath(0)
                    if dag.hasFn(om.MFn.kTransform):
                        dag.extendToShape()
                    mesh_fn = om.MFnMesh(dag)
                    records = _connected_shader_records(mesh, mesh_fn, dag, mesh_fn.numPolygons, False)
                except Exception:
                    records = []
                if records:
                    for rec in records:
                        material = rec.get("material")
                        key = rec.get("key") or material
                        for sg in rec.get("shading_engines") or []:
                            sg_to_key[sg] = key
                        material_by_key.setdefault(key, {
                            "material": material,
                            "shading_engines": set()
                        })
                        for sg in rec.get("shading_engines") or []:
                            material_by_key[key]["shading_engines"].add(sg)
                    continue
                for sg in sorted(set(cmds.listConnections(shapes[0], type='shadingEngine') or []), key=_short_node):
                    material = _sg_material_node(sg)
                    key = material or sg
                    sg_to_key[sg] = key
                    material_by_key.setdefault(key, {
                        "material": material,
                        "shading_engines": set()
                    })
                    material_by_key[key]["shading_engines"].add(sg)
            ordered_keys = sorted(material_by_key.keys(), key=lambda k: (_short_node(material_by_key[k].get("material") or k).lower(), _short_node(k).lower()))
            slots_by_key = {}
            if len(ordered_keys) > 1:
                for index, key in enumerate(ordered_keys, 1):
                    slots_by_key[key] = "M{:02d}".format(index)
            return material_by_key, sg_to_key, slots_by_key

        lp_materials_by_key, lp_sg_to_material_key, lp_material_slots_by_key = {}, {}, {}
        use_lp_material_slots = False
        material_cache_key = None
        material_cached_records = None
        material_cache = getattr(self, '_lp_material_analysis_cache', None)
        if material_cache is None:
            material_cache = {}
            self._lp_material_analysis_cache = material_cache

        if enable_lp_material_slots:
            material_cache_key = _lp_material_cache_key(final_lp_meshes)
            material_cached_records = material_cache.get(material_cache_key)
            if material_cached_records:
                use_lp_material_slots = True
                for slot in material_cached_records.get("slots", []):
                    lp_material_slots_by_key[slot] = slot
            else:
                material_progress_dlg = _make_material_progress((len(final_lp_meshes) * 2) + 2)
                lp_materials_by_key, lp_sg_to_material_key, lp_material_slots_by_key = _collect_lp_material_context(final_lp_meshes)
                if material_progress_dlg and material_progress_dlg.wasCanceled():
                    _close_material_progress()
                    return self.log("Analyze HP material scan canceled.", "orange")
                use_lp_material_slots = bool(lp_material_slots_by_key)

        def _lp_material_records(lp_node, total_faces, mesh_fn=None, dag=None):
            shapes = cmds.listRelatives(lp_node, shapes=True, fullPath=True, type='mesh', noIntermediate=True) or []
            if not shapes:
                return []
            shape = shapes[0]
            records_by_key = {}
            fast_records = _connected_shader_records(lp_node, mesh_fn, dag, total_faces, True) if mesh_fn and dag else []
            if fast_records:
                for rec in fast_records:
                    key = rec.get("key")
                    if key not in lp_material_slots_by_key:
                        continue
                    rec["slot"] = lp_material_slots_by_key.get(key)
                    records_by_key.setdefault(key, {
                        "key": key,
                        "slot": rec.get("slot"),
                        "material": rec.get("material"),
                        "shading_engines": set(),
                        "faces": set()
                    })
                    records_by_key[key]["shading_engines"].update(rec.get("shading_engines") or [])
                    records_by_key[key]["faces"].update(rec.get("faces") or [])
                records = []
                for key, rec in records_by_key.items():
                    rec["faces"] = sorted(rec["faces"])
                    rec["shading_engines"] = sorted(rec["shading_engines"], key=_short_node)
                    records.append(rec)
                return sorted(records, key=lambda r: (r.get("slot") or "M00", _short_node(r.get("material") or r.get("key")).lower()))
            for sg in sorted(set(cmds.listConnections(shape, type='shadingEngine') or []), key=_short_node):
                key = lp_sg_to_material_key.get(sg) or _sg_material_node(sg) or sg
                faces = _material_faces_for_sg(lp_node, shape, sg, total_faces)
                if not faces:
                    continue
                info = lp_materials_by_key.get(key, {})
                rec = records_by_key.setdefault(key, {
                    "key": key,
                    "slot": lp_material_slots_by_key.get(key),
                    "material": info.get("material") or _sg_material_node(sg),
                    "shading_engines": set(),
                    "faces": set()
                })
                rec["shading_engines"].add(sg)
                rec["faces"].update(faces)
            records = []
            for key, rec in records_by_key.items():
                rec["faces"] = sorted(rec["faces"])
                rec["shading_engines"] = sorted(rec["shading_engines"], key=_short_node)
                records.append(rec)
            return sorted(records, key=lambda r: (r.get("slot") or "M00", _short_node(r.get("material") or r.get("key")).lower()))

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
                material_records = _lp_material_records(lp_node, len(counts), mesh_fn, dag) if use_lp_material_slots else []
                face_material = {}
                if material_records:
                    for rec in material_records:
                        for face_id in rec.get("faces", []):
                            face_material.setdefault(face_id, rec)

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

                    material_rec = face_material.get(start_face)
                    material_key = material_rec.get("key") if material_rec else None
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
                                next_rec = face_material.get(nf)
                                next_key = next_rec.get("key") if next_rec else None
                                if nf not in visited_faces and (not use_lp_material_slots or next_key == material_key):
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
                    material_slot = material_rec.get("slot") if material_rec else None
                    if material_slot:
                        shell_key = "{}::{}".format(shell_key, material_slot)
                    verts_flat = []
                    for v in shell_verts:
                        pnt = points[v]
                        verts_flat.extend([pnt.x, pnt.y, pnt.z])
                    if material_slot and shell_faces:
                        max_face_samples = 1500
                        face_step = max(1, int(len(shell_faces) / float(max_face_samples)))
                        for face_id in shell_faces[::face_step]:
                            verts = face_vertices[face_id]
                            if not verts:
                                continue
                            acc_x = acc_y = acc_z = 0.0
                            for v in verts:
                                pnt = points[v]
                                acc_x += pnt.x
                                acc_y += pnt.y
                                acc_z += pnt.z
                            inv = 1.0 / float(len(verts))
                            verts_flat.extend([acc_x * inv, acc_y * inv, acc_z * inv])

                    shell_data = {
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
                    }
                    if material_slot:
                        shell_data["material_slot"] = material_slot
                        shell_data["material_key"] = material_rec.get("key")
                        shell_data["material_name"] = _short_node(material_rec.get("material") or material_rec.get("key"))
                        shell_data["material_shading_engines"] = list(material_rec.get("shading_engines") or [])
                    shells.append((shell_key, shell_data, verts_flat))
                    shell_index += 1

                return shells
            except Exception as e:
                self.log("LP shell cache failed for {}: {}".format(lp_node.split('|')[-1], e), "orange")
                return []

        computed_material_records = []
        if material_cached_records:
            for shell_key, shell_data, shell_verts in material_cached_records.get("records", []):
                self.lp_data_cache[shell_key] = dict(shell_data)
                lp_prebuilt_verts_cache[shell_key] = list(shell_verts)
            self.log("Analyze HP: reused cached LP material slots.", "lightblue")
        else:
            for m in final_lp_meshes:
                if use_lp_material_slots and _update_material_progress("Building LP material cache: {}".format(m.split('|')[-1])):
                    _close_material_progress()
                    return self.log("Analyze HP material scan canceled.", "orange")
                shell_records = build_virtual_lp_shells_for_hp_worker(m)
                if shell_records and (len(shell_records) > 1 or use_lp_material_slots):
                    for shell_key, shell_data, shell_verts in shell_records:
                        self.lp_data_cache[shell_key] = shell_data
                        lp_prebuilt_verts_cache[shell_key] = shell_verts
                        if use_lp_material_slots:
                            computed_material_records.append((shell_key, dict(shell_data), list(shell_verts)))
                    continue

                data = bg_core.MeshDataManager.get_mesh_data(m)
                if data:
                    if cmds.objExists(m):
                        data["bbox"] = cmds.xform(m, q=True, ws=True, bb=True)
                    self.lp_data_cache[m] = data

        if use_lp_material_slots and computed_material_records and material_cache_key:
            material_cache[material_cache_key] = {
                "records": computed_material_records,
                "slots": sorted(set([data.get("material_slot") for _key, data, _verts in computed_material_records if data.get("material_slot")]))
            }

        if use_lp_material_slots:
            self.log("Analyze HP: detected {} LP material slot(s) for grouping.".format(len(lp_material_slots_by_key)), "lightblue")
        _close_material_progress()

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

    def _material_slot_from_group_name(self, name):
        match = re.match(r"^(M\d{2,})(?:_|\.)", str(name or "").split('|')[-1])
        return match.group(1) if match else None

    def _lp_material_records_for_match_repair(self, lp_node, include_faces=False):
        if not lp_node or not cmds.objExists(lp_node):
            return []
        shapes = cmds.listRelatives(lp_node, shapes=True, fullPath=True, type='mesh', noIntermediate=True) or []
        if not shapes:
            return []
        try:
            sel = om.MSelectionList()
            sel.add(lp_node)
            dag = sel.getDagPath(0)
            if dag.hasFn(om.MFn.kTransform):
                dag.extendToShape()
            mesh_fn = om.MFnMesh(dag)
            shaders, face_shader_indices = mesh_fn.getConnectedShaders(dag.instanceNumber())
        except Exception:
            return []

        records = []
        for shader_index, shader_obj in enumerate(shaders):
            try:
                sg = om.MFnDependencyNode(shader_obj).name()
            except Exception:
                continue
            try:
                materials = cmds.listConnections("{}.surfaceShader".format(sg), source=True, destination=False) or []
                material = materials[0] if materials else None
            except Exception:
                material = None
            key = material or sg
            faces = [
                face_id for face_id, assigned_index in enumerate(face_shader_indices)
                if int(assigned_index) == shader_index
            ] if include_faces else []
            if faces or not include_faces:
                records.append({
                    "key": key,
                    "material": material,
                    "faces": faces
                })
        return records

    def _lp_material_slot_map_for_match_repair(self, lp_paths):
        records_by_key = {}
        for lp_path in lp_paths or []:
            for rec in self._lp_material_records_for_match_repair(lp_path, include_faces=False):
                key = rec.get("key")
                if key:
                    records_by_key.setdefault(key, rec)
        if len(records_by_key) <= 1:
            return {}
        ordered = sorted(
            records_by_key.values(),
            key=lambda rec: ((rec.get("material") or rec.get("key") or "").split('|')[-1].lower(), (rec.get("key") or "").split('|')[-1].lower())
        )
        return {rec.get("key"): "M{:02d}".format(index) for index, rec in enumerate(ordered, 1) if rec.get("key")}

    def _dominant_lp_material_slot_for_match_repair(self, lp_path, slot_by_key):
        records = self._lp_material_records_for_match_repair(lp_path, include_faces=True)
        if not records:
            return None
        counts = {}
        for rec in records:
            slot = slot_by_key.get(rec.get("key"))
            if slot:
                counts[slot] = counts.get(slot, 0) + len(rec.get("faces") or [])
        if not counts:
            return None
        return max(counts.keys(), key=lambda slot: (counts[slot], slot))

    def _repair_lp_matches_by_material_slot(self, matches, hp_groups, hp_verts_cache=None, lp_verts_cache_fast=None, lp_verts_cache_full=None):
        all_lp_paths = sorted({lp_path for paths in (matches or {}).values() for lp_path in (paths or [])})
        slot_by_key = self._lp_material_slot_map_for_match_repair(all_lp_paths)
        if not slot_by_key:
            return matches, 0

        compatible_groups = {}
        for grp_name in sorted((hp_groups or {}).keys()):
            slot = self._material_slot_from_group_name(grp_name)
            if slot:
                compatible_groups.setdefault(slot, []).append(grp_name)
        if not compatible_groups:
            return matches, 0

        repaired = {grp_name: set(paths or []) for grp_name, paths in (matches or {}).items()}
        move_count = 0

        for grp_name, lp_paths in list(repaired.items()):
            group_slot = self._material_slot_from_group_name(grp_name)
            if not group_slot:
                continue
            for lp_path in list(lp_paths):
                lp_slot = self._dominant_lp_material_slot_for_match_repair(lp_path, slot_by_key)
                if not lp_slot or lp_slot == group_slot:
                    continue
                target_candidates = compatible_groups.get(lp_slot) or []
                if not target_candidates:
                    continue

                target_group = None
                lp_verts = (lp_verts_cache_full or {}).get(lp_path) or (lp_verts_cache_fast or {}).get(lp_path)
                if lp_verts:
                    try:
                        constrained_groups = {name: hp_groups.get(name, []) for name in target_candidates}
                        repair_worker = LPMatchingWorker(
                            hp_groups=constrained_groups,
                            hp_data_cache=self.hp_data_cache,
                            lp_data_cache={lp_path: self.lp_data_cache.get(lp_path, {})},
                            hp_verts_cache=hp_verts_cache or {},
                            lp_verts_cache_fast={lp_path: (lp_verts_cache_fast or {}).get(lp_path, [])},
                            lp_verts_cache_full={lp_path: (lp_verts_cache_full or {}).get(lp_path, [])},
                            lp_threshold_coef=1.5
                        )
                        target_group = repair_worker.get_best_match(self.lp_data_cache.get(lp_path, {}), lp_verts)
                    except Exception:
                        target_group = None

                if not target_group:
                    lp_data = self.lp_data_cache.get(lp_path, {})
                    lp_center = lp_data.get("center") or [0.0, 0.0, 0.0]

                    def candidate_score(candidate_group):
                        best_dist = float('inf')
                        for hp_path in hp_groups.get(candidate_group, []):
                            hp_data = self.hp_data_cache.get(hp_path, {})
                            hp_center = hp_data.get("center") or [0.0, 0.0, 0.0]
                            dx = lp_center[0] - hp_center[0]
                            dy = lp_center[1] - hp_center[1]
                            dz = lp_center[2] - hp_center[2]
                            best_dist = min(best_dist, (dx * dx + dy * dy + dz * dz) ** 0.5)
                        return best_dist

                    target_group = min(target_candidates, key=candidate_score)
                repaired.setdefault(target_group, set()).add(lp_path)
                repaired[grp_name].discard(lp_path)
                move_count += 1

        return repaired, move_count

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
        self.lp_worker.finished.connect(lambda matches, groups=hp_groups, hvc=hp_verts_cache, lf=lp_verts_cache_fast, lfull=lp_verts_cache_full: self.on_lp_finished(matches, lp_main, groups, hvc, lf, lfull))
        self.progress_dlg_lp.canceled.connect(self.lp_worker.stop)

        self.lp_worker.start()

    def on_lp_finished(self, matches, lp_main, hp_groups=None, hp_verts_cache=None, lp_verts_cache_fast=None, lp_verts_cache_full=None):
        # ... (Код завершения остается абсолютно без изменений) ...
        self.progress_dlg_lp.close()
        total_matched = 0
        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        locked_subgroups = pair.get('locked', []) if pair else []
        matches, material_repair_count = self._repair_lp_matches_by_material_slot(
            matches,
            hp_groups or {},
            hp_verts_cache=hp_verts_cache,
            lp_verts_cache_fast=lp_verts_cache_fast,
            lp_verts_cache_full=lp_verts_cache_full
        )

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
        if material_repair_count:
            self.log("Assign LP material check: repaired {} LP mesh(es).".format(material_repair_count), "lightblue")
        if hasattr(self, 'record_user_action'):
            self.record_user_action("Assign LP finished", "matched={}/{} material_repairs={}".format(total_matched, len(self.lp_data_cache), material_repair_count))


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
            btn_vis.clicked.connect(lambda checked=False, h=hp_nodes, b=btn_vis: self.run_undoable_bg_action("Final HP Visibility", self.toggle_final_hp_vis, h, b))
            row_layout.addWidget(btn_vis)

            btn_name = SubgroupButton(display_name)
            btn_name.setCheckable(True)
            if hasattr(self, 'subgroup_name_style'):
                btn_name.setStyleSheet(self.subgroup_name_style(display_name, False))
            else:
                btn_name.setStyleSheet("background-color: transparent; text-align: left; padding-left: 5px;")
            btn_name.clicked.connect(lambda checked=False, n=display_name: self.set_final_row_selected(n, checked, from_click=True))
            btn_name.doubleClicked.connect(lambda checked=False, n=display_name: self.select_final_hp_nodes(n))
            btn_name.rightClicked.connect(lambda checked=False, n=display_name: self.run_undoable_bg_action("Rename Final Group", self.show_final_row_context_menu, n))
            row_layout.addWidget(btn_name, stretch=1)

            combo = QtWidgets.QComboBox()
            combo.setObjectName("FinalSmoothCombo")
            combo.addItems(["Smooth 0", "Smooth 1", "Smooth 2", "Smooth 3"])
            combo.setFixedWidth(85)
            combo.setStyleSheet("background-color: #444; color: white;")
            combo.setFocusPolicy(QtCore.Qt.NoFocus)

            cached_level = self.final_smooth_states.get(display_name, 2)
            combo.setCurrentIndex(cached_level)
            combo.currentIndexChanged.connect(lambda idx, prefix=full_prefix, name=display_name, c=combo:
                                               self.run_undoable_bg_action("Final Smooth Level", self.on_final_smooth_combo_changed, name, idx, prefix, c))
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

    def set_final_row_selected(self, name, checked=True, from_click=False):
        if not hasattr(self, 'final_selected_names'):
            self.final_selected_names = set()

        if from_click:
            modifiers = QtWidgets.QApplication.keyboardModifiers()
            if modifiers & QtCore.Qt.ControlModifier:
                self.final_selected_names.discard(name)
            elif modifiers & QtCore.Qt.ShiftModifier:
                self.final_selected_names.add(name)
            else:
                self.final_selected_names = set([name])
        else:
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
        if not self.validate_combine_fin_lp_structure(hp_main, lp_main):
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

    def combine_fin_subgroup_names(self, root_node, suffix, expected_group=None):
        result = set()
        if not root_node or not cmds.objExists(root_node):
            return result
        suffix_re = re.escape(suffix) + r'\d*$'
        for child in (cmds.listRelatives(root_node, children=True, fullPath=True, type='transform') or []):
            if not child or not cmds.objExists(child):
                continue
            if cmds.listRelatives(child, shapes=True, fullPath=True, type='mesh', noIntermediate=True):
                continue
            if expected_group:
                attr = "{}.{}".format(child, bg_core.BakeConfig.ATTR_BAKE_GROUP)
                if cmds.objExists(attr):
                    try:
                        if cmds.getAttr(attr) != expected_group:
                            continue
                    except Exception:
                        pass
            short_name = child.split('|')[-1].replace(".", "_")
            clean_name = re.sub(suffix_re, '', short_name, flags=re.IGNORECASE).strip()
            if clean_name:
                result.add(clean_name)
        return result

    def validate_combine_fin_lp_structure(self, hp_main, lp_main):
        hp_names = self.combine_fin_subgroup_names(hp_main, bg_core.BakeConfig.SUFFIX_HP, "HP")
        lp_names = self.combine_fin_subgroup_names(lp_main, bg_core.BakeConfig.SUFFIX_LP, "LP")
        missing_in_lp = sorted(hp_names - lp_names)
        extra_in_lp = sorted(lp_names - hp_names)
        if not missing_in_lp and not extra_in_lp:
            return True

        def _format_names(names, limit=12):
            names = list(names or [])
            if not names:
                return "-"
            shown = names[:limit]
            text = ", ".join(shown)
            if len(names) > limit:
                text += ", +{}".format(len(names) - limit)
            return text

        message = bg_l10n.text("Combine Fin stopped: LP subgroup structure does not match HP.")
        details = [
            bg_l10n.text("Missing in LP: {names}").format(names=_format_names(missing_in_lp)),
            bg_l10n.text("Extra in LP: {names}").format(names=_format_names(extra_in_lp)),
            bg_l10n.text("Fix LP subgroups with Assign LP Meshes or rename/create matching LP subgroups before Combine Fin.")
        ]
        full_message = "{}\n\n{}".format(message, "\n".join(details))
        self.log(full_message.replace("\n", " | "), "orange")
        cmds.warning(full_message)

        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setWindowTitle(bg_l10n.text("LP Subgroup Structure Mismatch"))
        box.setText(message)
        box.setInformativeText("\n".join(details))
        box.setStandardButtons(QtWidgets.QMessageBox.Ok)
        box.setStyleSheet("QMessageBox { background-color: #242424; color: white; } QPushButton { background-color: #333; padding: 5px; }")
        box.exec_()
        return False


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

    def selected_transform_nodes(self):
        raw_selection = cmds.ls(sl=True, long=True, flatten=True) or []
        result = []
        seen = set()
        for item in raw_selection:
            node = item.split('.', 1)[0]
            if not node or not cmds.objExists(node):
                continue
            if cmds.nodeType(node) == 'mesh':
                parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
                node = parents[0] if parents else node
            if not cmds.objExists(node) or cmds.nodeType(node) != 'transform':
                continue
            long_node = (cmds.ls(node, long=True) or [node])[0]
            if long_node not in seen:
                seen.add(long_node)
                result.append(long_node)
        return result

    def add_to_groups_ui(self, hp_node, lp_node, hp_main, lp_main):
        sel = self.selected_transform_nodes()
        if not sel:
            return
        hp_target = cmds.ls(hp_node, l=True)[0] if hp_node and cmds.objExists(hp_node) else None
        lp_target = cmds.ls(lp_node, l=True)[0] if lp_node and cmds.objExists(lp_node) else None

        moved_count = 0
        skipped_count = 0
        moved_names = []
        moved_nodes = []
        source_groups = set()
        target_groups = set()
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
                target_groups.add(target_node.split('|')[-1])
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
            target_name = self._format_debug_names(sorted(target_groups), limit=10) if target_groups and hasattr(self, '_format_debug_names') else (", ".join(sorted(target_groups)) if target_groups else "Unknown")
            moved_preview = self._format_debug_names(moved_names, limit=20) if hasattr(self, '_format_debug_names') else ", ".join(moved_names[:20])
            source_preview = self._format_debug_names(sorted(source_groups), limit=10) if hasattr(self, '_format_debug_names') else ", ".join(sorted(source_groups))
            self.record_user_action(
                "Add to Group",
                "target={} | moved={} | skipped={} | from={} | meshes={}".format(
                    target_name, moved_count, skipped_count, source_preview, moved_preview
                )
            )

    def find_subgroup_nodes_by_ui_name(self, hp_main, lp_main, ui_name):
        result = {'hp': None, 'lp': None}
        keep_hp = bool(hasattr(self, 'cb_keep_hp_structure') and self.cb_keep_hp_structure.isChecked())
        for root, side, suffix in (
            (hp_main, 'hp', bg_core.BakeConfig.SUFFIX_HP),
            (lp_main, 'lp', bg_core.BakeConfig.SUFFIX_LP),
        ):
            if not root or not cmds.objExists(root):
                continue
            for child in cmds.listRelatives(root, children=True, type='transform', fullPath=True) or []:
                if not cmds.objExists(child) or cmds.listRelatives(child, shapes=True):
                    continue
                short_name = child.split('|')[-1]
                is_side = keep_hp
                if not is_side:
                    attr = "{}.{}".format(child, bg_core.BakeConfig.ATTR_BAKE_GROUP)
                    is_side = cmds.objExists(attr) and cmds.getAttr(attr) == side.upper()
                    if not is_side and short_name.endswith(suffix):
                        is_side = True
                if not is_side:
                    continue
                child_ui_name = short_name
                pattern = r'(_HP|_hp|HP|hp)(\d*)$' if side == 'hp' else r'(_LP|_lp|LP|lp)(\d*)$'
                match = re.search(pattern, short_name)
                if match:
                    child_ui_name = short_name[:match.start()] + match.group(2)
                if child_ui_name == ui_name:
                    result[side] = child
        return result['hp'], result['lp']

    def add_to_selected_subgroup_ui(self):
        if not self.active_subgroup_name:
            cmds.warning("First, select a subgroup from the list on the left.")
            return
        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not pair:
            return
        hp_main, lp_main, _ = self.core.resolve_main_nodes(pair)
        hp_grp, lp_grp = self.find_subgroup_nodes_by_ui_name(hp_main, lp_main, self.active_subgroup_name)
        if hp_grp or lp_grp:
            self.add_to_groups_ui(hp_grp, lp_grp, hp_main, lp_main)
        else:
            cmds.warning("Active subgroup target was not found: {}".format(self.active_subgroup_name))

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
            self.run_undoable_bg_action("Add ZBrush Layer Meshes", self.add_selected_zbrush_meshes_to_layer)

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

    def _create_by_mat_short_name(self, name):
        value = str(name or "").split('|')[-1].split(':')[-1].strip()
        return value or "No_Material"

    def _create_by_mat_safe_base(self, raw_name, used_names):
        value = self._create_by_mat_short_name(raw_name)
        value = re.sub(r'[^A-Za-z0-9_]+', '_', value).strip('_')
        value = re.sub(r'_+', '_', value)
        if not value:
            value = "Material"
        if not re.match(r'^[A-Za-z_]', value):
            value = "Mat_{}".format(value)
        value = re.sub(r'(_HP|_LP)$', '', value, flags=re.IGNORECASE).strip('_') or "Material"
        base = value[:54]
        existing_bases = set([str(p.get('base', '')) for p in self.root_pairs or []])
        candidate = base
        index = 2
        while (
            candidate in used_names or
            candidate in existing_bases or
            cmds.objExists(candidate + bg_core.BakeConfig.SUFFIX_HP) or
            cmds.objExists(candidate + bg_core.BakeConfig.SUFFIX_LP)
        ):
            suffix = "_{:02d}".format(index)
            candidate = "{}{}".format(base[:max(1, 54 - len(suffix))], suffix)
            index += 1
        used_names.add(candidate)
        return candidate

    def _create_by_mat_next_book_name(self):
        existing = set([p.get('book') for p in self.root_pairs or [] if p.get('book')])
        index = 1
        while True:
            name = "Book_{:02d}".format(index)
            if name not in existing:
                return name
            index += 1

    def _create_by_mat_material_signature(self, lp_node):
        records = []
        if hasattr(self, 'lp_material_records_for_node'):
            records = self.lp_material_records_for_node(lp_node, include_faces=True)
            if not records:
                records = self.lp_material_records_for_node(lp_node, include_faces=False)
        by_key = {}
        for rec in records or []:
            key = rec.get("key") or rec.get("material")
            if not key:
                continue
            by_key.setdefault(key, self._create_by_mat_short_name(rec.get("material") or key))
        if not by_key:
            by_key["No_Material"] = "No_Material"
        signature = tuple(sorted(by_key.keys(), key=lambda key: by_key.get(key, key).lower()))
        labels = [by_key[key] for key in signature]
        return signature, labels

    def _create_by_mat_mesh_data(self, mesh_node):
        data = bg_core.MeshDataManager.get_mesh_data(mesh_node)
        if not data:
            try:
                bb = cmds.exactWorldBoundingBox(mesh_node)
                center = [(bb[0] + bb[3]) * 0.5, (bb[1] + bb[4]) * 0.5, (bb[2] + bb[5]) * 0.5]
                diag = ((bb[3] - bb[0]) ** 2 + (bb[4] - bb[1]) ** 2 + (bb[5] - bb[2]) ** 2) ** 0.5
                data = {
                    "name": mesh_node,
                    "min": [bb[0], bb[1], bb[2]],
                    "max": [bb[3], bb[4], bb[5]],
                    "center": center,
                    "diag": diag,
                    "volume": max((bb[3] - bb[0]) * (bb[4] - bb[1]) * (bb[5] - bb[2]), 0.0)
                }
            except Exception:
                return None
        if "volume" not in data:
            mn = data.get("min", [0.0, 0.0, 0.0])
            mx = data.get("max", [0.0, 0.0, 0.0])
            data["volume"] = max((mx[0] - mn[0]) * (mx[1] - mn[1]) * (mx[2] - mn[2]), 0.0)
        if not data.get("sample_points"):
            data["sample_points"] = self._create_by_mat_mesh_sample_points(mesh_node, 48) or self._create_by_mat_bbox_points(data)
        return data

    def _create_by_mat_bbox_points(self, data):
        try:
            mn = data.get("min")
            mx = data.get("max")
            center = data.get("center", [(mn[0] + mx[0]) * 0.5, (mn[1] + mx[1]) * 0.5, (mn[2] + mx[2]) * 0.5])
            return [
                [mn[0], mn[1], mn[2]], [mn[0], mn[1], mx[2]], [mn[0], mx[1], mn[2]], [mn[0], mx[1], mx[2]],
                [mx[0], mn[1], mn[2]], [mx[0], mn[1], mx[2]], [mx[0], mx[1], mn[2]], [mx[0], mx[1], mx[2]],
                [center[0], center[1], center[2]]
            ]
        except Exception:
            return []

    def _create_by_mat_mesh_sample_points(self, mesh_node, max_points=64):
        if not om or not mesh_node or not cmds.objExists(mesh_node):
            return []
        try:
            sel = om.MSelectionList()
            sel.add(mesh_node)
            dag = sel.getDagPath(0)
            if dag.hasFn(om.MFn.kTransform):
                dag.extendToShape()
            mesh_fn = om.MFnMesh(dag)
            points = mesh_fn.getPoints(om.MSpace.kWorld)
            if len(points) == 0:
                return []
            total = len(points)
            step = max(1, int(math.ceil(total / float(max(max_points, 1)))))
            result = []
            for index in range(0, total, step):
                pnt = points[index]
                result.append([pnt.x, pnt.y, pnt.z])
                if len(result) >= max_points:
                    break
            return result
        except Exception:
            return []

    def _create_by_mat_shell_sample_points(self, points, shell_verts, shell_faces, face_vertices, max_points=160):
        result = []
        try:
            ordered_verts = sorted(shell_verts)
            vert_budget = max(16, int(max_points * 0.55))
            vert_step = max(1, int(math.ceil(len(ordered_verts) / float(max(vert_budget, 1)))))
            for vertex_id in ordered_verts[::vert_step]:
                pnt = points[vertex_id]
                result.append([pnt.x, pnt.y, pnt.z])
                if len(result) >= vert_budget:
                    break

            face_budget = max_points - len(result)
            if face_budget > 0 and shell_faces:
                face_step = max(1, int(math.ceil(len(shell_faces) / float(face_budget))))
                for face_id in shell_faces[::face_step]:
                    verts = face_vertices[face_id]
                    if not verts:
                        continue
                    acc_x = acc_y = acc_z = 0.0
                    for vertex_id in verts:
                        pnt = points[vertex_id]
                        acc_x += pnt.x
                        acc_y += pnt.y
                        acc_z += pnt.z
                    inv = 1.0 / float(len(verts))
                    result.append([acc_x * inv, acc_y * inv, acc_z * inv])
                    if len(result) >= max_points:
                        break
        except Exception:
            pass
        return result

    def _create_by_mat_lp_proxy_records(self, lp_node, signature, labels):
        fallback_data = self._create_by_mat_mesh_data(lp_node)
        fallback = [{
            "lp_node": lp_node,
            "signature": signature,
            "labels": labels,
            "data": fallback_data,
            "node_data": fallback_data,
            "signature_size": len(signature or []),
            "face_count": 0,
            "material_key": None
        }] if fallback_data else []

        if not om or not lp_node or not cmds.objExists(lp_node):
            return fallback

        try:
            material_records = []
            if hasattr(self, 'lp_material_records_for_node'):
                material_records = self.lp_material_records_for_node(lp_node, include_faces=True) or []
            use_material_regions = len([rec for rec in material_records if rec.get("faces")]) > 1

            face_material = {}
            if use_material_regions:
                for rec in material_records:
                    for face_id in rec.get("faces") or []:
                        face_material[face_id] = rec

            sel = om.MSelectionList()
            sel.add(lp_node)
            dag = sel.getDagPath(0)
            if dag.hasFn(om.MFn.kTransform):
                dag.extendToShape()

            mesh_fn = om.MFnMesh(dag)
            points = mesh_fn.getPoints(om.MSpace.kWorld)
            counts, connects = mesh_fn.getVertices()
            if len(counts) == 0:
                return fallback

            face_vertices = []
            vertex_to_faces = {}
            cursor = 0
            for face_id, count in enumerate(counts):
                verts = list(connects[cursor:cursor + count])
                cursor += count
                face_vertices.append(verts)
                for vertex_id in verts:
                    vertex_to_faces.setdefault(vertex_id, []).append(face_id)

            visited_faces = set()
            proxies = []
            shell_index = 0

            for start_face in range(len(face_vertices)):
                if start_face in visited_faces:
                    continue
                start_rec = face_material.get(start_face)
                start_key = start_rec.get("key") if start_rec else None
                stack = [start_face]
                visited_faces.add(start_face)
                shell_faces = []
                shell_verts = set()

                while stack:
                    face_id = stack.pop()
                    shell_faces.append(face_id)
                    for vertex_id in face_vertices[face_id]:
                        shell_verts.add(vertex_id)
                        for next_face in vertex_to_faces.get(vertex_id, []):
                            if next_face in visited_faces:
                                continue
                            if use_material_regions:
                                next_rec = face_material.get(next_face)
                                next_key = next_rec.get("key") if next_rec else None
                                if next_key != start_key:
                                    continue
                            visited_faces.add(next_face)
                            stack.append(next_face)

                if not shell_verts:
                    continue

                xs = [points[v].x for v in shell_verts]
                ys = [points[v].y for v in shell_verts]
                zs = [points[v].z for v in shell_verts]
                mn = [min(xs), min(ys), min(zs)]
                mx = [max(xs), max(ys), max(zs)]
                size = [max(mx[i] - mn[i], 0.0) for i in range(3)]
                center = [(mn[i] + mx[i]) * 0.5 for i in range(3)]
                diag = math.sqrt(size[0] * size[0] + size[1] * size[1] + size[2] * size[2])
                volume = max(size[0] * size[1] * size[2], 0.0)
                sample_points = self._create_by_mat_shell_sample_points(points, shell_verts, shell_faces, face_vertices)
                data = {
                    "name": "{}::mat_proxy_{:03d}".format(lp_node, shell_index),
                    "real_node": lp_node,
                    "min": mn,
                    "max": mx,
                    "center": center,
                    "diag": diag,
                    "volume": volume,
                    "sample_points": sample_points or self._create_by_mat_bbox_points({"min": mn, "max": mx, "center": center})
                }
                proxies.append({
                    "lp_node": lp_node,
                    "signature": signature,
                    "labels": labels,
                    "data": data,
                    "node_data": fallback_data,
                    "signature_size": len(signature or []),
                    "face_count": len(shell_faces),
                    "material_key": start_key
                })
                shell_index += 1

            return proxies or fallback
        except Exception as e:
            self.log("Create by Mat: LP proxy build failed for {}: {}".format(lp_node.split('|')[-1], e), "orange")
            return fallback

    def _create_by_mat_intersection_volume(self, data_a, data_b):
        try:
            mn_a = data_a.get("min")
            mx_a = data_a.get("max")
            mn_b = data_b.get("min")
            mx_b = data_b.get("max")
            dx = max(0.0, min(mx_a[0], mx_b[0]) - max(mn_a[0], mn_b[0]))
            dy = max(0.0, min(mx_a[1], mx_b[1]) - max(mn_a[1], mn_b[1]))
            dz = max(0.0, min(mx_a[2], mx_b[2]) - max(mn_a[2], mn_b[2]))
            return dx * dy * dz
        except Exception:
            return 0.0

    def _create_by_mat_bbox_gap_distance(self, data_a, data_b):
        try:
            mn_a = data_a.get("min")
            mx_a = data_a.get("max")
            mn_b = data_b.get("min")
            mx_b = data_b.get("max")
            total = 0.0
            for axis in range(3):
                if mx_a[axis] < mn_b[axis]:
                    delta = mn_b[axis] - mx_a[axis]
                elif mx_b[axis] < mn_a[axis]:
                    delta = mn_a[axis] - mx_b[axis]
                else:
                    delta = 0.0
                total += delta * delta
            return math.sqrt(total)
        except Exception:
            return 1e18

    def _create_by_mat_point_sample_distance(self, data_a, data_b):
        points_a = data_a.get("sample_points") or self._create_by_mat_bbox_points(data_a)
        points_b = data_b.get("sample_points") or self._create_by_mat_bbox_points(data_b)
        if not points_a or not points_b:
            return 1e18
        best_sq = 1e36
        try:
            for pa in points_a:
                ax, ay, az = pa[0], pa[1], pa[2]
                for pb in points_b:
                    dx = ax - pb[0]
                    dy = ay - pb[1]
                    dz = az - pb[2]
                    dist_sq = dx * dx + dy * dy + dz * dz
                    if dist_sq < best_sq:
                        best_sq = dist_sq
            return math.sqrt(best_sq)
        except Exception:
            return 1e18

    def _create_by_mat_hp_proxy_quick_score(self, hp_data, proxy):
        lp_data = proxy.get("data") or {}
        inter = self._create_by_mat_intersection_volume(hp_data, lp_data)
        bbox_gap = self._create_by_mat_bbox_gap_distance(hp_data, lp_data)
        hp_diag = max(float(hp_data.get("diag", 0.0) or 0.0), 0.000001)
        lp_diag = max(float(lp_data.get("diag", 0.0) or 0.0), 0.000001)
        min_diag = max(min(hp_diag, lp_diag), 0.000001)
        max_diag = max(hp_diag, lp_diag, 0.000001)
        if inter <= 0.0 and bbox_gap > max(min_diag * 4.0, max_diag * 0.06):
            return None
        center_dist = bg_core.MathUtils.distance(hp_data.get("center", [0.0, 0.0, 0.0]), lp_data.get("center", [0.0, 0.0, 0.0]))
        hp_vol = max(float(hp_data.get("volume", 0.0) or 0.0), 0.000001)
        overlap_hp = inter / hp_vol
        gap_norm = bbox_gap / max(min_diag, max_diag * 0.01, 0.000001)
        center_norm = center_dist / max_diag
        size_ratio = min_diag / max_diag
        score = (1600.0 if inter > 0.0 else 0.0) + (overlap_hp * 400.0) + (size_ratio * 30.0)
        score -= gap_norm * 100.0
        score -= center_norm * 20.0
        return {
            "quick_score": score,
            "proxy": proxy
        }

    def _create_by_mat_hp_proxy_score(self, hp_data, proxy):
        lp_data = proxy.get("data") or {}
        inter = self._create_by_mat_intersection_volume(hp_data, lp_data)
        bbox_gap = self._create_by_mat_bbox_gap_distance(hp_data, lp_data)
        sample_dist = self._create_by_mat_point_sample_distance(hp_data, lp_data)
        hp_vol = max(float(hp_data.get("volume", 0.0) or 0.0), 0.000001)
        lp_vol = max(float(lp_data.get("volume", 0.0) or 0.0), 0.000001)
        hp_diag = max(float(hp_data.get("diag", 0.0) or 0.0), 0.000001)
        lp_diag = max(float(lp_data.get("diag", 0.0) or 0.0), 0.000001)
        min_diag = max(min(hp_diag, lp_diag), 0.000001)
        max_diag = max(hp_diag, lp_diag, 0.000001)
        overlap_min = inter / max(min(hp_vol, lp_vol), 0.000001)
        overlap_hp = inter / hp_vol
        center_dist = bg_core.MathUtils.distance(hp_data.get("center", [0.0, 0.0, 0.0]), lp_data.get("center", [0.0, 0.0, 0.0]))
        center_norm = center_dist / max_diag
        sample_norm = sample_dist / max(min_diag, max_diag * 0.01, 0.000001)
        gap_norm = bbox_gap / max(min_diag, max_diag * 0.01, 0.000001)
        size_ratio = min_diag / max_diag

        score = (overlap_min * 900.0) + (overlap_hp * 350.0) + (size_ratio * 45.0)
        if inter > 0.0:
            score += 1800.0
        score -= sample_norm * 95.0
        score -= gap_norm * 120.0
        score -= center_norm * 18.0
        if lp_diag > hp_diag * 18.0 and overlap_hp < 0.35:
            score -= 80.0

        confident = False
        if inter > 0.0 and (overlap_hp >= 0.015 or sample_norm <= 1.75):
            confident = True
        elif bbox_gap <= max(min_diag * 0.35, max_diag * 0.005) and sample_norm <= 1.35:
            confident = True

        return {
            "score": score,
            "has_overlap": inter > 0.0,
            "confident": confident,
            "bbox_gap": bbox_gap,
            "sample_dist": sample_dist,
            "overlap_hp": overlap_hp,
            "overlap_min": overlap_min,
            "proxy": proxy
        }

    def _create_by_mat_bbox_contains_point(self, data, point, padding=0.0):
        try:
            mn = data.get("min")
            mx = data.get("max")
            return (
                point[0] >= mn[0] - padding and point[0] <= mx[0] + padding and
                point[1] >= mn[1] - padding and point[1] <= mx[1] + padding and
                point[2] >= mn[2] - padding and point[2] <= mx[2] + padding
            )
        except Exception:
            return False

    def _create_by_mat_mark_container_proxies(self, proxies):
        source_by_node = {}
        for proxy in proxies or []:
            lp_node = proxy.get("lp_node")
            data = proxy.get("node_data") or proxy.get("data")
            if not lp_node or not data:
                continue
            source_by_node.setdefault(lp_node, {
                "data": data,
                "signature_size": int(proxy.get("signature_size") or 0)
            })

        volumes = sorted([
            float(info.get("data", {}).get("volume", 0.0) or 0.0)
            for info in source_by_node.values()
            if float(info.get("data", {}).get("volume", 0.0) or 0.0) > 0.0
        ])
        if volumes:
            mid = len(volumes) // 2
            median_volume = volumes[mid] if len(volumes) % 2 else (volumes[mid - 1] + volumes[mid]) * 0.5
        else:
            median_volume = 1.0

        centers = []
        for lp_node, info in source_by_node.items():
            data = info.get("data") or {}
            center = data.get("center")
            if center:
                centers.append((lp_node, center))

        container_nodes = set()
        total_centers = max(len(centers) - 1, 1)
        for lp_node, info in source_by_node.items():
            data = info.get("data") or {}
            if int(info.get("signature_size") or 0) <= 1:
                continue
            volume = float(data.get("volume", 0.0) or 0.0)
            diag = float(data.get("diag", 0.0) or 0.0)
            padding = max(diag * 0.005, 0.000001)
            inside_count = 0
            for other_node, center in centers:
                if other_node == lp_node:
                    continue
                if self._create_by_mat_bbox_contains_point(data, center, padding):
                    inside_count += 1
            inside_ratio = inside_count / float(total_centers)
            if (
                volume >= max(median_volume * 6.0, 0.000001)
                and (inside_count >= 8 or inside_ratio >= 0.08)
            ):
                container_nodes.add(lp_node)

        for proxy in proxies or []:
            proxy["is_container"] = proxy.get("lp_node") in container_nodes
        return len(container_nodes)

    def _create_by_mat_hp_owner_score(self, hp_data, proxy, scene_diag):
        lp_data = proxy.get("data") or {}
        inter = self._create_by_mat_intersection_volume(hp_data, lp_data)
        bbox_gap = self._create_by_mat_bbox_gap_distance(hp_data, lp_data)
        sample_dist = self._create_by_mat_point_sample_distance(hp_data, lp_data)
        hp_vol = max(float(hp_data.get("volume", 0.0) or 0.0), 0.000001)
        lp_vol = max(float(lp_data.get("volume", 0.0) or 0.0), 0.000001)
        hp_diag = max(float(hp_data.get("diag", 0.0) or 0.0), 0.000001)
        lp_diag = max(float(lp_data.get("diag", 0.0) or 0.0), 0.000001)
        min_diag = max(min(hp_diag, lp_diag), 0.000001)
        max_diag = max(hp_diag, lp_diag, 0.000001)
        overlap_hp = inter / hp_vol
        overlap_lp = inter / lp_vol
        size_ratio = min_diag / max_diag
        near_scale = max(hp_diag * 0.25, lp_diag * 0.02, scene_diag * 0.001, 0.000001)
        sample_norm = sample_dist / near_scale
        gap_norm = bbox_gap / max(hp_diag * 0.25, lp_diag * 0.02, scene_diag * 0.001, 0.000001)
        is_container = bool(proxy.get("is_container"))

        score = overlap_hp * 1800.0
        score += max(0.0, 1.0 - min(sample_norm, 1.0)) * 420.0
        score += size_ratio * 120.0
        score += overlap_lp * 80.0
        if inter > 0.0:
            score += 260.0
        score -= gap_norm * 140.0
        if lp_diag < hp_diag * 0.22 and overlap_hp < 0.12:
            score -= 520.0
        if is_container:
            score -= 180.0
            if overlap_hp < 0.16 and sample_norm > 0.75:
                score -= 260.0

        confident = False
        if overlap_hp >= 0.10 and sample_norm <= 2.2:
            confident = True
        elif overlap_hp >= 0.04 and size_ratio >= 0.22 and sample_norm <= 1.25:
            confident = True
        elif not is_container and sample_norm <= 0.55 and bbox_gap <= max(hp_diag * 0.12, scene_diag * 0.0005):
            confident = True
        elif is_container and overlap_hp >= 0.22:
            confident = True

        strong = (
            overlap_hp >= 0.22 or
            (sample_norm <= 0.45 and size_ratio >= 0.12) or
            (is_container and overlap_hp >= 0.35)
        )

        return {
            "score": score,
            "confident": confident,
            "strong": strong,
            "has_overlap": inter > 0.0,
            "overlap_hp": overlap_hp,
            "sample_dist": sample_dist,
            "bbox_gap": bbox_gap,
            "proxy": proxy
        }

    def _create_by_mat_hp_parent_score(self, child_data, parent_data, scene_diag):
        child_diag = max(float(child_data.get("diag", 0.0) or 0.0), 0.000001)
        parent_diag = max(float(parent_data.get("diag", 0.0) or 0.0), 0.000001)
        if parent_diag <= child_diag * 1.25:
            return None

        inter = self._create_by_mat_intersection_volume(child_data, parent_data)
        child_vol = max(float(child_data.get("volume", 0.0) or 0.0), 0.000001)
        overlap_child = inter / child_vol
        bbox_gap = self._create_by_mat_bbox_gap_distance(child_data, parent_data)
        if inter <= 0.0 and bbox_gap > max(child_diag * 1.2, parent_diag * 0.08, scene_diag * 0.003):
            return None
        sample_dist = self._create_by_mat_point_sample_distance(child_data, parent_data)
        near_limit = max(child_diag * 0.55, parent_diag * 0.035, scene_diag * 0.001, 0.000001)
        if sample_dist > near_limit and bbox_gap > max(child_diag * 0.25, parent_diag * 0.01, scene_diag * 0.0005) and overlap_child < 0.12:
            return None

        score = max(0.0, 1.0 - min(sample_dist / near_limit, 1.0)) * 130.0
        score += min(overlap_child, 1.0) * 95.0
        score += min(parent_diag / max(child_diag, 0.000001), 8.0) * 4.0
        if bbox_gap <= max(child_diag * 0.15, parent_diag * 0.008):
            score += 25.0
        return {
            "score": score,
            "sample_dist": sample_dist,
            "bbox_gap": bbox_gap,
            "overlap_child": overlap_child
        }

    def _create_by_mat_avg_sample_distance(self, source_data, target_data, max_source=96, max_target=128):
        source_points = source_data.get("sample_points") or self._create_by_mat_bbox_points(source_data)
        target_points = target_data.get("sample_points") or self._create_by_mat_bbox_points(target_data)
        if not source_points or not target_points:
            return 1e18
        if len(source_points) > max_source:
            step = max(1, int(math.ceil(len(source_points) / float(max_source))))
            source_points = source_points[::step][:max_source]
        if len(target_points) > max_target:
            step = max(1, int(math.ceil(len(target_points) / float(max_target))))
            target_points = target_points[::step][:max_target]
        total = 0.0
        count = 0
        try:
            for src in source_points:
                best_sq = 1e36
                sx, sy, sz = src[0], src[1], src[2]
                for dst in target_points:
                    dx = sx - dst[0]
                    dy = sy - dst[1]
                    dz = sz - dst[2]
                    dist_sq = dx * dx + dy * dy + dz * dz
                    if dist_sq < best_sq:
                        best_sq = dist_sq
                total += math.sqrt(max(best_sq, 0.0))
                count += 1
            return total / float(max(count, 1))
        except Exception:
            return 1e18

    def _create_by_mat_lp_hp_audit_quick_score(self, proxy, hp_data, scene_diag):
        lp_data = proxy.get("data") or {}
        inter = self._create_by_mat_intersection_volume(lp_data, hp_data)
        bbox_gap = self._create_by_mat_bbox_gap_distance(lp_data, hp_data)
        lp_diag = max(float(lp_data.get("diag", 0.0) or 0.0), 0.000001)
        hp_diag = max(float(hp_data.get("diag", 0.0) or 0.0), 0.000001)
        if inter <= 0.0 and bbox_gap > max(lp_diag * 0.75, hp_diag * 1.10, scene_diag * 0.004):
            return None
        lp_vol = max(float(lp_data.get("volume", 0.0) or 0.0), 0.000001)
        hp_vol = max(float(hp_data.get("volume", 0.0) or 0.0), 0.000001)
        overlap_lp = inter / lp_vol
        overlap_hp = inter / hp_vol
        center_dist = bg_core.MathUtils.distance(lp_data.get("center", [0.0, 0.0, 0.0]), hp_data.get("center", [0.0, 0.0, 0.0]))
        scale = max(lp_diag, hp_diag, scene_diag * 0.01, 0.000001)
        gap_norm = bbox_gap / max(min(lp_diag, hp_diag), scene_diag * 0.001, 0.000001)
        center_norm = center_dist / scale
        score = overlap_lp * 700.0 + overlap_hp * 180.0
        if inter > 0.0:
            score += 160.0
        score -= gap_norm * 70.0
        score -= center_norm * 25.0
        if proxy.get("is_container"):
            score -= 220.0
        return score

    def _create_by_mat_lp_hp_audit_score(self, proxy, hp_data, scene_diag):
        lp_data = proxy.get("data") or {}
        inter = self._create_by_mat_intersection_volume(lp_data, hp_data)
        bbox_gap = self._create_by_mat_bbox_gap_distance(lp_data, hp_data)
        if inter <= 0.0:
            lp_diag_pre = max(float(lp_data.get("diag", 0.0) or 0.0), 0.000001)
            hp_diag_pre = max(float(hp_data.get("diag", 0.0) or 0.0), 0.000001)
            if bbox_gap > max(lp_diag_pre * 0.55, hp_diag_pre * 0.85, scene_diag * 0.003):
                return None
        avg_dist = self._create_by_mat_avg_sample_distance(lp_data, hp_data)
        lp_vol = max(float(lp_data.get("volume", 0.0) or 0.0), 0.000001)
        hp_vol = max(float(hp_data.get("volume", 0.0) or 0.0), 0.000001)
        lp_diag = max(float(lp_data.get("diag", 0.0) or 0.0), 0.000001)
        hp_diag = max(float(hp_data.get("diag", 0.0) or 0.0), 0.000001)
        overlap_lp = inter / lp_vol
        overlap_hp = inter / hp_vol
        near_limit = max(lp_diag * 0.12, hp_diag * 0.08, scene_diag * 0.001, 0.000001)
        avg_norm = avg_dist / near_limit
        gap_norm = bbox_gap / max(lp_diag * 0.20, hp_diag * 0.20, scene_diag * 0.001, 0.000001)
        score = overlap_lp * 1200.0 + overlap_hp * 260.0
        score += max(0.0, 1.0 - min(avg_norm, 1.0)) * 620.0
        if inter > 0.0:
            score += 220.0
        score -= gap_norm * 120.0
        if hp_diag < lp_diag * 0.08 and overlap_lp < 0.10:
            score -= 260.0
        if proxy.get("is_container"):
            score -= 320.0

        strong = False
        if not proxy.get("is_container"):
            strong = (
                overlap_lp >= 0.16 or
                (overlap_lp >= 0.055 and avg_norm <= 1.25) or
                (avg_norm <= 0.62 and bbox_gap <= max(lp_diag * 0.10, hp_diag * 0.10, scene_diag * 0.0005))
            )
        else:
            strong = overlap_lp >= 0.35 and avg_norm <= 1.0
        return {
            "score": score,
            "strong": strong,
            "overlap_lp": overlap_lp,
            "overlap_hp": overlap_hp,
            "avg_distance": avg_dist,
            "bbox_gap": bbox_gap,
            "proxy": proxy
        }

    def _create_by_mat_best_bucket_lp_audit_score(self, bucket, hp_data, scene_diag):
        if not bucket:
            return 0.0
        best_score = 0.0
        quick_candidates = []
        for proxy in bucket.get("lp_proxies", []) or []:
            quick = self._create_by_mat_lp_hp_audit_quick_score(proxy, hp_data, scene_diag)
            if quick is None:
                continue
            quick_candidates.append((quick, proxy))
        quick_candidates.sort(key=lambda item: item[0], reverse=True)
        for _quick, proxy in quick_candidates[:16]:
            result = self._create_by_mat_lp_hp_audit_score(proxy, hp_data, scene_diag)
            if result:
                best_score = max(best_score, float(result.get("score", 0.0) or 0.0))
        return best_score

    def create_root_pairs_by_material_from_picked(self):
        if not self.picked_hp or not cmds.objExists(self.picked_hp):
            return cmds.warning("Invalid HP node.")
        if not self.picked_lp or not cmds.objExists(self.picked_lp):
            return cmds.warning("Invalid LP node.")

        if not self.validate_frozen_transforms(
            [self.picked_hp, self.picked_lp],
            [self.picked_hp, self.picked_lp],
            bg_l10n.text("Create by Mat")
        ):
            return

        hp_source = (cmds.ls(self.picked_hp, long=True) or [self.picked_hp])[0]
        lp_source = (cmds.ls(self.picked_lp, long=True) or [self.picked_lp])[0]

        progress = QtWidgets.QProgressDialog(bg_l10n.text("Creating chapters by LP materials..."), bg_l10n.text("Cancel"), 0, 100, self)
        progress.setWindowModality(QtCore.Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()
        QtWidgets.QApplication.processEvents()

        try:
            with bg_core.undo_chunk("CreateByMaterial"):
                progress.setLabelText(bg_l10n.text("Preparing source meshes..."))
                progress.setValue(5)
                QtWidgets.QApplication.processEvents()

                hp_meshes = self.prepare_meshes(hp_source, flatten=True)
                lp_meshes = self.prepare_meshes(lp_source, flatten=True)
                hp_meshes = [m for m in hp_meshes if m and cmds.objExists(m)]
                lp_meshes = [m for m in lp_meshes if m and cmds.objExists(m)]

                if not lp_meshes:
                    self.log("Create by Mat: no LP meshes found.", "red")
                    return
                if not hp_meshes:
                    self.log("Create by Mat: no HP meshes found.", "red")
                    return

                buckets_by_signature = {}
                all_lp_proxies = []
                total_lp = max(len(lp_meshes), 1)
                for index, lp_node in enumerate(lp_meshes):
                    if progress.wasCanceled():
                        self.log("Create by Mat canceled.", "orange")
                        return
                    progress.setLabelText(bg_l10n.text("Scanning LP materials: {name}").format(name=lp_node.split('|')[-1]))
                    progress.setValue(5 + int((index / float(total_lp)) * 25))
                    QtWidgets.QApplication.processEvents()

                    signature, labels = self._create_by_mat_material_signature(lp_node)
                    bucket = buckets_by_signature.setdefault(signature, {
                        "labels": labels,
                        "lp_meshes": [],
                        "lp_proxies": [],
                        "hp_meshes": []
                    })
                    bucket["lp_meshes"].append(lp_node)
                    proxies = self._create_by_mat_lp_proxy_records(lp_node, signature, labels)
                    for proxy in proxies:
                        proxy["bucket"] = bucket
                        bucket["lp_proxies"].append(proxy)
                        all_lp_proxies.append(proxy)

                if not buckets_by_signature:
                    self.log("Create by Mat: LP materials were not detected.", "red")
                    return
                self.log("Create by Mat: built {} LP match proxy region(s).".format(len(all_lp_proxies)), "lightblue")

                ordered_buckets = []
                used_bases = set()
                for signature, bucket in sorted(buckets_by_signature.items(), key=lambda item: " ".join(item[1].get("labels", [])).lower()):
                    labels = bucket.get("labels") or ["Material"]
                    raw_base = "_".join([self._create_by_mat_short_name(label) for label in labels])
                    bucket["base"] = self._create_by_mat_safe_base(raw_base, used_bases)
                    bucket["signature"] = signature
                    ordered_buckets.append(bucket)

                container_count = self._create_by_mat_mark_container_proxies(all_lp_proxies)
                if container_count:
                    self.log("Create by Mat: detected {} large multi-material LP container(s).".format(container_count), "lightblue")

                lp_diags = sorted([
                    float((proxy.get("data") or {}).get("diag", 0.0) or 0.0)
                    for proxy in all_lp_proxies
                    if float((proxy.get("data") or {}).get("diag", 0.0) or 0.0) > 0.0
                ])
                hp_scene_diag_values = []
                for hp_node in hp_meshes:
                    hp_data = self._create_by_mat_mesh_data(hp_node)
                    if hp_data:
                        hp_scene_diag_values.append(float(hp_data.get("diag", 0.0) or 0.0))
                scene_diag_values = sorted([v for v in (lp_diags + hp_scene_diag_values) if v > 0.0])
                if scene_diag_values:
                    mid = len(scene_diag_values) // 2
                    scene_diag = scene_diag_values[mid] if len(scene_diag_values) % 2 else (scene_diag_values[mid - 1] + scene_diag_values[mid]) * 0.5
                else:
                    scene_diag = 1.0
                if hp_scene_diag_values:
                    hp_diags_sorted = sorted([v for v in hp_scene_diag_values if v > 0.0])
                    mid = len(hp_diags_sorted) // 2
                    median_hp_diag = hp_diags_sorted[mid] if len(hp_diags_sorted) % 2 else (hp_diags_sorted[mid - 1] + hp_diags_sorted[mid]) * 0.5
                else:
                    median_hp_diag = scene_diag

                progress.setLabelText(bg_l10n.text("Resolving HP ownership from LP chapters..."))
                progress.setValue(35)
                QtWidgets.QApplication.processEvents()

                low_confidence = 0
                review_hp_meshes = []
                review_examples = []
                hp_records = {}
                direct_count = 0
                container_direct_count = 0
                total_hp = max(len(hp_meshes), 1)
                for index, hp_node in enumerate(hp_meshes):
                    if progress.wasCanceled():
                        self.log("Create by Mat canceled.", "orange")
                        return
                    progress.setValue(35 + int((index / float(total_hp)) * 22))
                    QtWidgets.QApplication.processEvents()

                    hp_data = self._create_by_mat_mesh_data(hp_node)
                    if not hp_data:
                        continue
                    quick_scored = []
                    for proxy in all_lp_proxies:
                        metrics = self._create_by_mat_hp_proxy_quick_score(hp_data, proxy)
                        if metrics:
                            quick_scored.append(metrics)
                    hp_records[hp_node] = {
                        "data": hp_data,
                        "bucket": None,
                        "score": 0.0,
                        "strong": False,
                        "source": "unassigned",
                        "has_overlap": False
                    }
                    if not quick_scored:
                        continue
                    quick_scored.sort(key=lambda item: item.get("quick_score", -1e18), reverse=True)
                    scored = []
                    for quick in quick_scored[:48]:
                        metrics = self._create_by_mat_hp_owner_score(hp_data, quick.get("proxy"), scene_diag)
                        scored.append(metrics)
                    if not scored:
                        continue
                    scored.sort(key=lambda item: item.get("score", -1e18), reverse=True)
                    non_container = [candidate for candidate in scored if candidate.get("confident") and not (candidate.get("proxy") or {}).get("is_container")]
                    container = [candidate for candidate in scored if candidate.get("confident") and (candidate.get("proxy") or {}).get("is_container")]
                    best = non_container[0] if non_container else None
                    if best is None and container:
                        best = container[0]
                    if best is None:
                        continue
                    best_bucket = (best.get("proxy") or {}).get("bucket")
                    if best_bucket is None:
                        continue
                    hp_records[hp_node].update({
                        "bucket": best_bucket,
                        "score": float(best.get("score", 0.0) or 0.0),
                        "strong": bool(best.get("strong")),
                        "source": "container" if (best.get("proxy") or {}).get("is_container") else "lp",
                        "has_overlap": bool(best.get("has_overlap"))
                    })
                    direct_count += 1
                    if not best.get("has_overlap"):
                        low_confidence += 1
                    if (best.get("proxy") or {}).get("is_container"):
                        container_direct_count += 1

                progress.setLabelText(bg_l10n.text("Attaching HP floaters to large owners..."))
                progress.setValue(58)
                QtWidgets.QApplication.processEvents()

                assigned_items = [(hp, rec) for hp, rec in hp_records.items() if rec.get("bucket") is not None]
                stable_parents = [
                    (hp, rec) for hp, rec in assigned_items
                    if rec.get("strong") and float(rec.get("data", {}).get("diag", 0.0) or 0.0) >= median_hp_diag * 0.35
                ]
                floater_reassigned = 0
                floater_assigned = 0
                floater_tests = 0
                total_records = max(len(hp_records), 1)
                for index, (hp_node, rec) in enumerate(list(hp_records.items())):
                    if progress.wasCanceled():
                        self.log("Create by Mat canceled.", "orange")
                        return
                    if index % max(1, total_records // 20) == 0:
                        progress.setValue(58 + int((index / float(total_records)) * 12))
                        QtWidgets.QApplication.processEvents()

                    hp_data = rec.get("data") or {}
                    hp_diag = float(hp_data.get("diag", 0.0) or 0.0)
                    if rec.get("strong") and rec.get("source") == "lp" and hp_diag >= median_hp_diag * 0.70:
                        continue

                    best_parent = None
                    best_parent_score = None
                    for parent_hp, parent_rec in stable_parents:
                        if parent_hp == hp_node:
                            continue
                        parent_bucket = parent_rec.get("bucket")
                        if parent_bucket is None:
                            continue
                        parent_data = parent_rec.get("data") or {}
                        floater_tests += 1
                        parent_score = self._create_by_mat_hp_parent_score(hp_data, parent_data, scene_diag)
                        if not parent_score:
                            continue
                        if best_parent_score is None or parent_score.get("score", 0.0) > best_parent_score.get("score", 0.0):
                            best_parent = parent_rec
                            best_parent_score = parent_score

                    if not best_parent or not best_parent_score:
                        continue
                    parent_score_value = float(best_parent_score.get("score", 0.0) or 0.0)
                    current_score = float(rec.get("score", 0.0) or 0.0)
                    can_relink = (
                        rec.get("bucket") is None or
                        not rec.get("strong") or
                        rec.get("source") == "container" or
                        current_score < 75.0
                    )
                    if not can_relink:
                        continue
                    if parent_score_value < 72.0 and rec.get("bucket") is not None:
                        continue
                    if parent_score_value < 54.0:
                        continue
                    old_bucket = rec.get("bucket")
                    rec["bucket"] = best_parent.get("bucket")
                    rec["source"] = "floater"
                    rec["strong"] = False
                    rec["score"] = max(current_score, parent_score_value)
                    if old_bucket is None:
                        floater_assigned += 1
                    elif old_bucket is not rec["bucket"]:
                        floater_reassigned += 1

                progress.setLabelText(bg_l10n.text("Checking LP meshes for missing HP..."))
                progress.setValue(70)
                QtWidgets.QApplication.processEvents()

                lp_audit_checked = 0
                lp_audit_candidates = 0
                lp_audit_assigned = 0
                lp_audit_reassigned = 0
                lp_audit_container_conflicts = 0
                audit_best_by_hp = {}
                audit_proxies = sorted(
                    [proxy for proxy in all_lp_proxies if proxy.get("bucket") is not None],
                    key=lambda proxy: (
                        1 if proxy.get("is_container") else 0,
                        float((proxy.get("data") or {}).get("volume", 0.0) or 0.0)
                    )
                )
                total_audit = max(len(audit_proxies), 1)

                for index, proxy in enumerate(audit_proxies):
                    if progress.wasCanceled():
                        self.log("Create by Mat canceled.", "orange")
                        return
                    if index % max(1, total_audit // 20) == 0:
                        progress.setValue(70 + int((index / float(total_audit)) * 10))
                        QtWidgets.QApplication.processEvents()

                    target_bucket = proxy.get("bucket")
                    if target_bucket is None:
                        continue
                    lp_audit_checked += 1
                    quick_candidates = []
                    for hp_node, rec in hp_records.items():
                        if rec.get("bucket") is target_bucket:
                            continue
                        hp_data = rec.get("data") or {}
                        quick = self._create_by_mat_lp_hp_audit_quick_score(proxy, hp_data, scene_diag)
                        if quick is None:
                            continue
                        quick_candidates.append((quick, hp_node, rec))
                    if not quick_candidates:
                        continue
                    quick_candidates.sort(key=lambda item: item[0], reverse=True)

                    for _quick, hp_node, rec in quick_candidates[:24]:
                        result = self._create_by_mat_lp_hp_audit_score(proxy, rec.get("data") or {}, scene_diag)
                        if not result or not result.get("strong"):
                            continue
                        lp_audit_candidates += 1
                        current = audit_best_by_hp.get(hp_node)
                        result_score = float(result.get("score", 0.0) or 0.0)
                        if current is None or result_score > float(current.get("score", 0.0) or 0.0):
                            audit_best_by_hp[hp_node] = {
                                "score": result_score,
                                "target_bucket": target_bucket,
                                "proxy": proxy,
                                "result": result
                            }

                for hp_node, candidate in audit_best_by_hp.items():
                    rec = hp_records.get(hp_node)
                    if not rec:
                        continue
                    target_bucket = candidate.get("target_bucket")
                    current_bucket = rec.get("bucket")
                    if target_bucket is None or current_bucket is target_bucket:
                        continue

                    target_proxy = candidate.get("proxy") or {}
                    candidate_score = float(candidate.get("score", 0.0) or 0.0)
                    current_score = self._create_by_mat_best_bucket_lp_audit_score(current_bucket, rec.get("data") or {}, scene_diag)

                    if target_proxy.get("is_container"):
                        lp_audit_container_conflicts += 1
                        continue

                    source = rec.get("source")
                    needs_repair = (
                        current_bucket is None or
                        source in ("container", "unassigned") or
                        not rec.get("strong") or
                        candidate_score >= current_score + 85.0 or
                        (current_score <= 1.0 and candidate_score >= 260.0)
                    )
                    if not needs_repair:
                        continue

                    old_bucket = current_bucket
                    rec["bucket"] = target_bucket
                    rec["source"] = "lp_audit"
                    rec["strong"] = False
                    rec["score"] = max(float(rec.get("score", 0.0) or 0.0), candidate_score)
                    if old_bucket is None:
                        lp_audit_assigned += 1
                    else:
                        lp_audit_reassigned += 1

                for hp_node, rec in hp_records.items():
                    bucket = rec.get("bucket")
                    if bucket is None:
                        review_hp_meshes.append(hp_node)
                        if len(review_examples) < 8:
                            review_examples.append("{} -> no LP owner".format(hp_node.split('|')[-1]))
                        continue
                    bucket.setdefault("hp_meshes", []).append(hp_node)

                if review_hp_meshes:
                    review_base = self._create_by_mat_safe_base("Review_Unmatched", used_bases)
                    review_bucket = {
                        "labels": ["Review_Unmatched"],
                        "lp_meshes": [],
                        "lp_proxies": [],
                        "hp_meshes": review_hp_meshes,
                        "base": review_base,
                        "signature": ("Review_Unmatched",),
                        "is_review": True
                    }
                    ordered_buckets.append(review_bucket)

                book_name = self._create_by_mat_next_book_name()
                new_pairs = []
                progress.setLabelText(bg_l10n.text("Creating material chapters..."))
                progress.setValue(82)
                QtWidgets.QApplication.processEvents()

                total_buckets = max(len(ordered_buckets), 1)
                for index, bucket in enumerate(ordered_buckets):
                    if progress.wasCanceled():
                        self.log("Create by Mat canceled.", "orange")
                        return
                    progress.setValue(82 + int((index / float(total_buckets)) * 15))
                    QtWidgets.QApplication.processEvents()

                    base = bucket["base"]
                    hp_root = cmds.group(em=True, name=base + bg_core.BakeConfig.SUFFIX_HP, parent=hp_source)
                    lp_root = cmds.group(em=True, name=base + bg_core.BakeConfig.SUFFIX_LP, parent=lp_source)
                    hp_root = (cmds.ls(hp_root, long=True) or [hp_root])[0]
                    lp_root = (cmds.ls(lp_root, long=True) or [lp_root])[0]

                    lp_to_move = [node for node in bucket.get("lp_meshes", []) if node and cmds.objExists(node)]
                    hp_to_move = [node for node in bucket.get("hp_meshes", []) if node and cmds.objExists(node)]
                    if lp_to_move:
                        cmds.parent(lp_to_move, lp_root, absolute=True)
                    if hp_to_move:
                        cmds.parent(hp_to_move, hp_root, absolute=True)

                    pair = {
                        "id": str(uuid.uuid4()),
                        "base": base,
                        "hp_uuid": cmds.ls(hp_root, uuid=True)[0],
                        "lp_uuid": cmds.ls(lp_root, uuid=True)[0],
                        "locked": [],
                        "book": book_name,
                        "final_smooth_states": {}
                    }
                    new_pairs.append(pair)

                self.root_pairs.extend(new_pairs)
                if hasattr(self.core, 'root_pairs'):
                    self.core.root_pairs = self.root_pairs
                if hasattr(self.core, '_node_cache'):
                    self.core._node_cache.clear()
                bg_core.BakeSessionModel.save(self.root_pairs)

                self.picked_hp, self.picked_lp = None, None
                self.le_picked_hp.clear()
                self.le_picked_lp.clear()
                self.active_material_visibility_filter = None

                if new_pairs:
                    self.activate_root(new_pairs[0])
                else:
                    self.refresh_right_panel()
                    self.refresh_left_panel()

                message = "Create by Mat: created {} chapter(s) in {}.".format(len(new_pairs), book_name)
                self.log(message, "lightgreen")
                self.log(
                    "Create by Mat HP ownership: direct={}, container_fallback={}, floater_assigned={}, floater_reassigned={}, lp_audit_assigned={}, lp_audit_reassigned={}, review={}.".format(
                        direct_count,
                        container_direct_count,
                        floater_assigned,
                        floater_reassigned,
                        lp_audit_assigned,
                        lp_audit_reassigned,
                        len(review_hp_meshes)
                    ),
                    "lightblue"
                )
                self.log(
                    "Create by Mat LP audit: checked={}, candidates={}, container_conflicts={}.".format(
                        lp_audit_checked,
                        lp_audit_candidates,
                        lp_audit_container_conflicts
                    ),
                    "lightblue"
                )
                if low_confidence:
                    self.log("Create by Mat: {} HP mesh(es) assigned by close LP proxy without bbox overlap.".format(low_confidence), "orange")
                if review_hp_meshes:
                    self.log("Create by Mat: {} HP mesh(es) moved to Review_Unmatched for manual check.".format(len(review_hp_meshes)), "orange")
                    if review_examples:
                        self.log("Create by Mat review examples: {}".format("; ".join(review_examples)), "orange")
                if hasattr(self, 'record_user_action'):
                    self.record_user_action(
                        "Create by Mat",
                        "chapters={} | book={} | direct_hp={} | container_hp={} | floater_assigned={} | floater_reassigned={} | lp_audit_assigned={} | lp_audit_reassigned={} | lp_audit_checked={} | lp_audit_candidates={} | lp_audit_container_conflicts={} | low_confidence_hp={} | review_hp={}".format(
                            len(new_pairs),
                            book_name,
                            direct_count,
                            container_direct_count,
                            floater_assigned,
                            floater_reassigned,
                            lp_audit_assigned,
                            lp_audit_reassigned,
                            lp_audit_checked,
                            lp_audit_candidates,
                            lp_audit_container_conflicts,
                            low_confidence,
                            len(review_hp_meshes)
                        )
                    )
                cmds.inViewMessage(amg=message, pos='midCenter', fade=True)
        finally:
            try:
                progress.close()
            except RuntimeError:
                pass

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
            if not self.cb_keep_hp_structure.isChecked():
                self._skip_color_update_once = True
            self.activate_root(np)
            self.refresh_right_panel()

            self.prepare_meshes(hp_node, flatten=not self.cb_keep_hp_structure.isChecked())
            self.prepare_meshes(lp_node, flatten=True)
            self.refresh_left_panel()
            if self.cb_keep_hp_structure.isChecked() and hasattr(self, 'refresh_subgroup_color_preview'):
                self.refresh_subgroup_color_preview(reset_indices=True)
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

    def _separate_mesh_transforms(self, mesh_nodes, select_result=True):
        valid_nodes = []
        seen = set()
        for node in mesh_nodes or []:
            if not node or not cmds.objExists(node):
                continue
            node_type = cmds.nodeType(node)
            if node_type == "mesh":
                parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
                node = parents[0] if parents else None
            if not node or not cmds.objExists(node) or cmds.nodeType(node) != "transform":
                continue
            shapes = cmds.listRelatives(node, shapes=True, fullPath=True, type='mesh', noIntermediate=True) or []
            if not shapes:
                continue
            long_node = (cmds.ls(node, long=True) or [node])[0]
            if long_node not in seen:
                seen.add(long_node)
                valid_nodes.append(long_node)
        if not valid_nodes:
            return []

        final_selection = []
        with bg_core.undo_chunk("ModelPack_Separate"):
            for obj in valid_nodes:
                if not cmds.objExists(obj):
                    continue
                base_name = obj.split('|')[-1]
                target_parent = self.get_parent_tool(obj)
                try:
                    result = cmds.polySeparate(obj, ch=False) or []
                except Exception as e:
                    cmds.warning("Separate failed for {}: {}".format(base_name, e))
                    continue

                parts = []
                for item in result:
                    if not item or not cmds.objExists(item):
                        continue
                    if cmds.nodeType(item) == "mesh":
                        parents = cmds.listRelatives(item, parent=True, fullPath=True) or []
                        item = parents[0] if parents else None
                    if not item or not cmds.objExists(item) or cmds.nodeType(item) != "transform":
                        continue
                    if not cmds.listRelatives(item, shapes=True, fullPath=True, type='mesh', noIntermediate=True):
                        continue
                    long_item = (cmds.ls(item, long=True) or [item])[0]
                    if long_item not in parts:
                        parts.append(long_item)

                if not parts:
                    continue

                for index, part in enumerate(parts, 1):
                    if not cmds.objExists(part):
                        continue
                    try:
                        cmds.delete(part, constructionHistory=True)
                    except Exception:
                        pass
                    try:
                        new_name = cmds.rename(part, "{}_Part{}".format(base_name, index))
                    except Exception:
                        new_name = part
                    if target_parent and cmds.objExists(target_parent):
                        try:
                            new_name = cmds.parent(new_name, target_parent, absolute=True)[0]
                        except Exception:
                            pass
                    else:
                        try:
                            if self.get_parent_tool(new_name):
                                new_name = cmds.parent(new_name, world=True)[0]
                        except Exception:
                            pass
                    final_selection.append((cmds.ls(new_name, long=True) or [new_name])[0])

        cache = getattr(self, '_combined_mesh_check_cache', None)
        if isinstance(cache, dict):
            cache.clear()
        if final_selection and select_result:
            cmds.select(final_selection, replace=True)
        return final_selection

    def tool_separate(self):
        sel = cmds.ls(sl=True, l=True)
        if not sel:
            return cmds.warning("Select an object to separate.")
        try:
            final_selection = self._separate_mesh_transforms(sel, select_result=True)
            if final_selection and hasattr(self, 'record_user_action'):
                self.record_user_action(
                    "Separate",
                    "input={} | parts={}".format(len(sel), len(final_selection))
                )
            elif not final_selection:
                cmds.warning("Separate: no mesh parts were created.")
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
            self.update_find_sim_button("Find All")
            self.btn_fs.setStyleSheet("background-color: #27ae60; font-weight: bold;")
            cmds.inViewMessage(amg="Mode: Find ALL (Ignores Layout)", pos='midCenter', fade=True)
        else:
            self.find_sim_mode = 'SIM'
            self.update_find_sim_button("Find Sim")
            self.btn_fs.setStyleSheet("background-color: #3b5998; font-weight: bold;")
            cmds.inViewMessage(amg="Mode: Find SIM (Strict Layout Matching)", pos='midCenter', fade=True)

    def update_find_sim_button(self, key):
        self.btn_fs.setProperty("bg_i18n_key", key)
        self.btn_fs.setText(bg_l10n.text(key))
        tip = bg_l10n.tooltip(key)
        self.btn_fs.setToolTip(tip)
        self.btn_fs.setStatusTip(tip)
        self.btn_fs.setProperty("bg_status_tip", tip)


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
                    pair_item.setForeground(0, QtGui.QColor("#ffffff"))
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
        if hasattr(self, 'schedule_dock_relayout'):
            self.schedule_dock_relayout()

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

    def _toc_pair_from_item(self, item):
        if not item or item.data(0, QtCore.Qt.UserRole) == "BOOK":
            return None
        pair_id = item.data(0, QtCore.Qt.UserRole)
        return next((p for p in self.root_pairs if p.get('id') == pair_id), None)

    def _toc_safe_chapter_base(self, raw_name):
        value = str(raw_name or "").strip().replace(" ", "_").replace(".", "_")
        value = re.sub(r'[^A-Za-z0-9_]+', '_', value)
        value = re.sub(r'_+', '_', value).strip('_')
        value = re.sub(r'(_HP|_LP)$', '', value, flags=re.IGNORECASE).strip('_')
        if not value:
            value = "Chapter"
        if not re.match(r'^[A-Za-z_]', value):
            value = "Chapter_{}".format(value)
        return value[:54] or "Chapter"

    def rename_toc_chapter(self, pair_id):
        pair = next((p for p in self.root_pairs if p.get('id') == pair_id), None)
        if not pair:
            return

        old_base = pair.get('base', 'Chapter')
        new_name, ok = QtWidgets.QInputDialog.getText(
            self,
            bg_l10n.text("Rename Chapter"),
            bg_l10n.text("New name for {name}:").format(name=old_base),
            QtWidgets.QLineEdit.Normal,
            old_base
        )
        if not ok:
            return

        new_base = self._toc_safe_chapter_base(new_name)
        if not new_base or new_base == old_base:
            return

        duplicate = next((p for p in self.root_pairs if p.get('id') != pair_id and p.get('base') == new_base), None)
        if duplicate:
            self.log("Rename Chapter skipped: '{}' already exists.".format(new_base), "orange")
            return

        hp_node, lp_node, _ = self.core.resolve_main_nodes(pair)
        with bg_core.undo_chunk("RenameChapter"):
            try:
                if hp_node and cmds.objExists(hp_node):
                    hp_node = cmds.rename(hp_node, "{}{}".format(new_base, bg_core.BakeConfig.SUFFIX_HP))
                    hp_node = (cmds.ls(hp_node, long=True) or [hp_node])[0]
                    pair['hp_uuid'] = cmds.ls(hp_node, uuid=True)[0]
                if lp_node and cmds.objExists(lp_node):
                    lp_node = cmds.rename(lp_node, "{}{}".format(new_base, bg_core.BakeConfig.SUFFIX_LP))
                    lp_node = (cmds.ls(lp_node, long=True) or [lp_node])[0]
                    pair['lp_uuid'] = cmds.ls(lp_node, uuid=True)[0]

                pair['base'] = new_base
                if hasattr(self.core, '_node_cache'):
                    self.core._node_cache.clear()
                bg_core.BakeSessionModel.save(self.root_pairs)
                self.refresh_right_panel()
                if pair_id == getattr(self, 'active_root_id', None):
                    self.refresh_left_panel()
                self.log("Chapter renamed: {} -> {}".format(old_base, new_base), "lightgreen")
                if hasattr(self, 'record_user_action'):
                    self.record_user_action("Rename Chapter", "{} -> {}".format(old_base, new_base))
            except Exception as e:
                self.log("Rename Chapter failed: {}".format(e), "red")

    def _toc_selected_mesh_transforms(self):
        selected = cmds.ls(selection=True, long=True, objectsOnly=True) or []
        transforms = []
        seen = set()
        for node in selected:
            if not node or not cmds.objExists(node):
                continue
            node_type = cmds.nodeType(node)
            if node_type == "mesh":
                parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
                node = parents[0] if parents else None
            elif node_type != "transform":
                continue
            if not node or not cmds.objExists(node):
                continue
            shapes = cmds.listRelatives(node, shapes=True, fullPath=True, type='mesh', noIntermediate=True) or []
            if not shapes:
                continue
            long_node = (cmds.ls(node, long=True) or [node])[0]
            if long_node not in seen:
                seen.add(long_node)
                transforms.append(long_node)
        return transforms

    def _toc_known_roots(self):
        roots = []
        for pair in self.root_pairs or []:
            hp_node, lp_node, _ = self.core.resolve_main_nodes(pair)
            if hp_node and cmds.objExists(hp_node):
                roots.append((pair, "HP", (cmds.ls(hp_node, long=True) or [hp_node])[0]))
            if lp_node and cmds.objExists(lp_node):
                roots.append((pair, "LP", (cmds.ls(lp_node, long=True) or [lp_node])[0]))
        roots.sort(key=lambda item: len(item[2]), reverse=True)
        return roots

    def _toc_mesh_root_kind(self, mesh_node, known_roots):
        long_node = (cmds.ls(mesh_node, long=True) or [mesh_node])[0]
        for pair, kind, root in known_roots:
            if long_node == root:
                return None, None
            if long_node.startswith(root + "|"):
                return pair, kind
        return None, None

    def _toc_meshes_by_kind(self, known_roots, kind):
        meshes = []
        seen = set()
        for _pair, root_kind, root in known_roots:
            if root_kind != kind or not root or not cmds.objExists(root):
                continue
            for node in (cmds.listRelatives(root, allDescendents=True, fullPath=True, type='transform') or []):
                if not node or not cmds.objExists(node):
                    continue
                if not cmds.listRelatives(node, shapes=True, fullPath=True, type='mesh', noIntermediate=True):
                    continue
                long_node = (cmds.ls(node, long=True) or [node])[0]
                if long_node not in seen:
                    seen.add(long_node)
                    meshes.append(long_node)
        return meshes

    def _toc_find_lost_sample_points(self, mesh_node, max_points=240):
        if not om or not mesh_node or not cmds.objExists(mesh_node):
            return self._create_by_mat_mesh_sample_points(mesh_node, max_points)
        try:
            sel = om.MSelectionList()
            sel.add(mesh_node)
            dag = sel.getDagPath(0)
            if dag.hasFn(om.MFn.kTransform):
                dag.extendToShape()
            mesh_fn = om.MFnMesh(dag)
            points = mesh_fn.getPoints(om.MSpace.kWorld)
            if len(points) == 0:
                return []

            result = []
            vert_budget = max(32, int(max_points * 0.45))
            vert_step = max(1, int(math.ceil(len(points) / float(vert_budget))))
            for index in range(0, len(points), vert_step):
                pnt = points[index]
                result.append([pnt.x, pnt.y, pnt.z])
                if len(result) >= vert_budget:
                    break

            face_budget = max_points - len(result)
            if face_budget > 0:
                counts, connects = mesh_fn.getVertices()
                if len(counts) > 0:
                    face_step = max(1, int(math.ceil(len(counts) / float(face_budget))))
                    cursor = 0
                    for face_id, count in enumerate(counts):
                        verts = list(connects[cursor:cursor + count])
                        cursor += count
                        if face_id % face_step != 0 or not verts:
                            continue
                        acc_x = acc_y = acc_z = 0.0
                        for vertex_id in verts:
                            pnt = points[vertex_id]
                            acc_x += pnt.x
                            acc_y += pnt.y
                            acc_z += pnt.z
                        inv = 1.0 / float(len(verts))
                        result.append([acc_x * inv, acc_y * inv, acc_z * inv])
                        if len(result) >= max_points:
                            break
            return result
        except Exception:
            return self._create_by_mat_mesh_sample_points(mesh_node, max_points)

    def _toc_find_lost_mesh_data(self, mesh_node):
        data = self._create_by_mat_mesh_data(mesh_node)
        if not data:
            return None
        data = dict(data)
        samples = self._toc_find_lost_sample_points(mesh_node, 240)
        if samples:
            data["sample_points"] = samples
        return data

    def _toc_find_lost_is_hp_floater_candidate(self, hp_data, lp_data):
        hp_diag = max(float(hp_data.get("diag", 0.0) or 0.0), 0.000001)
        lp_diag = max(float(lp_data.get("diag", 0.0) or 0.0), 0.000001)
        if lp_diag <= hp_diag * 1.20:
            return False
        hp_vol = max(float(hp_data.get("volume", hp_data.get("bbox_vol", 0.0)) or 0.0), 0.000001)
        lp_vol = max(float(lp_data.get("volume", lp_data.get("bbox_vol", 0.0)) or 0.0), 0.000001)
        diag_ratio = hp_diag / lp_diag
        vol_ratio = hp_vol / lp_vol
        return diag_ratio <= 0.45 or vol_ratio <= 0.16

    def _toc_find_lost_expanded_child_fraction(self, child_data, parent_data, expand):
        try:
            child_min = child_data.get("min")
            child_max = child_data.get("max")
            parent_min = parent_data.get("min")
            parent_max = parent_data.get("max")
            dx = max(0.0, min(child_max[0], parent_max[0] + expand) - max(child_min[0], parent_min[0] - expand))
            dy = max(0.0, min(child_max[1], parent_max[1] + expand) - max(child_min[1], parent_min[1] - expand))
            dz = max(0.0, min(child_max[2], parent_max[2] + expand) - max(child_min[2], parent_min[2] - expand))
            child_vol = max(float(child_data.get("volume", child_data.get("bbox_vol", 0.0)) or 0.0), 0.000001)
            return (dx * dy * dz) / child_vol
        except Exception:
            return 0.0

    def _toc_find_lost_hp_floater_score(self, hp_data, lp_data):
        if not self._toc_find_lost_is_hp_floater_candidate(hp_data, lp_data):
            return None

        inter = self._create_by_mat_intersection_volume(hp_data, lp_data)
        gap = self._create_by_mat_bbox_gap_distance(hp_data, lp_data)
        hp_diag = max(float(hp_data.get("diag", 0.0) or 0.0), 0.000001)
        lp_diag = max(float(lp_data.get("diag", 0.0) or 0.0), 0.000001)
        hp_vol = max(float(hp_data.get("volume", hp_data.get("bbox_vol", 0.0)) or 0.0), 0.000001)
        overlap_hp = inter / hp_vol
        expand = max(hp_diag * 0.35, lp_diag * 0.018, 0.001)
        expanded_fraction = self._toc_find_lost_expanded_child_fraction(hp_data, lp_data, expand)

        if inter <= 0.0 and expanded_fraction <= 0.0 and gap > max(hp_diag * 2.0, lp_diag * 0.08):
            return None

        hp_to_lp = self._create_by_mat_avg_sample_distance(hp_data, lp_data, max_source=180, max_target=260)
        closest = self._create_by_mat_point_sample_distance(hp_data, lp_data)
        near_limit = max(hp_diag * 0.38, lp_diag * 0.018, 0.001)
        closest_limit = max(hp_diag * 0.18, lp_diag * 0.008, 0.001)
        avg_norm = hp_to_lp / near_limit
        closest_norm = closest / closest_limit
        close_score = max(0.0, 1.0 - min(avg_norm, 1.0))
        closest_score = max(0.0, 1.0 - min(closest_norm, 1.0))
        size_bonus = max(0.0, 1.0 - min(hp_diag / max(lp_diag * 0.45, 0.000001), 1.0))
        gap_norm = gap / max(hp_diag, lp_diag * 0.015, 0.001)

        score = close_score * 980.0
        score += closest_score * 360.0
        score += min(expanded_fraction, 1.0) * 620.0
        score += min(overlap_hp, 1.0) * 460.0
        score += size_bonus * 130.0
        if inter > 0.0:
            score += 160.0
        score -= gap_norm * 100.0

        strong = (
            (expanded_fraction >= 0.20 and avg_norm <= 2.15) or
            (avg_norm <= 1.05 and closest_norm <= 1.60) or
            (overlap_hp >= 0.08 and closest_norm <= 1.20)
        )
        return {
            "score": score,
            "strong": strong,
            "floater": True,
            "overlap_source": overlap_hp,
            "overlap_target": expanded_fraction,
            "avg_distance": hp_to_lp,
            "closest_distance": closest
        }

    def _toc_find_lost_subgroup_name_for_mesh(self, mesh_node, pair, kind):
        if not mesh_node or not pair or kind not in ("HP", "LP"):
            return None
        try:
            hp_root, lp_root, _ = self.core.resolve_main_nodes(pair)
            root = hp_root if kind == "HP" else lp_root
            if not root or not cmds.objExists(root):
                return None
            root = (cmds.ls(root, long=True) or [root])[0]
            current = (cmds.ls(mesh_node, long=True) or [mesh_node])[0]
            direct_child = None
            while current and cmds.objExists(current) and current != root:
                parent = cmds.listRelatives(current, parent=True, fullPath=True) or []
                if not parent:
                    break
                if parent[0] == root:
                    direct_child = current
                    break
                current = parent[0]
            if not direct_child or cmds.listRelatives(direct_child, shapes=True, fullPath=True, type='mesh', noIntermediate=True):
                return None
            short_name = direct_child.split('|')[-1]
            pattern = r'(_HP|_hp|HP|hp)(\d*)$' if kind == "HP" else r'(_LP|_lp|LP|lp)(\d*)$'
            match = re.search(pattern, short_name)
            if match:
                return short_name[:match.start()] + match.group(2)
            return short_name
        except Exception:
            return None

    def _toc_find_lost_hp_target_for_lp(self, lp_node, known_roots):
        lp_pair, lp_kind = self._toc_mesh_root_kind(lp_node, known_roots)
        if not lp_pair or lp_kind != "LP":
            return None, None, None
        hp_root, lp_root, _ = self.core.resolve_main_nodes(lp_pair)
        if not hp_root or not cmds.objExists(hp_root):
            return None, lp_pair, None
        hp_target = (cmds.ls(hp_root, long=True) or [hp_root])[0]
        subgroup_name = self._toc_find_lost_subgroup_name_for_mesh(lp_node, lp_pair, "LP")
        if subgroup_name and hasattr(self, 'find_subgroup_nodes_by_ui_name'):
            hp_grp, _lp_grp = self.find_subgroup_nodes_by_ui_name(hp_root, lp_root, subgroup_name)
            if hp_grp and cmds.objExists(hp_grp):
                hp_target = (cmds.ls(hp_grp, long=True) or [hp_grp])[0]
        return hp_target, lp_pair, subgroup_name

    def _toc_find_lost_parent_hp_meshes(self, hp_nodes, hp_target):
        moved_nodes = []
        skipped = []
        if not hp_target or not cmds.objExists(hp_target):
            return moved_nodes, list(hp_nodes or [])
        hp_target = (cmds.ls(hp_target, long=True) or [hp_target])[0]
        for hp_node in hp_nodes or []:
            if not hp_node or not cmds.objExists(hp_node):
                skipped.append(hp_node)
                continue
            hp_node = (cmds.ls(hp_node, long=True) or [hp_node])[0]
            current_parent = cmds.listRelatives(hp_node, parent=True, fullPath=True) or []
            if current_parent and current_parent[0] == hp_target:
                moved_nodes.append(hp_node)
                continue
            try:
                parented = cmds.parent(hp_node, hp_target, absolute=True) or []
                moved_nodes.extend(cmds.ls(parented, long=True) or parented)
            except Exception as e:
                skipped.append("{} ({})".format(hp_node, e))
        return moved_nodes, skipped

    def _toc_find_lost_quick_score(self, source_data, target_data, source_kind):
        inter = self._create_by_mat_intersection_volume(source_data, target_data)
        gap = self._create_by_mat_bbox_gap_distance(source_data, target_data)
        source_diag = max(float(source_data.get("diag", 0.0) or 0.0), 0.000001)
        target_diag = max(float(target_data.get("diag", 0.0) or 0.0), 0.000001)
        if inter <= 0.0 and gap > max(source_diag * 0.9, target_diag * 0.9):
            return None
        source_vol = max(float(source_data.get("volume", 0.0) or 0.0), 0.000001)
        target_vol = max(float(target_data.get("volume", 0.0) or 0.0), 0.000001)
        overlap_source = inter / source_vol
        overlap_target = inter / target_vol
        center_dist = bg_core.MathUtils.distance(source_data.get("center", [0.0, 0.0, 0.0]), target_data.get("center", [0.0, 0.0, 0.0]))
        center_norm = center_dist / max(source_diag, target_diag, 0.000001)
        if source_kind == "LP":
            score = overlap_target * 520.0 + overlap_source * 220.0
        else:
            score = overlap_source * 520.0 + overlap_target * 220.0
        if inter > 0.0:
            score += 120.0
        score -= center_norm * 40.0
        score -= gap / max(min(source_diag, target_diag), 0.000001) * 55.0
        return score

    def _toc_find_lost_precise_score(self, source_data, target_data, source_kind):
        if source_kind == "LP":
            floater_score = self._toc_find_lost_hp_floater_score(target_data, source_data)
        else:
            floater_score = self._toc_find_lost_hp_floater_score(source_data, target_data)
        if floater_score:
            return floater_score

        inter = self._create_by_mat_intersection_volume(source_data, target_data)
        gap = self._create_by_mat_bbox_gap_distance(source_data, target_data)
        source_diag = max(float(source_data.get("diag", 0.0) or 0.0), 0.000001)
        target_diag = max(float(target_data.get("diag", 0.0) or 0.0), 0.000001)
        source_vol = max(float(source_data.get("volume", 0.0) or 0.0), 0.000001)
        target_vol = max(float(target_data.get("volume", 0.0) or 0.0), 0.000001)
        overlap_source = inter / source_vol
        overlap_target = inter / target_vol
        source_to_target = self._create_by_mat_avg_sample_distance(source_data, target_data, max_source=120, max_target=160)
        target_to_source = self._create_by_mat_avg_sample_distance(target_data, source_data, max_source=120, max_target=160)
        if source_kind == "LP":
            avg_dist = target_to_source
            fit_overlap = overlap_target
            cover_overlap = overlap_source
        else:
            avg_dist = source_to_target
            fit_overlap = overlap_source
            cover_overlap = overlap_target
        near_limit = max(min(source_diag, target_diag) * 0.28, max(source_diag, target_diag) * 0.025, 0.000001)
        avg_norm = avg_dist / near_limit
        score = fit_overlap * 1050.0 + cover_overlap * 260.0
        score += max(0.0, 1.0 - min(avg_norm, 1.0)) * 680.0
        if inter > 0.0:
            score += 180.0
        score -= gap / max(source_diag * 0.20, target_diag * 0.20, 0.000001) * 120.0
        strong = (
            fit_overlap >= 0.18 or
            (fit_overlap >= 0.05 and avg_norm <= 1.35) or
            (avg_norm <= 0.72 and gap <= max(source_diag * 0.10, target_diag * 0.10))
        )
        return {
            "score": score,
            "strong": strong,
            "overlap_source": overlap_source,
            "overlap_target": overlap_target,
            "avg_distance": avg_dist
        }

    def find_lost_meshes_from_selection(self, target_pair_id=None):
        selected_meshes = self._toc_selected_mesh_transforms()
        if not selected_meshes:
            self.log("Find Lost: select an LP or HP mesh first.", "orange")
            return

        known_roots = self._toc_known_roots()
        data_cache = {}
        meshes_by_kind = {
            "HP": self._toc_meshes_by_kind(known_roots, "HP"),
            "LP": self._toc_meshes_by_kind(known_roots, "LP")
        }
        result_nodes = []
        moved_nodes = []
        move_skipped = []
        seen_results = set()
        skipped = 0
        floater_hits = 0

        def mesh_data(node):
            if node not in data_cache:
                data_cache[node] = self._toc_find_lost_mesh_data(node)
            return data_cache.get(node)

        for source_node in selected_meshes:
            source_pair, source_kind = self._toc_mesh_root_kind(source_node, known_roots)
            if source_kind not in ("HP", "LP"):
                skipped += 1
                continue
            target_kind = "HP" if source_kind == "LP" else "LP"
            source_data = mesh_data(source_node)
            if not source_data:
                skipped += 1
                continue

            candidates = []
            for target_node in meshes_by_kind.get(target_kind, []):
                if target_node == source_node:
                    continue
                target_data = mesh_data(target_node)
                if not target_data:
                    continue
                quick = self._toc_find_lost_quick_score(source_data, target_data, source_kind)
                if quick is None:
                    continue
                candidates.append((quick, target_node, target_data))
            if not candidates:
                continue

            candidates.sort(key=lambda item: item[0], reverse=True)
            precise = []
            for _quick, target_node, target_data in candidates[:96]:
                score = self._toc_find_lost_precise_score(source_data, target_data, source_kind)
                if score and score.get("strong"):
                    precise.append((float(score.get("score", 0.0) or 0.0), target_node, score))
            if not precise:
                continue

            precise.sort(key=lambda item: item[0], reverse=True)
            best_score = precise[0][0]
            limit = max(best_score * 0.62, best_score - 220.0)
            source_results = []
            for score, target_node, _meta in precise[:16]:
                if score < limit:
                    continue
                if target_node not in seen_results:
                    seen_results.add(target_node)
                    result_nodes.append(target_node)
                    source_results.append((target_node, _meta))
                    if _meta.get("floater"):
                        floater_hits += 1

            if source_kind == "LP":
                hp_target, _lp_pair, subgroup_name = self._toc_find_lost_hp_target_for_lp(source_node, known_roots)
                hp_nodes = [node for node, _meta in source_results if node and cmds.objExists(node)]
                moved, move_failed = self._toc_find_lost_parent_hp_meshes(hp_nodes, hp_target)
                moved_nodes.extend(moved)
                move_skipped.extend(move_failed)
                if moved and subgroup_name and hasattr(self, 'recolor_moved_subgroup_nodes'):
                    self.recolor_moved_subgroup_nodes(moved, subgroup_name)
            elif source_kind == "HP" and source_results:
                best_lp = source_results[0][0]
                hp_target, _lp_pair, subgroup_name = self._toc_find_lost_hp_target_for_lp(best_lp, known_roots)
                moved, move_failed = self._toc_find_lost_parent_hp_meshes([source_node], hp_target)
                moved_nodes.extend(moved)
                move_skipped.extend(move_failed)
                if moved and subgroup_name and hasattr(self, 'recolor_moved_subgroup_nodes'):
                    self.recolor_moved_subgroup_nodes(moved, subgroup_name)

        if moved_nodes:
            unique_moved = []
            seen_moved = set()
            for node in moved_nodes:
                if node and cmds.objExists(node):
                    long_node = (cmds.ls(node, long=True) or [node])[0]
                    if long_node not in seen_moved:
                        seen_moved.add(long_node)
                        unique_moved.append(long_node)
            if unique_moved:
                cmds.select(unique_moved, replace=True)
            if hasattr(self, 'refresh_left_panel'):
                self.refresh_left_panel()
            if hasattr(self, 'refresh_right_panel'):
                self.refresh_right_panel()
            found_kind = "HP/LP"
            if unique_moved:
                _pair, result_kind = self._toc_mesh_root_kind(unique_moved[0], self._toc_known_roots())
                if result_kind:
                    found_kind = result_kind
            floater_note = " (floater mode: {})".format(floater_hits) if floater_hits else ""
            self.log("Find Lost: moved {} {} mesh(es){}.".format(len(unique_moved), found_kind, floater_note), "lightgreen")
            if move_skipped:
                names = [str(n).split('|')[-1] for n in move_skipped[:8]]
                self.log("Find Lost: skipped {} mesh(es): {}".format(len(move_skipped), ", ".join(names)), "orange")
            if hasattr(self, 'record_user_action'):
                self.record_user_action("Find Lost", "moved={} found={} skipped={} move_skipped={} floaters={}".format(len(unique_moved), len(result_nodes), skipped, len(move_skipped), floater_hits))
        elif result_nodes:
            cmds.select(result_nodes, replace=True)
            self.log("Find Lost: found {} candidate mesh(es), but nothing was moved.".format(len(result_nodes)), "orange")
        else:
            self.log("Find Lost: no matching opposite meshes found.", "orange")

    def move_selected_meshes_to_chapter(self, target_pair_id):
        target_pair = next((p for p in self.root_pairs if p.get('id') == target_pair_id), None)
        if not target_pair:
            return

        selected_meshes = self._toc_selected_mesh_transforms()
        if not selected_meshes:
            self.log("Move Mesh to: no selected mesh transforms.", "orange")
            return

        target_hp, target_lp, _ = self.core.resolve_main_nodes(target_pair)
        if not target_hp or not cmds.objExists(target_hp) or not target_lp or not cmds.objExists(target_lp):
            self.log("Move Mesh to failed: target chapter roots are missing.", "red")
            return

        target_hp = (cmds.ls(target_hp, long=True) or [target_hp])[0]
        target_lp = (cmds.ls(target_lp, long=True) or [target_lp])[0]
        known_roots = self._toc_known_roots()
        moved = 0
        skipped = []

        with bg_core.undo_chunk("MoveSelectedMeshesToChapter"):
            for mesh_node in selected_meshes:
                source_pair, kind = self._toc_mesh_root_kind(mesh_node, known_roots)
                if not source_pair or kind not in ("HP", "LP"):
                    skipped.append(mesh_node)
                    continue
                if source_pair.get('id') == target_pair_id:
                    continue
                target_root = target_hp if kind == "HP" else target_lp
                try:
                    cmds.parent(mesh_node, target_root, absolute=True)
                    moved += 1
                except Exception as e:
                    skipped.append("{} ({})".format(mesh_node, e))

        if moved:
            self.log("Move Mesh to '{}': moved {} mesh(es).".format(target_pair.get('base', 'Chapter'), moved), "lightgreen")
            if hasattr(self, 'record_user_action'):
                self.record_user_action("Move Mesh to Chapter", "{} | moved={}".format(target_pair.get('base', 'Chapter'), moved))
        if skipped:
            names = [str(n).split('|')[-1] for n in skipped[:8]]
            self.log("Move Mesh to: skipped {} item(s) outside known HP/LP roots or failed to move: {}".format(len(skipped), ", ".join(names)), "orange")
        self.refresh_right_panel()
        if target_pair_id == getattr(self, 'active_root_id', None):
            self.refresh_left_panel()

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
        clicked_item = self.toc_tree.itemAt(pos)
        if clicked_item and not clicked_item.isSelected():
            self.toc_tree.clearSelection()
            clicked_item.setSelected(True)
            items = [clicked_item]
        if not items:
            return
        clicked_pair = self._toc_pair_from_item(clicked_item)
        menu = QtWidgets.QMenu()
        menu.setStyleSheet("QMenu { background-color: #242424; color: #ddd; border: 1px solid #444; } QMenu::item:selected { background-color: #3e3e3e; }")

        act_sel = QAction("Select Meshes", self)
        act_sel.triggered.connect(self.select_toc_items)
        menu.addAction(act_sel)

        if clicked_pair:
            act_move_mesh = QAction("Move Mesh to", self)
            act_move_mesh.triggered.connect(lambda checked=False, p_id=clicked_pair.get('id'): self.run_undoable_bg_action("Move Mesh to Chapter", self.move_selected_meshes_to_chapter, p_id))
            menu.addAction(act_move_mesh)

            act_rename_chapter = QAction("Rename Chapter", self)
            act_rename_chapter.triggered.connect(lambda checked=False, p_id=clicked_pair.get('id'): self.run_undoable_bg_action("Rename Chapter", self.rename_toc_chapter, p_id))
            menu.addAction(act_rename_chapter)

            act_find_lost = QAction("Find Lost (beta)", self)
            act_find_lost.triggered.connect(lambda checked=False, p_id=clicked_pair.get('id'): self.run_undoable_bg_action("Find Lost", self.find_lost_meshes_from_selection, p_id))
            menu.addAction(act_find_lost)

            menu.addSeparator()

        act_group = QAction("Group into Book (Ctrl+G)", self)
        act_group.triggered.connect(lambda checked=False: self.run_undoable_bg_action("Group into Book", self.group_selected_into_book))
        menu.addAction(act_group)

        existing_books = sorted(list(set([p.get('book') for p in self.root_pairs if p.get('book')])))
        if existing_books:
            add_to_menu = menu.addMenu("Add to")
            add_to_menu.setStyleSheet("QMenu { background-color: #242424; color: #ddd; border: 1px solid #444; } QMenu::item:selected { background-color: #3e3e3e; }")
            for b_name in existing_books:
                action = QAction(b_name, self)
                action.triggered.connect(lambda checked=False, b=b_name: self.run_undoable_bg_action("Add to Book", self.add_selected_to_existing_book, b))
                add_to_menu.addAction(action)

        act_ungroup = QAction("Extract from the book", self)
        act_ungroup.triggered.connect(lambda checked=False: self.run_undoable_bg_action("Extract from Book", self.ungroup_toc_items))
        menu.addAction(act_ungroup)

        menu.addSeparator()
        act_del = QAction("Delete Selection", self)
        act_del.triggered.connect(lambda checked=False: self.run_undoable_bg_action("Delete TOC Selection", self.delete_toc_items))
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
