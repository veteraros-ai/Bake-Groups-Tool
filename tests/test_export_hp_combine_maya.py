# -*- coding: utf-8 -*-
"""Maya standalone coverage for fast subgroup HP export combining."""
from __future__ import print_function

import os
import shutil
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import maya.standalone

maya.standalone.initialize(name="python")

import maya.cmds as cmds


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_DIR = os.environ.get("BAKE_GROUPS_TEST_SCRIPT_DIR") or os.path.join(ROOT, "Bake_Groups")
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import bg_final_export


Processor = bg_final_export.FinalExportProcessor


def _long(node):
    return (cmds.ls(node, long=True) or [node])[0]


def _material(name):
    shader = cmds.shadingNode("lambert", asShader=True, name=name)
    shading_group = cmds.sets(
        renderable=True, noSurfaceShader=True, empty=True,
        name=name + "SG")
    cmds.connectAttr(
        shader + ".outColor", shading_group + ".surfaceShader", force=True)
    return shading_group


def _build_scene():
    cmds.file(new=True, force=True)
    hp_root = cmds.group(empty=True, name="Chapter_HP")
    hp_group = cmds.group(empty=True, name="Group_HP", parent=hp_root)
    lp_root = cmds.group(empty=True, name="Chapter_LP")
    lp_group = cmds.group(empty=True, name="Group_LP", parent=lp_root)

    regular = []
    for index, x_pos in ((1, -3.0), (2, 3.0)):
        node = cmds.polyCube(
            name="Chapter_Group_high_{:03d}".format(index),
            constructionHistory=False)[0]
        cmds.xform(node, translation=(x_pos, 1.0, 0.0), worldSpace=True)
        node = cmds.parent(node, hp_group, absolute=True)[0]
        regular.append(_long(node))

    zbrush = cmds.polyCube(
        name="Chapter_Group_high_003", constructionHistory=False)[0]
    cmds.xform(zbrush, translation=(0.0, 4.0, 0.0), worldSpace=True)
    zbrush = _long(cmds.parent(zbrush, hp_group, absolute=True)[0])
    zbrush_layer = cmds.createDisplayLayer(
        name="Sculpt_ZBrush", empty=True)
    cmds.editDisplayLayerMembers(zbrush_layer, zbrush, noRecurse=True)
    zbrush_shape = cmds.listRelatives(
        zbrush, shapes=True, fullPath=True, noIntermediate=True)[0]
    cmds.setAttr(zbrush_shape + ".displaySmoothMesh", 2)

    material_a = _material("HP_Red")
    material_b = _material("HP_Blue")
    cmds.sets(regular[0], edit=True, forceElement=material_a)
    cmds.sets(regular[1], edit=True, forceElement=material_b)

    low = cmds.polyPlane(
        name="Chapter_Group_low_001", constructionHistory=False)[0]
    cmds.parent(low, lp_group)

    return _long(hp_root), _long(lp_root), regular, zbrush, zbrush_shape


def _temp_roots():
    nodes = []
    for pattern in (
            "BG_HP_Export_Zero_Temp*",
            "BG_Export_Tri_Temp*",
            "BG_Material_Export_Temp*"):
        nodes.extend(cmds.ls(pattern, type="transform", long=True) or [])
    return nodes


def _capture_selection(captures):
    selected = cmds.ls(selection=True, long=True, type="transform") or []
    records = []
    for node in selected:
        shapes = cmds.listRelatives(
            node, shapes=True, fullPath=True, noIntermediate=True) or []
        shading_groups = set()
        for shape in shapes:
            shading_groups.update(
                cmds.listConnections(shape, type="shadingEngine") or [])
        records.append({
            "name": node.split("|")[-1],
            "faces": cmds.polyEvaluate(node, face=True),
            "translation": cmds.xform(
                node, query=True, translation=True, worldSpace=True),
            "rotation": cmds.xform(
                node, query=True, rotation=True, worldSpace=True),
            "scale": cmds.xform(
                node, query=True, scale=True, relative=True),
            "materials": sorted(shading_groups),
        })
    captures.append(records)


