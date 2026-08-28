"""Regression for hidden native alpha followed by owner monitor movement."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

import addon_utils
import bpy


addon_utils.enable("Bake_Tools_Blender", default_set=False, persistent=False)

from Bake_Tools_Blender.addon.bake_tools_blender import blender_bridge, qt_window  # noqa: E402


user32 = ctypes.windll.user32
user32.SetWindowPos.argtypes = (
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.UINT,
)
user32.SetWindowPos.restype = wintypes.BOOL
user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
user32.GetWindowLongW.restype = ctypes.c_long
user32.GetLayeredWindowAttributes.argtypes = (
    wintypes.HWND, ctypes.POINTER(wintypes.COLORREF),
    ctypes.POINTER(wintypes.BYTE), ctypes.POINTER(wintypes.DWORD),
)
user32.GetLayeredWindowAttributes.restype = wintypes.BOOL


for obj in tuple(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
assert len(bpy.context.scene.objects) == 0
for screen in bpy.data.screens:
    for area in screen.areas:
        if area.type == "VIEW_3D":
            area.spaces.active.show_region_ui = True
view3d_area = next(area for area in bpy.context.window.screen.areas if area.type == "VIEW_3D")
view3d_region = next(region for region in view3d_area.regions if region.type == "WINDOW")

original_category = qt_window.blender_sidebar_category
qt_window.blender_sidebar_category = lambda: "Bake Tools"
window = qt_window.show_manager()
owner = int(blender_bridge.blender_window_handle(window))
manager = int(window.winId())
owner_original = blender_bridge.blender_window_rect(window)
preexisting_popup = {"active": False}


def _native_visibility():
    exstyle = int(user32.GetWindowLongW(wintypes.HWND(manager), -20)) & 0xFFFFFFFF
    alpha = wintypes.BYTE(255)
    color = wintypes.COLORREF()
    flags = wintypes.DWORD()
    have_alpha = bool(user32.GetLayeredWindowAttributes(
        wintypes.HWND(manager), ctypes.byref(color), ctypes.byref(alpha), ctypes.byref(flags)
    ))
    return exstyle, int(alpha.value) if have_alpha else 255


def _monitor_work_areas():
    class MONITORINFO(ctypes.Structure):
        _fields_ = (
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
        )

    result = []
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
        ctypes.POINTER(wintypes.RECT), wintypes.LPARAM,
    )

    def collect(monitor, _dc, _rect, _data):
        info = MONITORINFO(); info.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            work = info.rcWork
            result.append((work.left, work.top, work.right, work.bottom))
        return True

    callback = callback_type(collect)
    user32.EnumDisplayMonitors(None, None, callback, 0)
    return result


def reproduce():
    # Reproduce the state left by closing while a Blender popup suppressed the
    # owned manager. The manager must be physically restored, not only marked so
    # in Python.
    blender_bridge.set_qt_window_suppressed(window, "fixture_popup", True)
    before_hide = _native_visibility()
    assert before_hide[0] & 0x00080000 and before_hide[0] & 0x00000020
    assert before_hide[1] == 0
    qt_window.hide_manager()
    after_hide = _native_visibility()
    assert not (after_hide[0] & 0x00000020), after_hide
    assert after_hide[1] == 255, after_hide

    # Open a real Blender menu while the manager is closed. The next timer runs
    # only after Blender has created and painted its TEMPORARY region.
    with bpy.context.temp_override(
        window=bpy.context.window, screen=bpy.context.window.screen,
        area=view3d_area, region=view3d_region,
    ):
        bpy.ops.wm.call_menu(name="VIEW3D_MT_view")
    return None


def move_and_reopen():
    preexisting_popup["active"] = blender_bridge.blender_temporary_ui_active()
    left, top, right, bottom = owner_original
    width, height = right - left, bottom - top
    work_areas = _monitor_work_areas()
    target = next(
        (rect for rect in work_areas if not (rect[0] <= left < rect[2] and rect[1] <= top < rect[3])),
        None,
    )
    if target is None:
        target = (left + 120, top + 80, right + 120, bottom + 80)
    move_left, move_top = target[0] + 40, target[1] + 40
    user32.SetWindowPos(
        wintypes.HWND(owner), None, move_left, move_top, width, height,
        0x0004 | 0x0010,
    )  # NOZORDER | NOACTIVATE

    # Moving the owner normally closes the menu, but its TEMPORARY region can
    # remain visible to Blender Python until a later redraw. Open immediately,
    # before Escape is dispatched, to preserve that exact bootstrap state.
    user32.PostMessageW(wintypes.HWND(owner), 0x0100, 0x1B, 0)
    user32.PostMessageW(wintypes.HWND(owner), 0x0101, 0x1B, 0)
    qt_window.show_manager()
    return None


def verify():
    try:
        exstyle, alpha = _native_visibility()
        manager_rect = blender_bridge.qt_window_rect(window)
        sidebar_rect = blender_bridge.blender_sidebar_rect(window)
        owner_rect = blender_bridge.blender_window_rect(window)
        print(
            "BAKE_TOOLS_CLOSED_MONITOR_REOPEN_STATE objects=0 exstyle={:#x} alpha={} "
            "manager={} sidebar={} owner={} reasons={} preexisting_popup={}".format(
                exstyle, alpha, manager_rect, sidebar_rect, owner_rect,
                sorted(getattr(window, "_bt_suppression_reasons", set())),
                preexisting_popup["active"],
            )
        )
        assert window.isVisible()
        assert not (exstyle & 0x00000020)
        assert alpha == 255
        assert not window._bt_native_suppressed
        assert window._bt_suppression_reasons == set()
        assert manager_rect is not None and sidebar_rect is not None and owner_rect is not None
        assert abs(manager_rect[0] - (sidebar_rect[0] + 8)) <= 4
        assert abs(manager_rect[2] - (sidebar_rect[2] - 28)) <= 4
        assert owner_rect[0] <= manager_rect[0] < manager_rect[2] <= owner_rect[2]
        print("BAKE_TOOLS_CLOSED_MENU_MONITOR_REOPEN_OK")
    finally:
        qt_window.blender_sidebar_category = original_category
        if owner_original is not None:
            left, top, right, bottom = owner_original
            user32.SetWindowPos(
                wintypes.HWND(owner), None, left, top, right - left, bottom - top,
                0x0004 | 0x0010,
            )
        qt_window.shutdown_manager()
        bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(reproduce, first_interval=1.0)
bpy.app.timers.register(move_and_reopen, first_interval=1.8)
bpy.app.timers.register(verify, first_interval=3.6)
