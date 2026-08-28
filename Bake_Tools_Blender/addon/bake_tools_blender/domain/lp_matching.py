"""Host-independent contracts for the Assign LP stage."""

from __future__ import annotations

from dataclasses import dataclass

from .analysis import MeshSnapshot


@dataclass(frozen=True, slots=True)
class LPMatchGroup:
    name: str
    hp_meshes: tuple[MeshSnapshot, ...]


@dataclass(frozen=True, slots=True)
class LPMatchSettings:
    optimization: str = "OPTIMAL"
    threshold_coefficient: float = 1.5
    bbox_padding: float = 1.05


@dataclass(frozen=True, slots=True)
class LPAssignment:
    group_name: str
    lp_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LPMatchResult:
    assignments: tuple[LPAssignment, ...]
    processed_lp: int
    matched_lp: int
    unmatched_lp_keys: tuple[str, ...]
    material_repairs: int = 0
    warnings: tuple[str, ...] = ()
    debug_lines: tuple[str, ...] = ()
