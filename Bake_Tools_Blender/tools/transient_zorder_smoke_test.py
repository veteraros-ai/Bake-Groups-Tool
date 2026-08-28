"""Verify that a Blender-owned native transient is promoted above the manager."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

import addon_utils
import bpy


addon_utils.enable("Bake_Tools_Blender", default_set=False, persistent=False)

from Bake_Tools_Blender.addon.bake_tools_blender.blender_bridge import (  # noqa: E402
    blender_window_handle,
    sync_blender_transient_z_order,
)
from Bake_Tools_Blender.addon.bake_tools_blender.qt_window import show_manager, shutdown_manager  # noqa: E402


window = show_manager()
user32 = ctypes.windll.user32
user32.CreateWindowExW.argtypes = (
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
)
user32.CreateWindowExW.restype = wintypes.HWND
owner = blender_window_handle(window)
manager = int(window.winId())
transient = int(user32.CreateWindowExW(
    0x00000080, "STATIC", "Blender transient smoke", 0x80000000 | 0x10000000 | 0x00800000,
    100, 100, 260, 80, wintypes.HWND(owner), None, None, None,
) or 0)
assert transient
# Put the fixture below the Qt overlay to reproduce the reported ordering.
user32.SetWindowPos(wintypes.HWND(transient), wintypes.HWND(1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)


def z_order():
    result = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    callback = callback_type(lambda hwnd, _param: result.append(int(hwnd)) or True)
    user32.EnumWindows(callback, 0)
    return result


def verify():
    before = z_order()
    status = sync_blender_transient_z_order(window)
    after = z_order()
    assert transient in after and manager in after
    assert after.index(transient) < after.index(manager), (before, after, status)
    assert not status["suppressed"]
    user32.DestroyWindow(wintypes.HWND(transient))
    # Exercise the in-client modal fallback without hiding/stopping Qt.
    user32.EnableWindow(wintypes.HWND(owner), False)
    suppressed = sync_blender_transient_z_order(window)
    assert suppressed["suppressed"] and window.isVisible()
    user32.EnableWindow(wintypes.HWND(owner), True)
    restored = sync_blender_transient_z_order(window)
    assert not restored["suppressed"] and window.isVisible()
    print("BAKE_TOOLS_TRANSIENT_ZORDER_OK transient={} fallback={}".format(status, suppressed))
    shutdown_manager()
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(verify, first_interval=1.0)
