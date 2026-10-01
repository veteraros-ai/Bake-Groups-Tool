# -*- coding: utf-8 -*-
"""Small, UV-free transfer format for the external HP FBX exporter.

All geometry is read from Maya's API in world space. Scene DAG nodes and Undo
state are never changed. The C++ helper consumes this stream one mesh at a time.
"""
from __future__ import absolute_import

import array
import os
import struct
import sys

import maya.api.OpenMaya as om
import maya.cmds as cmds


MAGIC = b"BGHPBIN1"
_U32 = struct.Struct("<I")
_U64 = struct.Struct("<Q")
_F64_3 = struct.Struct("<ddd")


def _u32(stream, value):
    if value < 0 or value > 0xffffffff:
        raise ValueError("HP transfer value is outside 32-bit range: {}".format(value))
    stream.write(_U32.pack(value))


def _string(stream, value):
    data = value.encode("utf-8")
    if len(data) > 1024 * 1024:
        raise ValueError("HP transfer name is too long")
    _u32(stream, len(data))
    stream.write(data)


def _array(stream, typecode, values):
    payload = array.array(typecode, values)
    if sys.byteorder != "little":
        payload.byteswap()
    payload.tofile(stream)


def _material(shader_object, cache):
    sg_name = om.MFnDependencyNode(shader_object).name()
    cached = cache.get(sg_name)
    if cached is not None:
        return cached
    surfaces = cmds.listConnections(
        sg_name + ".surfaceShader", source=True, destination=False) or []
    shader = surfaces[0] if surfaces else sg_name
    color = (0.8, 0.8, 0.8)
    for attribute in ("baseColor", "color"):
        try:
            value = cmds.getAttr(shader + "." + attribute)
            if value and len(value[0]) >= 3:
                color = tuple(float(v) for v in value[0][:3])
                break
        except Exception:
            pass
    cached = (shader, color)
    cache[sg_name] = cached
    return cached


def _level(mesh_name, long_name, levels, is_zbrush):
    if is_zbrush:
        return 0
    short_lower = mesh_name.lower()
    value = levels.get(long_name, levels.get(short_lower, 0))
    if not value and "_high" in short_lower:
        prefix = short_lower.rsplit("_high", 1)[0]
        value = levels.get("prefix:" + prefix, 0)
    return max(0, min(5, int(value or 0)))


def write(path, meshes, levels, zbrush_check, cancel_check=None):
    """Write all HP meshes directly; returns the number of exported meshes."""
    if not meshes:
        raise ValueError("No HP meshes to export")
    seen_names = set()
    material_cache = {}
    count = 0
    with open(path, "wb") as stream:
        stream.write(MAGIC)
        _u32(stream, len(meshes))
        for mesh in meshes:
            if cancel_check and cancel_check():
                raise InterruptedError("HP export cancelled")
            selection = om.MSelectionList()
            selection.add(mesh)
            dag = selection.getDagPath(0)
            if dag.node().hasFn(om.MFn.kTransform):
                dag.extendToShape()
            if not dag.node().hasFn(om.MFn.kMesh):
                raise ValueError("HP node has no mesh shape: {}".format(mesh))
            fn = om.MFnMesh(dag)
            if fn.isIntermediateObject:
                raise ValueError("HP node is an intermediate shape: {}".format(mesh))
            long_name = (cmds.ls(mesh, long=True) or [mesh])[0]
            short_name = long_name.split("|")[-1]
            name = short_name
            suffix = 2
            while name.lower() in seen_names:
                name = "{}__BG{:03d}".format(short_name, suffix)
                suffix += 1
            seen_names.add(name.lower())
            level = _level(short_name, long_name, levels, zbrush_check(mesh))
            points = fn.getPoints(om.MSpace.kWorld)
            counts, indices = fn.getVertices()
            if not points or not counts:
                raise ValueError("Empty HP mesh: {}".format(mesh))
            if sum(counts) != len(indices):
                raise ValueError("Invalid polygon index count: {}".format(mesh))
            shaders, face_materials = fn.getConnectedShaders(dag.instanceNumber())
            materials = [_material(shader, material_cache) for shader in shaders]
            if len(face_materials) != len(counts):
                raise ValueError("Invalid face material count: {}".format(mesh))
            normals = fn.getNormals(om.MSpace.kWorld) if level == 0 else []
            normal_counts, normal_ids = fn.getNormalIds() if level == 0 else ([], [])
            if normals and (list(normal_counts) != list(counts) or len(normal_ids) != len(indices)):
                raise ValueError("Invalid normal indices: {}".format(mesh))

            _string(stream, name)
            _u32(stream, level)
            _u32(stream, len(materials))
            for material_name, color in materials:
                _string(stream, material_name)
                stream.write(_F64_3.pack(*color))
            _u32(stream, len(points))
            _u32(stream, len(counts))
            stream.write(_U64.pack(len(indices)))
            _u32(stream, len(normals))
            _array(stream, "d", (coordinate for point in points
                                 for coordinate in (point.x, point.y, point.z)))
            if normals:
                _array(stream, "d", (coordinate for normal in normals
                                     for coordinate in (normal.x, normal.y, normal.z)))
            offset = 0
            reverse = dag.inclusiveMatrix().det4x4() < 0.0
            face_buffer = bytearray()
            for face_index, face_count in enumerate(counts):
                material_index = int(face_materials[face_index])
                face_buffer.extend(struct.pack(
                    "<II", face_count,
                    material_index if material_index >= 0 else 0xffffffff))
                face_vertices = indices[offset:offset + face_count]
                face_normals = normal_ids[offset:offset + face_count] if normals else ()
                if reverse:
                    face_vertices = face_vertices[::-1]
                    face_normals = face_normals[::-1]
                face_buffer.extend(array.array("I", face_vertices).tobytes())
                if normals:
                    face_buffer.extend(array.array("I", face_normals).tobytes())
                offset += face_count
                if len(face_buffer) >= 4 * 1024 * 1024:
                    stream.write(face_buffer)
                    face_buffer.clear()
            if face_buffer:
                stream.write(face_buffer)
            count += 1
    return count
