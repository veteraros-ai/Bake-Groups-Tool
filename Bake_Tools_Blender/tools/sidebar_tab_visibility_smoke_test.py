"""GUI verification that the Qt manager follows the active Blender N-tab."""

from __future__ import annotations

import addon_utils
import bpy


addon_utils.enable("Bake_Tools_Blender", default_set=False, persistent=False)
from Bake_Tools_Blender.addon.bake_tools_blender import qt_window  # noqa: E402


area = next(area for area in bpy.context.screen.areas if area.type == "VIEW_3D")
area.spaces.active.show_region_ui = True
region = next(region for region in area.regions if region.type == "UI")
assert isinstance(region.active_panel_category, str)
window = qt_window.show_manager()


def verify():
    original = qt_window.blender_sidebar_category
    qt_window.blender_sidebar_category = lambda: "Bake Tools"
    assert window._sync_sidebar_tab_visibility() == "Bake Tools"
    assert "sidebar_tab" not in getattr(window, "_bt_suppression_reasons", set())
    qt_window.blender_sidebar_category = lambda: "Item"
    assert window._sync_sidebar_tab_visibility() == "Item"
    assert "sidebar_tab" in window._bt_suppression_reasons
    assert window._bt_native_suppressed and window.isVisible()
    qt_window.blender_sidebar_category = lambda: None
    assert window._sync_sidebar_tab_visibility() is None
    assert "sidebar_tab" in window._bt_suppression_reasons
    qt_window.blender_sidebar_category = lambda: "Bake Tools"
    assert window._sync_sidebar_tab_visibility() == "Bake Tools"
    assert "sidebar_tab" not in window._bt_suppression_reasons
    assert not window._bt_native_suppressed and window.isVisible()
    qt_window.reset_qt_window_suppression(window)
    qt_window.blender_sidebar_category = lambda: None
    assert window._sync_sidebar_tab_visibility() is None
    assert "sidebar_tab" not in window._bt_suppression_reasons
    assert not window._bt_native_suppressed and window.isVisible()
    qt_window.blender_sidebar_category = original
    print("BAKE_TOOLS_SIDEBAR_TAB_VISIBILITY_OK")
    qt_window.shutdown_manager(); bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(verify, first_interval=1.0)
