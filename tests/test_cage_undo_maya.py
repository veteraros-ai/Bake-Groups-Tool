# -*- coding: utf-8 -*-
"""Run with Maya 2027 mayapy; touches only fresh disposable standalone scenes.

    mayapy.exe -B tests/test_cage_undo_maya.py
"""
import importlib
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.dont_write_bytecode = True
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds
import maya.mel as mel
import maya.api.OpenMaya as om

SOURCE = Path(os.environ.get("BAKE_GROUPS_TEST_SCRIPT_DIR") or
              str(Path(__file__).resolve().parents[1] / "Bake_Groups"))
sys.path.insert(0, str(SOURCE))
import bg_cage_undo as cage_undo


def path(node):
    selection = om.MSelectionList()
    selection.add(node)
    result = selection.getDagPath(0)
    if result.hasFn(om.MFn.kTransform):
        result.extendToShape()
    return result


def points(node, space=om.MSpace.kObject):
    return tuple((p.x, p.y, p.z) for p in om.MFnMesh(path(node)).getPoints(space))


def moved(values, delta):
    return tuple(tuple(a + b for a, b in zip(p, delta)) for p in values)


def transform_fn(node):
    selection = om.MSelectionList()
    selection.add(node)
    return om.MFnTransform(selection.getDagPath(0))


class CageUndoMayaTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        cmds.undoInfo(state=True, infinity=True)
        self.a = cmds.polyCube(name="cage_A", constructionHistory=False)[0]
        self.b = cmds.polyCube(name="cage_B", constructionHistory=False)[0]
        self.sentinel = cmds.createNode("transform", name="userUndoSentinel")
        cmds.setAttr(self.sentinel + ".translateX", 9.0)
        self.undo_name = cmds.undoInfo(query=True, undoName=True)

    def assertPoints(self, actual, expected):
        self.assertEqual(len(actual), len(expected))
        for a, b in zip(actual, expected):
            for x, y in zip(a, b):
                # Maya stores mesh coordinates as floats, even via MPoint.
                self.assertAlmostEqual(x, y, delta=1e-6)

    def assertQueueUnchanged(self):
        self.assertEqual(cmds.undoInfo(query=True, undoName=True), self.undo_name)

    def test_00_lazy_load_pending_payload_survives_reload(self):
        self.assertFalse(mel.eval('exists "bgCageMeshEditV1"'))
        before = points(self.a)
        with mock.patch.object(cmds, "loadPlugin", side_effect=AssertionError("no-op loaded plugin")):
            self.assertFalse(cage_undo.set_points_undoable(self.a, before))
        # A failed first load must restore the preview and release its token.
        with mock.patch.object(cmds, "loadPlugin", side_effect=RuntimeError("load failed")):
            with self.assertRaises(RuntimeError):
                cage_undo.set_points_undoable(self.a, moved(before, (1, 0, 0)))
        self.assertFalse(cage_undo._REGISTRY["pending"])
        self.assertPoints(points(self.a), before)
        after = moved(before, (0.1, 0.2, 0.3))
        real_load = cmds.loadPlugin
        observed = []

        def reload_then_load(*args, **kwargs):
            pending = cage_undo._REGISTRY["pending"]
            self.assertEqual(len(pending), 1)
            observed.append(next(iter(pending)))
            importlib.reload(cage_undo)
            self.assertIs(cage_undo._REGISTRY["pending"], pending)
            return real_load(*args, **kwargs)

        with mock.patch.object(cmds, "loadPlugin", side_effect=reload_then_load):
            self.assertTrue(cage_undo.set_points_undoable(
                self.a, after, space=om.MSpace.kObject))
        self.assertEqual(len(observed), 1)
        self.assertFalse(cage_undo._REGISTRY["pending"])
        cmds.undo()
        self.assertPoints(points(self.a), before)
        cmds.redo()
        self.assertPoints(points(self.a), after)

    def test_capture_direct_api_and_many_live_ticks_one_multi_mesh_undo(self):
        before_a, before_b = points(self.a), points(self.b)
        tx = cage_undo.MeshEditTransaction(label="CageGesture")
        handle = tx.capture(self.a)
        self.assertIsInstance(handle, om.MObjectHandle)
        self.assertEqual(tx.capture(path(self.a)).object(), handle.object())
        # capture() must also support callers doing their own API previews.
        om.MFnMesh(path(self.a)).setPoints(
            om.MPointArray(moved(before_a, (0, 0.15, 0))), om.MSpace.kObject)
        for tick in range(1, 21):
            after_a = moved(before_a, (tick * 0.1, 0.25, 0))
            after_b = moved(before_b, (0, tick * 0.2, -0.25))
            tx.set_points(self.a, after_a, space=om.MSpace.kObject)
            tx.set_points(self.b, after_b, space=om.MSpace.kObject)
            self.assertQueueUnchanged()
        self.assertTrue(tx.commit())
        # Standalone may return an empty undoName, including for built-in cmds.
        # The actual two consecutive undo operations below prove the boundary.
        cmds.undo()
        self.assertPoints(points(self.a), before_a)
        self.assertPoints(points(self.b), before_b)
        self.assertQueueUnchanged()
        self.assertEqual(cmds.getAttr(self.sentinel + ".translateX"), 9)
        cmds.redo()
        self.assertPoints(points(self.a), after_a)
        self.assertPoints(points(self.b), after_b)
        cmds.undo()
        cmds.undo()
        self.assertEqual(cmds.getAttr(self.sentinel + ".translateX"), 0)

    def test_cancel_restores_all_and_keeps_user_undo_and_redo(self):
        cmds.undo()  # Keep an existing user redo item through the entire preview.
        undo_name = cmds.undoInfo(query=True, undoName=True)
        redo_name = cmds.undoInfo(query=True, redoName=True)
        before_a, before_b = points(self.a), points(self.b)
        tx = cage_undo.MeshEditTransaction()
        tx.set_points(self.a, moved(before_a, (1, 0, 0)))
        tx.set_points(self.b, moved(before_b, (0, 2, 0)))
        self.assertTrue(tx.cancel())
        self.assertFalse(tx.cancel())
        self.assertFalse(tx.commit())
        self.assertPoints(points(self.a), before_a)
        self.assertPoints(points(self.b), before_b)
        self.assertEqual(cmds.undoInfo(query=True, undoName=True), undo_name)
        self.assertEqual(cmds.undoInfo(query=True, redoName=True), redo_name)
        cmds.redo()
        self.assertEqual(cmds.getAttr(self.sentinel + ".translateX"), 9)

    def test_noops_empty_capture_same_and_return_to_start(self):
        initial = points(self.a)
        self.assertFalse(cage_undo.MeshEditTransaction().commit())
        tx = cage_undo.MeshEditTransaction()
        tx.capture(self.a)
        self.assertFalse(tx.commit())
        self.assertFalse(cage_undo.set_points_undoable(self.a, initial))
        tx = cage_undo.MeshEditTransaction()
        tx.set_points(self.a, moved(initial, (1, 0, 0)))
        tx.set_points(self.a, initial)
        with mock.patch.object(cage_undo, "_submit", side_effect=AssertionError("no-op submitted")):
            self.assertFalse(tx.commit())
        self.assertQueueUnchanged()
        cmds.undo()
        self.assertEqual(cmds.getAttr(self.sentinel + ".translateX"), 0)
        self.assertPoints(points(self.a), initial)

    def test_world_points_preserve_sculpt_and_parent_transforms(self):
        group = cmds.group(empty=True, name="cageParent")
        self.a = cmds.parent(self.a, group)[0]
        cmds.setAttr(group + ".translate", 7, -2, 4, type="double3")
        cmds.setAttr(group + ".rotate", 23, -37, 11, type="double3")
        cmds.setAttr(group + ".scale", 1.5, 0.75, -2, type="double3")
        cmds.setAttr(self.a + ".translate", 0.5, 0.25, -0.75, type="double3")
        sculpt = list(points(self.a))
        sculpt[3] = (1.3, -0.75, 0.89)
        om.MFnMesh(path(self.a)).setPoints(om.MPointArray(sculpt), om.MSpace.kObject)
        before_local, before_world = points(self.a), points(self.a, om.MSpace.kWorld)
        matrix = tuple(path(self.a).inclusiveMatrix())
        world_after = moved(before_world, (0.4, -0.1, 0.7))
        self.assertTrue(cage_undo.set_points_undoable(self.a, world_after))
        self.assertPoints(points(self.a, om.MSpace.kWorld), world_after)
        local_after = points(self.a)
        cmds.undo()
        self.assertPoints(points(self.a), before_local)
        self.assertPoints(points(self.a, om.MSpace.kWorld), before_world)
        cmds.redo()
        self.assertPoints(points(self.a), local_after)
        self.assertEqual(tuple(path(self.a).inclusiveMatrix()), matrix)

    def test_rename_reparent_and_transform_changes_use_stable_shape(self):
        before = points(self.a)
        after = moved(before, (1, 2, 3))
        tx = cage_undo.MeshEditTransaction()
        handle = tx.capture(self.a)
        tx.set_points(self.a, after, space=om.MSpace.kObject)
        self.assertTrue(tx.commit())
        # Direct API edits leave the point command at the top of the undo stack.
        fn = transform_fn(self.a)
        fn.setName("renamedCage")
        om.MFnDependencyNode(handle.object()).setName("renamedCageShape")
        group = om.MFnTransform()
        parent_obj = group.create()
        group.setName("apiParent")
        modifier = om.MDagModifier()
        modifier.reparentNode(fn.object(), parent_obj)
        modifier.doIt()
        group.setTranslation(om.MVector(12, -4, 3), om.MSpace.kTransform)
        group.setScale((2, 0.5, 3))
        fn.setTranslation(om.MVector(-1, 3, 4), om.MSpace.kTransform)
        current_path = om.MDagPath.getAPathTo(handle.object())
        expected_matrix = tuple(current_path.inclusiveMatrix())
        cmds.undo()
        self.assertPoints(points(current_path.fullPathName()), before)
        cmds.redo()
        self.assertPoints(points(current_path.fullPathName()), after)
        self.assertEqual(tuple(current_path.inclusiveMatrix()), expected_matrix)

    def test_live_rename_and_transform_then_cancel(self):
        before = points(self.a)
        tx = cage_undo.MeshEditTransaction()
        handle = tx.capture(self.a)
        tx.set_points(self.a, moved(before, (1, 2, 3)))
        fn = transform_fn(self.a)
        fn.setName("renamedDuringPreview")
        fn.setTranslation(om.MVector(20, 0, 0), om.MSpace.kTransform)
        tx.cancel()
        current = om.MDagPath.getAPathTo(handle.object()).fullPathName()
        self.assertPoints(points(current), before)
        self.assertPoints(points(current, om.MSpace.kWorld), moved(before, (20, 0, 0)))
        self.assertQueueUnchanged()

    def test_reload_existing_undo_and_new_commands_are_independent(self):
        before = points(self.a)
        first = moved(before, (1, 0, 0))
        second = moved(before, (0, 2, 0))
        cage_undo.set_points_undoable(self.a, first)
        importlib.reload(cage_undo)
        # A retained command must not use newly defined implementation helpers.
        with mock.patch.object(cage_undo._EditPayload, "apply", side_effect=AssertionError("reload lookup")), \
                mock.patch.object(cage_undo._MeshSnapshot, "write", side_effect=AssertionError("reload lookup")):
            cmds.undo()
            self.assertPoints(points(self.a), before)
            cmds.redo()
            self.assertPoints(points(self.a), first)
        cage_undo.set_points_undoable(self.a, second)
        importlib.reload(cage_undo)
        cmds.undo()
        self.assertPoints(points(self.a), first)
        cmds.undo()
        self.assertPoints(points(self.a), before)
        cmds.redo()
        self.assertPoints(points(self.a), first)
        cmds.redo()
        self.assertPoints(points(self.a), second)
        self.assertFalse(cage_undo._REGISTRY["pending"])

    def test_reload_during_live_transaction(self):
        before = points(self.a)
        tx = cage_undo.MeshEditTransaction()
        tx.set_points(self.a, moved(before, (1, 0, 0)))
        importlib.reload(cage_undo)
        after = moved(before, (2, 0, 0))
        tx.set_points(self.a, after)
        self.assertTrue(tx.commit())
        cmds.undo()
        self.assertPoints(points(self.a), before)
        cmds.redo()
        self.assertPoints(points(self.a), after)

    def test_invalid_preview_rolls_back_all_meshes(self):
        before_a, before_b = points(self.a), points(self.b)
        tx = cage_undo.MeshEditTransaction()
        tx.set_points(self.a, moved(before_a, (1, 0, 0)))
        tx.set_points(self.b, moved(before_b, (0, 1, 0)))
        with self.assertRaises(RuntimeError):
            tx.set_points(self.b, [(0, 0, 0)])
        self.assertPoints(points(self.a), before_a)
        self.assertPoints(points(self.b), before_b)
        self.assertQueueUnchanged()
        self.assertFalse(tx.commit())

    def test_failed_plugin_submission_cancels_and_cleans_pending(self):
        before = points(self.a)
        tx = cage_undo.MeshEditTransaction()
        tx.set_points(self.a, moved(before, (1, 0, 0)))
        with mock.patch.object(cmds, "bgCageMeshEditV1", side_effect=RuntimeError("submission failed")):
            with self.assertRaises(RuntimeError):
                tx.commit()
        self.assertPoints(points(self.a), before)
        self.assertQueueUnchanged()
        self.assertFalse(cage_undo._REGISTRY["pending"])

    def test_cancel_deleted_mesh_restores_survivors(self):
        before_a, before_b = points(self.a), points(self.b)
        tx = cage_undo.MeshEditTransaction()
        tx.set_points(self.a, moved(before_a, (1, 0, 0)))
        tx.set_points(self.b, moved(before_b, (0, 1, 0)))
        modifier = om.MDagModifier()
        modifier.deleteNode(transform_fn(self.b).object())
        modifier.doIt()
        self.assertTrue(tx.cancel())
        self.assertPoints(points(self.a), before_a)
        self.assertQueueUnchanged()

    def test_commit_deleted_mesh_is_atomic_and_cancels_survivors(self):
        before_a, before_b = points(self.a), points(self.b)
        tx = cage_undo.MeshEditTransaction()
        tx.set_points(self.a, moved(before_a, (1, 0, 0)))
        tx.set_points(self.b, moved(before_b, (0, 1, 0)))
        modifier = om.MDagModifier()
        modifier.deleteNode(transform_fn(self.b).object())
        modifier.doIt()
        with self.assertRaises(RuntimeError):
            tx.commit()
        self.assertPoints(points(self.a), before_a)
        self.assertQueueUnchanged()
        self.assertFalse(cage_undo._REGISTRY["pending"])

    def test_command_validates_every_mesh_before_any_apply(self):
        before_a, before_b = points(self.a), points(self.b)
        tx = cage_undo.MeshEditTransaction()
        tx.set_points(self.a, moved(before_a, (1, 0, 0)))
        tx.set_points(self.b, moved(before_b, (0, 1, 0)))
        # Exercise the very same payload method used by MPxCommand undo/redo,
        # without corrupting Maya's queue by intentionally failing cmds.undo.
        records = list(tx._records)
        payload = cage_undo._EditPayload("AtomicProbe", [
            (record, record.before, record.read()) for record in records])
        after_a = points(self.a)
        modifier = om.MDagModifier()
        modifier.deleteNode(transform_fn(self.b).object())
        modifier.doIt()
        with self.assertRaises(RuntimeError):
            payload.apply(False)
        self.assertPoints(points(self.a), after_a)
        tx.cancel()
        self.assertPoints(points(self.a), before_a)

    def test_command_apply_failure_rolls_back_earlier_mesh(self):
        before_a, before_b = points(self.a), points(self.b)
        tx = cage_undo.MeshEditTransaction()
        tx.capture(self.a)
        tx.capture(self.b)
        records = list(tx._records)
        payload = cage_undo._EditPayload("AtomicFailure", [
            (record, record.before, moved(record.before, (1, 2, 3)))
            for record in records])
        real_write = records[1].write
        calls = []

        def fail_after_write(values):
            real_write(values)
            calls.append(True)
            if len(calls) == 1:
                raise RuntimeError("partial write")

        with mock.patch.object(records[1], "write", side_effect=fail_after_write):
            with self.assertRaises(RuntimeError):
                payload.apply(True)
        self.assertPoints(points(self.a), before_a)
        self.assertPoints(points(self.b), before_b)
        tx.cancel()
        self.assertQueueUnchanged()

    def test_atomic_topology_validation_even_when_vertex_count_matches(self):
        tx = cage_undo.MeshEditTransaction()
        tx.capture(self.a)
        tx.capture(self.b)
        records = list(tx._records)
        before_a = points(self.a)
        payload = cage_undo._EditPayload("TopologyProbe", [
            (record, record.before, moved(record.before, (1, 2, 3)))
            for record in records])
        fn = om.MFnMesh(path(self.b))
        vertex_count = fn.numVertices
        fn.deleteFace(0)
        self.assertEqual(om.MFnMesh(path(self.b)).numVertices, vertex_count)
        with self.assertRaisesRegex(RuntimeError, "topology"):
            payload.apply(True)
        self.assertPoints(points(self.a), before_a)
        # Avoid a cancellation warning for the intentionally changed topology.
        tx._records = [records[0]]
        tx.cancel()
        self.assertQueueUnchanged()

    def test_native_command_rejects_invalid_payload_before_any_write(self):
        tx = cage_undo.MeshEditTransaction()
        tx.capture(self.a)
        tx.capture(self.b)
        records = list(tx._records)
        before_a = points(self.a)
        payload = cage_undo._EditPayload("InvalidNativePayload", [
            (record, record.before, moved(record.before, (1, 2, 3)))
            for record in records])
        modifier = om.MDagModifier()
        modifier.deleteNode(transform_fn(self.b).object())
        modifier.doIt()
        with self.assertRaises(RuntimeError):
            cage_undo._submit(payload)
        self.assertPoints(points(self.a), before_a)
        self.assertFalse(cage_undo._REGISTRY["pending"])
        tx.cancel()
        cmds.undo()
        self.assertEqual(cmds.getAttr(self.sentinel + ".translateX"), 0)

    def test_world_noop_under_nonuniform_parent_transform(self):
        group = cmds.group(empty=True, name="noOpParent")
        self.a = cmds.parent(self.a, group)[0]
        cmds.setAttr(group + ".translate", 3, -7, 9, type="double3")
        cmds.setAttr(group + ".rotate", 17, 36, -11, type="double3")
        cmds.setAttr(group + ".scale", 2, 0.3, -1.7, type="double3")
        cmds.setAttr(self.sentinel + ".translateX", 12)
        before = points(self.a)
        self.assertFalse(cage_undo.set_points_undoable(
            self.a, points(self.a, om.MSpace.kWorld)))
        cmds.undo()
        self.assertEqual(cmds.getAttr(self.sentinel + ".translateX"), 9)
        self.assertPoints(points(self.a), before)

    def test_context_manager_exception_cancels(self):
        before = points(self.a)
        with self.assertRaisesRegex(ValueError, "gesture failed"):
            with cage_undo.MeshEditTransaction() as tx:
                tx.set_points(self.a, moved(before, (1, 0, 0)))
                raise ValueError("gesture failed")
        self.assertPoints(points(self.a), before)
        self.assertQueueUnchanged()

    def test_history_mesh_immediate_edit(self):
        mesh = cmds.polyCube(name="historyCage", constructionHistory=True)[0]
        before = points(mesh)
        after = moved(before, (0.2, -0.4, 0.7))
        self.assertTrue(cage_undo.set_points_undoable(mesh, after))
        cmds.undo()
        self.assertPoints(points(mesh), before)
        cmds.redo()
        self.assertPoints(points(mesh), after)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CageUndoMayaTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    maya.standalone.uninitialize()
    sys.exit(0 if result.wasSuccessful() else 1)
