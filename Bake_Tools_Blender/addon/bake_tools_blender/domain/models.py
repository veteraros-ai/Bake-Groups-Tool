"""Immutable data passed from Blender to the standalone Qt view."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ObjectRefView:
    name: str
    object_type: str


@dataclass(frozen=True, slots=True)
class SubgroupView:
    item_id: str
    name: str
    visible: bool
    locked: bool
    hp_members: tuple[ObjectRefView, ...]
    lp_members: tuple[ObjectRefView, ...]
    smooth_level: int
    cage_override: float
    color_index: int
    custom_color: tuple[float, float, float] | None

    @property
    def hp_count(self):
        return len(self.hp_members)

    @property
    def lp_count(self):
        return len(self.lp_members)


@dataclass(frozen=True, slots=True)
class ChapterView:
    item_id: str
    name: str
    book: str
    hp_object: str
    lp_object: str
    hp_root_kind: str
    lp_root_kind: str
    visible: bool
    groups_visible: bool
    expanded: bool
    subgroups: tuple[SubgroupView, ...]
    matcher_clusters: tuple["MatcherClusterView", ...]


@dataclass(frozen=True, slots=True)
class MatcherClusterView:
    item_id: str
    name: str
    title: str
    linked: bool
    already_grouped: bool
    score: float
    hp_members: tuple[ObjectRefView, ...]
    lp_members: tuple[ObjectRefView, ...]


@dataclass(frozen=True, slots=True)
class ManagerView:
    scene_name: str
    hp_object: str
    lp_object: str
    hp_root_kind: str
    lp_root_kind: str
    active_pair_id: str
    active_subgroup: int
    chapters: tuple[ChapterView, ...]
    group_name: str
    show_algorithm: bool
    color_subgroups: bool
    keep_hp_structure: bool
    hp_visible: bool
    lp_visible: bool
    cage_visible: bool
    active_has_cage: bool
    groups_visible: bool
    final_view: bool
    preview_smoothing: bool
    find_mode: str
    language: str
    hp_strategy: str
    optimization: str
    collision_pct: int
    ignore_floaters: bool
    adjacent_link: bool
    link_vertex: int
    link_distance: float
    matcher_tolerance: float
    matcher_min_hp_lp: int
    matcher_mode: str
    strict_geo_check: bool
    cage_wire: bool
    cage_status: str
    export_scope: str
    export_include_hp: bool
    export_include_lp: bool
    export_include_cage: bool
    export_has_cage: bool
    export_lp_triangulate: bool
    export_files: str
    export_by_material: bool
    export_lp_one_file: bool
    export_directory: str
    export_status: str
    log_text: str

    @property
    def active_chapter(self):
        return next((chapter for chapter in self.chapters if chapter.item_id == self.active_pair_id), None)
