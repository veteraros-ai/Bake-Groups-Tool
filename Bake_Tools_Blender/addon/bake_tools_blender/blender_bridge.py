"""Keep Object/Collection selection stable across Blender editor contexts."""

from __future__ import annotations

from contextlib import contextmanager
import os
import sys
import time

import bpy

from .qt_window_manager import window_manager


_last_object = None
_last_collection = None
_last_kind = ""
_last_window = None
_last_view3d_area = None
_observed_active_object = None
_observed_active_collection = None
_native_popup_hook = 0
_native_popup_hook_callback = None
_native_popup_hook_state = {}


def _valid_object(value):
    try:
        return value if value is not None and bpy.data.objects.get(value.name) == value else None
    except (ReferenceError, RuntimeError):
        return None


def _valid_collection(value):
    try:
        return value if value is not None and bpy.data.collections.get(value.name) == value else None
    except (ReferenceError, RuntimeError):
        return None


def _selected_ids(context):
    try:
        return tuple(context.selected_ids)
    except (AttributeError, ReferenceError, RuntimeError):
        return ()


def capture_context(context=None, update_kind=True):
    """Remember the latest native Blender selection without clearing good data."""
    global _last_object, _last_collection, _last_kind, _last_window, _last_view3d_area
    global _observed_active_object, _observed_active_collection
    context = context or bpy.context

    try:
        if context.window is not None:
            _last_window = context.window
        if context.area is not None and context.area.type == "VIEW_3D":
            _last_view3d_area = context.area
    except (AttributeError, ReferenceError, RuntimeError):
        pass

    try:
        active_object = context.view_layer.objects.active
    except (AttributeError, ReferenceError, RuntimeError):
        active_object = None

    try:
        layer_collection = context.view_layer.active_layer_collection
        active_collection = layer_collection.collection if layer_collection is not None else None
        scene_collection = context.scene.collection
    except (AttributeError, ReferenceError, RuntimeError):
        active_collection = None
        scene_collection = None

    object_changed = active_object != _observed_active_object
    collection_changed = active_collection != _observed_active_collection
    _observed_active_object = active_object
    _observed_active_collection = active_collection

    ids = _selected_ids(context)
    selected_collection = next(
        (item for item in ids if isinstance(item, bpy.types.Collection)), None
    )
    selected_object = next(
        (item for item in ids if isinstance(item, bpy.types.Object)), None
    )
    if selected_collection is not None:
        _last_collection = selected_collection
        if update_kind:
            _last_kind = "COLLECTION"
        return "COLLECTION", selected_collection
    if selected_object is not None:
        _last_object = selected_object
        if update_kind:
            _last_kind = "OBJECT"
        return "OBJECT", selected_object

    if active_object is not None:
        _last_object = active_object
    if active_collection is not None and active_collection != scene_collection:
        _last_collection = active_collection

    if update_kind:
        # Selection-only changes are not depsgraph updates.  Comparing Blender's
        # active pointers lets the timer distinguish choosing a Collection from
        # a stale Object that remains active in the View Layer.
        if object_changed and active_object is not None:
            _last_kind = "OBJECT"
        elif collection_changed and active_collection is not None and active_collection != scene_collection:
            _last_kind = "COLLECTION"
        elif not _last_kind:
            if active_object is not None:
                _last_kind = "OBJECT"
            elif active_collection is not None and active_collection != scene_collection:
                _last_kind = "COLLECTION"

    if _last_kind == "COLLECTION" and active_collection is not None and active_collection != scene_collection:
        return "COLLECTION", active_collection
    if active_object is not None:
        return "OBJECT", active_object
    if active_collection is not None and active_collection != scene_collection:
        return "COLLECTION", active_collection
    return "", None


def blender_workspace_identity():
    """Return a stable identity for the Workspace shown by the host window.

    The manager is bound to the Workspace from which it was explicitly opened.
    Comparing Blender RNA pointers instead of the display name keeps this
    working when an artist renames or localizes the default ``Layout``
    workspace.  No Object or Collection is required, so this also works in an
    empty scene.
    """
    try:
        windows = tuple(bpy.context.window_manager.windows)
        window = _last_window if _last_window in windows else (windows[0] if windows else None)
        workspace = getattr(window, "workspace", None) if window is not None else None
        if workspace is None:
            return None
        return int(workspace.as_pointer()), str(workspace.name)
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        return None


def resolve_object(context=None):
    """Prefer a current active object, then the last valid Blender object."""
    context = context or bpy.context
    try:
        current = context.view_layer.objects.active
    except (AttributeError, ReferenceError, RuntimeError):
        current = None
    return _valid_object(current) or _valid_object(_last_object)


def resolve_collection(context=None, selected_only=False):
    """Resolve a selected Outliner collection or the remembered active collection."""
    context = context or bpy.context
    selected = next(
        (item for item in _selected_ids(context) if isinstance(item, bpy.types.Collection)), None
    )
    if selected is not None:
        return selected
    if selected_only:
        return None
    return _valid_collection(_last_collection)


