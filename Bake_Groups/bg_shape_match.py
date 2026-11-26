# -*- coding: utf-8 -*-
"""Conservative matching for duplicate mesh point clouds.

Only compact, translation/rotation/reflection-invariant summaries are retained
between calls. Full point arrays are materialized temporarily for a comparison.
"""

from __future__ import division

import itertools

import numpy as np


class ShapeMatcher(object):
    """Match point clouds cached as ``{name: flat_xyz_values}``.

    The matcher deliberately does not normalize scale: uniformly scaled meshes
    are different shapes for baking purposes. Vertex count and every available
    point must agree within a small, scale-relative tolerance.
    """

    _RADIAL_QUANTILES = (0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0)

    def __init__(self, point_cache):
        self._point_cache = point_cache if point_cache is not None else {}
        self._features = {}

    def signature(self, name):
        """Return a compact invariant signature, or ``None`` for bad data.

        The tuple contains point count, RMS radius, normalized covariance
        eigenvalues, and normalized radial quantiles. It can cheaply screen
        candidates but is not proof that two meshes match.
        """
        feature = self._feature(name)
        if feature is None:
            return None
        return (feature["count"], feature["rms"],
                tuple(float(x) for x in feature["normalized_eigenvalues"]),
                tuple(float(x) for x in feature["normalized_radial_quantiles"]))

    def matches(self, name_a, name_b, allow_reflection=True):
        """Return whether both cached point clouds are congruent.

        Point order is ignored. The test permits translation, proper rotation,
        and (by default) reflection, but never scale. Invalid or empty samples
        fail closed.
        """
        if name_a == name_b:
            feature = self._feature(name_a)
            return bool(feature is not None and feature["count"] > 0)

        fa, fb = self._feature(name_a), self._feature(name_b)
        if fa is None or fb is None or fa["count"] != fb["count"]:
            return False
        tolerance = 1e-7 + 1e-4 * max(fa["rms"], fb["rms"])
        if not self._compatible_features(fa, fb, tolerance):
            return False

        try:
            points_a = self._points(name_a, fa["count"])
            points_b = self._points(name_b, fb["count"])
            if points_a is None or points_b is None:
                return False
            # A right-handed PCA frame removes translation and rotation. Its
            # remaining ambiguity is just axis signs; even sign changes are
            # proper rotations, odd sign changes are reflections.
            centered_a = points_a - fa["centroid"]
            centered_b = points_b - fb["centroid"]
            # Common frozen-scale mirrors and ordinary duplicates preserve
            # vertex indices. Test that correspondence first (including the
            # exact reflection policy) before order-independent matching.
            if self._same_indexed_cloud(centered_a, centered_b, tolerance,
                                        allow_reflection):
                return True
            canonical_a = np.dot(centered_a, fa["eigenvectors"])
            canonical_b = np.dot(centered_b, fb["eigenvectors"])
            signs = itertools.product((-1.0, 1.0), repeat=3)
            for sign_tuple in signs:
                if not allow_reflection and sign_tuple[0] * sign_tuple[1] * sign_tuple[2] < 0:
                    continue
                if self._same_unordered_cloud(
                        canonical_a, canonical_b * np.asarray(sign_tuple), tolerance):
                    return True
            # Repeated covariance eigenvalues leave an arbitrary orientation
            # within that subspace. Try bounded, rotation-invariant landmark
            # candidates; the full cloud is still checked after alignment.
            if self._degenerate_frame_match(centered_a, centered_b, tolerance,
                                            allow_reflection):
                return True
        except (ArithmeticError, TypeError, ValueError, np.linalg.LinAlgError):
            return False
        return False

    def _feature(self, name):
        if name in self._features:
            return self._features[name]
        try:
            raw = self._point_cache.get(name)
            if raw is None:
                self._features[name] = None
                return None
            flat = np.asarray(raw, dtype=np.float64)
            if flat.size == 0 or flat.size % 3:
                self._features[name] = None
                return None
            points = flat.reshape((-1, 3))
            if not np.isfinite(points).all():
                self._features[name] = None
                return None
            centroid = points.mean(axis=0)
            centered = points - centroid
            covariance = np.dot(centered.T, centered) / float(len(points))
            eigenvalues, eigenvectors = np.linalg.eigh(covariance)
            eigenvalues = np.maximum(eigenvalues, 0.0)[::-1]
            eigenvectors = eigenvectors[:, ::-1]
            if np.linalg.det(eigenvectors) < 0.0:
                eigenvectors[:, -1] *= -1.0
            radii = np.sqrt(np.einsum("ij,ij->i", centered, centered))
            rms = float(np.sqrt(np.mean(radii * radii)))
            if not np.isfinite(rms):
                self._features[name] = None
                return None
            safe_rms = max(rms, 1e-30)
            radial_quantiles = np.quantile(radii, self._RADIAL_QUANTILES)
            normalized_eigenvalues = eigenvalues / (safe_rms * safe_rms)
            normalized_radial_quantiles = radial_quantiles / safe_rms
            feature = {
                "count": int(len(points)),
                "centroid": centroid,
                "rms": rms,
                "eigenvectors": eigenvectors,
                "normalized_eigenvalues": normalized_eigenvalues,
                "normalized_radial_quantiles": normalized_radial_quantiles,
            }
            self._features[name] = feature
            return feature
        except (AttributeError, TypeError, ValueError, OverflowError, np.linalg.LinAlgError):
            self._features[name] = None
            return None

    def _points(self, name, expected_count):
        flat = np.asarray(self._point_cache.get(name), dtype=np.float64)
        if flat.size != expected_count * 3 or not np.isfinite(flat).all():
            return None
        return flat.reshape((-1, 3))

    @staticmethod
    def _compatible_features(a, b, tolerance):
        if abs(a["rms"] - b["rms"]) > tolerance:
            return False
        scale = max(a["rms"], b["rms"], 1e-30)
        # Invariant moments are only a prefilter; a little slack avoids
        # rejecting harmless floating-point noise in large point samples.
        relative = tolerance / scale * 4.0 + 1e-10
        return (np.all(np.abs(a["normalized_eigenvalues"] -
                              b["normalized_eigenvalues"]) <= relative) and
                np.all(np.abs(a["normalized_radial_quantiles"] -
                              b["normalized_radial_quantiles"]) <= relative))

    @staticmethod
    def _same_unordered_cloud(points_a, points_b, tolerance):
        """Check a one-to-one all-point match using a bounded spatial hash."""
        cell_size = max(tolerance, 1e-12)
        buckets = {}
        for index, point in enumerate(points_b):
            key = tuple(np.floor(point / cell_size).astype(np.int64))
            buckets.setdefault(key, []).append(index)

        unmatched = set(range(len(points_b)))
        neighbor_offsets = tuple(itertools.product((-1, 0, 1), repeat=3))
        # Assign the most constrained points first, reducing ambiguous greedy
        # choices when a mesh contains coincident or near-coincident vertices.
        candidate_rows = []
        for point in points_a:
            key = tuple(np.floor(point / cell_size).astype(np.int64))
            candidates = []
            for offset in neighbor_offsets:
                bucket_key = (key[0] + offset[0], key[1] + offset[1], key[2] + offset[2])
                candidates.extend(buckets.get(bucket_key, ()))
            candidates = [i for i in candidates
                          if float(np.linalg.norm(points_b[i] - point)) <= tolerance]
            if not candidates:
                return False
            candidate_rows.append((len(candidates), point, candidates))
        candidate_rows.sort(key=lambda row: row[0])

        for _count, point, candidates in candidate_rows:
            available = [i for i in candidates if i in unmatched]
            if not available:
                return False
            chosen = min(available, key=lambda i: float(np.linalg.norm(points_b[i] - point)))
            unmatched.remove(chosen)
        return not unmatched

    @staticmethod
    def _same_indexed_cloud(points_a, points_b, tolerance, allow_reflection):
        covariance = np.dot(points_a.T, points_b)
        u, _singular, vt = np.linalg.svd(covariance)
        rotation = np.dot(u, vt)
        if not allow_reflection and np.linalg.det(rotation) < 0.0:
            u[:, -1] *= -1.0
            rotation = np.dot(u, vt)
        delta = np.dot(points_a, rotation) - points_b
        residual = np.sqrt(np.einsum("ij,ij->i", delta, delta))
        return bool(np.max(residual) <= tolerance)

    @staticmethod
    def _degenerate_frame_match(points_a, points_b, tolerance, allow_reflection):
        """Try bounded farthest-point frames for repeated PCA eigenvalues.

        One farthest-from-centroid source anchor determines the first frame
        axis. Its most perpendicular source point determines the second. On
        the target, only up to eight radius-matched first anchors and eight
        dot/perpendicular-matched second anchors are considered.
        """
        if len(points_a) < 3 or len(points_b) != len(points_a):
            return False
        norms_a = np.sqrt(np.einsum("ij,ij->i", points_a, points_a))
        anchor_a_index = int(np.argmax(norms_a))
        anchor_a = points_a[anchor_a_index]
        radius_a = float(norms_a[anchor_a_index])
        if radius_a <= tolerance:
            return False

        unit_a = anchor_a / radius_a
        relative_a = points_a - np.outer(np.dot(points_a, unit_a), unit_a)
        perpendicular_a = np.sqrt(np.einsum("ij,ij->i", relative_a, relative_a))
        perpendicular_a[anchor_a_index] = -1.0
        second_a_index = int(np.argmax(perpendicular_a))
        second_a = points_a[second_a_index]
        second_dot_a = float(np.dot(anchor_a, second_a))
        second_perp_a = float(perpendicular_a[second_a_index])
        if second_perp_a <= tolerance:
            return False

        norms_b = np.sqrt(np.einsum("ij,ij->i", points_b, points_b))
        anchor_candidates = np.flatnonzero(np.abs(norms_b - radius_a) <= tolerance * 2.0)
        if not len(anchor_candidates):
            return False
        anchor_candidates = sorted(anchor_candidates,
                                   key=lambda index: abs(norms_b[index] - radius_a))[:8]
        signs = itertools.product((-1.0, 1.0), repeat=3)
        sign_choices = tuple(signs)

        for anchor_b_index in anchor_candidates:
            anchor_b = points_b[anchor_b_index]
            radius_b = float(norms_b[anchor_b_index])
            if radius_b <= tolerance:
                continue
            unit_b = anchor_b / radius_b
            relative_b = points_b - np.outer(np.dot(points_b, unit_b), unit_b)
            perpendicular_b = np.sqrt(np.einsum("ij,ij->i", relative_b, relative_b))
            dot_b = np.dot(points_b, anchor_b)
            perpendicular_b[anchor_b_index] = -1.0
            dot_b[anchor_b_index] = 0.0

            # Dot and perpendicular length jointly identify the second anchor
            # without depending on either cloud's orientation.
            dot_tolerance = tolerance * max(radius_a, radius_b, tolerance) * 4.0
            candidates = np.flatnonzero(
                (np.abs(dot_b - second_dot_a) <= dot_tolerance) &
                (np.abs(perpendicular_b - second_perp_a) <= tolerance * 2.0))
            if not len(candidates):
                continue
            candidates = sorted(
                candidates,
                key=lambda index: (abs(dot_b[index] - second_dot_a) /
                                   max(radius_b, tolerance) +
                                   abs(perpendicular_b[index] - second_perp_a)))[:8]

            frame_a = ShapeMatcher._landmark_frame(anchor_a, second_a)
            if frame_a is None:
                return False
            local_a = np.dot(points_a, frame_a)
            for second_b_index in candidates:
                frame_b = ShapeMatcher._landmark_frame(anchor_b, points_b[second_b_index])
                if frame_b is None:
                    continue
                local_b = np.dot(points_b, frame_b)
                for sign in sign_choices:
                    if not allow_reflection and sign[0] * sign[1] * sign[2] < 0:
                        continue
                    if ShapeMatcher._same_unordered_cloud(
                            local_a, local_b * np.asarray(sign), tolerance):
                        return True
        return False

    @staticmethod
    def _landmark_frame(anchor, second):
        x_axis = anchor.copy()
        x_length = float(np.linalg.norm(x_axis))
        if x_length <= 1e-15:
            return None
        x_axis /= x_length
        y_axis = second - x_axis * float(np.dot(second, x_axis))
        y_length = float(np.linalg.norm(y_axis))
        if y_length <= 1e-12:
            return None
        y_axis /= y_length
        z_axis = np.cross(x_axis, y_axis)
        return np.column_stack((x_axis, y_axis, z_axis))
