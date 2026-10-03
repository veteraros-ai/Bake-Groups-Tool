"""Validate all help targets, translations and a closable guide offscreen."""

from pathlib import Path
import sys

import addon_utils
import bpy


addon_utils.enable("Bake_Tools_Blender", default_set=False, persistent=False)
from Bake_Tools_Blender.addon.bake_tools_blender.dependencies import enable_pyside6
enable_pyside6()
from PySide6 import QtCore, QtGui, QtWidgets
from Bake_Tools_Blender.addon.bake_tools_blender.guide_manual import build_document
from Bake_Tools_Blender.addon.bake_tools_blender.guide_manual import CURRENT_VERSION
from Bake_Tools_Blender.addon.bake_tools_blender.guide_window import GuideWindow
from Bake_Tools_Blender.addon.bake_tools_blender.qt_window import _HELP_TOPICS


app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
assert CURRENT_VERSION == "1.0.1"
targets = set(_HELP_TOPICS.values()) | {"release.whats_new", "overview"}
for language in ("en", "ru", "ja", "zh-CN"):
    document = build_document(language)
    overview_images = [entry.get("asset") for entry in document["items"]
                       if entry.get("anchor") == "overview" and entry.get("type") == "image"]
    assert overview_images == ["blender_window_full.png", "blender_window_main.png",
                               "blender_window_toc.png"], (language, overview_images)
    if language == "en":
        headings = [entry.get("text") for entry in document["items"]
                    if entry.get("anchor") == "release.whats_new" and entry.get("type") == "text"]
        assert "New in version 1.0.1" in headings, headings
    anchors = {entry.get("anchor") for entry in document["items"] if entry.get("type") == "rect"}
    missing = targets - anchors
    assert not missing, (language, sorted(missing))
    print("GUIDE_LANGUAGE_PASS", language, len(document["items"]), len(anchors))

window = GuideWindow(initial_topic="release.whats_new")
assert window.isVisible()
for language in ("ru", "ja", "zh-CN", "en"):
    window.switch_language(language)
    assert window.isVisible() and window.topic_box.currentData() == "release.whats_new"
for target in sorted(targets):
    window.go_to(target)
window.go_to("hp.keep")
app.processEvents()
viewport = window.view.viewport()
position = QtCore.QPointF(viewport.width() * .38, viewport.height() * .42)
for delta in (120, -120, 120, 120):
    before = window.view.mapToScene(position.toPoint())
    wheel = QtGui.QWheelEvent(position, viewport.mapToGlobal(position.toPoint()),
                              QtCore.QPoint(), QtCore.QPoint(0, delta),
                              QtCore.Qt.NoButton, QtCore.Qt.NoModifier,
                              QtCore.Qt.NoScrollPhase, False)
    app.sendEvent(viewport, wheel)
    app.processEvents()
    after = window.view.mapToScene(position.toPoint())
    drift = (after - before).manhattanLength()
    assert drift < 10, (delta, drift, before, after)
assert window.close()
app.processEvents()
print("GUIDE_CLOSE_PASS", len(targets))
