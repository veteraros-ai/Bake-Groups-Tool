"""Read-only Analyze HP diagnostic for the currently loaded .blend file."""

from __future__ import annotations

import sys
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace

import bpy


ADDON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_ROOT.parent))

import Bake_Tools_Blender as addon  # noqa: E402
from Bake_Tools_Blender.addon.bake_tools_blender.analysis_adapter import capture_analysis_input  # noqa: E402
from Bake_Tools_Blender.addon.bake_tools_blender.analysis_service import AnalysisService  # noqa: E402


def main():
    registered_here = not hasattr(bpy.types.Scene, "bake_tools_settings")
    if registered_here:
        addon.register()
    try:
        state = bpy.context.scene.bake_tools_settings
        pair = next((item for item in state.pairs if item.item_id == state.active_pair_id), None)
        if pair is None and state.pairs:
            pair = state.pairs[0]
        if pair is None:
            hp_root = bpy.data.objects.get("Suspension_02_HP")
            lp_root = bpy.data.objects.get("Suspension_02_LP")
            if hp_root is None or lp_root is None:
                raise RuntimeError("No Bake Tools chapter or Suspension_02 roots found")
            pair = SimpleNamespace(
                hp_root=hp_root,
                lp_root=lp_root,
                hp_root_kind="OBJECT",
                lp_root_kind="OBJECT",
                scope_by_members=False,
                material_slots=False,
                subgroups=(),
            )
            state.hp_strategy = "VERTEX"
            state.optimization = "OPTIMAL"
            state.collision_pct = 15
            state.ignore_floaters = True
            state.adjacent_link = False
            state.link_vertex = 8
            state.link_distance = 0.1
        hp, lp, settings, reserved, _objects = capture_analysis_input(
            bpy.context, pair, state
        )
        result = AnalysisService().analyze(hp, lp, settings, reserved)
        groups = OrderedDict((group.name, len(group.hp_keys)) for group in result.groups)
        print("SCENE", bpy.data.filepath or "Untitled")
        print("INPUT", len(hp), "HP", len(lp), "LP")
        print("ZBRUSH", sum(1 for mesh in hp if mesh.is_zbrush))
        print("GROUPS", dict(groups))
        print("WARNINGS", list(result.warnings))
        print("BAKE_TOOLS_ANALYSIS_SCENE_DIAGNOSTIC_OK")
    finally:
        if registered_here:
            addon.unregister()


if __name__ == "__main__":
    main()