def resolve_auto(context=None):
    """Resolve the user's latest explicit Object/Collection selection."""
    context = context or bpy.context
    selected_collection = resolve_collection(context, selected_only=True)
    if selected_collection is not None:
        return "COLLECTION", selected_collection
    if _last_kind == "COLLECTION":
        collection = _valid_collection(_last_collection)
        if collection is not None:
            return "COLLECTION", collection
    if _last_kind == "OBJECT":
        obj = _valid_object(_last_object)
        if obj is not None:
            return "OBJECT", obj
    obj = resolve_object(context)
    if obj is not None:
        return "OBJECT", obj
    collection = resolve_collection(context)
    if collection is not None:
        return "COLLECTION", collection
    return "", None


@contextmanager
def operator_context():
    """Run bpy.ops in the Blender window that last interacted with the user."""
    window = None
    try:
        windows = tuple(bpy.context.window_manager.windows)
    except (AttributeError, ReferenceError, RuntimeError):
        windows = ()
    if _last_window in windows:
        window = _last_window
    elif windows:
        window = windows[0]
    if window is None:
        yield
        return

    try:
        screen = window.screen
        area = next((item for item in screen.areas if item.type == "VIEW_3D"), None)
        region = next((item for item in area.regions if item.type == "WINDOW"), None) if area else None
        override = {"window": window, "screen": screen}
        if area is not None:
            override["area"] = area
        if region is not None:
            override["region"] = region
    except (ReferenceError, RuntimeError, TypeError):
        yield
        return
    with bpy.context.temp_override(**override):
        yield


def _find_blender_window_handle(exclude=0):
    """Return the visible top-level Blender HWND owned by this process."""
    if sys.platform != "win32":
        return 0
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        process_id = os.getpid()
        candidates = []
        enum_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def collect(hwnd, _lparam):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value != process_id or int(hwnd) == int(exclude) or not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, length + 1)
            if "Blender" in title.value:
                candidates.append(int(hwnd))
            return True

        callback = enum_proc_type(collect)
        user32.EnumWindows(callback, 0)
        if not candidates:
            return 0
        foreground = int(user32.GetForegroundWindow())
        return foreground if foreground in candidates else candidates[0]
    except (AttributeError, OSError, TypeError, ValueError):
        return 0


def blender_window_handle(qt_window=None):
    """Resolve the owner HWND, preferring an already attached Qt manager."""
    if sys.platform != "win32":
        return 0
    try:
        import ctypes
        from ctypes import wintypes

        manager = int(qt_window.winId()) if qt_window is not None else 0
        if manager:
            user32 = ctypes.windll.user32
            user32.GetWindow.argtypes = (wintypes.HWND, ctypes.c_uint)
            user32.GetWindow.restype = wintypes.HWND
            owner = user32.GetWindow(wintypes.HWND(manager), 4)  # GW_OWNER
            if owner:
                return int(owner)
        return _find_blender_window_handle(manager)
    except (AttributeError, OSError, TypeError, ValueError):
        return 0


def blender_window_rect(qt_window=None):
    """Return the Blender top-level frame as (left, top, right, bottom)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        hwnd = blender_window_handle(qt_window)
        if not hwnd:
            return None
        rect = wintypes.RECT()
        if not ctypes.windll.user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            return None
        return rect.left, rect.top, rect.right, rect.bottom
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def blender_window_in_move_size(qt_window=None):
    """Return whether Windows is running the owner's modal move/resize loop.

    Repositioning an owned Qt popup from inside that loop can recursively enter
    Qt and Blender window handling. On Windows this presents as a frozen desktop
    until focus is changed with Alt+Tab.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class GUITHREADINFO(ctypes.Structure):
            _fields_ = (
                ("cbSize", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND),
                ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND),
                ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND),
                ("hwndCaret", wintypes.HWND),
                ("rcCaret", wintypes.RECT),
            )

        hwnd = blender_window_handle(qt_window)
        if not hwnd:
            return False
        user32 = ctypes.windll.user32
        thread_id = user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), None)
        info = GUITHREADINFO()
        info.cbSize = ctypes.sizeof(GUITHREADINFO)
        if not thread_id or not user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
            return False
        gui_in_move_size = 0x00000002
        return bool(info.flags & gui_in_move_size) or int(info.hwndMoveSize or 0) == int(hwnd)
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _visible_temporary_region(region):
    """Pure predicate used by the Blender UI detector and headless tests."""
    try:
        return bool(
            region is not None
            and region.type == "TEMPORARY"
            and region.width > 1
            and region.height > 1
        )
    except (AttributeError, ReferenceError, RuntimeError):
        return False


def _regions_have_temporary(regions):
    return any(_visible_temporary_region(region) for region in regions)


def blender_temporary_ui_active():
    """Return whether Blender is showing an in-window popup/popover region.

    Blender draws many popovers (Options, Transform, object pickers and
    tooltips) inside its main GHOST HWND as ``TEMPORARY`` regions.  Win32
    z-order inspection cannot see them, so the owned Qt manager has to yield
    while such a region exists or it will paint over native Blender controls.
    """
    try:
        # Blender 5.x exposes the active menu/popover directly.  Keep the area
        # scan below as a fallback for another Blender window or a context that
        # is temporarily not carrying the popup region.
        if _visible_temporary_region(getattr(bpy.context, "region_popup", None)):
            return True
        for window in tuple(bpy.context.window_manager.windows):
            screen = window.screen
            if screen is None:
                continue
            for area in tuple(screen.areas):
                # ``region_popup`` is context-sensitive. Application timers do
                # not retain the editor context that opened the popup, so the
                # bare bpy.context lookup above misses Outliner menus and many
                # View3D popovers. Query it once per editor context explicitly.
                try:
                    with bpy.context.temp_override(window=window, area=area):
                        if _visible_temporary_region(getattr(bpy.context, "region_popup", None)):
                            return True
                except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
                    pass
                if _regions_have_temporary(tuple(area.regions)):
                    return True
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return False
    return False


