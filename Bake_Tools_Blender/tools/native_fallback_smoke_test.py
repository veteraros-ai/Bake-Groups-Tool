"""Ensure an unavailable native extension preserves the Python math path."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace


os.environ["BAKE_TOOLS_DISABLE_NATIVE"] = "1"
ADDON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_ROOT.parent))

from Bake_Tools_Blender.addon.bake_tools_blender import native_core  # noqa: E402
from Bake_Tools_Blender.addon.bake_tools_blender.lp_matching_service import (  # noqa: E402
    _average_nearest_distance,
)


def main():
    assert not native_core.available()
    assert native_core.backend_name() == "Python fallback"
    assert "BAKE_TOOLS_DISABLE_NATIVE" in native_core.load_error()

    source = SimpleNamespace(vertices=((0.0, 0.0, 0.0), (2.0, 0.0, 0.0)), center=(1.0, 0.0, 0.0))
    target = SimpleNamespace(vertices=((1.0, 0.0, 0.0),), center=(1.0, 0.0, 0.0))
    distance = _average_nearest_distance(source, target, 128, {}, "target")
    assert abs(distance - 1.0) < 1.0e-6
    print("BAKE_TOOLS_NATIVE_FALLBACK_OK")


if __name__ == "__main__":
    main()
