"""Persistent scene state for the Blender Bake Tools port.

The Qt window is intentionally not a second database.  Everything an artist
can change in the manager is stored here so it participates in .blend saves,
Blender undo/redo and scene switching.
"""

from __future__ import annotations

import bpy
from uuid import uuid4
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)


class BakeToolsObjectRef(bpy.types.PropertyGroup):
    """Persistent Blender Object pointer with a diagnostic rename fallback."""

    target: PointerProperty(name="Object", type=bpy.types.Object)
    last_name: StringProperty(name="Last Known Name", default="")


class BakeToolsSubgroup(bpy.types.PropertyGroup):
    item_id: StringProperty(name="Persistent ID", default="")
    name: StringProperty(name="Name", default="Main")
    visible: BoolProperty(name="Visible", default=True)
    locked: BoolProperty(name="Locked", default=False)
    hp_count: IntProperty(name="HP", default=0, min=0)
    lp_count: IntProperty(name="LP", default=0, min=0)
    hp_members: CollectionProperty(type=BakeToolsObjectRef)
    lp_members: CollectionProperty(type=BakeToolsObjectRef)
    smooth_level: IntProperty(name="Smooth", default=1, min=0, max=3)
    cage_override: FloatProperty(name="Cage Override", default=-1.0, min=-1.0, precision=3)
    color_index: IntProperty(name="Palette Index", default=-1, min=-1)
    use_custom_color: BoolProperty(name="Custom Color", default=False)
    custom_color: FloatVectorProperty(
        name="Subgroup Color", subtype="COLOR", size=3,
        default=(0.26, 0.52, 0.32), min=0.0, max=1.0,
    )


class BakeToolsMatcherCluster(bpy.types.PropertyGroup):
    """Persistent HP-LP Matcher proposal/manual link for one chapter."""

    item_id: StringProperty(name="Persistent ID", default="")
    name: StringProperty(name="Cluster", default="")
    title: StringProperty(name="Display Title", default="")
    linked: BoolProperty(name="Linked", default=False)
    already_grouped: BoolProperty(name="Already Grouped", default=False)
    score: FloatProperty(name="Score", default=0.0)
    hp_members: CollectionProperty(type=BakeToolsObjectRef)
    lp_members: CollectionProperty(type=BakeToolsObjectRef)


class BakeToolsPair(bpy.types.PropertyGroup):
    item_id: StringProperty(name="Persistent ID", default="")
    name: StringProperty(name="Chapter", default="Bake Group")
    book: StringProperty(name="Book", default="")
    hp_object: StringProperty(name="HP", default="")
    lp_object: StringProperty(name="LP", default="")
    hp_root: PointerProperty(name="HP Root", type=bpy.types.Object)
    lp_root: PointerProperty(name="LP Root", type=bpy.types.Object)
    hp_collection: PointerProperty(name="HP Collection", type=bpy.types.Collection)
    lp_collection: PointerProperty(name="LP Collection", type=bpy.types.Collection)
    hp_root_kind: StringProperty(name="HP Root Type", default="OBJECT")
    lp_root_kind: StringProperty(name="LP Root Type", default="OBJECT")
    visible: BoolProperty(name="Visible", default=True)
    groups_visible: BoolProperty(name="Groups Visible", default=True)
    cage_visible: BoolProperty(name="Cage Visible", default=True)
    expanded: BoolProperty(name="Expanded", default=True)
    material_slots: BoolProperty(
        name="LP Material Slots",
        description="Keep several LP materials in one chapter and distribute them during HP analysis",
        default=False,
    )
    scope_by_members: BoolProperty(
        name="Scoped Chapter",
        description="Limit this material-created chapter to explicit source objects",
        default=False,
    )
    hp_scope_members: CollectionProperty(type=BakeToolsObjectRef)
    lp_scope_members: CollectionProperty(type=BakeToolsObjectRef)
    subgroups: CollectionProperty(type=BakeToolsSubgroup)
    matcher_clusters: CollectionProperty(type=BakeToolsMatcherCluster)


