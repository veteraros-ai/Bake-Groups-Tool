# -*- coding: utf-8 -*-
"""Maya standalone integration test for the optional external HP FBX path."""
from __future__ import print_function

import os
import sys
import tempfile
import subprocess

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6 import QtWidgets
except ImportError:
    from PySide2 import QtWidgets

APP = QtWidgets.QApplication.instance()
if not isinstance(APP, QtWidgets.QApplication):
    APP = QtWidgets.QApplication([])

import maya.standalone
maya.standalone.initialize(name="python")

import maya.cmds as cmds
cmds.loadPlugin('fbxmaya', quiet=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_DIR = os.environ.get("BAKE_GROUPS_TEST_SCRIPT_DIR") or os.path.join(ROOT, "Bake_Groups")
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import bg_final_export
import bg_mixins


class _PanelHarness(QtWidgets.QWidget, bg_mixins.ExportMixin):
    def __init__(self):
        QtWidgets.QWidget.__init__(self)


def _obj_face_counts(path):
    result = {}
    current = None
    with open(path, "r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            if line.startswith("o "):
                current = line[2:].strip()
                result.setdefault(current, 0)
            elif line.startswith("f ") and current is not None:
                result[current] += 1
    return result


def _test_export_panel():
    harness = _PanelHarness()
    panel = harness.build_export_panel({})
    assert not hasattr(harness, 'exp_external_hp_obj')
    assert harness.exp_files_sep.isChecked()
    assert harness.exp_files_one.isEnabled()
    assert harness._use_external_hp_fbx(True, True, False, False, False)
    assert not harness._use_external_hp_fbx(True, True, True, False, False)
    assert harness._use_external_hp_fbx(True, True, True, True, False)
    assert harness._use_external_hp_fbx(True, True, True, False, True)
    harness.exp_target.setCurrentIndex(1)
    assert not harness.exp_files_one.isEnabled()
    harness.exp_target.setCurrentIndex(0)
    panel.close()
    harness.close()


def _test_external_option_reaches_hp_export():
    harness = _PanelHarness()
    original = bg_final_export.FinalExportProcessor.export_chapter
    calls = []
    def record(*_args, **kwargs):
        calls.append((kwargs.get('mode'), kwargs.get('external_hp_obj', False)))
        return True
    bg_final_export.FinalExportProcessor.export_chapter = staticmethod(record)
    try:
        assert harness._export_chapter_files(
            {'base': 'Chapter'}, '.', True, False, False, False,
            snapshot={'hp': 'HP', 'lp': 'LP'}, external_hp_obj=True)
        assert calls == [('hp', True)], calls
    finally:
        bg_final_export.FinalExportProcessor.export_chapter = original
        harness.close()


def _test_world_transform_normals_and_materials():
    import maya.api.OpenMaya as om
    import bg_hp_binary
    cmds.file(new=True, force=True)
    parent = cmds.group(empty=True, name='HP_Parent')
    cmds.setAttr(parent + '.scaleX', -2)
    cmds.setAttr(parent + '.rotateY', 33)
    mesh = cmds.polyCube(name='Rigid_high', constructionHistory=False)[0]
    mesh = cmds.parent(mesh, parent, absolute=True)[0]
    material = cmds.shadingNode('lambert', asShader=True, name='BGTestRed')
    cmds.setAttr(material + '.color', 1, 0, 0, type='double3')
    sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name='BGTestRedSG')
    cmds.connectAttr(material + '.outColor', sg + '.surfaceShader', force=True)
    cmds.sets(mesh + '.f[0]', edit=True, forceElement=sg)
    layer = cmds.createDisplayLayer(empty=True, name='ZBrush_HP')
    cmds.editDisplayLayerMembers(layer, mesh, noRecurse=True)
    selection = om.MSelectionList()
    selection.add(mesh)
    dag = selection.getDagPath(0)
    dag.extendToShape()
    points = om.MFnMesh(dag).getPoints(om.MSpace.kWorld)
    expected = {(round(p.x, 6), round(p.y, 6), round(p.z, 6)) for p in points}
    before = cmds.xform(mesh, query=True, matrix=True, worldSpace=True)
    with tempfile.TemporaryDirectory(prefix='bg_hp_attributes_test_') as folder:
        binary = os.path.join(folder, 'input.bghp')
        obj = os.path.join(folder, 'output.obj')
        fbx = os.path.join(folder, 'проверка HP.fbx')
        assert bg_hp_binary.write(binary, [mesh], {mesh: 2},
                                  bg_final_export.FinalExportProcessor._is_zbrush_mesh) == 1
        helper = bg_final_export.FinalExportProcessor.external_obj_helper_path()
        subprocess.run([helper, '--input', binary, '--input-binary',
                        '--output', obj], check=True, stdout=subprocess.PIPE)
        with open(obj, encoding='utf-8') as stream:
            lines = stream.readlines()
        actual = {tuple(round(float(x), 6) for x in line.split()[1:])
                  for line in lines if line.startswith('v ')}
        assert actual == expected, (actual, expected)
        assert sum(line.startswith('f ') for line in lines) == 6
        assert sum(line.startswith('vn ') for line in lines) == 24
        assert any(line.strip() == 'usemtl BGTestRed' for line in lines)
        assert not any(line.startswith('vt ') for line in lines)
        vertices = [tuple(map(float, line.split()[1:]))
                    for line in lines if line.startswith('v ')]
        normals = [tuple(map(float, line.split()[1:]))
                   for line in lines if line.startswith('vn ')]
        for line in (line for line in lines if line.startswith('f ')):
            corners = [tuple(map(int, token.split('//'))) for token in line.split()[1:]]
            p, q, r = (vertices[corners[i][0]-1] for i in range(3))
            a = tuple(q[i]-p[i] for i in range(3))
            b = tuple(r[i]-p[i] for i in range(3))
            cross = (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2],
                     a[0]*b[1]-a[1]*b[0])
            normal = normals[corners[0][1]-1]
            assert sum(cross[i] * normal[i] for i in range(3)) > 0
        with open(os.path.join(folder, 'output.mtl'), encoding='utf-8') as stream:
            mtl = stream.read()
        assert 'newmtl BGTestRed' in mtl
        subprocess.run([helper, '--input', binary, '--input-binary',
                        '--output', fbx, '--format', 'fbx'], check=True,
                       stdout=subprocess.PIPE)
        assert os.path.getsize(fbx) > 0
        assert cmds.xform(mesh, query=True, matrix=True, worldSpace=True) == before
        cmds.file(new=True, force=True)
        cmds.file(fbx, i=True, type='FBX', ignoreVersion=True)
        assert cmds.polyEvaluate('Rigid_high', face=True) == 6
        assert cmds.objExists('BGTestRed')
        assert cmds.getAttr('BGTestRed.color')[0][0] > 0.9


