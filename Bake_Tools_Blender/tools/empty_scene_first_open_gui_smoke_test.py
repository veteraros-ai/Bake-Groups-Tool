"""GUI regression: the manager must open on the first click in an empty scene."""

from __future__ import annotations

import addon_utils
import bpy


addon_utils.enable("Bake_Tools_Blender", default_set=False, persistent=False)

from Bake_Tools_Blender.addon.bake_tools_blender import qt_window  # noqa: E402
from Bake_Tools_Blender.addon.bake_tools_blender.blender_bridge import (  # noqa: E402
    blender_sidebar_rect,
    capture_context,
    qt_window_rect,
)


def _view3d_context():
    window = bpy.context.window
    area = next(item for item in window.screen.areas if item.type == "VIEW_3D")
    area.spaces.active.show_region_ui = True
    region = next(item for item in area.regions if item.type == "UI")
    try:
        region.active_panel_category = "Bake Tools"
    except (AttributeError, RuntimeError, TypeError):
        pass
    return window, area, region


def run_test():
    original_category = qt_window.blender_sidebar_category
    try:
        for obj in tuple(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        assert len(bpy.context.scene.objects) == 0

        window, area, region = _view3d_context()
        with bpy.context.temp_override(
            window=window, screen=window.screen, area=area, region=region
        ):
            capture_context(bpy.context)
            # Blender can return no active category on the very first Sidebar
            # redraw. This was the real-world invisible-first-open failure.
            qt_window.blender_sidebar_category = lambda: None
            result = bpy.ops.bake_tools.show_manager("EXEC_DEFAULT")
        assert "FINISHED" in result

        def verify():
            manager = qt_window._window
            category = qt_window.blender_sidebar_category()
            manager_rect = qt_window_rect(manager) if manager is not None else None
            sidebar_rect = blender_sidebar_rect(manager) if manager is not None else None
            reasons = set(getattr(manager, "_bt_suppression_reasons", set())) if manager else set()
            print(
                "BAKE_TOOLS_EMPTY_FIRST_OPEN_STATE visible={} category={!r} reasons={} manager={} sidebar={}".format(
                    bool(manager and manager.isVisible()), category, sorted(reasons), manager_rect, sidebar_rect
                )
            )
            try:
                assert manager is not None and manager.isVisible()
                assert "sidebar_tab" not in reasons
                assert manager_rect is not None
                assert sidebar_rect is not None
                assert manager_rect[0] >= sidebar_rect[0]
                assert manager_rect[2] <= sidebar_rect[2]
                print("BAKE_TOOLS_EMPTY_SCENE_FIRST_OPEN_OK")
            finally:
                qt_window.blender_sidebar_category = original_category
                qt_window.shutdown_manager()
                bpy.ops.wm.quit_blender()
            return None

        bpy.app.timers.register(verify, first_interval=1.5)
    except Exception:
        qt_window.blender_sidebar_category = original_category
        qt_window.shutdown_manager()
        bpy.ops.wm.quit_blender()
        raise
    return None


bpy.app.timers.register(run_test, first_interval=0.5)
