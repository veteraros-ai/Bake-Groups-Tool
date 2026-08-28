"""Regression test for Color HP, Keep HP and explicit manager startup."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy


ADDON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_ROOT.parent))

import Bake_Tools_Blender as addon  # noqa: E402


def empty(name, parent=None):
    obj = bpy.data.objects.new(name, None)
    bpy.context.collection.objects.link(obj)
    obj.parent = parent
    return obj


def cube(name, parent=None):
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.active_object
    obj.name = name
    obj.parent = parent
    return obj


def pick(obj, role):
    for selected in bpy.context.selected_objects:
        selected.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    from Bake_Tools_Blender.addon.bake_tools_blender.blender_bridge import capture_context

    capture_context()
    assert "FINISHED" in bpy.ops.bake_tools.pick_object(role=role)


def main():
    addon.register()
    from Bake_Tools_Blender.addon.bake_tools_blender import qt_window

    # Registering the add-on or loading a scene must not create the Qt manager.
    assert not qt_window.manager_is_visible()

    hp_root = empty("Weapon_HP")
    lp_root = empty("Weapon_LP")
    body_group = empty("Body_HP", hp_root)
    bolts_group = empty("Bolts_HP", hp_root)
    body_a = cube("Body_A", body_group)
    body_b = cube("Body_B", body_group)
    bolt = cube("Bolt_A", bolts_group)
    lp_mesh = cube("Weapon_Low", lp_root)

    pick(hp_root, "HP")
    pick(lp_root, "LP")
    assert "FINISHED" in bpy.ops.bake_tools.create_pair()
    state = bpy.context.scene.bake_tools_settings
    state.keep_hp_structure = True
    assert "FINISHED" in bpy.ops.bake_tools.analyze_hp()

    pair = state.pairs[state.active_pair]
    groups = {group.name: group for group in pair.subgroups}
    assert set(groups) == {"Body", "Bolts"}
    assert groups["Body"].hp_count == 2
    assert groups["Bolts"].hp_count == 1
    assert not groups["Body"].lp_members and not groups["Bolts"].lp_members

    originals = {obj.name: tuple(obj.color) for obj in (body_a, body_b, bolt, lp_mesh)}
    view_modes = []
    screens = {window.screen for window in bpy.context.window_manager.windows}
    if not screens:
        screens = tuple(bpy.data.screens)
    for screen in screens:
        for area in screen.areas:
            if area.type == "VIEW_3D":
                space = area.spaces.active
                view_modes.append((space, space.shading.type, space.shading.color_type))
    assert "FINISHED" in bpy.ops.bake_tools.set_setting(setting="color_subgroups", value="1")
    assert tuple(body_a.color) == tuple(body_b.color)
    assert tuple(body_a.color) != tuple(bolt.color)
    assert tuple(lp_mesh.color) == originals[lp_mesh.name]
    assert body_a.get("_bake_tools_color_preview") is True
    for space, _shading_type, _color_type in view_modes:
        assert space.shading.type == "SOLID"
        assert space.shading.color_type == "OBJECT"

    assert "FINISHED" in bpy.ops.bake_tools.set_setting(setting="color_subgroups", value="0")
    for obj in (body_a, body_b, bolt, lp_mesh):
        assert tuple(obj.color) == originals[obj.name]
    assert "_bake_tools_color_preview" not in body_a
    for space, shading_type, color_type in view_modes:
        assert space.shading.type == shading_type
        assert space.shading.color_type == color_type

    addon.unregister()
    print("BAKE_TOOLS_COLOR_KEEP_OK")


if __name__ == "__main__":
    main()
