# -*- coding: utf-8 -*-
"""Maya standalone smoke tests for the optimized export preparation path."""
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


def _make_material(name):
    material = cmds.shadingNode("lambert", asShader=True, name=name)
    shading_group = cmds.sets(
        renderable=True, noSurfaceShader=True, empty=True,
        name=name + "SG")
    cmds.connectAttr(material + ".outColor", shading_group + ".surfaceShader", force=True)
    return shading_group


def _build_scene():
    cmds.file(new=True, force=True)
    hp_root = cmds.group(empty=True, name="Chapter_HP")
    hp_group = cmds.group(empty=True, name="Group_HP", parent=hp_root)
    lp_root = cmds.group(empty=True, name="Chapter_LP")
    lp_group = cmds.group(empty=True, name="Group_LP", parent=lp_root)

    hp = cmds.polyCube(name="Chapter_Group_high_001", constructionHistory=False)[0]
    hp = cmds.parent(hp, hp_group)[0]

    lp_nodes = []
    for index in (1, 2):
        lp = cmds.polyPlane(
            name="Chapter_Group_low_{:03d}".format(index),
            subdivisionsX=4, subdivisionsY=1,
            constructionHistory=False)[0]
        lp = cmds.parent(lp, lp_group)[0]
        lp_nodes.append(_long(lp))

    sg_a = _make_material("ExportTest_A")
    sg_b = _make_material("ExportTest_B")
    for lp in lp_nodes:
        cmds.sets("{}.f[0:1]".format(lp), edit=True, forceElement=sg_a)
        cmds.sets("{}.f[2:3]".format(lp), edit=True, forceElement=sg_b)

    return _long(hp_root), _long(lp_root), _long(hp), lp_nodes


def _temp_roots():
    out = []
    for pattern in (
            "BG_HP_Export_Zero_Temp*",
            "BG_Export_Tri_Temp*",
            "BG_Material_Export_Temp*"):
        out.extend(cmds.ls(pattern, type="transform", long=True) or [])
    return out


