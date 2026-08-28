"""Live HWND regression for transparent Close -> Open and Sidebar placement."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

import addon_utils
import bpy


addon_utils.enable("Bake_Tools_Blender", default_set=False, persistent=False)

from Bake_Tools_Blender.addon.bake_tools_blender import blender_bridge, qt_window  # noqa: E402
from Bake_Tools_Blender.addon.bake_tools_blender.blender_bridge import (  # noqa: E402
    blender_sidebar_rect,
    blender_view3d_header_rect,
    qt_window_rect,
    set_qt_window_suppressed,
)


for screen in bpy.data.screens:
    for area in screen.areas:
        if area.type == "VIEW_3D":
            area.spaces.active.show_region_ui = True

original_category = qt_window.blender_sidebar_category
qt_window.blender_sidebar_category = lambda: "Bake Tools"
window = qt_window.show_manager()
assert blender_bridge._native_popup_hook


def verify_reopen():
    try:
        manager = wintypes.HWND(int(window.winId()))
        sidebar = blender_sidebar_rect(window)
        header = blender_view3d_header_rect(window)
        before = qt_window_rect(window)
        assert sidebar is not None and header is not None and before is not None

        # Reproduce the old defect exactly: a Blender popover had left the
        # reusable native widget layered, alpha=0 and click-through.
        set_qt_window_suppressed(window, "fixture_popover", True)
        style = int(ctypes.windll.user32.GetWindowLongW(manager, -20))
        assert window._bt_native_suppressed
        assert style & 0x00080000 and style & 0x00000020
        window._bt_header_popup_guard = True
        window._bt_header_popup_expire = 999999.0

        qt_window.hide_manager()
        assert not blender_bridge._native_popup_hook
        assert not window._bt_native_suppressed
        assert window._bt_suppression_reasons == set()
        assert not window._bt_header_popup_guard

        reopened = qt_window.show_manager()
        assert blender_bridge._native_popup_hook
        reopened._sync_pseudo_dock()
        after = qt_window_rect(reopened)
        assert reopened is window and reopened.isVisible()
        assert not reopened._bt_native_suppressed
        assert reopened._bt_suppression_reasons == set()
        assert after is not None
        assert abs(after[0] - (sidebar[0] + 8)) <= 4
        assert abs(after[2] - (sidebar[2] - 28)) <= 4
        assert after[1] >= sidebar[1] + 44
        assert header[3] <= sidebar[3]
        print(
            "BAKE_TOOLS_WINDOW_REOPEN_GUI_OK visible=1 alpha_reset=1 "
            "guard_reset=1 dock_without_owner_move=1"
        )
    finally:
        qt_window.blender_sidebar_category = original_category
        qt_window.shutdown_manager()
        bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(verify_reopen, first_interval=1.5)
