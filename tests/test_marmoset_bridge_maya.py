# -*- coding: utf-8 -*-
"""Maya standalone tests for the Marmoset package exporter."""
from __future__ import print_function

import json
import os
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

import bg_final_export
import bg_marmoset_bridge
import bg_mixins


class _PanelHarness(QtWidgets.QWidget, bg_mixins.ExportMixin):
    def __init__(self):
        QtWidgets.QWidget.__init__(self)


def _test_export_panel():
    harness = _PanelHarness()
    panel = harness.build_export_panel({})
    assert harness.exp_target.count() == 2
    harness.exp_target.setCurrentIndex(1)
    assert harness.exp_inc_hp.isChecked() and harness.exp_inc_lp.isChecked()
    assert not harness.exp_inc_cage.isChecked()
    assert harness.exp_inc_hp.isEnabled() and harness.exp_inc_lp.isEnabled()
    assert not harness.exp_inc_cage.isEnabled()
    assert not harness.exp_files_one.isEnabled()
    harness.exp_inc_lp.setChecked(False)
    harness.exp_scope.setCurrentIndex(1)
    assert not harness.exp_inc_lp.isChecked()
    harness.exp_target.setCurrentIndex(0)
    harness.exp_target.setCurrentIndex(1)
    assert harness.exp_inc_hp.isChecked() and not harness.exp_inc_lp.isChecked()
    harness.exp_inc_hp.setChecked(False)
    harness.exp_inc_lp.setChecked(True)
    harness._update_export_target_controls()
    assert not harness.exp_inc_hp.isChecked() and harness.exp_inc_lp.isChecked()
    harness.exp_target.setCurrentIndex(0)
    assert harness.exp_inc_hp.isEnabled()
    harness.exp_scope.setCurrentIndex(0)
    assert not harness.exp_bymat.isEnabled()  # Active Chapter
    harness.exp_scope.setCurrentIndex(1)
    assert harness.exp_bymat.isEnabled()
    panel.close()
    harness.close()


def _test_export_role_routing():
    harness = _PanelHarness()
    panel = harness.build_export_panel({})
    harness.exp_target.setCurrentIndex(1)
    pair = {"id": "routing", "base": "Routing", "book": "Book"}
    harness.root_pairs = [pair]
    harness.active_root_id = "routing"
    harness.save_final_smooth_states = lambda: None
    harness._prepare_export_snapshots = lambda targets: {"routing": {}}
    preflights, exports, preview = [], [], []
    harness._export_preflight = lambda targets, hp, lp, cage, **kw: (
        preflights.append((hp, lp, cage)) or ([], []))
    harness.disable_preview_smoothing_for_export = lambda: preview.append(True)
    harness._export_marmoset_package = lambda targets, snapshots, **kw: exports.append(kw)
    harness.exp_inc_hp.setChecked(False)
    harness._export_run()
    assert preflights[-1] == (False, True, False)
    assert exports[-1] == {"inc_hp": False, "inc_lp": True}
    assert not preview, "LP-only export must not disable the HP preview"
    harness.exp_inc_hp.setChecked(True)
    harness.exp_inc_lp.setChecked(False)
    harness._export_run()
    assert preflights[-1] == (True, False, False)
    assert exports[-1] == {"inc_hp": True, "inc_lp": False}
    assert preview == [True]
    assert not harness._marmoset_export_in_progress
    panel.close()
    harness.close()


def _test_plugin_installation():
    root = tempfile.mkdtemp(prefix="bg_toolbag_install_test_")
    old_value = os.environ.get("LOCALAPPDATA")
    os.environ["LOCALAPPDATA"] = root
    try:
        status = bg_marmoset_bridge.toolbag_plugin_status()
        assert status["state"] == "missing"
        destination = bg_marmoset_bridge.install_toolbag_plugin()
        assert destination == status["destination"]
        assert bg_marmoset_bridge.toolbag_plugin_status()["state"] == "current"
        with open(destination, "wb") as stream:
            stream.write(b"# existing user version\n")
        assert bg_marmoset_bridge.toolbag_plugin_status()["state"] == "outdated"
        bg_marmoset_bridge.install_toolbag_plugin()
        assert bg_marmoset_bridge.toolbag_plugin_status()["state"] == "current"
        backups = [name for name in os.listdir(os.path.dirname(destination))
                   if name.startswith("__main__.py.bak-")]
        assert len(backups) == 1
        with open(os.path.join(os.path.dirname(destination), backups[0]), "rb") as stream:
            assert stream.read() == b"# existing user version\n"
    finally:
        if old_value is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = old_value
        shutil.rmtree(root, ignore_errors=True)


