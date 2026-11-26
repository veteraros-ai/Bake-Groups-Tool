# -*- coding: utf-8 -*-
"""Scene-revision cache for HP/LP preflight results.

The cache is deliberately conservative: without Maya callbacks it never
returns a hit. Callbacks cover edits that preserve counts and bounds, while a
small root identity/settings token prevents reuse across changed contexts.
"""
from __future__ import print_function

import maya.api.OpenMaya as om
import maya.cmds as cmds


class PreflightCache(object):
    def __init__(self):
        self._callbacks = []
        self._node_callbacks = []
        self._generation = 0
        self._revision = 0
        self._entries = {}
        self._watch_tokens = set()
        self._watched_roots = set()
        self._enabled = True
        try:
            for event in ("SceneOpened", "NewSceneOpened"):
                self._callbacks.append(om.MEventMessage.addEventCallback(event, self._scene_changed_cb))
            for event in ("Undo", "Redo"):
                self._callbacks.append(om.MEventMessage.addEventCallback(event, self._invalidate_cb))
            self._callbacks.append(om.MDGMessage.addNodeAddedCallback(self._invalidate_cb, "dependNode"))
            self._callbacks.append(om.MDGMessage.addNodeRemovedCallback(self._invalidate_cb, "dependNode"))
            self._callbacks.append(om.MDagMessage.addParentAddedCallback(self._invalidate_dag_cb))
            self._callbacks.append(om.MDagMessage.addParentRemovedCallback(self._invalidate_dag_cb))
        except Exception:
            self._enabled = False
            self.close()

    @property
    def enabled(self):
        return self._enabled

    @property
    def generation(self):
        return self._generation

    @property
    def revision(self):
        return self._revision

    def _invalidate_cb(self, *unused):
        self.invalidate()
        self._watched_roots.clear()

    def _invalidate_dag_cb(self, *unused):
        self.invalidate()
        self._watched_roots.clear()

    def _attribute_changed_cb(self, message, _plug, _other_plug, _client_data=None):
        # Chapter navigation toggles HP/LP visibility without changing anything
        # measured by preflight. Display-layer edits remain watched for ZBrush.
        if _plug.partialName(useLongNames=True) == 'visibility':
            return
        # AttributeEval is a read/evaluation notification, not a scene edit.
        # Treat only value, connection, lock, or schema changes as mutations.
        mutation_flags = (
            om.MNodeMessage.kConnectionMade |
            om.MNodeMessage.kConnectionBroken |
            om.MNodeMessage.kAttributeSet |
            om.MNodeMessage.kAttributeLocked |
            om.MNodeMessage.kAttributeUnlocked |
            om.MNodeMessage.kAttributeAdded |
            om.MNodeMessage.kAttributeRemoved |
            om.MNodeMessage.kAttributeArrayAdded |
            om.MNodeMessage.kAttributeArrayRemoved |
            om.MNodeMessage.kOtherPlugSet
        )
        message = int(message)
        if message & int(mutation_flags):
            self.invalidate()
            connection_flags = (om.MNodeMessage.kConnectionMade |
                                om.MNodeMessage.kConnectionBroken)
            if message & int(connection_flags):
                # A shading assignment or history rewire can introduce new
                # upstream nodes without changing the watched DAG paths.
                self._watched_roots.clear()

    def _scene_changed_cb(self, *unused):
        self.invalidate()
        for callback in self._node_callbacks:
            try:
                om.MMessage.removeCallback(callback)
            except Exception:
                pass
        self._node_callbacks = []
        self._watch_tokens.clear()
        self._watched_roots.clear()

    def invalidate(self):
        self._generation += 1
        self._entries.clear()

    @staticmethod
    def _long_name(node):
        if not node or not cmds.objExists(node):
            return None
        paths = cmds.ls(node, long=True) or []
        return paths[0] if paths else node

    def _watch_node(self, path):
        if not self._enabled:
            return
        try:
            selection = om.MSelectionList()
            selection.add(path)
            obj = selection.getDependNode(0)
            token = om.MObjectHandle(obj).hashCode()
            if token in self._watch_tokens:
                return
            self._node_callbacks.append(
                om.MNodeMessage.addAttributeChangedCallback(obj, self._attribute_changed_cb))
            self._node_callbacks.append(
                om.MNodeMessage.addNameChangedCallback(obj, self._invalidate_cb))
            self._watch_tokens.add(token)
        except Exception:
            self.close()

    def _root_paths(self, roots):
        normalized = []
        for root in roots:
            path = self._long_name(root)
            if not path:
                return None
            uuids = cmds.ls(path, uuid=True) or []
            normalized.append((path, uuids[0] if uuids else None))
        return tuple(sorted(set(normalized)))

    def watch(self, roots):
        """Watch DAG scope, ancestors, construction history, and materials."""
        if not self._enabled:
            return False
        try:
            root_paths = self._root_paths(roots)
            if not root_paths:
                return False
            if root_paths in self._watched_roots:
                return True

            nodes_to_watch = set()
            mesh_paths = set()
            shading_groups = set()
            for root, _uuid in root_paths:
                nodes = cmds.listRelatives(root, allDescendents=True, fullPath=True) or []
                nodes.append(root)
                parent = cmds.listRelatives(root, parent=True, fullPath=True) or []
                while parent:
                    nodes.append(parent[0])
                    parent = cmds.listRelatives(parent[0], parent=True, fullPath=True) or []
                nodes_to_watch.update(nodes)
                for dag_node in nodes:
                    if cmds.nodeType(dag_node) == 'mesh':
                        mesh_paths.add(dag_node)

            # A mesh's construction history is relevant to its evaluated
            # geometry; querying history on every ancestor/transform is
            # redundant and may walk large shader-membership graphs.
            for mesh_path in mesh_paths:
                nodes_to_watch.update(cmds.listHistory(mesh_path, pruneDagObjects=True) or [])
                shading_groups.update(cmds.listConnections(
                    mesh_path, type='shadingEngine') or [])

            # Query only shader plugs. Asking listConnections(shading_group)
            # returns every set member (often hundreds of meshes), then
            # listHistory was redundantly called for each member as if it were
            # a shader. Process each unique shader network once instead.
            for shading_group in shading_groups:
                nodes_to_watch.add(shading_group)
                for plug in ('surfaceShader', 'displacementShader', 'volumeShader'):
                    shader_nodes = cmds.listConnections(
                        shading_group + '.' + plug,
                        source=True, destination=False) or []
                    nodes_to_watch.update(shader_nodes)
                    for shader in shader_nodes:
                        nodes_to_watch.update(cmds.listHistory(
                            shader, pruneDagObjects=True) or [])

            nodes_to_watch.update(cmds.ls(type="displayLayer") or [])
            watched_tokens = set()
            for node in nodes_to_watch:
                if not node or not cmds.objExists(node):
                    continue
                uuids = cmds.ls(node, uuid=True) or []
                token = uuids[0] if uuids else node
                if token in watched_tokens:
                    continue
                watched_tokens.add(token)
                self._watch_node(node)

            self._watched_roots.add(root_paths)
            return self._enabled
        except Exception:
            self.close()
            return False

    def _key(self, roots, settings):
        paths = self._root_paths(roots)
        if not paths:
            return None
        return paths, settings

    def get(self, roots, settings=()):
        if not self._enabled:
            return None
        try:
            key = self._key(roots, settings)
            entry = self._entries.get(key)
            if entry and entry[0] == self._generation:
                return entry[1]
        except Exception:
            self.invalidate()
        return None

    def put(self, roots, settings, value):
        if not self._enabled:
            return None
        try:
            if not self.watch(roots):
                return None
            key = self._key(roots, settings)
            self._revision += 1
            self._entries[key] = (self._generation, value, self._revision)
            return self._revision
        except Exception:
            self.invalidate()
            return None

    def is_current(self, roots, settings, revision=None):
        if not self._enabled:
            return False
        try:
            entry = self._entries.get(self._key(roots, settings))
            return bool(entry and entry[0] == self._generation and
                        (revision is None or entry[2] == revision))
        except Exception:
            self.invalidate()
            return False

    def close(self):
        for callback in self._callbacks + self._node_callbacks:
            try:
                om.MMessage.removeCallback(callback)
            except Exception:
                pass
        self._callbacks = []
        self._node_callbacks = []
        self._watch_tokens.clear()
        self._entries.clear()
        self._watched_roots.clear()
        self._enabled = False
