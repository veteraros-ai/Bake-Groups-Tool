"""Blender ABI and numerical contract checks for bg_math_core_blender.pyd."""

from __future__ import annotations

import sys
from math import sqrt
from pathlib import Path


ADDON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_ROOT.parent))

from Bake_Tools_Blender.addon.bake_tools_blender import native_core  # noqa: E402


def main():
    assert native_core.available(), native_core.load_error()
    assert native_core.backend_name().startswith("C++ 0.1.0")

    left = ((0.0, 0.0, 0.0), (2.0, 0.0, 0.0))
    right = ((1.0, 0.0, 0.0),)
    assert abs(native_core.calculate_avg_distance(left, right) - 1.0) < 1.0e-6
    assert abs(native_core.calculate_bidirectional_avg_distance(left, right) - 1.0) < 1.0e-6
    assert abs(native_core.calculate_min_distance(left, right) - 1.0) < 1.0e-6

    cloud_a = tuple((index * 0.17, (index % 5) * 0.31, (index % 7) * -0.13) for index in range(37))
    cloud_b = tuple((index * -0.11 + 1.7, (index % 3) * 0.29, (index % 11) * 0.07) for index in range(53))
    expected_average = sum(
        min(sqrt(sum((point[axis] - target[axis]) ** 2 for axis in range(3))) for target in cloud_b)
        for point in cloud_a
    ) / len(cloud_a)
    expected_minimum = min(
        sqrt(sum((point[axis] - target[axis]) ** 2 for axis in range(3)))
        for point in cloud_a for target in cloud_b
    )
    assert abs(native_core.calculate_avg_distance(cloud_a, cloud_b) - expected_average) < 1.0e-5
    assert abs(native_core.calculate_min_distance(cloud_a, cloud_b) - expected_minimum) < 1.0e-5
    assert native_core.check_mesh_collision(((0.0, 0.0, 0.0),), ((0.09, 0.0, 0.0),), 0.1)
    assert not native_core.check_mesh_collision(((0.0, 0.0, 0.0),), ((0.19, 0.19, 0.0),), 0.1)

    fingerprint = native_core.generate_fingerprint(left, (1.0, 0.0, 0.0))
    assert fingerprint.startswith("v2_")
    metrics = native_core.analyze_mesh_shape(
        ((-2.0, 0.0, 0.0), (0.0, 0.5, 0.0), (2.0, 0.0, 0.0))
    )
    assert metrics.elongation >= 1.0
    assert len(metrics.center) == 3 and len(metrics.dimensions) == 3

    try:
        native_core.calculate_avg_distance(((0.0, 1.0),), right)
    except ValueError:
        pass
    else:
        raise AssertionError("Malformed xyz input was accepted")

    module = native_core._load()
    try:
        module.calculate_min_distance([0.0, 1.0], [0.0, 0.0, 0.0])
    except ValueError:
        pass
    else:
        raise AssertionError("C++ boundary accepted a malformed flat buffer")

    print("BAKE_TOOLS_NATIVE_CORE_OK", native_core.backend_name())


if __name__ == "__main__":
    main()
