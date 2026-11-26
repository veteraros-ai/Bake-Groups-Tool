"""Non-destructive Blender viewport preview for Maya's ``Color HP`` mode."""

from __future__ import annotations

import json
from pathlib import Path

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


def _palette_path():
    return Path(bpy.utils.user_resource("CONFIG", path="bake_tools_blender", create=True)) / "hp_palette.json"


def load_palette_settings():
    try:
        data = json.loads(_palette_path().read_text(encoding="utf8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_palette_settings(settings):
    path = _palette_path()
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(settings, ensure_ascii=False), encoding="utf8")
    pending.replace(path)


_active_palette = None


def get_palette():
    global _active_palette
    if _active_palette is None:
        saved = load_palette_settings().get("default", {})
        _active_palette = (saved.get("colors", PALETTE), saved.get("overrides", {}))
    return _active_palette


def set_palette(colors, overrides):
    global _active_palette
    _active_palette = (tuple(tuple(rgb) for rgb in colors), dict(overrides))


def subgroup_rgb(subgroup, custom_color=None):
    colors, overrides = get_palette()
    custom = overrides.get(subgroup.name, custom_color)
    if custom is None and getattr(subgroup, "use_custom_color", False):
        custom = subgroup.custom_color
    if custom is not None:
        return tuple(float(value) for value in custom[:3])
    return indexed_rgb(subgroup.color_index, colors)


def indexed_rgb(index, colors):
    index = max(0, int(index))
    base = colors[index % len(colors)]
    shade_pass = index // len(colors)
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
        if space.shading.type != "SOLID":
            space.shading.type = "SOLID"
        if space.shading.color_type != "OBJECT":
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


def _restore_object_color(obj):
    original = obj.get(_ORIGINAL_COLOR)
    if original is not None and len(original) >= 4:
        obj.color = tuple(float(channel) for channel in original[:4])
    for key in (_ORIGINAL_COLOR, _PREVIEW_MARKER):
        if key in obj:
            del obj[key]


def restore_color_preview(restore_shading=True):
    """Restore every mesh touched by the preview, including after a file reload."""
    restored = 0
    for obj in bpy.data.objects:
        if not obj.get(_PREVIEW_MARKER, False):
            continue
        _restore_object_color(obj)
        restored += 1
    if restore_shading:
        _restore_shading_modes()
    else:
        _tag_view3d_redraw()
    return restored


def refresh_color_preview(state, pair=None):
    """Apply the active chapter palette or restore the artist's object colors."""
    if not state.color_subgroups or pair is None:
        restore_color_preview()
        return 0

    ensure_pair_color_indices(pair)
    targets = {}
    for subgroup in pair.subgroups:
        color = (*subgroup_rgb(subgroup), 1.0)
        for obj in ObjectRepository.valid_members(subgroup, "HP"):
            if obj.type == "MESH":
                targets[obj] = color

    for obj in bpy.data.objects:
        if obj.get(_PREVIEW_MARKER, False) and obj not in targets:
            _restore_object_color(obj)
    for obj, color in targets.items():
        if not obj.get(_PREVIEW_MARKER, False):
            obj[_ORIGINAL_COLOR] = tuple(obj.color)
            obj[_PREVIEW_MARKER] = True
        if any(abs(old - new) > 1e-6 for old, new in zip(obj.color, color)):
            obj.color = color
    if targets:
        _enable_object_colors()
    else:
        _restore_shading_modes()
    return len(targets)
