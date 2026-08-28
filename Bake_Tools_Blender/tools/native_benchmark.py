"""Small reproducible C++/Python nearest-distance comparison inside Blender."""

from __future__ import annotations

from math import sqrt
import sys
from pathlib import Path
from time import perf_counter


ADDON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_ROOT.parent))

from Bake_Tools_Blender.addon.bake_tools_blender import native_core  # noqa: E402
from Bake_Tools_Blender.addon.bake_tools_blender.lp_matching_service import (  # noqa: E402
    _build_kd,
    _nearest_squared,
)


def main():
    target = tuple(
        ((index % 97) * 0.019, ((index * 7) % 89) * 0.017, ((index * 13) % 83) * 0.023)
        for index in range(4000)
    )
    source = tuple(
        ((index % 71) * 0.021 + 0.004, ((index * 5) % 67) * 0.020, ((index * 11) % 73) * 0.018)
        for index in range(1500)
    )

    start = perf_counter()
    native_value = native_core.calculate_avg_distance(source, target)
    native_seconds = perf_counter() - start

    start = perf_counter()
    tree = _build_kd(target)
    python_value = sum(sqrt(_nearest_squared(tree, point)) for point in source) / len(source)
    python_seconds = perf_counter() - start

    assert abs(native_value - python_value) < 1.0e-5
    speedup = python_seconds / max(native_seconds, 1.0e-9)
    print(
        "BAKE_TOOLS_NATIVE_BENCHMARK native={:.6f}s python={:.6f}s speedup={:.2f}x distance={:.8f}".format(
            native_seconds, python_seconds, speedup, native_value
        )
    )


if __name__ == "__main__":
    main()
