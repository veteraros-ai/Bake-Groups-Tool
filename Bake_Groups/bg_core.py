# -*- coding: utf-8 -*-
from __future__ import print_function, division, absolute_import

import maya.cmds as cmds
import maya.api.OpenMaya as om
import json
import re
import os
import uuid
import math
import io
from contextlib import contextmanager

# ==========================================
# CONSTANTS & CONFIGURATION
# ==========================================
class BakeConfig(object):
    KEY = "BakeGroupManagerData_v6"
    SUFFIX_HP = "_HP"
    SUFFIX_LP = "_LP"
    SUFFIX_BAKE = "_bake"
    WORKSPACE_NAME = "BakeManagerUIWorkspaceControl"
    ATTR_BAKE_GROUP = "BakeManagerGroup"
    
    # HERE ARE THE LOST STYLES, restored:
    STYLE_MAIN = """
        QWidget { background-color: #2D2D30; color: #E0E0E0; font-family: Arial, sans-serif; font-size: 11px; }
        QPushButton { background: #3D3D40; border: 1px solid #444; border-radius: 3px; padding: 5px; min-height: 20px;}
        QPushButton:hover { background: #4A4A4F; border: 1px solid #555; }
        QPushButton:pressed { background: #2A2A2D; }
        QLineEdit { background: #1E1E1E; border: 1px solid #444; border-radius: 3px; padding: 4px; color: #75cce8;}
        QLabel { color: #CCCCCC; background: transparent; border: none; }
        QScrollArea { background: #252526; border: 1px solid #444; border-radius: 3px; }
        QSplitter::handle { background-color: #444; width: 2px; }
        QCheckBox { spacing: 5px; }
        QCheckBox::indicator { width: 13px; height: 13px; background-color: #252526; border: 1px solid #444; }
        QCheckBox::indicator:checked { background-color: #55ddff; border: 1px solid #55ddff; }
        QDoubleSpinBox, QSpinBox { background: #1E1E1E; border: 1px solid #444; border-radius: 3px; padding: 4px; color: #E0E0E0; }
        QTextEdit { background: #1E1E1E; border: 1px solid #444; border-radius: 3px; }
        QGroupBox { border: 1px solid #444; margin-top: 15px; font-weight: bold; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 3px; color: #aaa; }
        
        QListWidget, QTreeWidget { background-color: #1E1E1E; border: 1px solid #444; border-radius: 3px; padding: 4px; outline: none; }
        QListWidget::item, QTreeWidget::item { padding: 4px; border-bottom: 1px solid #333; }
        QListWidget::item:hover, QTreeWidget::item:hover { background-color: #3A3A3A; }
        QListWidget::item:selected, QTreeWidget::item:selected { background-color: #d18c15; color: black; font-weight: bold; }
    """
    STYLE_FRAME = "background-color: #2D2D30; border: 1px solid #444; border-radius: 2px;"
    STYLE_BTN_VIS_ON = "background-color: #395373; border: 1px solid #111; font-weight: bold;"
    STYLE_BTN_VIS_OFF = "background-color: #8c6239; border: 1px solid #111; font-weight: bold;"
    STYLE_SUBGROUP_ACTIVE = "background-color: #3b5998; font-weight: bold; text-align: left; padding-left: 5px; border-radius: 2px;"
    STYLE_SUBGROUP_NORMAL = "background-color: transparent; border: none; text-align: left; padding-left: 5px;"
    STYLE_CONTEXT_MENU = """
        QMenu {
            background-color: #2D2D30;
            border: 1px solid #444444;
            padding: 5px;
        }
        QMenu::item {
            background-color: transparent;
            padding: 6px 20px 6px 20px;
            color: #E0E0E0;
        }
        QMenu::item:selected {
            background-color: #4F4F54;
            color: #FFFFFF;
            border-radius: 2px;
        }
        QMenu::separator {
            height: 1px;
            background: #444444;
            margin: 4px 0px;
        }
    """

    @staticmethod
    def trim_name(base_part, suffix_part, max_length=60):
        if len(base_part) + len(suffix_part) <= max_length:
            return base_part + suffix_part
        allowed_base_len = max_length - len(suffix_part)
        if allowed_base_len > 0:
            return base_part[:allowed_base_len] + suffix_part
        else:
            return suffix_part[-max_length:]

