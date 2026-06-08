# -*- coding: utf-8 -*-
from __future__ import print_function, division, absolute_import

import maya.cmds as cmds
import os
import bg_localization as bg_l10n
import re

try:
    from PySide6 import QtWidgets, QtCore, QtGui
    QAction = QtGui.QAction
except ImportError:
    from PySide2 import QtWidgets, QtCore, QtGui
    QAction = QtWidgets.QAction

import bg_core

# ==========================================
# ICON UTILITY
# ==========================================
CURRENT_DIR = os.path.dirname(__file__)
ICONS_DIR = os.path.join(CURRENT_DIR, "icons")

def get_icon(icon_name):
    """Returns QIcon by filename from the icons folder."""
    icon_path = os.path.join(ICONS_DIR, icon_name)
    if os.path.exists(icon_path):
        return QtGui.QIcon(icon_path)
    return QtGui.QIcon()


# ==========================================
# COLLAPSIBLE SECTION
# ==========================================
class CollapsibleSection(QtWidgets.QWidget):
    """Collapsible widget acting like a QGroupBox with a clickable header."""
    def __init__(self, title="", parent=None):
        super(CollapsibleSection, self).__init__(parent)
        
        # Header button
        self.toggle_button = QtWidgets.QToolButton(text=title, checkable=True, checked=False)
        self.toggle_button.setStyleSheet("""
            QToolButton { border: none; font-weight: bold; background-color: #3b3b3b; padding: 4px; border-radius: 3px;}
            QToolButton:hover { background-color: #4b4b4b; }
        """)
        self.toggle_button.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.toggle_button.setArrowType(QtCore.Qt.RightArrow)
        self.toggle_button.toggled.connect(self.set_expanded)
        
        # Content container
        self.content_area = QtWidgets.QWidget()
        self.content_layout = QtWidgets.QVBoxLayout(self.content_area)
        self.content_layout.setContentsMargins(4, 4, 4, 4)
        
        # Header layout
        self.header_widget = QtWidgets.QWidget()
        self.header_layout = QtWidgets.QHBoxLayout(self.header_widget)
        self.header_layout.setContentsMargins(0, 0, 0, 0)
        self.header_layout.setSpacing(6)
        self.header_layout.addWidget(self.toggle_button)

        # Main Layout
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addWidget(self.header_widget)
        main_layout.addWidget(self.content_area)
        self.content_area.setVisible(False)
        
    def set_expanded(self, checked):
        self.toggle_button.setArrowType(QtCore.Qt.RightArrow if not checked else QtCore.Qt.DownArrow)
        self.content_area.setVisible(checked)

    def on_pressed(self):
        self.set_expanded(self.toggle_button.isChecked())
        
    def addWidget(self, widget):
        self.content_layout.addWidget(widget)
        
    def addLayout(self, layout):
        self.content_layout.addLayout(layout)

    def addHeaderWidget(self, widget, stretch=0):
        self.header_layout.addWidget(widget, stretch)


# ==========================================
# FINAL MESH LINE EDIT
# ==========================================
class FinalMeshLineEdit(QtWidgets.QLineEdit):
    def __init__(self, mesh_path, parent=None):
        super(FinalMeshLineEdit, self).__init__(parent)
        self.mesh_path = mesh_path
        self.setText(mesh_path.split('|')[-1])
        self.setStyleSheet("""
            QLineEdit {
                background: #2b2b2b; 
                border: 1px solid #444444; 
                border-radius: 4px;
                color: #dddddd;
                padding: 4px;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
            QLineEdit:focus {
                border: 1px solid #5c85d6;
                background: #333333;
            }
        """)
        self.editingFinished.connect(self.on_rename)

    def mouseDoubleClickEvent(self, event):
        if cmds.objExists(self.mesh_path):
            cmds.select(self.mesh_path, replace=True)
        super(FinalMeshLineEdit, self).mouseDoubleClickEvent(event)

    def on_rename(self):
        new_name = self.text().strip().replace(".", "_")
        old_name = self.mesh_path.split('|')[-1]
        
        if new_name and new_name != old_name and cmds.objExists(self.mesh_path):
            try:
                new_node = cmds.rename(self.mesh_path, new_name)
                parts = self.mesh_path.split('|')
                parts[-1] = new_node
                self.mesh_path = '|'.join(parts)
                self.clearFocus()
            except Exception as e:
                cmds.warning("Rename failed: {}".format(e))
                self.setText(old_name)


# ==========================================
# SUBGROUP BUTTON
# ==========================================
class SubgroupButton(QtWidgets.QPushButton):
    doubleClicked = QtCore.Signal()
    rightClicked = QtCore.Signal()
    
    def __init__(self, *args, **kwargs):
        super(SubgroupButton, self).__init__(*args, **kwargs)
        self.setMinimumWidth(30)
        sp = self.sizePolicy()
        sp.setHorizontalPolicy(QtWidgets.QSizePolicy.Ignored)
        self.setSizePolicy(sp)
        
    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.RightButton:
            self.rightClicked.emit()
        super(SubgroupButton, self).mousePressEvent(event)
        
    def mouseDoubleClickEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.doubleClicked.emit()
        super(SubgroupButton, self).mouseDoubleClickEvent(event)


# ==========================================
# RESOLVE NAME DIALOG
# ==========================================
class ResolveNameDialog(QtWidgets.QDialog):
    def __init__(self, hp_base, lp_base, parent=None):
        super(ResolveNameDialog, self).__init__(parent)
        self.setWindowTitle("Name Mismatch")
        self.setStyleSheet("""
            QDialog { background-color: #242424; color: #d0d0d0; font-family: 'Segoe UI'; }
            QPushButton { background-color: #333333; border: 1px solid #444; border-radius: 4px; padding: 6px; color: #ddd; }
            QPushButton:hover { background-color: #444; }
            QLineEdit { background: #2b2b2b; border: 1px solid #444; border-radius: 4px; color: #ddd; padding: 4px; }
        """)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel("Base names differ.\nChoose name for pair:"))
        self.btn_group = QtWidgets.QButtonGroup(self)
        self.rb_hp = QtWidgets.QRadioButton("HP:  {}".format(hp_base))
        self.rb_hp.setChecked(True)
        self.btn_group.addButton(self.rb_hp)
        layout.addWidget(self.rb_hp)
        self.rb_lp = QtWidgets.QRadioButton("LP:  {}".format(lp_base))
        self.btn_group.addButton(self.rb_lp)
        layout.addWidget(self.rb_lp)
        custom_layout = QtWidgets.QHBoxLayout()
        self.rb_custom = QtWidgets.QRadioButton("Custom:")
        self.btn_group.addButton(self.rb_custom)
        self.input_custom = QtWidgets.QLineEdit()
        self.input_custom.textChanged.connect(lambda: self.rb_custom.setChecked(True))
        custom_layout.addWidget(self.rb_custom)
        custom_layout.addWidget(self.input_custom)
        layout.addLayout(custom_layout)
        btn_layout = QtWidgets.QHBoxLayout()
        btn_ok = QtWidgets.QPushButton("Apply")
        btn_ok.clicked.connect(self.accept)
        btn_layout.addWidget(btn_ok)
        btn_cancel = QtWidgets.QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)
        bg_l10n.localize_widget_tree(self)

    def get_chosen_name(self):
        if self.rb_hp.isChecked():
            return self.rb_hp.text().replace("HP:  ", "").strip()
        if self.rb_lp.isChecked():
            return self.rb_lp.text().replace("LP:  ", "").strip()
        return self.input_custom.text().strip() if self.rb_custom.isChecked() else "BakeGroup"
