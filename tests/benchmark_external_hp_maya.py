"""Compare the direct and compatibility HP export paths on an existing package.

Run under mayapy with the package directory and freshly built helper EXE.
No package files or source geometry are modified.
"""
import collections
import json
import os
import subprocess
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "Bake_Groups"))
import bg_final_export
import bg_hp_binary


def _faces(path):
    count = 0
    with open(path, "rb") as stream:
        for line in stream:
            count += line.startswith(b"f ")
    return count


def run(package, helper, level_override=None):
    with open(os.path.join(package, "BakeGroups_Marmoset.json"), encoding="utf-8") as stream:
        manifest = json.load(stream)
    records = {item['export_name']: item for chapter in manifest['chapters']
               for item in chapter['meshes']}
    cmds.loadPlugin('fbxmaya')
    cmds.file(new=True, force=True)
    for chapter in manifest['chapters']:
        cmds.file(os.path.join(package, chapter['model']), i=True, type='FBX',
                  ignoreVersion=True, executeScriptNodes=False)
    hp = []
    zbrush = []
    levels = {}
    for node in cmds.ls(type='transform', long=True):
        record = records.get(node.split('|')[-1])
        if not record or record['role'] != 'high':
            continue
        hp.append(node)
        levels[node] = 0 if record.get('zbrush') else (
            int(level_override) if level_override is not None
            else int(record['subdivision_level']))
        if record.get('zbrush'):
            zbrush.append(node)
    if zbrush:
        layer = cmds.createDisplayLayer(empty=True, name='ZBrush_HP')
        cmds.editDisplayLayerMembers(layer, zbrush, noRecurse=True)
    print("Loaded {} HP, {} ZBrush; levels {}".format(
        len(hp), len(zbrush), dict(collections.Counter(levels.values()))), flush=True)
    processor = bg_final_export.FinalExportProcessor
    with tempfile.TemporaryDirectory(prefix="bg_hp_direct_benchmark_") as temp_dir:
        direct_input = os.path.join(temp_dir, "direct.bghp")
        direct_obj = os.path.join(temp_dir, "direct.obj")
        direct_fbx = os.path.join(temp_dir, "direct.fbx")
        compat_input = os.path.join(temp_dir, "compat.fbx")
        compat_obj = os.path.join(temp_dir, "compat.obj")
        t0 = time.perf_counter()
        bg_hp_binary.write(direct_input, hp, levels, processor._is_zbrush_mesh)
        t1 = time.perf_counter()
        processor._run_external_obj_export(
            helper, direct_input, direct_obj, None, binary_input=True)
        t2 = time.perf_counter()
        processor._run_external_obj_export(
            helper, direct_input, direct_fbx, None, binary_input=True,
            output_format='fbx')
        t_fbx = time.perf_counter()
        print("Direct transfer {:.3f}s, OBJ helper {:.3f}s, FBX helper {:.3f}s; "
              "OBJ {:.1f} MB, FBX {:.1f} MB".format(
                  t1-t0, t2-t1, t_fbx-t2,
                  os.path.getsize(direct_obj)/1e6, os.path.getsize(direct_fbx)/1e6), flush=True)
        mapping = {}
        compat = {}
        root = None
        try:
            copies, root = processor._make_zero_transform_hp_export_copies(
                hp, levels, combine_non_zbrush=True, apply_smoothing=False,
                external_level_map=mapping)
            t3 = time.perf_counter()
            assert processor._export_with_lp_triangulation_rollback(
                copies, compat_input, external_level_map=mapping)
            t4 = time.perf_counter()
            processor._run_external_obj_export(helper, compat_input, compat_obj, mapping)
            t5 = time.perf_counter()
            compat_faces = _faces(compat_obj)
            compat = {'maya_copies': round(t3-t_fbx, 3), 'fbx': round(t4-t3, 3),
                      'helper': round(t5-t4, 3), 'total': round(t5-t_fbx, 3),
                      'fbx_bytes': os.path.getsize(compat_input), 'faces': compat_faces}
        except Exception as exc:
            compat = {'error': str(exc)}
            if os.path.isfile(compat_input):
                probe_obj = os.path.join(temp_dir, 'probe.obj')
                probe = subprocess.run([helper, '--input', compat_input,
                                        '--output', probe_obj, '--level', '0'],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                if probe.returncode == 0:
                    names = []
                    with open(probe_obj, 'rb') as stream:
                        for line in stream:
                            if line.startswith(b'o '):
                                names.append(line[2:].decode('utf-8', errors='replace').strip())
                    missing = [name for name in mapping if name not in names]
                    compat['missing_names'] = missing[:5]
                    compat['fbx_names'] = names[:10]
        finally:
            processor._delete_temp_nodes([root])
        direct_faces = _faces(direct_obj)
        if 'faces' in compat:
            assert direct_faces == compat['faces'], (direct_faces, compat['faces'])
        cmds.file(new=True, force=True)
        cmds.file(direct_fbx, i=True, type='FBX', ignoreVersion=True,
                  executeScriptNodes=False)
        imported_faces = sum(cmds.polyEvaluate(shape, face=True)
                             for shape in cmds.ls(type='mesh', long=True))
        assert imported_faces == direct_faces, (imported_faces, direct_faces)
        print(json.dumps({
            'hp': len(hp), 'faces': direct_faces, 'imported_faces': imported_faces,
            'direct': {'transfer': round(t1-t0, 3), 'obj_helper': round(t2-t1, 3),
                       'fbx_helper': round(t_fbx-t2, 3),
                       'fbx_total': round(t_fbx-t2+t1-t0, 3),
                       'obj_bytes': os.path.getsize(direct_obj),
                       'fbx_bytes': os.path.getsize(direct_fbx),
                       'transfer_bytes': os.path.getsize(direct_input)},
            'compatibility': compat
        }, indent=2), flush=True)


if __name__ == '__main__':
    try:
        run(os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2]),
            sys.argv[3] if len(sys.argv) > 3 else None)
    finally:
        maya.standalone.uninitialize()