def blender_header_popup_guard_active(qt_window):
    """Track Blender UI interactions that may open in-window popovers.

    ``region_popup`` is context-bound and Blender application timers do not
    always inherit the context of the header button that opened a popover.
    Win32 nevertheless retains the click state and cursor position.  Latch a
    short-lived guard after clicks in any Blender editor header or a right-click
    anywhere in Blender's client, keeping the Qt pseudo-dock suppressed until
    the next choice/cancel click or Escape.  This covers Outliner context menus
    as well as View3D Options/Transform popovers.  This
    uses read-only input polling; it does not hook Blender's WndProc, enter Qt,
    move windows or call Blender data from another thread.
    """
    if sys.platform != "win32" or qt_window is None:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        owner = int(blender_window_handle(qt_window))
        manager = int(qt_window.winId())
        if not owner or not manager:
            return False

        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        manager_rect = wintypes.RECT()
        user32.GetWindowRect(wintypes.HWND(manager), ctypes.byref(manager_rect))
        inside_manager = bool(
            manager_rect.left <= point.x < manager_rect.right
            and manager_rect.top <= point.y < manager_rect.bottom
        )
        header_rects = blender_ui_header_rects(qt_window)
        sidebar_rect = blender_sidebar_rect(qt_window)
        inside_sidebar = bool(
            sidebar_rect is not None
            and sidebar_rect[0] <= point.x < sidebar_rect[2]
            and sidebar_rect[1] <= point.y < sidebar_rect[3]
        )
        inside_header = bool(
            any(left <= point.x < right and top <= point.y < bottom
                for left, top, right, bottom in header_rects)
            and not inside_manager
            and not inside_sidebar
        )

        # Consume global mouse-state bits only while Blender is the active root
        # under the cursor.  Without this gate, a click in another application
        # could be observed after Alt+Tab and suppress the manager spuriously.
        user32.GetForegroundWindow.argtypes = ()
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.WindowFromPoint.argtypes = (wintypes.POINT,)
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        user32.GetAncestor.restype = wintypes.HWND
        foreground = int(user32.GetForegroundWindow() or 0)
        hit = int(user32.WindowFromPoint(point) or 0)
        hit_root = int(user32.GetAncestor(wintypes.HWND(hit), 2) or hit) if hit else 0  # GA_ROOT
        foreground_root = int(user32.GetAncestor(wintypes.HWND(foreground), 2) or foreground) if foreground else 0
        blender_input = bool(hit_root == owner and foreground_root == owner)

        now = time.monotonic()
        left_state = int(user32.GetAsyncKeyState(0x01))
        right_state = int(user32.GetAsyncKeyState(0x02))
        middle_state = int(user32.GetAsyncKeyState(0x04))
        left_down = bool(left_state & 0x8000)
        right_down = bool(right_state & 0x8000)
        middle_down = bool(middle_state & 0x8000)
        down = bool(left_down or right_down or middle_down)
        was_down = bool(getattr(qt_window, "_bt_header_mouse_down", False))
        clicked = bool(
            blender_input
            and (
                (down and not was_down)
                or ((left_state | right_state | middle_state) & 0x0001)
            )
        )
        right_clicked = bool(
            blender_input and ((right_down and not was_down) or (right_state & 0x0001))
        )
        popup_trigger = bool(inside_header or right_clicked)
        qt_window._bt_header_mouse_down = down

        active = bool(getattr(qt_window, "_bt_header_popup_guard", False))
        expire = float(getattr(qt_window, "_bt_header_popup_expire", 0.0))
        closing = float(getattr(qt_window, "_bt_header_popup_closing", 0.0))
        anchor = getattr(qt_window, "_bt_header_popup_anchor", None)

        if int(user32.GetAsyncKeyState(0x1B)) & 0x0001:  # Escape
            active = False
            closing = 0.0
        elif clicked:
            if active and popup_trigger:
                same_button = bool(
                    anchor and abs(point.x - anchor[0]) <= 28 and abs(point.y - anchor[1]) <= 20
                )
                if same_button:
                    closing = now + 0.25
                else:
                    anchor = (int(point.x), int(point.y))
                    expire = now + 30.0
                    closing = 0.0
            elif active:
                closing = now + 0.25
            elif popup_trigger:
                active = True
                anchor = (int(point.x), int(point.y))
                expire = now + 30.0
                closing = 0.0

        if active and ((closing and now >= closing) or (expire and now >= expire)):
            active = False
            closing = 0.0
        qt_window._bt_header_popup_guard = active
        qt_window._bt_header_popup_expire = expire
        qt_window._bt_header_popup_closing = closing
        qt_window._bt_header_popup_anchor = anchor
        return active
    except (AttributeError, OSError, ReferenceError, TypeError, ValueError):
        return False


