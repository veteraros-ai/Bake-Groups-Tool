"""Headless regression for HP-LP Matcher persistence and subgroup colors."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import addon.bake_tools_blender as addon  # noqa: E402


def cube(name, location, scale, parent):
    bpy.ops.mesh.primitive_cube_add(location=location)
    obj = bpy.context.object
    obj.name = name
    obj.scale = scale
    obj.parent = parent
    return obj


def main():
    addon.register()
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    hp_root = bpy.data.objects.new("HP", None)
    lp_root = bpy.data.objects.new("LP", None)
    bpy.context.scene.collection.objects.link(hp_root)
    bpy.context.scene.collection.objects.link(lp_root)
    cube("PartA_high_001", (-3.0, 0.0, 0.0), (1.0, 1.0, 1.0), hp_root)
    cube("PartA_high_002", (-3.0, 0.0, 0.0), (1.0, 1.0, 1.0), hp_root)
    cube("PartB_high_001", (3.0, 0.0, 0.0), (1.0, 1.0, 1.0), hp_root)
    cube("PartB_high_002", (3.0, 0.0, 0.0), (1.0, 1.0, 1.0), hp_root)
    cube("PartA_LP", (-3.0, 0.0, 0.0), (1.0, 1.0, 1.0), lp_root)
    cube("PartB_LP", (3.0, 0.0, 0.0), (1.0, 1.0, 1.0), lp_root)
    bpy.context.view_layer.update()

    state = bpy.context.scene.bake_tools_settings
    pair = state.pairs.add()
    pair.item_id = "matcher-test"
    pair.name = "Matcher Test"
    pair.hp_root = hp_root
    pair.lp_root = lp_root
    pair.hp_root_kind = pair.lp_root_kind = "OBJECT"
    pair.hp_object = hp_root.name
    pair.lp_object = lp_root.name
    state.active_pair_id = pair.item_id
    state.matcher_mode = "BALANCED"
    state.matcher_min_hp_lp = 2

    result = bpy.ops.bake_tools.action(action="FIND_GROUPS")
    assert "FINISHED" in result, result
    # Maya merges repeated LP transforms with identical topology/volume even
    # when their world positions differ.
    assert len(pair.matcher_clusters) == 1, [item.title for item in pair.matcher_clusters]
    first = pair.matcher_clusters[0]
    bpy.ops.bake_tools.action(action="LINK", value=first.item_id)
    assert first.linked
    linked_name = first.name
    assert "FINISHED" in bpy.ops.bake_tools.analyze_hp()
    subgroup = next(item for item in pair.subgroups if item.name == linked_name)
    assert len(subgroup.hp_members) == 4
    from addon.bake_tools_blender.properties import ensure_state_ids
    ensure_state_ids(state)
    assert subgroup.color_index >= 0
    from addon.bake_tools_blender.store import BlenderStateStore
    snapshot = BlenderStateStore().snapshot()
    assert snapshot.active_chapter.matcher_clusters[0].hp_members
    assert snapshot.active_chapter.subgroups[0].color_index == subgroup.color_index
    state.color_subgroups = True
    from addon.bake_tools_blender.qt_window import BakeToolsWindow, QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["BakeToolsMatcherTest"])
    window = BakeToolsWindow()
    matcher_item = window.match_list.item(0)
    matcher_item.setSelected(True)
    app.processEvents()
    assert window._selected_matcher_ids() == (first.item_id,)
    assert matcher_item.background().color().name() == "#3a5375"
    # A forced periodic/UI refresh rebuilds QListWidgetItems. Ordinary row
    # selection must survive independently of the persistent Link flag.
    window.refresh_from_store(force=True)
    app.processEvents()
    matcher_item = window.match_list.item(0)
    assert matcher_item.isSelected()
    assert window._selected_matcher_ids() == (first.item_id,)
    assert matcher_item.background().color().name() == "#3a5375"
    rows = window.subgroup_body.findChildren(QtWidgets.QFrame, "subgroupColorRow")
    assert rows and "rgba" in rows[0].styleSheet()
    name_buttons = [
        widget for widget in rows[0].findChildren(QtWidgets.QAbstractButton)
        if widget.text() == subgroup.name
    ]
    assert name_buttons and not name_buttons[0].toolTip()
    tooltips = [widget.toolTip() for widget in rows[0].findChildren(QtWidgets.QWidget) if widget.toolTip()]
    assert tooltips  # Eye/Add/Lock/Delete controls retain Maya-style help.
    window.close()
    print("MATCHER_SMOKE_OK", len(pair.matcher_clusters), subgroup.color_index)
    addon.unregister()


if __name__ == "__main__":
    main()
