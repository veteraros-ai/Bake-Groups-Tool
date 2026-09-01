# -*- coding: utf-8 -*-
"""Maya standalone smoke test for the All Books orchestration path."""
from __future__ import print_function

import contextlib
import os
import re
import shutil
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6 import QtWidgets
except ImportError:
    from PySide2 import QtWidgets

APP = QtWidgets.QApplication.instance()
if not isinstance(APP, QtWidgets.QApplication):
    APP = QtWidgets.QApplication([])

import maya.standalone

maya.standalone.initialize(name="python")

import maya.cmds as cmds


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_DIR = os.environ.get("BAKE_GROUPS_TEST_SCRIPT_DIR") or os.path.join(ROOT, "Bake_Groups")
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import bg_core
import bg_final_export
import bg_mixins


class _Toggle(object):
    def __init__(self, value):
        self.value = value

    def isChecked(self):
        return bool(self.value)


class _Index(object):
    def __init__(self, value):
        self.value = value

    def currentIndex(self):
        return int(self.value)


class ExportHarness(QtWidgets.QWidget, bg_mixins.ExportMixin):
    def __init__(self, pairs):
        QtWidgets.QWidget.__init__(self)
        self.root_pairs = pairs
        self.active_root_id = pairs[0]['id']
        self.core = bg_core.MayaCore()
        self.exp_inc_hp = _Toggle(True)
        self.exp_inc_lp = _Toggle(True)
        self.exp_inc_cage = _Toggle(False)
        self.exp_files_one = _Toggle(False)
        self.exp_bymat = _Toggle(False)
        self.exp_lp_one = _Toggle(False)
        self.exp_scope = _Index(2)
        self.exp_status = QtWidgets.QLabel()

    def save_final_smooth_states(self):
        return None

    def disable_preview_smoothing_for_export(self):
        return None

    @contextlib.contextmanager
    def suspend_subgroup_color_preview(self):
        yield

    @contextlib.contextmanager
    def suspend_isolation(self):
        yield

    def log(self, _message, _color=None):
        return None

    def lp_material_records_for_node(self, node, include_faces=False):
        records = []
        for shading_group, faces in bg_final_export.FinalExportProcessor._get_mesh_materials_and_faces(node).items():
            materials = cmds.listConnections(
                shading_group + ".surfaceShader",
                source=True, destination=False) or []
            records.append({
                'key': materials[0] if materials else shading_group,
                'material': materials[0] if materials else None,
                'faces': faces if include_faces else [],
            })
        return records

    @staticmethod
    def _clean_material_book_name(name):
        return str(name or "").split('|')[-1].split(':')[-1]

    @staticmethod
    def _sanitize_export_name(name):
        return re.sub(r'[^A-Za-z0-9_\-]+', '_', str(name)).strip('_') or "Export"


def _chapter(base, book, index):
    hp_root = cmds.group(empty=True, name=base + "_HP")
    hp_group = cmds.group(empty=True, name="Group_HP", parent=hp_root)
    hp_nodes = []
    for part in (1, 2, 3):
        hp = cmds.polyCube(
            name="raw_hp_{}_{}".format(index, part),
            constructionHistory=False)[0]
        hp_nodes.append(cmds.parent(hp, hp_group)[0])
    zbrush_layer = cmds.createDisplayLayer(
        name="Book_ZBrush_{}_{}".format(index, book), empty=True)
    cmds.editDisplayLayerMembers(zbrush_layer, hp_nodes[-1], noRecurse=True)

    lp_root = cmds.group(empty=True, name=base + "_LP")
    lp_group = cmds.group(empty=True, name="Group_LP", parent=lp_root)
    lp = cmds.polyPlane(name="raw_lp_{}".format(index), constructionHistory=False)[0]
    cmds.parent(lp, lp_group)
    material = book
    shading_group = book + "SG"
    if not cmds.objExists(material):
        material = cmds.shadingNode("lambert", asShader=True, name=material)
        shading_group = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True,
            name=shading_group)
        cmds.connectAttr(material + ".outColor", shading_group + ".surfaceShader", force=True)
    cmds.sets(lp, edit=True, forceElement=shading_group)

    return {
        'id': "pair-{}".format(index),
        'base': base,
        'book': book,
        'hp_uuid': cmds.ls(hp_root, uuid=True)[0],
        'lp_uuid': cmds.ls(lp_root, uuid=True)[0],
        'final_smooth_states': {'Group': 0},
    }


def run():
    cmds.file(new=True, force=True)
    pairs = [
        _chapter("Chapter_01", "Book_A", 1),
        _chapter("Chapter_02", "Book_A", 2),
        _chapter("Chapter_03", "Book_B", 3),
    ]
    harness = ExportHarness(pairs)
    export_dir = tempfile.mkdtemp(prefix="bake_groups_batch_test_")
    exports = []

    original_dialog = cmds.fileDialog2
    original_message = cmds.inViewMessage
    original_export = bg_final_export.FinalExportProcessor.export_selected_fbx

    def capture(path):
        exports.append((
            os.path.basename(path),
            [node.split('|')[-1] for node in (cmds.ls(selection=True, long=True) or [])]))

    cmds.fileDialog2 = lambda **_kwargs: [export_dir]
    cmds.inViewMessage = lambda **_kwargs: None
    bg_final_export.FinalExportProcessor.export_selected_fbx = staticmethod(capture)
    try:
        harness._export_run()
        expected = sorted(
            "{}_{}.fbx".format(pair['base'], suffix)
            for pair in pairs for suffix in ("HP", "LP"))
        assert sorted(name for name, _selection in exports) == expected
        assert all(selection for _name, selection in exports)
        separate = dict(exports)
        for pair in pairs:
            high_selection = separate[pair['base'] + "_HP.fbx"]
            # Two regular HP meshes combine; the ZBrush mesh stays separate.
            assert len(high_selection) == 2, (pair['base'], high_selection)
        assert "3" in harness.exp_status.text(), (harness.exp_status.text(), exports)

        exports[:] = []
        harness.exp_bymat.value = True
        harness._export_run()
        expected_by_material = sorted(
            "{}_{}.fbx".format(book, suffix)
            for book in ("Book_A", "Book_B") for suffix in ("HP", "LP"))
        assert sorted(name for name, _selection in exports) == expected_by_material
        assert all(selection for _name, selection in exports)
        by_material = dict(exports)
        assert len(by_material["Book_A_HP.fbx"]) == 4, by_material["Book_A_HP.fbx"]
        assert len(by_material["Book_B_HP.fbx"]) == 2, by_material["Book_B_HP.fbx"]
        assert "2" in harness.exp_status.text(), (harness.exp_status.text(), exports)
    finally:
        bg_final_export.FinalExportProcessor.export_selected_fbx = original_export
        cmds.fileDialog2 = original_dialog
        cmds.inViewMessage = original_message
        harness.close()
        shutil.rmtree(export_dir, ignore_errors=True)

    assert not bg_final_export.FinalExportProcessor._cleanup_stale_export_temps()
    assert not any(
        node.startswith("BG_") for node in (cmds.ls(type="transform") or []))
    assert cmds.undoInfo(query=True, state=True)
    print("optimized All Books export test: OK (separate + by-material)")
    APP.processEvents()


if __name__ == "__main__":
    try:
        run()
    finally:
        maya.standalone.uninitialize()
