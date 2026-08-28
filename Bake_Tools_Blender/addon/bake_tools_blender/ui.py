"""Explicit Blender Sidebar launcher for the pixel-matched Qt manager."""

from __future__ import annotations

import bpy

from .blender_bridge import capture_context
from .localization import text as localized_text


_last_error = ""


def _tr(context, source):
    settings = getattr(getattr(context, "scene", None), "bake_tools_settings", None)
    return localized_text(source, getattr(settings, "language", "EN"))


def _redraw_sidebars():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


class BAKE_TOOLS_OT_show_manager(bpy.types.Operator):
    """Open the manager only after an explicit artist action."""

    bl_idname = "bake_tools.show_manager"
    bl_label = "Open Bake Group Manager"
    bl_description = "Open the Bake Tools manager in the current 3D View Sidebar area"

    def execute(self, context):
        global _last_error
        capture_context(context)
        try:
            from .qt_window import show_manager

            show_manager(context)
            _last_error = ""
            _redraw_sidebars()
        except (ImportError, RuntimeError, OSError) as exc:
            _last_error = str(exc)
            self.report({"ERROR"}, "Bake Tools manager could not start: {}".format(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class BAKE_TOOLS_OT_hide_manager(bpy.types.Operator):
    """Close the embedded manager from Blender's native Sidebar."""

    bl_idname = "bake_tools.hide_manager"
    bl_label = "Close Bake Group Manager"
    bl_description = "Close the embedded Bake Tools manager"

    def execute(self, _context):
        from .qt_window import hide_manager

        hide_manager()
        _redraw_sidebars()
        return {"FINISHED"}


def reset_launcher_state():
    global _last_error
    _last_error = ""


class BAKE_TOOLS_PT_launcher(bpy.types.Panel):
    """Small native host used only to launch the real manager on demand."""

    bl_idname = "BAKE_TOOLS_PT_launcher"
    bl_label = "Bake Group Manager Pro"
    bl_category = "Bake Tools"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_options = {"HIDE_HEADER"}

    def draw(self, context):
        capture_context(context)
        layout = self.layout
        try:
            from .qt_window import manager_is_visible

            visible = manager_is_visible()
        except (ImportError, RuntimeError, OSError):
            visible = False
        if visible:
            layout.operator("bake_tools.hide_manager", text=_tr(context, "Close Bake Group Manager"), icon="X")
        else:
            layout.operator("bake_tools.show_manager", text=_tr(context, "Open Bake Group Manager"), icon="WINDOW")
        if _last_error:
            box = layout.box()
            box.alert = True
            box.label(text=_tr(context, "PySide6 manager could not start"), icon="ERROR")


UI_CLASSES = (BAKE_TOOLS_OT_show_manager, BAKE_TOOLS_OT_hide_manager, BAKE_TOOLS_PT_launcher)