# ==========================================
# UNDO CONTEXT MANAGER
# ==========================================
@contextmanager
def undo_chunk(chunk_name="BakeManagerAction"):
    cmds.undoInfo(openChunk=True, chunkName=chunk_name)
    try:
        yield
    except Exception as e:
        cmds.warning("Action failed: {}".format(str(e)))
        raise e
    finally:
        cmds.undoInfo(closeChunk=True)

# ==========================================
# DATA MODEL (SESSION MANAGER)
# ==========================================
class BakeSessionModel(object):
    @staticmethod
    def get_json_path():
        scene_name = cmds.file(q=True, sceneName=True)
        if not scene_name: 
            return None
        base_path, _ = os.path.splitext(scene_name)
        return base_path + "_BakeGroups.json"

    @classmethod
    def save(cls, data):
        data_str = json.dumps(data)
        cmds.fileInfo(BakeConfig.KEY, data_str)
        json_path = cls.get_json_path()
        if json_path:
            try:
                with io.open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=4, ensure_ascii=False)
            except Exception as e: 
                cmds.warning("Failed to save JSON to disk: {}".format(e))

    @classmethod
    def load(cls):
        data = []
        json_path = cls.get_json_path()
        if json_path and os.path.exists(json_path):
            try:
                with io.open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except IOError as e:
                cmds.warning("Could not read JSON file: {}".format(e))
            except ValueError as e:
                cmds.warning("JSON file is corrupted: {}".format(e))

        if not data:
            info = cmds.fileInfo(BakeConfig.KEY, query=True) or []
            if not info: info = cmds.fileInfo("BakeGroupManagerData_v5", query=True) or []
            if info:
                try: data = json.loads(info[0])
                except: pass
                    
        seen_ids = set()
        for p in data:
            if 'id' not in p or p['id'] in seen_ids:
                p['id'] = str(uuid.uuid4())
            if 'locked' not in p:
                p['locked'] = []
            if 'final_smooth_states' not in p or not isinstance(p.get('final_smooth_states'), dict):
                p['final_smooth_states'] = {}
            seen_ids.add(p['id'])
            
        return data