def sync_blender_transient_z_order(qt_window):
    """Keep Blender dialogs and popovers above the pseudo-docked Qt manager.

    The manager is an owned top-level Qt window, because embedding a Qt HWND as
    a Blender child deadlocks the two event systems.  A consequence of that
    safe design is that Blender's later transient windows can initially land
    below the manager.  Promote only same-process Blender transients and never
    activate or resize them.  Some Blender menus are drawn in the main GHOST
    window instead of a separate HWND; while such a menu is active the native
    manager becomes transparent and click-through (Qt remains visible and
    pumped, so restoration cannot be lost).
    """
    if sys.platform != "win32" or qt_window is None:
        return {"promoted": 0, "suppressed": False, "menu_mode": False}
    if blender_window_in_move_size(qt_window):
        return {
            "promoted": 0,
            "suppressed": window_manager.is_suppressed(qt_window),
            "menu_mode": False,
        }
    return _sync_blender_transient_z_order(qt_window)


def _restore_native_popup_guard_window(state, user32, wintypes):
    """Reveal a guard-hidden HWND without entering either UI framework.

    Blender starts viewport orbit on MMB while dismissing an in-window popup.
    During that modal interaction ``bpy.app.timers`` may not run, so waiting for
    the ordinary Qt pump leaves the manager alpha at zero for many seconds.  A
    WH_GETMESSAGE callback cannot safely call Qt or Blender Python, but changing
    the already-owned HWND alpha/style is thread-local native work and mirrors
    the pre-dispatch hide performed by the same callback.

    ``native_restore_pending`` is consumed by ``sync_native_popup_guard`` on the
    next regular pump.  That later reconciliation keeps the composable Python
    suppression reasons authoritative without delaying the visible recovery.
    """
    manager = int(state.get("manager", 0)) if state else 0
    if not manager or not state.get("native_hidden"):
        return False
    try:
        hwnd = wintypes.HWND(manager)
        user32.SetLayeredWindowAttributes(hwnd, 0, 255, 0x00000002)
        user32.SetWindowLongW(hwnd, -20, int(state.get("base_exstyle", 0)))
        try:
            # Alpha restoration normally updates the DWM surface immediately;
            # this invalidation also covers drivers that retain the transparent
            # frame until Blender/Qt performs another geometry event.
            user32.RedrawWindow(
                hwnd, None, None,
                0x0001 | 0x0080 | 0x0100,  # INVALIDATE | UPDATENOW | ALLCHILDREN
            )
        except (AttributeError, OSError, TypeError, ValueError):
            pass
        state.update(
            active=False,
            confirmed=False,
            closing=0.0,
            native_hidden=False,
            native_restore_pending=True,
        )
        state["native_restore_count"] = int(state.get("native_restore_count", 0)) + 1
        return True
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def start_native_popup_guard(qt_window):
    """Install a same-thread Win32 input guard for Blender-drawn popups.

    Blender popup menus can start and paint between two ``bpy.app.timers``
    ticks. A ``WH_GETMESSAGE`` hook on Blender's own GUI thread sees the mouse
    message before dispatch and makes the owned manager transparent immediately.
    The callback performs native HWND operations only: it never enters Qt or
    Blender Python APIs and therefore cannot recurse into either event system.
    """
    global _native_popup_hook, _native_popup_hook_callback, _native_popup_hook_state
    if sys.platform != "win32" or qt_window is None:
        return False
    stop_native_popup_guard(qt_window)
    try:
        import ctypes
        from ctypes import wintypes

        class MSG(ctypes.Structure):
            _fields_ = (
                ("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD), ("pt", wintypes.POINT),
                ("lPrivate", wintypes.DWORD),
            )

        user32 = ctypes.windll.user32
        user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        user32.GetAncestor.restype = wintypes.HWND
        user32.CallNextHookEx.argtypes = (
            wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
        )
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        manager = int(qt_window.winId())
        owner = int(blender_window_handle(qt_window))
        thread_id = int(user32.GetWindowThreadProcessId(wintypes.HWND(owner), None) or 0)
        if not manager or not owner or not thread_id:
            return False
        state = {
            "manager": manager,
            "owner": owner,
            "base_exstyle": int(user32.GetWindowLongW(wintypes.HWND(manager), -20)),
            "headers": blender_ui_header_rects(qt_window),
            "active": False,
            "confirmed": False,
            "triggered": 0.0,
            "closing": 0.0,
            "native_hidden": False,
            "native_restore_pending": False,
            "native_restore_count": 0,
        }
        _native_popup_hook_state = state
        hook_proc_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
        )

        def hook_proc(code, wparam, lparam):
            try:
                if code >= 0 and lparam:
                    msg = ctypes.cast(lparam, ctypes.POINTER(MSG)).contents
                    message = int(msg.message)
                    if message in {0x0201, 0x0204, 0x0207, 0x020B}:
                        # WM_LBUTTONDOWN / WM_RBUTTONDOWN / WM_MBUTTONDOWN /
                        # WM_XBUTTONDOWN. Any button can dismiss a Blender
                        # TEMPORARY region; MMB additionally starts viewport
                        # orbit and can starve bpy.app.timers until the drag ends.
                        point = wintypes.POINT(
                            ctypes.c_short(int(msg.lParam) & 0xFFFF).value,
                            ctypes.c_short((int(msg.lParam) >> 16) & 0xFFFF).value,
                        )
                        user32.ClientToScreen(msg.hwnd, ctypes.byref(point))
                        root = int(user32.GetAncestor(msg.hwnd, 2) or msg.hwnd)  # GA_ROOT
                        manager_rect = wintypes.RECT()
                        user32.GetWindowRect(wintypes.HWND(manager), ctypes.byref(manager_rect))
                        inside_manager = bool(
                            manager_rect.left <= point.x < manager_rect.right
                            and manager_rect.top <= point.y < manager_rect.bottom
                        )
                        in_header = any(
                            left <= point.x < right and top <= point.y < bottom
                            for left, top, right, bottom in state.get("headers", ())
                        )
                        trigger = bool(
                            root == owner and not inside_manager
                            and (message == 0x0204 or in_header)
                        )
                        now = time.monotonic()
                        if state.get("active"):
                            state["closing"] = now + 0.30
                        elif trigger:
                            state.update(active=True, confirmed=False, triggered=now, closing=0.0)
                            exstyle = int(state["base_exstyle"])
                            user32.SetWindowLongW(
                                wintypes.HWND(manager), -20,
                                exstyle | 0x00080000 | 0x00000020,
                            )
                            user32.SetLayeredWindowAttributes(
                                wintypes.HWND(manager), 0, 0, 0x00000002
                            )
                            state["native_hidden"] = True
                            state["native_restore_pending"] = False
                    elif message == 0x0200:  # WM_MOUSEMOVE
                        # Once MMB movement follows the popup-closing press, the
                        # original popup message has already been dispatched and
                        # Blender is entering viewport orbit. Restore natively
                        # now; the Blender timer may be suspended by that modal
                        # operator and cannot be the recovery mechanism.
                        root = int(user32.GetAncestor(msg.hwnd, 2) or msg.hwnd)
                        middle_drag = bool(int(msg.wParam) & 0x0010)  # MK_MBUTTON
                        if (
                            root == owner
                            and middle_drag
                            and state.get("active")
                            and state.get("closing")
                        ):
                            _restore_native_popup_guard_window(state, user32, wintypes)
                    elif message in {0x0100, 0x0104} and int(msg.wParam) == 0x1B:  # Escape
                        if state.get("active"):
                            state["closing"] = time.monotonic() + 0.15
            except (AttributeError, OSError, TypeError, ValueError):
                pass
            return user32.CallNextHookEx(
                wintypes.HHOOK(_native_popup_hook), code, wparam, lparam
            )

        callback = hook_proc_type(hook_proc)
        user32.SetWindowsHookExW.argtypes = (
            ctypes.c_int, hook_proc_type, wintypes.HINSTANCE, wintypes.DWORD,
        )
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        handle = int(user32.SetWindowsHookExW(3, callback, None, thread_id) or 0)  # WH_GETMESSAGE
        if not handle:
            _native_popup_hook_state = {}
            return False
        _native_popup_hook_callback = callback
        _native_popup_hook = handle
        return True
    except (AttributeError, OSError, TypeError, ValueError):
        _native_popup_hook = 0
        _native_popup_hook_callback = None
        _native_popup_hook_state = {}
        return False


