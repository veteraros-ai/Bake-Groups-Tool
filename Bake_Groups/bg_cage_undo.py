# -*- coding: utf-8 -*-
"""Direct-API cage previews with one native Maya undo item per gesture.

Import normally; the first non-empty commit loads this same file as an API 2.0
Python plug-in. No undo chunks, global undo calls, or undo-state changes are
used. Call from Maya's main thread. A transaction owns point edits only: avoid
topology edits or unrelated undoable commands during its live gesture.

Snapshots are object-space, so existing sculpting and DAG transforms survive.
Pending payloads live in builtins because Maya loads Python plug-ins under a
different module name. Retained commands own their payload and implementation;
neither undo nor redo looks up a subsequently reloaded module's helpers.
"""
from __future__ import absolute_import

import builtins
import math
import os
import uuid

import maya.cmds as cmds
import maya.mel as mel
import maya.api.OpenMaya as om

__all__ = ["MeshEditTransaction", "set_points_undoable"]

_COMMAND_NAME = "bgCageMeshEditV1"
_REGISTRY_KEY = "_bake_groups_cage_undo_v1"
if not hasattr(builtins, _REGISTRY_KEY):
    setattr(builtins, _REGISTRY_KEY, {"pending": {}})
_REGISTRY = getattr(builtins, _REGISTRY_KEY)


def maya_useNewAPI():
    """Tell Maya's plug-in loader to supply API 2.0 objects."""


def _mesh_path(node):
    if isinstance(node, om.MFnMesh):
        path = node.dagPath()
    elif isinstance(node, om.MDagPath):
        path = om.MDagPath(node)
    elif isinstance(node, om.MObjectHandle):
        if not node.isValid() or not node.isAlive():
            raise RuntimeError("Cage mesh was deleted")
        path = om.MDagPath.getAPathTo(node.object())
    elif isinstance(node, om.MObject):
        path = om.MDagPath.getAPathTo(node)
    else:
        selection = om.MSelectionList()
        selection.add(node)
        path = selection.getDagPath(0)
    if path.hasFn(om.MFn.kTransform):
        shapes = []
        for index in range(path.numberOfShapesDirectlyBelow()):
            shape = om.MDagPath(path)
            shape.extendToShape(index)
            if (shape.hasFn(om.MFn.kMesh)
                    and not om.MFnDagNode(shape).isIntermediateObject):
                shapes.append(shape)
        if len(shapes) != 1:
            raise ValueError("Expected exactly one non-intermediate mesh shape")
        path = shapes[0]
    if not path.hasFn(om.MFn.kMesh):
        raise ValueError("Expected a polygon mesh")
    return path


def _same_points(left, right):
    # Ignore round-trip noise when world-space input is converted back to local.
    return len(left) == len(right) and all(
        abs(a - b) <= 1e-10 for p, q in zip(left, right) for a, b in zip(p, q)
    )


class _MeshSnapshot(object):
    # Methods on instances retained by Maya must never depend on reloadable
    # module globals. Capture the API module in the class itself.
    _api = om

    def __init__(self, path):
        self.handle = self._api.MObjectHandle(path.node())
        self.topology = self._topology(self.function())
        self.before = self.read()

    def function(self):
        if not self.handle.isAlive() or not self.handle.isValid():
            raise RuntimeError("Cage mesh was deleted")
        obj = self.handle.object()
        if not obj.hasFn(self._api.MFn.kMesh):
            raise RuntimeError("Cage target is no longer a mesh")
        return self._api.MFnMesh(self._api.MDagPath.getAPathTo(obj))

    @staticmethod
    def _topology(fn):
        counts, vertices = fn.getVertices()
        return fn.numVertices, tuple(counts), tuple(vertices)

    def validate(self, points):
        fn = self.function()
        if self._topology(fn) != self.topology or len(points) != fn.numVertices:
            raise RuntimeError("Cage mesh topology changed during point edit")
        if self._api.MFnDependencyNode(self.handle.object()).isLocked:
            raise RuntimeError("Cage mesh is locked")
        return fn

    def read(self):
        return tuple((p.x, p.y, p.z, p.w)
                     for p in self.function().getPoints(self._api.MSpace.kObject))

    def write(self, points):
        # Rebuild the function set: setPoints may invalidate a cached fn when
        # Maya creates tweaks on a mesh with construction history.
        self.function().setPoints(self._api.MPointArray(points),
                                  self._api.MSpace.kObject)


class _EditPayload(object):
    """Immutable buffers plus a reload-independent atomic apply operation."""

    def __init__(self, label, entries):
        self.label = label
        self.entries = tuple(entries)  # (snapshot, before tuples, after tuples)

    def apply(self, after):
        targets = [(record, end if after else start)
                   for record, start, end in self.entries]
        # Validate EVERY target before touching ANY target, including topology.
        for record, points in targets:
            record.validate(points)
        rollback = [(record, record.read()) for record, _ in targets]
        attempted = 0
        try:
            for record, points in targets:
                attempted += 1
                record.write(points)
        except Exception:
            # Also roll back the failing write in case Maya partially applied it.
            for record, points in reversed(rollback[:attempted]):
                try:
                    record.write(points)
                except Exception:
                    pass
            raise


