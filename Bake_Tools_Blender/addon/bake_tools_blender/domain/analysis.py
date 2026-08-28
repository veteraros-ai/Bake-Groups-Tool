"""Pure data contracts for HP analysis.

These values deliberately contain no ``bpy`` or Qt objects.  Blender may build
them on its main thread and the grouping service may process them independently.
"""

from __future__ import annotations

from dataclasses import dataclass


Vec3 = tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class MeshSnapshot:
    key: str
    name: str
    bbox_min: Vec3
    bbox_max: Vec3
    center: Vec3
    dimensions: Vec3
    diagonal: float
    bbox_volume: float
    vertex_count: int
    edge_count: int
    face_count: int
    vertices: tuple[Vec3, ...]
    # Blender has no Maya display layers.  The main-thread adapter resolves the
    # BakeTools ZBrush collection/marker into this host-agnostic flag before the
    # worker starts.
    is_zbrush: bool = False
    # Imported assets can retain a previous Bake Groups semantic island in the
    # object name (for example ``ZBrush_Huge_001_high_004``).  Maya treats the
    # equivalent GT/custom island as hard input; keeping the hint here lets the
    # Blender service preserve it without parsing names in the worker itself.
    semantic_group: str = ""

    @property
    def topology(self):
        return self.vertex_count, self.edge_count, self.face_count


@dataclass(frozen=True, slots=True)
class AnalysisSettings:
    strategy: str = "VERTEX"
    optimization: str = "OPTIMAL"
    collision_pct: int = 15
    ignore_floaters: bool = True
    adjacent_link: bool = False
    link_vertex: int = 8
    link_distance_pct: float = 0.1
    use_symmetry: bool = True
    group_limit: int = 12
    # Metres represented by one Blender unit.  Maya's collision tolerance is
    # defined in centimetres and must be converted before the native query.
    unit_scale_meters: float = 1.0


@dataclass(frozen=True, slots=True)
class AnalysisGroup:
    name: str
    hp_keys: tuple[str, ...]
    lp_owner_keys: tuple[str, ...] = ()
    category: str = "Medium"


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    groups: tuple[AnalysisGroup, ...]
    processed_hp: int
    matched_hp: int
    unmatched_hp: int
    compound_components: int
    compound_links: int
    floater_links: int
    warnings: tuple[str, ...] = ()
    debug_lines: tuple[str, ...] = ()