def _test_missing_plugin_offer():
    harness = _PanelHarness()
    harness.log = lambda *_args: None
    original_status = bg_marmoset_bridge.toolbag_plugin_status
    bg_marmoset_bridge.toolbag_plugin_status = lambda: {
        "state": "missing", "destination": "test-plugin-path"}
    try:
        offered = []
        harness._ask_marmoset_plugin_setup = lambda status: (
            offered.append(status["state"]) or "export_only")
        assert harness._ensure_marmoset_plugin()
        assert offered == ["missing"]
        harness._ask_marmoset_plugin_setup = lambda _status: "cancel"
        assert not harness._ensure_marmoset_plugin()
    finally:
        bg_marmoset_bridge.toolbag_plugin_status = original_status
        harness.close()


def _test_toolbag_connection_states():
    original_pids = bg_marmoset_bridge.toolbag_running_pids
    original_link = os.environ.get("BG_TOOLBAG_LINK_DIR")
    root = tempfile.mkdtemp(prefix="bg_toolbag_connection_test_")
    try:
        os.environ["BG_TOOLBAG_LINK_DIR"] = root
        bg_marmoset_bridge.toolbag_running_pids = lambda: set()
        assert bg_marmoset_bridge.find_toolbag_project("package.json")["state"] == "not_running"
        bg_marmoset_bridge.toolbag_running_pids = lambda: {12345}
        assert bg_marmoset_bridge.find_toolbag_project("package.json")["state"] == "bridge_unavailable"
    finally:
        bg_marmoset_bridge.toolbag_running_pids = original_pids
        if original_link is None:
            os.environ.pop("BG_TOOLBAG_LINK_DIR", None)
        else:
            os.environ["BG_TOOLBAG_LINK_DIR"] = original_link
        shutil.rmtree(root)


def _test_toolbag_launch_command():
    root = tempfile.mkdtemp(prefix="bg_toolbag_launch_test_")
    manifest = os.path.join(root, "BakeGroups_Marmoset.json")
    with open(manifest, "w", encoding="utf-8") as stream:
        stream.write("{}")
    original_status = bg_marmoset_bridge.toolbag_plugin_status
    original_executable = bg_marmoset_bridge.toolbag_executable_path
    original_popen = bg_marmoset_bridge.subprocess.Popen
    calls = []
    bg_marmoset_bridge.toolbag_plugin_status = lambda: {
        "state": "current", "destination": os.path.join(root, "bridge", "__main__.py")}
    bg_marmoset_bridge.toolbag_executable_path = lambda: os.path.join(root, "Toolbag 5", "toolbag.exe")
    bg_marmoset_bridge.subprocess.Popen = lambda *args, **kwargs: (
        calls.append((args, kwargs)) or object())
    try:
        bg_marmoset_bridge.launch_toolbag_bridge(manifest)
        assert len(calls) == 1
        command = calls[0][0][0]
        assert command[-2:] == ["--bake-groups-manifest", manifest]
        assert command[1].endswith("__main__.py")
    finally:
        bg_marmoset_bridge.toolbag_plugin_status = original_status
        bg_marmoset_bridge.toolbag_executable_path = original_executable
        bg_marmoset_bridge.subprocess.Popen = original_popen
        shutil.rmtree(root, ignore_errors=True)


def _long(node):
    return (cmds.ls(node, long=True) or [node])[0]