def run():
    hp_root, lp_root, original_hp, original_lps = _build_scene()
    original_hp_faces = cmds.polyEvaluate(original_hp, face=True)
    original_lp_faces = [cmds.polyEvaluate(node, face=True) for node in original_lps]

    snapshot = Processor.build_chapter_snapshot(
        "Chapter", hp_root, lp_root, {"Group": 1})
    assert len(snapshot["hp_all"]) == 1
    assert len(snapshot["lp_all"]) == 2
    for lp in snapshot["lp_all"]:
        material_faces = Processor._get_mesh_materials_and_faces(lp)
        assert len(material_faces) == 2
        assert sorted(face for faces in material_faces.values() for face in faces) == [0, 1, 2, 3]

    captures = []

    def capture_export(_path):
        selected = cmds.ls(selection=True, long=True, type="transform") or []
        captures.append({
            "names": [node.split("|")[-1] for node in selected],
            "faces": [cmds.polyEvaluate(node, face=True) for node in selected],
            "matrices": [cmds.xform(node, query=True, matrix=True, worldSpace=True) for node in selected],
        })

    original_export = Processor.export_selected_fbx
    Processor.export_selected_fbx = staticmethod(capture_export)
    try:
        result = Processor.export_chapter(
            "Chapter", hp_root, lp_root, [], mode="hp",
            export_dir=os.getcwd(), smooth_states={"Group": 1},
            prepared_chapters=[snapshot], status_callback=lambda _label: None)
        assert result == "Chapter_HP"
        hp_capture = captures.pop(0)
        assert len([name for name in hp_capture["names"] if "_high" in name.lower()]) == 1
        assert hp_capture["faces"][0] > original_hp_faces
        assert cmds.polyEvaluate(original_hp, face=True) == original_hp_faces
        assert not _temp_roots()
        assert cmds.undoInfo(query=True, state=True)

        zero_smooth_snapshot = Processor.build_chapter_snapshot(
            "Chapter", hp_root, lp_root, {"Group": 0})
        result = Processor.export_chapter(
            "Chapter", hp_root, lp_root, [], mode="hp",
            export_dir=os.getcwd(), smooth_states={"Group": 0},
            prepared_chapters=[zero_smooth_snapshot],
            status_callback=lambda _label: None)
        assert result == "Chapter_HP"
        zero_smooth_capture = captures.pop(0)
        assert zero_smooth_capture["faces"] == [original_hp_faces]
        assert cmds.polyEvaluate(original_hp, face=True) == original_hp_faces
        assert not _temp_roots()

        result = Processor.export_chapter(
            "Chapter", hp_root, lp_root, [], mode="lp",
            export_dir=os.getcwd(), smooth_states={"Group": 1},
            prepared_chapters=[snapshot], status_callback=lambda _label: None)
        assert result == "Chapter_LP"
        lp_capture = captures.pop(0)
        assert len(lp_capture["names"]) == 2
        assert lp_capture["faces"] == [8, 8]
        assert [cmds.polyEvaluate(node, face=True) for node in original_lps] == original_lp_faces
        assert not _temp_roots()
        assert cmds.undoInfo(query=True, state=True)

        result = Processor.export_chapter(
            "Chapter", hp_root, lp_root, [], mode="both",
            export_dir=os.getcwd(), smooth_states={"Group": 1},
            prepared_chapters=[snapshot], status_callback=lambda _label: None)
        assert result == "Chapter"
        both_capture = captures.pop(0)
        high_names = [name for name in both_capture["names"] if "_high" in name.lower()]
        low_names = [name for name in both_capture["names"] if "_low" in name.lower()]
        assert len(high_names) == 2, high_names
        assert len(low_names) == 4, low_names
        assert len(set(high_names)) == 2
        assert cmds.polyEvaluate(original_hp, face=True) == original_hp_faces
        assert [cmds.polyEvaluate(node, face=True) for node in original_lps] == original_lp_faces
        assert not _temp_roots()
        assert cmds.undoInfo(query=True, state=True)

        result = Processor.export_chapter(
            "Chapter", hp_root, lp_root, [], mode="hp",
            export_dir=os.getcwd(), smooth_states={"Group": 1},
            prepared_chapters=[snapshot], status_callback=lambda _label: None,
            cancel_check=lambda: True)
        assert result is False
        assert not captures
        assert not _temp_roots()
    finally:
        Processor.export_selected_fbx = original_export
        Processor._cleanup_stale_export_temps()

    export_dir = tempfile.mkdtemp(prefix="bake_groups_export_test_")
    try:
        before_settings = {
            command: Processor._query_fbx_bool(command)
            for command in (
                "FBXExportInputConnections",
                "FBXExportGenerateLog",
                "FBXExportSmoothMesh")
        }
        result = Processor.export_chapter(
            "Chapter", hp_root, lp_root, [], mode="lp",
            export_dir=export_dir, smooth_states={"Group": 1},
            prepared_chapters=[snapshot], status_callback=lambda _label: None)
        output_path = os.path.join(export_dir, result + ".fbx")
        assert os.path.isfile(output_path)
        assert os.path.getsize(output_path) > 0
        hp_result = Processor.export_chapter(
            "Chapter", hp_root, lp_root, [], mode="hp",
            export_dir=export_dir, smooth_states={"Group": 1},
            prepared_chapters=[snapshot], status_callback=lambda _label: None)
        hp_output_path = os.path.join(export_dir, hp_result + ".fbx")
        assert os.path.isfile(hp_output_path)
        assert os.path.getsize(hp_output_path) > 0
        both_result = Processor.export_chapter(
            "Chapter", hp_root, lp_root, [], mode="both",
            export_dir=export_dir, smooth_states={"Group": 1},
            prepared_chapters=[snapshot], status_callback=lambda _label: None)
        both_output_path = os.path.join(export_dir, both_result + ".fbx")
        assert os.path.isfile(both_output_path)
        assert os.path.getsize(both_output_path) > 0
        after_settings = {
            command: Processor._query_fbx_bool(command)
            for command in before_settings
        }
        assert after_settings == before_settings
        assert not _temp_roots()

        cmds.file(new=True, force=True)
        cmds.file(
            hp_output_path, i=True, type="FBX", ignoreVersion=True,
            mergeNamespacesOnClash=False, namespace="fbxtest", options="fbx")
        hp_imported_shapes = cmds.ls(type="mesh", long=True, noIntermediate=True) or []
        assert len(hp_imported_shapes) == 1
        hp_imported = cmds.listRelatives(
            hp_imported_shapes[0], parent=True, fullPath=True)[0]
        assert cmds.polyEvaluate(hp_imported, face=True) > original_hp_faces
        assert not any("BG_HP_Export_" in node for node in cmds.ls(type="transform") or [])

        cmds.file(new=True, force=True)
        cmds.file(
            output_path, i=True, type="FBX", ignoreVersion=True,
            mergeNamespacesOnClash=False, namespace="fbxtest", options="fbx")
        imported_shapes = cmds.ls(type="mesh", long=True, noIntermediate=True) or []
        imported_meshes = [
            cmds.listRelatives(shape, parent=True, fullPath=True)[0]
            for shape in imported_shapes
        ]
        assert len(imported_meshes) == 2
        assert sorted(cmds.polyEvaluate(mesh, face=True) for mesh in imported_meshes) == [8, 8]
        imported_transforms = cmds.ls(type="transform") or []
        assert not any("BG_Export_" in node for node in imported_transforms), imported_transforms

        cmds.file(new=True, force=True)
        cmds.file(
            both_output_path, i=True, type="FBX", ignoreVersion=True,
            mergeNamespacesOnClash=False, namespace="fbxtest", options="fbx")
        combined_shapes = cmds.ls(type="mesh", long=True, noIntermediate=True) or []
        assert len(combined_shapes) == 6
        assert not any("BG_" in node for node in cmds.ls(type="transform") or [])
    finally:
        shutil.rmtree(export_dir, ignore_errors=True)

    print("optimized export smoke test: OK")


if __name__ == "__main__":
    try:
        run()
    finally:
        maya.standalone.uninitialize()
