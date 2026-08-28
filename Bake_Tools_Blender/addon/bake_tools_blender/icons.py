"""Load the original Bake Tools PNG icons as Blender preview icons."""

from __future__ import annotations

import os

import bpy
from bpy.utils import previews


_preview_collection = None


def _asset_dir():
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "assets"))


def register_icons():
    global _preview_collection
    if _preview_collection is not None:
        return
    _preview_collection = previews.new()
    for filename in os.listdir(_asset_dir()):
        if not filename.lower().endswith(".png"):
            continue
        path = os.path.join(_asset_dir(), filename)
        try:
            _preview_collection.load(filename, path, "IMAGE")
        except RuntimeError:
            continue


def unregister_icons():
    global _preview_collection
    if _preview_collection is not None:
        previews.remove(_preview_collection)
        _preview_collection = None


def icon_kwargs(filename, fallback="IMAGE_DATA"):
    if _preview_collection is not None and filename in _preview_collection:
        return {"icon_value": _preview_collection[filename].icon_id}
    return {"icon": fallback}
