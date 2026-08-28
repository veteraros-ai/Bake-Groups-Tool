"""Headless acceptance test for real Analyze HP membership."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import bpy


ADDON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_ROOT.parent))

import Bake_Tools_Blender as addon  # noqa: E402


def make_root(name):
    obj = bpy.data.objects.new(name, None)
    bpy.context.collection.objects.link(obj)
    return obj


def make_cube(name, location, parent, scale=1.0):
    bpy.ops.mesh.primitive_cube_add(location=location, scale=(scale, scale, scale))
    obj = bpy.context.active_object
    obj.name = name
    obj.parent = parent
    return obj


def select_only(*objects):
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0] if objects else None


def all_hp_members(pair):
    return [
        ref.target for subgroup in pair.subgroups for ref in subgroup.hp_members
        if ref.target is not None
    ]


def main():
    addon.register()
    state = bpy.context.scene.bake_tools_settings
    hp_root = make_root("ANALYSIS_HP")
    lp_root = make_root("ANALYSIS_LP")

    hp_a = make_cube("HP_A", (0.0, 0.0, 0.0), hp_root, 0.85)
    hp_detail = make_cube("HP_A_Detail", (0.0, 0.0, 0.0), hp_root, 0.25)
    hp_b = make_cube("HP_B", (10.0, 0.0, 0.0), hp_root, 0.85)
    hp_overlap_1 = make_cube("HP_Overlap_1", (30.0, 0.0, 0.0), hp_root, 1.0)
    hp_overlap_2 = make_cube("HP_Overlap_2", (30.0, 0.0, 0.0), hp_root, 1.0)
    hp_locked = make_cube("HP_Locked", (100.0, 0.0, 0.0), hp_root, 1.0)
    lp_a = make_cube("LP_A", (0.0, 0.0, 0.0), lp_root, 1.0)
    make_cube("LP_B", (10.0, 0.0, 0.0), lp_root, 1.0)

    bpy.context.view_layer.objects.active = hp_root
    bpy.ops.bake_tools.pick_object(role="HP")
    bpy.context.view_layer.objects.active = lp_root
    bpy.ops.bake_tools.pick_object(role="LP")
    state.group_name = "AnalysisChapter"
    assert "FINISHED" in bpy.ops.bake_tools.create_pair()
    pair = state.pairs[0]

    select_only(hp_locked)
    assert "FINISHED" in bpy.ops.bake_tools.add_subgroup(name="Locked_Custom")
    locked = pair.subgroups[0]
    assert "FINISHED" in bpy.ops.bake_tools.subgroup_action(
        action="TOGGLE_LOCK", subgroup_id=locked.item_id
    )

    # An old unlocked LP group must survive HP recalculation with its LP side intact.
    select_only(lp_a)
    assert "FINISHED" in bpy.ops.bake_tools.add_subgroup(name="Legacy_LP")
    assert "FINISHED" in bpy.ops.bake_tools.analyze_hp()

    assert locked.locked
    assert [ref.target for ref in locked.hp_members] == [hp_locked]
    legacy = next(group for group in pair.subgroups if group.name == "Legacy_LP")
    assert [ref.target for ref in legacy.lp_members] == [lp_a]
    assert len(legacy.hp_members) == 0

    expected = {hp_a, hp_detail, hp_b, hp_overlap_1, hp_overlap_2, hp_locked}
    actual = all_hp_members(pair)
    assert set(actual) == expected
    assert len(actual) == len(set(actual))
    generated = [group for group in pair.subgroups if group.name not in {"Locked_Custom", "Legacy_LP"}]
    assert generated
    # The two identical overlapping leftovers cannot share one bake bucket.
    overlap_groups = [
        group for group in generated
        if any(ref.target in {hp_overlap_1, hp_overlap_2} for ref in group.hp_members)
    ]
    assert len(overlap_groups) == 2
    assert "Analyze HP:" in state.log_text
    assert "LP_OWNER HP_A -> LP_A" in state.debug_text
    assert "math backend=C++ 0.1.0 (Blender)" in state.debug_text

    first_assignment = {
        ref.target.name: group.name
        for group in pair.subgroups for ref in group.hp_members if ref.target is not None
    }
    assert "FINISHED" in bpy.ops.bake_tools.analyze_hp()
    second_assignment = {
        ref.target.name: group.name
        for group in pair.subgroups for ref in group.hp_members if ref.target is not None
    }
    assert first_assignment == second_assignment

    # Pure worker path: topology-identical, separated parts are bolt candidates.
    from Bake_Tools_Blender.addon.bake_tools_blender.analysis_service import AnalysisService
    from Bake_Tools_Blender.addon.bake_tools_blender.domain.analysis import AnalysisSettings, MeshSnapshot

    def snapshot(key, x):
        return MeshSnapshot(
            key=key, name=key, bbox_min=(x, 0.0, 0.0), bbox_max=(x + 1.0, 1.0, 1.0),
            center=(x + 0.5, 0.5, 0.5), dimensions=(1.0, 1.0, 1.0),
            diagonal=3.0 ** 0.5, bbox_volume=1.0, vertex_count=8,
            edge_count=12, face_count=6, vertices=((x, 0.0, 0.0), (x + 1.0, 1.0, 1.0)),
        )

    result = AnalysisService().analyze(
        (snapshot("bolt_a", 0.0), snapshot("bolt_b", 4.0), snapshot("bolt_c", 8.0)),
        (), AnalysisSettings(strategy="TOPOLOGY")
    )
    assert len(result.groups) == 1
    assert result.groups[0].name == "Bolts_001"
    assert set(result.groups[0].hp_keys) == {"bolt_a", "bolt_b", "bolt_c"}

    # A Maya round-trip can retain explicit semantic islands in object names.
    # They are the Blender equivalent of hard GT/custom clusters and must not be
    # mixed by AABB packing.  ZBrush is a separate family even when geometry is
    # spatially identical to a normal mesh.
    semantic = (
        replace(snapshot("Chapter_Bolts_001_high_001", 0.0), semantic_group="Bolts_001"),
        replace(snapshot("Chapter_Bolts_001_high_002", 0.0), semantic_group="Bolts_001"),
        replace(
            snapshot("Chapter_ZBrush_Huge_001_high_001", 0.0),
            is_zbrush=True,
            semantic_group="ZBrush_Huge_001",
        ),
        replace(
            snapshot("Chapter_ZBrush_Huge_001_high_002", 0.0),
            is_zbrush=True,
            semantic_group="ZBrush_Huge_001",
        ),
    )
    semantic_result = AnalysisService().analyze(semantic, (), AnalysisSettings())
    semantic_groups = {group.name: set(group.hp_keys) for group in semantic_result.groups}
    assert set(semantic_groups) == {"Bolts_001", "ZBrush_Huge_001"}
    assert len(semantic_groups["Bolts_001"]) == 2
    assert len(semantic_groups["ZBrush_Huge_001"]) == 2

    # A shared LP owner is context, not an unconditional hard union.  Maya can
    # place HP owned by the same LP into different collision-safe buckets.
    shared_owner = replace(snapshot("shared_lp", -1.0), dimensions=(12.0, 2.0, 2.0),
                           bbox_min=(-1.0, -0.5, -0.5), bbox_max=(11.0, 1.5, 1.5),
                           diagonal=(12.0 ** 2 + 2.0 ** 2 + 2.0 ** 2) ** 0.5,
                           bbox_volume=48.0,
                           vertices=((-1.0, -0.5, -0.5), (11.0, 1.5, 1.5)))
    owner_context = AnalysisService().analyze(
        (snapshot("owned_a", 0.0), snapshot("owned_b", 10.0)),
        (shared_owner,),
        AnalysisSettings(strategy="SPATIAL"),
    )
    assert owner_context.matched_hp == 2
    assert owner_context.compound_components == 0

    # Adjacent linking is based on sampled world vertices and its user threshold.
    touch_a = snapshot("touch_a", 0.0)
    touch_b = replace(snapshot("touch_b", 1.0), vertices=((1.0, 1.0, 1.0), (2.0, 1.0, 1.0)))
    touching = AnalysisService().analyze(
        (touch_a, touch_b), (),
        AnalysisSettings(strategy="VERTEX", adjacent_link=True, link_vertex=1, link_distance_pct=1.0)
    )
    assert touching.compound_links == 1
    assert touching.compound_components == 1

    # In keeping with Maya, unchecking Ignore Floaters enables the floater pass.
    parent = MeshSnapshot(
        key="parent", name="parent", bbox_min=(0.0, 0.0, 0.0), bbox_max=(10.0, 10.0, 10.0),
        center=(5.0, 5.0, 5.0), dimensions=(10.0, 10.0, 10.0), diagonal=300.0 ** 0.5,
        bbox_volume=1000.0, vertex_count=8, edge_count=12, face_count=6,
        vertices=((0.0, 0.0, 0.0), (10.0, 10.0, 10.0)),
    )
    floater = MeshSnapshot(
        key="floater", name="floater", bbox_min=(10.001, 4.9, 4.9), bbox_max=(10.101, 5.1, 5.1),
        center=(10.051, 5.0, 5.0), dimensions=(0.1, 0.2, 0.2), diagonal=0.3,
        bbox_volume=0.004, vertex_count=8, edge_count=12, face_count=6,
        vertices=((10.001, 4.9, 4.9), (10.101, 5.1, 5.1)),
    )
    with_floaters = AnalysisService().analyze(
        (parent, floater), (), AnalysisSettings(ignore_floaters=False)
    )
    assert with_floaters.floater_links == 1

    addon.unregister()
    print("BAKE_TOOLS_ANALYSIS_SMOKE_OK")


if __name__ == "__main__":
    main()