def sync_native_popup_guard(qt_window, popup_visible=False):
    """Adopt the pre-dispatch native guard into composable suppression state."""
    state = _native_popup_hook_state
    if not state or qt_window is None or int(qt_window.winId()) != int(state.get("manager", 0)):
        return False
    state["headers"] = blender_ui_header_rects(qt_window)
    if state.pop("native_restore_pending", False):
        # The hook already repaired the physical HWND during a modal viewport
        # drag. Reconcile the registry before applying the remaining composable
        # reasons; a still-active unrelated reason can therefore suppress it
        # again on this regular, framework-safe pump.
        window_manager.mark_native_visible(
            qt_window, int(state.get("base_exstyle", 0))
        )
    now = time.monotonic()
    if popup_visible:
        state["confirmed"] = True
    if state.get("active"):
        closing = float(state.get("closing", 0.0))
        age = now - float(state.get("triggered", now))
        if (closing and now >= closing) or age >= 30.0:
            state["active"] = False
        elif not state.get("confirmed") and age >= 0.45:
            # Header icon toggles that do not create a popup must not make the
            # manager disappear until the next click.
            state["active"] = False
    if state.get("native_hidden") and not window_manager.is_suppressed(qt_window):
        window_manager.adopt_native_suppression(
            qt_window, int(state.get("base_exstyle", 0))
        )
    active = bool(state.get("active"))
    set_qt_window_suppressed(qt_window, "native_input_guard", active)
    if not active:
        state["native_hidden"] = False
        state["confirmed"] = False
        state["closing"] = 0.0
    return active


def stop_native_popup_guard(qt_window=None):
    """Remove the native hook and restore any pre-suppressed manager HWND."""
    global _native_popup_hook, _native_popup_hook_callback, _native_popup_hook_state
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        if _native_popup_hook:
            user32.UnhookWindowsHookEx(wintypes.HHOOK(_native_popup_hook))
        state = _native_popup_hook_state
        manager = int(state.get("manager", 0)) if state else 0
        _native_popup_hook = 0
        _native_popup_hook_callback = None
        _native_popup_hook_state = {}
        if qt_window is not None:
            # The hook can hide the HWND before Blender's timer sees the event.
            # Adopt that state first; removing only this reason then either
            # restores the HWND or leaves another composable reason in charge.
            if state and state.get("native_hidden"):
                window_manager.adopt_native_suppression(
                    qt_window, state.get("base_exstyle")
                )
            set_qt_window_suppressed(qt_window, "native_input_guard", False)
        elif manager and state.get("native_hidden"):
            user32.SetLayeredWindowAttributes(wintypes.HWND(manager), 0, 255, 0x00000002)
            user32.SetWindowLongW(
                wintypes.HWND(manager), -20, int(state.get("base_exstyle", 0))
            )
        return True
    except (AttributeError, OSError, TypeError, ValueError):
        _native_popup_hook = 0
        _native_popup_hook_callback = None
        _native_popup_hook_state = {}
        return False


