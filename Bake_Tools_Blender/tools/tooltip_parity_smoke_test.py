"""Verify Maya tooltip parity without attaching help to subgroup names."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from uuid import uuid4

import bpy


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ADDON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_ROOT.parent))

import Bake_Tools_Blender as addon  # noqa: E402


def main():
    addon.register()
    from Bake_Tools_Blender.addon.bake_tools_blender.qt_window import (
        BakeToolsWindow, QtWidgets,
    )

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["BakeToolsTooltipTest"])
    state = bpy.context.scene.bake_tools_settings
    state.language = "EN"
    pair = state.pairs.add(); pair.item_id = uuid4().hex; pair.name = "TooltipChapter"
    subgroup = pair.subgroups.add(); subgroup.item_id = uuid4().hex; subgroup.name = "Bolts_001"
    state.active_pair_id = pair.item_id; state.active_pair = 0

    window = BakeToolsWindow()
    window.refresh_from_store(force=True)
    app.processEvents()

    def button(key):
        for candidate in window.findChildren(QtWidgets.QAbstractButton):
            if str(candidate.property("bt_i18n_key") or "") == key:
                return candidate
        raise AssertionError("Missing button: {}".format(key))

    expected = {
        "Pick HP": "Store the selected HP root",
        "Pick LP": "Store the selected LP root",
        "Analyze HP": "Automatically split HP meshes",
        "Assign LP": "Assign LP meshes to LP subgroups",
        "Create Group": "Create paired HP/LP subgroups",
        "Color HP": "Color-code HP subgroups",
        "Keep HP": "Keep the existing HP hierarchy",
        "HP Visible": "Show or hide the active chapter HP root",
        "LP Visible": "Show or hide the LP root",
        "Groups Visible": "Show or hide all subgroups",
        "Find Sim": "Find similar meshes",
        "Export Settings": "Enter Export Settings",
        "Smooth View": "Preview HP smoothing",
        "Export": "Export the active chapter",
        "Save": "Save the current Bake Groups session",
        "Language": "Choose the interface language",
        "Find Groups": "Find LP groups",
        "Relocate": "Create real HP groups",
        "Link": "Save the selected proposal",
        "Unlink": "Remove the saved manual link",
        "New": "Create a new manual cluster",
        "Strict Geo Check (Resolve Overlaps)": "Use stricter geometry and overlap checks",
    }
    for key, fragment in expected.items():
        assert fragment in button(key).toolTip(), (key, button(key).toolTip())

    tooltip_keyed = {
        " Create Pair from Picked": "Create a chapter from the selected HP and LP roots",
        "Check Before Analyze": "Run duplicate, ZBrush and combined-mesh checks",
    }
    for key, fragment in tooltip_keyed.items():
        candidates = [
            candidate for candidate in window.findChildren(QtWidgets.QAbstractButton)
            if str(candidate.property("bt_i18n_tooltip_key") or "") == key
        ]
        assert candidates, "Missing tooltip-keyed button: {}".format(key)
        assert fragment in candidates[0].toolTip(), (key, candidates[0].toolTip())

    rows = window.subgroup_body.findChildren(QtWidgets.QFrame, "subgroupColorRow")
    assert rows and not rows[0].toolTip()
    subgroup_button = next(
        candidate for candidate in rows[0].findChildren(QtWidgets.QAbstractButton)
        if candidate.text() == subgroup.name
    )
    assert not subgroup_button.toolTip()
    row_help = [
        candidate.toolTip() for candidate in rows[0].findChildren(QtWidgets.QToolButton)
    ]
    assert any("Show or hide this item" in tip for tip in row_help)
    assert any("Add the selected mesh" in tip for tip in row_help)
    assert any("Lock this subgroup" in tip for tip in row_help)
    assert any("Delete this subgroup" in tip for tip in row_help)

    window.close()
    addon.unregister()
    print("BAKE_TOOLS_TOOLTIP_PARITY_OK buttons={} subgroup_name=none controls=4".format(
        len(expected) + len(tooltip_keyed)
    ))


if __name__ == "__main__":
    main()
