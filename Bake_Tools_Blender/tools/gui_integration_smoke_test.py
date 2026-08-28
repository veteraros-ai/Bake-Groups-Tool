"""GUI test for the non-blocking frameless owned Sidebar overlay."""

from __future__ import annotations

import addon_utils
import bpy


addon_utils.enable("Bake_Tools_Blender", default_set=False, persistent=False)

from Bake_Tools_Blender.addon.bake_tools_blender.blender_bridge import (  # noqa: E402
    blender_sidebar_rect,
    blender_window_rect,
    qt_window_has_blender_owner,
    qt_window_is_embedded,
    qt_window_rect,
)
from Bake_Tools_Blender.addon.bake_tools_blender.qt_window import (  # noqa: E402
    QtCore,
    manager_is_visible,
    show_manager,
    shutdown_manager,
)
from Bake_Tools_Blender.addon.bake_tools_blender.qt_window_manager import (  # noqa: E402
    window_manager,
)


for screen in bpy.data.screens:
    for area in screen.areas:
        if area.type == "VIEW_3D":
            area.spaces.active.show_region_ui = True

window = show_manager()
assert show_manager() is window
assert window_manager.primary_window() is window
assert window_manager.get("BakeToolsBlenderWindow") is window
window._set_pseudo_docked(True)
window._sync_pseudo_dock()
test_dialog = window._warning("Non-blocking test", "Blender timers must continue while this is open.")


def finish_test():
    owner_rect = blender_window_rect(window)
    sidebar_rect = blender_sidebar_rect(window)
    manager_rect = qt_window_rect(window)
    print(
        "BAKE_TOOLS_GUI_STATE owner={} native_child={} manager={} owner_rect={} sidebar_rect={}".format(
            qt_window_has_blender_owner(window), qt_window_is_embedded(window),
            manager_rect, owner_rect, sidebar_rect,
        )
    )
    try:
        assert window.isVisible()
        assert qt_window_has_blender_owner(window)
        assert not qt_window_is_embedded(window)
        assert not (window.windowFlags() & QtCore.Qt.WindowType.WindowStaysOnTopHint)
        assert window.windowFlags() & QtCore.Qt.WindowType.FramelessWindowHint
        assert window.windowModality() == QtCore.Qt.WindowModality.NonModal
        assert test_dialog.isVisible()
        assert test_dialog.windowModality() == QtCore.Qt.WindowModality.NonModal
        assert owner_rect is not None
        assert sidebar_rect is not None
        assert manager_rect is not None
        # The manager occupies only Sidebar content and leaves the native left
        # resize boundary, vertical tab strip and Close row accessible.
        assert abs(manager_rect[0] - (sidebar_rect[0] + 8)) <= 4
        assert abs(manager_rect[2] - (sidebar_rect[2] - 28)) <= 4
        assert manager_rect[1] >= sidebar_rect[1] + 44
        assert abs((manager_rect[2] - manager_rect[0]) - ((sidebar_rect[2] - sidebar_rect[0]) - 36)) <= 8
        assert owner_rect[0] <= manager_rect[0] < manager_rect[2] <= owner_rect[2]
        assert owner_rect[1] <= manager_rect[1] < manager_rect[3] <= owner_rect[3]
        assert "FINISHED" in bpy.ops.bake_tools.hide_manager()
        assert not manager_is_visible()
        print("BAKE_TOOLS_GUI_INTEGRATION_OK")
    finally:
        shutdown_manager()
        bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(finish_test, first_interval=2.0)
