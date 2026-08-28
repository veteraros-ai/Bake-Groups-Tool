"""GUI regression for hiding the Qt manager outside its opening Workspace."""

from __future__ import annotations

import addon_utils
import bpy


addon_utils.enable("Bake_Tools_Blender", default_set=False, persistent=False)
from Bake_Tools_Blender.addon.bake_tools_blender import qt_window  # noqa: E402


blender_window = bpy.context.window
host_workspace = blender_window.workspace
other_workspace = next(
    (workspace for workspace in bpy.data.workspaces if workspace != host_workspace),
    None,
)
assert other_workspace is not None, "Factory startup must provide another Workspace"

area = next(area for area in blender_window.screen.areas if area.type == "VIEW_3D")
area.spaces.active.show_region_ui = True
manager = qt_window.show_manager(bpy.context)
host_identity = qt_window.blender_workspace_identity()
assert host_identity is not None
assert manager._bt_host_workspace_identity == host_identity


def switch_away():
    blender_window.workspace = other_workspace
    bpy.app.timers.register(verify_away, first_interval=0.35)
    return None


def verify_away():
    qt_window.capture_context()
    current = manager._sync_workspace_visibility()
    assert current is not None and current[0] != host_identity[0]
    assert "workspace" in manager._bt_suppression_reasons
    assert manager._bt_native_suppressed and manager.isVisible()
    blender_window.workspace = host_workspace
    bpy.app.timers.register(verify_back, first_interval=0.35)
    return None


def verify_back():
    qt_window.capture_context()
    current = manager._sync_workspace_visibility()
    assert current is not None and current[0] == host_identity[0]
    assert "workspace" not in manager._bt_suppression_reasons
    # Another composable reason (for example a non-Bake-Tools Sidebar tab) may
    # still suppress the HWND; the Workspace reason itself must be gone.
    print("BAKE_TOOLS_WORKSPACE_VISIBILITY_OK hide=1 restore=1")
    qt_window.shutdown_manager()
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(switch_away, first_interval=1.0)