def _strategy_items(_self, _context):
    return (
        ("SPATIAL", "Spatial Volume Match", "Match by spatial volume"),
        ("VERTEX", "Vertex Proximity", "Match by vertex proximity"),
        ("TOPOLOGY", "Topology Fingerprint", "Match by topology fingerprint"),
    )


def _optimization_items(_self, _context):
    return (
        ("OPTIMAL", "Optimal", "Use full resolution where possible"),
        ("SPEED", "Speed", "Use a tighter cache cap"),
    )


def _unit_items(_self, _context):
    return (
        ("PERCENT", "Percent of diagonal", "Resolve cage values as percentages"),
        ("ABSOLUTE", "World units", "Resolve cage values as world units"),
    )


def _match_mode_items(_self, _context):
    return (
        ("BALANCED", "Balanced", "Balanced HP/LP matching"),
        ("FAST", "Fast", "Prefer faster matching"),
        ("ACCURATE", "Accurate", "Prefer accurate geometry matches"),
    )


def _find_mode_items(_self, _context):
    return (
        ("SIM", "Find Sim", "Strict layout matching"),
        ("ALL", "Find All", "Ignore layout and find all candidates"),
    )


def _language_items(_self, _context):
    return (
        ("EN", "English", "English interface"),
        ("RU", "Русский", "Russian interface"),
        ("JA", "日本語", "Japanese interface"),
        ("ZH_CN", "简体中文", "Simplified Chinese interface"),
    )


def _export_scope_items(_self, _context):
    return (
        ("CHAPTER", "Active Chapter", "Export the active chapter"),
        ("BOOK", "Active Book", "Export the active book"),
        ("ALL", "All Books", "Export all books and chapters"),
    )


def _export_files_items(_self, _context):
    return (
        ("SEPARATE", "Separate", "Export separate files"),
        ("ONE", "HP+LP one file", "Export HP and LP together"),
    )


