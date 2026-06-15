# -*- coding: utf-8 -*-
from __future__ import print_function, division, absolute_import

import sys
import os
import uuid
import contextlib
from datetime import datetime

import maya.cmds as cmds
import bg_core
import bg_gt_matcher
import bg_final_export
import bg_localization as bg_l10n
import bg_update

from bg_worker_hp import HPGroupingWorker
from bg_worker_lp import LPMatchingWorker

try:
    from PySide6 import QtWidgets, QtCore, QtGui
    from maya.app.general.mayaMixin import MayaQWidgetDockableMixin
    QAction = QtGui.QAction
    QShortcut = QtGui.QShortcut
except ImportError:
    from PySide2 import QtWidgets, QtCore, QtGui
    from maya.app.general.mayaMixin import MayaQWidgetDockableMixin
    QAction = QtWidgets.QAction
    QShortcut = QtWidgets.QShortcut

from bg_ui_widgets import CollapsibleSection, FinalMeshLineEdit, SubgroupButton, ResolveNameDialog, get_icon
from bg_mixins import (HPAnalysisMixin, LPMatchingMixin, FinalViewMixin,
                       ExportMixin, GroupManagementMixin, SceneInteractionMixin, TOCMixin)


class BakeManagerUI(MayaQWidgetDockableMixin, QtWidgets.QMainWindow,
                    HPAnalysisMixin, LPMatchingMixin, FinalViewMixin,
                    ExportMixin, GroupManagementMixin, SceneInteractionMixin, TOCMixin):
    def __init__(self, parent=None):
        super(BakeManagerUI, self).__init__(parent=parent)
        self.setWindowTitle("Bake Group Manager Pro")
        self.setObjectName("BakeManagerUI")
        self.setMinimumSize(400, 400)
        self.resize(800, 800)
        self.setAcceptDrops(False)

        self.core = bg_core.MayaCore()
        self.root_pairs = bg_core.BakeSessionModel.load()
        self.active_root_id = None
        self.active_subgroup_name = None
        self.is_isolated = False
        self.script_jobs = []
        self.skip_delete_confirm = False
        self.picked_hp = None
        self.picked_lp = None
        self.hp_data_cache = {}
        self.lp_data_cache = {}
        self.is_preview_active = False
        self.is_final_view = False
        self.is_final_low_visible = False
        self.final_smooth_states = {}
        self.zbrush_triangle_threshold = 50
        self.last_debug_lines = []
        self.user_action_lines = []
        self.subgroup_color_override_cache = {}
        self.subgroup_color_index_map = {}
        self._is_closing = False
        self.update_worker = None
        self.update_dialog = None
        self.update_check_timer = None

        self.init_ui()
        self.apply_stylesheet()
        self.refresh_right_panel()
        self.setup_script_jobs()
        self.update_check_timer = QtCore.QTimer(self)
        self.update_check_timer.setSingleShot(True)
        self.update_check_timer.timeout.connect(self.start_update_check)
        self.update_check_timer.start(1200)

    # ------------------------------------------------------------------------
    # UI Initialization
    # ------------------------------------------------------------------------
    def init_ui(self):
        main_widget = QtWidgets.QWidget()
        self.setCentralWidget(main_widget)
        layout = QtWidgets.QHBoxLayout(main_widget)
        layout.setContentsMargins(10, 10, 10, 10)
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        layout.addWidget(self.splitter)

        # Left panel
        self.left_panel = QtWidgets.QWidget()
        self.left_panel.setMinimumWidth(150)
        left_layout = QtWidgets.QVBoxLayout(self.left_panel)
        left_layout.setContentsMargins(0, 0, 5, 0)
        left_layout.setSpacing(8)

        # ---- HP/LP selection ----
        g_frame = QtWidgets.QFrame()
        g_layout = QtWidgets.QVBoxLayout(g_frame)

        hp_layout = QtWidgets.QHBoxLayout()
        self.le_picked_hp = QtWidgets.QLineEdit()
        self.le_picked_hp.setReadOnly(True)
        self.le_picked_hp.setPlaceholderText("Pick HP...")
        self.le_picked_hp.setMinimumWidth(50)
        btn_pick_hp = QtWidgets.QPushButton("Pick HP")
        btn_pick_hp.clicked.connect(lambda: self.pick_node("HP"))
        hp_layout.addWidget(self.le_picked_hp, stretch=3)
        hp_layout.addWidget(btn_pick_hp, stretch=1)
        g_layout.addLayout(hp_layout)

        lp_layout = QtWidgets.QHBoxLayout()
        self.le_picked_lp = QtWidgets.QLineEdit()
        self.le_picked_lp.setReadOnly(True)
        self.le_picked_lp.setPlaceholderText("Pick LP...")
        self.le_picked_lp.setMinimumWidth(50)
        btn_pick_lp = QtWidgets.QPushButton("Pick LP")
        btn_pick_lp.clicked.connect(lambda: self.pick_node("LP"))
        lp_layout.addWidget(self.le_picked_lp, stretch=3)
        lp_layout.addWidget(btn_pick_lp, stretch=1)
        g_layout.addLayout(lp_layout)

        btn_create_main = QtWidgets.QPushButton(" Create Pair from Picked")
        btn_create_main.setIcon(get_icon("add_group.png"))
        btn_create_main.setStyleSheet("background-color: #3f523f; font-weight: bold; padding: 8px;")
        btn_create_main.clicked.connect(self.create_root_pair_from_picked)
        g_layout.addWidget(btn_create_main)

        tool_layout = QtWidgets.QHBoxLayout()
        self.cb_color_subgroups = QtWidgets.QCheckBox("Color Groups")
        self.cb_color_subgroups.setChecked(False)
        self.cb_color_subgroups.toggled.connect(self.on_color_by_subgroups_toggled)
        self.cb_keep_hp_structure = QtWidgets.QCheckBox("Keep HP")
        self.cb_keep_hp_structure.setChecked(False)
        self.cb_keep_hp_structure.toggled.connect(lambda checked: self.refresh_left_panel())

        self.btn_combine_mesh = QtWidgets.QPushButton("Combine")
        self.btn_combine_mesh.setStyleSheet("background-color: #3b5998;")
        self.btn_combine_mesh.clicked.connect(self.tool_combine)
        self.btn_separate_mesh = QtWidgets.QPushButton("Separate")
        self.btn_separate_mesh.setStyleSheet("background-color: #8c6239;")
        self.btn_separate_mesh.clicked.connect(self.tool_separate)
        self.btn_find_zbrush = QtWidgets.QPushButton("Find ZBrush")
        self.btn_find_zbrush.setStyleSheet("background-color: #d18c15; font-weight: bold;")
        self.btn_find_zbrush.clicked.connect(self.find_zbrush_meshes)
        self.btn_find_zbrush.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.btn_find_zbrush.customContextMenuRequested.connect(self.show_find_zbrush_context_menu)

        tool_layout.addWidget(self.btn_combine_mesh)
        tool_layout.addWidget(self.btn_separate_mesh)
        tool_layout.addWidget(self.btn_find_zbrush)

        tool_checks = QtWidgets.QVBoxLayout()
        tool_checks.setSpacing(0)
        tool_checks.addWidget(self.cb_color_subgroups)
        tool_checks.addWidget(self.cb_keep_hp_structure)
        tool_layout.addLayout(tool_checks)

        g_layout.addLayout(tool_layout)
        left_layout.addWidget(g_frame)

        # ---- Analysis block ----
        s_layout = QtWidgets.QHBoxLayout()
        self.btn_run_hp = QtWidgets.QPushButton(" Analyze HP")
        self.btn_run_hp.setIcon(get_icon("match_hp.png"))
        self.btn_run_hp.setStyleSheet("background-color: #f56342; font-weight: bold; color: white;")
        self.btn_run_hp.clicked.connect(lambda: self.run_hp_analysis(None))

        self.btn_run_lp = QtWidgets.QPushButton(" Assign LP Meshes")
        self.btn_run_lp.setIcon(get_icon("match_lp.png"))
        self.btn_run_lp.setStyleSheet("background-color: #2b8f41; font-weight: bold; color: white;")
        self.btn_run_lp.clicked.connect(self.run_lp_matching)
        s_layout.addWidget(self.btn_run_hp)
        s_layout.addWidget(self.btn_run_lp)
        left_layout.addLayout(s_layout)

        self.rebuild_algorithm_settings_ui(left_layout)

        # ---- Visibility block ----
        v_layout = QtWidgets.QHBoxLayout()
        self.btn_toggle_hp = QtWidgets.QPushButton("HP Visible")
        self.btn_toggle_hp.setCheckable(True)
        self.btn_toggle_hp.toggled.connect(lambda state: self.toggle_root_vis("HP", state))
        self.btn_toggle_lp = QtWidgets.QPushButton("LP Visible")
        self.btn_toggle_lp.setCheckable(True)
        self.btn_toggle_lp.toggled.connect(lambda state: self.toggle_root_vis("LP", state))
        self.btn_toggle_groups = QtWidgets.QPushButton("Groups Vis")
        self.btn_toggle_groups.setCheckable(True)
        self.btn_toggle_groups.toggled.connect(self.set_all_subgroups_vis)
        v_layout.addWidget(self.btn_toggle_hp)
        v_layout.addWidget(self.btn_toggle_lp)
        v_layout.addWidget(self.btn_toggle_groups)
        left_layout.addLayout(v_layout)

        # ---- Groups list (scroll area) ----
        self.subgroups_scroll = QtWidgets.QScrollArea()
        self.subgroups_scroll.setWidgetResizable(True)
        self.subgroups_widget = QtWidgets.QWidget()
        self.subgroups_widget.setStyleSheet("background-color: transparent;")
        self.subgroups_layout = QtWidgets.QVBoxLayout(self.subgroups_widget)
        self.subgroups_layout.setAlignment(QtCore.Qt.AlignTop)
        self.subgroups_scroll.setWidget(self.subgroups_widget)
        left_layout.addWidget(self.subgroups_scroll, 1) # Параметр 1 фиксирует верстку окна
        self.subgroups_scroll.setVisible(True)
        self.subgroups_widget.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.subgroups_widget.customContextMenuRequested.connect(self.show_subgroups_context_menu)

        # ---- Bottom action panel ----
        bl_layout = QtWidgets.QHBoxLayout()
        self.btn_fs = QtWidgets.QPushButton("Find Sim")
        self.btn_fs.setFixedHeight(30)
        self.btn_fs.setStyleSheet("background-color: #3b5998; font-weight: bold;")
        self.btn_fs.clicked.connect(self.find_similar_meshes_ui)
        self.btn_fs.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.btn_fs.customContextMenuRequested.connect(self.toggle_find_sim_mode)
        self.find_sim_mode = "SIM"

        self.btn_add = QtWidgets.QPushButton("Add to Act")
        self.btn_add.setFixedHeight(30)
        self.btn_add.setStyleSheet("background-color: #425c42; font-weight: bold;")
        self.btn_add.clicked.connect(self.add_to_selected_subgroup_ui)

        self.btn_combine_bake = QtWidgets.QPushButton("Combine Fin")
        self.btn_combine_bake.setFixedHeight(30)
        self.btn_combine_bake.setStyleSheet("background-color: #633f6b; font-weight: bold;")
        self.btn_combine_bake.clicked.connect(self.combine_all_subgroups_ui)

        self.btn_preview = QtWidgets.QPushButton("Smooth View")
        self.btn_preview.setFixedHeight(30)
        self.btn_preview.setStyleSheet("background-color: #3498db; font-weight: bold; color: white;")
        self.btn_preview.clicked.connect(self.toggle_preview_smoothing)
        self.btn_preview.setVisible(False)

        self.btn_toggle_view = QtWidgets.QPushButton("Final Group")
        self.btn_toggle_view.setFixedHeight(30)
        self.btn_toggle_view.setStyleSheet("background-color: #d35400; font-weight: bold;")
        self.btn_toggle_view.clicked.connect(self.toggle_final_view)

        self.btn_process_final = QtWidgets.QPushButton("Export")
        self.btn_process_final.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.btn_process_final.customContextMenuRequested.connect(self.show_export_context_menu)
        self.btn_process_final.setIcon(get_icon("export.png"))
        self.btn_process_final.setFixedHeight(30)
        self.btn_process_final.setStyleSheet("background-color: #27ae60; font-weight: bold; color: white;")
        self.btn_process_final.clicked.connect(self.export_final_group_ui)
        self.btn_process_final.setVisible(False)

        bl_layout.addWidget(self.btn_fs)
        bl_layout.addWidget(self.btn_add)
        bl_layout.addWidget(self.btn_combine_bake)
        bl_layout.addWidget(self.btn_preview)
        bl_layout.addWidget(self.btn_toggle_view)
        bl_layout.addWidget(self.btn_process_final)
        left_layout.addLayout(bl_layout)

        # Log output
        self.log_output = QtWidgets.QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumHeight(80)
        self.log_output.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.log_output.customContextMenuRequested.connect(self.show_log_context_menu)
        left_layout.addWidget(self.log_output)

        # Right panel (TOC)
        self.right_panel = QtWidgets.QWidget()
        self.right_panel.setMinimumWidth(150)
        right_layout = QtWidgets.QVBoxLayout(self.right_panel)
        right_layout.setContentsMargins(5, 0, 0, 0)

        self.right_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        right_layout.addWidget(self.right_splitter)

        self.gt_widget = bg_gt_matcher.GTWidget(self)
        self.right_splitter.addWidget(self.gt_widget)

        top_right_widget = QtWidgets.QWidget()
        top_right_layout = QtWidgets.QVBoxLayout(top_right_widget)
        top_right_layout.setContentsMargins(0, 0, 0, 0)

        lbl_toc = QtWidgets.QLabel("TABLE OF CONTENTS")
        lbl_toc.setAlignment(QtCore.Qt.AlignCenter)
        lbl_toc.setStyleSheet("font-weight: bold; background-color: #252526; border: 1px solid #444; padding: 6px;")
        top_right_layout.addWidget(lbl_toc)

        self.toc_tree = QtWidgets.QTreeWidget()
        self.toc_tree.setHeaderHidden(True)
        self.toc_tree.setColumnCount(2)
        header = self.toc_tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Fixed)
        header.resizeSection(1, 24)
        self.toc_tree.setIndentation(15)
        self.toc_tree.setDragEnabled(False)
        self.toc_tree.setAcceptDrops(False)
        self.toc_tree.setDragDropMode(QtWidgets.QAbstractItemView.NoDragDrop)
        self.toc_tree.setFocusPolicy(QtCore.Qt.NoFocus)
        self.toc_tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.toc_tree.itemClicked.connect(self.on_toc_clicked)
        self.toc_tree.itemDoubleClicked.connect(self.on_toc_double_clicked)
        self.toc_tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.toc_tree.customContextMenuRequested.connect(self.show_toc_context_menu)
        self.toc_tree.itemChanged.connect(self.on_toc_item_changed)

        self.shortcut_group = QShortcut(QtGui.QKeySequence("Ctrl+G"), self.toc_tree)
        self.shortcut_group.setContext(QtCore.Qt.WidgetShortcut)
        self.shortcut_group.activated.connect(self.group_selected_into_book)


        self.shortcut_select_by_mesh = QShortcut(QtGui.QKeySequence("Ctrl+Shift+Z"), self)
        self.shortcut_select_by_mesh.setContext(QtCore.Qt.ApplicationShortcut)
        self.shortcut_select_by_mesh.activated.connect(self.select_subgroup_by_selected_mesh)

        top_right_layout.addWidget(self.toc_tree)
        self.right_splitter.addWidget(top_right_widget)
        self.right_splitter.setSizes([400, 300])

        session_buttons_layout = QtWidgets.QHBoxLayout()
        session_buttons_layout.setSpacing(4)

        self.btn_save_session = QtWidgets.QPushButton("Save Session")
        self.btn_save_session.setFixedHeight(30)
        self.btn_save_session.setStyleSheet("background-color: #3b3b3b; font-weight: bold; border: 1px solid #555;")
        self.btn_save_session.clicked.connect(self.manual_save_session)
        session_buttons_layout.addWidget(self.btn_save_session)

        self.btn_load_session = QtWidgets.QPushButton("Load Session")
        self.btn_load_session.setFixedHeight(30)
        self.btn_load_session.clicked.connect(self.load_custom_session)
        session_buttons_layout.addWidget(self.btn_load_session)

        self.btn_language = QtWidgets.QPushButton("Language")
        self.btn_language.setFixedHeight(30)
        self.btn_language.clicked.connect(self.show_language_menu)
        session_buttons_layout.addWidget(self.btn_language)

        right_layout.addLayout(session_buttons_layout)

        self.splitter.addWidget(self.left_panel)
        self.splitter.addWidget(self.right_panel)
        self.splitter.setSizes([350, 250])
        bg_l10n.localize_widget_tree(self)
        self.chk_ignore_floaters.setChecked(True)

    def rebuild_algorithm_settings_ui(self, layout):
        self.algo_group = CollapsibleSection("Algorithm")
        self.input_suffix = QtWidgets.QLineEdit()
        self.input_suffix.setPlaceholderText("Group name")
        self.input_suffix.setMinimumWidth(70)
        btn_c_pair = QtWidgets.QPushButton("Create Group")
        btn_c_pair.setIcon(get_icon("add_group.png"))
        btn_c_pair.clicked.connect(self.create_subgroup_pair)
        self.algo_group.addHeaderWidget(self.input_suffix, stretch=1)
        self.algo_group.addHeaderWidget(btn_c_pair)

        algo_label = QtWidgets.QLabel("HP Clustering Strategy:")
        self.combo_hp_strategy = QtWidgets.QComboBox()
        self.combo_hp_strategy.addItems([
            "Spatial Volume Match",
            "PCA Shape Alignment",
            "Topology Fingerprint"
        ])
        self.combo_hp_strategy.setCurrentIndex(1)
        self.algo_group.addWidget(algo_label)
        self.algo_group.addWidget(self.combo_hp_strategy)

        grid = QtWidgets.QGridLayout()
        grid.addWidget(QtWidgets.QLabel("HP Collision (%):"), 0, 0)
        self.spin_threshold = QtWidgets.QSpinBox()
        self.spin_threshold.setRange(0, 100)
        self.spin_threshold.setValue(15)
        self.spin_collision_pct = self.spin_threshold
        grid.addWidget(self.spin_threshold, 0, 1)

        self.hp_group_limit = 12
        self.chk_ignore_floaters = QtWidgets.QCheckBox("Ignore Floaters")
        self.chk_ignore_floaters.setChecked(True)
        grid.addWidget(self.chk_ignore_floaters, 0, 2, 1, 2)

        grid.addWidget(QtWidgets.QLabel("HP Link Vtx:"), 1, 0)
        self.spin_compound_link_verts = QtWidgets.QSpinBox()
        self.spin_compound_link_verts.setRange(1, 500)
        self.spin_compound_link_verts.setValue(8)
        grid.addWidget(self.spin_compound_link_verts, 1, 1)

        grid.addWidget(QtWidgets.QLabel("HP Link Dist (%):"), 1, 2)
        self.spin_compound_link_dist = QtWidgets.QDoubleSpinBox()
        self.spin_compound_link_dist.setRange(0.01, 25.0)
        self.spin_compound_link_dist.setDecimals(2)
        self.spin_compound_link_dist.setValue(0.1)
        self.spin_compound_link_dist.setSingleStep(0.05)
        grid.addWidget(self.spin_compound_link_dist, 1, 3)

        self.algo_group.addLayout(grid)

        self.chk_use_symmetry = QtWidgets.QCheckBox("Calculate Symmetry Score (.pyd)")
        self.chk_use_symmetry.setChecked(True)

        # Advanced HP heuristics are intentionally kept out of the artist-facing UI,
        # but the worker still reads these widgets for its internal defaults.
        self.spin_bolt_elong = QtWidgets.QDoubleSpinBox()
        self.spin_bolt_elong.setRange(1.0, 10.0)
        self.spin_bolt_elong.setValue(1.5)
        self.spin_bolt_elong.setSingleStep(0.1)

        self.spin_bolt_sym = QtWidgets.QDoubleSpinBox()
        self.spin_bolt_sym.setRange(0.0, 5.0)
        self.spin_bolt_sym.setValue(0.8)
        self.spin_bolt_sym.setSingleStep(0.1)

        self.spin_wire_elong = QtWidgets.QDoubleSpinBox()
        self.spin_wire_elong.setRange(1.0, 20.0)
        self.spin_wire_elong.setValue(4.0)
        self.spin_wire_elong.setSingleStep(0.1)
        self.algo_group.toggle_button.setChecked(True)
        self.algo_group.on_pressed()
        layout.addWidget(self.algo_group)

    def apply_stylesheet(self):
        style = """
            QMainWindow, QWidget { background-color: #242424; color: #d0d0d0; font-family: 'Segoe UI', Arial, sans-serif; font-size: 11px; }
            QPushButton { background-color: #333333; border: 1px solid #444444; border-radius: 4px; padding: 6px; }
            QPushButton:hover { background-color: #444444; border: 1px solid #666666; }
            QPushButton:pressed { background-color: #1a1a1a; }
            QLineEdit, QTextEdit { background: #1e1e1e; border: 1px solid #333; border-radius: 4px; color: #ccc; padding: 4px; }
            QTreeWidget { background-color: #1e1e1e; border: 1px solid #333333; border-radius: 4px; outline: 0; }
            QTreeWidget::item { padding: 4px 2px; margin-bottom: 2px; border-radius: 3px; }
            QTreeWidget::item:hover { background-color: #3e3e3e; }
            QTreeWidget::item:selected { background-color: #3a5375; color: #ffffff; }
            QScrollArea { border: none; background-color: transparent; }
            QSplitter::handle { background: #333333; }
            QSplitter::handle:horizontal { width: 2px; }
            QFrame { border: 1px solid #333; border-radius: 4px; background-color: #2a2a2a; }
            QGroupBox { border: 1px solid #444; margin-top: 10px; border-radius: 4px; padding-top: 10px; font-weight: bold; }
            QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top center; padding: 0 3px; color: #aaa; }
        """
        self.setStyleSheet(style)

    # ------------------------------------------------------------------------
    # Core methods (logging, script jobs, session, isolation, etc.)
    # ------------------------------------------------------------------------
    def log(self, msg, color="white"):
        self.log_output.append("<font color='{}'>{}</font>".format(color, msg))
        self.log_output.verticalScrollBar().setValue(self.log_output.verticalScrollBar().maximum())

    def record_user_action(self, action, detail=""):
        try:
            pair = next((p for p in self.root_pairs if p.get('id') == self.active_root_id), None)
            chapter = pair.get('base', 'None') if pair else 'None'
        except Exception:
            chapter = 'None'

        line = "[{}] {} | chapter={}".format(datetime.now().strftime("%H:%M:%S"), action, chapter)
        if detail:
            line += " | {}".format(detail)

        self.user_action_lines.append(line)
        if len(self.user_action_lines) > 500:
            self.user_action_lines = self.user_action_lines[-500:]

    def _format_debug_names(self, names, limit=30):
        names = [str(n) for n in (names or [])]
        if len(names) <= limit:
            return ", ".join(names)
        return "{}, ... +{} more".format(", ".join(names[:limit]), len(names) - limit)

    def build_current_scene_snapshot(self):
        lines = []
        pair = next((p for p in self.root_pairs if p.get('id') == self.active_root_id), None)
        if not pair:
            return ["No active chapter selected."]

        hp_main, _, _ = self.core.resolve_main_nodes(pair)
        lines.append("Active chapter: {}".format(pair.get('base', 'Unknown')))

        def collect_group_snapshot(root_node, label):
            output = []
            if not root_node or not cmds.objExists(root_node):
                return ["{} root: missing".format(label)]

            children = cmds.listRelatives(root_node, children=True, fullPath=True, type='transform') or []
            direct_meshes = []
            groups = []
            for child in children:
                if cmds.listRelatives(child, shapes=True, type='mesh'):
                    direct_meshes.append(child.split('|')[-1])
                else:
                    groups.append(child)

            output.append("{} root: {}".format(label, root_node))
            if direct_meshes:
                output.append("{} direct meshes: {}".format(label, len(direct_meshes)))
                output.append("  meshes: {}".format(self._format_debug_names(direct_meshes)))

            if not groups:
                return output

            output.append("{} groups: {}".format(label, len(groups)))

            for group in groups:
                group_short = group.split('|')[-1]
                mesh_transforms = []
                children = cmds.listRelatives(group, children=True, fullPath=True, type='transform') or []
                for child in children:
                    if cmds.listRelatives(child, shapes=True, type='mesh'):
                        mesh_transforms.append(child.split('|')[-1])
                output.append("  {} | count={}".format(group_short, len(mesh_transforms)))
                if mesh_transforms:
                    output.append("    meshes: {}".format(self._format_debug_names(mesh_transforms)))
            return output

        lines.extend(collect_group_snapshot(hp_main, "HP"))
        lines.extend(collect_group_snapshot(lp_main, "LP"))
        return lines

    def show_log_context_menu(self, pos):
        menu = self.log_output.createStandardContextMenu()
        menu.addSeparator()
        action_save_debug = menu.addAction(bg_l10n.text("Save Debug Log"))
        action_save_debug.setEnabled(bool(getattr(self, 'last_debug_lines', []) or getattr(self, 'user_action_lines', [])))
        action_save_debug.triggered.connect(self.save_debug_log)
        global_pos = self.log_output.mapToGlobal(pos)
        if hasattr(menu, 'exec_'):
            menu.exec_(global_pos)
        else:
            menu.exec(global_pos)

    def save_debug_log(self):
        debug_lines = list(getattr(self, 'last_debug_lines', []) or [])
        action_lines = list(getattr(self, 'user_action_lines', []) or [])
        if not debug_lines and not action_lines:
            self.log(bg_l10n.text("No debug log to save yet. Run Analyze HP first."), "orange")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_path = os.path.join(os.path.expanduser("~"), "Desktop", "BakeGroups_Debug_{}.txt".format(timestamp))
        file_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            bg_l10n.text("Save Debug Log"),
            default_path,
            bg_l10n.text("Text Files (*.txt);;All Files (*)")
        )
        if not file_path:
            return
        if not os.path.splitext(file_path)[1]:
            file_path += ".txt"

        scene_name = cmds.file(q=True, sn=True) or "Untitled"
        visible_log = self.log_output.toPlainText()
        report = []
        report.append("Bake Groups Debug Log")
        report.append("Saved: {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        report.append("Scene: {}".format(scene_name))
        report.append("")
        report.append("=== Visible Log ===")
        report.append(visible_log if visible_log else "(empty)")
        report.append("")
        report.append("=== User Actions ===")
        report.extend(action_lines if action_lines else ["(no recorded user actions)"])
        report.append("")
        report.append("=== Current Scene Snapshot ===")
        report.extend(self.build_current_scene_snapshot())
        report.append("")
        report.append("=== Analyze HP Debug ===")
        report.extend(debug_lines if debug_lines else ["(Analyze HP debug is not available for this session.)"])

        try:
            with open(file_path, "w", encoding="utf-8") as handle:
                handle.write("\n".join(report))
            self.log(bg_l10n.text("Debug log saved: {path}").format(path=file_path), "lightgreen")
        except Exception as exc:
            self.log(bg_l10n.text("Failed to save debug log: {error}").format(error=exc), "red")

    def setup_script_jobs(self):
        self.script_jobs.append(cmds.scriptJob(event=["SceneOpened", self.reload_data_from_scene]))

    def start_update_check(self):
        if self._is_closing:
            return
        if self.update_worker and self.update_worker.isRunning():
            return
        worker = bg_update.UpdateCheckWorker()
        self.update_worker = worker
        worker.update_result.connect(self.handle_update_check_result)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(self.clear_update_worker)
        worker.start()

    def handle_update_check_result(self, result):
        if self._is_closing:
            return
        try:
            self.objectName()
        except RuntimeError:
            return
        if not result or not result.get("is_update_available"):
            return
        self.update_dialog = bg_update.show_update_dialog(result, self)
        self.update_dialog.destroyed.connect(self.clear_update_dialog)

    def clear_update_worker(self):
        sender = self.sender()
        if sender is None or sender == self.update_worker:
            self.update_worker = None

    def clear_update_dialog(self, *args):
        self.update_dialog = None

    def on_color_by_subgroups_toggled(self, checked):
        if checked:
            self.subgroup_color_index_map = {}
            self.update_subgroup_colors()
        else:
            self.restore_subgroup_colors()
            self.subgroup_color_index_map = {}
        self.refresh_left_panel()

    def subgroup_color_for_name(self, name):
        palette = [
            (0.953, 0.071, 0.027),
            (0.988, 0.424, 0.012),
            (0.953, 0.851, 0.443),
            (0.196, 0.831, 0.145),
            (0.145, 0.831, 0.824),
            (0.090, 0.247, 0.827),
            (0.647, 0.090, 0.827),
            (0.827, 0.090, 0.820),
            (1.000, 1.000, 1.000),
            (0.000, 0.506, 0.000),
            (0.000, 0.506, 0.482),
            (0.000, 0.000, 0.498),
            (0.114, 0.000, 0.498),
            (0.498, 0.000, 0.478),
            (0.518, 0.518, 0.518),
            (0.518, 0.176, 0.110),
            (0.278, 0.204, 0.129),
            (0.518, 0.459, 0.404),
            (1.000, 0.945, 0.145),
        ]
        index_map = getattr(self, 'subgroup_color_index_map', {}) or {}
        if name in index_map:
            index = index_map[name]
        else:
            value = 0
            for char_index, char in enumerate(str(name or "")):
                value += (char_index + 1) * ord(char)
            index = value
        base = palette[index % len(palette)]
        pass_index = index // len(palette)
        shade = 1.0 if pass_index == 0 else max(0.38, 0.62 ** pass_index)
        return tuple(max(0.0, min(1.0, channel * shade)) for channel in base)

    def ensure_subgroup_color_indices(self, names, reset=False):
        if reset:
            self.subgroup_color_index_map = {}
        index_map = getattr(self, 'subgroup_color_index_map', None)
        if index_map is None:
            index_map = {}
            self.subgroup_color_index_map = index_map
        next_index = max(index_map.values()) + 1 if index_map else 0
        for name in names or []:
            if name not in index_map:
                index_map[name] = next_index
                next_index += 1
        return index_map

    def color_to_qss_rgb(self, color, scale=255):
        return "rgb({}, {}, {})".format(
            max(0, min(255, int(color[0] * scale))),
            max(0, min(255, int(color[1] * scale))),
            max(0, min(255, int(color[2] * scale)))
        )

    def color_to_qss_rgba(self, color, alpha):
        return "rgba({}, {}, {}, {})".format(
            max(0, min(255, int(color[0] * 255))),
            max(0, min(255, int(color[1] * 255))),
            max(0, min(255, int(color[2] * 255))),
            max(0, min(255, int(alpha)))
        )

    def viewport_subgroup_color(self, color):
        neutral = 0.22
        mix = 0.18
        brightness = 0.68
        return tuple(
            max(0.0, min(0.85, ((channel * (1.0 - mix)) + (neutral * mix)) * brightness))
            for channel in color
        )

    def subgroup_row_style(self, subgroup_name, active=False):
        if hasattr(self, 'cb_color_subgroups') and self.cb_color_subgroups.isChecked():
            color = self.subgroup_color_for_name(subgroup_name)
            border = "#f1f5f2" if active else self.color_to_qss_rgb(color)
            bg = self.color_to_qss_rgba(color, 30 if not active else 66)
            width = 2 if active else 1
            return "QFrame { background-color: %s; border: %dpx solid %s; border-radius: 4px; }" % (bg, width, border)
        if active:
            return "QFrame { background-color: rgba(88, 129, 96, 48); border: 2px solid #f1f5f2; border-radius: 4px; }"
        return "QFrame { background-color: transparent; border: 1px solid #333; border-radius: 4px; }"

    def subgroup_name_style(self, subgroup_name, active=False):
        if hasattr(self, 'cb_color_subgroups') and self.cb_color_subgroups.isChecked():
            color = self.subgroup_color_for_name(subgroup_name)
            bg = self.color_to_qss_rgba(color, 68 if active else 28)
            border = self.color_to_qss_rgba(color, 180 if active else 95)
            weight = "bold" if active else "normal"
            return "background-color: %s; border: 1px solid %s; border-radius: 3px; font-weight: %s; text-align: left; padding-left: 5px;" % (bg, border, weight)
        if active:
            return "background-color: #3a5375; font-weight: bold; text-align: left; padding-left: 5px;"
        return "background-color: transparent; text-align: left; padding-left: 5px;"

    def subgroup_add_button_style(self, active=False):
        if active:
            return "QPushButton { background-color: #8fbd72; color: #102410; border: 1px solid #e7f5d8; border-radius: 4px; font-weight: bold; padding: 0px; } QPushButton:hover { background-color: #9cca7f; }"
        return "QPushButton { background-color: #425c42; color: white; border: 1px solid #425c42; border-radius: 4px; font-weight: bold; } QPushButton:hover { background-color: #4f704f; }"

    def iter_colorable_nodes(self, transform):
        if not transform or not cmds.objExists(transform):
            return []
        if cmds.nodeType(transform) == "mesh":
            if not cmds.getAttr(transform + ".intermediateObject"):
                return [transform]
            return []
        transforms = [transform]
        transforms.extend(cmds.listRelatives(transform, allDescendents=True, type='transform', fullPath=True) or [])
        nodes = []
        seen = set()
        for item in transforms:
            if not item or not cmds.objExists(item):
                continue
            shapes = cmds.listRelatives(item, shapes=True, type='mesh', noIntermediate=True, fullPath=True) or []
            for shape in shapes:
                if shape in seen:
                    continue
                if cmds.objExists(shape) and cmds.attributeQuery("overrideEnabled", node=shape, exists=True):
                    seen.add(shape)
                    nodes.append(shape)
        return nodes

    def store_override_state(self, node):
        if node in self.subgroup_color_override_cache:
            return
        attrs = {}
        try:
            node_uuid = cmds.ls(node, uuid=True) or []
            if node_uuid:
                attrs["__uuid"] = node_uuid[0]
        except Exception:
            pass
        for attr in ("overrideEnabled", "overrideRGBColors", "overrideColor"):
            plug = "{}.{}".format(node, attr)
            if cmds.objExists(plug):
                try:
                    attrs[attr] = cmds.getAttr(plug)
                except Exception:
                    pass
        plug = "{}.overrideColorRGB".format(node)
        if cmds.objExists(plug):
            try:
                value = cmds.getAttr(plug)
                attrs["overrideColorRGB"] = value[0] if isinstance(value, list) else value
            except Exception:
                pass
        for attr in ("displayColors", "displayColorChannel"):
            plug = "{}.{}".format(node, attr)
            if cmds.objExists(plug):
                try:
                    attrs[attr] = cmds.getAttr(plug)
                except Exception:
                    pass
        target = self.color_target_for_shape(node)
        if target:
            try:
                color_sets = cmds.polyColorSet(target, query=True, allColorSets=True) or []
                attrs["__had_color_set"] = "BG_Subgroup_Color" in color_sets
                current = cmds.polyColorSet(target, query=True, currentColorSet=True) or []
                if current:
                    attrs["__current_color_set"] = current[0]
            except Exception:
                pass
        self.subgroup_color_override_cache[node] = attrs

    def color_target_for_shape(self, node):
        if not node or not cmds.objExists(node):
            return None
        if cmds.nodeType(node) == "mesh":
            parent = cmds.listRelatives(node, parent=True, fullPath=True) or []
            return parent[0] if parent else None
        shapes = cmds.listRelatives(node, shapes=True, type='mesh', noIntermediate=True, fullPath=True) or []
        return node if shapes else None

    def mesh_shape_for_color_node(self, node):
        if not node or not cmds.objExists(node):
            return None
        if cmds.nodeType(node) == "mesh":
            return node
        shapes = cmds.listRelatives(node, shapes=True, type='mesh', noIntermediate=True, fullPath=True) or []
        return shapes[0] if shapes else None

    def ensure_subgroup_color_set(self, target):
        color_set = "BG_Subgroup_Color"
        color_sets = cmds.polyColorSet(target, query=True, allColorSets=True) or []
        if color_set not in color_sets:
            cmds.polyColorSet(target, create=True, colorSet=color_set)
        cmds.polyColorSet(target, currentColorSet=True, colorSet=color_set)
        return color_set

    def apply_override_color(self, node, color):
        if not node or not cmds.objExists(node):
            return
        self.store_override_state(node)
        target = self.color_target_for_shape(node)
        shape = self.mesh_shape_for_color_node(node)
        if not target:
            return
        try:
            self.ensure_subgroup_color_set(target)
            cmds.polyColorPerVertex(target, rgb=self.viewport_subgroup_color(color), colorDisplayOption=True, notUndoable=True)
            if shape and cmds.objExists("{}.displayColors".format(shape)):
                cmds.setAttr("{}.displayColors".format(shape), True)
            if shape and cmds.objExists("{}.displayColorChannel".format(shape)):
                cmds.setAttr("{}.displayColorChannel".format(shape), "color", type="string")
            try:
                cmds.polyOptions(target, colorShadedDisplay=True, colorMaterialChannel="ambientDiffuse")
            except Exception:
                pass
            return True
        except Exception:
            return False

    def delete_preview_materials(self):
        initial_sg = "initialShadingGroup"
        for sg in cmds.ls("BG_ColorPreview_SG_*") or []:
            if initial_sg and cmds.objExists(initial_sg):
                try:
                    members = cmds.sets(sg, query=True) or []
                except Exception:
                    members = []
                for member in members:
                    try:
                        cmds.sets(member, edit=True, forceElement=initial_sg)
                    except Exception:
                        pass
            try:
                cmds.delete(sg)
            except Exception:
                pass
        for node in cmds.ls("BG_ColorPreview_MAT_*") or []:
            try:
                cmds.delete(node)
            except Exception:
                pass

    def restore_subgroup_colors(self):
        cache = getattr(self, 'subgroup_color_override_cache', {})
        for node, attrs in list(cache.items()):
            if not cmds.objExists(node):
                node_uuid = attrs.get("__uuid")
                matches = cmds.ls(node_uuid, long=True) if node_uuid else []
                if matches:
                    node = matches[0]
                else:
                    continue
            try:
                if "overrideRGBColors" in attrs:
                    cmds.setAttr("{}.overrideRGBColors".format(node), attrs["overrideRGBColors"])
                if "overrideColorRGB" in attrs:
                    rgb = attrs["overrideColorRGB"]
                    cmds.setAttr("{}.overrideColorRGB".format(node), rgb[0], rgb[1], rgb[2])
                if "overrideColor" in attrs:
                    cmds.setAttr("{}.overrideColor".format(node), attrs["overrideColor"])
                if "overrideEnabled" in attrs:
                    cmds.setAttr("{}.overrideEnabled".format(node), attrs["overrideEnabled"])
                target = self.color_target_for_shape(node)
                has_external_color_set = False
                if target:
                    try:
                        color_sets = cmds.polyColorSet(target, query=True, allColorSets=True) or []
                        if "BG_Subgroup_Color" in color_sets:
                            cmds.polyColorSet(target, delete=True, colorSet="BG_Subgroup_Color")
                            color_sets = [name for name in color_sets if name != "BG_Subgroup_Color"]
                        has_external_color_set = bool(color_sets)
                    except Exception:
                        pass
                    if attrs.get("__current_color_set"):
                        try:
                            cmds.polyColorSet(target, currentColorSet=True, colorSet=attrs["__current_color_set"])
                        except Exception:
                            pass
                if "displayColorChannel" in attrs and cmds.objExists("{}.displayColorChannel".format(node)):
                    cmds.setAttr("{}.displayColorChannel".format(node), attrs["displayColorChannel"], type="string")
                if "displayColors" in attrs and cmds.objExists("{}.displayColors".format(node)):
                    cmds.setAttr("{}.displayColors".format(node), False if has_external_color_set else attrs["displayColors"])
            except Exception:
                pass
        self.subgroup_color_override_cache = {}
        self.delete_preview_materials()

    @contextlib.contextmanager
    def suspend_subgroup_color_preview(self):
        was_enabled = hasattr(self, 'cb_color_subgroups') and self.cb_color_subgroups.isChecked()
        if was_enabled:
            self.restore_subgroup_colors()
        try:
            yield
        finally:
            if was_enabled and hasattr(self, 'cb_color_subgroups') and self.cb_color_subgroups.isChecked():
                self.update_subgroup_colors()

    def refresh_subgroup_color_preview(self, reset_indices=False):
        if not hasattr(self, 'cb_color_subgroups') or not self.cb_color_subgroups.isChecked():
            return
        if reset_indices:
            self.subgroup_color_index_map = {}
        self.update_subgroup_colors()

    def update_subgroup_colors(self):
        if not hasattr(self, 'cb_color_subgroups') or not self.cb_color_subgroups.isChecked():
            return
        self.restore_subgroup_colors()
        if not self.active_root_id:
            return
        if getattr(self, 'is_final_view', False):
            colored_count = 0
            self.ensure_subgroup_color_indices([w.get('subgroup_name') for w in getattr(self, 'final_mesh_widgets', []) if w.get('subgroup_name')])
            for widget_data in getattr(self, 'final_mesh_widgets', []):
                name = widget_data.get('subgroup_name')
                color = self.subgroup_color_for_name(name)
                for node in widget_data.get('hp_nodes', []):
                    for color_node in self.iter_colorable_nodes(node):
                        if self.apply_override_color(color_node, color):
                            colored_count += 1
            self.log("Color Groups: colored {} mesh shapes.".format(colored_count), "lightblue")
            return
        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not pair:
            return
        hp_main, lp_main, _ = self.core.resolve_main_nodes(pair)
        group_names = []
        for root in (hp_main,):
            if not root or not cmds.objExists(root):
                continue
            for child in cmds.listRelatives(root, children=True, type='transform', fullPath=True) or []:
                if not cmds.objExists(child) or cmds.listRelatives(child, shapes=True, type='mesh', noIntermediate=True):
                    continue
                short_name = child.split('|')[-1]
                ui_name = short_name
                for suffix in (bg_core.BakeConfig.SUFFIX_HP, bg_core.BakeConfig.SUFFIX_LP):
                    if ui_name.endswith(suffix):
                        ui_name = ui_name[:-len(suffix)]
                        break
                if ui_name not in group_names:
                    group_names.append(ui_name)
        self.ensure_subgroup_color_indices(sorted(group_names))
        colored_count = 0
        for root, suffix in ((hp_main, bg_core.BakeConfig.SUFFIX_HP),):
            if not root or not cmds.objExists(root):
                continue
            children = cmds.listRelatives(root, children=True, type='transform', fullPath=True) or []
            for child in children:
                if not cmds.objExists(child) or cmds.listRelatives(child, shapes=True, type='mesh', noIntermediate=True):
                    continue
                short_name = child.split('|')[-1]
                if not self.cb_keep_hp_structure.isChecked():
                    attr = "{}.{}".format(child, bg_core.BakeConfig.ATTR_BAKE_GROUP)
                    if cmds.objExists(attr):
                        group_type = cmds.getAttr(attr)
                        if suffix == bg_core.BakeConfig.SUFFIX_HP and group_type != "HP":
                            continue
                    elif not short_name.endswith(suffix):
                        continue
                ui_name = short_name
                if short_name.endswith(suffix):
                    ui_name = short_name[:-len(suffix)]
                color = self.subgroup_color_for_name(ui_name)
                for color_node in self.iter_colorable_nodes(child):
                    if self.apply_override_color(color_node, color):
                        colored_count += 1
        self.log("Color Groups: colored {} mesh shapes.".format(colored_count), "lightblue")

    def recolor_moved_subgroup_nodes(self, nodes, subgroup_name):
        if not hasattr(self, 'cb_color_subgroups') or not self.cb_color_subgroups.isChecked():
            return
        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not pair:
            return
        hp_main, _, _ = self.core.resolve_main_nodes(pair)
        if not hp_main or not cmds.objExists(hp_main):
            return
        self.ensure_subgroup_color_indices([subgroup_name])
        color = self.subgroup_color_for_name(subgroup_name)
        colored_count = 0
        for node in nodes or []:
            if not node or not cmds.objExists(node):
                continue
            if not self.core.is_descendant_of(node, hp_main):
                continue
            for color_node in self.iter_colorable_nodes(node):
                if self.apply_override_color(color_node, color):
                    colored_count += 1
        if colored_count:
            self.log("Color Groups: recolored {} moved mesh shapes.".format(colored_count), "lightblue")

    def _disconnect_signal(self, signal, slot=None):
        try:
            if slot is None:
                signal.disconnect()
            else:
                signal.disconnect(slot)
        except (TypeError, RuntimeError):
            pass

    def _stop_worker_for_close(self, attr_name):
        worker = getattr(self, attr_name, None)
        if not worker:
            return True
        try:
            running = worker.isRunning()
        except RuntimeError:
            setattr(self, attr_name, None)
            return True
        if running:
            try:
                worker.stop()
            except AttributeError:
                pass
            worker.wait(5000)
        try:
            if worker.isRunning():
                return False
        except RuntimeError:
            pass
        return True

    def _stop_update_worker_for_close(self):
        worker = self.update_worker
        if not worker:
            return True
        self._disconnect_signal(worker.update_result, self.handle_update_check_result)
        self._disconnect_signal(worker.finished, self.clear_update_worker)
        try:
            running = worker.isRunning()
        except RuntimeError:
            self.update_worker = None
            return True
        if running:
            worker.wait(6000)
        try:
            if worker.isRunning():
                return False
        except RuntimeError:
            pass
        self.update_worker = None
        try:
            worker.deleteLater()
        except RuntimeError:
            pass
        return True

    @contextlib.contextmanager
    def suspend_isolation(self):
        panels = cmds.getPanel(type='modelPanel') or []
        isolation_states = {}
        for panel in panels:
            state = cmds.isolateSelect(panel, query=True, state=True)
            isolation_states[panel] = state
            if state:
                cmds.isolateSelect(panel, state=False)
        try:
            yield
        finally:
            for panel, state in isolation_states.items():
                if state:
                    cmds.isolateSelect(panel, state=True)

    def closeEvent(self, event):
        self._is_closing = True
        if self.update_check_timer:
            self.update_check_timer.stop()
        dialog = self.update_dialog
        install_worker = getattr(dialog, "install_worker", None) if dialog else None
        if install_worker and install_worker.isRunning():
            event.ignore()
            self._is_closing = False
            dialog.raise_()
            dialog.activateWindow()
            return
        if not self._stop_worker_for_close('hp_worker'):
            event.ignore()
            self._is_closing = False
            return
        if not self._stop_worker_for_close('lp_worker'):
            event.ignore()
            self._is_closing = False
            return
        if not self._stop_update_worker_for_close():
            event.ignore()
            self._is_closing = False
            return
        if dialog:
            dialog.close()
        self.restore_subgroup_colors()
        for job_id in self.script_jobs:
            try:
                if cmds.scriptJob(exists=job_id):
                    cmds.scriptJob(kill=job_id, force=True)
            except:
                pass
        self.script_jobs = []
        super(BakeManagerUI, self).closeEvent(event)

    def reload_data_from_scene(self):
        try:
            self.objectName()
        except RuntimeError:
            return
        self.core._node_cache.clear()
        self.root_pairs = bg_core.BakeSessionModel.load()
        self.active_root_id = None
        self.active_subgroup_name = None
        self.is_isolated = False
        self.refresh_right_panel()
        self.refresh_left_panel()

    def refresh_localized_ui(self):
        bg_l10n.localize_widget_tree(self)
        self.refresh_right_panel()
        self.refresh_left_panel()

    def show_language_menu(self):
        menu = QtWidgets.QMenu(self)
        current = bg_l10n.current_language()
        for lang in bg_l10n.available_languages():
            action = menu.addAction(lang.get("label", lang.get("code", "")))
            action.setCheckable(True)
            action.setChecked(lang.get("code") == current)
            action.triggered.connect(lambda checked=False, code=lang.get("code"): self.set_language_ui(code))
        menu.exec_(self.btn_language.mapToGlobal(QtCore.QPoint(0, self.btn_language.height())))

    def set_language_ui(self, code):
        if not code:
            return
        bg_l10n.set_language(code)
        self.refresh_localized_ui()
        if self.active_root_id:
            pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
            if pair:
                hp_main, lp_main, _ = self.core.resolve_main_nodes(pair)
                if hp_main and lp_main:
                    self.sync_toggle_buttons(hp_main, lp_main)
        cmds.inViewMessage(amg=bg_l10n.text("Language switched to {name}").format(name=code), pos='midCenter', fade=True)

    def load_custom_session(self):
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(self, bg_l10n.text("Select session file"), "", bg_l10n.text("JSON Files (*.json)"))
        if not file_path:
            return
        try:
            import json
            with open(file_path, 'r') as f:
                data = json.load(f)
            if isinstance(data, dict) and 'pairs' in data:
                pairs = data['pairs']
            elif isinstance(data, list):
                pairs = data
            else:
                cmds.warning(bg_l10n.text("Invalid session file format: expected a list or a 'pairs' key."))
                return

            if not isinstance(pairs, list):
                cmds.warning(bg_l10n.text("Invalid session file format: 'pairs' must be a list."))
                return

            seen_ids = set()
            for pair in pairs:
                if not isinstance(pair, dict):
                    continue
                if 'id' not in pair or pair['id'] in seen_ids:
                    pair['id'] = str(uuid.uuid4())
                if 'locked' not in pair:
                    pair['locked'] = []
                if 'final_smooth_states' not in pair or not isinstance(pair.get('final_smooth_states'), dict):
                    pair['final_smooth_states'] = {}
                seen_ids.add(pair['id'])

            self.core._node_cache.clear()
            self.root_pairs = [p for p in pairs if isinstance(p, dict)]
            if hasattr(self.core, 'root_pairs'):
                self.core.root_pairs = self.root_pairs
            self.active_root_id = None
            self.active_subgroup_name = None
            self.is_isolated = False
            self.refresh_right_panel()
            self.refresh_left_panel()
            cmds.inViewMessage(amg=bg_l10n.text("Session successfully loaded from: {name}").format(name=os.path.basename(file_path)), pos='midCenter', fade=True)
        except Exception as e:
            cmds.warning(bg_l10n.text("Error loading session: {error}").format(error=e))

    def manual_save_session(self):
        bg_core.BakeSessionModel.save(self.root_pairs)
        cmds.inViewMessage(amg=bg_l10n.text("Session Saved Successfully"), pos='midCenter', fade=True)

    def confirm_action(self, text):
        if self.skip_delete_confirm:
            return True
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(bg_l10n.text("Confirm Action"))
        box.setText(text)
        cb = QtWidgets.QCheckBox(bg_l10n.text("Don't ask again in this session"))
        bg_l10n.localize_widget(cb)
        box.setCheckBox(cb)
        box.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        box.setDefaultButton(QtWidgets.QMessageBox.No)
        box.setStyleSheet("QMessageBox { background-color: #242424; color: white; } QPushButton { background-color: #333; padding: 5px; }")
        res = box.exec_() if hasattr(box, 'exec_') else box.exec_()
        if cb.isChecked():
            self.skip_delete_confirm = True
        return res == QtWidgets.QMessageBox.Yes

    def refresh_left_panel(self):
        import re  # Гарантированная защита от NameError в среде Maya
        
        for i in reversed(range(self.subgroups_layout.count())):
            item = self.subgroups_layout.takeAt(i)
            if item.widget():
                item.widget().deleteLater()

        if getattr(self, 'is_final_view', False):
            self.render_final_view()
            bg_l10n.localize_widget_tree(self.subgroups_widget)
            return

        if not self.active_root_id:
            return
        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not pair:
            return
        hp_main, lp_main, _ = self.core.resolve_main_nodes(pair)
        if not hp_main or not lp_main:
            return

        hp_c = cmds.listRelatives(hp_main, children=True, type='transform', fullPath=True) or []
        lp_c = cmds.listRelatives(lp_main, children=True, type='transform', fullPath=True) or []

        groups = {}
        for child in hp_c:
            if not cmds.objExists(child):
                continue
            if cmds.listRelatives(child, shapes=True):
                continue
            sn = child.split('|')[-1]
            
            # Если включен Keep HP — доверяем вашей структуре и берем группу без проверок суффиксов
            if self.cb_keep_hp_structure.isChecked():
                is_hp = True
            else:
                is_hp = cmds.objExists(child + "." + bg_core.BakeConfig.ATTR_BAKE_GROUP) and cmds.getAttr(child + "." + bg_core.BakeConfig.ATTR_BAKE_GROUP) == "HP"
                if not is_hp and sn.endswith(bg_core.BakeConfig.SUFFIX_HP):
                    is_hp = True
            
            if not is_hp:
                continue
            ui_name = sn
            match = re.search(r'(_HP|_hp|HP|hp)(\d*)$', sn)
            if match:
                ui_name = sn[:match.start()] + match.group(2)
            groups[ui_name] = {'hp': child, 'lp': None}

        for child in lp_c:
            if not cmds.objExists(child):
                continue
            if cmds.listRelatives(child, shapes=True):
                continue
            sn = child.split('|')[-1]
            
            # Ослабляем фильтр для LP, если активен режим сохранения структуры
            if self.cb_keep_hp_structure.isChecked():
                is_lp = True
            else:
                is_lp = cmds.objExists(child + "." + bg_core.BakeConfig.ATTR_BAKE_GROUP) and cmds.getAttr(child + "." + bg_core.BakeConfig.ATTR_BAKE_GROUP) == "LP"
                if not is_lp and sn.endswith(bg_core.BakeConfig.SUFFIX_LP):
                    is_lp = True
            
            if not is_lp:
                continue
            ui_name = sn
            match = re.search(r'(_LP|_lp|LP|lp)(\d*)$', sn)
            if match:
                ui_name = sn[:match.start()] + match.group(2)
            if ui_name in groups:
                groups[ui_name]['lp'] = child
            elif self.cb_keep_hp_structure.isChecked():
                groups[ui_name] = {'hp': None, 'lp': child}

        locked_list = pair.get('locked', [])
        sorted_group_names = sorted(groups.keys())
        if hasattr(self, 'cb_color_subgroups') and self.cb_color_subgroups.isChecked():
            self.ensure_subgroup_color_indices(sorted_group_names)
        for ui_name in sorted_group_names:
            hp_node, lp_node = groups[ui_name]['hp'], groups[ui_name]['lp']
            is_active_subgroup = self.active_subgroup_name == ui_name
            frame = QtWidgets.QFrame()
            frame.setStyleSheet(self.subgroup_row_style(ui_name, is_active_subgroup))
            layout = QtWidgets.QHBoxLayout(frame)
            layout.setContentsMargins(4, 4, 4, 4)

            is_vis = self.is_visible(hp_node) or self.is_visible(lp_node)
            btn_vis = QtWidgets.QPushButton("Vis" if is_vis else "Hid")
            btn_vis.setFixedSize(30, 24)
            btn_vis.setStyleSheet("background-color: #4a5d4a;" if is_vis else "background-color: #8c4242;")
            btn_vis.clicked.connect(lambda checked=False, h=hp_node, l=lp_node, b=btn_vis: self.toggle_subgroup_vis(h, l, b))
            layout.addWidget(btn_vis)

            btn_name = SubgroupButton(ui_name)
            btn_name.setStyleSheet(self.subgroup_name_style(ui_name, is_active_subgroup))
            btn_name.clicked.connect(lambda checked=False, n=ui_name: self.set_active_subgroup(n))
            btn_name.doubleClicked.connect(lambda checked=False, h=hp_node, l=lp_node: self.select_meshes_in_group(h, l))
            btn_name.rightClicked.connect(lambda checked=False, old_name=ui_name, h=hp_node, l=lp_node: self.rename_subgroup_ui(old_name, h, l))
            layout.addWidget(btn_name, stretch=1)

            btn_plus = QtWidgets.QPushButton("Add")
            btn_plus.setFixedSize(56, 24)
            btn_plus.setStyleSheet(self.subgroup_add_button_style(is_active_subgroup))
            btn_plus.clicked.connect(lambda checked=False, h=hp_node, l=lp_node, pm_hp=hp_main, pm_lp=lp_main: self.add_to_groups_ui(h, l, pm_hp, pm_lp))
            layout.addWidget(btn_plus)

            btn_lock = QtWidgets.QPushButton()
            btn_lock.setFixedSize(24, 24)
            is_locked = ui_name in locked_list
            icon_name = "Look_Icon_Button.png" if is_locked else "Unlook_Icon_Button.png"
            icon_path = os.path.join(os.path.dirname(__file__), icon_name)
            if os.path.exists(icon_path):
                btn_lock.setIcon(QtGui.QIcon(icon_path))
                btn_lock.setIconSize(QtCore.QSize(18, 18))
            btn_lock.setStyleSheet("background-color: #8c4242;" if is_locked else "background-color: #428c42;")
            lock_tip_key = "Unlock subgroup" if is_locked else "Lock subgroup"
            btn_lock.setToolTip(bg_l10n.tooltip(lock_tip_key))
            btn_lock.setStatusTip(bg_l10n.tooltip(lock_tip_key))
            btn_lock.clicked.connect(lambda checked=False, n=ui_name: self.toggle_lock(n))
            layout.addWidget(btn_lock)

            btn_del = QtWidgets.QPushButton("X")
            btn_del.setFixedSize(24, 24)
            btn_del.setStyleSheet("background-color: #8c4242;")
            btn_del.clicked.connect(lambda checked=False, h=hp_node, l=lp_node, r_h=hp_main, r_l=lp_main: self.safe_delete_subgroup_ui(h, l, r_h, r_l))
            layout.addWidget(btn_del)

            self.subgroups_layout.addWidget(frame)
        bg_l10n.localize_widget_tree(self.subgroups_widget)

    def activate_root(self, pair):
        hp_node, lp_node, _ = self.core.resolve_main_nodes(pair)
        if not hp_node or not lp_node:
            self.active_root_id = pair['id']
            return cmds.warning("Original groups were deleted.")

        if self.active_root_id == pair['id']:
            self.is_isolated = not self.is_isolated
        else:
            self.active_root_id = pair['id']
            self.active_subgroup_name = None
            self.is_isolated = True
            self.subgroup_color_index_map = {}

        panel = 'modelPanel4'
        if not cmds.modelEditor(panel, exists=True):
            panel = cmds.playblast(activeEditor=True)
        if cmds.modelEditor(panel, exists=True):
            cmds.isolateSelect(panel, state=self.is_isolated)
            if self.is_isolated:
                iso_set = cmds.isolateSelect(panel, q=True, viewObjects=True)
                cmds.isolateSelect(panel, addDagObject=hp_node)
                cmds.isolateSelect(panel, addDagObject=lp_node)
                if iso_set:
                    set_name = iso_set[0] if isinstance(iso_set, list) else iso_set
                    if cmds.objExists(set_name):
                        cmds.sets(clear=set_name)
                nodes_to_add = [n for n in [hp_node, lp_node] if n and cmds.objExists(n)]
                for node in nodes_to_add:
                    cmds.isolateSelect(panel, addDagObject=node)
            cmds.isolateSelect(panel, update=True)
        else:
            cmds.warning("Active modelPanel not found for isolation.")

        self.sync_toggle_buttons(hp_node, lp_node)
        self.refresh_right_panel()
        self.refresh_left_panel()
        skip_color_update = bool(getattr(self, '_skip_color_update_once', False))
        self._skip_color_update_once = False
        color_groups_enabled = hasattr(self, 'cb_color_subgroups') and self.cb_color_subgroups.isChecked()
        if skip_color_update and color_groups_enabled:
            self.restore_subgroup_colors()
        elif color_groups_enabled:
            self.update_subgroup_colors()

    def select_meshes_in_group(self, hp_grp, lp_grp):
        to_select = []
        for grp in [hp_grp, lp_grp]:
            if grp and cmds.objExists(grp):
                meshes = cmds.listRelatives(grp, allDescendents=True, type='mesh', fullPath=True) or []
                to_select.extend(list(set([cmds.listRelatives(m, parent=True, fullPath=True)[0] for m in meshes])))
        if to_select:
            cmds.select(to_select, replace=True)
        else:
            cmds.select(clear=True)

    def sync_toggle_buttons(self, hp_node, lp_node):
        self.btn_toggle_hp.blockSignals(True)
        self.btn_toggle_lp.blockSignals(True)
        self.btn_toggle_groups.blockSignals(True)

        hp_vis = cmds.getAttr("{}.visibility".format(hp_node)) if hp_node and cmds.objExists(hp_node) else False
        lp_vis = cmds.getAttr("{}.visibility".format(lp_node)) if lp_node and cmds.objExists(lp_node) else False

        self.btn_toggle_hp.setChecked(hp_vis)
        self.btn_toggle_hp.setText(bg_l10n.text("HP Visible" if hp_vis else "HP Hidden"))
        self.btn_toggle_hp.setStyleSheet("background-color: #4a5d4a;" if hp_vis else "background-color: #8c4242;")

        self.btn_toggle_lp.setChecked(lp_vis)
        self.btn_toggle_lp.setText(bg_l10n.text("LP Visible" if lp_vis else "LP Hidden"))
        self.btn_toggle_lp.setStyleSheet("background-color: #4a5d4a;" if lp_vis else "background-color: #8c4242;")

        grp_vis = False
        has_children = False
        for p_node in [hp_node, lp_node]:
            if p_node and cmds.objExists(p_node):
                children = cmds.listRelatives(p_node, children=True, type='transform', fullPath=True) or []
                if children:
                    has_children = True
                    for c in children:
                        if cmds.getAttr("{}.visibility".format(c)):
                            grp_vis = True
                            break
            if grp_vis:
                break
        if not has_children and (hp_vis or lp_vis):
            grp_vis = True

        self.btn_toggle_groups.setChecked(grp_vis)
        self.btn_toggle_groups.setText(bg_l10n.text("Groups Vis" if grp_vis else "Groups Hidden"))
        self.btn_toggle_groups.setStyleSheet("background-color: #4a5d4a;" if grp_vis else "background-color: #8c4242;")

        self.btn_toggle_hp.blockSignals(False)
        self.btn_toggle_lp.blockSignals(False)
        self.btn_toggle_groups.blockSignals(False)

    def toggle_root_vis(self, type_str, state):
        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not pair:
            return

        if type_str == "LP" and getattr(self, 'is_final_view', False):
            self.set_final_low_visibility(pair.get('base', ''), state)
            return

        hp_node, lp_node, _ = self.core.resolve_main_nodes(pair)
        parent_node = hp_node if type_str == "HP" else lp_node
        btn = self.btn_toggle_hp if type_str == "HP" else self.btn_toggle_lp
        btn.setText(bg_l10n.text("{} Visible".format(type_str) if state else "{} Hidden".format(type_str)))
        btn.setStyleSheet("background-color: #4a5d4a;" if state else "background-color: #8c4242;")
        if parent_node and cmds.objExists(parent_node):
            cmds.setAttr("{}.visibility".format(parent_node), state)

    def set_all_subgroups_vis(self, state):
        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not pair:
            return
        hp_main, lp_main, _ = self.core.resolve_main_nodes(pair)
        for p_node in [hp_main, lp_main]:
            if p_node and cmds.objExists(p_node):
                for child in (cmds.listRelatives(p_node, children=True, type='transform', fullPath=True) or []):
                    cmds.setAttr("{}.visibility".format(child), state)
        self.btn_toggle_groups.setText(bg_l10n.text("Groups Vis" if state else "Groups Hidden"))
        self.btn_toggle_groups.setStyleSheet("background-color: #4a5d4a;" if state else "background-color: #8c4242;")
        if getattr(self, 'is_final_view', False):
            for widget_data in getattr(self, 'final_mesh_widgets', []):
                for hp in widget_data.get('hp_nodes', []):
                    if cmds.objExists(hp):
                        cmds.setAttr(hp + ".visibility", state)
        self.refresh_left_panel()

    def is_visible(self, node):
        return cmds.getAttr("{}.visibility".format(node)) if node and cmds.objExists(node) else False

    def toggle_subgroup_vis(self, hp, lp, btn_widget):
        new_state = not (self.is_visible(hp) or self.is_visible(lp))
        if hp and cmds.objExists(hp):
            cmds.setAttr("{}.visibility".format(hp), new_state)
        if lp and cmds.objExists(lp):
            cmds.setAttr("{}.visibility".format(lp), new_state)
        btn_widget.setText(bg_l10n.text("Vis" if new_state else "Hid"))
        btn_widget.setStyleSheet("background-color: #4a5d4a;" if new_state else "background-color: #8c4242;")
        if getattr(self, 'is_final_view', False):
            pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
            if pair:
                hp_final = "Bake_Groups|{}|HP".format(pair['base'])
                lp_final = "Bake_Groups|{}|LP".format(pair['base'])
                if cmds.objExists(hp_final):
                    cmds.setAttr(hp_final + ".visibility", new_state)
                if cmds.objExists(lp_final):
                    cmds.setAttr(lp_final + ".visibility", new_state)

    def show_subgroups_context_menu(self, pos):
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(bg_core.BakeConfig.STYLE_CONTEXT_MENU)
        action_optimize = menu.addAction("Optimize subgroups (delete empty)")
        action_optimize.triggered.connect(self.optimize_subgroups)
        action_select_by_mesh = menu.addAction("Group search by mesh (Ctrl+Shift+Z)")
        action_select_by_mesh.triggered.connect(self.select_subgroup_by_selected_mesh)
        menu.addSeparator()
        action_select_low = menu.addAction("Select all _low meshes (Active Chapter)")
        action_select_low.triggered.connect(self.select_all_combined_low_meshes)
        action_select_book_low = menu.addAction("Select all _low meshes (Entire Book)")
        action_select_book_low.triggered.connect(self.select_all_book_low_meshes)
        bg_l10n.localize_menu(menu)
        menu.exec_(self.subgroups_widget.mapToGlobal(pos))

    def select_subgroup_by_selected_mesh(self, checked=False):
        import re
        
        # Получаем полный (long) путь к выделенным объектам
        sel = cmds.ls(sl=True, long=True)
        if not sel:
            self.log("Nothing is highlighted in the scene.", "yellow")
            return

        if not self.active_root_id:
            self.log("There is no active bake group. First, select a group in the TOC.", "yellow")
            return

        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not pair:
            return

        hp_main, lp_main, _ = self.core.resolve_main_nodes(pair)
        
        # Собираем все дочерние группы (трансформы подгрупп) текущего Root
        subgroup_nodes = []
        for main_node in [hp_main, lp_main]:
            if main_node and cmds.objExists(main_node):
                children = cmds.listRelatives(main_node, children=True, type='transform', fullPath=True) or []
                subgroup_nodes.extend(children)

        found_ui_name = None

        # Ищем, какому дочернему узлу принадлежит выделенный объект
        for obj in sel:
            for sub_node in subgroup_nodes:
                # Работает как для самого трансформа, так и для вложенных нод/компонентов (фейсов, вертексов)
                if obj == sub_node or obj.startswith(sub_node + "|"):
                    sn = sub_node.split('|')[-1]
                    ui_name = sn
                    
                    # Универсальная регулярка для отсечения суффиксов и HP, и LP
                    match = re.search(r'(_HP|_hp|HP|hp|_LP|_lp|LP|lp)(\d*)$', sn)
                    if match:
                        ui_name = sn[:match.start()] + match.group(2)
                    
                    found_ui_name = ui_name
                    break
            if found_ui_name:
                break

        if found_ui_name:
            self.set_active_subgroup(found_ui_name)
            if hasattr(self, 'refresh_left_panel'):
                self.refresh_left_panel()
                
            self.log("A subgroup has been found and is active: {}".format(found_ui_name), "green")
        else:
            self.log("The selected object does not belong to any subgroup of the active Root.", "yellow")

    def select_all_combined_low_meshes(self):
        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not pair:
            cmds.warning("No active group for selection.")
            return
        base_name = pair.get('base', '')
        chapter_grp_path = "LP_Combine_BG|{}".format(base_name)
        _, lp_main, _ = self.core.resolve_main_nodes(pair)
        search_root = chapter_grp_path if cmds.objExists(chapter_grp_path) else lp_main
        if not search_root or not cmds.objExists(search_root):
            cmds.warning("LP root not found.")
            return
        to_select = []
        lp_children = cmds.listRelatives(search_root, children=True, fullPath=True) or []
        for child in lp_children:
            short_name = child.split('|')[-1]
            is_subgroup = not cmds.listRelatives(child, shapes=True)
            if "_low" in short_name and not is_subgroup:
                to_select.append(child)
        if to_select:
            cmds.select(to_select, replace=True)
            cmds.inViewMessage(amg="Selected {} final _low meshes".format(len(to_select)), pos='midCenter', fade=True)
        else:
            cmds.warning("Combined _low meshes not found. Run 'Combine Fin' first.")
            cmds.select(clear=True)

    def select_all_book_low_meshes(self):
        if not self.active_root_id:
            cmds.warning("No active chapter to determine book.")
            return
        active_pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not active_pair:
            return
        active_book = active_pair.get('book')
        if not active_book:
            cmds.warning("Active chapter is not linked to any Book.")
            return
        book_pairs = [p for p in self.root_pairs if p.get('book') == active_book]
        if not book_pairs:
            return
        to_select = []
        for pair in book_pairs:
            base_name = pair.get('base', '')
            chapter_grp_path = "LP_Combine_BG|{}".format(base_name)
            _, lp_main, _ = self.core.resolve_main_nodes(pair)
            search_root = chapter_grp_path if cmds.objExists(chapter_grp_path) else lp_main
            if not search_root or not cmds.objExists(search_root):
                continue
            lp_children = cmds.listRelatives(search_root, children=True, fullPath=True) or []
            for child in lp_children:
                short_name = child.split('|')[-1]
                is_subgroup = not cmds.listRelatives(child, shapes=True)
                if "_low" in short_name and not is_subgroup:
                    to_select.append(child)
        if to_select:
            cmds.select(to_select, replace=True)
            cmds.inViewMessage(amg="Selected {} _low meshes in book '{}'".format(len(to_select), active_book), pos='midCenter', fade=True)
        else:
            cmds.warning("Combined _low meshes in book '{}' not found.".format(active_book))
            cmds.select(clear=True)


def main():
    if cmds.workspaceControl(bg_core.BakeConfig.WORKSPACE_NAME, exists=True):
        cmds.deleteUI(bg_core.BakeConfig.WORKSPACE_NAME, control=True)

    global bake_manager_ui
    bake_manager_ui = BakeManagerUI()

    bake_manager_ui.show(
        dockable=True, floating=False, area='right', allowedArea='right', retain=False
    )

    dock_targets = ['AttributeEditor', 'ToolSettings', 'ChannelBoxLayerEditor']
    for target in dock_targets:
        try:
            cmds.workspaceControl(bg_core.BakeConfig.WORKSPACE_NAME, e=True, tabToControl=[target, -1])
            break
        except RuntimeError:
            continue

    try:
        cmds.workspaceControl(bg_core.BakeConfig.WORKSPACE_NAME, e=True, restore=True)
    except:
        pass


if __name__ == "__main__":
    main()
