# -*- coding: utf-8 -*-
import sys
import os
import json
import math
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

    def _mesh_transforms_from_selection(self, nodes):
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

    def _category_from_node(self, node):
        current = node
        while current and cmds.objExists(current):
            try:
                if cmds.attributeQuery("BakeManagerGroup", node=current, exists=True):
                    value = cmds.getAttr(current + ".BakeManagerGroup")
                    if value in ("HP", "LP"):
                        return value
            except Exception:
                pass

            name = self._short(current).lower()
            if name.endswith("_hp") or name.endswith("hp") or "_high" in name:
                return "HP"
            if name.endswith("_lp") or name.endswith("lp") or "_low" in name:
                return "LP"

            parent = cmds.listRelatives(current, parent=True, fullPath=True) or []
            current = parent[0] if parent else None
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

    def _fill_percents(self, lp_info, hp_info):
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

    def _passes_display_filter(self, selected_category, lp_pct, hp_pct, min_overlap):
        if selected_category == "HP":
            return lp_pct >= min_overlap
        return max(lp_pct, hp_pct) >= min_overlap

    def calculate_selected_fill(self):
        self.fill_tree.clear()
        selected = cmds.ls(selection=True, long=True) or []
        selected_meshes = self._mesh_transforms_from_selection(selected)
        if not selected_meshes:
            self.log("<b>Fill:</b> Select LP or HP mesh transforms first.")
            return

        categories = sorted(set([self._category_from_node(m) for m in selected_meshes]))
        categories = [c for c in categories if c in ("LP", "HP")]
        if len(categories) != 1:
            self.log("<b>Fill:</b> Selection must contain meshes from one category only: LP or HP.")
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

        for lp_info in lp_infos:
            matches = []
            for hp_info in hp_infos:
                lp_pct, hp_pct, method = self._fill_percents(lp_info, hp_info)
                if not self._passes_display_filter(selected_category, lp_pct, hp_pct, min_overlap):
                    continue
                matches.append((hp_info, lp_pct, hp_pct, method))

            matches.sort(key=lambda item: (item[1], item[2], max(item[1], item[2])), reverse=True)
            if not matches:
                continue

            definite_nodes = [item[0]["node"] for item in matches if item[1] >= definite_lp]
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
                is_definite = lp_pct >= definite_lp
                info_text = "LP {:.1f}% / HP {:.1f}% [{}]".format(lp_pct, hp_pct, method)
                if is_definite:
                    info_text += " [Definite LP owner]"
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
            "<b>Fill:</b> {} selected {} mesh(es), compared against {} {} mesh(es). Found {} LP->HP link(s).".format(
                len(selected_meshes),
                selected_category,
                len(hp_meshes if selected_category == "LP" else lp_meshes),
                "HP" if selected_category == "LP" else "LP",
                total_links
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
            self.log("<b>Fill:</b> Selected {} node(s) from definite LP owner links.".format(len(nodes)))
        else:
            self.log("<b>Fill:</b> No definite LP owner links found.")

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