def _sync_blender_transient_z_order(qt_window):
    """Implementation kept separate from the pre-dispatch popup hook."""
    try:
        import ctypes
        from ctypes import wintypes

        class GUITHREADINFO(ctypes.Structure):
            _fields_ = (
                ("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND),
                ("rcCaret", wintypes.RECT),
            )

        user32 = ctypes.windll.user32
        user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetWindow.argtypes = (wintypes.HWND, wintypes.UINT)
        user32.GetWindow.restype = wintypes.HWND
        user32.SetWindowPos.argtypes = (
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        )
        user32.SetWindowPos.restype = wintypes.BOOL
        manager = int(qt_window.winId())
        owner = int(blender_window_handle(qt_window))
        if not manager or not owner:
            return {"promoted": 0, "suppressed": False, "menu_mode": False}

        process_id = os.getpid()
        windows = []
        enum_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def collect(hwnd, _lparam):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == process_id and user32.IsWindowVisible(hwnd):
                windows.append(int(hwnd))
            return True

        callback = enum_proc_type(collect)
        user32.EnumWindows(callback, 0)  # EnumWindows order is top to bottom.
        manager_index = windows.index(manager) if manager in windows else -1
        candidates = []
        for hwnd in windows:
            if hwnd in (manager, owner):
                continue
            root_owner = int(user32.GetAncestor(wintypes.HWND(hwnd), 3) or 0)  # GA_ROOTOWNER
            direct_owner = int(user32.GetWindow(wintypes.HWND(hwnd), 4) or 0)  # GW_OWNER
            # Qt dialogs owned by the manager already have the correct stacking
            # and must not be mistaken for Blender transients.
            if root_owner == manager or direct_owner == manager:
                continue
            if root_owner == owner or direct_owner == owner:
                candidates.append(hwnd)
                continue
            class_name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(wintypes.HWND(hwnd), class_name, len(class_name))
            title_len = user32.GetWindowTextLengthW(wintypes.HWND(hwnd))
            title = ctypes.create_unicode_buffer(title_len + 1)
            user32.GetWindowTextW(wintypes.HWND(hwnd), title, title_len + 1)
            signature = (class_name.value + " " + title.value).lower()
            if "ghost" in signature or "blender" in signature:
                candidates.append(hwnd)

        promoted = 0
        for hwnd in reversed(candidates):
            # Avoid recurring SetWindowPos calls when the transient is already
            # above the manager in the current top-to-bottom z-order.
            candidate_index = windows.index(hwnd) if hwnd in windows else -1
            if manager_index >= 0 and 0 <= candidate_index < manager_index:
                continue
            if user32.SetWindowPos(
                wintypes.HWND(hwnd), wintypes.HWND(0), 0, 0, 0, 0,
                0x0001 | 0x0002 | 0x0010 | 0x0200,
                # NOSIZE | NOMOVE | NOACTIVATE | NOOWNERZORDER
            ):
                promoted += 1

        thread_id = user32.GetWindowThreadProcessId(wintypes.HWND(owner), None)
        info = GUITHREADINFO(); info.cbSize = ctypes.sizeof(GUITHREADINFO)
        have_info = bool(thread_id and user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)))
        menu_flags = 0x00000004 | 0x00000008 | 0x00000010  # MENU/SYSTEM/POPUP mode
        menu_owner = int(info.hwndMenuOwner or 0) if have_info else 0
        menu_mode = bool(have_info and (info.flags & menu_flags) and menu_owner in (0, owner))
        owner_disabled = not bool(user32.IsWindowEnabled(wintypes.HWND(owner)))
        # A modal Blender dialog must never compete with the owned Qt manager,
        # even when its HWND was detected and promoted.  Menus/popovers drawn in
        # the main GHOST window need the same suppression.  Reasons are combined
        # with Sidebar-tab suppression so one subsystem cannot restore another.
        should_suppress = bool(menu_mode or owner_disabled)
        set_qt_window_suppressed(qt_window, "blender_transient", should_suppress)
        return {"promoted": promoted, "suppressed": should_suppress, "menu_mode": menu_mode}
    except (AttributeError, OSError, TypeError, ValueError):
        return {
            "promoted": 0,
            "suppressed": window_manager.is_suppressed(qt_window),
            "menu_mode": False,
        }


def set_qt_window_suppressed(qt_window, reason, enabled):
    """Make the manager invisible/click-through while keeping its Qt pump alive."""
    if qt_window is None:
        return False
    return window_manager.set_suppression_reason(qt_window, reason, enabled)


