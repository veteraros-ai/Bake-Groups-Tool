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
        FinalExportProcessor._set_fbx_bool("FBXExportSmoothMesh", False)
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
    def _export_with_lp_triangulation_rollback(export_nodes, export_path):
        """Export the selection to FBX, temporarily triangulating any LP (_low)
        meshes in the set first, then rolling the triangulation back so the LP
        originals stay quads. HP nodes (usually zero-transform temp copies) pass
        through untouched. The FBX file write is not undoable, so cmds.undo() only
        reverts the in-scene triangulation."""
        export_nodes = [n for n in (export_nodes or []) if n and cmds.objExists(n)]
        if not export_nodes:
            return
        lp_nodes = [n for n in export_nodes if "_low" in n.split('|')[-1].lower()]
        if not lp_nodes:
            cmds.select(export_nodes, replace=True)
            FinalExportProcessor.export_selected_fbx(export_path)
            return
        cmds.undoInfo(openChunk=True, chunkName="LPExportTriangulate")
        try:
            for m in lp_nodes:
                try:
                    cmds.polyTriangulate(m, constructionHistory=False)
                except Exception:
                    pass
            cmds.select(export_nodes, replace=True)
            FinalExportProcessor.export_selected_fbx(export_path)
        finally:
            cmds.undoInfo(closeChunk=True)
            try:
                cmds.undo()  # roll back the LP triangulation; originals stay quads
            except Exception:
                pass

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
    def _smooth_level_from_states(smooth_states, base_name, prefix, default_level=0):
        """Return the saved Smooth level for one export subgroup.

        ``final_smooth_states`` historically used the short subgroup name as its
        key, while the export path works with the full ``{chapter}_{subgroup}``
        prefix.  Support both representations (and the temporary ``prefix:``
        representation used by the exporter).  Crucially, an unknown state must
        mean *no smoothing*, never an implicit Smooth 2 export.
        """
        if not smooth_states:
            return default_level

        prefix_key = str(prefix).strip().lower()
        base_prefix = str(base_name).strip().lower() + "_"
        keys = [prefix_key, "prefix:{}".format(prefix_key)]
        if prefix_key.startswith(base_prefix):
            keys.append(prefix_key[len(base_prefix):])

        lower_map = {}
        for key, value in smooth_states.items():
            lower_map[str(key).strip().lower()] = value

        for key in keys:
            value = lower_map.get(str(key).strip().lower())
            if value is not None:
                try:
                    return int(value)
                except Exception:
                    return default_level
        return default_level

    @staticmethod
    def _resolve_child_under_parent(node, parent):
        parent_long = cmds.ls(parent, long=True)
        parent_long = parent_long[0] if parent_long else parent
        node_short = node.split('|')[-1]

        matches = cmds.ls(node, long=True) or []
        for match in matches:
            if match.startswith(parent_long + "|"):
                return match

        children = cmds.listRelatives(parent_long, children=True, fullPath=True, type='transform') or []
        for child in children:
            if child.split('|')[-1] == node_short:
                return child

        return matches[0] if matches else node

    @staticmethod
    def _make_zero_transform_hp_export_copies(meshes, smooth_levels=None):
        """Create temporary HP export copies with world-space geometry and zeroed transforms."""
        if not meshes:
            return meshes, None

        FinalExportProcessor._cleanup_zero_transform_hp_export_temps()
        smooth_levels = smooth_levels or {}
        temp_root = cmds.group(em=True, name="BG_HP_Export_Zero_Temp#", world=True)
        temp_root = cmds.ls(temp_root, long=True)[0]
        prepared = []

        for mesh in meshes:
            if not mesh or not cmds.objExists(mesh):
                continue

            short_name = mesh.split('|')[-1]
            if "_high" not in short_name.lower():
                prepared.append(mesh)
                continue

            try:
                long_mesh = cmds.ls(mesh, long=True)
                long_mesh = long_mesh[0] if long_mesh else mesh
                is_zbrush_mesh = FinalExportProcessor._is_zbrush_mesh(mesh)
                level = smooth_levels.get(long_mesh, smooth_levels.get(short_name.lower(), 0))
                if not level and not is_zbrush_mesh:
                    short_lower = short_name.lower()
                    for key, value in smooth_levels.items():
                        if not str(key).startswith("prefix:"):
                            continue
                        prefix = str(key).split(":", 1)[1]
                        if short_lower.startswith(prefix + "_high"):
                            level = value
                            break
                if is_zbrush_mesh:
                    level = 0

                dup = cmds.duplicate(mesh, returnRootsOnly=True)[0]
                dup = cmds.ls(dup, long=True)[0]
                if level > 0:
                    try:
                        cmds.polySmooth(dup, divisions=int(level), keepBorder=False, constructionHistory=False)
                        cmds.delete(dup, constructionHistory=True)
                    except Exception as e:
                        cmds.warning("Could not smooth HP export copy '{}': {}".format(short_name, e))
                dup = cmds.parent(dup, temp_root, absolute=True)[0]
                dup = cmds.rename(dup, short_name)
                dup = FinalExportProcessor._resolve_child_under_parent(dup, temp_root)
                if is_zbrush_mesh:
                    for shape in cmds.listRelatives(dup, shapes=True, fullPath=True) or []:
                        if cmds.objExists(shape + ".displaySmoothMesh"):
                            cmds.setAttr(shape + ".displaySmoothMesh", 0)
                try:
                    cmds.makeIdentity(dup, apply=True, t=True, r=True, s=True, n=False, pn=True)
                except Exception as e:
                    cmds.warning("Could not zero HP export transform '{}': {}".format(short_name, e))
                prepared.append(FinalExportProcessor._resolve_child_under_parent(dup, temp_root))
            except Exception as e:
                cmds.warning("Could not create zero-transform HP export copy '{}': {}".format(short_name, e))
                prepared.append(mesh)

        return prepared, temp_root

    @staticmethod
    def _cleanup_zero_transform_hp_export_temps(extra_nodes=None):
        nodes = []
        for node in extra_nodes or []:
            if node and cmds.objExists(node):
                nodes.append(node)
        nodes.extend(cmds.ls("BG_HP_Export_Zero_Temp*", type='transform', long=True) or [])
        seen = set()
        for node in sorted(nodes, key=lambda n: n.count('|'), reverse=True):
            if not node or node in seen or not cmds.objExists(node):
                continue
            seen.add(node)
            try:
                cmds.delete(node)
            except Exception:
                pass


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
    def export_chapter(base_name, hp_main, lp_main, final_mesh_widgets, parent_window=None, mode='both', export_dir=None, smooth_states=None, extra_chapters=None, export_name_override=None):
        """Prepare meshes, apply smoothing, export, and rollback.

        ``extra_chapters`` (list of {'base','hp','lp','smooth'}) folds several
        chapters into ONE combined HP/LP file (Export by material, book named
        after a material), and ``export_name_override`` sets the file base name.
        With both omitted this behaves exactly as a single-chapter export."""
        if not export_dir:
            export_dirs = cmds.fileDialog2(fileMode=3, caption=bg_l10n.text("Select Export Directory"))
            if not export_dirs: return False
            export_dir = export_dirs[0]

        if not cmds.pluginInfo('fbxmaya', query=True, loaded=True):
            cmds.loadPlugin('fbxmaya')

        FinalExportProcessor._cleanup_zero_transform_hp_export_temps()

        # Chapters folded into this export - one, unless Export by material.
        chapters = [{'base': base_name, 'hp': hp_main, 'lp': lp_main, 'smooth': smooth_states or {}}]
        if extra_chapters:
            chapters.extend(extra_chapters)
        name_base = export_name_override or base_name

        def _chapter_for_short(short_lower):
            for ch in chapters:
                if short_lower.startswith(str(ch['base']).lower() + "_"):
                    return ch
            return chapters[0]

        # Determine prefixes and smooth levels (support UI and Batch export)
        prefixes_to_process = []

        # LP _low_NNN meshes now live in place inside the LP subgroups under
        # each chapter's lp_main (no LP_Combine_BG). Collect by walking descendants.
        lp_all = []
        for ch in chapters:
            if not ch['lp'] or not cmds.objExists(ch['lp']):
                continue
            for child in (cmds.listRelatives(ch['lp'], allDescendents=True, fullPath=True, type='transform') or []):
                shapes = cmds.listRelatives(child, shapes=True, fullPath=True, type='mesh') or []
                if shapes and not cmds.getAttr(shapes[0] + ".intermediateObject") and "_low" in child.split('|')[-1].lower():
                    lp_all.append(child)

        if mode == 'lp':
            if not lp_all:
                return False

            export_name = "{}_LP".format(name_base).replace(".", "_")
            export_path = "{}/{}.fbx".format(export_dir.rstrip('/\\'), export_name).replace('\\', '/')
            FinalExportProcessor._export_with_lp_triangulation_rollback(lp_all, export_path)
            return export_name
                
        if final_mesh_widgets:
            for item in final_mesh_widgets:
                prefixes_to_process.append({
                    'prefix': item['full_prefix'].lower(),
                    'smooth_level': FinalExportProcessor._smooth_level_from_item(item),
                    'hp_nodes': item.get('hp_nodes') or []
                })
        else:
            # Batch mode has no live combo boxes.  Use the saved state only;
            # missing/legacy-unresolvable data must not silently export at
            # Smooth 2.
            for lp in lp_all:
                lp_short = lp.split('|')[-1].lower()
                if "_low" in lp_short:
                    prefix = lp_short.rsplit("_low", 1)[0]
                    if not any(p['prefix'] == prefix for p in prefixes_to_process):
                        ch = _chapter_for_short(lp_short)
                        prefixes_to_process.append({
                            'prefix': prefix,
                            'smooth_level': FinalExportProcessor._smooth_level_from_states(
                                ch['smooth'], ch['base'], prefix, default_level=0
                            )
                        })
        
        if not prefixes_to_process:
            prefixes_to_process.append({'prefix': '___dummy___', 'smooth_level': 0})
            
        progress_dlg = QtWidgets.QProgressDialog(bg_l10n.text("Exporting Chapter..."), bg_l10n.text("Cancel"), 0, len(prefixes_to_process), parent_window)
        progress_dlg.setWindowModality(QtCore.Qt.WindowModal)
        progress_dlg.show()
        
        smooth_levels = {}
        temp_nodes = []
        cmds.refresh(suspend=True)
        
        try:
            hp_all = []
            for ch in chapters:
                if ch['hp'] and cmds.objExists(ch['hp']):
                    hp_all.extend(FinalExportProcessor.get_valid_mesh_transforms(ch['hp']))
            hp_all = list(set(hp_all))

            exported_meshes = set()
            all_to_export = []
            
            for i, item in enumerate(prefixes_to_process):
                if progress_dlg.wasCanceled(): break
                
                full_prefix_lower = item['prefix']
                smooth_level = item['smooth_level']
                try:
                    smooth_level_value = max(0, int(smooth_level or 0))
                except Exception:
                    smooth_level_value = 0
                
                progress_dlg.setLabelText(bg_l10n.text("Preparing: {name}").format(name=full_prefix_lower))
                progress_dlg.setValue(i)
                QtWidgets.QApplication.processEvents()
                
                hp_meshes = [m for m in hp_all if m.split('|')[-1].lower().startswith(full_prefix_lower + "_high")]
                lp_meshes = [m for m in lp_all if m.split('|')[-1].lower().startswith(full_prefix_lower + "_low")]
                item_hp_meshes = []
                for hp_node in item.get('hp_nodes', []) or []:
                    for hp_match in cmds.ls(hp_node, long=True) or []:
                        if hp_match in hp_all:
                            item_hp_meshes.append(hp_match)
                if item_hp_meshes:
                    hp_meshes = list(set(hp_meshes + item_hp_meshes))
                
                if not hp_meshes:
                    cmds.warning("Warning: No HP meshes found for LP prefix '{}'. Check naming.".format(full_prefix_lower))

                smoothable_hp_shorts = set()
                if smooth_level_value > 0 and mode in ['both', 'hp']:
                    smooth_levels["prefix:{}".format(full_prefix_lower)] = max(
                        smooth_levels.get("prefix:{}".format(full_prefix_lower), 0),
                        smooth_level_value
                    )
                    for hp in hp_meshes:
                        short_name = hp.split('|')[-1].lower()
                        if FinalExportProcessor._is_zbrush_mesh(hp):
                            cmds.warning("Skipped smooth for '{}' (ZBrush geometry)".format(hp))
                        else:
                            hp_long = cmds.ls(hp, long=True)
                            hp_long = hp_long[0] if hp_long else hp
                            smoothable_hp_shorts.add(short_name)
                            smooth_levels[hp_long] = max(smooth_levels.get(hp_long, 0), smooth_level_value)
                            smooth_levels[short_name] = max(smooth_levels.get(short_name, 0), smooth_level_value)
                
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
                        if smooth_level_value > 0 and mode in ['both', 'hp']:
                            for hp in new_hps:
                                short_name = hp.split('|')[-1].lower()
                                if any(short_name.startswith(source + "_mat") for source in smoothable_hp_shorts):
                                    hp_long = cmds.ls(hp, long=True)
                                    hp_long = hp_long[0] if hp_long else hp
                                    smooth_levels[hp_long] = max(smooth_levels.get(hp_long, 0), smooth_level_value)
                                    smooth_levels[short_name] = max(smooth_levels.get(short_name, 0), smooth_level_value)
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
                    export_nodes, temp_root = FinalExportProcessor._make_zero_transform_hp_export_copies(all_to_export, smooth_levels)
                    if temp_root:
                        temp_nodes.append(temp_root)

                cmds.select(export_nodes, replace=True)
                
                # If exporting separately, add corresponding suffix to the file
                suffix = ""
                if mode == 'hp': suffix = "_HP"
                elif mode == 'lp': suffix = "_LP"
                
                export_name = "{}{}".format(name_base, suffix).replace(".", "_")
                export_path = "{}/{}.fbx".format(export_dir.rstrip('/\\'), export_name).replace('\\', '/')

                FinalExportProcessor._export_with_lp_triangulation_rollback(export_nodes, export_path)
                return export_name
                
        except Exception as e:
            cmds.warning("Export failed: {}".format(e))
            return False
        
        finally:
            FinalExportProcessor._cleanup_zero_transform_hp_export_temps(reversed(temp_nodes))
            cmds.select(clear=True)
            cmds.refresh(suspend=False)
            progress_dlg.close()
            
        return False
