"""Standalone PySide6 Bake Group Manager hosted by Blender.

The widget hierarchy follows the narrow Maya window while all mutable state is
owned by Blender and changed through undoable operators.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from functools import partial
from pathlib import Path
import json

import bpy

from .blender_bridge import (
    attach_qt_window_to_blender,
    blender_sidebar_category,
    blender_sidebar_rect,
    blender_workspace_identity,
    blender_header_popup_guard_active,
    blender_temporary_ui_active,
    blender_window_in_move_size,
    blender_window_rect,
    capture_context,
    place_qt_window_in_sidebar,
    qt_window_rect,
    reset_qt_window_suppression,
    set_qt_window_suppressed,
    start_native_popup_guard,
    stop_native_popup_guard,
    sync_blender_transient_z_order,
    sync_native_popup_guard,
)
from .controller import ManagerController
from .dependencies import enable_pyside6
from .qt_window_manager import window_manager as _qt_window_manager
from . import localization as i18n
from .progress import cancel_task, set_listener
from .color_preview import PALETTE


QtCore, QtGui, QtWidgets = enable_pyside6()


def manager_is_visible():
    return _qt_window_manager.is_primary_visible()


def __getattr__(name):
    """Keep legacy diagnostics that read module globals working."""
    if name == "_window":
        return _qt_window_manager.primary_window()
    if name == "_pump_registered":
        return _qt_window_manager.pump_active
    raise AttributeError(name)


def _asset_path(name):
    return str(Path(__file__).resolve().parents[2] / "assets" / name)


def _clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        child_layout = item.layout()
        if widget is not None:
            # Blender pumps Qt from a timer, so DeferredDelete events are not
            # guaranteed to run before the next chapter is rendered. Detach and
            # hide immediately; deleteLater remains the safe final destruction.
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        elif child_layout is not None:
            _clear_layout(child_layout)


class SubgroupNameButton(QtWidgets.QPushButton):
    doubleClicked = QtCore.Signal()

    def mouseDoubleClickEvent(self, event):
        self.doubleClicked.emit()
        event.accept()


class BakeToolsWindow(QtWidgets.QMainWindow):
    """Maya-shaped narrow manager backed by the active Blender scene."""

    def __init__(self):
        super().__init__()
        self.setObjectName("BakeToolsBlenderWindow")
        self.setWindowTitle("Bake Group Manager Pro")
        self.setWindowFlags(
            QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.NoDropShadowWindowHint
        )
        self.setWindowModality(QtCore.Qt.WindowModality.NonModal)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NativeWindow, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        # The native Blender Sidebar owns the width.  Keep the Qt minimum below
        # Blender's practical UI-region minimum so a narrow Sidebar can never
        # push this owned top-level window back over the VIEW_3D controls.
        self.setMinimumSize(210, 420)
        self.resize(520, 940)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._window_settings = QtCore.QSettings("BakeTools", "BakeGroupManagerPro")
        self._pseudo_docked = True
        self._applying_dock_geometry = False
        self._dock_initialized = False
        self._last_applied_dock_rect = None
        self._responsive_narrow = False
        self._responsive_page = "main"
        self._snapshot = None
        self._localized_language = ""
        self._dirty = True
        self._section_signatures = {}
        self._open_dialogs = set()
        self._progress_dialogs = {}
        # Maya tracks these per UI session, not in the scene file.  A chapter
        # is considered checked only after the complete preflight chain ends.
        self._mesh_checked_pair_ids = set()
        self._combined_check_skipped_pair_ids = set()
        self._final_selected_ids = set()
        self._final_rows = {}
        self._final_rubber = None
        self._final_rubber_origin = None
        # Ordinary Matcher row selection is UI-session state, separate from the
        # persistent Link flag.  Blender steals native focus while the selection
        # operator highlights HP objects, so Qt's inactive palette alone cannot
        # be used as the visible source of truth.
        self._matcher_selected_by_chapter = {}
        self.controller = ManagerController(self.request_refresh)
        set_listener(self._on_progress_event)
        self._build_ui()
        self.setStyleSheet(self._stylesheet())
        saved_geometry = self._window_settings.value("windowGeometry")
        if saved_geometry and not self._pseudo_docked:
            self.restoreGeometry(saved_geometry)
        self.refresh_from_store(force=True)

    @staticmethod
    def _stylesheet():
        return """
            QMainWindow, QWidget { background: #242424; color: #d1d1d1; font: 11px 'Segoe UI'; }
            QGroupBox { border: 1px solid #363636; border-radius: 2px; margin-top: 6px;
                        padding: 3px 2px 2px; font-weight: bold; }
            QGroupBox::title { subcontrol-origin: margin; left: 4px; padding: 0 2px; color: #dddddd; }
            QLabel { background: transparent; }
            QPushButton, QToolButton { background: #3b3b3b; border: 1px solid #505050;
                                      border-radius: 2px; min-height: 21px; padding: 1px 4px; }
            QPushButton:hover, QToolButton:hover { background: #4a4a4a; border-color: #707070; }
            QPushButton:pressed, QToolButton:pressed { background: #202020; }
            QPushButton:checked { border: 1px solid #b6b6b6; }
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QListWidget,
            QTreeWidget { background: #1c1c1c; border: 1px solid #353535; border-radius: 2px;
                          padding: 1px 2px; selection-background-color: #284c6a; }
            QComboBox::drop-down { width: 16px; border: none; }
            QTreeWidget::item, QListWidget::item { min-height: 21px; padding: 0; }
            QListWidget#matcherList::item:selected,
            QListWidget#matcherList::item:selected:active,
            QListWidget#matcherList::item:selected:!active {
                background: #3a5375; color: #ffffff; border: 1px solid #718ba5;
            }
            QScrollArea { border: 1px solid #343434; background: #1b1b1b; }
            QScrollBar:vertical { background: #232323; width: 10px; margin: 0; }
            QScrollBar::handle:vertical { background: #555; min-height: 18px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QSplitter::handle { background: #333; width: 2px; height: 2px; }
            QPushButton#analyze { background: #ff614a; color: white; font-weight: bold; }
            QPushButton#assign, QPushButton#exportAction { background: #2b9144; color: white; font-weight: bold; }
            QPushButton#findGroups { background: #e5a611; color: #111; font-weight: bold; }
            QPushButton#relocate, QPushButton#findSim, QPushButton#smoothView {
                background: #4568ad; color: white; font-weight: bold; }
            QPushButton#findAll { background: #2b9144; color: white; font-weight: bold; }
            QPushButton#exportSettings { background: #dd6200; color: white; font-weight: bold; }
            QPushButton#responsivePage { min-height: 19px; padding: 0 4px; }
            QPushButton#responsivePage:checked { background: #465a70; border-color: #7d91a7;
                                                  color: white; font-weight: bold; }
            QPushButton#link { background: #2d8a42; color: white; font-weight: bold; }
            QPushButton#unlink { background: #be3838; color: white; font-weight: bold; }
            QPushButton#activeRowName { color: white; font-weight: bold; border-color: #687d91; }
            QToolButton#combine { background: #4168ad; }
            QToolButton#separate { background: #986b3e; }
            QToolButton#findZbrush { background: #e49a0d; }
            QLabel#cageHeader { background: #3a2a44; border: 1px solid #5a3d6b;
                                color: white; font-weight: bold; padding: 4px; }
            QLabel#exportHeader { background: #23402a; border: 1px solid #356b45;
                                  color: white; font-weight: bold; padding: 4px; }
            QComboBox#scope { background: #2f5d3a; color: white; font-weight: bold;
                              border: 1px solid #59a06e; padding: 3px; }
        """

    @staticmethod
    def _compact(layout, margins=(2, 2, 2, 2), spacing=2):
        layout.setContentsMargins(*margins)
        layout.setSpacing(spacing)
        return layout

    def _button(self, text, callback=None, object_name=None, icon=None):
        button = QtWidgets.QPushButton(text)
        button.setProperty("bt_i18n_key", text)
        if object_name:
            button.setObjectName(object_name)
        if icon:
            button.setIcon(QtGui.QIcon(_asset_path(icon)))
            button.setIconSize(QtCore.QSize(22, 22))
        if callback:
            button.clicked.connect(callback)
        return button

    def _tool(self, icon, tooltip, object_name, action):
        button = QtWidgets.QToolButton()
        button.setObjectName(object_name)
        button.setToolTip(tooltip)
        button.setProperty("bt_i18n_tooltip_key", tooltip)
        button.setIcon(QtGui.QIcon(_asset_path(icon)))
        button.setIconSize(QtCore.QSize(29, 29))
        button.setFixedSize(42, 42)
        button.clicked.connect(lambda: self.controller.action(action))
        return button

    def _icon_button(self, asset, tooltip, callback, color="#3b3b3b", size=28, icon_size=18):
        button = QtWidgets.QToolButton()
        button.setToolTip(tooltip)
        button.setProperty("bt_i18n_tooltip_key", tooltip)
        button.setIcon(QtGui.QIcon(_asset_path(asset)))
        button.setIconSize(QtCore.QSize(icon_size, icon_size))
        button.setFixedSize(size, size)
        button.setStyleSheet(
            "QToolButton { background: %s; border: 1px solid #444; border-radius: 3px; padding: 1px; }" % color
        )
        button.clicked.connect(callback)
        return button

    def _setting(self, name, value):
        self.controller.set_setting(name, value)

    def request_refresh(self):
        self._dirty = True

    def _language(self):
        return self._snapshot.language if self._snapshot is not None else "EN"

    def _tr(self, key, default=None, **replacements):
        return i18n.text(key, self._language(), default, **replacements)

    def _apply_localization(self, root=None):
        i18n.localize_widget_tree(root or self, self._language(), (QtCore, QtGui, QtWidgets))

    def _on_progress_event(self, event):
        progress_text = lambda value: i18n.runtime_text(value, self._language())
        dialog = self._progress_dialogs.get(event.task_id)
        if event.kind == "BEGIN":
            dialog = QtWidgets.QProgressDialog(
                progress_text(event.label), self._tr("Cancel"), 0, 100, self
            )
            dialog.setWindowTitle(self._tr(event.title))
            dialog.setWindowModality(QtCore.Qt.WindowModality.NonModal)
            dialog.setMinimumDuration(0)
            dialog.setAutoClose(False); dialog.setAutoReset(False)
            dialog.setValue(event.value)
            if not event.cancellable:
                dialog.setCancelButton(None)
            else:
                dialog.canceled.connect(lambda task_id=event.task_id: cancel_task(task_id))
            self._progress_dialogs[event.task_id] = dialog
            self._apply_localization(dialog)
            dialog.show(); dialog.raise_()
        elif event.kind == "UPDATE" and dialog is not None:
            dialog.setLabelText(progress_text(event.label))
            dialog.setValue(event.value)
            dialog.repaint()
        elif event.kind == "END" and dialog is not None:
            dialog.setValue(100 if not event.cancelled else event.value)
            dialog.close(); dialog.deleteLater()
            self._progress_dialogs.pop(event.task_id, None)
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 10)

    def _open_nonblocking(self, dialog):
        """Open a Qt dialog without nesting an event loop inside Blender's timer."""
        dialog.setWindowModality(QtCore.Qt.WindowModality.NonModal)
        dialog.setModal(False)
        # Do not let QMessageBox destroy itself from inside its native
        # buttonClicked/finished stack.  Blender pumps Qt from a bpy timer and a
        # Blender operator may itself process events; WA_DeleteOnClose can then
        # invalidate QMessageBoxPrivate before Qt has returned from the click.
        dialog.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._apply_localization(dialog)
        self._open_dialogs.add(dialog)
        _qt_window_manager.register(dialog, role="dialog", unique=False)

        def discard(*_args):
            self._open_dialogs.discard(dialog)
            _qt_window_manager.unregister(dialog)

        def cleanup(*_args):
            discard()
            # Deletion is deliberately queued until the native click handler
            # has fully unwound.  Calling deleteLater directly is normally safe,
            # but not when Blender re-enters Qt while executing an operator.
            QtCore.QTimer.singleShot(0, dialog.deleteLater)

        dialog.finished.connect(cleanup)
        dialog.destroyed.connect(discard)
        # QDialog.open() silently promotes a dialog to WindowModal in Qt 6.
        # show() preserves the explicit NonModal contract and still returns.
        dialog.show()
        return dialog

    @staticmethod
    def _defer_dialog_action(callback):
        """Run Blender/UI work only after the current Qt dialog event returns."""
        if callback is not None:
            QtCore.QTimer.singleShot(0, callback)

    def _warning(self, title, text):
        box = QtWidgets.QMessageBox(
            QtWidgets.QMessageBox.Icon.Warning, self._tr(title), self._tr(str(text)), parent=self
        )
        box.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Ok)
        return self._open_nonblocking(box)

    def _confirm(self, title, text, accepted):
        box = QtWidgets.QMessageBox(
            QtWidgets.QMessageBox.Icon.Question, self._tr(title), self._tr(text), parent=self
        )
        box.setStandardButtons(
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QtWidgets.QMessageBox.StandardButton.No)

        def finished(_result):
            if box.standardButton(box.clickedButton()) == QtWidgets.QMessageBox.StandardButton.Yes:
                self._defer_dialog_action(accepted)

        box.finished.connect(finished)
        return self._open_nonblocking(box)

    def _request_text(self, title, label, initial, accepted):
        dialog = QtWidgets.QInputDialog(self)
        dialog.setWindowTitle(self._tr(title))
        dialog.setLabelText(self._tr(label))
        dialog.setInputMode(QtWidgets.QInputDialog.InputMode.TextInput)
        dialog.setTextValue(initial or "")
        dialog.accepted.connect(
            lambda: self._defer_dialog_action(
                lambda value=dialog.textValue().strip(): accepted(value)
            )
        )
        return self._open_nonblocking(dialog)

    def _popup_menu(self, menu, global_pos):
        """Show a context menu without a nested Qt event loop."""
        i18n.localize_menu(menu, self._language(), (QtCore, QtGui, QtWidgets))
        menu.aboutToHide.connect(menu.deleteLater)
        menu.popup(global_pos)

    def _dock_target_geometry(self):
        rect = blender_sidebar_rect(self)
        if rect is None:
            return None
        left, top, right, bottom = rect
        owner = blender_window_rect(self)
        # The UI region includes its vertical tab strip.  The manager must stay
        # *inside* that region: extending left into the WINDOW region covers the
        # navigation gizmo and also makes the native Sidebar width impossible to
        # adjust.  Keep a small uncovered lane on the left for Blender's own
        # resize boundary, plus the tabs and native Close row on the right/top.
        sidebar_resize_lane = 8
        sidebar_tab_strip = 28
        sidebar_header = 48
        target_left = left + sidebar_resize_lane
        target_right = right - sidebar_tab_strip
        target_top = min(bottom - 1, top + sidebar_header)
        if owner:
            target_left = max(target_left, owner[0])
            target_right = min(target_right, owner[2])
        width = max(1, target_right - target_left)
        height = max(1, bottom - target_top)
        return QtCore.QRect(target_left, target_top, width, height)

    def _set_pseudo_docked(self, value):
        # Kept as a compatibility hook for older tests/settings. The current
        # manager is always a Blender-owned frameless top-level Qt window.
        self._pseudo_docked = True

    def _sync_pseudo_dock(self):
        if blender_window_in_move_size(self):
            return
        target = self._dock_target_geometry()
        if target is None:
            return
        screen_rect = (
            target.left(), target.top(),
            target.left() + target.width(), target.top() + target.height(),
        )
        actual_rect = qt_window_rect(self)
        actual_matches = bool(
            actual_rect is not None
            and all(
                abs(int(actual) - int(expected)) <= 4
                for actual, expected in zip(actual_rect, screen_rect)
            )
        )
        if screen_rect == self._last_applied_dock_rect and actual_matches:
            return
        self._applying_dock_geometry = True
        try:
            applied = place_qt_window_in_sidebar(self, screen_rect)
        finally:
            self._applying_dock_geometry = False
        if applied:
            self._last_applied_dock_rect = screen_rect

    def _sync_sidebar_tab_visibility(self):
        category = blender_sidebar_category()
        # ``active_panel_category`` is briefly empty/UNSUPPORTED while Blender
        # creates or redraws a freshly opened Sidebar.  That is an unknown
        # bootstrap state, not evidence that the artist left Bake Tools.  Keep
        # the current suppression state until Blender reports a real category:
        # a first Open remains visible, while a manager already hidden on Item/
        # Tool/View cannot flash back over the native Sidebar during redraw.
        if category is not None:
            set_qt_window_suppressed(self, "sidebar_tab", category != "Bake Tools")
        return category

    def _bind_host_workspace(self):
        """Bind this manager session to the Workspace that explicitly opened it."""
        identity = blender_workspace_identity()
        if identity is not None:
            self._bt_host_workspace_identity = identity
        return identity

    def _sync_workspace_visibility(self):
        """Hide outside the opening Workspace and restore without a stale frame."""
        current = blender_workspace_identity()
        host = getattr(self, "_bt_host_workspace_identity", None)
        if host is None and current is not None:
            self._bt_host_workspace_identity = current
            host = current
        if current is None or host is None:
            # Blender can briefly report no Workspace while replacing a Screen.
            # As with an unknown Sidebar category, keep the current state rather
            # than flashing the external Qt HWND over an unrelated Workspace.
            return None

        matches = current[0] == host[0]
        was_workspace_suppressed = "workspace" in getattr(
            self, "_bt_suppression_reasons", set()
        )
        if matches and was_workspace_suppressed:
            # Recompute both tab state and native geometry while the HWND is
            # still transparent.  Only then remove the Workspace reason, so a
            # monitor/Workspace switch cannot reveal one stale frame elsewhere.
            self._last_applied_dock_rect = None
            self._sync_sidebar_tab_visibility()
            self._sync_pseudo_dock()
        set_qt_window_suppressed(self, "workspace", not matches)
        self._bt_in_host_workspace = matches
        return current

    def toggle_pseudo_dock(self):
        self._sync_pseudo_dock()

    def showEvent(self, event):
        super().showEvent(event)
        self._last_applied_dock_rect = None
        QtCore.QTimer.singleShot(0, self._initialize_pseudo_dock)

    def hideEvent(self, event):
        for dialog in tuple(self._progress_dialogs.values()):
            dialog.close(); dialog.deleteLater()
        self._progress_dialogs.clear()
        for dialog in tuple(self._open_dialogs):
            dialog.close()
        super().hideEvent(event)

    def _initialize_pseudo_dock(self):
        self._sync_pseudo_dock()
        self._dock_initialized = True

    def moveEvent(self, event):
        super().moveEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_responsive_layout()
        if hasattr(self, "_window_settings") and not self._applying_dock_geometry:
            self._window_settings.setValue("windowGeometry", self.saveGeometry())

    def closeEvent(self, event):
        self._window_settings.setValue("windowGeometry", self.saveGeometry())
        self._window_settings.sync()
        super().closeEvent(event)

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        outer = self._compact(QtWidgets.QVBoxLayout(central), (4, 4, 4, 4), 2)
        outer.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetNoConstraint)

        # Only appears while the real Blender Sidebar is too narrow for the
        # original two-column Maya layout.  At normal width this row is hidden,
        # so the requested visual structure remains unchanged.
        self.responsive_bar = QtWidgets.QWidget()
        self.responsive_bar.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.responsive_bar.setFixedHeight(24)
        responsive_layout = self._compact(QtWidgets.QHBoxLayout(self.responsive_bar), (0, 0, 0, 0), 2)
        self.responsive_main_button = self._button(
            "Main", lambda: self._set_responsive_page("main"), "responsivePage"
        )
        self.responsive_side_button = self._button(
            "Matcher / TOC", lambda: self._set_responsive_page("side"), "responsivePage"
        )
        for button in (self.responsive_main_button, self.responsive_side_button):
            button.setCheckable(True)
            responsive_layout.addWidget(button, 1)
        outer.addWidget(self.responsive_bar, 0)
        self.responsive_bar.hide()

        self.main_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.main_splitter.setChildrenCollapsible(False)
        outer.addWidget(self.main_splitter, 1)

        left = QtWidgets.QWidget()
        left.setMinimumWidth(0)
        self.left_panel = left
        left_layout = self._compact(QtWidgets.QVBoxLayout(left), (0, 0, 1, 0), 2)
        self.main_splitter.addWidget(left)
        self._build_selection(left_layout)
        self._build_tools(left_layout)
        self._build_analysis(left_layout)
        self._build_subgroups(left_layout)
        self._build_bottom_actions(left_layout)
        self._build_log(left_layout)

        right = QtWidgets.QWidget()
        right.setMinimumWidth(0)
        right.setMaximumWidth(205)
        self.right_panel = right
        right_layout = self._compact(QtWidgets.QVBoxLayout(right), (1, 0, 0, 0), 2)
        self.main_splitter.addWidget(right)
        self._build_right_side(right_layout)
        self.main_splitter.setStretchFactor(0, 1)
        self.main_splitter.setStretchFactor(1, 0)
        self.main_splitter.setSizes([315, 195])
        self._update_responsive_layout(force=True)

    def _set_responsive_page(self, page):
        self._responsive_page = "side" if page == "side" else "main"
        self._update_responsive_layout(force=True)

    def _update_responsive_layout(self, force=False):
        """Keep the Maya layout at full width and a usable fallback when narrow."""
        if not hasattr(self, "main_splitter"):
            return
        narrow = self.width() < 470
        if not force and narrow == self._responsive_narrow:
            return
        self._responsive_narrow = narrow
        if narrow:
            self.responsive_bar.show()
            show_main = self._responsive_page == "main"
            self.left_panel.setVisible(show_main)
            self.right_panel.setVisible(not show_main)
            self.right_panel.setMaximumWidth(16777215)
            self.responsive_main_button.setChecked(show_main)
            self.responsive_side_button.setChecked(not show_main)
        else:
            self.responsive_bar.hide()
            self.left_panel.show()
            self.right_panel.show()
            self.right_panel.setMaximumWidth(205)
            self.responsive_main_button.setChecked(False)
            self.responsive_side_button.setChecked(False)
            self.main_splitter.setSizes([315, 195])

    def _build_selection(self, parent):
        group = QtWidgets.QGroupBox("HP / LP")
        grid = self._compact(QtWidgets.QGridLayout(group), (3, 3, 3, 3), 2)
        self.hp_edit = QtWidgets.QLineEdit(); self.hp_edit.setPlaceholderText("Pick HP..."); self.hp_edit.setReadOnly(True)
        self.lp_edit = QtWidgets.QLineEdit(); self.lp_edit.setPlaceholderText("Pick LP..."); self.lp_edit.setReadOnly(True)
        grid.addWidget(self.hp_edit, 0, 0)
        self.pick_hp_button = self._button("Pick HP", lambda: self.controller.pick("HP"))
        self.pick_hp_button.setToolTip("Pick selected Object or active Collection. Right-click to choose explicitly.")
        self.pick_hp_button.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.pick_hp_button.customContextMenuRequested.connect(
            lambda pos: self._show_pick_menu("HP", self.pick_hp_button, pos)
        )
        grid.addWidget(self.pick_hp_button, 0, 1)
        grid.addWidget(self.lp_edit, 1, 0)
        self.pick_lp_button = self._button("Pick LP", lambda: self.controller.pick("LP"))
        self.pick_lp_button.setToolTip("Pick selected Object or active Collection. Right-click to choose explicitly.")
        self.pick_lp_button.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.pick_lp_button.customContextMenuRequested.connect(
            lambda pos: self._show_pick_menu("LP", self.pick_lp_button, pos)
        )
        grid.addWidget(self.pick_lp_button, 1, 1)
        create = self._button("", self._create_pair, icon="Create_Bake_Groups.png")
        # The icon-only button has no caption from which the localization pass
        # can infer its Maya help key, so keep the original Maya key explicitly.
        create.setToolTip(" Create Pair from Picked")
        create.setProperty("bt_i18n_tooltip_key", " Create Pair from Picked")
        create.setIconSize(QtCore.QSize(52, 52))
        create.setFixedSize(76, 76)
        grid.addWidget(create, 0, 2, 2, 1)
        parent.addWidget(group)

    @staticmethod
    def _pair_base_name(name, role):
        value = (name or "").strip()
        suffix = "_{}".format(role.upper())
        if value.upper().endswith(suffix):
            candidate = value[:-len(suffix)].rstrip("_.- ")
            if candidate:
                return candidate
        return value or "BakeGroup"

    def _create_pair(self):
        """Mirror Maya's material decision, then resolve the chapter name."""
        snapshot = self.controller.snapshot()
        if snapshot is None or not snapshot.hp_object or not snapshot.lp_object:
            self._warning("Create Pair", "Pick HP and LP before creating a pair.")
            return
        material_slots = False
        material_summary = self.controller.lp_material_summary()
        if material_summary is not None and material_summary.count > 1:
            box = QtWidgets.QMessageBox(self)
            box.setWindowTitle(self._tr("Multiple LP Materials"))
            box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
            box.setText(self._tr("The LP has several materials ({}).").format(material_summary.count))
            box.setInformativeText(
                self._tr(
                    "Create it as a single chapter (split by material during Analyze HP), "
                    "or split it into several chapters now?"
                )
            )
            one_button = box.addButton(
                self._tr("Create as one chapter"), QtWidgets.QMessageBox.ButtonRole.AcceptRole
            )
            several_button = box.addButton(
                self._tr("Create several chapters"), QtWidgets.QMessageBox.ButtonRole.ActionRole
            )
            box.addButton(QtWidgets.QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(one_button)
            def material_decided(clicked):
                if clicked == several_button:
                    self._defer_dialog_action(self.controller.create_pairs_by_material)
                elif clicked == one_button:
                    self._defer_dialog_action(lambda: self._resolve_pair_name(snapshot, True))

            box.buttonClicked.connect(material_decided)
            self._open_nonblocking(box)
            return
        self._resolve_pair_name(snapshot, material_slots)

    def _resolve_pair_name(self, snapshot, material_slots=False):
        hp_base = self._pair_base_name(snapshot.hp_object, "HP")
        lp_base = self._pair_base_name(snapshot.lp_object, "LP")
        if hp_base == lp_base:
            self.controller.create_pair("CUSTOM", hp_base, material_slots=material_slots)
            return

        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Name Mismatch")
        dialog.setMinimumWidth(310)
        layout = self._compact(QtWidgets.QVBoxLayout(dialog), (10, 10, 10, 10), 6)
        layout.addWidget(QtWidgets.QLabel("Base names differ.\nChoose name for pair:"))
        group = QtWidgets.QButtonGroup(dialog)
        hp_radio = QtWidgets.QRadioButton("HP:  {}".format(hp_base)); hp_radio.setChecked(True)
        lp_radio = QtWidgets.QRadioButton("LP:  {}".format(lp_base))
        group.addButton(hp_radio); group.addButton(lp_radio)
        layout.addWidget(hp_radio); layout.addWidget(lp_radio)
        custom_row = self._compact(QtWidgets.QHBoxLayout(), (0, 0, 0, 0), 3)
        custom_radio = QtWidgets.QRadioButton("Custom:")
        custom_edit = QtWidgets.QLineEdit()
        group.addButton(custom_radio)
        custom_edit.textChanged.connect(lambda: custom_radio.setChecked(True))
        custom_row.addWidget(custom_radio); custom_row.addWidget(custom_edit, 1)
        layout.addLayout(custom_row)
        buttons = QtWidgets.QDialogButtonBox()
        apply_button = buttons.addButton("Apply", QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("Cancel", QtWidgets.QDialogButtonBox.ButtonRole.RejectRole)
        apply_button.clicked.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        def apply_choice():
            if hp_radio.isChecked():
                choice, value = "HP", ""
            elif lp_radio.isChecked():
                choice, value = "LP", ""
            else:
                value = custom_edit.text().strip()
                if not value:
                    self._warning("Create Pair", "Enter a custom chapter name.")
                    return
                choice = "CUSTOM"
            self.controller.create_pair(choice, value, material_slots=material_slots)

        dialog.accepted.connect(lambda: self._defer_dialog_action(apply_choice))
        self._open_nonblocking(dialog)

    def _build_tools(self, parent):
        group = QtWidgets.QGroupBox()
        row = self._compact(QtWidgets.QHBoxLayout(group), (3, 3, 3, 3), 2)
        self.combine_button = self._tool("Combine.png", "Combine", "combine", "COMBINE")
        self.separate_button = self._tool("Separate.png", "Separate", "separate", "SEPARATE")
        self.find_zbrush_button = self._tool(
            "Find_ZBRUSH.png", "Find ZBrush", "findZbrush", "FIND_ZBRUSH"
        )
        self.find_zbrush_button.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.find_zbrush_button.customContextMenuRequested.connect(self._show_find_zbrush_context_menu)
        self.check_mesh_button = self._tool(
            "Cheking_Icon.png", "Check Before Analyze", "check", "CHECK"
        )
        self.check_mesh_button.clicked.disconnect()
        self.check_mesh_button.clicked.connect(self._run_mesh_check)
        row.addWidget(self.combine_button)
        row.addWidget(self.separate_button)
        row.addWidget(self.find_zbrush_button)
        row.addWidget(self.check_mesh_button)
        checks = self._compact(QtWidgets.QVBoxLayout(), (0, 0, 0, 0), 0)
        self.color_hp = QtWidgets.QCheckBox("Color HP")
        self.keep_hp = QtWidgets.QCheckBox("Keep HP")
        self.color_hp.toggled.connect(partial(self._setting, "color_subgroups"))
        self.keep_hp.toggled.connect(partial(self._setting, "keep_hp_structure"))
        checks.addWidget(self.color_hp); checks.addWidget(self.keep_hp)
        row.addLayout(checks)
        parent.addWidget(group)

    def _show_find_zbrush_context_menu(self, point):
        menu = QtWidgets.QMenu(self)
        panel = QtWidgets.QWidget(menu)
        layout = self._compact(QtWidgets.QVBoxLayout(panel), (8, 6, 8, 6), 4)
        threshold = self.controller.zbrush_threshold()
        label = QtWidgets.QLabel(self._tr("Triangular faces: {}%").format(threshold))
        slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        slider.setRange(1, 100)
        slider.setValue(threshold)
        slider.setMinimumWidth(180)
        slider.valueChanged.connect(
            lambda value: label.setText(self._tr("Triangular faces: {}%").format(value))
        )
        slider.sliderReleased.connect(
            lambda: self._setting("zbrush_triangle_threshold", slider.value())
        )
        layout.addWidget(label)
        layout.addWidget(slider)
        widget_action = QtWidgets.QWidgetAction(menu)
        widget_action.setDefaultWidget(panel)
        menu.addAction(widget_action)
        menu.addSeparator()
        find_action = menu.addAction("Find ZBrush now")
        add_action = menu.addAction("Add selected meshes to ZBrush layer")
        select_action = menu.addAction("Select meshes added to ZBrush layer")
        find_action.triggered.connect(lambda: self.controller.action("FIND_ZBRUSH"))
        add_action.triggered.connect(lambda: self.controller.action("ZBRUSH_ADD_SELECTED"))
        select_action.triggered.connect(lambda: self.controller.action("ZBRUSH_SELECT_LAYER"))
        self._popup_menu(menu, self.find_zbrush_button.mapToGlobal(point))

    def _run_mesh_check(self, on_complete=None):
        if not self.controller.action("CHECK"):
            return
        report, issue_count = self.controller.mesh_check_result()
        payload = self.controller.mesh_check_payload()
        payload["report"] = report
        payload["issue_count"] = issue_count
        self._show_transform_check(payload, on_complete)

    def _finish_mesh_check(self, payload, on_complete=None):
        pair_id = str(payload.get("pair_id") or "")
        if pair_id:
            self._mesh_checked_pair_ids.add(pair_id)
        if on_complete:
            on_complete()
            return
        issue_count = int(payload.get("issue_count") or 0)
        text = (
            self._tr("Mesh Check completed. Review the actions in the Log.")
            if issue_count else self._tr("Mesh Check completed. No issues found.")
        )
        box = QtWidgets.QMessageBox(
            QtWidgets.QMessageBox.Icon.Information,
            self._tr("Mesh Check"), text, parent=self,
        )
        box.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Ok)
        self._open_nonblocking(box)

    def _show_transform_check(self, payload, on_complete=None):
        names = tuple(payload.get("transforms") or ())
        if not names:
            self._show_duplicate_check(payload, on_complete)
            return
        shown = list(names[:12])
        if len(names) > 12:
            shown.append("... +{}".format(len(names) - 12))
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(self._tr("Apply Transformations"))
        box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        box.setText(self._tr("Objects with unapplied Location, Rotation or Scale were found."))
        box.setInformativeText("{}\n\n{}".format(
            self._tr("Apply transformations now without changing visible mesh geometry, select the objects for inspection, or skip this warning."),
            "\n".join(shown),
        ))
        select_button = box.addButton(self._tr("Select"), QtWidgets.QMessageBox.ButtonRole.ActionRole)
        apply_button = box.addButton(self._tr("Apply Transforms"), QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        skip_button = box.addButton(self._tr("Skip"), QtWidgets.QMessageBox.ButtonRole.ActionRole)
        box.setDefaultButton(apply_button)

        def finished(_result):
            clicked = box.clickedButton()
            if clicked == select_button:
                self._defer_dialog_action(
                    lambda: self.controller.action("CHECK_SELECT_TRANSFORMS")
                )
            elif clicked == apply_button:
                def apply_and_continue():
                    if self.controller.action("CHECK_APPLY_TRANSFORMS"):
                        self._show_duplicate_check(payload, on_complete)
                self._defer_dialog_action(apply_and_continue)
            elif clicked == skip_button:
                self._defer_dialog_action(
                    lambda: self._show_duplicate_check(payload, on_complete)
                )
        box.finished.connect(finished)
        self._open_nonblocking(box)

    def _show_duplicate_check(self, payload, on_complete=None):
        groups = tuple(payload.get("duplicates") or ())
        if not groups:
            self._show_zbrush_check(payload, on_complete)
            return
        preview = []
        for index, names in enumerate(groups[:4], 1):
            shown = list(names[:6])
            if len(names) > 6:
                shown.append("... +{}".format(len(names) - 6))
            preview.append("{} {}: {}".format(self._tr("Group"), index, ", ".join(shown)))
        if len(groups) > 4:
            preview.append("... +{}".format(len(groups) - 4))
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(self._tr("Duplicate Meshes Found"))
        box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        box.setText(self._tr("Duplicate meshes were found before Analyze HP."))
        box.setInformativeText("{}\n\n{}".format(
            self._tr("Resolve duplicates before running Analyze HP. Select the found meshes, remove extra copies, or skip this warning and continue."),
            "\n".join(preview),
        ))
        select_button = box.addButton(self._tr("Select"), QtWidgets.QMessageBox.ButtonRole.ActionRole)
        remove_button = box.addButton(self._tr("Remove Extra Copies"), QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
        skip_button = box.addButton(self._tr("Skip"), QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(select_button)

        def finished(_result):
            clicked = box.clickedButton()
            if clicked == select_button:
                self._defer_dialog_action(
                    lambda: self.controller.action("CHECK_SELECT_DUPLICATES")
                )
            elif clicked == remove_button:
                def remove_and_continue():
                    self.controller.action("CHECK_REMOVE_DUPLICATES")
                    self._show_zbrush_check(payload, on_complete)
                self._defer_dialog_action(remove_and_continue)
            elif clicked == skip_button:
                self._defer_dialog_action(
                    lambda: self._show_zbrush_check(payload, on_complete)
                )
        box.finished.connect(finished)
        self._open_nonblocking(box)

    def _show_zbrush_check(self, payload, on_complete=None):
        names = tuple(payload.get("zbrush") or ())
        if not names:
            self._show_combined_check(payload, on_complete)
            return
        shown = list(names[:12])
        if len(names) > 12:
            shown.append("... +{}".format(len(names) - 12))
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(self._tr("Possible ZBrush Meshes"))
        box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        box.setText(self._tr("Possible ZBrush meshes were found before Analyze HP."))
        box.setInformativeText("{}\n\n{}".format(
            self._tr("Add these meshes to the BakeTools ZBrush layer, select them for inspection, or skip this warning and continue."),
            "\n".join(shown),
        ))
        select_button = box.addButton(self._tr("Select"), QtWidgets.QMessageBox.ButtonRole.ActionRole)
        add_button = box.addButton(self._tr("Add to ZBrush Layer"), QtWidgets.QMessageBox.ButtonRole.ActionRole)
        skip_button = box.addButton(self._tr("Skip"), QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(add_button)

        def finished(_result):
            clicked = box.clickedButton()
            if clicked == select_button:
                self._defer_dialog_action(
                    lambda: self.controller.action("CHECK_SELECT_ZBRUSH")
                )
            elif clicked == add_button:
                def add_and_continue():
                    self.controller.action("CHECK_ADD_ZBRUSH")
                    self._show_combined_check(payload, on_complete)
                self._defer_dialog_action(add_and_continue)
            elif clicked == skip_button:
                self._defer_dialog_action(
                    lambda: self._show_combined_check(payload, on_complete)
                )
        box.finished.connect(finished)
        self._open_nonblocking(box)

    def _show_combined_check(self, payload, on_complete=None):
        names = tuple(payload.get("combined") or ())
        pair_id = str(payload.get("pair_id") or "")
        if not names or pair_id in self._combined_check_skipped_pair_ids:
            self._finish_mesh_check(payload, on_complete)
            return
        shown = list(names[:12])
        if len(names) > 12:
            shown.append("... +{}".format(len(names) - 12))
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(self._tr("Combined Meshes Found"))
        box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        box.setText(self._tr("Combined mesh candidates were found before Analyze HP."))
        box.setInformativeText("{}\n\n{}".format(
            self._tr("These meshes may need to be separated before analysis. Select them, separate them now, or skip this warning and continue."),
            "\n".join(shown),
        ))
        select_button = box.addButton(self._tr("Select"), QtWidgets.QMessageBox.ButtonRole.ActionRole)
        separate_button = box.addButton(self._tr("Separate"), QtWidgets.QMessageBox.ButtonRole.ActionRole)
        skip_chapter = box.addButton(self._tr("Skip This Chapter"), QtWidgets.QMessageBox.ButtonRole.ActionRole)
        skip_button = box.addButton(self._tr("Skip"), QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(select_button)

        def finished(_result):
            clicked = box.clickedButton()
            if clicked == select_button:
                self._defer_dialog_action(
                    lambda: self.controller.action("CHECK_SELECT_COMBINED")
                )
            elif clicked == separate_button:
                def separate_and_finish():
                    self.controller.action("CHECK_SEPARATE_COMBINED")
                    self._finish_mesh_check(payload, on_complete)
                self._defer_dialog_action(separate_and_finish)
            elif clicked == skip_chapter:
                def skip_chapter_and_finish():
                    if pair_id:
                        self._combined_check_skipped_pair_ids.add(pair_id)
                    self._finish_mesh_check(payload, on_complete)
                self._defer_dialog_action(skip_chapter_and_finish)
            elif clicked == skip_button:
                self._defer_dialog_action(
                    lambda: self._finish_mesh_check(payload, on_complete)
                )
        box.finished.connect(finished)
        self._open_nonblocking(box)

    def _request_analyze_hp(self):
        pair_id = str(self._snapshot.active_pair_id if self._snapshot else "")
        if not pair_id or pair_id in self._mesh_checked_pair_ids:
            self.controller.analyze_hp()
            return
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(self._tr("Structure Not Checked"))
        box.setIcon(QtWidgets.QMessageBox.Icon.Question)
        box.setText(self._tr("Duplicate, ZBrush and combined-mesh checks have not been run for this chapter yet."))
        box.setInformativeText(self._tr("Run the check now, or continue Analyze HP without checking?"))
        check_button = box.addButton(self._tr("Check Now"), QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        continue_button = box.addButton(self._tr("Continue"), QtWidgets.QMessageBox.ButtonRole.ActionRole)
        box.addButton(QtWidgets.QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(check_button)

        def finished(_result):
            if box.clickedButton() == check_button:
                self._defer_dialog_action(
                    lambda: self._run_mesh_check(on_complete=self.controller.analyze_hp)
                )
            elif box.clickedButton() == continue_button:
                self._defer_dialog_action(self.controller.analyze_hp)
        box.finished.connect(finished)
        self._open_nonblocking(box)

    def _build_analysis(self, parent):
        actions = self._compact(QtWidgets.QHBoxLayout(), (0, 0, 0, 0), 2)
        actions.addWidget(self._button("Analyze HP", self._request_analyze_hp, "analyze"))
        actions.addWidget(self._button("Assign LP", lambda: self.controller.action("ASSIGN_LP"), "assign"))
        parent.addLayout(actions)

        header = QtWidgets.QWidget()
        header_row = self._compact(QtWidgets.QHBoxLayout(header), (0, 0, 0, 0), 2)
        self.algorithm_button = self._button("▶  Algorithm", self._toggle_algorithm)
        header_row.addWidget(self.algorithm_button)
        self.group_name = QtWidgets.QLineEdit(); self.group_name.setPlaceholderText("Group name")
        self.group_name.editingFinished.connect(
            lambda: self._setting("group_name", self.group_name.text().strip())
        )
        header_row.addWidget(self.group_name, 1)
        header_row.addWidget(self._button("Create Group", self._add_subgroup))
        parent.addWidget(header)

        self.algorithm_options = QtWidgets.QWidget()
        options = self._compact(QtWidgets.QVBoxLayout(self.algorithm_options), (2, 0, 2, 0), 1)
        options.addWidget(QtWidgets.QLabel("HP Strategy:"))
        self.strategy = QtWidgets.QComboBox()
        for label, value in (("Spatial Volume Match", "SPATIAL"), ("Vertex Proximity", "VERTEX"),
                             ("Topology Fingerprint", "TOPOLOGY")):
            self.strategy.addItem(label, value)
        self.strategy.currentIndexChanged.connect(
            lambda: self._setting("hp_strategy", self.strategy.currentData())
        )
        options.addWidget(self.strategy)
        options.addWidget(QtWidgets.QLabel("Optimization:"))
        self.optimization = QtWidgets.QComboBox()
        self.optimization.addItem("Optimal", "OPTIMAL"); self.optimization.addItem("Speed", "SPEED")
        self.optimization.currentIndexChanged.connect(
            lambda: self._setting("optimization", self.optimization.currentData())
        )
        options.addWidget(self.optimization)

        collision_row = self._compact(QtWidgets.QHBoxLayout(), (0, 0, 0, 0), 2)
        collision_row.addWidget(QtWidgets.QLabel("Collision (%):"))
        self.collision = QtWidgets.QSpinBox(); self.collision.setRange(0, 100); self.collision.setFixedWidth(48)
        self.collision.valueChanged.connect(partial(self._setting, "collision_pct"))
        collision_row.addWidget(self.collision); collision_row.addStretch(1)
        self.ignore_floaters = QtWidgets.QCheckBox("Ignore Floaters")
        self.ignore_floaters.toggled.connect(partial(self._setting, "ignore_floaters"))
        collision_row.addWidget(self.ignore_floaters)
        options.addLayout(collision_row)

        link_row = self._compact(QtWidgets.QHBoxLayout(), (0, 0, 0, 0), 2)
        link_row.addWidget(QtWidgets.QLabel("Link Vertex:"))
        self.link_vertex = QtWidgets.QSpinBox(); self.link_vertex.setRange(1, 500); self.link_vertex.setFixedWidth(45)
        self.link_vertex.valueChanged.connect(partial(self._setting, "link_vertex"))
        link_row.addWidget(self.link_vertex)
        link_row.addWidget(QtWidgets.QLabel("Link Dist (%):"))
        self.link_distance = QtWidgets.QDoubleSpinBox(); self.link_distance.setRange(.01, 25); self.link_distance.setDecimals(2); self.link_distance.setFixedWidth(56)
        self.link_distance.valueChanged.connect(partial(self._setting, "link_distance"))
        link_row.addWidget(self.link_distance); link_row.addStretch(1)
        self.adjacent_link = QtWidgets.QCheckBox(); self.adjacent_link.setToolTip("Link adjacent vertices")
        self.adjacent_link.toggled.connect(partial(self._setting, "adjacent_link"))
        link_row.addWidget(self.adjacent_link)
        options.addLayout(link_row)
        parent.addWidget(self.algorithm_options)

        visibility = self._compact(QtWidgets.QHBoxLayout(), (0, 0, 0, 0), 2)
        self.hp_visible = self._button("HP Visible", lambda: self._toggle_visibility("hp_visible"))
        self.lp_visible = self._button("LP Visible", lambda: self._toggle_visibility("lp_visible"))
        self.groups_visible = self._button("Groups Vis", lambda: self._toggle_visibility("groups_visible"))
        self.groups_visible.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.groups_visible.customContextMenuRequested.connect(self._show_groups_visibility_menu)
        visibility.addWidget(self.hp_visible); visibility.addWidget(self.lp_visible); visibility.addWidget(self.groups_visible)
        parent.addLayout(visibility)

    def _build_subgroups(self, parent):
        self.subgroups_title = QtWidgets.QLabel("SUBGROUPS")
        parent.addWidget(self.subgroups_title)
        self.subgroup_scroll = QtWidgets.QScrollArea()
        self.subgroup_scroll.setWidgetResizable(True)
        self.subgroup_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.subgroup_scroll.setMinimumHeight(220)
        self.subgroup_body = QtWidgets.QWidget()
        self.subgroup_body.setMouseTracking(True)
        self.subgroup_body.installEventFilter(self)
        self.subgroup_layout = self._compact(QtWidgets.QVBoxLayout(self.subgroup_body), (3, 3, 3, 3), 3)
        self.subgroup_scroll.setWidget(self.subgroup_body)
        self.subgroup_body.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.subgroup_body.customContextMenuRequested.connect(
            lambda pos: self._show_subgroup_menu(self.subgroup_body.mapToGlobal(pos), "")
        )
        parent.addWidget(self.subgroup_scroll, 1)

    def _build_bottom_actions(self, parent):
        self.normal_actions = QtWidgets.QWidget()
        row = self._compact(QtWidgets.QHBoxLayout(self.normal_actions), (0, 0, 0, 0), 2)
        self.find_button = self._button("Find Sim", lambda: self.controller.action("FIND_SIM"), "findSim")
        self.find_button.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.find_button.customContextMenuRequested.connect(self._toggle_find_mode)
        row.addWidget(self.find_button)
        self.export_settings_button = self._button(
            "Export Settings", lambda: self.controller.action("EXPORT_SETTINGS"), "exportSettings"
        )
        row.addWidget(self.export_settings_button)
        parent.addWidget(self.normal_actions)

        self.final_actions = QtWidgets.QWidget()
        final = self._compact(QtWidgets.QHBoxLayout(self.final_actions), (0, 0, 0, 0), 2)
        back = self._icon_button(
            "Back_button.png", "Back", lambda: self.controller.action("EXPORT_SETTINGS"), "#8b4b26", 29, 20
        )
        final.addWidget(back)
        self.smooth_button = self._button("Smooth View", lambda: self.controller.action("SMOOTH"), "smoothView")
        final.addWidget(self.smooth_button, 1)
        self.export_button = self._button("Export", self._choose_export_directory, "exportAction")
        final.addWidget(self.export_button, 1)
        parent.addWidget(self.final_actions)

    def _build_log(self, parent):
        box = QtWidgets.QGroupBox("Log")
        layout = self._compact(QtWidgets.QVBoxLayout(box), (3, 3, 3, 3), 1)
        self.log_output = QtWidgets.QTextEdit(); self.log_output.setReadOnly(True); self.log_output.setMaximumHeight(92)
        self.log_output.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.log_output.customContextMenuRequested.connect(self._show_log_menu)
        layout.addWidget(self.log_output)
        parent.addWidget(box)

    def _build_right_side(self, parent):
        self.right_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.right_splitter.setChildrenCollapsible(True)
        self.matcher_panel = self._build_matcher()
        self.toc_panel = self._build_toc()
        self.cage_panel = self._build_cage_panel()
        self.export_panel = self._build_export_panel()
        for widget in (self.matcher_panel, self.toc_panel, self.cage_panel, self.export_panel):
            self.right_splitter.addWidget(widget)
        parent.addWidget(self.right_splitter, 1)

        session = self._compact(QtWidgets.QHBoxLayout(), (0, 0, 0, 0), 2)
        session.addWidget(self._button("Save", lambda: self.controller.action("SAVE_SESSION")))
        self.language_button = self._button("Language")
        self.language_button.clicked.connect(self._show_language_menu)
        self.language_button.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.language_button.customContextMenuRequested.connect(lambda _pos: self._show_language_menu())
        session.addWidget(self.language_button)
        session.addWidget(self._button("About", self._show_about))
        parent.addLayout(session)
        self.right_splitter.setSizes([430, 380, 0, 0])

    def _build_matcher(self):
        group = QtWidgets.QGroupBox("HP -> LP Matcher")
        layout = self._compact(QtWidgets.QVBoxLayout(group), (3, 3, 3, 3), 2)
        form = self._compact(QtWidgets.QFormLayout(), (0, 0, 0, 0), 2)
        form.setHorizontalSpacing(3)
        self.matcher_hp = QtWidgets.QLineEdit("None"); self.matcher_hp.setReadOnly(True)
        self.matcher_lp = QtWidgets.QLineEdit("None"); self.matcher_lp.setReadOnly(True)
        self.matcher_tolerance = QtWidgets.QDoubleSpinBox(); self.matcher_tolerance.setRange(.01, 20); self.matcher_tolerance.setDecimals(2); self.matcher_tolerance.setSuffix(" %")
        self.matcher_tolerance.valueChanged.connect(partial(self._setting, "matcher_tolerance"))
        self.matcher_minimum = QtWidgets.QSpinBox(); self.matcher_minimum.setRange(1, 100)
        self.matcher_minimum.valueChanged.connect(partial(self._setting, "matcher_min_hp_lp"))
        self.matcher_mode = QtWidgets.QComboBox()
        for text, data in (("Balanced", "BALANCED"), ("Fast", "FAST"), ("Accurate", "ACCURATE")):
            self.matcher_mode.addItem(text, data)
        self.matcher_mode.currentIndexChanged.connect(lambda: self._setting("matcher_mode", self.matcher_mode.currentData()))
        hp_label = QtWidgets.QLabel("HP Root:"); hp_label.setStyleSheet("color:#d7d7d7")
        lp_label = QtWidgets.QLabel("LP Root:"); lp_label.setStyleSheet("color:#d7d7d7")
        form.addRow(hp_label, self.matcher_hp); form.addRow(lp_label, self.matcher_lp)
        form.addRow("Tolerance (%):", self.matcher_tolerance)
        form.addRow("Min HP/LP:", self.matcher_minimum)
        form.addRow("Match Mode:", self.matcher_mode)
        layout.addLayout(form)
        self.strict_geo = QtWidgets.QCheckBox("Strict Geo Check")
        self.strict_geo.setToolTip("Resolve overlapping HP/LP matches")
        self.strict_geo.toggled.connect(partial(self._setting, "strict_geo_check"))
        layout.addWidget(self.strict_geo)
        actions = self._compact(QtWidgets.QHBoxLayout(), (0, 0, 0, 0), 2)
        actions.addWidget(self._button("Find Groups", lambda: self.controller.action("FIND_GROUPS"), "findGroups"))
        actions.addWidget(self._button("Relocate", lambda: self.controller.action("RELOCATE"), "relocate"))
        layout.addLayout(actions)
        self.match_list = QtWidgets.QListWidget(); self.match_list.setObjectName("matcherList")
        self.match_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.match_list.itemSelectionChanged.connect(self._matcher_selection_changed)
        self.match_list.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.match_list.customContextMenuRequested.connect(self._show_matcher_menu)
        layout.addWidget(self.match_list, 1)
        links = self._compact(QtWidgets.QHBoxLayout(), (0, 0, 0, 0), 2)
        links.addWidget(self._button("Link", lambda: self._matcher_action("LINK"), "link"))
        links.addWidget(self._button("Unlink", lambda: self._matcher_action("UNLINK"), "unlink"))
        links.addWidget(self._button("New", lambda: self._matcher_action("NEW")))
        layout.addLayout(links)
        return group

    def _build_toc(self):
        group = QtWidgets.QGroupBox("TABLE OF CONTENTS")
        layout = self._compact(QtWidgets.QVBoxLayout(group), (3, 3, 3, 3), 1)
        self.toc_tree = QtWidgets.QTreeWidget(); self.toc_tree.setHeaderHidden(True); self.toc_tree.setColumnCount(2)
        self.toc_tree.setIndentation(12); self.toc_tree.setRootIsDecorated(True)
        header = self.toc_tree.header(); header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Fixed); header.resizeSection(1, 25)
        self.toc_tree.itemClicked.connect(self._toc_clicked)
        self.toc_tree.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.toc_tree.customContextMenuRequested.connect(self._show_toc_menu)
        layout.addWidget(self.toc_tree)
        return group

    def _cage_slider_row(self, action):
        widget = QtWidgets.QWidget()
        row = self._compact(QtWidgets.QHBoxLayout(widget), (0, 0, 0, 0), 2)
        spin = QtWidgets.QDoubleSpinBox(); spin.setRange(-30.0, 30.0); spin.setDecimals(4); spin.setSingleStep(0.01)
        spin.setSuffix(" %"); spin.setValue(0); spin.setFixedWidth(82)
        spin.setToolTip("Relative adjustment as a percentage of the complete chapter HP/LP bounding box")
        slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal); slider.setRange(-1000, 1000); slider.setValue(0)
        slider.setToolTip(spin.toolTip())
        slider.setStyleSheet(
            "QSlider::groove:horizontal{height:5px;background:#1e1e1e;border:1px solid #444;border-radius:2px;}"
            "QSlider::handle:horizontal{background:#b48ead;border:1px solid #5a3d6b;width:12px;margin:-5px 0;border-radius:6px;}"
        )
        def slider_percent(value):
            normalized = abs(float(value)) / 1000.0
            return (-1.0 if value < 0 else 1.0) * (normalized ** 3) * 30.0
        slider.valueChanged.connect(lambda value: spin.setValue(slider_percent(value)))
        def commit():
            if abs(spin.value()) > 1e-9:
                self._cage_action(action, spin.value())
            slider.blockSignals(True); spin.blockSignals(True)
            slider.setValue(0); spin.setValue(0)
            slider.blockSignals(False); spin.blockSignals(False)
        slider.sliderReleased.connect(commit); spin.editingFinished.connect(commit)
        row.addWidget(spin); row.addWidget(slider, 1)
        return widget

    def _cage_action(self, action, delta=None):
        payload = {"subgroups": sorted(self._final_selected_ids)}
        if delta is not None:
            payload["delta"] = float(delta)
        self.controller.action(action, json.dumps(payload, separators=(",", ":")))

    def _build_cage_panel(self):
        panel = QtWidgets.QWidget()
        layout = self._compact(QtWidgets.QVBoxLayout(panel), (3, 3, 3, 3), 3)
        header = QtWidgets.QLabel("CAGE SETTINGS"); header.setObjectName("cageHeader"); header.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)
        buttons = self._compact(QtWidgets.QHBoxLayout(), (0, 0, 0, 0), 4)
        buttons.addWidget(self._icon_button("Cage_Create.png", "Create Cage", lambda: self._cage_action("CAGE_CREATE"), size=38, icon_size=27))
        buttons.addWidget(self._icon_button("Cage_Brush.png", "Sculpt Cage (Inflate)", lambda: self._cage_action("CAGE_SCULPT"), size=38, icon_size=27))
        self.cage_display = self._icon_button("Cage_Wireframe.png", "Display: Wireframe", self._toggle_cage_display, size=38, icon_size=27)
        buttons.addWidget(self.cage_display); buttons.addStretch(1); layout.addLayout(buttons)
        layout.addWidget(QtWidgets.QLabel("Expansion (inflate)")); layout.addWidget(self._cage_slider_row("CAGE_EXPANSION"))
        utility = self._compact(QtWidgets.QHBoxLayout(), (0, 0, 0, 0), 4)
        utility.addWidget(self._icon_button("Cage_Find.png", "Find intersections", lambda: self._cage_action("CAGE_FIND"), size=38, icon_size=27))
        self.cage_export_button = self._icon_button(
            "Cage_Export.png", "Export Cage", lambda: self._cage_action("CAGE_EXPORT"),
            size=38, icon_size=27,
        )
        utility.addWidget(self.cage_export_button)
        utility.addWidget(self._icon_button("Cage_Delete.png", "Delete Cage", lambda: self._cage_action("CAGE_DELETE"), "#6d3838", 38, 27))
        utility.addStretch(1); layout.addLayout(utility)
        layout.addWidget(QtWidgets.QLabel("Normal move")); layout.addWidget(self._cage_slider_row("CAGE_NORMAL"))
        layout.addStretch(1)
        self.cage_status = QtWidgets.QLabel("No cage yet - press Create Cage."); self.cage_status.setWordWrap(True); self.cage_status.setStyleSheet("color:#d9b3ff")
        layout.addWidget(self.cage_status)
        return panel

    def _build_export_panel(self):
        panel = QtWidgets.QWidget()
        layout = self._compact(QtWidgets.QVBoxLayout(panel), (3, 3, 3, 3), 2)
        header = QtWidgets.QLabel("EXPORT"); header.setObjectName("exportHeader"); header.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)
        self.export_scope = QtWidgets.QComboBox(); self.export_scope.setObjectName("scope")
        for text, data in (("Active Chapter", "CHAPTER"), ("Active Book", "BOOK"), ("All Books", "ALL")):
            self.export_scope.addItem(text, data)
        self.export_scope.currentIndexChanged.connect(self._export_scope_changed)
        layout.addWidget(self.export_scope)
        layout.addWidget(QtWidgets.QLabel("Include"))
        include = self._compact(QtWidgets.QHBoxLayout(), (0, 0, 0, 0), 3)
        self.export_hp = QtWidgets.QCheckBox("HP")
        self.export_lp = QtWidgets.QCheckBox("LP")
        self.export_lp_triangle = QtWidgets.QCheckBox("LP Triangle")
        self.export_cage = QtWidgets.QCheckBox("Cage")
        self.export_hp.toggled.connect(partial(self._setting, "export_include_hp"))
        self.export_lp.toggled.connect(partial(self._setting, "export_include_lp"))
        self.export_lp_triangle.toggled.connect(partial(self._setting, "export_lp_triangulate"))
        self.export_cage.toggled.connect(partial(self._setting, "export_include_cage"))
        self.export_lp_triangle.setToolTip("Temporarily triangulate LP meshes during FBX export")
        self.export_cage.setToolTip("Include a separately exported Cage FBX when a Cage exists")
        include.addWidget(self.export_hp); include.addWidget(self.export_lp); include.addWidget(self.export_lp_triangle); include.addStretch(1)
        layout.addLayout(include); layout.addWidget(QtWidgets.QLabel("Files"))
        files = self._compact(QtWidgets.QHBoxLayout(), (0, 0, 0, 0), 3)
        self.export_separate = QtWidgets.QRadioButton("Separate"); self.export_one = QtWidgets.QRadioButton("HP+LP one file")
        self.export_separate.toggled.connect(lambda checked: checked and self._setting("export_files", "SEPARATE"))
        self.export_one.toggled.connect(lambda checked: checked and self._setting("export_files", "ONE"))
        files.addWidget(self.export_separate); files.addWidget(self.export_one); files.addStretch(1); layout.addLayout(files)
        flags = self._compact(QtWidgets.QHBoxLayout(), (0, 0, 0, 0), 3)
        self.export_by_material = QtWidgets.QCheckBox("By material")
        self.export_lp_one = QtWidgets.QCheckBox("LP in one file")
        self.export_by_material.toggled.connect(partial(self._setting, "export_by_material"))
        self.export_lp_one.toggled.connect(partial(self._setting, "export_lp_one_file"))
        flags.addWidget(self.export_cage); flags.addWidget(self.export_by_material); flags.addWidget(self.export_lp_one); flags.addStretch(1); layout.addLayout(flags)
        path_row = self._compact(QtWidgets.QHBoxLayout(), (0, 0, 0, 0), 2)
        self.export_path = QtWidgets.QLineEdit()
        self.export_path.setClearButtonEnabled(True)
        self.export_path.setPlaceholderText("Select Export Directory")
        self.export_path.editingFinished.connect(self._store_export_path)
        path_row.addWidget(self.export_path, 1)
        path_row.addWidget(self._button("…", self._choose_export_directory))
        layout.addLayout(path_row)
        self.export_status = QtWidgets.QLabel(""); self.export_status.setWordWrap(True); self.export_status.setStyleSheet("color:#a9d6b4")
        layout.addWidget(self.export_status); layout.addStretch(1)
        return panel

    def _toggle_algorithm(self):
        current = bool(self._snapshot and self._snapshot.show_algorithm)
        self._setting("show_algorithm", not current)

    def _add_subgroup(self):
        name = self.group_name.text().strip()
        try:
            bpy.ops.bake_tools.add_subgroup("EXEC_DEFAULT", name=name)
        finally:
            self.request_refresh()

    def _toggle_visibility(self, setting):
        target = setting
        if (setting == "lp_visible" and self._snapshot and self._snapshot.final_view
                and self._snapshot.active_has_cage):
            target = "cage_visible"
        current = bool(getattr(self._snapshot, target, True)) if self._snapshot else True
        self._setting(target, not current)

    def _toggle_cage_display(self):
        self._setting("cage_wire", not bool(self._snapshot and self._snapshot.cage_wire))

    def _toggle_find_mode(self, _pos=None):
        target = "ALL" if self._snapshot and self._snapshot.find_mode == "SIM" else "SIM"
        self._setting("find_mode", target)

    def _show_pick_menu(self, role, button, pos):
        menu = QtWidgets.QMenu(button)
        menu.addAction("Pick Active Object", lambda: self.controller.pick(role, "OBJECT"))
        menu.addAction("Pick Active Collection", lambda: self.controller.pick(role, "COLLECTION"))
        self._popup_menu(menu, button.mapToGlobal(pos))

    def _export_scope_changed(self):
        data = self.export_scope.currentData()
        if data:
            self._setting("export_scope", data)

    def _choose_export_directory(self):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(self._tr("Select Export Directory"))
        dialog.setMinimumWidth(520)
        layout = self._compact(QtWidgets.QVBoxLayout(dialog), (10, 10, 10, 10), 6)
        layout.addWidget(QtWidgets.QLabel(self._tr("Paste or enter the full folder path:")))
        path_row = self._compact(QtWidgets.QHBoxLayout(), (0, 0, 0, 0), 4)
        path_edit = QtWidgets.QLineEdit()
        current = self.export_path.text().strip() or (
            self._snapshot.export_directory if self._snapshot else ""
        )
        path_edit.setText(current)
        path_edit.setPlaceholderText(r"C:\\Project\\Exports")
        path_edit.setClearButtonEnabled(True)
        path_row.addWidget(path_edit, 1)
        paste_button = self._button("Paste")
        paste_button.clicked.connect(
            lambda: path_edit.setText(QtWidgets.QApplication.clipboard().text().strip().strip('"'))
        )
        browse_button = self._button("Browse…")
        path_row.addWidget(paste_button); path_row.addWidget(browse_button)
        layout.addLayout(path_row)
        error = QtWidgets.QLabel("")
        error.setStyleSheet("color:#ff796d")
        layout.addWidget(error)
        actions = self._compact(QtWidgets.QHBoxLayout(), (0, 2, 0, 0), 4)
        actions.addStretch(1)
        cancel = self._button("Cancel", dialog.reject)
        export = self._button("Export")
        export.setObjectName("exportSettings")
        actions.addWidget(cancel); actions.addWidget(export)
        layout.addLayout(actions)

        def browse():
            browser = QtWidgets.QFileDialog(dialog, self._tr("Select Export Directory"))
            browser.setFileMode(QtWidgets.QFileDialog.FileMode.Directory)
            browser.setOption(QtWidgets.QFileDialog.Option.ShowDirsOnly, True)
            if path_edit.text().strip():
                browser.setDirectory(path_edit.text().strip())
            browser.fileSelected.connect(path_edit.setText)
            self._open_nonblocking(browser)

        def submit():
            directory = path_edit.text().strip().strip('"')
            if not directory:
                error.setText(self._tr("The export directory path is empty."))
                path_edit.setFocus()
                return
            dialog.accept()
            self._defer_dialog_action(lambda value=directory: self._export_to_directory(value))

        browse_button.clicked.connect(browse)
        export.clicked.connect(submit)
        path_edit.returnPressed.connect(submit)
        self._open_nonblocking(dialog)
        path_edit.setFocus(); path_edit.selectAll()

    def _store_export_path(self):
        directory = self.export_path.text().strip().strip('"')
        if self.export_path.text() != directory:
            self.export_path.setText(directory)
        current = self._snapshot.export_directory if self._snapshot else ""
        if directory != current:
            self.controller.set_setting("export_directory", directory)

    def _export_to_directory(self, directory):
        directory = str(directory or "").strip()
        if not directory:
            return
        self.controller.set_setting("export_directory", directory)
        try:
            from .export_service import build_export_plan
            state = self.controller.store.settings()
            pair = next((item for item in state.pairs if item.item_id == state.active_pair_id), None)
            plan = build_export_plan(state, pair, directory)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            self._warning("Export", str(exc))
            return
        if plan.warnings:
            message = self._tr("Warnings:") + "\n" + "\n".join("• " + warning for warning in plan.warnings)
            self._confirm("Export preflight", message, lambda: self.controller.action("EXPORT"))
        else:
            self.controller.action("EXPORT")

    def _set_combo(self, widget, value):
        index = widget.findData(value)
        if index >= 0 and index != widget.currentIndex():
            blocker = QtCore.QSignalBlocker(widget)
            widget.setCurrentIndex(index)
            del blocker

    def _set_checked(self, widget, value):
        if widget.isChecked() != bool(value):
            blocker = QtCore.QSignalBlocker(widget)
            widget.setChecked(bool(value))
            del blocker

    def refresh_from_store(self, force=False):
        snapshot = self.controller.snapshot()
        self._dirty = False
        if snapshot is None or (not force and snapshot == self._snapshot):
            return
        self._snapshot = snapshot
        active = snapshot.active_chapter
        self.hp_edit.setText(snapshot.hp_object)
        self.lp_edit.setText(snapshot.lp_object)
        self.hp_edit.setToolTip("{} root: {}".format(snapshot.hp_root_kind.title() or "Unassigned", snapshot.hp_object or "None"))
        material_summary = self.controller.lp_material_summary()
        material_count = material_summary.count if material_summary is not None else 0
        self.lp_edit.setToolTip("{} root: {} | Used materials: {}".format(
            snapshot.lp_root_kind.title() or "Unassigned", snapshot.lp_object or "None", material_count
        ))
        self.matcher_hp.setText(active.hp_object if active else snapshot.hp_object or "None")
        self.matcher_lp.setText(active.lp_object if active else snapshot.lp_object or "None")
        if not self.group_name.hasFocus():
            self.group_name.setText(snapshot.group_name)
        self._set_checked(self.color_hp, snapshot.color_subgroups); self._set_checked(self.keep_hp, snapshot.keep_hp_structure)
        self._set_combo(self.strategy, snapshot.hp_strategy); self._set_combo(self.optimization, snapshot.optimization)
        for widget, value in ((self.collision, snapshot.collision_pct), (self.link_vertex, snapshot.link_vertex),
                              (self.link_distance, snapshot.link_distance), (self.matcher_tolerance, snapshot.matcher_tolerance),
                              (self.matcher_minimum, snapshot.matcher_min_hp_lp)):
            blocker = QtCore.QSignalBlocker(widget); widget.setValue(value); del blocker
        self._set_checked(self.ignore_floaters, snapshot.ignore_floaters); self._set_checked(self.adjacent_link, snapshot.adjacent_link)
        self._set_combo(self.matcher_mode, snapshot.matcher_mode); self._set_checked(self.strict_geo, snapshot.strict_geo_check)
        matcher_sig = (snapshot.active_pair_id, active.matcher_clusters if active else ())
        if force or self._section_signatures.get("matcher") != matcher_sig:
            self._section_signatures["matcher"] = matcher_sig
            self._render_matcher(active)
        self.algorithm_options.setVisible(snapshot.show_algorithm)
        algorithm_key = "▼  Algorithm" if snapshot.show_algorithm else "▶  Algorithm"
        self.algorithm_button.setProperty("bt_i18n_key", algorithm_key)
        self.algorithm_button.setText(self._tr(algorithm_key))
        self._set_visibility_button(self.hp_visible, "HP", snapshot.hp_visible, colored=True)
        if snapshot.final_view and snapshot.active_has_cage:
            self._set_visibility_button(
                self.lp_visible, "Cage", snapshot.cage_visible,
                visible_key="Cage Vis", hidden_key="Cage Hid", colored=True,
            )
        else:
            self._set_visibility_button(self.lp_visible, "LP", snapshot.lp_visible, colored=True)
        self._set_visibility_button(self.groups_visible, "Groups", snapshot.groups_visible)
        find_key = "Find All" if snapshot.find_mode == "ALL" else "Find Sim"
        self.find_button.setProperty("bt_i18n_key", find_key)
        self.find_button.setText(self._tr(find_key))
        self.find_button.setObjectName("findAll" if snapshot.find_mode == "ALL" else "findSim")
        self.find_button.style().unpolish(self.find_button); self.find_button.style().polish(self.find_button)
        self._refresh_export_controls(snapshot)
        self._refresh_mode(snapshot.final_view, snapshot.cage_wire)
        subgroup_sig = (snapshot.active_pair_id, snapshot.active_subgroup, snapshot.final_view,
                        active.subgroups if active else ())
        if force or self._section_signatures.get("subgroups") != subgroup_sig:
            self._section_signatures["subgroups"] = subgroup_sig
            self._render_subgroups(active, snapshot.active_subgroup, snapshot.final_view)
        toc_sig = (snapshot.active_pair_id, tuple((c.item_id, c.name, c.book, c.visible, c.expanded) for c in snapshot.chapters))
        if force or self._section_signatures.get("toc") != toc_sig:
            self._section_signatures["toc"] = toc_sig
            self._render_toc(snapshot)
        localized_log = i18n.runtime_block(snapshot.log_text, snapshot.language)
        if self.log_output.toPlainText() != localized_log:
            self.log_output.setPlainText(localized_log)
            self.log_output.verticalScrollBar().setValue(self.log_output.verticalScrollBar().maximum())
        self._apply_localization()
        self._localized_language = snapshot.language

    def _set_visibility_button(
        self, button, label, visible, *, visible_key=None, hidden_key=None, colored=False,
    ):
        key = (visible_key if visible else hidden_key) or "{} {}".format(
            label, "Visible" if visible else "Hidden"
        )
        button.setProperty("bt_i18n_key", key)
        button.setText(self._tr(key))
        if colored:
            button.setStyleSheet(
                "background-color:#4a5d4a;" if visible else "background-color:#8c4242;"
            )
        else:
            button.setStyleSheet("" if visible else "color:#888;background:#303030")

    def _refresh_mode(self, final_view, cage_wire):
        self.normal_actions.setVisible(not final_view); self.final_actions.setVisible(final_view)
        self.subgroups_title.setVisible(not final_view)
        self.matcher_panel.setVisible(not final_view)
        self.cage_panel.setVisible(final_view); self.export_panel.setVisible(final_view)
        self.cage_display.setIcon(QtGui.QIcon(_asset_path("Cage_Solid_Gray.png" if cage_wire else "Cage_Wireframe.png")))
        cage_tip = "Display: Solid gray" if cage_wire else "Display: Wireframe"
        self.cage_display.setProperty("bt_i18n_tooltip_key", cage_tip)
        self.cage_display.setToolTip(self._tr(cage_tip))
        mode_sig = (final_view,)
        if self._section_signatures.get("mode") != mode_sig:
            self._section_signatures["mode"] = mode_sig
            QtCore.QTimer.singleShot(0, lambda: self.right_splitter.setSizes(
                [0, 360, 260, 190] if final_view else [430, 380, 0, 0]
            ))

    def _refresh_export_controls(self, snapshot):
        self._set_combo(self.export_scope, snapshot.export_scope)
        self._set_checked(self.export_hp, snapshot.export_include_hp); self._set_checked(self.export_lp, snapshot.export_include_lp)
        self._set_checked(self.export_lp_triangle, snapshot.export_lp_triangulate)
        self._set_checked(self.export_cage, snapshot.export_include_cage and snapshot.export_has_cage)
        self.export_cage.setEnabled(snapshot.export_has_cage)
        self.cage_export_button.setEnabled(snapshot.active_has_cage)
        self._set_checked(self.export_separate, snapshot.export_files == "SEPARATE")
        self._set_checked(self.export_one, snapshot.export_files == "ONE")
        is_chapter = snapshot.export_scope == "CHAPTER"
        self.export_by_material.setEnabled(not is_chapter); self.export_lp_one.setEnabled(not is_chapter)
        self._set_checked(self.export_by_material, snapshot.export_by_material and not is_chapter)
        self._set_checked(self.export_lp_one, snapshot.export_lp_one_file and not is_chapter)
        if not self.export_path.hasFocus() and self.export_path.text() != snapshot.export_directory:
            self.export_path.setText(snapshot.export_directory)
        self.export_status.setText(
            snapshot.export_status or (self._tr("Select a chapter in the TOC.") if not snapshot.active_chapter else "")
        )
        self.cage_status.setText(snapshot.cage_status)
        smooth_key = "Smooth ON" if snapshot.preview_smoothing else "Smooth View"
        self.smooth_button.setProperty("bt_i18n_key", smooth_key)
        self.smooth_button.setText(self._tr(smooth_key))

    def _render_subgroups(self, chapter, active_index, final_view):
        _clear_layout(self.subgroup_layout)
        self._final_rows = {}
        if chapter is None:
            self._final_selected_ids.clear()
            self.subgroup_layout.addStretch(1); return
        valid_ids = {subgroup.item_id for subgroup in chapter.subgroups}
        self._final_selected_ids.intersection_update(valid_ids)
        if not chapter.subgroups:
            label = QtWidgets.QLabel("No subgroups yet\nAnalyze HP or create a group")
            label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter); label.setStyleSheet("color:#888;padding:8px")
            self.subgroup_layout.addWidget(label); self.subgroup_layout.addStretch(1); return
        for index, subgroup in enumerate(chapter.subgroups):
            row = self._make_final_row(subgroup, index == active_index) if final_view else self._make_subgroup_row(subgroup, index == active_index)
            row.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
            if final_view:
                row.customContextMenuRequested.connect(
                    lambda pos, widget=row, item_id=subgroup.item_id: self._show_final_subgroup_menu(widget.mapToGlobal(pos), item_id)
                )
            else:
                row.customContextMenuRequested.connect(
                    lambda pos, widget=row, item_id=subgroup.item_id: self._show_subgroup_menu(widget.mapToGlobal(pos), item_id)
                )
            self.subgroup_layout.addWidget(row)
        self.subgroup_layout.addStretch(1)

    def _make_subgroup_row(self, subgroup, active):
        row = QtWidgets.QFrame(); row.setObjectName("subgroupColorRow")
        layout = self._compact(QtWidgets.QHBoxLayout(row), (2, 2, 2, 2), 3)
        eye = self._icon_button(
            "open_eye.png" if subgroup.visible else "close_eye.png", "Toggle visibility",
            lambda: self.controller.subgroup_action("TOGGLE_VISIBLE", subgroup.item_id),
            "#405941" if subgroup.visible else "#703d3d"
        )
        eye.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        eye.customContextMenuRequested.connect(
            lambda _pos: self.controller.subgroup_action("ISOLATE_VISIBLE", subgroup.item_id)
        )
        layout.addWidget(eye)
        name = SubgroupNameButton(subgroup.name)
        name.setObjectName("activeRowName" if active else "rowName")
        name.clicked.connect(lambda: self.controller.subgroup_action("ACTIVATE", subgroup.item_id))
        name.doubleClicked.connect(lambda: self.controller.subgroup_action("SELECT_MESHES", subgroup.item_id))
        name.setStyleSheet(self._subgroup_name_style(subgroup, active)); name.setMinimumWidth(48)
        name.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed); layout.addWidget(name, 1)
        add = self._icon_button("Plus.png", "Add selected mesh", lambda: self.controller.subgroup_action("ADD_SELECTED", subgroup.item_id), "#315c3a")
        layout.addWidget(add)
        lock = self._icon_button(
            "Look_Icon_Button.png" if subgroup.locked else "Unlook_Icon_Button.png",
            "Unlock subgroup" if subgroup.locked else "Lock subgroup",
            lambda: self.controller.subgroup_action("TOGGLE_LOCK", subgroup.item_id),
            "#8c4242" if subgroup.locked else "#428c42"
        )
        lock.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        lock.customContextMenuRequested.connect(
            lambda _pos: self.controller.subgroup_action("ISOLATE_LOCK", subgroup.item_id)
        )
        layout.addWidget(lock)
        layout.addWidget(self._icon_button("Delete.png", "X", lambda: self._confirm_delete_subgroup(subgroup.item_id, subgroup.name), "#8c4242"))
        row.setStyleSheet(self._subgroup_row_style(subgroup, active))
        row.setFixedHeight(34); return row

    def _make_final_row(self, subgroup, active):
        selected = subgroup.item_id in self._final_selected_ids
        row = QtWidgets.QFrame(); row.setObjectName("subgroupColorRow")
        layout = self._compact(QtWidgets.QHBoxLayout(row), (2, 2, 2, 2), 2)
        eye = self._icon_button(
            "open_eye.png" if subgroup.visible else "close_eye.png", "Toggle visibility",
            lambda: self.controller.subgroup_action("TOGGLE_VISIBLE", subgroup.item_id),
            "#405941" if subgroup.visible else "#703d3d", 26, 16
        )
        eye.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        eye.customContextMenuRequested.connect(
            lambda _pos: self.controller.subgroup_action("ISOLATE_VISIBLE", subgroup.item_id)
        )
        layout.addWidget(eye)
        name = SubgroupNameButton(subgroup.name)
        name.setObjectName("activeRowName" if active or selected else "rowName")
        name.clicked.connect(lambda: self._select_final_subgroup(subgroup.item_id))
        name.doubleClicked.connect(lambda: self.controller.subgroup_action("SELECT_MESHES", subgroup.item_id))
        name.setStyleSheet(self._subgroup_name_style(subgroup, active or selected)); name.setMinimumWidth(58)
        name.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed); layout.addWidget(name, 1)
        smooth = QtWidgets.QComboBox(); smooth.setFixedWidth(75)
        for level in range(4): smooth.addItem("Smooth {}".format(level), level)
        smooth.setCurrentIndex(subgroup.smooth_level)
        smooth.currentIndexChanged.connect(
            lambda level, item_id=subgroup.item_id: self._batch_final_smooth("SET", item_id, level)
        )
        layout.addWidget(smooth)
        layout.addWidget(self._icon_button(
            "Plus.png", "+",
            lambda: self._batch_final_smooth("UP", subgroup.item_id),
            "#315c3a", 26, 16,
        ))
        layout.addWidget(self._icon_button(
            "Minus.png", "-",
            lambda: self._batch_final_smooth("DOWN", subgroup.item_id),
            "#765239", 26, 16,
        ))
        row.setStyleSheet(self._subgroup_row_style(subgroup, active or selected))
        self._final_rows[subgroup.item_id] = (row, name, subgroup, active)
        row.setFixedHeight(32); return row

    def _batch_final_smooth(self, mode, clicked_id, level=0):
        selected = set(self._final_selected_ids)
        targets = selected if clicked_id in selected and selected else {clicked_id}
        payload = {
            "subgroups": sorted(targets),
            "mode": str(mode),
            "level": int(level),
        }
        self.controller.action(
            "SUBGROUP_SMOOTH_BATCH", json.dumps(payload, separators=(",", ":"))
        )

    def _select_final_subgroup(self, subgroup_id):
        modifiers = QtWidgets.QApplication.keyboardModifiers()
        additive = bool(modifiers & (
            QtCore.Qt.KeyboardModifier.ControlModifier | QtCore.Qt.KeyboardModifier.ShiftModifier
        ))
        if additive:
            if subgroup_id in self._final_selected_ids:
                self._final_selected_ids.remove(subgroup_id)
            else:
                self._final_selected_ids.add(subgroup_id)
        else:
            self._final_selected_ids = {subgroup_id}
        self._refresh_final_selection_visuals()
        self.controller.subgroup_action("ACTIVATE", subgroup_id)

    def _refresh_final_selection_visuals(self):
        for item_id, (row, name, subgroup, active) in self._final_rows.items():
            selected = item_id in self._final_selected_ids
            name.setObjectName("activeRowName" if active or selected else "rowName")
            name.setStyleSheet(self._subgroup_name_style(subgroup, active or selected))
            row.setStyleSheet(self._subgroup_row_style(subgroup, active or selected))
            name.style().unpolish(name); name.style().polish(name)

    def _select_all_final_subgroups(self):
        self._final_selected_ids = set(self._final_rows)
        self._refresh_final_selection_visuals()

    def _select_final_rows_in_rect(self, rect, additive=False):
        selected = {
            item_id for item_id, (row, _name, _subgroup, _active) in self._final_rows.items()
            if rect.intersects(row.geometry())
        }
        self._final_selected_ids = (self._final_selected_ids | selected) if additive else selected
        self._refresh_final_selection_visuals()

    def eventFilter(self, watched, event):
        if watched is self.subgroup_body and self._snapshot and self._snapshot.final_view:
            event_type = event.type()
            if event_type == QtCore.QEvent.Type.MouseButtonPress and event.button() == QtCore.Qt.MouseButton.LeftButton:
                self._final_rubber_origin = event.position().toPoint()
                if self._final_rubber is None:
                    self._final_rubber = QtWidgets.QRubberBand(
                        QtWidgets.QRubberBand.Shape.Rectangle, self.subgroup_body
                    )
                self._final_rubber.setGeometry(QtCore.QRect(self._final_rubber_origin, QtCore.QSize()))
                self._final_rubber.show()
                return True
            if event_type == QtCore.QEvent.Type.MouseMove and self._final_rubber_origin is not None:
                self._final_rubber.setGeometry(
                    QtCore.QRect(self._final_rubber_origin, event.position().toPoint()).normalized()
                )
                return True
            if event_type == QtCore.QEvent.Type.MouseButtonRelease and self._final_rubber_origin is not None:
                rect = QtCore.QRect(self._final_rubber_origin, event.position().toPoint()).normalized()
                self._final_rubber_origin = None
                if self._final_rubber is not None:
                    self._final_rubber.hide()
                additive = bool(event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier)
                if rect.width() <= 3 and rect.height() <= 3:
                    if not additive:
                        self._final_selected_ids.clear()
                        self._refresh_final_selection_visuals()
                else:
                    self._select_final_rows_in_rect(rect, additive)
                return True
        return super().eventFilter(watched, event)

    def _subgroup_rgb(self, subgroup):
        if subgroup.custom_color is not None:
            return tuple(float(value) for value in subgroup.custom_color)
        index = max(0, int(subgroup.color_index))
        base = PALETTE[index % len(PALETTE)]
        shade_pass = index // len(PALETTE)
        shade = 1.0 if shade_pass == 0 else max(0.38, 0.62 ** shade_pass)
        return tuple(channel * shade for channel in base)

    @staticmethod
    def _rgba(rgb, alpha):
        return "rgba({},{},{},{})".format(
            *(max(0, min(255, int(value * 255))) for value in rgb), int(alpha)
        )

    def _subgroup_row_style(self, subgroup, active):
        if not self._snapshot or not self._snapshot.color_subgroups:
            if active:
                return "QFrame#subgroupColorRow{background:rgba(88,129,96,48);border:2px solid #f1f5f2;border-radius:4px}"
            return "QFrame#subgroupColorRow{background:transparent;border:1px solid #333;border-radius:4px}"
        rgb = self._subgroup_rgb(subgroup)
        border = "#f1f5f2" if active else self._rgba(rgb, 255)
        return "QFrame#subgroupColorRow{{background:{};border:{}px solid {};border-radius:4px}}".format(
            self._rgba(rgb, 66 if active else 30), 2 if active else 1, border
        )

    def _subgroup_name_style(self, subgroup, active):
        common = "text-align:left;padding-left:5px;border-radius:3px;"
        if not self._snapshot or not self._snapshot.color_subgroups:
            return common + ("background:#3a5375;font-weight:bold;" if active else "background:transparent;")
        rgb = self._subgroup_rgb(subgroup)
        return common + "background:{};border:1px solid {};font-weight:{};".format(
            self._rgba(rgb, 68 if active else 28), self._rgba(rgb, 180 if active else 95),
            "bold" if active else "normal",
        )

    def _render_matcher(self, chapter):
        chapter_id = chapter.item_id if chapter else ""
        selected_ids = set(self._matcher_selected_by_chapter.get(chapter_id, ()))
        blocker = QtCore.QSignalBlocker(self.match_list)
        self.match_list.clear()
        valid_ids = set()
        if chapter:
            for cluster in chapter.matcher_clusters:
                text = cluster.title or "{} [HP: {}]".format(cluster.name, len(cluster.hp_members))
                if cluster.linked and not text.startswith("[Linked]"):
                    text = "[Linked] " + text
                item = QtWidgets.QListWidgetItem(text)
                item.setData(QtCore.Qt.ItemDataRole.UserRole, cluster.item_id)
                item.setData(QtCore.Qt.ItemDataRole.UserRole + 1, bool(cluster.linked))
                valid_ids.add(cluster.item_id)
                if cluster.linked:
                    item.setBackground(QtGui.QColor("#2e7d32"))
                    item.setForeground(QtGui.QColor("white"))
                self.match_list.addItem(item)
                if cluster.item_id in selected_ids:
                    item.setSelected(True)
        selected_ids.intersection_update(valid_ids)
        if chapter_id:
            self._matcher_selected_by_chapter[chapter_id] = selected_ids
        del blocker
        self._refresh_matcher_selection_visuals()

    def _selected_matcher_ids(self):
        return tuple(
            str(item.data(QtCore.Qt.ItemDataRole.UserRole) or "")
            for item in self.match_list.selectedItems()
            if item.data(QtCore.Qt.ItemDataRole.UserRole)
        )

    def _matcher_action(self, action):
        ids = self._selected_matcher_ids()
        if action == "UNLINK" and not ids and self._snapshot and self._snapshot.active_chapter:
            linked = tuple(cluster for cluster in self._snapshot.active_chapter.matcher_clusters if cluster.linked)
            if linked:
                menu = QtWidgets.QMenu(self)
                for cluster in linked:
                    menu.addAction(cluster.name, lambda checked=False, item_id=cluster.item_id: self.controller.action("UNLINK", item_id))
                self._popup_menu(menu, self.match_list.mapToGlobal(QtCore.QPoint(0, self.match_list.height())))
                return None
        return self.controller.action(action, "|".join(ids))

    def _matcher_selection_changed(self):
        ids = self._selected_matcher_ids()
        chapter_id = (
            self._snapshot.active_chapter.item_id
            if self._snapshot and self._snapshot.active_chapter else ""
        )
        if chapter_id:
            self._matcher_selected_by_chapter[chapter_id] = set(ids)
        self._refresh_matcher_selection_visuals()
        if ids:
            self.controller.action("GT_MATCH", "|".join(ids))

    def _refresh_matcher_selection_visuals(self):
        """Keep Maya-blue selection visible after Blender takes native focus."""
        selected = set(self._selected_matcher_ids())
        for row in range(self.match_list.count()):
            item = self.match_list.item(row)
            item_id = str(item.data(QtCore.Qt.ItemDataRole.UserRole) or "")
            linked = bool(item.data(QtCore.Qt.ItemDataRole.UserRole + 1))
            if item_id in selected:
                item.setBackground(QtGui.QColor("#3a5375"))
                item.setForeground(QtGui.QColor("#ffffff"))
            elif linked:
                item.setBackground(QtGui.QColor("#2e7d32"))
                item.setForeground(QtGui.QColor("#ffffff"))
            else:
                item.setBackground(QtGui.QBrush())
                item.setForeground(QtGui.QBrush())

    def _render_toc(self, snapshot):
        self.toc_tree.blockSignals(True); self.toc_tree.clear()
        books = OrderedDict()
        for chapter in snapshot.chapters:
            if chapter.book: books.setdefault(chapter.book, []).append(chapter)
            else: self._add_toc_chapter(None, chapter, snapshot.active_pair_id)
        for book_name, chapters in books.items():
            book_item = QtWidgets.QTreeWidgetItem([book_name, ""]); book_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, "book")
            book_item.setData(0, QtCore.Qt.ItemDataRole.UserRole + 1, book_name); self.toc_tree.addTopLevelItem(book_item)
            for chapter in chapters: self._add_toc_chapter(book_item, chapter, snapshot.active_pair_id)
            book_item.setExpanded(True); self._set_toc_eye(book_item, all(c.visible for c in chapters), "book", book_name)
        self.toc_tree.blockSignals(False)

    def _add_toc_chapter(self, parent, chapter, active_id):
        item = QtWidgets.QTreeWidgetItem([chapter.name, ""]); item.setData(0, QtCore.Qt.ItemDataRole.UserRole, "chapter")
        item.setData(0, QtCore.Qt.ItemDataRole.UserRole + 1, chapter.item_id)
        (parent.addChild(item) if parent else self.toc_tree.addTopLevelItem(item))
        font = item.font(0); font.setBold(chapter.item_id == active_id); item.setFont(0, font)
        item.setForeground(0, QtGui.QColor("#ffffff" if chapter.item_id == active_id else ("#cfcfcf" if chapter.visible else "#777777")))
        self._set_toc_eye(item, chapter.visible, "chapter", chapter.item_id)

    def _set_toc_eye(self, item, visible, kind, value):
        eye = QtWidgets.QToolButton(); eye.setFixedSize(22, 22); eye.setAutoRaise(True)
        eye.setIcon(QtGui.QIcon(_asset_path("open_eye.png" if visible else "close_eye.png"))); eye.setIconSize(QtCore.QSize(15, 15))
        eye.setStyleSheet("QToolButton{background:transparent;border:none;padding:1px}")
        if kind == "chapter": eye.clicked.connect(lambda: self.controller.pair_action("TOGGLE_VISIBLE", value))
        else: eye.clicked.connect(lambda: self._toggle_book_visibility(value, visible))
        self.toc_tree.setItemWidget(item, 1, eye)

    def _toggle_book_visibility(self, book_name, currently_visible):
        if not self._snapshot: return
        target = not currently_visible
        for chapter in self._snapshot.chapters:
            if chapter.book == book_name and chapter.visible != target:
                self.controller.pair_action("TOGGLE_VISIBLE", chapter.item_id)

    def _toc_clicked(self, item, column):
        if column == 0 and item.data(0, QtCore.Qt.ItemDataRole.UserRole) == "chapter":
            self.controller.pair_action("ACTIVATE", item.data(0, QtCore.Qt.ItemDataRole.UserRole + 1))

    def _selected_subgroup_id(self):
        if not self._snapshot or not self._snapshot.active_chapter: return ""
        groups = self._snapshot.active_chapter.subgroups
        return groups[self._snapshot.active_subgroup].item_id if 0 <= self._snapshot.active_subgroup < len(groups) else ""

    def _add_subgroup_menu_action(self, menu):
        menu.addAction("Add subgroup", self._add_subgroup)

    def _show_subgroup_menu(self, global_pos, subgroup_id):
        subgroup_id = subgroup_id or self._selected_subgroup_id()
        menu = QtWidgets.QMenu(self); self._add_subgroup_menu_action(menu)
        if subgroup_id:
            menu.addAction("Rename subgroup", lambda: self._rename_subgroup(subgroup_id))
            menu.addAction("Select subgroup meshes", lambda: self.controller.subgroup_action("SELECT_MESHES", subgroup_id))
            menu.addAction("Select Color", lambda: self._choose_subgroup_color(subgroup_id)); menu.addSeparator()
            menu.addAction("Optimize subgroups (delete empty)", lambda: self.controller.action("OPTIMIZE_GROUPS"))
            menu.addAction("Group search by mesh", lambda: self.controller.action("FIND_SUBGROUP")); menu.addSeparator()
            menu.addAction("Delete", lambda: self._confirm_delete_subgroup(subgroup_id, "subgroup"))
        self._popup_menu(menu, global_pos)

    def _show_final_subgroup_menu(self, global_pos, subgroup_id):
        menu = QtWidgets.QMenu(self)
        menu.addAction("Rename Final Group", lambda: self._rename_subgroup(subgroup_id))
        menu.addAction("Select Color", lambda: self._choose_subgroup_color(subgroup_id))
        menu.addAction("Select All Subgroups", self._select_all_final_subgroups)
        self._popup_menu(menu, global_pos)

    def _rename_subgroup(self, subgroup_id):
        subgroup = next((s for s in (self._snapshot.active_chapter.subgroups if self._snapshot and self._snapshot.active_chapter else ()) if s.item_id == subgroup_id), None)
        if not subgroup: return
        self._request_text(
            "Rename Subgroup", "Name:", subgroup.name,
            lambda value: value and self.controller.subgroup_action("RENAME", subgroup_id, value),
        )

    def _confirm_delete_subgroup(self, subgroup_id, name):
        self._confirm(
            "Delete Subgroup", "Delete '{}'?".format(name),
            lambda: self.controller.subgroup_action("DELETE", subgroup_id),
        )

    def _choose_subgroup_color(self, subgroup_id):
        subgroup = next((item for item in (self._snapshot.active_chapter.subgroups if self._snapshot and self._snapshot.active_chapter else ()) if item.item_id == subgroup_id), None)
        initial = QtGui.QColor.fromRgbF(*(self._subgroup_rgb(subgroup) if subgroup else (0.26, 0.52, 0.32)))
        dialog = QtWidgets.QColorDialog(initial, self)
        dialog.setWindowTitle("Select Color")
        dialog.colorSelected.connect(
            lambda color: color.isValid() and self.controller.subgroup_action(
                "SET_COLOR", subgroup_id,
                "{},{},{}".format(color.redF(), color.greenF(), color.blueF()),
            )
        )
        self._open_nonblocking(dialog)

    def _show_groups_visibility_menu(self, pos):
        menu = QtWidgets.QMenu(self)
        menu.addAction("Show all groups", lambda: self._setting("groups_visible", True))
        menu.addAction("Hide all groups", lambda: self._setting("groups_visible", False))
        menu.addAction("Isolate active group", lambda: self.controller.action("CHECK"))
        self._popup_menu(menu, self.groups_visible.mapToGlobal(pos))

    def _show_toc_menu(self, pos):
        item = self.toc_tree.itemAt(pos)
        if item is None: return
        kind = item.data(0, QtCore.Qt.ItemDataRole.UserRole); value = item.data(0, QtCore.Qt.ItemDataRole.UserRole + 1)
        menu = QtWidgets.QMenu(self)
        if kind == "chapter":
            menu.addAction("Select Meshes", lambda: self.controller.pair_action("SELECT_MESHES", value))
            menu.addAction("Move Mesh to", lambda: self.controller.action("ASSIGN_LP"))
            menu.addAction("Rename Chapter", lambda: self._rename_chapter(value, item.text(0)))
            menu.addAction("Find Lost (beta)", lambda: self.controller.action("FIND_GROUPS"))
            menu.addAction("Split by materials", lambda: self.controller.action("SEPARATE")); menu.addSeparator()
            menu.addAction("Group into Book", lambda: self._group_into_book(value))
            books = sorted({c.book for c in self._snapshot.chapters if c.book}) if self._snapshot else []
            if books:
                submenu = menu.addMenu("Add to existing book")
                for book in books: submenu.addAction(book, lambda checked=False, name=book: self.controller.pair_action("SET_BOOK", value, name))
            menu.addAction("Extract from the book", lambda: self.controller.pair_action("EXTRACT_BOOK", value)); menu.addSeparator()
            menu.addAction("Delete", lambda: self._confirm_delete_chapter(value, item.text(0)))
        else:
            menu.addAction("Select all chapters", lambda: self._select_book(value))
            menu.addAction("Rename Book", lambda: self._rename_book(value))
            menu.addAction("Ungroup Book", lambda: self._ungroup_book(value))
        self._popup_menu(menu, self.toc_tree.viewport().mapToGlobal(pos))

    def _rename_chapter(self, pair_id, old):
        self._request_text(
            "Rename Chapter", "Name:", old,
            lambda value: value and self.controller.pair_action("RENAME", pair_id, value),
        )

    def _group_into_book(self, pair_id):
        self._request_text(
            "Group into Book", "Book name:", "",
            lambda value: value and self.controller.pair_action("SET_BOOK", pair_id, value),
        )

    def _rename_book(self, old):
        def apply(value):
            if value and self._snapshot:
                for chapter in self._snapshot.chapters:
                    if chapter.book == old:
                        self.controller.pair_action("SET_BOOK", chapter.item_id, value)

        self._request_text("Rename Book", "Book name:", old, apply)

    def _ungroup_book(self, book):
        if self._snapshot:
            for chapter in self._snapshot.chapters:
                if chapter.book == book: self.controller.pair_action("EXTRACT_BOOK", chapter.item_id)

    def _select_book(self, book):
        if self._snapshot:
            for chapter in self._snapshot.chapters:
                if chapter.book == book: self.controller.pair_action("SELECT_MESHES", chapter.item_id)

    def _confirm_delete_chapter(self, pair_id, name):
        self._confirm(
            "Delete Chapter", "Delete '{}'?".format(name),
            lambda: self.controller.pair_action("DELETE", pair_id),
        )

    def _show_matcher_menu(self, pos):
        menu = QtWidgets.QMenu(self)
        menu.addAction("Link", lambda: self._matcher_action("LINK")); menu.addAction("Unlink", lambda: self._matcher_action("UNLINK"))
        menu.addAction("New", lambda: self._matcher_action("NEW")); self._popup_menu(menu, self.match_list.viewport().mapToGlobal(pos))

    def _show_language_menu(self):
        menu = QtWidgets.QMenu(self)
        current = self._snapshot.language if self._snapshot else "EN"
        for language in i18n.available_languages():
            code, label = language["state_code"], language["label"]
            action = menu.addAction(label); action.setCheckable(True); action.setChecked(code == current)
            action.triggered.connect(lambda checked=False, value=code: self._setting("language", value))
        self._popup_menu(menu, self.language_button.mapToGlobal(QtCore.QPoint(0, self.language_button.height())))

    def _show_log_menu(self, pos):
        menu = self.log_output.createStandardContextMenu(); menu.addSeparator()
        menu.addAction("Save Support Package", self._save_support_package)
        menu.addAction("Save Debug Log", self._save_debug_log); menu.addSeparator()
        menu.addAction("Clear Log", lambda: self.controller.action("CLEAR_LOG"))
        self._popup_menu(menu, self.log_output.mapToGlobal(pos))

    def _save_diagnostics_dialog(self, kind):
        support = kind == "SUPPORT"
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default = ("BakeGroups_Support_{}.zip" if support else "BakeGroups_Debug_{}.txt").format(stamp)
        title = "Save Support Package" if support else "Save Debug Log"
        dialog = QtWidgets.QFileDialog(self, self._tr(title))
        dialog.setAcceptMode(QtWidgets.QFileDialog.AcceptMode.AcceptSave)
        dialog.setFileMode(QtWidgets.QFileDialog.FileMode.AnyFile)
        dialog.setDefaultSuffix("zip" if support else "txt")
        dialog.setNameFilters((("ZIP Archives (*.zip)", "All Files (*)") if support else
                               ("Text Files (*.txt)", "All Files (*)")))
        desktop = Path.home() / "Desktop"
        if desktop.is_dir():
            dialog.setDirectory(str(desktop))
        dialog.selectFile(default)

        def save(path):
            if not self.controller.save_diagnostics(kind, path):
                self._warning(title, "Failed to save diagnostics")

        dialog.fileSelected.connect(save)
        self._open_nonblocking(dialog)

    def _save_debug_log(self):
        self._save_diagnostics_dialog("DEBUG")

    def _save_support_package(self):
        self._save_diagnostics_dialog("SUPPORT")

    def _show_about(self):
        from .about_update import AboutUpdateDialog

        self._open_nonblocking(AboutUpdateDialog(self, self._tr))


def notify_store_changed():
    window = _qt_window_manager.primary_window()
    if window is not None:
        window.request_refresh()


def _temporary_popup_requires_suppression(window):
    """Ignore only Blender temporary UI that predates the explicit Open.

    The native launcher button can leave its tooltip/menu region alive until a
    later redraw. Suppressing the freshly opened manager for that stale region
    made an empty scene look object-dependent. Once Blender reports no temporary
    region, the latch clears and every subsequent popup is protected normally.
    """
    active = blender_temporary_ui_active()
    if bool(getattr(window, "_bt_ignore_preexisting_temporary", False)):
        if not active:
            window._bt_ignore_preexisting_temporary = False
        return False
    return active


def _pump_events():
    app = QtWidgets.QApplication.instance()
    window = _qt_window_manager.primary_window()
    if app is None or window is None or not window.isVisible():
        _qt_window_manager.mark_pump_stopped(_pump_events)
        return None
    # Never re-enter Qt's dispatcher from Blender while Windows owns the
    # thread's modal title-bar move/resize loop. The previous recurring Qt
    # timer did exactly that and could stall the desktop until Alt+Tab.
    if blender_window_in_move_size(window):
        return 0.10
    # Capture Blender's selection before a queued Qt click changes OS focus.
    capture_context()
    window._sync_workspace_visibility()
    in_host_workspace = bool(getattr(window, "_bt_in_host_workspace", True))
    if in_host_workspace:
        window._sync_sidebar_tab_visibility()
    # Most Blender popovers are regions inside the main native window, not
    # separate HWNDs.  Yield the pseudo-dock until that temporary region closes
    # so Options/Transform/Outliner menus remain visible and clickable above it.
    # Input polling must happen before Qt processes queued messages; otherwise
    # GetAsyncKeyState's one-shot click bit can be consumed before the guard sees
    # the event that opened the popup.
    temporary_popup = _temporary_popup_requires_suppression(window)
    header_guard = blender_header_popup_guard_active(window)
    # Adopt the pre-dispatch Win32 guard first. Otherwise clearing an older
    # reason could briefly restore the manager before this reason is registered.
    sync_native_popup_guard(window, temporary_popup)
    set_qt_window_suppressed(window, "blender_temporary_region", temporary_popup)
    set_qt_window_suppressed(
        window, "blender_header_interaction", header_guard
    )
    sync_blender_transient_z_order(window)
    app.processEvents()
    if in_host_workspace:
        window._sync_pseudo_dock()
    window.refresh_from_store()
    return 0.10


def show_manager(context=None):
    """Show one persistent, non-activating manager over Blender's Sidebar content."""
    # Preserve the exact VIEW_3D/UI that launched us before importing/showing Qt
    # can move OS focus away from Blender.  This does not depend on an active
    # Object, so an empty Scene Collection has the same dock target as a filled
    # scene.
    capture_context(context)
    _qt_window_manager.ensure_application(QtWidgets, ["BakeToolsBlender"])
    window, _created = _qt_window_manager.get_or_create_primary(BakeToolsWindow)
    set_listener(window._on_progress_event)
    # The manager is reusable.  Never inherit transparent/click-through state
    # from a previous Sidebar tab, Blender popover or explicit Close.
    reset_qt_window_suppression(window)
    window._bind_host_workspace()
    window._bt_in_host_workspace = True
    # Snapshot tooltip/menu state before the Qt HWND can cover the launcher. A
    # pre-existing region belongs to the explicit Open action and must not make
    # the first manager frame transparent.
    window._bt_ignore_preexisting_temporary = bool(blender_temporary_ui_active())
    window._last_applied_dock_rect = None
    window.request_refresh(); window.refresh_from_store(force=True)
    window.show()
    # Qt creates/recreates the final native HWND while showing. Re-embed it
    # after every show so it can never return as an independent desktop popup.
    attach_qt_window_to_blender(window)
    start_native_popup_guard(window)
    window._sync_workspace_visibility()
    window._sync_sidebar_tab_visibility()
    window._sync_pseudo_dock()
    window.raise_()
    _qt_window_manager.start_pump(_pump_events, first_interval=0.02)
    QtCore.QTimer.singleShot(250, lambda: _offer_telemetry_consent(window))
    return window


def _offer_telemetry_consent(window):
    """Ask once, non-modally, before the Blender port sends any telemetry."""
    from . import telemetry

    if telemetry.consent_value() is not None:
        if telemetry.consent_value() is True:
            try:
                state = getattr(bpy.context.scene, "bake_tools_settings", None)
                telemetry.report_async(getattr(state, "language", ""))
            except (AttributeError, RuntimeError):
                pass
        return
    existing = getattr(window, "_telemetry_consent_box", None)
    if existing is not None and existing.isVisible():
        return
    box = QtWidgets.QMessageBox(window)
    box.setWindowTitle(window._tr("Anonymous usage statistics"))
    box.setIcon(QtWidgets.QMessageBox.Icon.Information)
    box.setText(window._tr("Help improve Bake Groups Tool?"))
    box.setInformativeText(window._tr(
        "Allow one installation/update event per version. The event contains a random client ID, "
        "product and host versions, interface language, and platform. It never contains scene data, "
        "names, or file paths. You can change this later in About."
    ))
    box.setStandardButtons(
        QtWidgets.QMessageBox.StandardButton.Yes |
        QtWidgets.QMessageBox.StandardButton.No
    )
    box.setDefaultButton(QtWidgets.QMessageBox.StandardButton.No)
    box.setWindowModality(QtCore.Qt.WindowModality.NonModal)
    window._telemetry_consent_box = box

    def finished(result):
        enabled = result == int(QtWidgets.QMessageBox.StandardButton.Yes)
        telemetry.set_consent(enabled)
        if enabled:
            try:
                state = getattr(bpy.context.scene, "bake_tools_settings", None)
                telemetry.report_async(getattr(state, "language", ""))
            except (AttributeError, RuntimeError):
                pass
        window._telemetry_consent_box = None

    box.finished.connect(finished)
    box.open()


def hide_manager():
    """Hide the manager without destroying its UI/state and stop the Qt pump."""
    window = _qt_window_manager.primary_window()
    _qt_window_manager.stop_pump()
    if window is not None:
        stop_native_popup_guard(window)
        reset_qt_window_suppression(window)
        window._bt_ignore_preexisting_temporary = False
        window.hide()
    set_listener(None)


def shutdown_manager():
    window = _qt_window_manager.primary_window()
    _qt_window_manager.stop_pump()
    if window is not None:
        stop_native_popup_guard(window)
        reset_qt_window_suppression(window)
        window._bt_ignore_preexisting_temporary = False
        window.close(); window.deleteLater()
        _qt_window_manager.unregister(window)
    set_listener(None)
    _qt_window_manager.shutdown()