# ==========================================
# MATH & DATA EXTRACTION (Original logic restored)
# ==========================================
class MeshDataManager(object):
    @staticmethod
    def get_mesh_data(mesh_name):
        if not cmds.objExists(mesh_name): return None
        try:
            shapes = cmds.listRelatives(mesh_name, shapes=True, type="mesh", noIntermediate=True, fullPath=True)
            if not shapes: return None
                
            selListShape = om.MSelectionList(); selListShape.add(shapes[0])
            shapeDagPath = selListShape.getDagPath(0)
            fnMesh = om.MFnMesh(shapeDagPath)
            vtx_count = fnMesh.numVertices
            edge_count = fnMesh.numEdges
            uv_count = 0
            uv_shell_count = 0
            uv_signature = "empty"
            try:
                u_values, v_values = fnMesh.getUVs()
                uv_count = len(u_values)
                if uv_count:
                    u_min = min([float(u) for u in u_values])
                    u_max = max([float(u) for u in u_values])
                    v_min = min([float(v) for v in v_values])
                    v_max = max([float(v) for v in v_values])
                    try:
                        _uv_shell_ids, uv_shell_count = fnMesh.getUvShellsIds()
                    except Exception:
                        uv_shell_count = 0
                    uv_signature = "{}:{}:{:.4f}:{:.4f}".format(
                        uv_count,
                        uv_shell_count,
                        max(u_max - u_min, 0.0),
                        max(v_max - v_min, 0.0)
                    )
            except Exception:
                pass
            
            selListTransform = om.MSelectionList(); selListTransform.add(mesh_name)
            dagPath = selListTransform.getDagPath(0)
            
            fnDag = om.MFnDagNode(dagPath)
            bbox = fnDag.boundingBox
            bbox.transformUsing(dagPath.inclusiveMatrix())
            
            p_min = bbox.min; p_max = bbox.max; center = bbox.center
            display_layers = cmds.listConnections(mesh_name, type="displayLayer") or []
            is_zbrush = any("zbrush" in layer.lower() for layer in display_layers)

            vol = (p_max.x - p_min.x) * (p_max.y - p_min.y) * (p_max.z - p_min.z)
            diag = ((p_max.x - p_min.x)**2 + (p_max.y - p_min.y)**2 + (p_max.z - p_min.z)**2)**0.5
            
            return {
                "name": mesh_name, "vtx": vtx_count, "edges": edge_count,
                "min": (p_min.x, p_min.y, p_min.z), "max": (p_max.x, p_max.y, p_max.z),
                "center": (center.x, center.y, center.z),
                "volume": vol, "diag": diag, "is_zbrush": is_zbrush,
                "uv_count": uv_count, "uv_shell_count": uv_shell_count,
                "uv_signature": uv_signature
            }
        except: return None

    @staticmethod
    def get_bbox_data_gt(dag_path_str):
        try:
            selList = om.MSelectionList()
            selList.add(dag_path_str)
            dagPath = selList.getDagPath(0)
            if dagPath.hasFn(om.MFn.kTransform):
                try: dagPath.extendToShape()
                except RuntimeError: pass
            fnDag = om.MFnDagNode(dagPath)
            bbox = fnDag.boundingBox
            bbox.transformUsing(dagPath.inclusiveMatrix())
            pmin = bbox.min; pmax = bbox.max
            return {
                'min': [pmin.x, pmin.y, pmin.z],
                'max': [pmax.x, pmax.y, pmax.z],
                'center': [(pmin.x + pmax.x) * 0.5, (pmin.y + pmax.y) * 0.5, (pmin.z + pmax.z) * 0.5],
                'size': [pmax.x - pmin.x, pmax.y - pmin.y, pmax.z - pmin.z],
                'node': dag_path_str
            }
        except Exception: return None

    @staticmethod
    def combine_bboxes(bbox_list):
        if not bbox_list: return None
        c_min = [min(b['min'][i] for b in bbox_list) for i in range(3)]
        c_max = [max(b['max'][i] for b in bbox_list) for i in range(3)]
        c_size = [c_max[i] - c_min[i] for i in range(3)]
        return {'min': c_min, 'max': c_max, 'size': c_size}

    @staticmethod
    def get_meshes_in_group(group_node):
        if not group_node or not cmds.objExists(group_node): return []
        all_desc = cmds.listRelatives(group_node, allDescendents=True, type='transform', fullPath=True) or []
        return [t for t in all_desc if cmds.listRelatives(t, shapes=True, type='mesh')]

class MathUtils(object):
    @staticmethod
    def distance(p1, p2):
        return ((p1[0]-p2[0])**2 + (p1[1]-p2[1])**2 + (p1[2]-p2[2])**2) ** 0.5

    @staticmethod
    def is_overlapping(m1, m2, padding=1.02):
        dist = MathUtils.distance(m1["center"], m2["center"])
        rad_sum = (m1["diag"] + m2["diag"]) * 0.5 * padding
        if dist > rad_sum: return False

        def expand_bbox(b, p):
            cx, cy, cz = b['center']
            hx, hy, hz = (b['max'][0]-cx)*p, (b['max'][1]-cy)*p, (b['max'][2]-cz)*p
            return (cx-hx, cy-hy, cz-hz), (cx+hx, cy+hy, cz+hz)

        min1, max1 = expand_bbox(m1, padding)
        min2, max2 = expand_bbox(m2, padding)

        dx = max(0.0, min(max1[0], max2[0]) - max(min1[0], min2[0]))
        dy = max(0.0, min(max1[1], max2[1]) - max(min1[1], min2[1]))
        dz = max(0.0, min(max1[2], max2[2]) - max(min1[2], min2[2]))
        return (dx * dy * dz) > 0

class StatsUtils(object):
    @staticmethod
    def mean(data):
        return sum(data) / float(len(data)) if data else 0.0

    @staticmethod
    def median(data):
        if not data: return 0.0
        s_data = sorted(data)
        n = len(s_data)
        m = n // 2
        if n % 2 == 0: return (s_data[m - 1] + s_data[m]) / 2.0
        else: return float(s_data[m])