def run():
    _test_export_panel()
    _test_export_role_routing()
    _test_plugin_installation()
    _test_missing_plugin_offer()
    _test_toolbag_connection_states()
    _test_toolbag_launch_command()
    cmds.file(new=True, force=True)
    hp_root = cmds.group(empty=True, name="Chapter_HP")
    hp_group = cmds.group(empty=True, name="Group_HP", parent=hp_root)
    lp_root = cmds.group(empty=True, name="Chapter_LP")
    lp_group = cmds.group(empty=True, name="Group_LP", parent=lp_root)
    hp_nodes = []
    for index in range(2):
        node = cmds.polyCube(
            name="Chapter_Group_high_{:03d}".format(index + 1),
            constructionHistory=False)[0]
        hp_nodes.append(_long(cmds.parent(node, hp_group)[0]))
    lp = cmds.polyPlane(
        name="Chapter_Group_low_001", subdivisionsX=2, subdivisionsY=2,
        constructionHistory=False)[0]
    lp = _long(cmds.parent(lp, lp_group)[0])
    zbrush = cmds.createDisplayLayer(name="ZBrush_HP", empty=True)
    cmds.editDisplayLayerMembers(zbrush, hp_nodes[1], noRecurse=True)

    snapshot = bg_final_export.FinalExportProcessor.build_chapter_snapshot(
        "Chapter", _long(hp_root), _long(lp_root), {"Group": 2})
    pair = {"id": "chapter-id", "base": "Chapter", "book": "Book"}
    out_dir = tempfile.mkdtemp(prefix="bg_marmoset_test_")
    captured = []
    original_export = bg_final_export.FinalExportProcessor.export_selected_fbx

    def capture(path):
        with open(path, 'wb') as stream:
            stream.write(b"FBX test placeholder")
        selected = cmds.ls(selection=True, long=True, type="transform") or []
        captured.append({
            "path": path,
            "names": [node.split("|")[-1] for node in selected],
            "faces": [cmds.polyEvaluate(node, face=True) for node in selected],
        })

    bg_final_export.FinalExportProcessor.export_selected_fbx = staticmethod(capture)
    try:
        manifest_path = bg_marmoset_bridge.MarmosetPackageExporter.export_package(
            [pair], {"chapter-id": snapshot}, out_dir)
        assert manifest_path and os.path.isfile(manifest_path)
        with open(manifest_path, "r") as stream:
            manifest = json.load(stream)
        assert manifest["schema"] == "bake-groups-toolbag"
        assert "cage" not in json.dumps(manifest).lower()
        records = manifest["chapters"][0]["meshes"]
        hp_records = [record for record in records if record["role"] == "high"]
        lp_records = [record for record in records if record["role"] == "low"]
        assert len(hp_records) == 2 and len(lp_records) == 1
        assert sorted(record["subdivision_level"] for record in hp_records) == [0, 2]
        assert all("_high_bg" in record["export_name"] for record in hp_records)
        assert len(captured) == 1
        # Both HP cubes remain separate and unsmoothed (6 faces each); LP is
        # triangulated from four quads to eight triangles.
        assert sorted(captured[0]["faces"]) == [6, 6, 8], captured[0]
        assert len(captured[0]["names"]) == 3
        assert cmds.polyEvaluate(hp_nodes[0], face=True) == 6
        assert cmds.polyEvaluate(lp, face=True) == 4
        assert not cmds.ls("BG_Marmoset_Export_Temp*", type="transform")
        combined_path = os.path.join(out_dir, "models", "Chapter_Marmoset.fbx")
        combined_stat = os.stat(combined_path)
        exporter = bg_marmoset_bridge.MarmosetPackageExporter
        # Existing combined packages, including schema-1 legacy packages, can
        # be upgraded without dropping records or overwriting the other role.
        manifest["chapters"][0].pop("models")
        manifest["chapters"][0].pop("update_roles")
        bg_marmoset_bridge._atomic_write_json(manifest_path, manifest)
        exporter.export_package([pair], {"chapter-id": snapshot}, out_dir, inc_lp=False)
        with open(manifest_path) as stream:
            partial = json.load(stream)["chapters"][0]
        assert partial["update_roles"] == ["high"]
        assert len(partial["meshes"]) == 3
        assert sorted(captured[-1]["faces"]) == [6, 6]
        assert len(captured[-1]["names"]) == 2
        assert len(partial["models"]) == 2
        assert os.stat(combined_path).st_mtime_ns == combined_stat.st_mtime_ns
        hp_path = os.path.join(out_dir, "models", "Chapter_Marmoset_HP.fbx")
        hp_stat = os.stat(hp_path)
        exporter.export_package([pair], {"chapter-id": snapshot}, out_dir, inc_hp=False)
        with open(manifest_path) as stream:
            partial = json.load(stream)["chapters"][0]
        assert partial["update_roles"] == ["low"]
        assert len(partial["meshes"]) == 3
        assert captured[-1]["faces"] == [8]
        assert len(captured[-1]["names"]) == 1
        assert os.stat(hp_path).st_mtime_ns == hp_stat.st_mtime_ns
        assert {m["path"] for m in partial["models"]} == {
            "models/Chapter_Marmoset_HP.fbx", "models/Chapter_Marmoset_LP.fbx"}
        before = len(captured)
        assert exporter.export_package(
            [pair], {"chapter-id": snapshot}, out_dir, inc_hp=False, inc_lp=False) is None
        assert len(captured) == before
        # Validation must only complain about the requested root/role.
        harness = _PanelHarness()
        partial_snapshot = dict(snapshot, lp=None, loose_lp=["irrelevant"])
        errors, warnings = harness._export_preflight(
            [pair], True, False, False, {"chapter-id": partial_snapshot})
        assert not errors and not warnings, (errors, warnings)
        errors, warnings = harness._export_preflight(
            [pair], False, True, False,
            {"chapter-id": dict(snapshot, hp=None, loose_lp=[])})
        assert not errors and not warnings, (errors, warnings)
        harness.close()
        print("Marmoset Maya bridge test: OK")
    finally:
        bg_final_export.FinalExportProcessor.export_selected_fbx = original_export
        bg_final_export.FinalExportProcessor._cleanup_stale_export_temps()
        shutil.rmtree(out_dir, ignore_errors=True)

    fixture_dir = os.environ.get("BG_MARMOSET_FIXTURE_DIR")
    if fixture_dir:
        if not os.path.isdir(fixture_dir):
            os.makedirs(fixture_dir)
        actual_manifest = bg_marmoset_bridge.MarmosetPackageExporter.export_package(
            [pair], {"chapter-id": snapshot}, fixture_dir)
        assert actual_manifest and os.path.isfile(actual_manifest)
        assert os.path.getsize(os.path.join(
            fixture_dir, "models", "Chapter_Marmoset.fbx")) > 0
        print("Marmoset fixture: {}".format(actual_manifest))


if __name__ == "__main__":
    try:
        run()
    finally:
        maya.standalone.uninitialize()
