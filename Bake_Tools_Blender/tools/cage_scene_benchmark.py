"""Measure exact raw Cage creation on the currently loaded production scene."""

from __future__ import annotations

import time

import addon_utils
import bpy


addon_utils.enable("Bake_Tools_Blender", default_set=False, persistent=False)

from Bake_Tools_Blender.addon.bake_tools_blender.cage_service import (  # noqa: E402
    CAGE_SOURCE,
    create_cages,
    delete_cages,
)


state = bpy.context.scene.bake_tools_settings
pair = next(
    (candidate for candidate in state.pairs if candidate.item_id == state.active_pair_id),
    state.pairs[0] if state.pairs else None,
)
if pair is None:
    raise RuntimeError("No Bake Tools chapter in the loaded scene")

started = time.perf_counter()
cages = create_cages(bpy.context, state, pair)
elapsed = time.perf_counter() - started
mismatches = []
for cage in cages:
    source = bpy.data.objects.get(str(cage.get(CAGE_SOURCE, "")))
    if source is None or len(source.data.vertices) != len(cage.data.vertices):
        mismatches.append(cage.name)
        continue
    if any(
        (cage_vertex.co - source_vertex.co).length > 1.0e-9
        for cage_vertex, source_vertex in zip(cage.data.vertices, source.data.vertices)
    ):
        mismatches.append(cage.name)

assert not mismatches, "Cage differs from raw LP: {}".format(mismatches[:5])
print(
    "BAKE_TOOLS_CAGE_SCENE_BENCHMARK count={} seconds={:.4f} per_object_ms={:.3f} exact=1".format(
        len(cages), elapsed, elapsed * 1000.0 / max(1, len(cages))
    )
)
delete_cages(state, pair)
