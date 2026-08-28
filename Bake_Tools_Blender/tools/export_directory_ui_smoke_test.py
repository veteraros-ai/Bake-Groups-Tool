"""Verify the compact export path dialog and directly editable path field."""

from __future__ import annotations

import sys
from pathlib import Path

import addon_utils
import bpy


addon_utils.enable("Bake_Tools_Blender", default_set=False, persistent=False)
ADDON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_ROOT.parent))

from Bake_Tools_Blender.addon.bake_tools_blender import qt_window  # noqa: E402


app = qt_window.QtWidgets.QApplication.instance() or qt_window.QtWidgets.QApplication(["ExportPathTest"])
window = qt_window.BakeToolsWindow()
state = bpy.context.scene.bake_tools_settings

direct_path = str(Path(bpy.app.tempdir) / "Pasted Export Path")
window.export_path.setText('"{}"'.format(direct_path))
window._store_export_path()
assert state.export_directory == direct_path
assert not window.export_path.isReadOnly()

window._choose_export_directory()
dialogs = [dialog for dialog in window._open_dialogs if type(dialog) is qt_window.QtWidgets.QDialog]
assert len(dialogs) == 1
dialog = dialogs[0]
assert not isinstance(dialog, qt_window.QtWidgets.QFileDialog)
edits = dialog.findChildren(qt_window.QtWidgets.QLineEdit)
assert len(edits) == 1 and edits[0].text() == direct_path and not edits[0].isReadOnly()
labels = {button.text() for button in dialog.findChildren(qt_window.QtWidgets.QPushButton)}
assert {"Paste", "Browse…", "Export", "Cancel"} <= labels
dialog.close(); window.close(); window.deleteLater(); app.processEvents()
print("BAKE_TOOLS_EXPORT_DIRECTORY_UI_OK editable=1 paste=1 browse=1")