class BakeToolsSettings(bpy.types.PropertyGroup):
    hp_object: StringProperty(name="Picked HP", default="")
    lp_object: StringProperty(name="Picked LP", default="")
    hp_root: PointerProperty(name="Picked HP Root", type=bpy.types.Object)
    lp_root: PointerProperty(name="Picked LP Root", type=bpy.types.Object)
    hp_collection: PointerProperty(name="Picked HP Collection", type=bpy.types.Collection)
    lp_collection: PointerProperty(name="Picked LP Collection", type=bpy.types.Collection)
    hp_root_kind: StringProperty(name="Picked HP Root Type", default="")
    lp_root_kind: StringProperty(name="Picked LP Root Type", default="")
    zbrush_members: CollectionProperty(type=BakeToolsObjectRef)
    zbrush_triangle_threshold: IntProperty(
        name="ZBrush Triangular Faces (%)",
        description="Minimum triangular-face ratio used by Find ZBrush",
        default=50,
        min=1,
        max=100,
    )
    mesh_check_report: StringProperty(name="Last Mesh Check", default="")
    mesh_check_issue_count: IntProperty(name="Mesh Check Issues", default=0, min=0)
    mesh_check_payload: StringProperty(name="Mesh Check Actions", default="{}")
    pairs: CollectionProperty(type=BakeToolsPair)
    active_pair: IntProperty(name="Active Chapter", default=0, min=0)
    active_pair_id: StringProperty(name="Active Chapter ID", default="")
    chapter_isolated: BoolProperty(
        name="Isolate Active Chapter",
        description="Show only the active chapter, matching Maya isolateSelect behavior",
        default=True,
    )
    active_subgroup: IntProperty(name="Active Subgroup", default=0, min=0)
    group_name: StringProperty(name="Group Name", default="")
    show_algorithm: BoolProperty(name="Algorithm", default=False)

    color_subgroups: BoolProperty(name="Color HP", default=False)
    keep_hp_structure: BoolProperty(name="Keep HP", default=False)
    hp_visible: BoolProperty(name="HP Visible", default=True)
    lp_visible: BoolProperty(name="LP Visible", default=True)
    groups_visible: BoolProperty(name="Groups Vis", default=True)
    final_view: BoolProperty(name="Export Settings", default=False)
    preview_smoothing: BoolProperty(name="Smooth View", default=False)
    find_mode: EnumProperty(name="Find Mode", items=_find_mode_items, default=0)
    language: EnumProperty(name="Language", items=_language_items, default=0)

    hp_strategy: EnumProperty(name="HP Clustering Strategy", items=_strategy_items, default=1)
    optimization: EnumProperty(name="Optimization", items=_optimization_items, default=0)
    collision_pct: IntProperty(name="Collision (%)", default=15, min=0, max=100)
    ignore_floaters: BoolProperty(name="Ignore Floaters", default=True)
    adjacent_link: BoolProperty(name="Adjacent Vertex Link", default=False)
    link_vertex: IntProperty(name="Link Vertex", default=8, min=1, max=500)
    link_distance: FloatProperty(name="Link Dist (%)", default=0.1, min=0.01, max=25.0, precision=2)
    calculate_symmetry: BoolProperty(name="Calculate Symmetry Score (.pyd)", default=True)

    cage_inflate: FloatProperty(name="Inflate", default=0.0, min=0.0)
    cage_gap: FloatProperty(name="Gap", default=0.5, min=0.0)
    cage_unit: EnumProperty(name="Cage Units", items=_unit_items, default=0)
    cage_fitted: BoolProperty(name="Fitted", default=False)
    cage_wire: BoolProperty(name="Wireframe Cage", default=False)
    cage_export: BoolProperty(name="Export Cage", default=True)
    cage_status: StringProperty(name="Cage Status", default="No cage yet - press Create Cage.")
    cage_intersections_json: StringProperty(name="Cage Intersections", default="{}")

    gt_query: StringProperty(name="GT Query", default="")
    matcher_tolerance: FloatProperty(name="Tolerance (%)", default=5.0, min=0.0, max=100.0, precision=2)
    matcher_min_hp_lp: IntProperty(name="Min HP/LP", default=2, min=1, max=100)
    matcher_mode: EnumProperty(name="Match Mode", items=_match_mode_items, default=0)
    strict_geo_check: BoolProperty(name="Strict Geo Check", default=True)

    export_scope: EnumProperty(name="Export Scope", items=_export_scope_items, default=0)
    export_include_hp: BoolProperty(name="Include HP", default=True)
    export_include_lp: BoolProperty(name="Include LP", default=True)
    export_include_cage: BoolProperty(name="Include Cage", default=True)
    export_lp_triangulate: BoolProperty(
        name="LP Triangle",
        description="Temporarily triangulate LP meshes during FBX export",
        default=True,
    )
    export_files: EnumProperty(name="Export Files", items=_export_files_items, default=0)
    export_by_material: BoolProperty(name="By Material", default=False)
    export_lp_one_file: BoolProperty(name="LP in one file", default=False)
    export_directory: StringProperty(name="Export Directory", default="", subtype="DIR_PATH")
    export_status: StringProperty(name="Export Status", default="")
    log_text: StringProperty(name="Log", default="Ready.")
    action_history: StringProperty(name="User Action History", default="")
    debug_text: StringProperty(name="Debug Log", default="")


