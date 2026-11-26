# -*- coding: utf-8 -*-
from __future__ import print_function, division, absolute_import

import time

# maya.cmds is intentionally NOT used inside this worker thread.
import bg_core

try:
    import bg_math_core_runtime as bg_math_core
    HAS_MATH_CORE = True
except ImportError:
    try:
        import bg_math_core
        HAS_MATH_CORE = True
    except ImportError:
        bg_math_core = None
        HAS_MATH_CORE = False
        print("WARNING: bg_math_core (.pyd) not found! LP matching will use bbox-center fallback logic.")

try:
    from PySide6 import QtCore
except ImportError:
    from PySide2 import QtCore


class LPMatchingWorker(QtCore.QThread):
    progress_value = QtCore.Signal(int)
    progress_text = QtCore.Signal(str)
    finished = QtCore.Signal(dict)

    def __init__(self, hp_groups, hp_data_cache, lp_data_cache,
                 hp_verts_cache, lp_verts_cache_fast, lp_verts_cache_full,
                 lp_threshold_coef):
        """
        Fast LP -> HP group matcher.

        Important performance rule:
        This worker must not call Maya API/cmds. All geometry is pre-cached in
        bg_mixins on the main thread.
        """
        super(LPMatchingWorker, self).__init__()
        self.hp_groups = hp_groups
        self.hp_data = hp_data_cache
        self.lp_data = lp_data_cache

        self.hp_verts_cache = hp_verts_cache
        self.lp_verts_cache_fast = lp_verts_cache_fast
        self.lp_verts_cache_full = lp_verts_cache_full

        self.lp_threshold_coef = lp_threshold_coef
        self.is_cancelled = False

    @staticmethod
    def _center_from_min_max(data):
        return [
            (data["max"][0] + data["min"][0]) * 0.5,
            (data["max"][1] + data["min"][1]) * 0.5,
            (data["max"][2] + data["min"][2]) * 0.5,
        ]

    @staticmethod
    def _center_distance(center_a, center_b):
        dx = center_a[0] - center_b[0]
        dy = center_a[1] - center_b[1]
        dz = center_a[2] - center_b[2]
        return (dx * dx + dy * dy + dz * dz) ** 0.5

    def get_best_match(self, lp_data, lp_verts_flat, require_geometry=False):
        """Rank spatially plausible HP candidates by measured geometry."""
        center_lp = self._center_from_min_max(lp_data)
        best_key = None
        best_group = None
        for group, hp_paths in self.hp_groups.items():
            if self.is_cancelled:
                return None
            for hp_path in hp_paths:
                hp_data = self.hp_data.get(hp_path)
                if not hp_data or not bg_core.MathUtils.is_overlapping(
                        lp_data, hp_data, padding=1.05, include_flat=True):
                    continue

                hp_verts = self.hp_verts_cache.get(hp_path)
                measured = bool(HAS_MATH_CORE and hp_verts and lp_verts_flat)
                if require_geometry and not measured:
                    continue
                if measured:
                    vertex_count = lp_data.get("vtx", 0)
                    coord_count = vertex_count * 3
                    if (0 < coord_count <= min(len(lp_verts_flat), len(hp_verts)) and
                            vertex_count == hp_data.get("vtx") and
                            lp_data.get("edges") == hp_data.get("edges")):
                        # Compare the authored vertices. The LP and HP caches
                        # can contain different numbers of interpolated points.
                        low = lp_verts_flat[:coord_count]
                        high = hp_verts[:coord_count]
                        distance = max(bg_math_core.calculate_avg_distance(low, high),
                                       bg_math_core.calculate_avg_distance(high, low))
                    else:
                        distance = bg_math_core.calculate_avg_distance(lp_verts_flat, hp_verts)
                else:
                    distance = self._center_distance(center_lp, self._center_from_min_max(hp_data))
                threshold = lp_data.get("diag", 1.0) * self.lp_threshold_coef
                if "zbrush" in group.lower():
                    threshold *= 3.0
                if distance <= threshold:
                    key = (not measured, distance, group)
                    if best_key is None or key < best_key:
                        best_key, best_group = key, group
        return best_group

    def run(self):
        matches = {grp: set() for grp in self.hp_groups.keys()}
        unassigned_lp = list(self.lp_data.keys())

        self.progress_text.emit("Geometric surface analysis via C++ Kernel (Fast Pass)...")
        first_pass_misses = []

        total = max(len(unassigned_lp), 1)
        for i, lp_path in enumerate(unassigned_lp):
            if self.is_cancelled:
                return
            self.progress_value.emit(int((i / total) * 50))

            lp_verts_flat = self.lp_verts_cache_fast.get(lp_path)
            if not lp_verts_flat:
                # Do not silently drop it; try full cache in second pass.
                first_pass_misses.append(lp_path)
                continue

            best_grp = self.get_best_match(self.lp_data[lp_path], lp_verts_flat)
            if best_grp:
                matches[best_grp].add(lp_path)
            else:
                first_pass_misses.append(lp_path)

        if first_pass_misses:
            self.progress_text.emit("Precise tracking for unmatched LP...")
            total_misses = max(len(first_pass_misses), 1)
            for i, lp_path in enumerate(first_pass_misses):
                if self.is_cancelled:
                    return
                self.progress_value.emit(50 + int((i / total_misses) * 50))

                lp_verts_flat = self.lp_verts_cache_full.get(lp_path)
                if not lp_verts_flat:
                    continue

                best_grp = self.get_best_match(self.lp_data[lp_path], lp_verts_flat)
                if best_grp:
                    matches[best_grp].add(lp_path)

        self.progress_value.emit(100)
        self.progress_text.emit("Matching complete!")
        self.finished.emit(matches)

    def stop(self):
        self.is_cancelled = True
        self.quit()
        self.wait()
