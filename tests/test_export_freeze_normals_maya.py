"""Normal-preserving export regressions; disposable Maya standalone only."""
import contextlib
import math
import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6 import QtWidgets
except ImportError:
    from PySide2 import QtWidgets
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.mel as mel

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.environ.get("BAKE_GROUPS_TEST_SCRIPT_DIR") or os.path.join(ROOT, "Bake_Groups"))
import bg_final_export
import bg_marmoset_bridge
import bg_mixins

P = bg_final_export.FinalExportProcessor

def triangle_normals(node):
    mesh = P._export_mesh_fn(node)
    counts, ids = mesh.getTriangles()
    out = {}
    offset = 0
    points = mesh.getPoints(om.MSpace.kWorld)
    for face, count in enumerate(counts):
        for i in range(count):
            vertices = ids[offset:offset+3]
            keys = [tuple(round(v, 6) for v in (points[vertex].x, points[vertex].y, points[vertex].z))
                    for vertex in vertices]
            tri_key = tuple(sorted(keys))
            for vertex, key in zip(vertices, keys):
                normal = mesh.getFaceVertexNormal(face, vertex, om.MSpace.kWorld).normal()
                out[(tri_key, key)] = tuple(normal)
            offset += 3
    return out

def max_delta(a, b):
    if set(a) != set(b):
        raise AssertionError("Triangle/corner correspondence differs")
    return max(math.sqrt(sum((x-y)**2 for x, y in zip(a[k], b[k]))) for k in a)

def state(node):
    mesh = P._export_mesh_fn(node)
    normals = mesh.getNormals()
    counts, ids = mesh.getNormalIds()
    return {"points": tuple(tuple(p) for p in mesh.getPoints()),
            "faces": tuple(tuple(a) for a in mesh.getVertices()),
            "normals": tuple(tuple(n) for n in normals),
            "normal_ids": (tuple(counts), tuple(ids)),
            "locked": tuple(mesh.isNormalLocked(i) for i in range(mesh.numNormals)),
            "uv": tuple(tuple(a) for a in mesh.getUVs("map1")),
            "uv_ids": tuple(tuple(a) for a in mesh.getAssignedUVs("map1"))}

class Harness(QtWidgets.QWidget, bg_mixins.ExportMixin):
    pass

class FreezeNormalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cmds.loadPlugin("fbxmaya", quiet=True)

    def setUp(self):
        cmds.file(new=True, force=True)
        P._freeze_lp_normals = True
        self.output = tempfile.TemporaryDirectory(prefix="bg_freeze_normals_")
        self.addCleanup(self.output.cleanup)
        self.lp = cmds.polyPlane(name="Chapter_Part_low", sx=3, sy=3, constructionHistory=False)[0]
        cmds.move(0, 0.25, 0, self.lp + ".vtx[5]", relative=True)
        cmds.move(0.04, -0.17, 0, self.lp + ".vtx[10]", relative=True)
        self.before = state(self.lp)
        self.expected = triangle_normals(self.lp)

    def capture_export(self, freeze):
        captured = []
        def capture(_path):
            captured.append(triangle_normals(cmds.ls(selection=True, long=True, type="transform")[0]))
        with mock.patch.object(P, "export_selected_fbx", side_effect=capture):
            with P.export_session(freeze_lp_normals=freeze):
                self.assertTrue(P._export_with_lp_triangulation_rollback(
                    [self.lp], os.path.join(self.output.name, "part.fbx")))
        self.assertEqual(self.before, state(self.lp))
        self.assertFalse(cmds.ls("BG_Export_Tri_Temp*", type="transform"))
        return captured[0]

    def test_freeze_default_and_opt_out(self):
        self.assertTrue(P._freeze_lp_normals)
        self.assertLess(max_delta(self.expected, self.capture_export(True)), 1e-6)
        self.assertGreater(max_delta(self.expected, self.capture_export(False)), 0.01)

    def test_shared_normal_table_and_tangents(self):
        clone = cmds.duplicate(self.lp)[0]
        fn = P._export_mesh_fn(clone)
        normals = tuple(tuple(n) for n in fn.getNormals())
        ids = tuple(tuple(a) for a in fn.getNormalIds())
        tangents = tuple(tuple(t) for t in fn.getTangents(om.MSpace.kObject, "map1"))
        P._freeze_export_copy_normals(clone)
        fn = P._export_mesh_fn(clone)
        self.assertLess(max(math.sqrt(sum((x-y)**2 for x,y in zip(a,b)))
                            for a,b in zip(normals, fn.getNormals())), 1e-6)
        self.assertEqual(ids, tuple(tuple(a) for a in fn.getNormalIds()))
        self.assertEqual(tangents, tuple(tuple(t) for t in fn.getTangents(om.MSpace.kObject, "map1")))
        self.assertTrue(all(fn.isNormalLocked(i) for i in range(fn.numNormals)))
        self.assertEqual(self.before, state(self.lp))

    def test_hard_edges_preserved(self):
        cube = cmds.polyCube(name="Hard_low", constructionHistory=False)[0]
        before = triangle_normals(cube)
        original = state(cube)
        clone = cmds.duplicate(cube)[0]
        P._triangulate_export_copy(clone)
        self.assertLess(max_delta(before, triangle_normals(clone)), 1e-6)
        self.assertEqual(original, state(cube))

    def test_nested_session_and_exception_restore_flag(self):
        with P.export_session(freeze_lp_normals=False):
            self.assertFalse(P._freeze_lp_normals)
            with P.export_session():
                self.assertFalse(P._freeze_lp_normals)
            self.assertFalse(P._freeze_lp_normals)
        self.assertTrue(P._freeze_lp_normals)
        with self.assertRaises(ValueError):
            with P.export_session(freeze_lp_normals=False):
                raise ValueError("test")
        self.assertTrue(P._freeze_lp_normals)

    def test_error_preserves_original_and_cleans_temps(self):
        with mock.patch.object(P, "_freeze_export_copy_normals", side_effect=RuntimeError("freeze failed")):
            with P.export_session():
                self.assertFalse(P._export_with_lp_triangulation_rollback(
                    [self.lp], os.path.join(self.output.name, "failure.fbx")))
        self.assertEqual(self.before, state(self.lp))
        self.assertFalse(cmds.ls("BG_Export_Tri_Temp*", type="transform"))

    def test_real_fbx_roundtrip(self):
        path = os.path.join(self.output.name, "roundtrip.fbx")
        with P.export_session():
            self.assertTrue(P._export_with_lp_triangulation_rollback([self.lp], path))
        self.assertEqual(self.before, state(self.lp))
        cmds.file(new=True, force=True)
        cmds.file(path, i=True, type="FBX", ignoreVersion=True)
        nodes = cmds.ls(type="mesh", long=True, noIntermediate=True)
        imported = cmds.listRelatives(nodes[0], parent=True, fullPath=True)[0]
        self.assertLess(max_delta(self.expected, triangle_normals(imported)), 1e-6)

    def test_marmoset_role_name_without_low_suffix(self):
        snapshot = {"base": "Chapter", "hp_all": [], "lp_all": [self.lp],
                    "hp_by_prefix": {}, "lp_by_prefix": {"Part": [self.lp]}, "prefix_items": []}
        captured = []
        def capture(path):
            nodes = cmds.ls(selection=True, long=True, type="transform")
            captured.append(triangle_normals(nodes[0]))
            with open(path, "wb") as stream:
                stream.write(b"diagnostic placeholder")
        with mock.patch.object(P, "export_selected_fbx", side_effect=capture):
            with P.export_session():
                self.assertTrue(bg_marmoset_bridge.MarmosetPackageExporter.export_chapter(
                    snapshot, self.output.name, inc_hp=False, inc_lp=True))
        self.assertLess(max_delta(self.expected, captured[0]), 1e-6)
        self.assertEqual(self.before, state(self.lp))

    def test_ui_default_opt_out_and_rebuild(self):
        owner = Harness()
        panel = owner.build_export_panel({})
        self.assertTrue(owner.exp_freeze_lp_normals.isChecked())
        owner.exp_freeze_lp_normals.setChecked(False)
        self.assertFalse(owner._export_freeze_normals_enabled())
        owner.exp_target.setCurrentIndex(1)
        self.assertFalse(owner._export_freeze_normals_enabled())
        panel.close()
        panel = owner.build_export_panel({})
        self.assertFalse(owner.exp_freeze_lp_normals.isChecked())
        panel.close()
        owner.close()

    def test_marmoset_preview_and_global_triangulate_never_bake_hp(self):
        high = cmds.polyCube(name="Chapter_High", constructionHistory=False)[0]
        shape = cmds.listRelatives(high, shapes=True, fullPath=True)[0]
        cmds.setAttr(shape + ".smoothLevel", 2)
        cmds.setAttr(shape + ".displaySmoothMesh", 2)
        before = state(high)
        previous_tri = bool(mel.eval("FBXExportTriangulate -q;"))
        previous_smooth = bool(mel.eval("FBXExportSmoothMesh -q;"))
        self.addCleanup(lambda: mel.eval("FBXExportTriangulate -v {};".format(
            "true" if previous_tri else "false")))
        self.addCleanup(lambda: mel.eval("FBXExportSmoothMesh -v {};".format(
            "true" if previous_smooth else "false")))
        mel.eval("FBXExportTriangulate -v true;")
        mel.eval("FBXExportSmoothMesh -v true;")
        snapshot = {"base":"Preview", "hp_all":[high], "lp_all":[self.lp],
                    "hp_by_prefix":{"Part":[high]}, "lp_by_prefix":{"Part":[self.lp]},
                    "prefix_items":[{"prefix":"Part","smooth_level":2}]}
        with P.export_session():
            self.assertFalse(mel.eval("FBXExportTriangulate -q;"))
            chapter = bg_marmoset_bridge.MarmosetPackageExporter.export_chapter(
                snapshot, self.output.name)
        self.assertTrue(mel.eval("FBXExportTriangulate -q;"))
        self.assertTrue(mel.eval("FBXExportSmoothMesh -q;"))
        self.assertEqual(before, state(high))
        self.assertEqual(cmds.getAttr(shape + ".displaySmoothMesh"), 2)
        path = os.path.join(self.output.name, os.path.basename(chapter["model"]))
        cmds.file(new=True, force=True)
        cmds.file(path, i=True, type="FBX", ignoreVersion=True)
        shapes = cmds.ls(type="mesh", long=True, noIntermediate=True)
        high_shape = next(s for s in shapes if "_high_" in s)
        imported_high = cmds.listRelatives(high_shape, parent=True, fullPath=True)[0]
        self.assertEqual(P._export_mesh_fn(imported_high).numPolygons, 6,
                         "HP preview was baked or HP quads were triangulated")
        self.assertTrue(all(n==4 for n in P._export_mesh_fn(imported_high).getVertices()[0]))
        imported_low = cmds.listRelatives(next(s for s in shapes if "_low_" in s), parent=True, fullPath=True)[0]
        self.assertLess(max_delta(self.expected, triangle_normals(imported_low)), 1e-6)

if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        maya.standalone.uninitialize()