def ensure_state_ids(state):
    """Migrate older scenes to stable IDs without changing artist-visible data."""
    changed = False
    for role in ("hp", "lp"):
        root_attr = "{}_root".format(role)
        collection_attr = "{}_collection".format(role)
        kind_attr = "{}_root_kind".format(role)
        name_attr = "{}_object".format(role)
        root = getattr(state, root_attr, None)
        collection = getattr(state, collection_attr, None)
        kind = getattr(state, kind_attr, "")
        name = getattr(state, name_attr, "")
        if kind == "COLLECTION" and collection is None and name:
            collection = bpy.data.collections.get(name)
            if collection is not None:
                setattr(state, collection_attr, collection)
                changed = True
        elif root is None and name:
            root = bpy.data.objects.get(name)
            if root is not None:
                setattr(state, root_attr, root)
                changed = True
        if collection is not None and (kind == "COLLECTION" or root is None):
            if kind != "COLLECTION":
                setattr(state, kind_attr, "COLLECTION")
                changed = True
            if name != collection.name:
                setattr(state, name_attr, collection.name)
                changed = True
        elif root is not None and name != root.name:
            if kind != "OBJECT":
                setattr(state, kind_attr, "OBJECT")
                changed = True
            setattr(state, name_attr, root.name)
            changed = True
    for pair in state.pairs:
        if not pair.item_id:
            pair.item_id = uuid4().hex
            changed = True
        for role in ("hp", "lp"):
            root_attr = "{}_root".format(role)
            collection_attr = "{}_collection".format(role)
            kind_attr = "{}_root_kind".format(role)
            name_attr = "{}_object".format(role)
            root = getattr(pair, root_attr, None)
            collection = getattr(pair, collection_attr, None)
            kind = getattr(pair, kind_attr, "OBJECT")
            name = getattr(pair, name_attr, "")
            if kind == "COLLECTION" and collection is None and name:
                collection = bpy.data.collections.get(name)
                if collection is not None:
                    setattr(pair, collection_attr, collection)
                    changed = True
            elif root is None and name:
                root = bpy.data.objects.get(name)
                if root is not None:
                    setattr(pair, root_attr, root)
                    changed = True
            if collection is not None and kind == "COLLECTION":
                if name != collection.name:
                    setattr(pair, name_attr, collection.name)
                    changed = True
            elif root is not None and name != root.name:
                if kind != "OBJECT":
                    setattr(pair, kind_attr, "OBJECT")
                    changed = True
                setattr(pair, name_attr, root.name)
                changed = True
        used_color_indices = {
            int(subgroup.color_index) for subgroup in pair.subgroups
            if int(subgroup.color_index) >= 0
        }
        next_color_index = 0
        for subgroup in pair.subgroups:
            if not subgroup.item_id:
                subgroup.item_id = uuid4().hex
                changed = True
            if subgroup.color_index < 0:
                while next_color_index in used_color_indices:
                    next_color_index += 1
                subgroup.color_index = next_color_index
                used_color_indices.add(next_color_index)
                next_color_index += 1
                changed = True
            for refs in (subgroup.hp_members, subgroup.lp_members):
                for ref in refs:
                    if ref.target is not None and ref.last_name != ref.target.name:
                        ref.last_name = ref.target.name
                        changed = True
        for cluster in pair.matcher_clusters:
            if not cluster.item_id:
                cluster.item_id = uuid4().hex
                changed = True
            for refs in (cluster.hp_members, cluster.lp_members):
                for ref in refs:
                    if ref.target is not None and ref.last_name != ref.target.name:
                        ref.last_name = ref.target.name
                        changed = True
    for ref in state.zbrush_members:
        if ref.target is not None and ref.last_name != ref.target.name:
            ref.last_name = ref.target.name
            changed = True
    if state.pairs:
        state.active_pair = min(max(state.active_pair, 0), len(state.pairs) - 1)
        active = state.pairs[state.active_pair]
        if state.active_pair_id != active.item_id:
            state.active_pair_id = active.item_id
            changed = True
    elif state.active_pair_id:
        state.active_pair_id = ""
        changed = True
    return changed


PROPERTY_CLASSES = (
    BakeToolsObjectRef, BakeToolsSubgroup, BakeToolsMatcherCluster,
    BakeToolsPair, BakeToolsSettings,
)


def register_properties():
    if not hasattr(bpy.types.Scene, "bake_tools_settings"):
        bpy.types.Scene.bake_tools_settings = PointerProperty(type=BakeToolsSettings)


def unregister_properties():
    if hasattr(bpy.types.Scene, "bake_tools_settings"):
        del bpy.types.Scene.bake_tools_settings
