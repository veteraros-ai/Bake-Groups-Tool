"""Headless regression for Blender in-window popup detection."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


ADDON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_ROOT.parent))

from Bake_Tools_Blender.addon.bake_tools_blender.blender_bridge import (  # noqa: E402
    _regions_have_temporary,
    _visible_temporary_region,
)
from Bake_Tools_Blender.addon.bake_tools_blender import qt_window  # noqa: E402


ordinary = SimpleNamespace(type="WINDOW", width=300, height=200)
closed = SimpleNamespace(type="TEMPORARY", width=0, height=0)
popover = SimpleNamespace(type="TEMPORARY", width=180, height=240)

assert not _regions_have_temporary((ordinary, closed))
assert _regions_have_temporary((ordinary, popover))
assert _visible_temporary_region(popover)
assert not _visible_temporary_region(None)


class WindowState:
    _bt_ignore_preexisting_temporary = True


window = WindowState()
original_detector = qt_window.blender_temporary_ui_active
try:
    qt_window.blender_temporary_ui_active = lambda: True
    assert not qt_window._temporary_popup_requires_suppression(window)
    assert window._bt_ignore_preexisting_temporary
    qt_window.blender_temporary_ui_active = lambda: False
    assert not qt_window._temporary_popup_requires_suppression(window)
    assert not window._bt_ignore_preexisting_temporary
    qt_window.blender_temporary_ui_active = lambda: True
    assert qt_window._temporary_popup_requires_suppression(window)
finally:
    qt_window.blender_temporary_ui_active = original_detector
print("BAKE_TOOLS_TEMPORARY_REGION_OK")
