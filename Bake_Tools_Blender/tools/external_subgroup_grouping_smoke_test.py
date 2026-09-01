"""Regression for outside-chapter subgroup assignment and export grouping."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy


ADDON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_ROOT.parent))

import Bake_Tools_Blender as addon  # noqa: E402


def empty(name):
    obj = bpy.data.objects.new(name, None)
    bpy.context.collection.objects.link(obj)
    return obj


def cube(name, parent=None):
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.active_object
    obj.name = name
    obj.parent = parent
    return obj


def select_only(*objects):
    for obj in tuple(bpy.context.selected_objects):
        obj.select_set(False)
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0] if objects else None


def refs(collection):
    return {ref.target for ref in collection if ref.target is not None}


def managed(role, pair_id="", subgroup_id="", side=""):
    return next(
        (
            collection for collection in bpy.data.collections
            if collection.get("bake_tools_managed", False)
            and str(collection.get("bake_tools_collection_role", "")) == role
            and str(collection.get("bake_tools_pair_id", "")) == pair_id
            and str(collection.get("bake_tools_subgroup_id", "")) == subgroup_id
            and str(collection.get("bake_tools_side", "")) == side
        ),
        None,
    )


def verify_qt_role_routing(state, subgroup, outside):
    """Exercise the one-visible fast path and both-visible non-modal prompt."""
    try:
        from Bake_Tools_Blender.addon.bake_tools_blender import qt_window
    except Exception:
        # Source checkouts intentionally omit the bundled marketplace runtime;
        # the same script exercises this branch from an installed/release build.
        return

    app = qt_window.QtWidgets.QApplication.instance() or qt_window.QtWidgets.QApplication(
        ["BakeToolsExternalMembership"]
    )
    window = qt_window.BakeToolsWindow()
    real_action = window.controller.subgroup_action
    calls = []
    window.controller.subgroup_action = lambda *args: calls.append(args) or True
    select_only(outside)

    state.hp_visible = True
    state.lp_visible = False
    window.refresh_from_store(force=True)
    window._add_selected_to_subgroup(subgroup.item_id)
    assert calls == [("ADD_SELECTED", subgroup.item_id, "HP")]

    calls.clear()
    state.hp_visible = True
    state.lp_visible = True
    window.refresh_from_store(force=True)
    window._add_selected_to_subgroup(subgroup.item_id)
    boxes = [item for item in window._open_dialogs if isinstance(item, qt_window.QtWidgets.QMessageBox)]
    assert boxes
    box = boxes[-1]
    labels = {button.text(): button for button in box.buttons()}
    assert "HP" in labels and "LP" in labels
    labels["LP"].click()
    app.processEvents()
    assert calls == [("ADD_SELECTED", subgroup.item_id, "LP")]

    window.controller.subgroup_action = real_action
    window.close()
    app.processEvents()


def main():
    addon.register()
    hp_root = empty("ExternalTest_HP")
    lp_root = empty("ExternalTest_LP")
    hp_inside = cube("Inside_HP", hp_root)
    lp_inside = cube("Inside_LP", lp_root)
    outside_hp = cube("Outside_HP")
    outside_lp = cube("Outside_LP")
    artist_collections = {
        outside_hp: tuple(outside_hp.users_collection),
        outside_lp: tuple(outside_lp.users_collection),
    }

    state = bpy.context.scene.bake_tools_settings
    pair = state.pairs.add()
    pair.item_id = "external-pair"
    pair.name = "External Chapter"
    pair.hp_root = hp_root
    pair.lp_root = lp_root
    pair.hp_root_kind = "OBJECT"
    pair.lp_root_kind = "OBJECT"
    state.active_pair_id = pair.item_id
    subgroup = pair.subgroups.add()
    subgroup.item_id = "external-subgroup"
    subgroup.name = "Bolts"

    verify_qt_role_routing(state, subgroup, outside_hp)

    # Existing root members still classify without a side hint.
    select_only(hp_inside, lp_inside)
    assert "FINISHED" in bpy.ops.bake_tools.subgroup_action(
        action="ADD_SELECTED", subgroup_id=subgroup.item_id
    )

    # Qt supplies the only-visible side for outside members.
    select_only(outside_hp)
    assert "FINISHED" in bpy.ops.bake_tools.subgroup_action(
        action="ADD_SELECTED", subgroup_id=subgroup.item_id, value="HP"
    )
    select_only(outside_lp)
    assert "FINISHED" in bpy.ops.bake_tools.subgroup_action(
        action="ADD_SELECTED", subgroup_id=subgroup.item_id, value="LP"
    )

    assert pair.scope_by_members
    assert refs(pair.hp_scope_members) == {hp_inside, outside_hp}
    assert refs(pair.lp_scope_members) == {lp_inside, outside_lp}
    assert refs(subgroup.hp_members) == {hp_inside, outside_hp}
    assert refs(subgroup.lp_members) == {lp_inside, outside_lp}

    # Export Settings materializes a non-destructive Outliner mirror.
    assert "FINISHED" in bpy.ops.bake_tools.action(action="EXPORT_SETTINGS")
    hp_group = managed("SUBGROUP", pair.item_id, subgroup.item_id, "HP")
    lp_group = managed("SUBGROUP", pair.item_id, subgroup.item_id, "LP")
    assert hp_group is not None and lp_group is not None
    assert set(hp_group.objects) == {hp_inside, outside_hp}
    assert set(lp_group.objects) == {lp_inside, outside_lp}
    assert outside_hp.parent is None and outside_lp.parent is None
    for obj, original in artist_collections.items():
        assert all(collection in obj.users_collection for collection in original)

    # Re-entering Export Settings reconciles moves instead of leaving stale
    # links in the previous subgroup Collection.
    second = pair.subgroups.add()
    second.item_id = "external-subgroup-second"
    second.name = "Moved"
    select_only(outside_hp)
    assert "FINISHED" in bpy.ops.bake_tools.subgroup_action(
        action="ADD_SELECTED", subgroup_id=second.item_id, value="HP"
    )
    assert "FINISHED" in bpy.ops.bake_tools.action(action="EXPORT_SETTINGS")
    assert "FINISHED" in bpy.ops.bake_tools.action(action="EXPORT_SETTINGS")
    moved_hp_group = managed("SUBGROUP", pair.item_id, second.item_id, "HP")
    assert outside_hp not in set(hp_group.objects)
    assert moved_hp_group is not None and outside_hp in set(moved_hp_group.objects)

    print("BAKE_TOOLS_EXTERNAL_SUBGROUP_GROUPING_OK")


if __name__ == "__main__":
    main()
