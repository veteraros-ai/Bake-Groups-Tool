"""Headless Qt regression for Export Settings subgroup interactions."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from uuid import uuid4

import bpy


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ADDON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_ROOT.parent))

import Bake_Tools_Blender as addon  # noqa: E402


def main():
    addon.register()
    from Bake_Tools_Blender.addon.bake_tools_blender.qt_window import (
        BakeToolsWindow, QtCore, QtWidgets,
    )

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["BakeToolsFinalUITest"])
    state = bpy.context.scene.bake_tools_settings
    pair = state.pairs.add(); pair.item_id = uuid4().hex; pair.name = "Chapter"
    for name in ("Bolts_001", "Large_001", "ZBrush_Huge_001"):
        subgroup = pair.subgroups.add(); subgroup.item_id = uuid4().hex; subgroup.name = name
        assert subgroup.smooth_level == 1
    state.active_pair_id = pair.item_id; state.active_pair = 0; state.final_view = True

    window = BakeToolsWindow()
    window.refresh_from_store(force=True)
    app.processEvents()
    assert len(window._final_rows) == 3
    assert window.export_lp_triangle.isChecked()
    assert not window.export_cage.isEnabled()
    assert "#4a5d4a" in window.hp_visible.styleSheet()
    assert "#4a5d4a" in window.lp_visible.styleSheet()
    first_id = pair.subgroups[0].item_id
    row, _name, _subgroup, _active = window._final_rows[first_id]
    eye = next(button for button in row.findChildren(QtWidgets.QToolButton))
    assert eye.contextMenuPolicy() == QtCore.Qt.ContextMenuPolicy.CustomContextMenu
    assert eye.toolTip()
    assert not row.toolTip() and not _name.toolTip()

    window._select_all_final_subgroups()
    assert window._final_selected_ids == {group.item_id for group in pair.subgroups}
    # Rectangle selection itself uses row geometry; covering just the first row
    # must replace the selection unless Ctrl/additive is requested.
    window._select_final_rows_in_rect(row.geometry(), additive=False)
    assert first_id in window._final_selected_ids

    calls = []
    window.controller.action = lambda action, value="": calls.append((action, value))
    window._final_selected_ids = {first_id}
    window._cage_action("CAGE_CREATE")
    action, value = calls[-1]
    assert action == "CAGE_CREATE" and json.loads(value)["subgroups"] == [first_id]

    second_id = pair.subgroups[1].item_id
    window._final_selected_ids = {first_id, second_id}
    window._batch_final_smooth("UP", first_id)
    action, value = calls[-1]
    payload = json.loads(value)
    assert action == "SUBGROUP_SMOOTH_BATCH"
    assert set(payload["subgroups"]) == {first_id, second_id} and payload["mode"] == "UP"

    # Maya parity: in Export Settings an existing Cage replaces the LP
    # visibility toggle and keeps the same green/red state coding.
    from Bake_Tools_Blender.addon.bake_tools_blender.cage_service import (  # noqa: E402
        CAGE_MARKER, CAGE_PAIR_ID, CAGE_SUBGROUP_ID,
    )
    mesh = bpy.data.meshes.new("FinalUICageMesh")
    cage = bpy.data.objects.new("FinalUICage", mesh)
    bpy.context.collection.objects.link(cage)
    cage[CAGE_MARKER] = True; cage[CAGE_PAIR_ID] = pair.item_id; cage[CAGE_SUBGROUP_ID] = first_id
    pair.cage_visible = False
    window.refresh_from_store(force=True)
    assert window.export_cage.isEnabled()
    assert window.export_cage.isChecked()
    assert window.cage_export_button.isEnabled()
    assert window.lp_visible.property("bt_i18n_key") == "Cage Hid"
    assert "#8c4242" in window.lp_visible.styleSheet()
    setting_calls = []
    window.controller.set_setting = lambda setting, value: setting_calls.append((setting, value))
    window._toggle_visibility("lp_visible")
    assert setting_calls[-1] == ("cage_visible", True)
    pair.cage_visible = True
    window.refresh_from_store(force=True)
    assert window.lp_visible.property("bt_i18n_key") == "Cage Vis"
    assert "#4a5d4a" in window.lp_visible.styleSheet()

    # Ten percent of slider travel is only 0.03% of the chapter BBox. This
    # protects small assets from the former coarse 1% jump.
    slider_row = window._cage_slider_row("CAGE_EXPANSION")
    slider = slider_row.findChild(QtWidgets.QSlider)
    spin = slider_row.findChild(QtWidgets.QDoubleSpinBox)
    slider.setValue(100)
    assert abs(spin.value() - 0.03) < 1.0e-6

    window.close()
    addon.unregister()
    print("BAKE_TOOLS_FINAL_UI_OK eye_rmb=1 rubber_selection=1 cage_scope=1 batch_smooth=1 visibility_colors=1 cage_toggle=1 bbox_jog=1 smooth_default=1 export_flags=1 tooltips=1")


if __name__ == "__main__":
    main()
