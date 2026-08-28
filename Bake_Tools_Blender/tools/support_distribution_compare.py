"""Compare Maya support-package HP groups with Blender semantic preservation.

Run with Blender so the installed add-on and its optional native module are
resolved exactly as they are in production::

    blender --background --factory-startup --python support_distribution_compare.py -- maya.zip blender.zip

The Blender package contains complete HP membership.  Maya's scene snapshot
contains authoritative final subgroup names/counts.  This diagnostic rebuilds a
host-agnostic plan from the shared object names and verifies the round-trip hard
semantic islands used by the corrected analysis service.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from zipfile import ZipFile


ADDON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_ROOT.parent))

from Bake_Tools_Blender.addon.bake_tools_blender.analysis_adapter import (  # noqa: E402
    _semantic_group_from_name,
)
from Bake_Tools_Blender.addon.bake_tools_blender.analysis_service import AnalysisService  # noqa: E402
from Bake_Tools_Blender.addon.bake_tools_blender.domain.analysis import (  # noqa: E402
    AnalysisSettings,
    MeshSnapshot,
)


def _read_zip(path, member):
    with ZipFile(path) as archive:
        return archive.read(member).decode("utf-8-sig")


def _maya_groups(path):
    snapshot = _read_zip(path, "scene_snapshot.txt")
    return {
        match.group(1): int(match.group(2))
        for match in re.finditer(r"^  (.+?)_HP \| count=(\d+)$", snapshot, re.MULTILINE)
    }


def _blender_hp_names(path):
    pairs = json.loads(_read_zip(path, "session_pairs.json"))
    return tuple(
        name
        for pair in pairs
        for subgroup in pair.get("subgroups", ())
        for name in subgroup.get("hp_members", ())
    )


def _snapshot(name, index):
    semantic_group = _semantic_group_from_name(name)
    x = float(index * 2)
    return MeshSnapshot(
        key=name,
        name=name,
        bbox_min=(x, 0.0, 0.0),
        bbox_max=(x + 1.0, 1.0, 1.0),
        center=(x + 0.5, 0.5, 0.5),
        dimensions=(1.0, 1.0, 1.0),
        diagonal=3.0 ** 0.5,
        bbox_volume=1.0,
        vertex_count=8,
        edge_count=12,
        face_count=6,
        vertices=((x, 0.0, 0.0), (x + 1.0, 1.0, 1.0)),
        is_zbrush=semantic_group.startswith("ZBrush_"),
        semantic_group=semantic_group,
    )


def main():
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else ()
    if len(args) != 2:
        raise SystemExit("Expected: Maya_support.zip Blender_support.zip")
    maya_groups = _maya_groups(Path(args[0]))
    hp_names = _blender_hp_names(Path(args[1]))
    snapshots = tuple(_snapshot(name, index) for index, name in enumerate(hp_names))
    result = AnalysisService().analyze(snapshots, (), AnalysisSettings())
    blender_groups = {group.name: len(group.hp_keys) for group in result.groups}
    print("MAYA", json.dumps(maya_groups, sort_keys=True))
    print("BLENDER_CORRECTED", json.dumps(blender_groups, sort_keys=True))
    print("ZBRUSH_HP", sum(1 for mesh in snapshots if mesh.is_zbrush))
    assert len(hp_names) == len(set(hp_names)), "Blender support package contains duplicate HP membership"
    assert maya_groups == blender_groups, (maya_groups, blender_groups)
    print("BAKE_TOOLS_SUPPORT_DISTRIBUTION_PARITY_OK")


if __name__ == "__main__":
    main()