def _restore_qt_window_native_visibility(qt_window, base_exstyle=None):
    """Physically restore a manager HWND even when Python flags are stale.

    Native popup suppression can run before the Blender timer adopts its state.
    Conversely, stopping the hook may clear its bookkeeping before the ordinary
    suppression reasons are reset. The HWND is therefore the source of truth at
    a Close/Open boundary: remove click-through and restore full alpha even if
    ``_bt_native_suppressed`` already says False.
    """
    if qt_window is None:
        return False
    return window_manager.restore_native(qt_window, base_exstyle)


def reset_qt_window_suppression(qt_window):
    """Restore a reusable manager to an ordinary visible native state.

    ``hide_manager`` intentionally keeps the Qt widget alive.  Native layered
    alpha and click-through styles must not survive that hide/show boundary,
    otherwise the next explicit Open creates a logically visible but fully
    transparent window until another geometry event happens.
    """
    if qt_window is None:
        return False
    # Do not trust ``_bt_native_suppressed`` here. The native hook can leave a
    # transparent/click-through HWND after its Python flag has already cleared.
    window_manager.clear_suppression_reasons(qt_window)
    restored = _restore_qt_window_native_visibility(qt_window)
    if restored:
        window_manager.mark_native_visible(qt_window)
    qt_window._bt_header_mouse_down = False
    qt_window._bt_header_popup_guard = False
    qt_window._bt_header_popup_expire = 0.0
    qt_window._bt_header_popup_closing = 0.0
    qt_window._bt_header_popup_anchor = None
    return not window_manager.is_suppressed(qt_window)


def blender_sidebar_category():
    """Return the active tab/category of the VIEW_3D Sidebar used by Bake Tools."""
    try:
        windows = tuple(bpy.context.window_manager.windows)
        window = _last_window if _last_window in windows else (windows[0] if windows else None)
        if window is None or window.screen is None:
            return None
        areas = tuple(window.screen.areas)
        area = _last_view3d_area if any(candidate == _last_view3d_area for candidate in areas) else None
        if area is None or area.type != "VIEW_3D":
            candidates = [candidate for candidate in areas if candidate.type == "VIEW_3D"]
            area = max(candidates, key=lambda candidate: candidate.width * candidate.height) if candidates else None
        if area is None:
            return None
        sidebar = next((region for region in area.regions if region.type == "UI" and region.width > 1), None)
        if sidebar is None:
            return None
        value = str(getattr(sidebar, "active_panel_category", "") or "").strip()
        return None if not value or value.upper() == "UNSUPPORTED" else value
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        return None


def blender_sidebar_rect(qt_window=None):
    """Return the active VIEW_3D Sidebar region in screen coordinates.

    Blender region coordinates use a bottom-left origin inside the native
    client area. Qt uses a top-left screen origin, so the Win32 client origin
    and height are required for a stable pseudo-dock target.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        windows = tuple(bpy.context.window_manager.windows)
        window = _last_window if _last_window in windows else (windows[0] if windows else None)
        if window is None or window.screen is None:
            return None
        areas = tuple(window.screen.areas)
        area = _last_view3d_area if any(candidate == _last_view3d_area for candidate in areas) else None
        if area is None or area.type != "VIEW_3D":
            candidates = [candidate for candidate in areas if candidate.type == "VIEW_3D"]
            area = max(candidates, key=lambda candidate: candidate.width * candidate.height) if candidates else None
        if area is None:
            return None
        sidebar = next((region for region in area.regions if region.type == "UI" and region.width > 1), None)
        if sidebar is None:
            return None

        hwnd = blender_window_handle(qt_window)
        if not hwnd:
            return None
        user32 = ctypes.windll.user32
        client = wintypes.RECT()
        origin = wintypes.POINT(0, 0)
        if not user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(client)):
            return None
        if not user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(origin)):
            return None
        client_height = client.bottom - client.top
        left = origin.x + int(sidebar.x)
        right = left + int(sidebar.width)
        top = origin.y + client_height - int(sidebar.y + sidebar.height)
        bottom = top + int(sidebar.height)
        return left, top, right, bottom
    except (AttributeError, ReferenceError, OSError, TypeError, ValueError):
        return None


def blender_view3d_header_rect(qt_window=None):
    """Return only the active 3D View HEADER region in screen coordinates.

    A fixed top-of-window band also contains Blender's menus, workspace tabs
    and the Bake Tools launcher itself.  Treating that whole band as a popover
    source made the manager disappear immediately after Open.  Blender region
    coordinates provide the exact, DPI-independent header boundary.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        windows = tuple(bpy.context.window_manager.windows)
        window = _last_window if _last_window in windows else (windows[0] if windows else None)
        if window is None or window.screen is None:
            return None
        areas = tuple(window.screen.areas)
        area = _last_view3d_area if any(candidate == _last_view3d_area for candidate in areas) else None
        if area is None or area.type != "VIEW_3D":
            candidates = [candidate for candidate in areas if candidate.type == "VIEW_3D"]
            area = max(candidates, key=lambda candidate: candidate.width * candidate.height) if candidates else None
        if area is None:
            return None
        header = next(
            (region for region in area.regions if region.type == "HEADER" and region.width > 1 and region.height > 1),
            None,
        )
        if header is None:
            return None

        hwnd = blender_window_handle(qt_window)
        if not hwnd:
            return None
        user32 = ctypes.windll.user32
        client = wintypes.RECT()
        origin = wintypes.POINT(0, 0)
        if not user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(client)):
            return None
        if not user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(origin)):
            return None
        client_height = int(client.bottom - client.top)
        left = int(origin.x + header.x)
        right = left + int(header.width)
        top = int(origin.y + client_height - int(header.y + header.height))
        bottom = top + int(header.height)
        return left, top, right, bottom
    except (AttributeError, ReferenceError, OSError, TypeError, ValueError):
        return None


