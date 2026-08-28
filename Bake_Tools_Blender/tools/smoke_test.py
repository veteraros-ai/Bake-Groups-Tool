"""Headless architecture and Qt-construction smoke test for a release build."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import bpy


ADDON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_ROOT.parent))

import Bake_Tools_Blender as addon  # noqa: E402


def make_root(name, location):
    obj = bpy.data.objects.new(name, None)
    obj.location = location
    bpy.context.collection.objects.link(obj)
    return obj


def make_object(name, location, parent):
    bpy.ops.mesh.primitive_cube_add(location=location)
    obj = bpy.context.active_object
    obj.name = name
    obj.parent = parent
    return obj


def select_only(*objects):
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0] if objects else None


def main():
    addon.register()
    hp = make_root("HP_ROOT", (-2, 0, 0))
    lp = make_root("LP_ROOT", (2, 0, 0))
    hp_a = make_object("HP_A", (-2, 0, 0), hp)
    hp_b = make_object("HP_B", (-2, 1, 0), hp)
    lp_a = make_object("LP_A", (2, 0, 0), lp)
    lp_b = make_object("LP_B", (2, 1, 0), lp)

    # Reproduce a context switch between Blender editors: selection is captured,
    # then the source area's active object disappears before the operator runs.
    from Bake_Tools_Blender.addon.bake_tools_blender.blender_bridge import capture_context

    select_only(hp)
    capture_context()
    select_only()
    assert "FINISHED" in bpy.ops.bake_tools.pick_object(role="HP")
    select_only(lp)
    capture_context()
    select_only()
    assert "FINISHED" in bpy.ops.bake_tools.pick_object(role="LP")
    bpy.context.scene.bake_tools_settings.group_name = "Gun_01"
    assert "FINISHED" in bpy.ops.bake_tools.create_pair()
    state = bpy.context.scene.bake_tools_settings
    assert not state.hp_object and state.hp_root is None and state.hp_collection is None
    assert not state.lp_object and state.lp_root is None and state.lp_collection is None
    select_only(hp_a, lp_a)
    assert "FINISHED" in bpy.ops.bake_tools.add_subgroup(name="Bolts_001")
    select_only(hp_b, lp_b)
    assert "FINISHED" in bpy.ops.bake_tools.add_subgroup(name="Large_001")

    from Bake_Tools_Blender.addon.bake_tools_blender.store import BlenderStateStore

    snapshot = BlenderStateStore().snapshot()
    assert snapshot.active_chapter.name == "Gun_01"
    assert [item.name for item in snapshot.active_chapter.subgroups] == ["Bolts_001", "Large_001"]
    bolts = snapshot.active_chapter.subgroups[0]
    large = snapshot.active_chapter.subgroups[1]
    assert [member.name for member in bolts.hp_members] == ["HP_A"]
    assert [member.name for member in bolts.lp_members] == ["LP_A"]
    assert [member.name for member in large.hp_members] == ["HP_B"]
    assert [member.name for member in large.lp_members] == ["LP_B"]

    # Add Selected moves exclusive membership instead of duplicating it.
    select_only(hp_a)
    bpy.ops.bake_tools.subgroup_action(action="ADD_SELECTED", subgroup_id=large.item_id)
    hp_a.name = "HP_A_Renamed"
    snapshot = BlenderStateStore().snapshot()
    bolts, large = snapshot.active_chapter.subgroups
    assert bolts.hp_count == 0 and bolts.lp_count == 1
    assert sorted(member.name for member in large.hp_members) == ["HP_A_Renamed", "HP_B"]

    bpy.ops.bake_tools.subgroup_action(action="SELECT_MESHES", subgroup_id=large.item_id)
    assert {obj.name for obj in bpy.context.selected_objects} == {"HP_A_Renamed", "HP_B", "LP_B"}
    bpy.ops.bake_tools.subgroup_action(action="TOGGLE_VISIBLE", subgroup_id=large.item_id)
    assert hp_a.hide_viewport and hp_b.hide_viewport and lp_b.hide_viewport
    assert not lp_a.hide_viewport
    bpy.ops.bake_tools.subgroup_action(action="TOGGLE_VISIBLE", subgroup_id=large.item_id)
    assert not hp_a.hide_viewport and not hp_b.hide_viewport and not lp_b.hide_viewport

    # Deleting a subgroup releases members; it never deletes scene objects.
    bpy.ops.bake_tools.subgroup_action(action="DELETE", subgroup_id=large.item_id)
    assert all(bpy.data.objects.get(name) is not None for name in ("HP_A_Renamed", "HP_B", "LP_B"))
    snapshot = BlenderStateStore().snapshot()
    assert [item.name for item in snapshot.active_chapter.subgroups] == ["Bolts_001"]
    select_only(hp_a)
    subgroup_id = snapshot.active_chapter.subgroups[0].item_id
    bpy.ops.bake_tools.subgroup_action(action="ADD_SELECTED", subgroup_id=subgroup_id)
    hp.name = "HP_ROOT_RENAMED"
    snapshot = BlenderStateStore().snapshot()
    assert snapshot.active_chapter.hp_object == "HP_ROOT_RENAMED"
    assert snapshot.active_chapter.subgroups[0].hp_count == 1
    assert snapshot.active_chapter.subgroups[0].lp_count == 1
    pair_id = snapshot.active_chapter.item_id
    bpy.ops.bake_tools.subgroup_action(action="TOGGLE_VISIBLE", subgroup_id=subgroup_id)
    bpy.ops.bake_tools.subgroup_action(action="TOGGLE_LOCK", subgroup_id=subgroup_id)
    bpy.ops.bake_tools.pair_action(action="SET_BOOK", pair_id=pair_id, value="Weapons")
    bpy.ops.bake_tools.set_setting(setting="find_mode", value="ALL")
    bpy.ops.bake_tools.set_setting(setting="matcher_mode", value="ACCURATE")
    bpy.ops.bake_tools.set_setting(setting="export_scope", value="ALL")
    bpy.ops.bake_tools.set_setting(setting="final_view", value="1")

    snapshot = BlenderStateStore().snapshot()
    assert snapshot.active_chapter.book == "Weapons"
    assert snapshot.active_chapter.subgroups[0].visible is False
    assert snapshot.active_chapter.subgroups[0].locked is True
    assert snapshot.final_view is True
    assert snapshot.find_mode == "ALL"
    assert snapshot.matcher_mode == "ACCURATE"
    assert snapshot.export_scope == "ALL"

    # The same properties used by the native Create Chapter dialog must resolve
    # a custom choice into the stored chapter name.
    pair_count = len(bpy.context.scene.bake_tools_settings.pairs)
    select_only(hp)
    capture_context()
    assert "FINISHED" in bpy.ops.bake_tools.pick_object(role="HP")
    select_only(lp)
    capture_context()
    assert "FINISHED" in bpy.ops.bake_tools.pick_object(role="LP")
    assert "FINISHED" in bpy.ops.bake_tools.create_pair(
        hp_base="HP_Choice", lp_base="LP_Choice",
        name_choice="CUSTOM", custom_name="Dialog_Chapter",
    )
    assert len(bpy.context.scene.bake_tools_settings.pairs) == pair_count + 1
    assert bpy.context.scene.bake_tools_settings.pairs[-1].name == "Dialog_Chapter"
    assert "FINISHED" in bpy.ops.bake_tools.remove_pair()

    from Bake_Tools_Blender.addon.bake_tools_blender.sync import _notify_after_scene_change
    assert _notify_after_scene_change in bpy.app.handlers.undo_post
    assert _notify_after_scene_change in bpy.app.handlers.redo_post
    assert _notify_after_scene_change in bpy.app.handlers.load_post

    roundtrip_path = os.environ.get("BAKE_TOOLS_TEST_BLEND")
    if roundtrip_path:
        assert "FINISHED" in bpy.ops.wm.save_as_mainfile(filepath=roundtrip_path)
        assert "FINISHED" in bpy.ops.wm.open_mainfile(filepath=roundtrip_path)
        snapshot = BlenderStateStore().snapshot()
        assert snapshot.active_chapter.hp_object == "HP_ROOT_RENAMED"
        assert [member.name for member in snapshot.active_chapter.subgroups[0].hp_members] == ["HP_A_Renamed"]
        assert [member.name for member in snapshot.active_chapter.subgroups[0].lp_members] == ["LP_A"]

    assert bpy.types.Operator.bl_rna_get_subclass_py("BAKE_TOOLS_OT_open_manager") is None
    assert bpy.types.Operator.bl_rna_get_subclass_py("BAKE_TOOLS_OT_show_manager") is not None
    assert bpy.types.Operator.bl_rna_get_subclass_py("BAKE_TOOLS_OT_hide_manager") is not None
    assert bpy.types.Panel.bl_rna_get_subclass_py("BAKE_TOOLS_PT_main") is None
    assert bpy.types.Panel.bl_rna_get_subclass_py("BAKE_TOOLS_PT_launcher") is not None
    from Bake_Tools_Blender.addon.bake_tools_blender import qt_window

    app = qt_window.QtWidgets.QApplication.instance() or qt_window.QtWidgets.QApplication(["BakeToolsSmoke"])
    window = qt_window.BakeToolsWindow()
    assert window.main_splitter.count() == 2
    window.resize(240, 800)
    window._update_responsive_layout(force=True)
    assert window._responsive_narrow
    assert not window.responsive_bar.isHidden()
    assert window.responsive_bar.height() == 24
    assert window.responsive_bar.maximumHeight() == 24
    assert window.responsive_bar.sizePolicy().verticalPolicy() == qt_window.QtWidgets.QSizePolicy.Policy.Fixed
    assert not window.left_panel.isHidden()
    assert window.right_panel.isHidden()
    window._set_responsive_page("side")
    assert window.left_panel.isHidden()
    assert not window.right_panel.isHidden()
    window.resize(520, 800)
    window._update_responsive_layout(force=True)
    assert not window._responsive_narrow
    assert window.responsive_bar.isHidden()
    assert not window.left_panel.isHidden()
    assert not window.right_panel.isHidden()
    assert not (window.windowFlags() & qt_window.QtCore.Qt.WindowType.WindowStaysOnTopHint)
    assert window.windowFlags() & qt_window.QtCore.Qt.WindowType.FramelessWindowHint
    assert window.windowModality() == qt_window.QtCore.Qt.WindowModality.NonModal

    # The first Analyze HP request must reproduce Maya's preflight offer.
    window._request_analyze_hp()
    structure_dialog = next(
        dialog for dialog in window._open_dialogs
        if isinstance(dialog, qt_window.QtWidgets.QMessageBox)
        and dialog.windowTitle() == "Structure Not Checked"
    )
    structure_buttons = {button.text() for button in structure_dialog.buttons()}
    assert {"Check Now", "Continue", "Cancel"} <= structure_buttons
    structure_dialog.close()

    # The checker exposes resolution actions rather than one read-only report.
    payload = {
        "pair_id": snapshot.active_pair_id,
        "duplicates": [["A", "B"]], "zbrush": [], "combined": [],
        "issue_count": 2, "report": "fixture",
    }
    window._show_duplicate_check(payload)
    duplicate_dialog = next(
        dialog for dialog in window._open_dialogs
        if isinstance(dialog, qt_window.QtWidgets.QMessageBox)
        and dialog.windowTitle() == "Duplicate Meshes Found"
    )
    duplicate_buttons = {button.text() for button in duplicate_dialog.buttons()}
    assert {"Select", "Remove Extra Copies", "Skip"} <= duplicate_buttons
    duplicate_dialog.close()

    # Regression for Blender crash reports ending in
    # QMessageBox::buttonClicked -> Qt6Core QByteArray::clear.  A mesh operator
    # and the next dialog must not run while Qt is still processing the button.
    deferred_actions = []
    original_action = window.controller.action
    window.controller.action = lambda action: deferred_actions.append(action) or True
    combined_payload = {
        "pair_id": snapshot.active_pair_id,
        "duplicates": [], "zbrush": [], "combined": ["Combined_A"],
        "issue_count": 1, "report": "fixture",
    }
    window._show_combined_check(combined_payload)
    combined_dialog = next(
        dialog for dialog in window._open_dialogs
        if isinstance(dialog, qt_window.QtWidgets.QMessageBox)
        and dialog.windowTitle() == "Combined Meshes Found"
    )
    separate_button = next(
        button for button in combined_dialog.buttons() if button.text() == "Separate"
    )
    separate_button.click()
    assert deferred_actions == []
    app.processEvents()
    assert deferred_actions == ["CHECK_SEPARATE_COMBINED"]
    app.processEvents()
    window.controller.action = original_action
    window.close()
    addon.unregister()
    print("BAKE_TOOLS_SMOKE_OK")


if __name__ == "__main__":
    main()
