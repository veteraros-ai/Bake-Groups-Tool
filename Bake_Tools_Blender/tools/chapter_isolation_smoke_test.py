"""Regression test for Maya-style chapter isolation and Qt subgroup cleanup."""

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


def cube(name, parent):
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.active_object
    obj.name = name
    obj.parent = parent
    return obj


def select_only(obj):
    for selected in tuple(bpy.context.selected_objects):
        selected.select_set(False)
    obj.hide_viewport = False
    obj.hide_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def pick(obj, role):
    select_only(obj)
    from Bake_Tools_Blender.addon.bake_tools_blender.blender_bridge import capture_context

    capture_context()
    assert "FINISHED" in bpy.ops.bake_tools.pick_object(role=role)


def create_pair(hp_root, lp_root):
    pick(hp_root, "HP")
    pick(lp_root, "LP")
    assert "FINISHED" in bpy.ops.bake_tools.create_pair()
    return bpy.context.scene.bake_tools_settings.pairs[-1]


def visible(obj):
    return not obj.hide_viewport and not obj.hide_get()


def main():
    addon.register()
    state = bpy.context.scene.bake_tools_settings

    hp_a = empty("ChapterA_HP")
    lp_a = empty("ChapterA_LP")
    mesh_a = cube("ChapterA_High", hp_a)
    cube("ChapterA_Low", lp_a)
    pair_a = create_pair(hp_a, lp_a)

    select_only(mesh_a)
    assert "FINISHED" in bpy.ops.bake_tools.add_subgroup(name="A_Only")
    assert len(pair_a.subgroups) == 1

    hp_b = empty("ChapterB_HP")
    lp_b = empty("ChapterB_LP")
    mesh_b = cube("ChapterB_High", hp_b)
    cube("ChapterB_Low", lp_b)
    pair_b = create_pair(hp_b, lp_b)
    assert len(pair_b.subgroups) == 0

    # Switching always isolates the newly active chapter, as in Maya.
    assert "FINISHED" in bpy.ops.bake_tools.pair_action(action="ACTIVATE", pair_id=pair_a.item_id)
    assert state.chapter_isolated and visible(mesh_a) and not visible(mesh_b)
    assert "FINISHED" in bpy.ops.bake_tools.pair_action(action="ACTIVATE", pair_id=pair_b.item_id)
    assert state.chapter_isolated and visible(mesh_b) and not visible(mesh_a)
    assert state.active_subgroup == 0 and len(pair_b.subgroups) == 0

    # Clicking the active chapter again releases isolation; another switch
    # enables it again, matching the original activate_root contract.
    assert "FINISHED" in bpy.ops.bake_tools.pair_action(action="ACTIVATE", pair_id=pair_b.item_id)
    assert not state.chapter_isolated and visible(mesh_a) and visible(mesh_b)
    assert "FINISHED" in bpy.ops.bake_tools.pair_action(action="ACTIVATE", pair_id=pair_a.item_id)
    assert state.chapter_isolated and visible(mesh_a) and not visible(mesh_b)

    # Render A then B through the same Qt layout. Old subgroup rows must be
    # detached immediately instead of remaining over the empty B chapter.
    from Bake_Tools_Blender.addon.bake_tools_blender import qt_window

    app = qt_window.QtWidgets.QApplication.instance() or qt_window.QtWidgets.QApplication(["BakeToolsIsolation"])
    window = qt_window.BakeToolsWindow()
    window.refresh_from_store(force=True)
    assert any(button.text() == "A_Only" for button in window.findChildren(qt_window.SubgroupNameButton))
    assert "FINISHED" in bpy.ops.bake_tools.pair_action(action="ACTIVATE", pair_id=pair_b.item_id)
    window.refresh_from_store(force=True)
    assert not window.findChildren(qt_window.SubgroupNameButton)
    assert not any(
        label.text() == "No active bake group"
        for label in window.findChildren(qt_window.QtWidgets.QLabel)
    )
    window.close()
    app.processEvents()

    addon.unregister()
    print("BAKE_TOOLS_CHAPTER_ISOLATION_OK")


if __name__ == "__main__":
    main()