def run():
    _test_export_panel()
    _test_external_option_reaches_hp_export()
    _test_world_transform_normals_and_materials()
    cmds.file(new=True, force=True)
    hp_root = cmds.group(empty=True, name="Chapter_HP")
    lp_root = cmds.group(empty=True, name="Chapter_LP")
    hp_groups = []
    for subgroup, translate_x in (("A", -2.0), ("B", 2.0)):
        hp_group = cmds.group(empty=True, name="Chapter_{}_HP".format(subgroup), parent=hp_root)
        lp_group = cmds.group(empty=True, name="Chapter_{}_LP".format(subgroup), parent=lp_root)
        hp = cmds.polyCube(
            name="Chapter_{}_high_001".format(subgroup), constructionHistory=False)[0]
        cmds.xform(hp, translation=(translate_x, 0, 0), worldSpace=True)
        hp = cmds.parent(hp, hp_group, absolute=True)[0]
        lp = cmds.polyCube(
            name="Chapter_{}_low_001".format(subgroup), constructionHistory=False)[0]
        cmds.xform(lp, translation=(translate_x, 0, 0), worldSpace=True)
        cmds.parent(lp, lp_group, absolute=True)
        hp_groups.append(hp)

    hp_faces_before = [cmds.polyEvaluate(mesh, face=True) for mesh in hp_groups]
    snapshot = bg_final_export.FinalExportProcessor.build_chapter_snapshot(
        "Chapter", hp_root, lp_root, {"A": 1, "B": 0})
    output_dir = tempfile.mkdtemp(prefix="bg_external_hp_test_")
    try:
        standard = bg_final_export.FinalExportProcessor.export_chapter(
            "Chapter", hp_root, lp_root, [], mode="hp", export_dir=output_dir,
            smooth_states={"A": 1, "B": 0}, prepared_chapters=[snapshot],
            status_callback=lambda _label: None)
        assert standard == "Chapter_HP"
        assert os.path.isfile(os.path.join(output_dir, "Chapter_HP.fbx"))
        assert [cmds.polyEvaluate(mesh, face=True) for mesh in hp_groups] == hp_faces_before

        old_preparation = bg_final_export.FinalExportProcessor._make_zero_transform_hp_export_copies
        def reject_fbx_fallback(*args, **kwargs):
            if kwargs.get('apply_smoothing') is False:
                raise AssertionError('Direct export unexpectedly used the FBX compatibility path')
            return old_preparation(*args, **kwargs)
        bg_final_export.FinalExportProcessor._make_zero_transform_hp_export_copies = staticmethod(reject_fbx_fallback)
        try:
            exported = bg_final_export.FinalExportProcessor.export_chapter(
            "Chapter", hp_root, lp_root, [], mode="hp", export_dir=output_dir,
            smooth_states={"A": 1, "B": 0}, prepared_chapters=[snapshot],
            status_callback=lambda _label: None, external_hp_obj=True)
        finally:
            bg_final_export.FinalExportProcessor._make_zero_transform_hp_export_copies = old_preparation
        fbx_path = os.path.join(output_dir, "Chapter_HP.fbx")
        assert exported == "Chapter_HP", exported
        assert os.path.isfile(fbx_path) and os.path.getsize(fbx_path) > 0
        import shutil
        direct_fbx = os.path.join(output_dir, "direct_HP.fbx")
        shutil.copyfile(fbx_path, direct_fbx)
        a_name = "Chapter_A_high_001"
        b_name = "Chapter_B_high_001"
        assert [cmds.polyEvaluate(mesh, face=True) for mesh in hp_groups] == hp_faces_before
        assert not cmds.ls("BG_HP_Export_Zero_Temp*", type="transform")
        import bg_hp_binary
        original_write = bg_hp_binary.write
        def unavailable(*_args, **_kwargs):
            raise RuntimeError('intentional direct-path failure')
        bg_hp_binary.write = unavailable
        try:
            fallback = bg_final_export.FinalExportProcessor.export_chapter(
                "Chapter", hp_root, lp_root, [], mode="hp", export_dir=output_dir,
                smooth_states={"A": 1, "B": 0}, prepared_chapters=[snapshot],
                status_callback=lambda _label: None, external_hp_obj=True)
        finally:
            bg_hp_binary.write = original_write
        assert fallback == "Chapter_HP", fallback
        assert os.path.isfile(fbx_path) and os.path.getsize(fbx_path) > 0
        assert not cmds.ls("BG_HP_Export_Zero_Temp*", type="transform")
        cmds.file(new=True, force=True)
        cmds.file(direct_fbx, i=True, type='FBX', ignoreVersion=True)
        assert cmds.polyEvaluate(a_name, face=True) > hp_faces_before[0]
        assert cmds.polyEvaluate(b_name, face=True) == hp_faces_before[1]
        cmds.file(new=True, force=True)
        cmds.file(fbx_path, i=True, type='FBX', ignoreVersion=True)
        assert cmds.polyEvaluate(a_name, face=True) > hp_faces_before[0]
        assert cmds.polyEvaluate(b_name, face=True) == hp_faces_before[1]
        print("External HP Maya export test: OK (per-subgroup Smooth; sources unchanged).")
    finally:
        bg_final_export.FinalExportProcessor._cleanup_stale_export_temps()
        if os.path.isdir(output_dir):
            import shutil
            shutil.rmtree(output_dir, ignore_errors=True)


if __name__ == "__main__":
    try:
        run()
    finally:
        maya.standalone.uninitialize()
