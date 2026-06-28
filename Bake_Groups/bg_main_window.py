# -*- coding: utf-8 -*-
from __future__ import print_function, division, absolute_import

import sys
import os
import json
import uuid
import contextlib
import re
import zipfile
import copy
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

try:
    import maya.api.OpenMaya as om
except Exception:
    om = None


class TOCNameDelegate(QtWidgets.QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        editor = super(TOCNameDelegate, self).createEditor(parent, option, index)
        if isinstance(editor, QtWidgets.QLineEdit):
            editor.setMinimumWidth(0)
            editor.setStyleSheet("QLineEdit { padding: 2px 4px; }")
        return editor

    def updateEditorGeometry(self, editor, option, index):
        tree = self.parent()
        if tree and index.column() == 0:
            rect = QtCore.QRect(option.rect)
            right = tree.viewport().width() - tree.columnWidth(1) - 4
            rect.setRight(max(rect.left() + 160, right))
            rect.adjust(0, 1, 0, -1)
            editor.setGeometry(rect)
            return
        super(TOCNameDelegate, self).updateEditorGeometry(editor, option, index)


class BakeManagerUI(MayaQWidgetDockableMixin, QtWidgets.QMainWindow,
                    HPAnalysisMixin, LPMatchingMixin, FinalViewMixin,
                    ExportMixin, GroupManagementMixin, SceneInteractionMixin, TOCMixin):
    def __init__(self, parent=None):
        super(BakeManagerUI, self).__init__(parent=parent)
        self.setWindowTitle("Bake Group Manager Pro")
        self.setObjectName("BakeManagerUI")
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        self.setMinimumSize(320, 400)
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
        self.active_material_visibility_filter = None
        self._is_closing = False
        self.update_worker = None
        self.update_dialog = None
        self.update_check_timer = None
        self.manual_update_check_requested = False
        self._dock_relayout_pending = False
        self._resize_relayout_pending = False
        self._shutdown_for_reload_done = False
        self.bg_undo_stack = []
        self._bg_undo_restoring = False
        self._bg_undo_running = False
        self._bg_undo_event_filter_app = None
        self._combined_check_skipped_chapters = set()

        self.init_ui()
        self.install_bg_undo_event_filter()
        self.apply_stylesheet()
        self.relax_dock_width_constraints()
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
        layout.setContentsMargins(6, 6, 6, 6)
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.splitter.setChildrenCollapsible(True)
        layout.addWidget(self.splitter)

        # Left panel
        self.left_panel = QtWidgets.QWidget()
        self.left_panel.setMinimumWidth(120)
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

        create_layout = QtWidgets.QHBoxLayout()
        btn_create_main = QtWidgets.QPushButton(" Create Pair from Picked")
        btn_create_main.setIcon(get_icon("add_group.png"))
        btn_create_main.setStyleSheet("background-color: #3f523f; font-weight: bold; padding: 8px;")
        btn_create_main.clicked.connect(lambda checked=False: self.run_undoable_bg_action("Create Pair", self.create_root_pair_from_picked))
        create_layout.addWidget(btn_create_main)

        self.btn_create_by_material = QtWidgets.QPushButton("Create by Mat")
        self.btn_create_by_material.setStyleSheet("background-color: #4b5140; font-weight: bold; padding: 8px;")
        self.btn_create_by_material.clicked.connect(lambda checked=False: self.run_undoable_bg_action("Create by Mat", self.create_root_pairs_by_material_from_picked))
        create_layout.addWidget(self.btn_create_by_material)
        g_layout.addLayout(create_layout)

        tool_layout = QtWidgets.QHBoxLayout()
        self.cb_color_subgroups = QtWidgets.QCheckBox("Color Groups")
        self.cb_color_subgroups.setChecked(False)
        self.cb_color_subgroups.toggled.connect(self.on_color_by_subgroups_toggled)
        self.cb_keep_hp_structure = QtWidgets.QCheckBox("Keep HP")
        self.cb_keep_hp_structure.setChecked(False)
        self.cb_keep_hp_structure.toggled.connect(lambda checked: self.refresh_left_panel())

        self.btn_combine_mesh = QtWidgets.QPushButton("Combine")
        self.btn_combine_mesh.setStyleSheet("background-color: #3b5998;")
        self.btn_combine_mesh.clicked.connect(lambda checked=False: self.run_undoable_bg_action("Combine", self.tool_combine))
        self.btn_separate_mesh = QtWidgets.QPushButton("Separate")
        self.btn_separate_mesh.setStyleSheet("background-color: #8c6239;")
        self.btn_separate_mesh.clicked.connect(lambda checked=False: self.run_undoable_bg_action("Separate", self.tool_separate))
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
        self.btn_toggle_hp.toggled.connect(lambda state: self.run_undoable_bg_action("HP Visibility", self.toggle_root_vis, "HP", state))
        self.btn_toggle_lp = QtWidgets.QPushButton("LP Visible")
        self.btn_toggle_lp.setCheckable(True)
        self.btn_toggle_lp.toggled.connect(lambda state: self.run_undoable_bg_action("LP Visibility", self.toggle_root_vis, "LP", state))
        self.btn_toggle_groups = QtWidgets.QPushButton("Groups Vis")
        self.btn_toggle_groups.setCheckable(True)
        self.btn_toggle_groups.toggled.connect(lambda state: self.run_undoable_bg_action("Groups Visibility", self.set_all_subgroups_vis, state))
        self.btn_toggle_groups.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.btn_toggle_groups.customContextMenuRequested.connect(self.show_groups_visibility_context_menu)
        v_layout.addWidget(self.btn_toggle_hp)
        v_layout.addWidget(self.btn_toggle_lp)
        v_layout.addWidget(self.btn_toggle_groups)
        left_layout.addLayout(v_layout)

        # ---- Groups list (scroll area) ----
        self.subgroups_scroll = QtWidgets.QScrollArea()
        self.subgroups_scroll.setWidgetResizable(True)
        self.subgroups_scroll.setMinimumWidth(0)
        try:
            self.subgroups_scroll.setSizeAdjustPolicy(QtWidgets.QAbstractScrollArea.AdjustIgnored)
        except Exception:
            pass
        self.subgroups_widget = QtWidgets.QWidget()
        self.subgroups_widget.setMinimumWidth(0)
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
        self.btn_add.clicked.connect(lambda checked=False: self.run_undoable_bg_action("Add to Selected Group", self.add_to_selected_subgroup_ui))

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
        self.btn_toggle_view.clicked.connect(lambda checked=False: self.run_undoable_bg_action("Final Group", self.toggle_final_view))

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
        self.right_panel.setMinimumWidth(120)
        right_layout = QtWidgets.QVBoxLayout(self.right_panel)
        right_layout.setContentsMargins(5, 0, 0, 0)

        self.right_splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.right_splitter.setChildrenCollapsible(True)
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
        header.resizeSection(1, 22)
        self.toc_tree.setItemDelegate(TOCNameDelegate(self.toc_tree))
        self.toc_tree.setIndentation(10)
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
        self.shortcut_group.activated.connect(lambda: self.run_undoable_bg_action("Group into Book", self.group_selected_into_book))


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
        self.splitter.setSizes([300, 180])
        bg_l10n.localize_widget_tree(self)
        self.chk_ignore_floaters.setChecked(True)
        self.chk_material_slots.setChecked(False)

    def relax_dock_width_constraints(self):
        policy_ignored = QtWidgets.QSizePolicy.Ignored
        policy_preferred = QtWidgets.QSizePolicy.Preferred
        targets = [
            (self, 320, policy_preferred),
            (getattr(self, 'left_panel', None), 120, policy_preferred),
            (getattr(self, 'right_panel', None), 120, policy_preferred),
            (getattr(self, 'splitter', None), 0, policy_ignored),
            (getattr(self, 'right_splitter', None), 0, policy_ignored),
            (getattr(self, 'subgroups_scroll', None), 0, policy_ignored),
            (getattr(self, 'subgroups_widget', None), 0, policy_ignored),
            (getattr(self, 'toc_tree', None), 0, policy_ignored),
            (getattr(self, 'gt_widget', None), 0, policy_ignored),
            (getattr(self, 'log_output', None), 0, policy_ignored),
        ]
        for widget, min_width, horizontal_policy in targets:
            if not widget:
                continue
            try:
                widget.setMinimumWidth(min_width)
                sp = widget.sizePolicy()
                sp.setHorizontalPolicy(horizontal_policy)
                widget.setSizePolicy(sp)
                widget.updateGeometry()
            except RuntimeError:
                continue
            except Exception:
                continue
        for widget in self.findChildren(QtWidgets.QWidget):
            try:
                if widget.isWindow():
                    continue
                if isinstance(widget, QtWidgets.QLabel):
                    widget.setWordWrap(False)
                    widget.setMinimumWidth(0)
                    sp = widget.sizePolicy()
                    sp.setHorizontalPolicy(policy_ignored)
                    widget.setSizePolicy(sp)
                elif isinstance(widget, QtWidgets.QComboBox):
                    min_size = widget.minimumSize()
                    max_size = widget.maximumSize()
                    fixed_combo = (
                        min_size.width() > 0 and
                        min_size.width() == max_size.width() and
                        max_size.width() < 16777215
                    )
                    if not fixed_combo:
                        widget.setMinimumWidth(72)
                        sp = widget.sizePolicy()
                        sp.setHorizontalPolicy(policy_preferred)
                        widget.setSizePolicy(sp)
                elif isinstance(widget, (QtWidgets.QLineEdit, QtWidgets.QTextEdit,
                                         QtWidgets.QAbstractItemView, QtWidgets.QScrollArea)):
                    widget.setMinimumWidth(0)
                    sp = widget.sizePolicy()
                    sp.setHorizontalPolicy(policy_ignored)
                    widget.setSizePolicy(sp)
                elif isinstance(widget, QtWidgets.QPushButton):
                    min_size = widget.minimumSize()
                    max_size = widget.maximumSize()
                    fixed_small = (
                        min_size.width() > 0 and
                        min_size.width() == max_size.width() and
                        max_size.width() <= 80
                    )
                    if not fixed_small:
                        widget.setMinimumWidth(0)
            except RuntimeError:
                continue
            except Exception:
                continue
        if hasattr(self, 'splitter'):
            try:
                self.splitter.setChildrenCollapsible(True)
                self.splitter.updateGeometry()
            except Exception:
                pass
        if hasattr(self, 'right_splitter'):
            try:
                self.right_splitter.setChildrenCollapsible(True)
                self.right_splitter.updateGeometry()
            except Exception:
                pass
        central = self.centralWidget()
        for widget in (central, getattr(self, 'left_panel', None), getattr(self, 'right_panel', None)):
            if not widget:
                continue
            try:
                layout = widget.layout()
                if layout:
                    layout.invalidate()
                    layout.activate()
                widget.updateGeometry()
            except Exception:
                pass

    def schedule_dock_relayout(self):
        if getattr(self, '_dock_relayout_pending', False):
            return
        self._dock_relayout_pending = True
        QtCore.QTimer.singleShot(0, self.run_dock_relayout)

    def run_dock_relayout(self):
        self._dock_relayout_pending = False
        if getattr(self, '_is_closing', False):
            return
        try:
            self.objectName()
        except RuntimeError:
            return
        self.relax_dock_width_constraints()
        try:
            if self.layout():
                self.layout().activate()
            self.updateGeometry()
        except Exception:
            pass

    def resizeEvent(self, event):
        super(BakeManagerUI, self).resizeEvent(event)
        if getattr(self, '_is_closing', False):
            return
        if getattr(self, '_resize_relayout_pending', False):
            return
        self._resize_relayout_pending = True
        QtCore.QTimer.singleShot(60, self.run_resize_relayout)

    def run_resize_relayout(self):
        self._resize_relayout_pending = False
        self.run_dock_relayout()
        try:
            self.repaint()
        except Exception:
            pass

    def rebuild_algorithm_settings_ui(self, layout):
        self.algo_group = CollapsibleSection("Algorithm")
        self.input_suffix = QtWidgets.QLineEdit()
        self.input_suffix.setPlaceholderText("Group name")
        self.input_suffix.setMinimumWidth(70)
        btn_c_pair = QtWidgets.QPushButton("Create Group")
        btn_c_pair.setIcon(get_icon("add_group.png"))
        btn_c_pair.clicked.connect(lambda checked=False: self.run_undoable_bg_action("Create Group", self.create_subgroup_pair))
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
        self.spin_threshold.setFixedWidth(50)
        self.spin_collision_pct = self.spin_threshold
        grid.addWidget(self.spin_threshold, 0, 1)

        self.hp_group_limit = 12
        self.chk_ignore_floaters = QtWidgets.QCheckBox("Ignore Floaters")
        self.chk_ignore_floaters.setChecked(True)
        grid.addWidget(self.chk_ignore_floaters, 0, 2)

        self.chk_material_slots = QtWidgets.QCheckBox("N_Mat")
        self.chk_material_slots.setChecked(False)
        grid.addWidget(self.chk_material_slots, 0, 3)

        self.lbl_hp_link_vtx = QtWidgets.QLabel("HP Link Vtx:")
        grid.addWidget(self.lbl_hp_link_vtx, 1, 0)
        self.spin_compound_link_verts = QtWidgets.QSpinBox()
        self.spin_compound_link_verts.setRange(1, 500)
        self.spin_compound_link_verts.setValue(8)
        self.spin_compound_link_verts.setObjectName("HP Link Vtx:")
        self.spin_compound_link_verts.setFixedWidth(50)
        grid.addWidget(self.spin_compound_link_verts, 1, 1)

        self.lbl_hp_link_dist = QtWidgets.QLabel("HP Link Dist (%):")
        grid.addWidget(self.lbl_hp_link_dist, 1, 2)
        self.spin_compound_link_dist = QtWidgets.QDoubleSpinBox()
        self.spin_compound_link_dist.setRange(0.01, 25.0)
        self.spin_compound_link_dist.setDecimals(2)
        self.spin_compound_link_dist.setValue(0.1)
        self.spin_compound_link_dist.setSingleStep(0.05)
        self.spin_compound_link_dist.setObjectName("HP Link Dist (%):")
        self.spin_compound_link_dist.setFixedWidth(58)
        grid.addWidget(self.spin_compound_link_dist, 1, 3)

        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(1, 0)
        grid.setColumnStretch(2, 2)
        grid.setColumnStretch(3, 0)

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

    def _bg_undo_candidate_nodes(self):
        nodes = set()
        for pair in self.root_pairs or []:
            try:
                hp_node, lp_node, _ = self.core.resolve_main_nodes(pair)
            except Exception:
                hp_node, lp_node = None, None
            for root in (hp_node, lp_node):
                if root and cmds.objExists(root):
                    long_root = (cmds.ls(root, long=True) or [root])[0]
                    nodes.add(long_root)
                    for child in (cmds.listRelatives(long_root, allDescendents=True, type='transform', fullPath=True) or []):
                        if cmds.objExists(child):
                            nodes.add(child)
        for root in ("LP_Combine_BG", "Bake_Groups"):
            if cmds.objExists(root):
                long_root = (cmds.ls(root, long=True) or [root])[0]
                nodes.add(long_root)
                for child in (cmds.listRelatives(long_root, allDescendents=True, type='transform', fullPath=True) or []):
                    if cmds.objExists(child):
                        nodes.add(child)
        return sorted(nodes)

    def capture_bg_undo_snapshot(self):
        visibility = {}
        for node in self._bg_undo_candidate_nodes():
            if not cmds.objExists(node):
                continue
            try:
                visibility[node] = bool(cmds.getAttr(node + ".visibility"))
            except Exception:
                pass
        return {
            "root_pairs": copy.deepcopy(self.root_pairs),
            "active_root_id": self.active_root_id,
            "active_subgroup_name": self.active_subgroup_name,
            "is_isolated": bool(getattr(self, 'is_isolated', False)),
            "is_final_view": bool(getattr(self, 'is_final_view', False)),
            "is_preview_active": bool(getattr(self, 'is_preview_active', False)),
            "is_final_low_visible": bool(getattr(self, 'is_final_low_visible', False)),
            "active_material_visibility_filter": getattr(self, 'active_material_visibility_filter', None),
            "final_smooth_states": copy.deepcopy(getattr(self, 'final_smooth_states', {}) or {}),
            "saved_subgroup_vis": copy.deepcopy(getattr(self, 'saved_subgroup_vis', {}) or {}),
            "visibility": visibility,
            "selection": cmds.ls(selection=True, long=True) or []
        }

    def restore_bg_undo_snapshot(self, snapshot):
        self.root_pairs = copy.deepcopy(snapshot.get("root_pairs", []))
        if hasattr(self.core, 'root_pairs'):
            self.core.root_pairs = self.root_pairs
        if hasattr(self.core, '_node_cache'):
            self.core._node_cache.clear()
        self.active_root_id = snapshot.get("active_root_id")
        self.active_subgroup_name = snapshot.get("active_subgroup_name")
        self.is_isolated = bool(snapshot.get("is_isolated", False))
        self.is_final_view = bool(snapshot.get("is_final_view", False))
        self.is_preview_active = bool(snapshot.get("is_preview_active", False))
        self.is_final_low_visible = bool(snapshot.get("is_final_low_visible", False))
        self.active_material_visibility_filter = snapshot.get("active_material_visibility_filter")
        self.final_smooth_states = copy.deepcopy(snapshot.get("final_smooth_states", {}) or {})
        self.saved_subgroup_vis = copy.deepcopy(snapshot.get("saved_subgroup_vis", {}) or {})
        for node, state in (snapshot.get("visibility", {}) or {}).items():
            if cmds.objExists(node):
                try:
                    cmds.setAttr(node + ".visibility", bool(state))
                except Exception:
                    pass
        bg_core.BakeSessionModel.save(self.root_pairs)
        self.refresh_right_panel()
        self.refresh_left_panel()
        pair = next((p for p in self.root_pairs if p.get('id') == self.active_root_id), None)
        if pair:
            hp_node, lp_node, _ = self.core.resolve_main_nodes(pair)
            if hp_node and lp_node:
                self.sync_toggle_buttons(hp_node, lp_node)
        selection = [node for node in (snapshot.get("selection", []) or []) if cmds.objExists(node)]
        if selection:
            cmds.select(selection, replace=True)
        else:
            cmds.select(clear=True)

    def install_bg_undo_event_filter(self):
        app = QtWidgets.QApplication.instance()
        if not app:
            return
        try:
            app.installEventFilter(self)
            self._bg_undo_event_filter_app = app
        except RuntimeError:
            self._bg_undo_event_filter_app = None

    def remove_bg_undo_event_filter(self):
        app = getattr(self, '_bg_undo_event_filter_app', None)
        if not app:
            return
        try:
            app.removeEventFilter(self)
        except RuntimeError:
            pass
        self._bg_undo_event_filter_app = None

    def eventFilter(self, watched, event):
        try:
            event_type = event.type()
        except Exception:
            return super(BakeManagerUI, self).eventFilter(watched, event)
        shortcut_types = (QtCore.QEvent.KeyPress, QtCore.QEvent.ShortcutOverride)
        if event_type in shortcut_types and self.should_handle_bg_undo_shortcut(event):
            if event_type == QtCore.QEvent.ShortcutOverride:
                event.accept()
                return True
            self.undo_last_bg_action()
            event.accept()
            return True
        return super(BakeManagerUI, self).eventFilter(watched, event)

    def should_handle_bg_undo_shortcut(self, event):
        if not getattr(self, 'bg_undo_stack', []):
            return False
        if getattr(self, '_bg_undo_restoring', False) or getattr(self, '_bg_undo_running', False):
            return False
        try:
            if event.key() != QtCore.Qt.Key_Z:
                return False
            modifiers = event.modifiers()
            if not (modifiers & QtCore.Qt.ControlModifier):
                return False
            if modifiers & (QtCore.Qt.ShiftModifier | QtCore.Qt.AltModifier | QtCore.Qt.MetaModifier):
                return False
        except Exception:
            return False
        focus = QtWidgets.QApplication.focusWidget()
        if not self.is_bg_undo_focus_inside_window(focus):
            return False
        return not self.is_bg_undo_editable_focus(focus)

    def is_bg_undo_focus_inside_window(self, focus):
        if not focus:
            return False
        try:
            return focus is self or self.isAncestorOf(focus)
        except RuntimeError:
            return False

    def is_bg_undo_editable_focus(self, focus):
        widget = focus
        while widget and widget is not self:
            if isinstance(widget, QtWidgets.QLineEdit) and not widget.isReadOnly():
                return True
            if isinstance(widget, (QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)) and not widget.isReadOnly():
                return True
            if isinstance(widget, QtWidgets.QAbstractSpinBox):
                return True
            if isinstance(widget, QtWidgets.QComboBox) and widget.isEditable():
                return True
            try:
                widget = widget.parentWidget()
            except RuntimeError:
                return False
        return False

    def update_bg_undo_button(self):
        pass

    def run_undoable_bg_action(self, action_name, callback, *args, **kwargs):
        if getattr(self, '_bg_undo_restoring', False) or getattr(self, '_bg_undo_running', False):
            return callback(*args, **kwargs)
        snapshot = self.capture_bg_undo_snapshot()
        self._bg_undo_running = True
        try:
            chunk_name = "BG_{}".format(re.sub(r'[^A-Za-z0-9_]+', '_', str(action_name))[:48])
            with bg_core.undo_chunk(chunk_name):
                result = callback(*args, **kwargs)
        finally:
            self._bg_undo_running = False
        self.bg_undo_stack.append({"action": str(action_name), "snapshot": snapshot})
        if len(self.bg_undo_stack) > 20:
            self.bg_undo_stack = self.bg_undo_stack[-20:]
        self.update_bg_undo_button()
        return result

    def undo_last_bg_action(self):
        if not getattr(self, 'bg_undo_stack', []):
            self.log(bg_l10n.text("No Bake Groups action to undo."), "orange")
            return
        entry = self.bg_undo_stack.pop()
        self._bg_undo_restoring = True
        try:
            try:
                cmds.undo()
            except Exception as exc:
                self.log(bg_l10n.text("Maya undo failed: {error}").format(error=exc), "orange")
            self.restore_bg_undo_snapshot(entry.get("snapshot", {}))
        finally:
            self._bg_undo_restoring = False
            self.update_bg_undo_button()
        message = bg_l10n.text("Undone Bake Groups action: {action}").format(action=entry.get("action", ""))
        self.log(message, "lightgreen")
        cmds.inViewMessage(amg=message, pos='midCenter', fade=True)

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

        hp_main, lp_main, _ = self.core.resolve_main_nodes(pair)
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
        action_support_package = menu.addAction(bg_l10n.text("Save Support Package"))
        action_support_package.triggered.connect(self.save_support_package)
        action_check_updates = menu.addAction(bg_l10n.text("Check for Updates"))
        action_check_updates.triggered.connect(self.check_updates_from_log_menu)
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

    def build_support_environment_snapshot(self):
        pair = next((p for p in self.root_pairs if p.get('id') == self.active_root_id), None)
        try:
            maya_version = cmds.about(version=True)
        except Exception:
            maya_version = "Unknown"
        try:
            maya_api = cmds.about(apiVersion=True)
        except Exception:
            maya_api = "Unknown"
        data = {
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "plugin_version": getattr(bg_update.bg_version, "__version__", "Unknown"),
            "plugin_name": getattr(bg_update.bg_version, "PLUGIN_NAME", "Bake Groups Tool"),
            "language": bg_l10n.current_language(),
            "maya_version": maya_version,
            "maya_api": maya_api,
            "scene": cmds.file(q=True, sn=True) or "Untitled",
            "active_chapter": pair.get("base") if pair else None,
            "is_final_view": bool(getattr(self, "is_final_view", False)),
            "is_preview_active": bool(getattr(self, "is_preview_active", False)),
            "active_material_visibility_filter": getattr(self, "active_material_visibility_filter", None),
            "settings": {
                "hp_strategy": self.combo_hp_strategy.currentText() if hasattr(self, "combo_hp_strategy") else None,
                "hp_collision_pct": self.spin_threshold.value() if hasattr(self, "spin_threshold") else None,
                "ignore_floaters": self.chk_ignore_floaters.isChecked() if hasattr(self, "chk_ignore_floaters") else None,
                "material_slots": self.chk_material_slots.isChecked() if hasattr(self, "chk_material_slots") else None,
                "hp_link_vtx": self.spin_compound_link_verts.value() if hasattr(self, "spin_compound_link_verts") else None,
                "hp_link_dist_pct": self.spin_compound_link_dist.value() if hasattr(self, "spin_compound_link_dist") else None,
                "color_groups": self.cb_color_subgroups.isChecked() if hasattr(self, "cb_color_subgroups") else None,
                "keep_hp": self.cb_keep_hp_structure.isChecked() if hasattr(self, "cb_keep_hp_structure") else None,
            },
        }
        return data

    def build_display_layer_snapshot(self):
        lines = []
        layers = cmds.ls(type="displayLayer") or []
        for layer in sorted(layers):
            if layer == "defaultLayer":
                continue
            try:
                visible = cmds.getAttr("{}.visibility".format(layer))
            except Exception:
                visible = "Unknown"
            members = cmds.editDisplayLayerMembers(layer, q=True, fullNames=True) or []
            lines.append("{} | visible={} | members={}".format(layer, visible, len(members)))
            if members:
                lines.append("  {}".format(self._format_debug_names([m.split('|')[-1] for m in members], limit=40)))
        return lines or ["(no custom display layers)"]

    def save_support_package(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_path = os.path.join(os.path.expanduser("~"), "Desktop", "BakeGroups_Support_{}.zip".format(timestamp))
        file_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            bg_l10n.text("Save Support Package"),
            default_path,
            bg_l10n.text("Zip Files (*.zip);;All Files (*)")
        )
        if not file_path:
            return
        if not os.path.splitext(file_path)[1]:
            file_path += ".zip"

        debug_lines = list(getattr(self, 'last_debug_lines', []) or [])
        action_lines = list(getattr(self, 'user_action_lines', []) or [])
        visible_log = self.log_output.toPlainText()
        scene_snapshot = self.build_current_scene_snapshot()
        display_layers = self.build_display_layer_snapshot()
        environment = self.build_support_environment_snapshot()

        report = []
        report.append("Bake Groups Support Package")
        report.append("Saved: {}".format(environment.get("saved_at")))
        report.append("Scene: {}".format(environment.get("scene")))
        report.append("Plugin: {} {}".format(environment.get("plugin_name"), environment.get("plugin_version")))
        report.append("Maya: {} | API: {}".format(environment.get("maya_version"), environment.get("maya_api")))
        report.append("Language: {}".format(environment.get("language")))
        report.append("")
        report.append("=== Visible Log ===")
        report.append(visible_log if visible_log else "(empty)")
        report.append("")
        report.append("=== User Actions ===")
        report.extend(action_lines if action_lines else ["(no recorded user actions)"])
        report.append("")
        report.append("=== Current Scene Snapshot ===")
        report.extend(scene_snapshot)
        report.append("")
        report.append("=== Display Layers ===")
        report.extend(display_layers)
        report.append("")
        report.append("=== Analyze HP Debug ===")
        report.extend(debug_lines if debug_lines else ["(Analyze HP debug is not available for this session.)"])

        try:
            with zipfile.ZipFile(file_path, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("support_report.txt", "\n".join(report))
                archive.writestr("visible_log.txt", visible_log or "")
                archive.writestr("user_actions.txt", "\n".join(action_lines))
                archive.writestr("scene_snapshot.txt", "\n".join(scene_snapshot))
                archive.writestr("display_layers.txt", "\n".join(display_layers))
                archive.writestr("analyze_hp_debug.txt", "\n".join(debug_lines))
                archive.writestr("environment.json", json.dumps(environment, indent=2, ensure_ascii=False, default=str))
                archive.writestr("session_pairs.json", json.dumps(self.root_pairs, indent=2, ensure_ascii=False, default=str))
                manifest_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "update_manifest.json")
                if os.path.exists(manifest_path):
                    archive.write(manifest_path, "update_manifest.json")
        except Exception as exc:
            self.log(bg_l10n.text("Failed to save support package: {error}").format(error=exc), "red")
            return

        self.log(bg_l10n.text("Support package saved: {path}").format(path=file_path), "lightgreen")

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

    def check_updates_from_log_menu(self):
        if self.update_worker and self.update_worker.isRunning():
            self.log(bg_l10n.text("Update check is already running."), "orange")
            return
        self.manual_update_check_requested = True
        self.log(bg_l10n.text("Checking for updates..."), "lightblue")
        self.start_update_check()

    def handle_update_check_result(self, result):
        if self._is_closing:
            return
        try:
            self.objectName()
        except RuntimeError:
            return
        manual_check = bool(getattr(self, "manual_update_check_requested", False))
        self.manual_update_check_requested = False
        if not result:
            if manual_check:
                self.log(bg_l10n.text("Update check failed: {error}").format(error="No result"), "red")
            return
        if result.get("error"):
            if manual_check:
                self.log(bg_l10n.text("Update check failed: {error}").format(error=result.get("error")), "red")
            return
        if not result.get("is_update_available"):
            if manual_check:
                current = result.get("current_version") or getattr(bg_update.bg_version, "__version__", "")
                latest = result.get("remote_version") or current
                self.log(bg_l10n.text("No update available. Installed: {current}. Latest: {latest}.").format(current=current, latest=latest), "lightgreen")
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
            self.restore_subgroup_colors(clean_history=True)
            pair = next((p for p in self.root_pairs if p.get('id') == self.active_root_id), None)
            if pair:
                hp_main, lp_main, _ = self.core.resolve_main_nodes(pair)
                removed = self.cleanup_subgroup_preview_color_sets([hp_main, lp_main], clean_history=True)
                if removed:
                    self.log("Color Groups: removed {} old preview color set(s).".format(removed), "lightblue")
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

    def remove_subgroup_preview_color_set(self, target, clean_history=False):
        if not target or not cmds.objExists(target):
            return False
        try:
            color_sets = cmds.polyColorSet(target, query=True, allColorSets=True) or []
            if "BG_Subgroup_Color" in color_sets:
                cmds.polyColorSet(target, delete=True, colorSet="BG_Subgroup_Color")
                if clean_history:
                    try:
                        cmds.delete(target, constructionHistory=True)
                    except Exception:
                        pass
                return True
        except Exception:
            pass
        return False

    def cleanup_subgroup_preview_color_sets(self, roots=None, clean_history=False):
        roots = roots or []
        targets = set()
        for root in roots:
            if not root or not cmds.objExists(root):
                continue
            mesh_shapes = cmds.listRelatives(root, allDescendents=True, fullPath=True, type='mesh', noIntermediate=True) or []
            for shape in mesh_shapes:
                parent = cmds.listRelatives(shape, parent=True, fullPath=True) or []
                if parent:
                    targets.add(parent[0])
        removed = 0
        for target in targets:
            try:
                color_sets = cmds.polyColorSet(target, query=True, allColorSets=True) or []
                if "BG_Subgroup_Color" in color_sets:
                    if self.remove_subgroup_preview_color_set(target, clean_history=clean_history):
                        removed += 1
            except Exception:
                pass
        return removed

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
        if not target or not shape:
            return
        try:
            self.ensure_subgroup_color_set(target)
            cmds.polyColorPerVertex(target, rgb=self.viewport_subgroup_color(color), colorDisplayOption=True, notUndoable=True)
            if shape and cmds.objExists("{}.displayColors".format(shape)):
                cmds.setAttr("{}.displayColors".format(shape), True)
            if shape and cmds.objExists("{}.displayColorChannel".format(shape)):
                try:
                    cmds.setAttr("{}.displayColorChannel".format(shape), "color", type="string")
                except Exception:
                    pass
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

    def restore_subgroup_colors(self, clean_history=False):
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
                            self.remove_subgroup_preview_color_set(target, clean_history=clean_history)
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
        if not self.active_chapter_has_subgroups():
            return
        if reset_indices:
            self.subgroup_color_index_map = {}
        self.update_subgroup_colors()

    def active_chapter_has_subgroups(self, pair=None):
        pair = pair or next((p for p in self.root_pairs if p.get('id') == self.active_root_id), None)
        if not pair:
            return False
        hp_main, _, _ = self.core.resolve_main_nodes(pair)
        if not hp_main or not cmds.objExists(hp_main):
            return False
        for child in cmds.listRelatives(hp_main, children=True, type='transform', fullPath=True) or []:
            if not child or not cmds.objExists(child):
                continue
            if cmds.listRelatives(child, shapes=True, type='mesh', noIntermediate=True):
                continue
            return True
        return False

    def update_subgroup_colors(self):
        if not hasattr(self, 'cb_color_subgroups') or not self.cb_color_subgroups.isChecked():
            return
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
        if not self.active_chapter_has_subgroups(pair):
            return
        self.restore_subgroup_colors(clean_history=True)
        hp_main, lp_main, _ = self.core.resolve_main_nodes(pair)
        self.cleanup_subgroup_preview_color_sets([hp_main])
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

    def shutdown_for_reload(self):
        if getattr(self, '_shutdown_for_reload_done', False):
            return True
        self._is_closing = True
        if self.update_check_timer:
            try:
                self.update_check_timer.stop()
            except RuntimeError:
                pass
        dialog = self.update_dialog
        install_worker = getattr(dialog, "install_worker", None) if dialog else None
        if install_worker and install_worker.isRunning():
            self._is_closing = False
            try:
                dialog.raise_()
                dialog.activateWindow()
            except RuntimeError:
                pass
            return False
        if not self._stop_worker_for_close('hp_worker'):
            self._is_closing = False
            return False
        if not self._stop_worker_for_close('lp_worker'):
            self._is_closing = False
            return False
        if not self._stop_update_worker_for_close():
            self._is_closing = False
            return False
        if dialog:
            try:
                dialog.close()
            except RuntimeError:
                pass
        try:
            self.restore_subgroup_colors()
        except Exception:
            pass
        for job_id in self.script_jobs:
            try:
                if cmds.scriptJob(exists=job_id):
                    cmds.scriptJob(kill=job_id, force=True)
            except:
                pass
        self.script_jobs = []
        self.remove_bg_undo_event_filter()
        self.update_worker = None
        self.update_dialog = None
        self.update_check_timer = None
        self._shutdown_for_reload_done = True
        return True

    def closeEvent(self, event):
        if not self.shutdown_for_reload():
            event.ignore()
            return
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

    def set_localized_button_state(self, button, key):
        if not button:
            return
        button.setProperty("bg_i18n_key", key)
        button.setText(bg_l10n.text(key))
        tip = bg_l10n.tooltip(key)
        button.setToolTip(tip)
        button.setStatusTip(tip)
        button.setProperty("bg_status_tip", tip)

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
            self.schedule_dock_relayout()
            return

        if not self.active_root_id:
            self.schedule_dock_relayout()
            return
        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not pair:
            self.schedule_dock_relayout()
            return
        hp_main, lp_main, _ = self.core.resolve_main_nodes(pair)
        if not hp_main or not lp_main:
            self.schedule_dock_relayout()
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

            is_vis = self.subgroup_pair_is_visible(hp_node, lp_node)
            btn_vis = QtWidgets.QPushButton("Vis" if is_vis else "Hid")
            btn_vis.setFixedSize(30, 24)
            btn_vis.setStyleSheet("background-color: #4a5d4a;" if is_vis else "background-color: #8c4242;")
            btn_vis.clicked.connect(lambda checked=False, h=hp_node, l=lp_node, b=btn_vis: self.run_undoable_bg_action("Subgroup Visibility", self.toggle_subgroup_vis, h, l, b))
            btn_vis.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            btn_vis.customContextMenuRequested.connect(lambda pos, h=hp_node, l=lp_node: self.run_undoable_bg_action("Isolate Subgroup Visibility", self.isolate_subgroup_vis, h, l))
            layout.addWidget(btn_vis)

            btn_name = SubgroupButton(ui_name)
            btn_name.setStyleSheet(self.subgroup_name_style(ui_name, is_active_subgroup))
            btn_name.clicked.connect(lambda checked=False, n=ui_name: self.set_active_subgroup(n))
            btn_name.doubleClicked.connect(lambda checked=False, h=hp_node, l=lp_node: self.select_meshes_in_group(h, l))
            btn_name.rightClicked.connect(lambda checked=False, old_name=ui_name, h=hp_node, l=lp_node: self.run_undoable_bg_action("Rename Group", self.rename_subgroup_ui, old_name, h, l))
            layout.addWidget(btn_name, stretch=1)

            btn_plus = QtWidgets.QPushButton("Add")
            btn_plus.setFixedSize(56, 24)
            btn_plus.setProperty("bg_no_tooltip", True)
            btn_plus.setStyleSheet(self.subgroup_add_button_style(is_active_subgroup))
            btn_plus.clicked.connect(lambda checked=False, h=hp_node, l=lp_node, pm_hp=hp_main, pm_lp=lp_main: self.run_undoable_bg_action("Add to Group", self.add_to_groups_ui, h, l, pm_hp, pm_lp))
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
            btn_lock.clicked.connect(lambda checked=False, n=ui_name: self.run_undoable_bg_action("Toggle Group Lock", self.toggle_lock, n))
            layout.addWidget(btn_lock)

            btn_del = QtWidgets.QPushButton("X")
            btn_del.setFixedSize(24, 24)
            btn_del.setStyleSheet("background-color: #8c4242;")
            btn_del.clicked.connect(lambda checked=False, h=hp_node, l=lp_node, r_h=hp_main, r_l=lp_main: self.run_undoable_bg_action("Delete Group", self.safe_delete_subgroup_ui, h, l, r_h, r_l))
            layout.addWidget(btn_del)

            self.subgroups_layout.addWidget(frame)
        bg_l10n.localize_widget_tree(self.subgroups_widget)
        self.clear_disabled_tooltips(self.subgroups_widget)
        self.schedule_dock_relayout()

    def clear_disabled_tooltips(self, root):
        if not root:
            return
        widgets = [root] + root.findChildren(QtWidgets.QWidget)
        for widget in widgets:
            if widget.property("bg_no_tooltip"):
                widget.setToolTip("")
                widget.setStatusTip("")
                widget.setProperty("bg_status_tip", "")

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
        self.set_localized_button_state(self.btn_toggle_hp, "HP Visible" if hp_vis else "HP Hidden")
        self.btn_toggle_hp.setStyleSheet("background-color: #4a5d4a;" if hp_vis else "background-color: #8c4242;")

        self.btn_toggle_lp.setChecked(lp_vis)
        self.set_localized_button_state(self.btn_toggle_lp, "LP Visible" if lp_vis else "LP Hidden")
        self.btn_toggle_lp.setStyleSheet("background-color: #4a5d4a;" if lp_vis else "background-color: #8c4242;")

        material_filter = getattr(self, 'active_material_visibility_filter', None)
        group_state = self.get_active_subgroups_visibility_state(hp_node, lp_node, hp_vis, lp_vis)
        if material_filter:
            material_nodes = [
                node for node in self.get_active_subgroup_nodes(hp_node, lp_node)
                if self.material_slot_from_subgroup_name(node) == material_filter
            ]
            material_visible = sum(1 for node in material_nodes if cmds.objExists(node) and self.is_visible(node))
            material_state = "all" if material_nodes and material_visible == len(material_nodes) else "partial"
            self.btn_toggle_groups.setChecked(False)
            if material_state == "all":
                self.set_localized_button_state(self.btn_toggle_groups, "Groups Vis/M")
                self.btn_toggle_groups.setStyleSheet("background-color: #b79b2c; color: #1f1f1f; font-weight: bold;")
            else:
                self.set_localized_button_state(self.btn_toggle_groups, "Groups Hid/M")
                self.btn_toggle_groups.setStyleSheet("background-color: #b79b2c; color: #1f1f1f; font-weight: bold;")
            self.btn_toggle_hp.blockSignals(False)
            self.btn_toggle_lp.blockSignals(False)
            self.btn_toggle_groups.blockSignals(False)
            return

        if group_state == "all":
            self.btn_toggle_groups.setChecked(True)
            self.set_localized_button_state(self.btn_toggle_groups, "Groups Vis")
            self.btn_toggle_groups.setStyleSheet("background-color: #4a5d4a;")
        elif group_state == "partial":
            self.btn_toggle_groups.setChecked(False)
            self.set_localized_button_state(self.btn_toggle_groups, "Groups Hid")
            self.btn_toggle_groups.setStyleSheet("background-color: #b79b2c; color: #1f1f1f; font-weight: bold;")
        else:
            self.btn_toggle_groups.setChecked(False)
            self.set_localized_button_state(self.btn_toggle_groups, "Groups Hidden")
            self.btn_toggle_groups.setStyleSheet("background-color: #8c4242;")

        self.btn_toggle_hp.blockSignals(False)
        self.btn_toggle_lp.blockSignals(False)
        self.btn_toggle_groups.blockSignals(False)

    def get_active_subgroup_nodes(self, hp_node, lp_node):
        nodes = []
        for p_node in [hp_node, lp_node]:
            if not p_node or not cmds.objExists(p_node):
                continue
            for child in (cmds.listRelatives(p_node, children=True, type='transform', fullPath=True) or []):
                if cmds.objExists(child):
                    nodes.append(child)
        return nodes

    def material_slot_from_subgroup_name(self, node_or_name):
        name = str(node_or_name or "").split('|')[-1]
        match = re.match(r"^(M\d{2,})(?:_|\.)", name)
        return match.group(1) if match else None

    def get_active_material_slots(self, hp_node, lp_node):
        slots = set()
        for node in self.get_active_subgroup_nodes(hp_node, lp_node):
            slot = self.material_slot_from_subgroup_name(node)
            if slot:
                slots.add(slot)
        return sorted(slots)

    def mesh_transform_nodes_under(self, node):
        if not node or not cmds.objExists(node):
            return []
        shapes = cmds.listRelatives(node, shapes=True, fullPath=True, type='mesh', noIntermediate=True) or []
        if shapes:
            return [node]
        mesh_shapes = cmds.listRelatives(node, allDescendents=True, fullPath=True, type='mesh', noIntermediate=True) or []
        transforms = []
        seen = set()
        for shape in mesh_shapes:
            parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
            if parents and parents[0] not in seen:
                seen.add(parents[0])
                transforms.append(parents[0])
        return transforms

    def lp_material_records_for_node(self, lp_node, include_faces=True):
        if not om or not lp_node or not cmds.objExists(lp_node):
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
            material = self.material_from_shading_engine(sg)
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

    def lp_material_slot_map_for_nodes(self, lp_nodes):
        records_by_key = {}
        for node in lp_nodes or []:
            for mesh_node in self.mesh_transform_nodes_under(node):
                for rec in self.lp_material_records_for_node(mesh_node, include_faces=False):
                    key = rec.get("key")
                    if key:
                        records_by_key.setdefault(key, rec)
        if len(records_by_key) <= 1:
            return {}
        ordered = sorted(records_by_key.values(), key=lambda rec: ((rec.get("material") or rec.get("key") or "").split('|')[-1].lower(), (rec.get("key") or "").split('|')[-1].lower()))
        return {rec.get("key"): "M{:02d}".format(index) for index, rec in enumerate(ordered, 1) if rec.get("key")}

    def lp_material_slots_for_node(self, lp_node, slot_by_key=None):
        slot_by_key = slot_by_key or {}
        records = self.lp_material_records_for_node(lp_node, include_faces=True)
        if len(records) <= 1:
            return {}
        if not slot_by_key:
            ordered = sorted(records, key=lambda rec: ((rec.get("material") or rec.get("key") or "").split('|')[-1].lower(), (rec.get("key") or "").split('|')[-1].lower()))
            slot_by_key = {rec.get("key"): "M{:02d}".format(index) for index, rec in enumerate(ordered, 1) if rec.get("key")}
        slots = {}
        for rec in records:
            slot = slot_by_key.get(rec.get("key"))
            if slot:
                slots.setdefault(slot, [])
                slots[slot].extend(rec.get("faces") or [])
        return slots

    def material_from_shading_engine(self, shading_engine):
        try:
            materials = cmds.listConnections("{}.surfaceShader".format(shading_engine), source=True, destination=False) or []
            return materials[0] if materials else None
        except Exception:
            return None

    def face_components_for_indices(self, node, faces):
        if not faces:
            return []
        faces = sorted(set(int(face) for face in faces))
        components = []
        start = faces[0]
        prev = faces[0]
        for face in faces[1:]:
            if face == prev + 1:
                prev = face
                continue
            components.append("{}.f[{}]".format(node, start) if start == prev else "{}.f[{}:{}]".format(node, start, prev))
            start = prev = face
        components.append("{}.f[{}]".format(node, start) if start == prev else "{}.f[{}:{}]".format(node, start, prev))
        return components

    def show_all_lp_material_faces(self, lp_nodes):
        for node in lp_nodes or []:
            if node and cmds.objExists(node):
                try:
                    cmds.showHidden(node)
                except Exception:
                    pass

    def isolate_lp_material_faces(self, lp_node, slot, slot_by_key=None):
        slots = self.lp_material_slots_for_node(lp_node, slot_by_key=slot_by_key)
        if not slots:
            return False
        self.show_all_lp_material_faces([lp_node])
        hide_faces = []
        for other_slot, faces in slots.items():
            if other_slot != slot:
                hide_faces.extend(faces)
        if not hide_faces:
            return True
        saved_selection = cmds.ls(selection=True, long=True) or []
        try:
            components = self.face_components_for_indices(lp_node, hide_faces)
            if components:
                cmds.select(components, replace=True)
                cmds.hide()
        finally:
            if saved_selection:
                cmds.select(saved_selection, replace=True)
            else:
                cmds.select(clear=True)
        return True

    def apply_lp_material_slot_visibility(self, lp_node, slot, slot_by_key=None):
        if not lp_node or not cmds.objExists(lp_node):
            return False
        mesh_nodes = self.mesh_transform_nodes_under(lp_node)
        if mesh_nodes and not (len(mesh_nodes) == 1 and mesh_nodes[0] == lp_node):
            any_visible = False
            for mesh_node in mesh_nodes:
                if self.apply_lp_material_slot_visibility(mesh_node, slot, slot_by_key=slot_by_key):
                    any_visible = True
            cmds.setAttr("{}.visibility".format(lp_node), any_visible)
            return any_visible

        records = self.lp_material_records_for_node(lp_node, include_faces=True)
        if not records:
            cmds.setAttr("{}.visibility".format(lp_node), False)
            return False

        if len(records) == 1:
            rec_slot = (slot_by_key or {}).get(records[0].get("key"))
            visible = rec_slot == slot or (not slot_by_key and slot == "M01")
            if visible:
                self.show_all_lp_material_faces([lp_node])
            cmds.setAttr("{}.visibility".format(lp_node), visible)
            return visible

        visible = self.isolate_lp_material_faces(lp_node, slot, slot_by_key=slot_by_key)
        cmds.setAttr("{}.visibility".format(lp_node), visible)
        return visible

    def show_groups_visibility_context_menu(self, pos):
        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not pair:
            return
        hp_main, lp_main, _ = self.core.resolve_main_nodes(pair)
        menu = QtWidgets.QMenu(self)
        slots = self.get_active_material_slots(hp_main, lp_main)
        if slots:
            for slot in slots:
                action = menu.addAction(bg_l10n.text("Only Show {slot}").format(slot=slot))
                action.triggered.connect(lambda checked=False, s=slot: self.run_undoable_bg_action("Only Show Material Section", self.only_show_material_slot, s))
            menu.addSeparator()
        else:
            action = menu.addAction(bg_l10n.text("No material sections found"))
            action.setEnabled(False)
        show_all_action = menu.addAction(bg_l10n.text("Groups Vis"))
        show_all_action.triggered.connect(lambda checked=False: self.run_undoable_bg_action("Groups Visibility", self.set_all_subgroups_vis, True))
        menu.exec_(self.btn_toggle_groups.mapToGlobal(pos)) if hasattr(menu, 'exec_') else menu.exec(self.btn_toggle_groups.mapToGlobal(pos))

    def only_show_material_slot(self, slot):
        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not pair:
            return
        self.active_material_visibility_filter = slot
        hp_main, lp_main, _ = self.core.resolve_main_nodes(pair)
        if hp_main and cmds.objExists(hp_main):
            cmds.setAttr("{}.visibility".format(hp_main), True)
        if lp_main and cmds.objExists(lp_main):
            cmds.setAttr("{}.visibility".format(lp_main), True)

        hp_children = cmds.listRelatives(hp_main, children=True, type='transform', fullPath=True) if hp_main and cmds.objExists(hp_main) else []
        lp_children = cmds.listRelatives(lp_main, children=True, type='transform', fullPath=True) if lp_main and cmds.objExists(lp_main) else []
        hp_children = hp_children or []
        lp_children = lp_children or []
        lp_has_slot_children = any(self.material_slot_from_subgroup_name(child) for child in lp_children)
        lp_slot_by_key = self.lp_material_slot_map_for_nodes(lp_children) if not lp_has_slot_children else {}
        self.show_all_lp_material_faces(lp_children)

        for child in hp_children:
            if cmds.objExists(child):
                cmds.setAttr("{}.visibility".format(child), self.material_slot_from_subgroup_name(child) == slot)

        for child in lp_children:
            if not cmds.objExists(child):
                continue
            child_slot = self.material_slot_from_subgroup_name(child)
            if lp_has_slot_children:
                cmds.setAttr("{}.visibility".format(child), child_slot == slot)
            else:
                self.apply_lp_material_slot_visibility(child, slot, slot_by_key=lp_slot_by_key)

        if getattr(self, 'is_final_view', False):
            for widget_data in getattr(self, 'final_mesh_widgets', []):
                subgroup_name = widget_data.get('subgroup_name') or ''
                state = self.material_slot_from_subgroup_name(subgroup_name) == slot
                for hp in widget_data.get('hp_nodes', []):
                    if cmds.objExists(hp):
                        cmds.setAttr(hp + ".visibility", state)

        self.sync_toggle_buttons(hp_main, lp_main)
        self.refresh_left_panel()
        if hasattr(self, 'record_user_action'):
            self.record_user_action("Only Show Material Section", slot)

    def get_active_subgroups_visibility_state(self, hp_node, lp_node, hp_vis=None, lp_vis=None):
        child_nodes = self.get_active_subgroup_nodes(hp_node, lp_node)
        if not child_nodes:
            if hp_vis is None:
                hp_vis = cmds.getAttr("{}.visibility".format(hp_node)) if hp_node and cmds.objExists(hp_node) else False
            if lp_vis is None:
                lp_vis = cmds.getAttr("{}.visibility".format(lp_node)) if lp_node and cmds.objExists(lp_node) else False
            return "all" if (hp_vis or lp_vis) else "none"
        visible_count = sum(1 for node in child_nodes if cmds.getAttr("{}.visibility".format(node)))
        if visible_count == len(child_nodes):
            return "all"
        if visible_count == 0:
            return "none"
        return "partial"

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
        self.set_localized_button_state(btn, "{} Visible".format(type_str) if state else "{} Hidden".format(type_str))
        btn.setStyleSheet("background-color: #4a5d4a;" if state else "background-color: #8c4242;")
        if parent_node and cmds.objExists(parent_node):
            cmds.setAttr("{}.visibility".format(parent_node), state)

    def set_all_subgroups_vis(self, state):
        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not pair:
            return
        if getattr(self, 'active_material_visibility_filter', None):
            self.active_material_visibility_filter = None
            state = True
        hp_main, lp_main, _ = self.core.resolve_main_nodes(pair)
        for p_node in [hp_main, lp_main]:
            if p_node and cmds.objExists(p_node):
                for child in (cmds.listRelatives(p_node, children=True, type='transform', fullPath=True) or []):
                    cmds.setAttr("{}.visibility".format(child), state)
                    if state and p_node == lp_main:
                        self.show_all_lp_material_faces([child])
        self.set_localized_button_state(self.btn_toggle_groups, "Groups Vis" if state else "Groups Hidden")
        self.btn_toggle_groups.setStyleSheet("background-color: #4a5d4a;" if state else "background-color: #8c4242;")
        if getattr(self, 'is_final_view', False):
            for widget_data in getattr(self, 'final_mesh_widgets', []):
                for hp in widget_data.get('hp_nodes', []):
                    if cmds.objExists(hp):
                        cmds.setAttr(hp + ".visibility", state)
        self.sync_toggle_buttons(hp_main, lp_main)
        self.refresh_left_panel()

    def is_visible(self, node):
        return cmds.getAttr("{}.visibility".format(node)) if node and cmds.objExists(node) else False

    def subgroup_pair_is_visible(self, hp, lp):
        nodes = [node for node in (hp, lp) if node and cmds.objExists(node)]
        return bool(nodes) and all(self.is_visible(node) for node in nodes)

    def toggle_subgroup_vis(self, hp, lp, btn_widget):
        new_state = not self.subgroup_pair_is_visible(hp, lp)
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
        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if pair:
            hp_main, lp_main, _ = self.core.resolve_main_nodes(pair)
            self.sync_toggle_buttons(hp_main, lp_main)

    def isolate_subgroup_vis(self, hp, lp):
        pair = next((p for p in self.root_pairs if p['id'] == self.active_root_id), None)
        if not pair:
            return
        hp_main, lp_main, _ = self.core.resolve_main_nodes(pair)
        material_filter = getattr(self, 'active_material_visibility_filter', None)
        subgroup_nodes = []
        for root in (hp_main, lp_main):
            if not root or not cmds.objExists(root):
                continue
            for child in (cmds.listRelatives(root, children=True, type='transform', fullPath=True) or []):
                if cmds.objExists(child) and not cmds.listRelatives(child, shapes=True, type='mesh', noIntermediate=True):
                    subgroup_nodes.append(child)
        target_nodes = set(cmds.ls([node for node in (hp, lp) if node and cmds.objExists(node)], long=True) or [])
        if material_filter:
            material_nodes = set(node for node in subgroup_nodes if self.material_slot_from_subgroup_name(node) == material_filter)
            target_nodes = target_nodes.intersection(material_nodes)
            if not target_nodes:
                return
            subgroup_nodes = [node for node in subgroup_nodes if node in material_nodes]
        visible_nodes = set(node for node in subgroup_nodes if self.is_visible(node))
        show_all = bool(target_nodes) and visible_nodes == target_nodes
        for node in subgroup_nodes:
            cmds.setAttr("{}.visibility".format(node), True if show_all else node in target_nodes)
        if material_filter and show_all:
            self.only_show_material_slot(material_filter)
            return
        self.sync_toggle_buttons(hp_main, lp_main)
        self.refresh_left_panel()
        if hasattr(self, 'record_user_action'):
            target_names = [node.split('|')[-1] for node in (hp, lp) if node and cmds.objExists(node)]
            action = "Show All Group Visibility" if show_all else "Isolate Group Visibility"
            self.record_user_action(action, ", ".join(target_names))

    def show_subgroups_context_menu(self, pos):
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(bg_core.BakeConfig.STYLE_CONTEXT_MENU)
        action_optimize = menu.addAction("Optimize subgroups (delete empty)")
        action_optimize.triggered.connect(lambda checked=False: self.run_undoable_bg_action("Optimize Groups", self.optimize_subgroups))
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


def cleanup_stale_bake_manager_ui():
    app = QtWidgets.QApplication.instance()
    if app:
        for widget in list(app.allWidgets()):
            try:
                object_name = widget.objectName()
            except RuntimeError:
                continue
            if object_name not in ("BakeManagerUI", bg_core.BakeConfig.WORKSPACE_NAME):
                continue
            try:
                if hasattr(widget, "shutdown_for_reload"):
                    widget.shutdown_for_reload()
            except Exception:
                pass
            try:
                widget.close()
            except RuntimeError:
                pass
            try:
                widget.setParent(None)
            except RuntimeError:
                pass
            try:
                widget.deleteLater()
            except RuntimeError:
                pass
        for _ in range(3):
            try:
                app.processEvents(QtCore.QEventLoop.AllEvents, 50)
            except Exception:
                try:
                    app.processEvents()
                except Exception:
                    break

    if cmds.workspaceControl(bg_core.BakeConfig.WORKSPACE_NAME, exists=True):
        try:
            cmds.deleteUI(bg_core.BakeConfig.WORKSPACE_NAME, control=True)
        except RuntimeError:
            pass
    try:
        if cmds.workspaceControlState(bg_core.BakeConfig.WORKSPACE_NAME, exists=True):
            cmds.workspaceControlState(bg_core.BakeConfig.WORKSPACE_NAME, remove=True)
    except Exception:
        pass


def main():
    cleanup_stale_bake_manager_ui()
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
