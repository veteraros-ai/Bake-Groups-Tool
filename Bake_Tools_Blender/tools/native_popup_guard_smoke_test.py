"""Live Windows regression for pre-dispatch Blender popup suppression."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import threading
import time

import addon_utils
import bpy


addon_utils.enable("Bake_Tools_Blender", default_set=False, persistent=False)
from Bake_Tools_Blender.addon.bake_tools_blender import blender_bridge, qt_window  # noqa: E402


qt_window.blender_sidebar_category = lambda: "Bake Tools"
window = qt_window.show_manager()
user32 = ctypes.windll.user32
user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
user32.GetWindowLongW.restype = ctypes.c_long
user32.PostMessageW.argtypes = (
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
)
user32.PostMessageW.restype = wintypes.BOOL
manager = wintypes.HWND(int(window.winId()))
owner = wintypes.HWND(blender_bridge.blender_window_handle(window))
assert owner and manager and blender_bridge._native_popup_hook
probe = {"hidden": False}


def native_probe_and_close():
    style = int(user32.GetWindowLongW(manager, -20))
    probe["hidden"] = bool(style & 0x00080000 and style & 0x00000020)
    user32.PostMessageW(owner, 0x0100, 0x1B, 0)  # WM_KEYDOWN Escape
    user32.PostMessageW(owner, 0x0101, 0x1B, 0)


def trigger():
    # A right-click in the main client reproduces an in-GHOST context menu.
    x, y = 80, 220
    packed = (y << 16) | x
    user32.PostMessageW(owner, 0x0204, 0x0002, packed)  # WM_RBUTTONDOWN
    user32.PostMessageW(owner, 0x0205, 0, packed)
    threading.Timer(0.20, native_probe_and_close).start()
    return None


deadline = time.monotonic() + 8.0


def verify():
    if not probe["hidden"] and time.monotonic() < deadline:
        return 0.10
    try:
        assert probe["hidden"], "Manager was not suppressed before Blender painted its popup"
        assert not window._bt_native_suppressed
        assert "native_input_guard" not in window._bt_suppression_reasons
        print("BAKE_TOOLS_NATIVE_POPUP_GUARD_OK predispatch=1 restored=1")
    finally:
        qt_window.shutdown_manager()
        bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(trigger, first_interval=1.0)
bpy.app.timers.register(verify, first_interval=2.0)
