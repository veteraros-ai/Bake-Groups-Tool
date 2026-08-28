"""Render the complete native manager through Blender's panel draw path."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy


ADDON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_ROOT / "tools"))
import visual_fixture  # noqa: F401,E402


capture_path = ADDON_ROOT / "docs" / "screenshots" / "native_manager_panel.png"


def view3d_override():
    window = bpy.context.window_manager.windows[0]
    area = next(area for area in window.screen.areas if area.type == "VIEW_3D")
    area.spaces.active.show_region_ui = True
    region = next(region for region in area.regions if region.type == "WINDOW")
    return {"window": window, "screen": window.screen, "area": area, "region": region}


def capture_panel():
    with bpy.context.temp_override(**view3d_override()):
        bpy.ops.screen.screenshot(filepath=str(capture_path))
    assert capture_path.is_file()
    print("BAKE_TOOLS_NATIVE_PANEL_OK")
    bpy.ops.wm.quit_blender()
    return None


def open_panel():
    with bpy.context.temp_override(**view3d_override()):
        result = bpy.ops.wm.call_panel(name="BAKE_TOOLS_PT_main", keep_open=True)
    assert "CANCELLED" not in result
    bpy.app.timers.register(capture_panel, first_interval=0.75)
    return None


bpy.app.timers.register(open_panel, first_interval=0.50)
