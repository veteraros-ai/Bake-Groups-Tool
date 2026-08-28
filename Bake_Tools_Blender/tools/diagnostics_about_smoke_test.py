"""Headless verification of 1.0.0 diagnostics and Maya-style About UI."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import zipfile

import bpy


ADDON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_ROOT.parent))
import Bake_Tools_Blender as addon  # noqa: E402


def main():
    addon.register()
    state = bpy.context.scene.bake_tools_settings
    state.log_text = "Ready.\nAnalyze HP complete"
    state.action_history = "[10:00:00] Picked HP\n[10:00:01] Analyze HP"
    state.debug_text = "strategy=PCA_SHAPE\ngroups=2"

    with tempfile.TemporaryDirectory(prefix="BakeToolsDiagnostics-") as folder:
        folder = Path(folder)
        debug = folder / "debug.txt"
        support = folder / "support.zip"
        assert "FINISHED" in bpy.ops.bake_tools.save_diagnostics(kind="DEBUG", filepath=str(debug))
        report = debug.read_text(encoding="utf-8")
        for section in ("=== Visible Log ===", "=== User Actions ===",
                        "=== Current Scene Snapshot ===", "=== Analyze / Assign Debug ==="):
            assert section in report
        assert "Analyze HP complete" in report and "strategy=PCA_SHAPE" in report

        assert "FINISHED" in bpy.ops.bake_tools.save_diagnostics(kind="SUPPORT", filepath=str(support))
        with zipfile.ZipFile(support, "r") as archive:
            expected = {
                "support_report.txt", "visible_log.txt", "user_actions.txt",
                "scene_snapshot.txt", "collections.txt", "analyze_assign_debug.txt",
                "environment.json", "session_pairs.json", "package_manifest.json",
                "update_manifest.json",
            }
            assert expected <= set(archive.namelist())
            environment = json.loads(archive.read("environment.json"))
            manifest = json.loads(archive.read("package_manifest.json"))
            assert environment["plugin_version"] == "1.0.0"
            assert manifest["version"] == "1.0.0"

    from Bake_Tools_Blender.addon.bake_tools_blender.about_update import AboutUpdateDialog
    from Bake_Tools_Blender.addon.bake_tools_blender import qt_window, update_service

    app = qt_window.QtWidgets.QApplication.instance() or qt_window.QtWidgets.QApplication(["BakeToolsAboutTest"])
    dialog = AboutUpdateDialog(None, lambda value: value)
    assert dialog.windowModality() == qt_window.QtCore.Qt.WindowModality.NonModal
    assert dialog.minimumWidth() == 520 and dialog.maximumWidth() == 560
    assert dialog.installed.text() == "1.0.0"
    assert dialog.check.text() == "Check"
    assert dialog.manual.text() == "Show manual"
    assert dialog.rollback.text() == "Rollback"
    assert update_service.manual_path().is_file()
    dialog._set_result({
        "current_version": "0.9.9", "remote_version": "1.0.0",
        "is_update_available": True, "release_notes": "Test release",
    })
    assert dialog.latest.text() == "1.0.0" and not dialog.notes.isHidden()
    dialog.close()
    addon.unregister()
    print("BAKE_TOOLS_DIAGNOSTICS_ABOUT_OK")


if __name__ == "__main__":
    main()