def run():
    hp_root, lp_root, regular, zbrush, zbrush_shape = _build_scene()
    source_faces = {
        node: cmds.polyEvaluate(node, face=True)
        for node in regular + [zbrush]
    }
    source_translations = {
        node: cmds.xform(node, query=True, translation=True, worldSpace=True)
        for node in regular + [zbrush]
    }
    snapshot = Processor.build_chapter_snapshot(
        "Chapter", hp_root, lp_root, {"Group": 1})

    captures = []
    unite_input_counts = []
    smooth_targets = []
    original_export = Processor.export_selected_fbx
    original_unite = cmds.polyUnite
    original_smooth = cmds.polySmooth

    def capture_export(_path):
        _capture_selection(captures)

    def track_unite(*args, **kwargs):
        nodes = args[0] if args and isinstance(args[0], (list, tuple)) else args
        unite_input_counts.append(len(nodes))
        return original_unite(*args, **kwargs)

    def track_smooth(*args, **kwargs):
        smooth_targets.append(str(args[0]) if args else "")
        return original_smooth(*args, **kwargs)

    Processor.export_selected_fbx = staticmethod(capture_export)
    cmds.polyUnite = track_unite
    cmds.polySmooth = track_smooth
    try:
        result = Processor.export_chapter(
            "Chapter", hp_root, lp_root, [], mode="hp",
            export_dir=os.getcwd(), smooth_states={"Group": 1},
            prepared_chapters=[snapshot], status_callback=lambda _label: None)
    finally:
        Processor.export_selected_fbx = original_export
        cmds.polyUnite = original_unite
        cmds.polySmooth = original_smooth

    assert result == "Chapter_HP"
    assert unite_input_counts == [2], unite_input_counts
    assert len(smooth_targets) == 1, smooth_targets
    assert len(captures) == 1
    records = {record["name"]: record for record in captures[0]}
    assert sorted(records) == [
        "Chapter_Group_high", "Chapter_Group_high_003"], sorted(records)

    combined = records["Chapter_Group_high"]
    zbrush_record = records["Chapter_Group_high_003"]
    assert combined["faces"] == 48, combined
    assert zbrush_record["faces"] == source_faces[zbrush], zbrush_record
    assert "HP_RedSG" in combined["materials"], combined["materials"]
    assert "HP_BlueSG" in combined["materials"], combined["materials"]

    for record in records.values():
        assert all(abs(value) < 1e-6 for value in record["translation"]), record
        assert all(abs(value) < 1e-6 for value in record["rotation"]), record
        assert all(abs(value - 1.0) < 1e-6 for value in record["scale"]), record

    # Source meshes, ZBrush preview state, and display-layer membership survive.
    for node in regular + [zbrush]:
        assert cmds.objExists(node)
        assert cmds.polyEvaluate(node, face=True) == source_faces[node]
        assert cmds.xform(
            node, query=True, translation=True, worldSpace=True) == source_translations[node]
    assert cmds.getAttr(zbrush_shape + ".displaySmoothMesh") == 2
    assert Processor._is_zbrush_mesh(zbrush)
    assert not _temp_roots(), _temp_roots()
    assert cmds.undoInfo(query=True, state=True)

    # A failed combine must fall back to separate copies, never fail the export.
    captures[:] = []
    original_export = Processor.export_selected_fbx
    original_combine = Processor._combine_regular_hp_export_group
    Processor.export_selected_fbx = staticmethod(capture_export)

    def fail_combine(*_args, **_kwargs):
        raise RuntimeError("intentional combine failure")

    Processor._combine_regular_hp_export_group = staticmethod(fail_combine)
    try:
        result = Processor.export_chapter(
            "Chapter", hp_root, lp_root, [], mode="hp",
            export_dir=os.getcwd(), smooth_states={"Group": 1},
            prepared_chapters=[snapshot], status_callback=lambda _label: None)
    finally:
        Processor.export_selected_fbx = original_export
        Processor._combine_regular_hp_export_group = original_combine
        Processor._cleanup_stale_export_temps()

    assert result == "Chapter_HP"
    fallback = {record["name"]: record for record in captures[0]}
    assert sorted(fallback) == [
        "Chapter_Group_high_001",
        "Chapter_Group_high_002",
        "Chapter_Group_high_003",
    ]
    assert fallback["Chapter_Group_high_001"]["faces"] == 24
    assert fallback["Chapter_Group_high_002"]["faces"] == 24
    assert fallback["Chapter_Group_high_003"]["faces"] == 6
    assert not _temp_roots(), _temp_roots()

    # Exercise the real FBX plug-in and verify the optimized object structure
    # survives a round trip, without leaking any internal BG temp hierarchy.
    export_dir = tempfile.mkdtemp(prefix="bake_groups_hp_combine_")
    try:
        result = Processor.export_chapter(
            "Chapter", hp_root, lp_root, [], mode="hp",
            export_dir=export_dir, smooth_states={"Group": 1},
            prepared_chapters=[snapshot], status_callback=lambda _label: None)
        export_path = os.path.join(export_dir, result + ".fbx")
        assert os.path.isfile(export_path)
        assert os.path.getsize(export_path) > 0

        cmds.file(new=True, force=True)
        cmds.file(
            export_path, i=True, type="FBX", ignoreVersion=True,
            mergeNamespacesOnClash=False, namespace="hpcombine", options="fbx")
        imported_shapes = cmds.ls(
            type="mesh", long=True, noIntermediate=True) or []
        imported = [
            cmds.listRelatives(shape, parent=True, fullPath=True)[0]
            for shape in imported_shapes
        ]
        assert len(imported) == 2, imported
        assert sorted(cmds.polyEvaluate(node, face=True) for node in imported) == [6, 48]
        assert not any(
            "BG_HP_Export_" in node
            for node in (cmds.ls(type="transform", long=True) or []))
    finally:
        shutil.rmtree(export_dir, ignore_errors=True)

    print("HP subgroup combine export test: OK")


if __name__ == "__main__":
    try:
        run()
    finally:
        maya.standalone.uninitialize()