class _CageMeshEditCommand(om.MPxCommand):
    _pending = _REGISTRY["pending"]

    def __init__(self):
        super().__init__()
        self._payload = None
        self._complete = False

    @classmethod
    def creator(cls):
        return cls()

    def doIt(self, args):
        if len(args) != 1:
            raise RuntimeError("Cage edit requires one pending payload token")
        token = args.asString(0)
        self._payload = self._pending.pop(token, None)
        if self._payload is None:
            raise RuntimeError("Cage edit payload expired or was already consumed")
        self.redoIt()
        self._complete = True
        self.commandString = self._payload.label
        self.setResult(self._payload.label)

    def isUndoable(self):
        return self._complete

    def undoIt(self):
        self._payload.apply(False)

    def redoIt(self):
        self._payload.apply(True)


def initializePlugin(plugin_object):
    om.MFnPlugin(plugin_object, "Bake Groups", "1.0", "Any").registerCommand(
        _COMMAND_NAME, _CageMeshEditCommand.creator
    )


def uninitializePlugin(plugin_object):
    om.MFnPlugin(plugin_object).deregisterCommand(_COMMAND_NAME)


def _submit(payload):
    if not cmds.undoInfo(query=True, state=True):
        raise RuntimeError("Maya undo must be enabled to commit a Cage edit")
    token = uuid.uuid4().hex
    pending = _REGISTRY["pending"]
    pending[token] = payload
    try:
        # Source and deployed copies share the command and pending registry.
        # Never unload/reload a plug-in that owns commands still on the stack.
        if not mel.eval('exists "{}"'.format(_COMMAND_NAME)):
            cmds.loadPlugin(os.path.abspath(__file__), quiet=True)
        getattr(cmds, _COMMAND_NAME)(token)
    finally:
        pending.pop(token, None)


class MeshEditTransaction(object):
    """Own a live multi-mesh gesture; commit() returns True only for an edit.

    capture(node) is idempotent and returns the shape's stable MObjectHandle.
    set_points() captures first and previews directly, returning whether points
    changed. commit()/cancel() close the transaction and release its buffers;
    repeated finalization returns False. A failed preview or commit cancels all
    surviving unchanged-topology meshes before re-raising the original error.
    A context manager commits on success and cancels on an exception.
    """

    def __init__(self, label="CageEdit"):
        self.label = str(label)
        self._records = []
        self._closed = False

    def _capture_path(self, path):
        if self._closed:
            raise RuntimeError("Cage edit transaction is closed")
        for record in self._records:
            if record.handle.isValid() and record.handle.object() == path.node():
                return record
        record = _MeshSnapshot(path)
        self._records.append(record)
        return record

    def capture(self, node):
        try:
            return self._capture_path(_mesh_path(node)).handle
        except Exception:
            self.cancel()
            raise

    def set_points(self, node, points, space=om.MSpace.kWorld):
        try:
            path = _mesh_path(node)
            record = self._capture_path(path)
            if space not in (om.MSpace.kWorld, om.MSpace.kObject):
                raise ValueError("Cage points must use object or world space")
            values = om.MPointArray(points)
            if space == om.MSpace.kWorld:
                inverse = path.inclusiveMatrixInverse()
                values = om.MPointArray([p * inverse for p in values])
            local = tuple((p.x, p.y, p.z, p.w) for p in values)
            if not all(math.isfinite(v) for p in local for v in p):
                raise ValueError("Cage points must be finite")
            record.validate(local)
            if _same_points(record.read(), local):
                return False
            record.write(local)
            return True
        except Exception:
            self.cancel()
            raise

    def commit(self):
        if self._closed:
            return False
        try:
            entries = []
            for record in self._records:
                record.validate(record.before)
                after = record.read()
                if not _same_points(record.before, after):
                    entries.append((record, record.before, after))
            if entries:
                _submit(_EditPayload(self.label, entries))
            self._closed = True
            self._records = []
            return bool(entries)
        except Exception:
            self.cancel()
            raise

    def cancel(self):
        if self._closed:
            return False
        records, self._records = self._records, []
        self._closed = True
        restored = False
        for record in records:
            # Deletion is normal when a preview is interrupted. Still restore
            # the other meshes; never resurrect nodes or touch the undo queue.
            if not record.handle.isAlive() or not record.handle.isValid():
                continue
            try:
                record.validate(record.before)
                if not _same_points(record.read(), record.before):
                    record.write(record.before)
                    restored = True
            except Exception as error:
                om.MGlobal.displayWarning("Could not cancel Cage mesh edit: {}".format(error))
        return restored

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            self.commit()
        else:
            self.cancel()
        return False


def set_points_undoable(node, points, space=om.MSpace.kWorld):
    """Apply one immediate undoable point edit; return False for a no-op."""
    transaction = MeshEditTransaction()
    try:
        transaction.set_points(node, points, space=space)
        return transaction.commit()
    except Exception:
        transaction.cancel()
        raise
