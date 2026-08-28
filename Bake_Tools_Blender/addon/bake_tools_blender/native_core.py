"""Lazy, host-safe adapter for the optional Blender C++ math accelerator.

The extension consumes plain flattened world-space coordinates.  It never
receives ``bpy`` objects, so analysis services remain testable and thread-safe.
Every caller keeps a Python implementation as a correctness fallback.
"""

from __future__ import annotations

from importlib import import_module
from math import isfinite
import os
from threading import Lock


_MODULE_NAME = ".native.bg_math_core_blender"
_lock = Lock()
_module = None
_load_attempted = False
_load_error = ""


def _load():
    global _module, _load_attempted, _load_error
    if _load_attempted:
        return _module
    with _lock:
        if _load_attempted:
            return _module
        if os.environ.get("BAKE_TOOLS_DISABLE_NATIVE", "").strip().lower() in {"1", "true", "yes"}:
            _module = None
            _load_error = "Disabled by BAKE_TOOLS_DISABLE_NATIVE"
            _load_attempted = True
            return None
        try:
            _module = import_module(_MODULE_NAME, package=__package__)
            _load_error = ""
        except Exception as exc:  # ABI/load errors must never disable the add-on.
            _module = None
            _load_error = "{}: {}".format(type(exc).__name__, exc)
        _load_attempted = True
    return _module


def available():
    return _load() is not None


def backend_name():
    module = _load()
    if module is None:
        return "Python fallback"
    return "C++ {} ({})".format(
        getattr(module, "__version__", "unknown"),
        getattr(module, "host", "CPython"),
    )


def load_error():
    _load()
    return _load_error


def _flat(points):
    values = []
    extend = values.extend
    for point in points:
        if len(point) < 3:
            raise ValueError("Vertex coordinates must contain xyz")
        xyz = (float(point[0]), float(point[1]), float(point[2]))
        if not all(isfinite(value) for value in xyz):
            raise ValueError("Vertex coordinates contain NaN or infinity")
        extend(xyz)
    return values


def calculate_avg_distance(source_points, target_points):
    module = _load()
    if module is None:
        return None
    return float(module.calculate_avg_distance(_flat(source_points), _flat(target_points)))


def calculate_bidirectional_avg_distance(left_points, right_points):
    module = _load()
    if module is None:
        return None
    return float(module.calculate_bidirectional_avg_distance(_flat(left_points), _flat(right_points)))


def calculate_min_distance(left_points, right_points):
    module = _load()
    if module is None:
        return None
    return float(module.calculate_min_distance(_flat(left_points), _flat(right_points)))


def check_mesh_collision(left_points, right_points, threshold):
    module = _load()
    if module is None:
        return None
    return bool(module.check_mesh_collision(_flat(left_points), _flat(right_points), float(threshold)))


def are_symmetric(left_points, right_points, tolerance=0.01):
    module = _load()
    if module is None:
        return None
    return bool(module.are_symmetric(_flat(left_points), _flat(right_points), float(tolerance)))


def generate_fingerprint(points, center):
    module = _load()
    if module is None:
        return None
    return str(module.generate_fingerprint_data(_flat(points), [float(value) for value in center[:3]]))


def analyze_mesh_shape(points):
    module = _load()
    if module is None:
        return None
    return module.analyze_mesh_shape(_flat(points))


def calculate_vertex_owner_scores(lp_point_sets, hp_point_sets, candidate_pairs):
    module = _load()
    if module is None:
        return None
    return module.calculate_vertex_owner_scores(
        [_flat(points) for points in lp_point_sets],
        [_flat(points) for points in hp_point_sets],
        [(int(lp_index), int(hp_index)) for lp_index, hp_index in candidate_pairs],
    )