class GeoMatcher(object):
    @staticmethod
    def get_world_vertices(mesh_path, density_pct=10.0, is_large=False):
        try:
            sel = om.MSelectionList()
            sel.add(mesh_path)
            dag_path = sel.getDagPath(0)
            try: dag_path.extendToShape()
            except Exception: return []
                
            fn_mesh = om.MFnMesh(dag_path)
            # Returns MPointArray, which is bound to the C++ fn_mesh object
            all_points = fn_mesh.getPoints(om.MSpace.kWorld) 
            total_verts = len(all_points)
            
            if total_verts == 0: return []
            
            flat_verts = []
            
            if total_verts <= 12 or density_pct >= 100.0:
                # Extract data directly into a flat list of floats
                for i in range(total_verts):
                    p = all_points[i]
                    flat_verts.extend([float(p.x), float(p.y), float(p.z)])
                return flat_verts
                
            percent = density_pct / 100.0
            if not is_large: target_count = int(12 + (total_verts - 12) * percent)
            else: target_count = int(percent * 2 * (total_verts - 12))
                
            target_count = max(12, min(target_count, total_verts))
            step = max(1, total_verts // target_count)
            
            # Extract data directly into a flat list of floats
            count = 0
            for i in range(0, total_verts, step):
                if count >= target_count:
                    break
                p = all_points[i]
                flat_verts.extend([float(p.x), float(p.y), float(p.z)])
                count += 1
                
            return flat_verts
        except Exception: return []

    @staticmethod
    def build_group_intersectors(hp_paths):
        intersectors = {}
        for path in hp_paths:
            try:
                sel = om.MSelectionList(); sel.add(path)
                dag_path = sel.getDagPath(0)
                try: dag_path.extendToShape()
                except: continue
                if not dag_path.hasFn(om.MFn.kMesh): continue
                intersector = om.MMeshIntersector()
                intersector.create(dag_path.node(), dag_path.inclusiveMatrix())
                intersectors[path] = intersector
            except: continue
        return intersectors

    @staticmethod
    def calculate_average_distance(lp_vertices, hp_intersectors, hp_mesh_paths=None):
        if not hp_intersectors or not lp_vertices: return float('inf')
        total_dist = 0.0; count = 0
        for vtx in lp_vertices:
            min_dist = float('inf'); found = False
            for intersector in hp_intersectors:
                try:
                    point_info = intersector.getClosestPoint(vtx)
                    dist = vtx.distanceTo(om.MPoint(point_info.point))
                    if dist < min_dist: min_dist = dist; found = True
                except: continue
            if not found and hp_mesh_paths:
                for hp_path in hp_mesh_paths:
                    try:
                        sel = om.MSelectionList(); sel.add(hp_path)
                        dag_path = sel.getDagPath(0); dag_path.extendToShape()
                        fnMesh = om.MFnMesh(dag_path)
                        closest_pt, _ = fnMesh.getClosestPoint(vtx, om.MSpace.kWorld)
                        dist = vtx.distanceTo(closest_pt)
                        if dist < min_dist: min_dist = dist; found = True
                    except: continue
            if found: total_dist += min_dist; count += 1
        return (total_dist / count) if count > 0 else float('inf')

class MayaCore(object):
    def __init__(self): self._node_cache = {}

    def get_node_from_cache_or_uuid(self, uuid_str):
        if not uuid_str: return None
        if self._node_cache.get(uuid_str) and cmds.objExists(self._node_cache[uuid_str]): return self._node_cache[uuid_str]
        nodes = cmds.ls(uuid_str, long=True) or []
        if nodes:
            self._node_cache[uuid_str] = nodes[0]; return nodes[0]
        return None

    def resolve_main_nodes(self, pair):
        hp_node = self.get_node_from_cache_or_uuid(pair.get('hp_uuid'))
        lp_node = self.get_node_from_cache_or_uuid(pair.get('lp_uuid'))
        base = pair.get('base', '')
        def find_n(suffix):
            f = cmds.ls("*{}*{}".format(base, suffix), type='transform', long=True) or []
            for n in f:
                if n.split('|')[-1] == "{}{}".format(base, suffix): return n
            return f[0] if f else None
        if not hp_node and base: hp_node = find_n(BakeConfig.SUFFIX_HP)
        if not lp_node and base: lp_node = find_n(BakeConfig.SUFFIX_LP)
        needs_save = False
        for node, key in [(hp_node, 'hp_uuid'), (lp_node, 'lp_uuid')]:
            if node and cmds.objExists(node):
                n_uuid = cmds.ls(node, uuid=True)[0]
                self._node_cache[n_uuid] = node
                if n_uuid != pair.get(key): pair[key] = n_uuid; needs_save = True
        return hp_node, lp_node, needs_save

    @staticmethod
    def is_descendant_of(child, parent):
        if not child or not parent or not cmds.objExists(child) or not cmds.objExists(parent): return False
        return (cmds.ls(child, long=True) or [None])[0] in (cmds.listRelatives(parent, allDescendents=True, fullPath=True) or [])

    @staticmethod
    def get_model_panel():
        p = cmds.getPanel(withFocus=True)
        if p and cmds.getPanel(typeOf=p) == 'modelPanel': return p
        for op in (cmds.getPanel(visiblePanels=True) or []):
            if cmds.getPanel(typeOf=op) == 'modelPanel': return op
        return None

    def isolate_main_groups(self, groups, state):
        panel = self.get_model_panel()
        if not panel: return
        if state:
            vg = [g for g in groups if g and cmds.objExists(g)]
            if not vg: return
            sel = cmds.ls(selection=True) or []
            cmds.isolateSelect(panel, state=1)
            cmds.select(clear=True)
            cmds.isolateSelect(panel, loadSelected=True)
            for grp in vg: 
                cmds.isolateSelect(panel, addDagObject=grp)
            if sel: cmds.select(sel, replace=True)
            cmds.isolateSelect(panel, update=True)
        else:
            cmds.isolateSelect(panel, state=0)

    def find_similar_meshes_fast(self, targets, search_root):
        if not targets: return []
        all_tr = cmds.listRelatives(search_root, allDescendents=True, type='transform', fullPath=True) or []
        all_meshes = [x for x in all_tr if cmds.listRelatives(x, shapes=True, type='mesh') and x not in targets]

        if len(targets) == 1:
            target = targets[0]
            vtx = cmds.polyEvaluate(target, vertex=True)
            bb = cmds.exactWorldBoundingBox(target)
            t_diag = math.sqrt((bb[3]-bb[0])**2 + (bb[4]-bb[1])**2 + (bb[5]-bb[2])**2)
            similar = list(targets)
            for m in all_meshes:
                if cmds.polyEvaluate(m, vertex=True) == vtx:
                    m_bb = cmds.exactWorldBoundingBox(m)
                    m_diag = math.sqrt((m_bb[3]-m_bb[0])**2 + (m_bb[4]-m_bb[1])**2 + (m_bb[5]-m_bb[2])**2)
                    if (t_diag > 0 and m_diag > 0 and max(t_diag/m_diag, m_diag/t_diag) <= 3.0) or (t_diag == 0 and m_diag == 0):
                        similar.append(m)
            return similar
        else:
            similar = list(targets)
            t_info = []
            for t in targets:
                bb = cmds.exactWorldBoundingBox(t)
                center = [(bb[0]+bb[3])/2.0, (bb[1]+bb[4])/2.0, (bb[2]+bb[5])/2.0]
                t_info.append({'node': t, 'vtx': cmds.polyEvaluate(t, vertex=True), 'center': center})
            
            anchor = t_info[0]
            m_info_by_vtx = {}
            for m in all_meshes:
                vtx = cmds.polyEvaluate(m, vertex=True)
                bb = cmds.exactWorldBoundingBox(m)
                m_info_by_vtx.setdefault(vtx, []).append({
                    'node': m, 
                    'center': [(bb[0]+bb[3])/2.0, (bb[1]+bb[4])/2.0, (bb[2]+bb[5])/2.0]
                })
            
            anchor_candidates = m_info_by_vtx.get(anchor['vtx'], [])
            for ac in anchor_candidates:
                match_group = [ac['node']]
                delta = [ac['center'][i] - anchor['center'][i] for i in range(3)]
                is_full_match = True
                for i in range(1, len(t_info)):
                    t_part = t_info[i]
                    expected_center = [t_part['center'][j] + delta[j] for j in range(3)]
                    found_part = False
                    for pc in m_info_by_vtx.get(t_part['vtx'], []):
                        if pc['node'] in match_group: continue
                        dist = math.sqrt(sum((pc['center'][k] - expected_center[k])**2 for k in range(3)))
                        if dist < 0.1:  
                            match_group.append(pc['node'])
                            found_part = True
                            break
                    if not found_part:
                        is_full_match = False
                        break
                if is_full_match:
                    similar.extend(match_group)
            return similar

    def get_combine_tasks_count(self, source_root):
        """Fast calculation of group count for the progress bar scale."""
        if not source_root or not cmds.objExists(source_root): return 0
        count = 0
        for child in cmds.listRelatives(source_root, children=True, type='transform', fullPath=True) or []:
            if not cmds.listRelatives(child, shapes=True): 
                meshes = [m for m in (cmds.listRelatives(child, allDescendents=True, type='transform', fullPath=True) or []) if cmds.listRelatives(m, shapes=True, type='mesh')]
                if meshes: count += 1
        return count

    def combine_subgroups(self, source_root, is_lp=False, progress_cb=None):
        """Optimized process for HP (renaming) and LP (combine) in the root folder."""
        if not source_root or not cmds.objExists(source_root): return
        
        tasks = []
        for child in cmds.listRelatives(source_root, children=True, type='transform', fullPath=True) or []:
            # Look only for groups (without direct shapes)
            if not cmds.listRelatives(child, shapes=True): 
                meshes = [m for m in (cmds.listRelatives(child, allDescendents=True, type='transform', fullPath=True) or []) if cmds.listRelatives(m, shapes=True, type='mesh')]
                if meshes: tasks.append({'name': child.split('|')[-1], 'meshes': meshes, 'group_node': child})
        
        for task in tasks:
            # Clean the name from old suffixes and ensure dots become underscores
            subgroup_clean = task['name'].replace(BakeConfig.SUFFIX_HP, '').replace(BakeConfig.SUFFIX_LP, '')
            subgroup_clean = re.sub(r'(?i)[_-]?(high|low)$', '', subgroup_clean).strip('_').replace('.', '_')

            if progress_cb:
                if progress_cb("Processing: {}".format(subgroup_clean)): return
            
            # Duplicate meshes
            dupes = cmds.duplicate(task['meshes'], returnRootsOnly=True)
            
            if is_lp:
                self._process_lp_combine(dupes, source_root, subgroup_clean)
            else:
                # HP Logic: don't combine, move to root and number
                for i, d in enumerate(dupes):
                    moved = cmds.parent(d, source_root, absolute=True)[0]
                    new_name = "{}_high_{:03d}".format(subgroup_clean, i + 1)
                    cmds.rename(moved, new_name)
                    
            # Hide the original subgroup folder
            if cmds.objExists(task['group_node']):
                cmds.setAttr(task['group_node'] + ".visibility", False)
            
            cmds.select(clear=True)

    def _process_lp_combine(self, dupes, target_root, subgroup_clean):
        """Helper method for LP: combine or process multi-materials."""
        bake_name = "{}_low".format(subgroup_clean).replace('.', '_')
        cmds.refresh(suspend=True)
        try:
            # Check for multi-materials
            shading_engines = set()
            for m in dupes:
                history = cmds.listHistory(m, f=1, pdo=1) or []
                ses = cmds.ls(history, type='shadingEngine') or []
                shading_engines.update(ses)
            
            if len(shading_engines) > 1:
                # Multi-material: simply extract, Maya will automatically add numbers
                for d in dupes:
                    moved = cmds.parent(d, target_root, absolute=True)[0]
                    cmds.rename(moved, bake_name)
            else:
                # Single material: combine
                if len(dupes) > 1:
                    cmds.select(dupes, replace=True)
                    combined_mesh = cmds.polyUnite(ch=False, mergeUVSets=1)[0]
                    combined_mesh = cmds.parent(combined_mesh, target_root, absolute=True)[0]
                    cmds.delete(combined_mesh, constructionHistory=True)
                    cmds.xform(combined_mesh, cp=True)
                    cmds.rename(combined_mesh, bake_name)
                elif len(dupes) == 1:
                    moved_mesh = cmds.parent(dupes[0], target_root, absolute=True)[0]
                    cmds.rename(moved_mesh, bake_name)
        finally:
            cmds.refresh(suspend=False)
            cmds.select(clear=True)
