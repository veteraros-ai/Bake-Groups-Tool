"""Verify that a native WM_CLOSE exits Blender while the manager is visible."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

import addon_utils
import bpy


addon_utils.enable("Bake_Tools_Blender", default_set=False, persistent=False)

from Bake_Tools_Blender.addon.bake_tools_blender.blender_bridge import blender_window_handle  # noqa: E402
from Bake_Tools_Blender.addon.bake_tools_blender.qt_window import show_manager  # noqa: E402


for screen in bpy.data.screens:
    for area in screen.areas:
        if area.type == "VIEW_3D":
            area.spaces.active.show_region_ui = True

window = show_manager()


def close_blender_normally():
    owner = blender_window_handle(window)
    assert owner
    assert ctypes.windll.user32.IsWindowEnabled(wintypes.HWND(owner))
    print("BAKE_TOOLS_POSTING_NATIVE_WM_CLOSE")
    ctypes.windll.user32.PostMessageW(wintypes.HWND(owner), 0x0010, 0, 0)  # WM_CLOSE
    return None


bpy.app.timers.register(close_blender_normally, first_interval=2.0)
