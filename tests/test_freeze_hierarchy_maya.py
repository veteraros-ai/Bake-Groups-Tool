# -*- coding: utf-8 -*-
from __future__ import print_function

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_DIR = os.path.join(ROOT, "Bake_Groups")
NATIVE_DIR = os.path.join(SCRIPT_DIR, "bin", "2027", "runtime")
if os.path.isdir(NATIVE_DIR) and NATIVE_DIR not in sys.path:
    sys.path.insert(0, NATIVE_DIR)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from bg_mixins import SceneInteractionMixin


class Harness(SceneInteractionMixin):
    pass


def _world_vertices(node):
    values = cmds.xform(node + ".vtx[*]", query=True, translation=True, worldSpace=True) or []
    return [float(value) for value in values]


def _close(left, right, tolerance=1e-5):
    return len(left) == len(right) and all(abs(a - b) <= tolerance for a, b in zip(left, right))


def run():
    cmds.file(new=True, force=True)
    root = cmds.group(empty=True, name="Freeze_Test_HP")
    subgroup = cmds.group(empty=True, name="Freeze_Group_HP", parent=root)
    mesh = cmds.polyCube(name="Freeze_Mesh_high_001", constructionHistory=False)[0]
    mesh = cmds.parent(mesh, subgroup)[0]

    cmds.setAttr(root + ".translate", 7.0, -3.0, 2.0, type="double3")
    cmds.setAttr(root + ".rotate", 15.0, 25.0, -10.0, type="double3")
    cmds.setAttr(root + ".scale", 1.5, 0.75, 2.0, type="double3")
    cmds.setAttr(subgroup + ".translate", -1.0, 4.0, 0.5, type="double3")
    cmds.setAttr(subgroup + ".rotate", 0.0, 35.0, 12.0, type="double3")
    cmds.setAttr(mesh + ".translate", 0.25, 0.5, -0.75, type="double3")

    harness = Harness()
    hierarchy = harness._freeze_transform_hierarchy_nodes([root], [root])
    assert [node.split("|")[-1] for node in hierarchy] == [root, subgroup, mesh]
    invalid = harness._unfrozen_transforms(hierarchy)
    assert len(invalid) == 3
    assert not harness._freeze_transform_blockers(hierarchy, invalid)

    before = _world_vertices(mesh)
    harness._apply_freeze_transform_hierarchy(hierarchy)
    after = _world_vertices(mesh)
    assert _close(before, after)
    assert not harness._unfrozen_transforms(hierarchy)

    cmds.setAttr(mesh + ".translateX", 1.0)
    cmds.setAttr(mesh + ".translateX", lock=True)
    hierarchy = harness._freeze_transform_hierarchy_nodes([root], [root])
    invalid = harness._unfrozen_transforms(hierarchy)
    blockers = harness._freeze_transform_blockers(hierarchy, invalid)
    assert blockers and blockers[0][0].split("|")[-1] == mesh
    print("Freeze hierarchy tests passed")


if __name__ == "__main__":
    run()
