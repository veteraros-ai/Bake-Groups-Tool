"""Offscreen manager check: help hit testing, language sync and export flags."""

import addon_utils
import bpy


addon_utils.enable("Bake_Tools_Blender", default_set=False, persistent=False)
from Bake_Tools_Blender.addon.bake_tools_blender.dependencies import enable_pyside6
enable_pyside6()
from PySide6 import QtCore, QtGui, QtTest, QtWidgets
from Bake_Tools_Blender.addon.bake_tools_blender.qt_window import BakeToolsWindow


app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
state = bpy.context.scene.bake_tools_settings
window = BakeToolsWindow()
window.show()
app.processEvents()
assert window.export_lp_triangle.isChecked() == bool(state.export_lp_triangulate)
assert window.export_target.currentData() == "STANDARD_FBX"
assert not window.matcher_panel.isVisible()

window.toggle_help_mode()
assert window._help_mode
QtTest.QTest.mousePress(window.algorithm_button, QtCore.Qt.MouseButton.LeftButton)
QtTest.QTest.mouseRelease(window.algorithm_button, QtCore.Qt.MouseButton.LeftButton)
app.processEvents()
guide = window._guide_window
assert guide is not None and guide.isVisible()
assert guide.topic_box.currentData() == "hp.algorithm"
assert not window._help_mode
assert guide.view.horizontalScrollBarPolicy() == QtCore.Qt.ScrollBarAlwaysOff
assert guide.view.verticalScrollBarPolicy() == QtCore.Qt.ScrollBarAlwaysOff

def check_help(widget, expected_topic):
    previous = (window.color_hp.isChecked(), window.keep_hp.isChecked(),
                window.export_lp_triangle.isChecked(), window.export_target.currentData())
    window.toggle_help_mode()
    QtTest.QTest.mousePress(widget, QtCore.Qt.MouseButton.LeftButton)
    QtTest.QTest.mouseRelease(widget, QtCore.Qt.MouseButton.LeftButton)
    app.processEvents()
    assert not window._help_mode
    assert guide.topic_box.currentData() == expected_topic, (widget, guide.topic_box.currentData())
    assert previous == (window.color_hp.isChecked(), window.keep_hp.isChecked(),
                        window.export_lp_triangle.isChecked(), window.export_target.currentData())

for control, topic in (
    (window.color_hp, "hp.color"),
    (window.keep_hp, "hp.keep"),
    (window.export_scope, "export.scope"),
    (window.export_target, "export.target"),
    (window.export_hp, "export.files"),
    (window.export_lp_triangle, "export.files"),
    (window.export_separate, "export.files"),
    (window.export_path, "export.run"),
):
    check_help(control, topic)

# Exercise the exact Help -> Guide -> wheel sequence that previously jumped
# to a remote part of the board after hiding the scrollbars.
check_help(window.keep_hp, "hp.keep")
viewport = guide.view.viewport()
position = QtCore.QPointF(viewport.width() * .4, viewport.height() * .4)
before = guide.view.mapToScene(position.toPoint())
wheel = QtGui.QWheelEvent(position, viewport.mapToGlobal(position.toPoint()),
                          QtCore.QPoint(), QtCore.QPoint(0, 120),
                          QtCore.Qt.NoButton, QtCore.Qt.NoModifier,
                          QtCore.Qt.NoScrollPhase, False)
app.sendEvent(viewport, wheel)
app.processEvents()
assert (guide.view.mapToScene(position.toPoint()) - before).manhattanLength() < 10

from Bake_Tools_Blender.addon.bake_tools_blender.guide_manual import build_document
assets = [entry.get("asset") for entry in build_document("en")["items"]]
assert "blender_export_standard.png" in assets
assert "blender_export_marmoset.png" in assets
assert all(entry != "release_standard_fbx.png" for entry in assets)
assert all(entry != "release_marmoset_target.png" for entry in assets)

state.language = "JA"
window.refresh_from_store(force=True)
app.processEvents()
assert guide.isVisible() and guide.language == "ja"
assert guide.topic_box.currentData() == "hp.keep"
assert window.help_button.text() == "ヘルプ", window.help_button.text()
print("EXPORT_TARGET_JA", window.export_target.itemText(0), window.export_target.itemText(1))
guide.close()
app.processEvents()
assert window._guide_window is None
state.language = "EN"
window.close()
app.processEvents()
print("MANAGER_GUIDE_UI_PASS")
