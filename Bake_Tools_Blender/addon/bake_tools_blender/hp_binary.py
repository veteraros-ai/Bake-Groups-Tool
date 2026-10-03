"""Write the Maya-compatible, UV-free BGHPBIN1 stream from Blender meshes."""

from __future__ import annotations

from array import array
from pathlib import Path
import os
import struct
import sys
import time

import bpy


MAGIC = b"BGHPBIN1"
_U32 = struct.Struct("<I")
_U64 = struct.Struct("<Q")
_F64_3 = struct.Struct("<ddd")


class UnsupportedFastExport(ValueError):
    """The mesh needs Blender's regular FBX evaluator for correct output."""


def _u32(stream, value):
    if not 0 <= int(value) <= 0xffffffff:
        raise UnsupportedFastExport("HP geometry exceeds the transfer format limits")
    stream.write(_U32.pack(int(value)))


def _string(stream, value):
    encoded = str(value).encode("utf-8")
    if len(encoded) > 1024 * 1024:
        raise UnsupportedFastExport("HP mesh or material name is too long")
    _u32(stream, len(encoded))
    stream.write(encoded)


def _array(stream, typecode, values):
    data = array(typecode, values)
    if sys.byteorder != "little":
        data.byteswap()
    data.tofile(stream)


def _fbx_axis(vector):
    # Blender is Z-up; the existing Maya helper writes a Y-up FBX scene.
    return vector.x, vector.z, -vector.y


def _fbx_position(vector):
    # The helper's FBX scene is in centimetres, as in Maya. Blender's importer
    # converts those centimetres back to metres for the default scene units.
    x, y, z = _fbx_axis(vector)
    return x * 100.0, y * 100.0, z * 100.0


def _check_topology(mesh):
    if any(attribute.name == "crease_edge" and
           any(abs(value.value) > 1.0e-8 for value in attribute.data)
           for attribute in mesh.attributes):
        raise UnsupportedFastExport("Creased HP requires Blender subdivision")
    if mesh.shape_keys is not None:
        raise UnsupportedFastExport("HP shape keys require Blender FBX export")
    # The helper handles ordinary boundary edges. Wire and non-manifold edges
    # need Blender's evaluator because their corner rules are less predictable.
    import bmesh
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        if any(not (edge.is_manifold or edge.is_boundary) for edge in bm.edges):
            raise UnsupportedFastExport("Non-manifold HP requires Blender subdivision")
    finally:
        bm.free()


def write(path, objects, levels, progress=None):
    """Read evaluated base HP, preserving artist modifiers and scene state."""
    if not objects:
        raise ValueError("No HP meshes to export")
    depsgraph = bpy.context.evaluated_depsgraph_get()
    names = set()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    preview_state = []
    temporary_smooth = []
    profile = bool(os.environ.get("BAKE_TOOLS_PROFILE"))
    started = time.perf_counter()
    extraction_seconds = 0.0
    serialization_seconds = 0.0
    try:
        # Changing one modifier and updating the whole dependency graph for
        # every HP was much slower than Blender's own batch FBX export.  Build
        # the evaluated state once, then stream every mesh from that snapshot.
        for obj in objects:
            if obj.type != "MESH":
                raise UnsupportedFastExport("Non-mesh HP requires Blender FBX export")
            level = max(0, min(5, int(levels.get(obj.as_pointer(), 0))))
            for modifier in obj.modifiers:
                if modifier.name.startswith("Bake Tools Smooth Preview"):
                    preview_state.append((modifier, modifier.show_viewport))
                    modifier.show_viewport = False
            if level > 0:
                modifier = obj.modifiers.new("Bake Tools HP Transfer Smooth", "SUBSURF")
                temporary_smooth.append((obj, modifier))
                modifier.subdivision_type = "CATMULL_CLARK"
                modifier.levels = level
                modifier.render_levels = level
                modifier.show_viewport = True
        depsgraph.update()
        preparation_seconds = time.perf_counter() - started
        with open(path, "wb") as stream:
            stream.write(MAGIC)
            _u32(stream, len(objects))
            for index, obj in enumerate(objects):
                stage_started = time.perf_counter()
                evaluated = obj.evaluated_get(depsgraph)
                # HP transfer needs positions, faces, materials and normals,
                # never UV/color layers.  Avoid copying those heavy attributes.
                mesh = evaluated.to_mesh(preserve_all_data_layers=False, depsgraph=depsgraph)
                extraction_seconds += time.perf_counter() - stage_started
                stage_started = time.perf_counter()
                try:
                    if mesh is None or not mesh.vertices or not mesh.polygons:
                        raise UnsupportedFastExport("Empty HP mesh")
                    name = obj.name
                    suffix = 2
                    while name.casefold() in names:
                        name = "{}__BG{:03d}".format(obj.name, suffix)
                        suffix += 1
                    names.add(name.casefold())
                    _string(stream, name)
                    _u32(stream, 0)
                    materials = tuple(material for material in mesh.materials if material is not None)
                    _u32(stream, len(materials))
                    for material in materials:
                        _string(stream, material.name)
                        color = material.diffuse_color
                        stream.write(_F64_3.pack(float(color[0]), float(color[1]), float(color[2])))
                    _u32(stream, len(mesh.vertices))
                    _u32(stream, len(mesh.polygons))
                    stream.write(_U64.pack(len(mesh.loops)))
                    _u32(stream, len(mesh.loops))
                    matrix = obj.matrix_world
                    reverse = matrix.determinant() < 0
                    _array(stream, "d", (component for vertex in mesh.vertices
                                         for component in _fbx_position(matrix @ vertex.co)))
                    normal_matrix = matrix.to_3x3().inverted().transposed()
                    _array(stream, "d", (component for normal in mesh.corner_normals
                                         for component in _fbx_axis(
                                             (normal_matrix @ normal.vector).normalized())))
                    face_data = bytearray()
                    material_slots = {material.name: slot for slot, material in enumerate(materials)}
                    for polygon in mesh.polygons:
                        loops = list(range(polygon.loop_start, polygon.loop_start + polygon.loop_total))
                        if reverse:
                            loops.reverse()
                        face_material = mesh.materials[polygon.material_index] if mesh.materials else None
                        material_index = material_slots.get(face_material.name, 0xffffffff) if face_material else 0xffffffff
                        face_data.extend(struct.pack("<II", len(loops), material_index))
                        face_data.extend(array("I", (mesh.loops[loop].vertex_index for loop in loops)).tobytes())
                        face_data.extend(array("I", loops).tobytes())
                        if len(face_data) >= 4 * 1024 * 1024:
                            stream.write(face_data)
                            face_data.clear()
                    stream.write(face_data)
                finally:
                    evaluated.to_mesh_clear()
                serialization_seconds += time.perf_counter() - stage_started
                if progress:
                    progress.update(int((index + 1) * 50 / len(objects)),
                                    "Preparing HP: {}".format(obj.name))
    finally:
        for obj, modifier in reversed(temporary_smooth):
            obj.modifiers.remove(modifier)
        for modifier, visible in preview_state:
            modifier.show_viewport = visible
        depsgraph.update()
    if profile:
        print("BAKE_TOOLS_HP_WRITER_PROFILE prepare={:.3f}s extract={:.3f}s serialize={:.3f}s cleanup={:.3f}s".format(
            preparation_seconds, extraction_seconds, serialization_seconds,
            time.perf_counter() - started - preparation_seconds - extraction_seconds - serialization_seconds))
    return len(objects)
