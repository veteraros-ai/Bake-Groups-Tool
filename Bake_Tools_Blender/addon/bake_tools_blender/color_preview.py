"""Non-destructive Blender viewport preview for Maya's ``Color HP`` mode."""

from __future__ import annotations

import bpy

from .object_repository import ObjectRepository


_ORIGINAL_COLOR = "_bake_tools_original_color"
_PREVIEW_MARKER = "_bake_tools_color_preview"
_shading_modes = {}

PALETTE = (
    (0.953, 0.071, 0.027), (0.988, 0.424, 0.012), (0.953, 0.851, 0.443),
    (0.196, 0.831, 0.145), (0.145, 0.831, 0.824), (0.090, 0.247, 0.827),
    (0.647, 0.090, 0.827), (0.827, 0.090, 0.820), (1.000, 1.000, 1.000),
    (0.000, 0.506, 0.000), (0.000, 0.506, 0.482), (0.000, 0.000, 0.498),
    (0.114, 0.000, 0.498), (0.498, 0.000, 0.478), (0.518, 0.518, 0.518),
    (0.518, 0.176, 0.110), (0.278, 0.204, 0.129), (0.518, 0.459, 0.404),
    (1.000, 0.945, 0.145),
)


def ensure_pair_color_indices(pair):
    """Assign stable Maya-style palette slots without recycling scene data."""
    used = {int(group.color_index) for group in pair.subgroups if int(group.color_index) >= 0}
    next_index = 0
    for subgroup in pair.subgroups:
        if subgroup.color_index >= 0:
            continue
        while next_index in used:
            next_index += 1
        subgroup.color_index = next_index
        used.add(next_index)
        next_index += 1


def subgroup_rgb(subgroup):
    if getattr(subgroup, "use_custom_color", False):
        return tuple(float(value) for value in subgroup.custom_color[:3])
    index = max(0, int(getattr(subgroup, "color_index", 0)))
    base = PALETTE[index % len(PALETTE)]
    shade_pass = index // len(PALETTE)
    shade = 1.0 if shade_pass == 0 else max(0.38, 0.62 ** shade_pass)
    return tuple(max(0.0, min(1.0, channel * shade)) for channel in base)


def _view3d_spaces():
    seen = set()
    try:
        screens = {window.screen for window in bpy.context.window_manager.windows}
    except (AttributeError, ReferenceError, RuntimeError):
        screens = set()
    if not screens:
        # Background regression tests have screens but no Window instances.
        # Supporting them also keeps the preview resilient during workspace
        # initialization before Blender exposes its first window.
        screens = tuple(bpy.data.screens)
    for screen in screens:
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            for space in area.spaces:
                if space.type != "VIEW_3D":
                    continue
                key = space.as_pointer()
                if key not in seen:
                    seen.add(key)
                yield key, area, space


def _tag_view3d_redraw():
    for _key, area, _space in _view3d_spaces():
        try:
            area.tag_redraw()
        except (AttributeError, ReferenceError, RuntimeError):
            pass


def _enable_object_colors():
    for key, area, space in _view3d_spaces():
        if key not in _shading_modes:
            _shading_modes[key] = (space.shading.type, space.shading.color_type)
        # Object Color is evaluated by Workbench/Solid shading. Merely changing
        # color_type has no visible effect while the artist is in Material
        # Preview or Rendered mode, which made Color HP appear broken.
        space.shading.type = "SOLID"
        space.shading.color_type = "OBJECT"
        try:
            area.tag_redraw()
        except (AttributeError, ReferenceError, RuntimeError):
            pass


def _restore_shading_modes():
    for key, area, space in _view3d_spaces():
        previous = _shading_modes.get(key)
        if previous:
            try:
                shading_type, color_type = previous
                space.shading.type = shading_type
                space.shading.color_type = color_type
                area.tag_redraw()
            except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
                pass
    _shading_modes.clear()


def restore_color_preview(restore_shading=True):
    """Restore every mesh touched by the preview, including after a file reload."""
    restored = 0
    for obj in bpy.data.objects:
        if not obj.get(_PREVIEW_MARKER, False):
            continue
        original = obj.get(_ORIGINAL_COLOR)
        if original is not None and len(original) >= 4:
            obj.color = tuple(float(channel) for channel in original[:4])
        for key in (_ORIGINAL_COLOR, _PREVIEW_MARKER):
            if key in obj:
                del obj[key]
        restored += 1
    if restore_shading:
        _restore_shading_modes()
    else:
        _tag_view3d_redraw()
    return restored


def refresh_color_preview(state, pair=None):
    """Apply the active chapter palette or restore the artist's object colors."""
    restore_color_preview(restore_shading=not state.color_subgroups or pair is None)
    if not state.color_subgroups or pair is None:
        return 0

    ensure_pair_color_indices(pair)
    colored = 0
    for subgroup in pair.subgroups:
        rgb = subgroup_rgb(subgroup)
        for obj in ObjectRepository.valid_members(subgroup, "HP"):
            if obj.type != "MESH":
                continue
            if _ORIGINAL_COLOR not in obj:
                obj[_ORIGINAL_COLOR] = tuple(float(channel) for channel in obj.color)
            obj[_PREVIEW_MARKER] = True
            obj.color = (rgb[0], rgb[1], rgb[2], 1.0)
            colored += 1
    if colored:
        _enable_object_colors()
    else:
        _restore_shading_modes()
    return colored
