"""Bake Tools Blender add-on with its original PySide6 manager UI."""

from __future__ import annotations

import bpy

from .operators import OPERATOR_CLASSES
from .properties import PROPERTY_CLASSES, register_properties, unregister_properties
from .ui import UI_CLASSES, reset_launcher_state
from .icons import register_icons, unregister_icons
from .sync import register_sync_handlers, unregister_sync_handlers


bl_info = {
    "name": "Bake Groups Tool",
    "author": "Veteraros AI",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Bake Tools",
    "description": "Prepare HP/LP bake groups, cages, smoothing and FBX exports",
    "category": "3D View",
}


CLASSES = tuple(PROPERTY_CLASSES) + tuple(OPERATOR_CLASSES) + tuple(UI_CLASSES)
_is_registered = False


def register():
    global _is_registered
    if _is_registered:
        return
    reset_launcher_state()
    register_icons()
    for cls in CLASSES:
        try:
            bpy.utils.register_class(cls)
        except ValueError as exc:
            if "already registered" not in str(exc):
                raise
    register_properties()
    register_sync_handlers()
    _is_registered = True


def unregister():
    global _is_registered
    if not _is_registered:
        return
    try:
        from .qt_window import shutdown_manager

        shutdown_manager()
    except ImportError:
        pass
    try:
        from .color_preview import restore_color_preview

        restore_color_preview()
    except ImportError:
        pass
    try:
        from .smooth_preview import clear_preview

        clear_preview()
    except ImportError:
        pass
    reset_launcher_state()
    unregister_sync_handlers()
    unregister_properties()
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    unregister_icons()
    _is_registered = False


if __name__ == "__main__":
    register()
