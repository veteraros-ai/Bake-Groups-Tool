"""Interactive Win32 diagnostics for the owned Qt manager."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

import addon_utils
import bpy


addon_utils.enable("Bake_Tools_Blender", default_set=False, persistent=False)

from Bake_Tools_Blender.addon.bake_tools_blender.blender_bridge import (  # noqa: E402
    blender_window_handle,
    blender_window_in_move_size,
    blender_window_rect,
    qt_window_is_embedded,
    qt_window_rect,
)
from Bake_Tools_Blender.addon.bake_tools_blender import qt_window as qt_module  # noqa: E402
from Bake_Tools_Blender.addon.bake_tools_blender.qt_window import (  # noqa: E402
    manager_is_visible,
    show_manager,
    shutdown_manager,
)


for screen in bpy.data.screens:
    for area in screen.areas:
        if area.type == "VIEW_3D":
            area.spaces.active.show_region_ui = True
window = show_manager()
user32 = ctypes.windll.user32
manager_hwnd = int(window.winId())
owner_hwnd = blender_window_handle(window)
original = blender_window_rect(window)
manager_original = qt_window_rect(window)


def diagnose():
    style = user32.GetWindowLongW(wintypes.HWND(manager_hwnd), -16)
    exstyle = user32.GetWindowLongW(wintypes.HWND(manager_hwnd), -20)
    print("BAKE_TOOLS_NATIVE manager={} owner={} owner_enabled={} modality={} embedded={} style={:#x} exstyle={:#x}".format(
        manager_hwnd, owner_hwnd, bool(user32.IsWindowEnabled(wintypes.HWND(owner_hwnd))),
        int(window.windowModality().value), qt_window_is_embedded(window),
        style & 0xFFFFFFFF, exstyle & 0xFFFFFFFF,
    ))
    assert not blender_window_in_move_size(window)
    left, top, right, bottom = original
    user32.SetWindowPos(
        wintypes.HWND(owner_hwnd), None, left + 80, top + 60,
        max(760, right - left - 120), max(620, bottom - top - 100), 0x0004,
    )
    return None


def verify():
    changed = blender_window_rect(window)
    manager_changed = qt_window_rect(window)
    print("BAKE_TOOLS_OWNER_MOVE original={} changed={} manager_original={} manager_changed={} enabled={}".format(
        original, changed, manager_original, manager_changed,
        bool(user32.IsWindowEnabled(wintypes.HWND(owner_hwnd)))
    ))
    assert changed != original
    assert user32.IsWindowEnabled(wintypes.HWND(owner_hwnd))
    assert manager_changed != manager_original
    assert changed[0] <= manager_changed[0] < manager_changed[2] <= changed[2]
    assert changed[1] <= manager_changed[1] < manager_changed[3] <= changed[3]
    # Repeating a dock sync with unchanged geometry must be a no-op. During a
    # detected modal move/resize loop it must also be skipped completely.
    last_applied = window._last_applied_dock_rect
    window._sync_pseudo_dock()
    assert window._last_applied_dock_rect == last_applied
    before_guard = qt_window_rect(window)
    left, top, right, bottom = changed
    user32.SetWindowPos(
        wintypes.HWND(owner_hwnd), None, left + 30, top + 20,
        right - left, bottom - top, 0x0004,
    )
    guard = qt_module.blender_window_in_move_size
    try:
        qt_module.blender_window_in_move_size = lambda _window: True
        window._sync_pseudo_dock()
        assert qt_window_rect(window) == before_guard
    finally:
        qt_module.blender_window_in_move_size = guard
    window._sync_pseudo_dock()
    assert qt_window_rect(window) != before_guard
    assert "FINISHED" in bpy.ops.bake_tools.hide_manager()
    assert not manager_is_visible()
    assert "FINISHED" in bpy.ops.bake_tools.show_manager()
    assert manager_is_visible() and not qt_window_is_embedded(window)
    user32.SetWindowPos(
        wintypes.HWND(owner_hwnd), None, original[0], original[1],
        original[2] - original[0], original[3] - original[1], 0x0004,
    )
    shutdown_manager()
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(diagnose, first_interval=1.0)
bpy.app.timers.register(verify, first_interval=2.5)
