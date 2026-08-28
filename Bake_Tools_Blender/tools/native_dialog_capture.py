"""GUI capture proving that Create Pair opens Blender's native name dialog."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy


ADDON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_ROOT.parent))

import Bake_Tools_Blender as addon  # noqa: E402


def make_root(name):
    obj = bpy.data.objects.new(name, None)
    bpy.context.collection.objects.link(obj)
    return obj


def view3d_override():
    window = bpy.context.window_manager.windows[0]
    area = next(area for area in window.screen.areas if area.type == "VIEW_3D")
    area.spaces.active.show_region_ui = True
    region = next(region for region in area.regions if region.type == "WINDOW")
    return {"window": window, "screen": window.screen, "area": area, "region": region}


addon.register()
hp_root = make_root("SciFiGun_HP")
lp_root = make_root("SciFiWeapon_LP")
bpy.context.view_layer.objects.active = hp_root
hp_root.select_set(True)
bpy.ops.bake_tools.pick_object(role="HP")
hp_root.select_set(False)
bpy.context.view_layer.objects.active = lp_root
lp_root.select_set(True)
bpy.ops.bake_tools.pick_object(role="LP")

capture_path = ADDON_ROOT / "docs" / "screenshots" / "native_name_dialog.png"


def capture_dialog():
    with bpy.context.temp_override(**view3d_override()):
        bpy.ops.screen.screenshot(filepath=str(capture_path))
    assert capture_path.is_file()
    print("BAKE_TOOLS_NATIVE_NAME_DIALOG_OK")
    bpy.ops.wm.quit_blender()
    return None


def open_dialog():
    with bpy.context.temp_override(**view3d_override()):
        result = bpy.ops.bake_tools.create_pair("INVOKE_DEFAULT")
    assert "RUNNING_MODAL" in result
    bpy.app.timers.register(capture_dialog, first_interval=0.75)
    return None


bpy.app.timers.register(open_dialog, first_interval=0.50)
