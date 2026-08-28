"""Regression for popup dismissal followed immediately by viewport orbit."""

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
user32.GetLayeredWindowAttributes.argtypes = (
    wintypes.HWND,
    ctypes.POINTER(wintypes.COLORREF),
    ctypes.POINTER(wintypes.BYTE),
    ctypes.POINTER(wintypes.DWORD),
)
user32.GetLayeredWindowAttributes.restype = wintypes.BOOL
user32.PostMessageW.argtypes = (
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
)
user32.PostMessageW.restype = wintypes.BOOL

manager = wintypes.HWND(int(window.winId()))
owner = wintypes.HWND(blender_bridge.blender_window_handle(window))
assert owner and manager and blender_bridge._native_popup_hook
probe = {
    "hidden": False,
    "restored": False,
    "elapsed": None,
    "state": {},
    "native_restore_count": 0,
}


def _alpha():
    color = wintypes.COLORREF()
    alpha = wintypes.BYTE(255)
    flags = wintypes.DWORD()
    if user32.GetLayeredWindowAttributes(
        manager, ctypes.byref(color), ctypes.byref(alpha), ctypes.byref(flags)
    ):
        return int(alpha.value)
    return 255


def _exstyle():
    return int(user32.GetWindowLongW(manager, -20)) & 0xFFFFFFFF


def native_probe_and_orbit():
    style = _exstyle()
    probe["hidden"] = bool(style & 0x00080000 and style & 0x00000020)
    probe["state"] = dict(blender_bridge._native_popup_hook_state)
    started = time.monotonic()
    x, y = 120, 260
    packed = (y << 16) | x
    user32.PostMessageW(owner, 0x0207, 0x0010, packed)  # WM_MBUTTONDOWN
    user32.PostMessageW(owner, 0x0200, 0x0010, packed + 1)  # WM_MOUSEMOVE
    user32.PostMessageW(owner, 0x0208, 0, packed + 1)  # WM_MBUTTONUP

    def read_restored_alpha():
        probe["restored"] = not (_exstyle() & 0x00000020) and _alpha() == 255
        probe["elapsed"] = time.monotonic() - started
        probe["native_restore_count"] = int(
            blender_bridge._native_popup_hook_state.get("native_restore_count", 0)
        )

    threading.Timer(0.08, read_restored_alpha).start()


def trigger_popup_guard():
    x, y = 80, 220
    packed = (y << 16) | x
    user32.PostMessageW(owner, 0x0204, 0x0002, packed)  # WM_RBUTTONDOWN
    user32.PostMessageW(owner, 0x0205, 0, packed)  # WM_RBUTTONUP
    threading.Timer(0.25, native_probe_and_orbit).start()
    return None


deadline = time.monotonic() + 8.0


def verify():
    if probe["elapsed"] is None and time.monotonic() < deadline:
        return 0.05
    try:
        assert probe["hidden"], "Native popup guard did not hide the manager"
        assert probe["restored"], "MMB viewport drag did not restore manager alpha"
        assert probe["elapsed"] < 0.5, probe
        assert probe["native_restore_count"] >= 1, probe
        blender_bridge.sync_native_popup_guard(window, popup_visible=False)
        assert not window._bt_native_suppressed
        assert "native_input_guard" not in window._bt_suppression_reasons
        print(
            "BAKE_TOOLS_POPUP_ORBIT_RESTORE_OK hidden=1 restored=1 "
            "elapsed={:.3f}".format(probe["elapsed"])
        )
    finally:
        qt_window.shutdown_manager()
        bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(trigger_popup_guard, first_interval=1.0)
bpy.app.timers.register(verify, first_interval=1.8)
