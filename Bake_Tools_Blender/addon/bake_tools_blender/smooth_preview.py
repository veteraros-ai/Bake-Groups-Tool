"""Non-destructive Blender viewport subdivision matching Maya Smooth View."""

from __future__ import annotations

import bpy

from .object_repository import ObjectRepository
from .mesh_tools import is_zbrush_object


_NAME = "Bake Tools Smooth Preview"


def _preview_modifiers(obj):
    return tuple(modifier for modifier in obj.modifiers if modifier.name.startswith(_NAME))


def clear_preview(state=None, progress=None):
    objects = tuple(obj for obj in bpy.data.objects if obj.type == "MESH")
    removed = 0
    for index, obj in enumerate(objects):
        for modifier in _preview_modifiers(obj):
            obj.modifiers.remove(modifier)
            removed += 1
        if progress:
            progress.update(int((index + 1) * 100 / max(1, len(objects))), "Restoring: {}".format(obj.name))
    return removed


def apply_preview(state, pair, enabled, progress=None):
    if not enabled:
        return 0, clear_preview(state, progress)
    targets = []
    for subgroup in pair.subgroups:
        for obj in ObjectRepository.valid_members(subgroup, "HP"):
            targets.append((obj, int(subgroup.smooth_level)))
    # Clear stale preview modifiers from inactive chapters before applying the
    # active chapter, matching Maya's single active Export Settings preview.
    clear_preview(state)
    applied = 0
    skipped = 0
    for index, (obj, level) in enumerate(targets):
        if is_zbrush_object(state, obj) or level <= 0:
            skipped += 1
        else:
            modifier = obj.modifiers.new(_NAME, "SUBSURF")
            modifier.subdivision_type = "CATMULL_CLARK"
            modifier.levels = level
            modifier.render_levels = level
            modifier.show_viewport = True
            modifier.show_render = False
            modifier.show_in_editmode = False
            applied += 1
        if progress:
            progress.update(int((index + 1) * 100 / max(1, len(targets))), "Smoothing: {}".format(obj.name))
    return applied, skipped


def refresh_subgroup_preview(state, pair, subgroup):
    if not state.preview_smoothing:
        return
    for obj in ObjectRepository.valid_members(subgroup, "HP"):
        for modifier in _preview_modifiers(obj):
            obj.modifiers.remove(modifier)
        if subgroup.smooth_level > 0 and not is_zbrush_object(state, obj):
            modifier = obj.modifiers.new(_NAME, "SUBSURF")
            modifier.subdivision_type = "CATMULL_CLARK"
            modifier.levels = int(subgroup.smooth_level)
            modifier.render_levels = int(subgroup.smooth_level)
            modifier.show_viewport = True
            modifier.show_render = False


def set_preview_render_state(objects, enabled):
    changed = []
    for obj in objects:
        for modifier in _preview_modifiers(obj):
            changed.append((modifier, bool(modifier.show_viewport), bool(modifier.show_render)))
            # Blender FBX evaluates the viewport modifier stack.  Make an
            # existing preview modifier exportable even if its viewport monitor
            # was manually disabled, then restore both flags transactionally.
            modifier.show_viewport = bool(enabled)
            modifier.show_render = bool(enabled)
    return changed


def restore_preview_render_state(changed):
    for modifier, viewport_value, render_value in changed:
        try:
            modifier.show_viewport = viewport_value
            modifier.show_render = render_value
        except ReferenceError:
            pass
