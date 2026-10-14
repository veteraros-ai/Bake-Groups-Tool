# -*- coding: utf-8 -*-
"""Topology-safe cage export regressions; run directly with Autodesk mayapy.

Uses a fresh Maya standalone process and disposable scenes, never the user's
open application. Both captured export buffers and real FBX imports are checked.
"""
from __future__ import print_function

import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import maya.standalone

maya.standalone.initialize(name="python")

import maya.cmds as cmds
import maya.api.OpenMaya as om

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_DIR = os.environ.get("BAKE_GROUPS_TEST_SCRIPT_DIR") or os.path.join(ROOT, "Bake_Groups")
sys.path.insert(0, SCRIPT_DIR)

import bg_cage
import bg_final_export

Processor = bg_final_export.FinalExportProcessor
Cages = bg_cage.CageProcessor


def _long(node):
    return cmds.ls(node, long=True)[0]


def _mesh(node):
    return Processor._export_mesh_fn(node)


def _points(node, space=om.MSpace.kWorld):
    return tuple((p.x, p.y, p.z) for p in _mesh(node).getPoints(space))


def _signature(node):
    mesh = _mesh(node)
    counts, vertices = mesh.getVertices()
    return mesh.numVertices, tuple(counts), tuple(vertices)


def _buffers(node):
    mesh = _mesh(node)
    shaders, assignments = mesh.getConnectedShaders(mesh.dagPath().instanceNumber())
    return {
        "topology": _signature(node),
        "points": _points(node),
        "object_points": _points(node, om.MSpace.kObject),
        "normals": tuple(tuple(n) for n in mesh.getNormals()),
        "normal_ids": tuple(tuple(a) for a in mesh.getNormalIds()),
        "corner_normals": tuple(tuple(mesh.getFaceVertexNormal(face, vertex))
                                for face in range(mesh.numPolygons)
                                for vertex in mesh.getPolygonVertices(face)),
        "uvs": tuple((name, tuple(tuple(a) for a in mesh.getUVs(name)),
                      tuple(tuple(a) for a in mesh.getAssignedUVs(name)))
                     for name in mesh.getUVSetNames()),
        "materials": (tuple(om.MFnDependencyNode(s).name() for s in shaders), tuple(assignments)),
        "matrix": tuple(cmds.xform(node, query=True, matrix=True, worldSpace=True)),
    }


def _dag_nodes():
    return set(cmds.ls(dag=True, long=True) or [])


class CagePairExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cmds.loadPlugin("fbxmaya", quiet=True)

    def setUp(self):
        cmds.file(new=True, force=True)
        self.output = tempfile.TemporaryDirectory(prefix="bg_cage_pair_export_")
        self.addCleanup(self.output.cleanup)
        lp_root = cmds.group(empty=True, name="Original_LP")
        cage_root = cmds.group(empty=True, name="Original_Cages")
        self.lp = cmds.polyPlane(name="Chapter_Group_low_001", sx=1, sy=1,
                                 constructionHistory=False)[0]
        self.lp = _long(cmds.parent(self.lp, lp_root)[0])
        self.cage = _long(cmds.parent(
            cmds.duplicate(self.lp, name="Chapter_Group_low_001_cage")[0], cage_root)[0])
        Cages.bind_source(self.lp, self.cage)
        # This changes Maya's geometry-dependent diagonal WITHOUT changing the
        # ordered polygon topology. Independent cage triangulation is incorrect.
        mesh = _mesh(self.cage)
        points = mesh.getPoints()
        points[0].x = -0.3
        points[0].z = -0.3
        for p in points:
            p.y += 0.2
        mesh.setPoints(points)
        Cages.validate_pair(self.lp, self.cage)
        self.assertNotEqual(tuple(_mesh(self.lp).getTriangles()[1]),
                            tuple(_mesh(self.cage).getTriangles()[1]))
        self.originals = {n: _buffers(n) for n in (self.lp, self.cage)}
        self.original_dag = _dag_nodes()

    def path(self, name="export.fbx"):
        return os.path.join(self.output.name, name).replace("\\", "/")

    def export(self, nodes, path=None, **kwargs):
        with Processor.export_session():
            return Processor._export_with_lp_triangulation_rollback(
                nodes, path or self.path(), **kwargs)

    def assert_originals_preserved(self):
        for node, before in self.originals.items():
            self.assertEqual(before, _buffers(node), node)
        self.assertEqual(self.original_dag, _dag_nodes(), "Orphan export geometry")
        self.assertFalse(cmds.ls("BG_Export_Tri_Temp*", long=True))

    def capture(self, nodes, **kwargs):
        captures = []

        def capture_export(path):
            captures.append({n.split("|")[-1]: _buffers(n)
                             for n in cmds.ls(selection=True, long=True)})

        with mock.patch.object(Processor, "export_selected_fbx", side_effect=capture_export):
            self.assertTrue(self.export(nodes, **kwargs))
        self.assert_originals_preserved()
        self.assertEqual(1, len(captures))
        return captures[0]

    def test_lp_and_cage_use_identical_lp_triangles(self):
        capture = self.capture([self.lp, self.cage])
        low = capture[self.lp.split("|")[-1]]
        cage = capture[self.cage.split("|")[-1]]
        self.assertEqual(low["topology"], cage["topology"])
        self.assertTrue(all(count == 3 for count in cage["topology"][1]))
        self.assertEqual(self.originals[self.lp]["points"], low["points"])
        self.assertEqual(self.originals[self.cage]["points"], cage["points"])
        self.assertEqual(low["uvs"], cage["uvs"])
        # Locking per-corner normals may allocate new normal IDs, but must not
        # change the ordered face/vertex normals handed to FBX.
        self.assertEqual(low["corner_normals"], cage["corner_normals"])
        self.assertEqual(low["materials"], cage["materials"])

    def test_cage_first_and_separate_exports_have_same_connectivity(self):
        together = self.capture([self.cage, self.lp])
        low = self.capture([self.lp])
        cage = self.capture([self.cage], triangulate_all=True)
        low_name, cage_name = self.lp.split("|")[-1], self.cage.split("|")[-1]
        self.assertEqual(low[low_name]["topology"], cage[cage_name]["topology"])
        self.assertEqual(together[cage_name], cage[cage_name])

    def test_renamed_lp_and_cage_without_low_marker_use_stable_link(self):
        self.lp = _long(cmds.rename(self.lp, "RenamedSource"))
        self.cage = _long(cmds.rename(self.cage, "UserSculptedShell"))
        self.originals = {n: _buffers(n) for n in (self.lp, self.cage)}
        self.original_dag = _dag_nodes()
        capture = self.capture([self.cage])
        self.assertEqual(self.originals[self.cage]["points"], capture["RenamedSource_cage"]["points"])
        self.assertEqual((3, 3), capture["RenamedSource_cage"]["topology"][1])

    def test_world_points_survive_different_scaled_rotated_parents(self):
        for node, translate, rotate, scale in (
                (self.lp, (4, 2, 1), (12, 31, 8), (1.5, 2, 0.8)),
                (self.cage, (-3, 6, 2), (17, -22, 15), (0.8, 1.2, 1.7))):
            cmds.xform(node, translation=translate, rotation=rotate, scale=scale)
        self.originals = {n: _buffers(n) for n in (self.lp, self.cage)}
        cage = self.capture([self.cage])[self.cage.split("|")[-1]]
        for actual, expected in zip(cage["points"], self.originals[self.cage]["points"]):
            for a, b in zip(actual, expected):
                self.assertAlmostEqual(a, b, places=6)
        self._assert_real_fbx_roundtrip()

    def test_invalid_pair_never_writes_fbx(self):
        cmds.polyTriangulate(self.cage, constructionHistory=False)
        self.originals[self.cage] = _buffers(self.cage)
        with mock.patch.object(Processor, "export_selected_fbx") as writer:
            self.assertFalse(self.export([self.lp, self.cage]))
            writer.assert_not_called()
        self.assertFalse(os.path.exists(self.path()))
        self.assert_originals_preserved()

    def test_missing_source_never_writes_fbx(self):
        cmds.delete(self.lp)
        self.originals.pop(self.lp)
        self.original_dag = _dag_nodes()
        with mock.patch.object(Processor, "export_selected_fbx") as writer:
            self.assertFalse(self.export([self.cage]))
            writer.assert_not_called()
        self.assert_originals_preserved()

    def test_triangulation_failure_cleans_all_copies_and_preserves_file(self):
        # A previously exported file must not be overwritten on preparation failure.
        with open(self.path(), "wb") as stream:
            stream.write(b"existing FBX sentinel")
        original_tri = cmds.polyTriangulate
        calls = []

        def fail_second(node, **kwargs):
            calls.append((node, kwargs))
            if len(calls) == 2:
                raise RuntimeError("injected triangulation failure")
            return original_tri(node, **kwargs)

        with mock.patch.object(cmds, "polyTriangulate", side_effect=fail_second):
            with mock.patch.object(Processor, "export_selected_fbx") as writer:
                self.assertFalse(self.export([self.lp, self.cage]))
                writer.assert_not_called()
        self.assertEqual(2, len(calls))
        self.assertEqual(calls[0][1], calls[1][1])
        with open(self.path(), "rb") as stream:
            self.assertEqual(b"existing FBX sentinel", stream.read())
        self.assert_originals_preserved()

    def test_silent_triangulation_noop_is_rejected(self):
        for nodes in ([self.lp], [self.cage]):
            with mock.patch.object(cmds, "polyTriangulate", return_value=[]):
                with mock.patch.object(Processor, "export_selected_fbx") as writer:
                    self.assertFalse(self.export(nodes))
                    writer.assert_not_called()
            self.assert_originals_preserved()

    def test_lp_triangulation_exception_never_writes_fbx(self):
        with mock.patch.object(cmds, "polyTriangulate", side_effect=RuntimeError("LP failure")):
            with mock.patch.object(Processor, "export_selected_fbx") as writer:
                self.assertFalse(self.export([self.lp]))
                writer.assert_not_called()
        self.assertFalse(os.path.exists(self.path()))
        self.assert_originals_preserved()

    def test_duplicate_parent_failure_never_leaves_sibling_copy(self):
        with mock.patch.object(cmds, "parent", side_effect=RuntimeError("Parent failure")):
            with mock.patch.object(Processor, "export_selected_fbx") as writer:
                self.assertFalse(self.export([self.cage]))
                writer.assert_not_called()
        self.assert_originals_preserved()

    def test_cage_marked_reusable_still_never_mutates_original(self):
        self.capture([self.cage], reusable_temp_nodes=[self.cage])

    def test_world_root_name_collision_keeps_cage_suffix(self):
        self.lp = _long(cmds.parent(self.lp, world=True, absolute=True)[0])
        self.cage = _long(cmds.parent(self.cage, world=True, absolute=True)[0])
        self.originals = {n: _buffers(n) for n in (self.lp, self.cage)}
        self.original_dag = _dag_nodes()
        capture = self.capture([self.lp, self.cage])
        cage_name = next(name for name in capture if name.endswith("_cage"))
        low_name = next(name for name in capture if name != cage_name)
        self.assertEqual(capture[low_name]["topology"], capture[cage_name]["topology"])
        self.assertEqual(self.originals[self.cage]["points"], capture[cage_name]["points"])
        self._assert_real_fbx_roundtrip()

    def test_hard_normal_uv_seams_and_multiple_uv_sets(self):
        cmds.delete(self.lp, self.cage)
        self.lp = cmds.polyCube(name="Seamed_low", constructionHistory=False)[0]
        cmds.polyUVSet(self.lp, copy=True, uvSet="map1", newUVSet="lightmap")
        self.cage = cmds.duplicate(self.lp, name="Seamed_low_cage")[0]
        self.lp = _long(cmds.parent(self.lp, "Original_LP")[0])
        self.cage = _long(cmds.parent(self.cage, "Original_Cages")[0])
        Cages.bind_source(self.lp, self.cage)
        mesh = _mesh(self.cage)
        points = mesh.getPoints()
        points[0].x -= 0.5
        points[0].y += 0.2
        mesh.setPoints(points)
        self.originals = {n: _buffers(n) for n in (self.lp, self.cage)}
        self.original_dag = _dag_nodes()
        capture = self.capture([self.lp, self.cage])
        low, cage = capture["Seamed_low"], capture["Seamed_low_cage"]
        self.assertEqual(low["topology"], cage["topology"])
        self.assertEqual(low["uvs"], cage["uvs"])
        self.assertEqual(low["corner_normals"], cage["corner_normals"])
        self.assertEqual(low["materials"], cage["materials"])
        self._assert_real_fbx_roundtrip()

    def test_flatten_failure_removes_already_detached_copy(self):
        original_parent = cmds.parent
        detached = []

        def fail_second_flatten(*args, **kwargs):
            if kwargs.get("world"):
                detached.append(args[0])
                if len(detached) == 2:
                    raise RuntimeError("injected flatten failure")
            return original_parent(*args, **kwargs)

        with mock.patch.object(cmds, "parent", side_effect=fail_second_flatten):
            with mock.patch.object(Processor, "export_selected_fbx") as writer:
                self.assertFalse(self.export([self.lp, self.cage]))
                writer.assert_not_called()
        self.assert_originals_preserved()

    def test_real_fbx_roundtrip_separate_lp_and_cage(self):
        self._assert_real_fbx_roundtrip()

    def _assert_real_fbx_roundtrip(self):
        low_path, cage_path = self.path("LP.fbx"), self.path("Cage.fbx")
        self.assertTrue(self.export([self.lp], low_path))
        self.assertTrue(self.export([self.cage], cage_path))
        self.assert_originals_preserved()
        cmds.file(new=True, force=True)
        for path, namespace in ((low_path, "low"), (cage_path, "cage")):
            self.assertGreater(os.path.getsize(path), 0)
            cmds.file(path, i=True, type="FBX", ignoreVersion=True,
                      mergeNamespacesOnClash=False, namespace=namespace, options="fbx")
        meshes = cmds.ls(type="mesh", long=True, noIntermediate=True) or []
        self.assertEqual(2, len(meshes))
        imported = [cmds.listRelatives(shape, parent=True, fullPath=True)[0] for shape in meshes]
        cage = next(node for node in imported if node.split("|")[-1].endswith("_cage"))
        low = next(node for node in imported if node != cage)
        self.assertTrue(cage.split("|")[-1].endswith("_cage"), cage)
        self.assertEqual(_signature(low), _signature(cage))
        self.assertTrue(all(c == 3 for c in _signature(cage)[1]))
        self.assertEqual(_buffers(low)["uvs"], _buffers(cage)["uvs"])
        self.assertEqual(_buffers(low)["corner_normals"], _buffers(cage)["corner_normals"])
        # FBX may reorder source vertices. The same imported LP index must still
        # identify the matching cage point, not just the same unordered positions.
        source_low = self.originals[self.lp]["points"]
        source_cage = self.originals[self.cage]["points"]
        for low_point, cage_point in zip(_points(low), _points(cage)):
            index = min(range(len(source_low)), key=lambda i:
                        sum((a - b) ** 2 for a, b in zip(low_point, source_low[i])))
            for actual, expected in zip(cage_point, source_cage[index]):
                self.assertAlmostEqual(actual, expected, places=5)
        self.assertFalse(any("BG_" in n for n in cmds.ls(type="transform") or []))


if __name__ == "__main__":
    try:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(CagePairExportTests)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    finally:
        maya.standalone.uninitialize()
    sys.exit(0 if result.wasSuccessful() else 1)