def blender_ui_header_rects(qt_window=None):
    """Return every visible Blender editor header in owner screen coordinates.

    Popup buttons are not limited to the active View3D. Outliner, Properties,
    Top Bar and secondary editor headers can all open menus that extend beneath
    the owned Qt manager. The result is calculated on Blender's main thread and
    contains plain tuples only, keeping the input guard DPI-independent.
    """
    if sys.platform != "win32":
        return ()
    try:
        import ctypes
        from ctypes import wintypes

        windows = tuple(bpy.context.window_manager.windows)
        window = _last_window if _last_window in windows else (windows[0] if windows else None)
        if window is None or window.screen is None:
            return ()
        hwnd = blender_window_handle(qt_window)
        if not hwnd:
            return ()
        user32 = ctypes.windll.user32
        client = wintypes.RECT()
        origin = wintypes.POINT(0, 0)
        if not user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(client)):
            return ()
        if not user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(origin)):
            return ()
        client_height = int(client.bottom - client.top)
        result = []
        for area in tuple(window.screen.areas):
            for region in tuple(area.regions):
                if region.type not in {"HEADER", "TOOL_HEADER"} or region.width <= 1 or region.height <= 1:
                    continue
                left = int(origin.x + region.x)
                top = int(origin.y + client_height - int(region.y + region.height))
                result.append((left, top, left + int(region.width), top + int(region.height)))
        return tuple(result)
    except (AttributeError, ReferenceError, OSError, TypeError, ValueError):
        return ()


def attach_qt_window_to_blender(qt_window):
    """Make the frameless manager an owned popup without native reparenting.

    Qt top-level widgets must remain top-level Qt windows. Win32 SetParent/
    WS_CHILD against Blender's non-Qt HWND can deadlock Qt event delivery and
    Blender shutdown. GWLP_HWNDPARENT gives the required owner lifecycle while
    preserving Qt's supported window model.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        manager_hwnd = int(qt_window.winId())
        owner = _find_blender_window_handle(manager_hwnd)
        if not owner:
            return False
        setter = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        setter.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
        setter.restype = ctypes.c_ssize_t
        setter(wintypes.HWND(manager_hwnd), -8, ctypes.c_ssize_t(owner))  # GWLP_HWNDPARENT
        return True
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def qt_window_has_blender_owner(qt_window):
    """Return whether the manager is owned by or embedded in this Blender process."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        manager = wintypes.HWND(int(qt_window.winId()))
        user32.GetWindow.argtypes = (wintypes.HWND, ctypes.c_uint)
        user32.GetWindow.restype = wintypes.HWND
        user32.GetParent.argtypes = (wintypes.HWND,)
        user32.GetParent.restype = wintypes.HWND
        owner = user32.GetWindow(manager, 4)  # GW_OWNER for legacy popup
        if not owner:
            owner = user32.GetParent(manager)  # native child in current integration
        if not owner:
            return False
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(owner, ctypes.byref(pid))
        return pid.value == os.getpid()
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def qt_window_is_embedded(qt_window):
    """Return whether the Qt manager uses a real WS_CHILD native relationship."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        hwnd = wintypes.HWND(int(qt_window.winId()))
        user32 = ctypes.windll.user32
        user32.GetParent.argtypes = (wintypes.HWND,)
        user32.GetParent.restype = wintypes.HWND
        style = user32.GetWindowLongW(hwnd, -16)
        return bool(style & 0x40000000) and bool(user32.GetParent(hwnd))
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def place_qt_window_in_sidebar(qt_window, screen_rect):
    """Place the frameless owned popup using Sidebar screen coordinates."""
    if sys.platform != "win32" or screen_rect is None:
        return False
    if blender_window_in_move_size(qt_window):
        return False
    try:
        import ctypes
        from ctypes import wintypes

        manager = wintypes.HWND(int(qt_window.winId()))
        user32 = ctypes.windll.user32
        left, top, right, bottom = screen_rect
        return bool(user32.SetWindowPos(
            manager, None, int(left), int(top),
            max(1, int(right - left)), max(1, int(bottom - top)),
            0x0004 | 0x0010 | 0x0040 | 0x0200 | 0x0400,
            # NOZORDER/NOACTIVATE/SHOWWINDOW/NOOWNERZORDER/NOSENDCHANGING
        ))
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def qt_window_rect(qt_window):
    """Return the manager's actual native screen rectangle."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        rect = wintypes.RECT()
        hwnd = wintypes.HWND(int(qt_window.winId()))
        if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return rect.left, rect.top, rect.right, rect.bottom
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def reset_context():
    global _last_object, _last_collection, _last_kind, _last_window, _last_view3d_area
    global _observed_active_object, _observed_active_collection
    _last_object = None
    _last_collection = None
    _last_kind = ""
    _last_window = None
    _last_view3d_area = None
    _observed_active_object = None
    _observed_active_collection = None
