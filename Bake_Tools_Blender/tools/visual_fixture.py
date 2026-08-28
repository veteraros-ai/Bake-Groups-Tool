"""Populate a scene for visual comparison of the native Blender sidebar."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy


ADDON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_ROOT.parent))
import Bake_Tools_Blender as addon  # noqa: E402


addon.register()
state = bpy.context.scene.bake_tools_settings
state.pairs.clear()
state.log_text = "Ready."


def empty(name, location):
    obj = bpy.data.objects.new(name, None)
    obj.location = location
    bpy.context.collection.objects.link(obj)
    return obj


def create_chapter(name, book, subgroups, offset):
    hp = empty(name + "_HP", (offset, 0, 0))
    lp = empty(name + "_LP", (offset, 2, 0))
    bpy.context.view_layer.objects.active = hp
    hp.select_set(True)
    bpy.ops.bake_tools.pick_object(role="HP")
    hp.select_set(False)
    bpy.context.view_layer.objects.active = lp
    lp.select_set(True)
    bpy.ops.bake_tools.pick_object(role="LP")
    lp.select_set(False)
    state.group_name = name
    bpy.ops.bake_tools.create_pair()
    pair_id = state.active_pair_id
    if book:
        bpy.ops.bake_tools.pair_action(action="SET_BOOK", pair_id=pair_id, value=book)
    for subgroup in subgroups:
        bpy.ops.bake_tools.add_subgroup(name=subgroup)
    return pair_id


first = create_chapter(
    "Si_Fi_Gun_01",
    "Gun_01",
    ["Bolts_001", "Huge_001", "Huge_002", "Huge_003", "Huge_005", "Huge_008",
     "Large_001", "Large_002", "Large_003", "Large_004", "Medium_001", "Medium_002"],
    -2,
)
create_chapter("Si_Fi_Gun_02", "Gun_01", ["Body_001", "Trim_001"], 2)
bpy.ops.bake_tools.pair_action(action="ACTIVATE", pair_id=first)
state.show_algorithm = True
state.final_view = False
