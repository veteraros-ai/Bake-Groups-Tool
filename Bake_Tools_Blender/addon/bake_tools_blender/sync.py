"""Blender lifecycle hooks for native UI context synchronization."""

from __future__ import annotations

import bpy
from bpy.app.handlers import persistent

from .blender_bridge import blender_window_in_move_size, capture_context, reset_context
from .object_repository import ObjectRepository
from .properties import ensure_state_ids


@persistent
def _notify_after_scene_change(*_args):
    capture_context()
    try:
        scene = bpy.context.scene
        if scene is None or not hasattr(scene, "bake_tools_settings"):
            return
        state = scene.bake_tools_settings
        ensure_state_ids(state)
        ObjectRepository.sync_all_pair_visibility(state)
        from .color_preview import refresh_color_preview

        pair = next((item for item in state.pairs if item.item_id == state.active_pair_id), None)
        refresh_color_preview(state, pair)
    except (AttributeError, ReferenceError, RuntimeError):
        # Scene switching can briefly expose invalid RNA pointers. The next
        # load/undo notification or user action will resynchronize safely.
        return


_HANDLER_LISTS = (
    bpy.app.handlers.load_post,
    bpy.app.handlers.undo_post,
    bpy.app.handlers.redo_post,
)


def _poll_native_context():
    if blender_window_in_move_size():
        return 0.20
    capture_context()
    return 0.20


def register_sync_handlers():
    for handlers in _HANDLER_LISTS:
        if _notify_after_scene_change not in handlers:
            handlers.append(_notify_after_scene_change)
    if not bpy.app.timers.is_registered(_poll_native_context):
        bpy.app.timers.register(_poll_native_context, first_interval=0.05, persistent=True)


def unregister_sync_handlers():
    for handlers in _HANDLER_LISTS:
        if _notify_after_scene_change in handlers:
            handlers.remove(_notify_after_scene_change)
    if bpy.app.timers.is_registered(_poll_native_context):
        bpy.app.timers.unregister(_poll_native_context)
    reset_context()
