# -*- coding: utf-8 -*-
from __future__ import print_function, division, absolute_import

import time

# maya.cmds is intentionally NOT used inside this worker thread.
import bg_core

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

    def get_best_match(self, lp_data, lp_verts_flat):
        """
        Restored fast logic from the older worker:
        - bbox overlap prefilter
        - topology/position fast-confirm
        - one-way C++ distance: LP -> HP

        The previous v4 used bidirectional distance and weighted group scoring;
        that is more expensive and is not needed for the fast LP assignment pass.
        """
        best_grp = None
        min_avg_distance = float('inf')

        center_lp = self._center_from_min_max(lp_data)
        size_lp = [
            lp_data["max"][0] - lp_data["min"][0],
            lp_data["max"][1] - lp_data["min"][1],
            lp_data["max"][2] - lp_data["min"][2],
        ]
        diag_sq_lp = size_lp[0] * size_lp[0] + size_lp[1] * size_lp[1] + size_lp[2] * size_lp[2]

        for grp, hp_paths in self.hp_groups.items():
            if self.is_cancelled:
                return None

            fast_confirm = False

            for hp_path in hp_paths:
                hp_data = self.hp_data.get(hp_path)
                if not hp_data:
                    continue

                # Cheap broad-phase filter: only expensive C++ distance for plausible candidates.
                if not bg_core.MathUtils.is_overlapping(lp_data, hp_data, padding=1.05):
                    continue

                # Fast path for duplicated LP/HP topology in the same place.
                is_topo_match = (
                    lp_data.get("vtx", -1) == hp_data.get("vtx", -2) and
                    lp_data.get("edges", -1) == hp_data.get("edges", -2)
                )

                if is_topo_match:
                    size_hp = [
                        hp_data["max"][0] - hp_data["min"][0],
                        hp_data["max"][1] - hp_data["min"][1],
                        hp_data["max"][2] - hp_data["min"][2],
                    ]
                    dims_match = all(
                        abs(size_lp[i] - size_hp[i]) / (size_lp[i] if size_lp[i] > 1e-6 else 1e-6) < 0.001
                        for i in range(3)
                    )

                    if dims_match:
                        center_hp = self._center_from_min_max(hp_data)
                        dx = center_lp[0] - center_hp[0]
                        dy = center_lp[1] - center_hp[1]
                        dz = center_lp[2] - center_hp[2]
                        center_dist_sq = dx * dx + dy * dy + dz * dz

                        if center_dist_sq <= diag_sq_lp * 0.001:
                            fast_confirm = True
                            break

                hp_verts_flat = self.hp_verts_cache.get(hp_path, [])

                if HAS_MATH_CORE and hp_verts_flat and lp_verts_flat:
                    # Fast old behavior: one-way nearest-neighbour average LP -> HP.
                    avg_dist = bg_math_core.calculate_avg_distance(lp_verts_flat, hp_verts_flat)
                else:
                    # Safe fallback when .pyd is missing or some cache is empty.
                    center_hp = self._center_from_min_max(hp_data)
                    avg_dist = self._center_distance(center_lp, center_hp)

                if avg_dist < min_avg_distance:
                    min_avg_distance = avg_dist
                    best_grp = grp

            if fast_confirm:
                return grp

        if best_grp:
            threshold = lp_data.get("diag", 1.0) * self.lp_threshold_coef
            if "ZBrush" in best_grp or "zbrush" in best_grp.lower():
                threshold *= 3.0
            if min_avg_distance < threshold:
                return best_grp

        return None

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
