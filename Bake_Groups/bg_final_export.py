# -*- coding: utf-8 -*-
from __future__ import print_function, division, absolute_import

import maya.cmds as cmds
import maya.mel as mel
import bg_core
import bg_localization as bg_l10n
import re
import math

try:
    from PySide6 import QtWidgets, QtCore
except ImportError:
    from PySide2 import QtWidgets, QtCore

class FinalExportProcessor(object):
    @staticmethod
    def _query_fbx_bool(command):
        try:
            return bool(mel.eval("{} -q;".format(command)))
        except Exception:
            return None

    @staticmethod
    def _set_fbx_bool(command, value):
        try:
            mel.eval("{} -v {};".format(command, "true" if value else "false"))
        except Exception:
            pass

    @staticmethod
    def export_selected_fbx(export_path):
        if not cmds.pluginInfo('fbxmaya', query=True, loaded=True):
            cmds.loadPlugin('fbxmaya')

        previous_input_connections = FinalExportProcessor._query_fbx_bool("FBXExportInputConnections")
        previous_generate_log = FinalExportProcessor._query_fbx_bool("FBXExportGenerateLog")
        previous_smooth_mesh = FinalExportProcessor._query_fbx_bool("FBXExportSmoothMesh")

        FinalExportProcessor._set_fbx_bool("FBXExportGenerateLog", False)
        FinalExportProcessor._set_fbx_bool("FBXExportInputConnections", False)
        FinalExportProcessor._set_fbx_bool("FBXExportSmoothMesh", True)
        try:
            cmds.file(export_path, force=True, type="FBX export", exportSelected=True)
        finally:
            if previous_input_connections is not None:
                FinalExportProcessor._set_fbx_bool("FBXExportInputConnections", previous_input_connections)
            if previous_generate_log is not None:
                FinalExportProcessor._set_fbx_bool("FBXExportGenerateLog", previous_generate_log)
            if previous_smooth_mesh is not None:
                FinalExportProcessor._set_fbx_bool("FBXExportSmoothMesh", previous_smooth_mesh)

    @staticmethod
    def _is_zbrush_mesh(mesh_transform):
        if not mesh_transform or not cmds.objExists(mesh_transform):
            return False

        shapes = cmds.listRelatives(mesh_transform, shapes=True, fullPath=True) or []

        layers = cmds.listConnections(mesh_transform, type="displayLayer") or []
        for shape in shapes:
            layers.extend(cmds.listConnections(shape, type="displayLayer") or [])

        return any(layer and "zbrush" in layer.lower() for layer in layers)

    @staticmethod
    def _smooth_level_from_item(item):
        if 'smooth_level' in item:
            try:
                return int(item.get('smooth_level') or 0)
            except Exception:
                return 0
        combo = item.get('combo')
        if combo:
            return combo.currentIndex()
        return 0

    @staticmethod
    def _smooth_level_from_states(smooth_states, base_name, prefix, default_level=2):
        if not smooth_states:
            return default_level

        keys = [prefix]
        base_prefix = base_name + "_"
        if prefix.startswith(base_prefix):
            keys.append(prefix[len(base_prefix):])

        lower_map = {}
        for key, value in smooth_states.items():
            lower_map[str(key).lower()] = value

        for key in keys:
            value = lower_map.get(str(key).lower())
            if value is not None:
                try:
                    return int(value)
                except Exception:
                    return default_level
        return default_level

    @staticmethod
    def _valid_mesh_shapes(mesh_transform):
        if not mesh_transform or not cmds.objExists(mesh_transform):
            return []
        shapes = cmds.listRelatives(mesh_transform, shapes=True, fullPath=True, type='mesh') or []
        return [shape for shape in shapes if not cmds.getAttr(shape + ".intermediateObject")]

    @staticmethod
    def _capture_smooth_preview_state(shape, state):
        if shape in state:
            return

        attrs = {}
        for attr in ("displaySmoothMesh", "smoothLevel", "useSmoothPreviewForRender", "renderSmoothLevel"):
            plug = "{}.{}".format(shape, attr)
            if cmds.objExists(plug):
                try:
                    attrs[attr] = cmds.getAttr(plug)
                except Exception:
                    pass
        state[shape] = attrs

    @staticmethod
    def _apply_export_smooth_preview(meshes, level, state):
        try:
            level = max(0, int(level or 0))
        except Exception:
            level = 0

        for mesh in meshes:
            if not mesh or not cmds.objExists(mesh):
                continue

            is_zbrush_mesh = FinalExportProcessor._is_zbrush_mesh(mesh)

            for shape in FinalExportProcessor._valid_mesh_shapes(mesh):
                FinalExportProcessor._capture_smooth_preview_state(shape, state)
                try:
                    if is_zbrush_mesh or level == 0:
                        if cmds.objExists(shape + ".displaySmoothMesh"):
                            cmds.setAttr(shape + ".displaySmoothMesh", 0)
                        continue

                    if cmds.objExists(shape + ".smoothLevel"):
                        cmds.setAttr(shape + ".smoothLevel", level)
                    if cmds.objExists(shape + ".renderSmoothLevel"):
                        cmds.setAttr(shape + ".renderSmoothLevel", level)
                    if cmds.objExists(shape + ".useSmoothPreviewForRender"):
                        cmds.setAttr(shape + ".useSmoothPreviewForRender", 1)
                    if cmds.objExists(shape + ".displaySmoothMesh"):
                        cmds.setAttr(shape + ".displaySmoothMesh", 2)
                except Exception as e:
                    cmds.warning("Could not update export smoothing for '{}': {}".format(shape, e))

    @staticmethod
    def _restore_smooth_preview_state(state):
        for shape, attrs in state.items():
            if not shape or not cmds.objExists(shape):
                continue
            for attr, value in attrs.items():
                plug = "{}.{}".format(shape, attr)
                if cmds.objExists(plug):
                    try:
                        cmds.setAttr(plug, value)
                    except Exception:
                        pass

    @staticmethod
    def _make_zero_transform_hp_export_copies(meshes):
        """Create temporary HP export copies with world-space geometry and zeroed transforms."""
        if not meshes:
            return meshes, None

        temp_root = cmds.group(em=True, name="BG_HP_Export_Zero_Temp#", world=True)
        prepared = []

        for mesh in meshes:
            if not mesh or not cmds.objExists(mesh):
                continue

            short_name = mesh.split('|')[-1]
            if "_high" not in short_name.lower():
                prepared.append(mesh)
                continue

            try:
                dup = cmds.duplicate(mesh, returnRootsOnly=True)[0]
                dup = cmds.parent(dup, temp_root, absolute=True)[0]
                dup = cmds.rename(dup, short_name)
                if FinalExportProcessor._is_zbrush_mesh(mesh):
                    for shape in cmds.listRelatives(dup, shapes=True, fullPath=True) or []:
                        if cmds.objExists(shape + ".displaySmoothMesh"):
                            cmds.setAttr(shape + ".displaySmoothMesh", 0)
                try:
                    cmds.makeIdentity(dup, apply=True, t=True, r=True, s=True, n=False, pn=True)
                except Exception as e:
                    cmds.warning("Could not zero HP export transform '{}': {}".format(short_name, e))
                prepared.append(cmds.ls(dup, long=True)[0])
            except Exception as e:
                cmds.warning("Could not create zero-transform HP export copy '{}': {}".format(short_name, e))
                prepared.append(mesh)

        return prepared, temp_root


    @staticmethod
    def combine_all_subgroups(base_name, hp_main, lp_main, parent_window=None):
        """Final combine and rename logic."""
        if not hp_main or not lp_main:
            cmds.warning("Bake Groups: HP or LP root objects not found.")
            return {'success': False, 'hp': 0, 'lp': 0}

        progress_dlg = QtWidgets.QProgressDialog(bg_l10n.text("Baking Subgroups..."), bg_l10n.text("Cancel"), 0, 100, parent_window)
        progress_dlg.setWindowModality(QtCore.Qt.WindowModal)
        progress_dlg.show()

        with bg_core.undo_chunk("CombineAndRenameWithBase"):
            try:
                hp_count = 0
                lp_count = 0

                # --- HP LOGIC (Renaming inside subgroups) ---
                hp_subgroups = [g for g in cmds.listRelatives(hp_main, children=True, fullPath=True, type='transform') or [] 
                                if not cmds.listRelatives(g, shapes=True)]
                
                for sg in hp_subgroups:
                    sg_name = sg.split('|')[-1].replace(bg_core.BakeConfig.SUFFIX_HP, "").replace(".", "_")
                    meshes = cmds.listRelatives(sg, allDescendents=True, type='mesh', fullPath=True) or []
                    transforms = list(set([cmds.listRelatives(m, parent=True, fullPath=True)[0] for m in meshes]))
                    
                    for i, tr in enumerate(transforms):
                        new_name = "{}_{}_high_{:03d}".format(base_name, sg_name, i + 1).replace(".", "_")
                        cmds.rename(tr, new_name)
                        hp_count += 1

                # --- LP LOGIC (Combining in the global LP_Combine_BG root) ---
                def _has_mesh_shape(node):
                    return bool(cmds.listRelatives(node, shapes=True, type='mesh') or [])

                def _is_lp_subgroup(node):
                    short_name = node.split('|')[-1]
                    suffix_re = re.escape(bg_core.BakeConfig.SUFFIX_LP) + r'\d*$'
                    is_lp_group = bool(re.search(suffix_re, short_name, re.IGNORECASE))
                    attr = "{}.{}".format(node, bg_core.BakeConfig.ATTR_BAKE_GROUP)
                    if cmds.objExists(attr):
                        try:
                            is_lp_group = is_lp_group or cmds.getAttr(attr) == "LP"
                        except Exception:
                            pass
                    return is_lp_group

                def _clean_lp_group_name(node):
                    short_name = node.split('|')[-1].replace(".", "_")
                    return re.sub(
                        re.escape(bg_core.BakeConfig.SUFFIX_LP) + r'\d*$',
                        '',
                        short_name,
                        flags=re.IGNORECASE
                    )

                def _combine_lp_transforms(transforms, final_lp_name, chapter_lp_root):
                    if not transforms:
                        return False

                    old_finals = [child for child in (cmds.listRelatives(chapter_lp_root, children=True, fullPath=True, type='transform') or [])
                                  if child.split('|')[-1].startswith(final_lp_name) and cmds.listRelatives(child, shapes=True)]
                    if old_finals:
                        cmds.delete(old_finals)

                    dups = cmds.duplicate(transforms, returnRootsOnly=True)
                    if len(dups) > 1:
                        combined = cmds.polyUnite(dups, ch=False, mergeUVSets=True)[0]
                    else:
                        combined = dups[0]

                    cmds.delete(combined, constructionHistory=True)
                    cmds.polyTriangulate(combined, constructionHistory=False)
                    combined = cmds.rename(combined, final_lp_name)

                    current_parent = cmds.listRelatives(combined, parent=True, fullPath=True)
                    chapter_lp_full = cmds.ls(chapter_lp_root, l=True)[0]
                    if not current_parent or current_parent[0] != chapter_lp_full:
                        cmds.parent(combined, chapter_lp_full, absolute=True)
                    return True

                lp_children = cmds.listRelatives(lp_main, children=True, fullPath=True, type='transform') or []
                lp_subgroups = [g for g in lp_children if not _has_mesh_shape(g) and _is_lp_subgroup(g)]
                direct_lp_meshes = [g for g in lp_children if _has_mesh_shape(g)]

                if not lp_subgroups:
                    lp_subgroups = [g for g in lp_children if not _has_mesh_shape(g)]
                
                # Создаем или находим глобальный рут
                global_lp_root = "LP_Combine_BG"
                if not cmds.objExists(global_lp_root):
                    cmds.group(em=True, name=global_lp_root, world=True)

                # Создаем или находим папку главы
                chapter_lp_root = "{}|{}".format(global_lp_root, base_name)
                if not cmds.objExists(chapter_lp_root):
                    grp = cmds.group(em=True, name=base_name)
                    cmds.parent(grp, global_lp_root, absolute=True)
                
                for sg in lp_subgroups:
                    sg_name = _clean_lp_group_name(sg)
                    final_lp_name = "{}_{}_low".format(base_name, sg_name).replace(".", "_")
                    
                    # Ищем старые финалки уже в новой папке главы
                    meshes = cmds.listRelatives(sg, allDescendents=True, type='mesh', fullPath=True) or []
                    if not meshes: continue
                    transforms = list(set([cmds.listRelatives(m, parent=True, fullPath=True)[0] for m in meshes]))
                    if not transforms: continue

                    if _combine_lp_transforms(transforms, final_lp_name, chapter_lp_root):
                        lp_count += 1

                if lp_count == 0 and direct_lp_meshes:
                    final_lp_name = "{}_low".format(base_name).replace(".", "_")
                    if _combine_lp_transforms(direct_lp_meshes, final_lp_name, chapter_lp_root):
                        lp_count += 1
                            
            except Exception as e:
                cmds.warning("Combine error: {}".format(e))
                return {'success': False, 'hp': 0, 'lp': 0}
            finally:
                progress_dlg.close()
                
        return {'success': True, 'hp': hp_count, 'lp': lp_count}

    @staticmethod
    def process_final_group(base_name, hp_main, final_mesh_widgets):
        """Smooth final meshes and merge with ZBrush."""
        with bg_core.undo_chunk("ProcessFinalGroup"):
            for item in final_mesh_widgets:
                full_prefix = item['full_prefix']
                level = item['combo'].currentIndex()
                
                if level > 0:
                    hp_meshes = cmds.listRelatives(hp_main, children=True, fullPath=True) or []
                    for hp in hp_meshes:
                        short_name = hp.split('|')[-1]
                        if short_name.startswith(full_prefix + "_high") and cmds.objExists(hp):
                            if FinalExportProcessor._is_zbrush_mesh(hp):
                                cmds.warning("Skipped smooth for '{}' (ZBrush geometry)".format(hp))
                                continue
                            try:
                                cmds.polySmooth(hp, divisions=level, constructionHistory=False)
                            except Exception as e:
                                cmds.warning("Failed to smooth {}: {}".format(hp, e))
            
            hp_target = "Bake_Groups|{}|HP".format(base_name)
            if cmds.objExists(hp_target):
                smooth_meshes = cmds.ls("{}|Bake_Smooth_*".format(hp_target), type='transform', fullPath=True) or []
                for sm in smooth_meshes:
                    if not cmds.objExists(sm): continue
                    
                    base_node_name = sm.split('|')[-1].replace("Bake_Smooth_", "Bake_")
                    zb_mesh = "{}|{}".format(hp_target, base_node_name)
                    
                    if cmds.objExists(zb_mesh):
                        zb_short = zb_mesh.split('|')[-1]
                        combined = cmds.polyUnite([sm, zb_mesh], ch=False)[0]
                        cmds.delete(combined, constructionHistory=True)
                        cmds.xform(combined, cp=True)
                        combined = cmds.rename(combined, zb_short)
                        cmds.parent(combined, hp_target, absolute=True)
                    else:
                        new_name = sm.split('|')[-1].replace("Bake_Smooth_", "Bake_")
                        cmds.rename(sm, new_name)

    @staticmethod
    def get_valid_mesh_transforms(root_node):
        """Strict collection of only valid geometry for export."""
        if not cmds.objExists(root_node): return []
        shapes = cmds.listRelatives(root_node, allDescendents=True, fullPath=True, type='mesh') or []
        valid_shapes = [s for s in shapes if not cmds.getAttr(s + ".intermediateObject")]
        return list(set([cmds.listRelatives(s, parent=True, fullPath=True)[0] for s in valid_shapes]))

    @staticmethod
    def _get_mesh_materials_and_faces(mesh_transform):
        """Absolutely strict method for determining faces using polyListComponentConversion."""
        shapes = cmds.listRelatives(mesh_transform, shapes=True, fullPath=True)
        if not shapes: return {}
        shape = shapes[0]
        
        sgs = list(set(cmds.listConnections(shape, type='shadingEngine') or []))
        mat_dict = {}
        total_faces = cmds.polyEvaluate(mesh_transform, face=True)
        
        for sg in sgs:
            members = cmds.sets(sg, q=True) or []
            mesh_components = []
            
            # 1. Filter only the set elements belonging to our mesh
            for m in members:
                m_long = cmds.ls(m, long=True)
                if not m_long: continue
                m_path = m_long[0]
                
                if m_path == mesh_transform or m_path == shape:
                    mesh_components.append(m_path)
                elif m_path.startswith(mesh_transform + ".") or m_path.startswith(shape + "."):
                    mesh_components.append(m_path)
                    
            if not mesh_components:
                continue
                
            # If the material is assigned to the entire object
            if mesh_transform in mesh_components or shape in mesh_components:
                mat_dict[sg] = list(range(total_faces))
                continue
                
            # 2. Convert components strictly to faces (solves issues with f[*], f[0:5], etc.)
            try:
                faces = cmds.polyListComponentConversion(mesh_components, toFace=True)
                faces_flat = cmds.ls(faces, flatten=True, long=True) or []
                
                valid_indices = set()
                for item in faces_flat:
                    match = re.search(r'\.f\[(\d+)\]', item)
                    if match:
                        valid_indices.add(int(match.group(1)))
                        
                if valid_indices:
                    mat_dict[sg] = list(valid_indices)
            except Exception as e:
                cmds.warning("Error parsing faces for material {}: {}".format(sg, e))
                
        return mat_dict

    @staticmethod
    def _process_multimaterial_mesh(lp_mesh, hp_meshes, mat_dict):
        """Duplicates multi-material mesh, cleans faces, shifts UV, and duplicates HP."""
        new_lp_meshes = []
        new_hp_meshes = []
        
        idx = 1
        for sg, face_indices in mat_dict.items():
            if not face_indices: continue
            
            # 1. Duplicate LP
            dup_lp = cmds.duplicate(lp_mesh, returnRootsOnly=True)[0]
            short_name = lp_mesh.split('|')[-1]
            new_lp_name = cmds.rename(dup_lp, "{}_mat{}".format(short_name, idx))
            new_lp_full = cmds.ls(new_lp_name, long=True)[0]
            
            # 2. Clean extra faces
            total_faces = cmds.polyEvaluate(new_lp_full, face=True)
            faces_to_keep = set(face_indices)
            faces_to_delete = ["{}.f[{}]".format(new_lp_full, i) for i in range(total_faces) if i not in faces_to_keep]
            
            if faces_to_delete:
                cmds.delete(faces_to_delete)
                
            # 3. Shift UV into 0-1 tile (UDIM)
            uvs = cmds.ls("{}.map[*]".format(new_lp_full), flatten=True)
            if uvs:
                try:
                    # polyEvaluate on the whole object is safer than on a giant list of strings
                    uv_bbox = cmds.polyEvaluate(new_lp_full, boundingBoxComponent2d=True)
                    if uv_bbox:
                        u_min, u_max = uv_bbox[0]
                        v_min, v_max = uv_bbox[1]
                        offset_u = -math.floor(u_min)
                        offset_v = -math.floor(v_min)
                        if offset_u != 0 or offset_v != 0:
                            cmds.polyEditUV(uvs, uValue=offset_u, vValue=offset_v)
                except Exception as e:
                    cmds.warning("UV shift failed for {}: {}".format(new_lp_full, e))
                    
            new_lp_meshes.append(new_lp_full)
            
            # 4. Duplicate HP meshes
            if hp_meshes:
                for hp in hp_meshes:
                    if not cmds.objExists(hp): continue
                    dup_hp = cmds.duplicate(hp, returnRootsOnly=True)[0]
                    hp_short = hp.split('|')[-1]
                    new_hp_name = cmds.rename(dup_hp, "{}_mat{}".format(hp_short, idx))
                    new_hp_meshes.append(cmds.ls(new_hp_name, long=True)[0])
                    
            idx += 1
            
        return new_lp_meshes, new_hp_meshes

    @staticmethod
    def export_chapter(base_name, hp_main, lp_main, final_mesh_widgets, parent_window=None, mode='both', export_dir=None, smooth_states=None):
        """Prepare meshes, apply smoothing, export, and rollback."""
        if not export_dir:
            export_dirs = cmds.fileDialog2(fileMode=3, caption=bg_l10n.text("Select Export Directory"))
            if not export_dirs: return False
            export_dir = export_dirs[0]
        
        if not cmds.pluginInfo('fbxmaya', query=True, loaded=True):
            cmds.loadPlugin('fbxmaya')

        # Determine prefixes and smooth levels (support UI and Batch export)
        prefixes_to_process = []
        
        # Get the direct LP children list to search for prefixes (Support new LP_Combine_BG structure)
        chapter_grp_path = "LP_Combine_BG|{}".format(base_name)
        if cmds.objExists(chapter_grp_path):
            lp_direct_children = cmds.listRelatives(chapter_grp_path, children=True, fullPath=True, type='transform') or []
        else:
            lp_direct_children = cmds.listRelatives(lp_main, children=True, fullPath=True, type='transform') or []
            
        lp_all = []
        for child in lp_direct_children:
            shapes = cmds.listRelatives(child, shapes=True, fullPath=True, type='mesh') or []
            if shapes and not cmds.getAttr(shapes[0] + ".intermediateObject"):
                lp_all.append(child)

        if mode == 'lp':
            if not lp_all:
                return False

            cmds.select(lp_all, replace=True)
            export_name = "{}_LP".format(base_name).replace(".", "_")
            export_path = "{}/{}.fbx".format(export_dir.rstrip('/\\'), export_name).replace('\\', '/')
            FinalExportProcessor.export_selected_fbx(export_path)
            return export_name
                
        if final_mesh_widgets:
            for item in final_mesh_widgets:
                prefixes_to_process.append({
                    'prefix': item['full_prefix'].lower(),
                    'smooth_level': FinalExportProcessor._smooth_level_from_item(item)
                })
        else:
            # Batch mode: use saved UI state when available; otherwise keep UI default Smooth 2.
            for lp in lp_all:
                lp_short = lp.split('|')[-1].lower()
                if "_low" in lp_short:
                    prefix = lp_short.rsplit("_low", 1)[0]
                    if not any(p['prefix'] == prefix for p in prefixes_to_process):
                        prefixes_to_process.append({
                            'prefix': prefix,
                            'smooth_level': FinalExportProcessor._smooth_level_from_states(
                                smooth_states or {}, base_name, prefix, default_level=2
                            )
                        })
        
        if not prefixes_to_process:
            prefixes_to_process.append({'prefix': '___dummy___', 'smooth_level': 0})
            
        progress_dlg = QtWidgets.QProgressDialog(bg_l10n.text("Exporting Chapter..."), bg_l10n.text("Cancel"), 0, len(prefixes_to_process), parent_window)
        progress_dlg.setWindowModality(QtCore.Qt.WindowModal)
        progress_dlg.show()
        
        smooth_preview_state = {}
        temp_nodes = []
        cmds.refresh(suspend=True)
        
        try:
            hp_all = FinalExportProcessor.get_valid_mesh_transforms(hp_main)
            
            exported_meshes = set() 
            all_to_export = []
            
            for i, item in enumerate(prefixes_to_process):
                if progress_dlg.wasCanceled(): break
                
                full_prefix_lower = item['prefix']
                smooth_level = item['smooth_level']
                
                progress_dlg.setLabelText(bg_l10n.text("Preparing: {name}").format(name=full_prefix_lower))
                progress_dlg.setValue(i)
                QtWidgets.QApplication.processEvents()
                
                hp_meshes = [m for m in hp_all if m.split('|')[-1].lower().startswith(full_prefix_lower + "_high")]
                lp_meshes = [m for m in lp_all if m.split('|')[-1].lower().startswith(full_prefix_lower + "_low")]
                
                if not hp_meshes:
                    cmds.warning("Warning: No HP meshes found for LP prefix '{}'. Check naming.".format(full_prefix_lower))

                if mode in ['both', 'hp']:
                    FinalExportProcessor._apply_export_smooth_preview(hp_meshes, smooth_level, smooth_preview_state)
                
                # --- MULTI-MATERIAL LOGIC ---
                was_split = False
                final_hp_for_export = set(hp_meshes)
                final_lp_for_export = set()

                for lp in lp_meshes:
                    mat_dict = FinalExportProcessor._get_mesh_materials_and_faces(lp)
                    
                    if len(mat_dict) > 1:
                        was_split = True
                        new_lps, new_hps = FinalExportProcessor._process_multimaterial_mesh(lp, hp_meshes, mat_dict)
                        temp_nodes.extend(new_lps)
                        temp_nodes.extend(new_hps)
                        final_lp_for_export.update(new_lps)
                        final_hp_for_export.update(new_hps)
                        exported_meshes.add(lp)
                    else:
                        final_lp_for_export.add(lp)

                if was_split:
                    final_hp_for_export.difference_update(hp_meshes)
                    for hp in hp_meshes:
                        exported_meshes.add(hp)

                # --- ADD TO FINAL LIST (With mode filtering) ---
                if mode == 'hp' or mode == 'both':
                    for m in final_hp_for_export:
                        if m not in exported_meshes:
                            all_to_export.append(m)
                            exported_meshes.add(m)
                            
                if mode == 'lp' or mode == 'both':
                    for m in final_lp_for_export:
                        if m not in exported_meshes:
                            all_to_export.append(m)
                            exported_meshes.add(m)
            
            # Fallback for lost meshes (accounting for export mode)
            for m in hp_all:
                if m not in exported_meshes and mode in ['both', 'hp']:
                    all_to_export.append(m)
                    exported_meshes.add(m)
                    
            for m in lp_all:
                if m not in exported_meshes and mode in ['both', 'lp']:
                    all_to_export.append(m)
                    exported_meshes.add(m)
            
            # --- EXPORT ---
            if all_to_export:
                export_nodes = all_to_export
                if mode in ['both', 'hp']:
                    export_nodes, temp_root = FinalExportProcessor._make_zero_transform_hp_export_copies(all_to_export)
                    if temp_root:
                        temp_nodes.append(temp_root)

                cmds.select(export_nodes, replace=True)
                
                # If exporting separately, add corresponding suffix to the file
                suffix = ""
                if mode == 'hp': suffix = "_HP"
                elif mode == 'lp': suffix = "_LP"
                
                export_name = "{}{}".format(base_name, suffix).replace(".", "_")
                export_path = "{}/{}.fbx".format(export_dir.rstrip('/\\'), export_name).replace('\\', '/')
                
                FinalExportProcessor.export_selected_fbx(export_path)
                return export_name
                
        except Exception as e:
            cmds.warning("Export failed: {}".format(e))
            return False
        
        finally:
            FinalExportProcessor._restore_smooth_preview_state(smooth_preview_state)
            existing_temp_nodes = [node for node in reversed(temp_nodes) if node and cmds.objExists(node)]
            if existing_temp_nodes:
                try:
                    cmds.delete(existing_temp_nodes)
                except Exception as e:
                    cmds.warning("Could not clean export temporary nodes: {}".format(e))
            cmds.select(clear=True)
            cmds.refresh(suspend=False)
            def safe_refresh():
                try:
                    cmds.refresh()
                except Exception:
                    pass
            QtCore.QTimer.singleShot(100, safe_refresh)
            progress_dlg.close()
            
        return False
