# -*- coding: utf-8 -*-
"""Optional Maya benchmark: local API material lookup versus legacy SG scans."""
from __future__ import print_function

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import maya.standalone

maya.standalone.initialize(name="python")

import maya.cmds as cmds


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_DIR = os.environ.get("BAKE_GROUPS_TEST_SCRIPT_DIR") or os.path.join(ROOT, "Bake_Groups")
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import bg_final_export


def _material(name):
    material = cmds.shadingNode("lambert", asShader=True, name=name)
    shading_group = cmds.sets(
        renderable=True, noSurfaceShader=True, empty=True,
        name=name + "SG")
    cmds.connectAttr(material + ".outColor", shading_group + ".surfaceShader", force=True)
    return shading_group


def run(mesh_count=120):
    cmds.file(new=True, force=True)
    root = cmds.group(empty=True, name="Benchmark_LP")
    sg_a = _material("Benchmark_A")
    sg_b = _material("Benchmark_B")
    meshes = []
    for index in range(mesh_count):
        mesh = cmds.polyPlane(
            name="Benchmark_low_{:03d}".format(index),
            subdivisionsX=4, subdivisionsY=1,
            constructionHistory=False)[0]
        mesh = cmds.parent(mesh, root)[0]
        mesh = (cmds.ls(mesh, long=True) or [mesh])[0]
        cmds.sets("{}.f[0:1]".format(mesh), edit=True, forceElement=sg_a)
        cmds.sets("{}.f[2:3]".format(mesh), edit=True, forceElement=sg_b)
        meshes.append(mesh)

    start = time.perf_counter()
    fast = [
        bg_final_export.FinalExportProcessor._get_mesh_materials_and_faces(mesh)
        for mesh in meshes
    ]
    fast_seconds = time.perf_counter() - start

    start = time.perf_counter()
    legacy = []
    for mesh in meshes:
        shape = cmds.listRelatives(
            mesh, shapes=True, fullPath=True,
            type="mesh", noIntermediate=True)[0]
        legacy.append(
            bg_final_export.FinalExportProcessor._get_mesh_materials_and_faces_legacy(
                mesh, shape))
    legacy_seconds = time.perf_counter() - start

    assert [sorted(map(len, item.values())) for item in fast] == \
        [sorted(map(len, item.values())) for item in legacy]
    ratio = legacy_seconds / max(fast_seconds, 1e-9)
    print("material lookup: meshes={} API={:.4f}s legacy={:.4f}s speedup={:.1f}x".format(
        mesh_count, fast_seconds, legacy_seconds, ratio))


if __name__ == "__main__":
    try:
        run()
    finally:
        maya.standalone.uninitialize()
