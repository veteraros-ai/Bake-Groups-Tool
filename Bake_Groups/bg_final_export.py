# -*- coding: utf-8 -*-
from __future__ import print_function, division, absolute_import

import maya.cmds as cmds
import maya.mel as mel
import bg_core
import bg_localization as bg_l10n
import re
import math
import contextlib

try:
    import maya.api.OpenMaya as om
except Exception:
    om = None

try:
    from PySide6 import QtWidgets, QtCore
except ImportError:
    from PySide2 import QtWidgets, QtCore

class FinalExportProcessor(object):
    _fbx_session_depth = 0
    _fbx_previous_settings = None
    _viewport_session_depth = 0
    _viewport_was_suspended = False
    _undo_session_depth = 0
    _undo_was_enabled = False

    @classmethod
    @contextlib.contextmanager
    def _fbx_settings_session(cls):
        """Set FBX options once for a whole batch and restore them safely."""
        outermost = cls._fbx_session_depth == 0
        if outermost:
            if not cmds.pluginInfo('fbxmaya', query=True, loaded=True):
                cmds.loadPlugin('fbxmaya')
            cls._fbx_previous_settings = {
                "FBXExportInputConnections": cls._query_fbx_bool("FBXExportInputConnections"),
                "FBXExportGenerateLog": cls._query_fbx_bool("FBXExportGenerateLog"),
                "FBXExportSmoothMesh": cls._query_fbx_bool("FBXExportSmoothMesh"),
            }
            cls._set_fbx_bool("FBXExportGenerateLog", False)
            cls._set_fbx_bool("FBXExportInputConnections", False)
            cls._set_fbx_bool("FBXExportSmoothMesh", False)
        cls._fbx_session_depth += 1
        try:
            yield
        finally:
            cls._fbx_session_depth = max(0, cls._fbx_session_depth - 1)
            if outermost:
                previous = cls._fbx_previous_settings or {}
                for command, value in previous.items():
                    if value is not None:
                        cls._set_fbx_bool(command, value)
                cls._fbx_previous_settings = None

    @classmethod
    @contextlib.contextmanager
    def _viewport_suspended(cls):
        """Suspend redraw once, preserving an already-suspended Maya viewport."""
        outermost = cls._viewport_session_depth == 0
        if outermost:
            try:
                cls._viewport_was_suspended = bool(cmds.refresh(query=True, suspend=True))
            except Exception:
                cls._viewport_was_suspended = False
            if not cls._viewport_was_suspended:
                cmds.refresh(suspend=True)
        cls._viewport_session_depth += 1
        try:
            yield
        finally:
            cls._viewport_session_depth = max(0, cls._viewport_session_depth - 1)
            if outermost:
                if not cls._viewport_was_suspended:
                    try:
                        cmds.refresh(suspend=False)
                        cmds.refresh(force=True)
                    except Exception:
                        pass
                cls._viewport_was_suspended = False

    @classmethod
    @contextlib.contextmanager
    def _undo_disabled(cls):
        """Keep temporary export geometry out of Maya's memory-heavy Undo queue."""
        outermost = cls._undo_session_depth == 0
        if outermost:
            try:
                cls._undo_was_enabled = bool(cmds.undoInfo(query=True, state=True))
            except Exception:
                cls._undo_was_enabled = False
            if cls._undo_was_enabled:
                cmds.undoInfo(stateWithoutFlush=False)
        cls._undo_session_depth += 1
        try:
            yield
        finally:
            cls._undo_session_depth = max(0, cls._undo_session_depth - 1)
            if outermost:
                if cls._undo_was_enabled:
                    try:
                        cmds.undoInfo(stateWithoutFlush=True)
                    except Exception:
                        pass
                cls._undo_was_enabled = False

    @classmethod
    @contextlib.contextmanager
    def export_session(cls):
        """One safe context for a complete single or batch export operation."""
        outermost = cls._fbx_session_depth == 0
        with cls._fbx_settings_session():
            with cls._viewport_suspended():
                with cls._undo_disabled():
                    if outermost:
                        cls._cleanup_stale_export_temps()
                    try:
                        yield
                    finally:
                        if outermost:
                            cls._cleanup_stale_export_temps()

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
        if FinalExportProcessor._fbx_session_depth:
            cmds.file(export_path, force=True, type="FBX export", exportSelected=True)
            return
        with FinalExportProcessor._fbx_settings_session():
            cmds.file(export_path, force=True, type="FBX export", exportSelected=True)

    @staticmethod
    def _export_with_lp_triangulation_rollback(export_nodes, export_path,
                                               reusable_temp_nodes=None,
                                               triangulate_all=False):
        """Export triangulated temporary copies without touching scene originals.

        ``reusable_temp_nodes`` are already-disposable meshes produced by the
        material splitter, so they can be triangulated in place. Every original
        LP/Cage mesh is duplicated under a temporary root first. This removes the
        expensive global ``cmds.undo()`` and keeps the artist's Undo queue intact.
        """
        reusable = set(reusable_temp_nodes or [])
        export_nodes = FinalExportProcessor._unique_existing(export_nodes)
        if not export_nodes:
            return False

        def needs_triangulation(node):
            return triangulate_all or "_low" in node.split('|')[-1].lower()

        temp_root = None
        prepared = []
        disposable = []
        try:
            for node in export_nodes:
                if not needs_triangulation(node):
                    prepared.append(node)
                    if FinalExportProcessor._is_export_temp_node(node):
                        disposable.append(node)
                    continue
                tri_node = node
                if node not in reusable:
                    if temp_root is None:
                        temp_root = cmds.group(em=True, name="BG_Export_Tri_Temp#", world=True)
                        temp_root = (cmds.ls(temp_root, long=True) or [temp_root])[0]
                    tri_node = FinalExportProcessor._duplicate_under_temp_root(node, temp_root)
                disposable.append(tri_node)
                try:
                    cmds.polyTriangulate(tri_node, constructionHistory=False)
                except Exception as exc:
                    cmds.warning("Could not triangulate export copy '{}': {}".format(node, exc))
                prepared.append(tri_node)

            # FBX Export Selected includes ancestors. Detach disposable meshes
            # from BG_*_Temp roots so no internal helper group leaks into FBX.
            flattened = []
            flattened_disposable = []
            disposable_set = set(disposable)
            for node in prepared:
                if node in disposable_set or FinalExportProcessor._is_export_temp_node(node):
                    short_name = node.split('|')[-1]
                    node = cmds.parent(node, world=True, absolute=True)[0]
                    node = cmds.rename(node, short_name)
                    node = (cmds.ls(node, long=True) or [node])[0]
                    flattened_disposable.append(node)
                flattened.append(node)
            prepared = flattened
            disposable = flattened_disposable

            cmds.select(prepared, replace=True)
            FinalExportProcessor.export_selected_fbx(export_path)
            return True
        finally:
            FinalExportProcessor._delete_temp_nodes(disposable)
            if temp_root and cmds.objExists(temp_root):
                try:
                    cmds.delete(temp_root)
                except Exception:
                    pass

    @staticmethod
    def _unique_existing(nodes):
        out = []
        seen = set()
        for node in nodes or []:
            if not node or node in seen or not cmds.objExists(node):
                continue
            seen.add(node)
            out.append(node)
        return out

    @staticmethod
    def _duplicate_under_temp_root(node, temp_root):
        """Duplicate one transform, preserving world-space placement and name."""
        short_name = node.split('|')[-1]
        dup = cmds.duplicate(node, returnRootsOnly=True)[0]
        dup = (cmds.ls(dup, long=True) or [dup])[0]
        dup = cmds.parent(dup, temp_root, absolute=True)[0]
        dup = cmds.rename(dup, short_name)
        return FinalExportProcessor._resolve_child_under_parent(dup, temp_root)

    @staticmethod
    def _is_export_temp_node(node):
        prefixes = (
            "BG_HP_Export_Zero_Temp",
            "BG_Export_Tri_Temp",
            "BG_Material_Export_Temp",
        )
        return any(
            part.startswith(prefixes)
            for part in str(node).split('|') if part)

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
    def _rename_child_under_parent(node, parent, short_name):
        """Rename a known child without rescanning every sibling under ``parent``."""
        renamed = cmds.rename(node, short_name)
        parent_long = (cmds.ls(parent, long=True) or [parent])[0]
        renamed_short = str(renamed).split('|')[-1]
        candidate = "{}|{}".format(parent_long, renamed_short)
        if cmds.objExists(candidate):
            return candidate
        return FinalExportProcessor._resolve_child_under_parent(renamed, parent_long)

    @staticmethod
    def _prepare_individual_hp_export_copy(entry, temp_root, reusable):
        """Prepare one HP mesh while preserving its object identity and name."""
        mesh = entry['mesh']
        long_mesh = entry['long']
        short_name = entry['short']
        level = entry['level']
        is_zbrush_mesh = entry['zbrush']
        is_reusable = mesh in reusable or long_mesh in reusable
        dup = long_mesh
        owned_copy = False

        try:
            if not is_reusable:
                dup = cmds.duplicate(mesh, returnRootsOnly=True)[0]
                dup = (cmds.ls(dup, long=True) or [dup])[0]
                owned_copy = True

            # ZBrush meshes deliberately never enter this branch: they stay as
            # separate objects and receive neither polyUnite nor polySmooth.
            if level > 0 and not is_zbrush_mesh:
                try:
                    cmds.polySmooth(
                        dup, divisions=int(level), keepBorder=False,
                        constructionHistory=False)
                except Exception as e:
                    cmds.warning(
                        "Could not smooth HP export copy '{}': {}".format(
                            short_name, e))

            dup = cmds.parent(dup, temp_root, absolute=True)[0]
            dup = FinalExportProcessor._rename_child_under_parent(
                dup, temp_root, short_name)

            # FBX smooth-mesh export is disabled for the whole session. Keep the
            # temporary ZBrush copy explicitly unsmoothed as a second safeguard;
            # the source mesh and its display-layer membership are untouched.
            if is_zbrush_mesh:
                for shape in cmds.listRelatives(dup, shapes=True, fullPath=True) or []:
                    if cmds.objExists(shape + ".displaySmoothMesh"):
                        try:
                            cmds.setAttr(shape + ".displaySmoothMesh", 0)
                        except Exception:
                            pass

            try:
                cmds.makeIdentity(
                    dup, apply=True, t=True, r=True, s=True, n=False, pn=True)
            except Exception as e:
                cmds.warning(
                    "Could not zero HP export transform '{}': {}".format(
                        short_name, e))
            return dup
        except Exception:
            # A failure before parenting would otherwise leave a duplicate next
            # to the artist's source mesh, outside every export cleanup root.
            if owned_copy and dup and cmds.objExists(dup):
                try:
                    cmds.delete(dup)
                except Exception:
                    pass
            raise

    @staticmethod
    def _combine_regular_hp_export_group(entries, temp_root, level):
        """Combine one subgroup's non-ZBrush HP copies for fast FBX writing."""
        duplicates = []
        combined = None
        first_short = entries[0]['short']
        first_lower = first_short.lower()
        marker = first_lower.rfind("_high")
        combined_name = "{}_high".format(
            first_short[:marker] if marker >= 0 else first_short)

        try:
            # One duplicate command is substantially cheaper than one command per
            # small mesh. Do not parent these staging copies under temp_root:
            # polyUnite deletes empty input parents and could delete the shared
            # cleanup root along with them.
            source_nodes = [entry['long'] for entry in entries]
            duplicates = cmds.duplicate(source_nodes, returnRootsOnly=True) or []
            if len(duplicates) != len(source_nodes):
                raise RuntimeError(
                    "Expected {} HP duplicates, got {}".format(
                        len(source_nodes), len(duplicates)))

            combined = cmds.polyUnite(
                duplicates, constructionHistory=False, mergeUVSets=1,
                name="BG_HP_Export_Zero_TempCombined#")[0]
            duplicates = [node for node in duplicates if cmds.objExists(node)]
            if duplicates:
                FinalExportProcessor._delete_temp_nodes(duplicates)
                duplicates = []

            if level > 0:
                cmds.polySmooth(
                    combined, divisions=int(level), keepBorder=False,
                    constructionHistory=False)

            combined = cmds.parent(combined, temp_root, absolute=True)[0]
            combined = FinalExportProcessor._rename_child_under_parent(
                combined, temp_root, combined_name)
            cmds.makeIdentity(
                combined, apply=True, t=True, r=True, s=True, n=False, pn=True)
            return combined
        except Exception:
            cleanup = list(duplicates)
            if combined and cmds.objExists(combined):
                cleanup.append(combined)
            FinalExportProcessor._delete_temp_nodes(cleanup)
            raise

    @staticmethod
    def _make_zero_transform_hp_export_copies(
            meshes, smooth_levels=None, reusable_temp_nodes=None,
            combine_non_zbrush=False):
        """Prepare HP copies with world-space geometry and zeroed transforms.

        Material-split HP meshes are already temporary. Reusing them here avoids
        the former second full duplicate before smoothing and FBX serialization.

        For a separate HP export, regular HP meshes that share the same bake
        prefix can be combined into one disconnected export-only mesh. ZBrush
        meshes are never included in that combine and remain separate, unsmoothed
        objects. The artist's source geometry is never modified.
        """
        if not meshes:
            return meshes, None

        reusable = set(reusable_temp_nodes or [])
        smooth_levels = smooth_levels or {}
        temp_root = cmds.group(em=True, name="BG_HP_Export_Zero_Temp#", world=True)
        temp_root = (cmds.ls(temp_root, long=True) or [temp_root])[0]
        prepared = []
        passthrough = []
        groups = {}
        group_order = []

        # Classify once before creating temporary geometry. This is both faster
        # and guarantees that ZBrush membership is read from the source object,
        # before duplication can lose display-layer connections.
        for mesh in meshes:
            if not mesh or not cmds.objExists(mesh):
                continue

            short_name = mesh.split('|')[-1]
            if "_high" not in short_name.lower():
                passthrough.append(mesh)
                continue

            try:
                long_mesh = cmds.ls(mesh, long=True)
                long_mesh = long_mesh[0] if long_mesh else mesh
                is_zbrush_mesh = FinalExportProcessor._is_zbrush_mesh(mesh)
                level = smooth_levels.get(long_mesh, smooth_levels.get(short_name.lower(), 0))
                short_lower = short_name.lower()
                prefix = short_lower.rsplit("_high", 1)[0]
                if not level and not is_zbrush_mesh:
                    level = smooth_levels.get("prefix:{}".format(prefix), 0)
                try:
                    level = max(0, int(level or 0))
                except Exception:
                    level = 0
                if is_zbrush_mesh:
                    if level:
                        cmds.warning("Skipped smooth for '{}' (ZBrush geometry)".format(mesh))
                    level = 0

                if prefix not in groups:
                    groups[prefix] = {'regular': [], 'zbrush': [], 'level': 0}
                    group_order.append(prefix)
                entry = {
                    'mesh': mesh,
                    'long': long_mesh,
                    'short': short_name,
                    'level': level,
                    'zbrush': is_zbrush_mesh,
                }
                bucket = 'zbrush' if is_zbrush_mesh else 'regular'
                groups[prefix][bucket].append(entry)
                if not is_zbrush_mesh:
                    groups[prefix]['level'] = max(groups[prefix]['level'], level)
            except Exception as e:
                cmds.warning("Could not classify HP export mesh '{}': {}".format(short_name, e))
                passthrough.append(mesh)

        for prefix in group_order:
            group = groups[prefix]
            regular = group['regular']
            zbrush = group['zbrush']
            can_combine = (
                combine_non_zbrush and len(regular) > 1 and
                not any(entry['mesh'] in reusable or entry['long'] in reusable
                        for entry in regular))

            if can_combine:
                try:
                    prepared.append(
                        FinalExportProcessor._combine_regular_hp_export_group(
                            regular, temp_root, group['level']))
                    regular = []
                except Exception as e:
                    cmds.warning(
                        "Could not combine regular HP subgroup '{}'; using "
                        "separate export copies: {}".format(prefix, e))

            # Fallback and single-mesh subgroups preserve the existing path.
            for entry in regular:
                try:
                    prepared.append(
                        FinalExportProcessor._prepare_individual_hp_export_copy(
                            entry, temp_root, reusable))
                except Exception as e:
                    cmds.warning(
                        "Could not create zero-transform HP export copy '{}': {}".format(
                            entry['short'], e))
                    prepared.append(entry['mesh'])

            # ZBrush is intentionally always processed one object at a time and
            # never receives polyUnite or polySmooth.
            for entry in zbrush:
                try:
                    prepared.append(
                        FinalExportProcessor._prepare_individual_hp_export_copy(
                            entry, temp_root, reusable))
                except Exception as e:
                    cmds.warning(
                        "Could not create zero-transform ZBrush export copy '{}': {}".format(
                            entry['short'], e))
                    prepared.append(entry['mesh'])

        prepared.extend(passthrough)

        return prepared, temp_root

    @staticmethod
    def _cleanup_zero_transform_hp_export_temps(extra_nodes=None):
        nodes = []
        for node in extra_nodes or []:
            if node and cmds.objExists(node):
                nodes.append(node)
        if extra_nodes is None:
            nodes.extend(cmds.ls("BG_HP_Export_Zero_Temp*", type='transform', long=True) or [])
        FinalExportProcessor._delete_temp_nodes(nodes)

    @staticmethod
    def _delete_temp_nodes(nodes):
        seen = set()
        for node in sorted(nodes or [], key=lambda n: n.count('|'), reverse=True):
            if not node or node in seen or not cmds.objExists(node):
                continue
            seen.add(node)
            try:
                cmds.delete(node)
            except Exception:
                pass

    @staticmethod
    def _cleanup_stale_export_temps():
        """Fail-safe cleanup once per outer export, not once per output file."""
        nodes = []
        for pattern in ("BG_HP_Export_Zero_Temp*", "BG_Export_Tri_Temp*", "BG_Material_Export_Temp*"):
            nodes.extend(cmds.ls(pattern, type='transform', long=True) or [])
        FinalExportProcessor._delete_temp_nodes(nodes)


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
        if not root_node or not cmds.objExists(root_node):
            return []
        shapes = cmds.listRelatives(
            root_node, allDescendents=True, fullPath=True,
            type='mesh', noIntermediate=True) or []
        transforms = []
        seen = set()
        for shape in shapes:
            parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
            if parents and parents[0] not in seen:
                seen.add(parents[0])
                transforms.append(parents[0])
        return transforms

    @staticmethod
    def _get_mesh_materials_and_faces(mesh_transform):
        """Return local face assignments without scanning global shading sets."""
        shapes = cmds.listRelatives(
            mesh_transform, shapes=True, fullPath=True,
            type='mesh', noIntermediate=True) or []
        if not shapes:
            return {}
        shape = shapes[0]
        if om is not None:
            try:
                selection = om.MSelectionList()
                selection.add(shape)
                dag = selection.getDagPath(0)
                mesh_fn = om.MFnMesh(dag)
                shaders, face_shader_indices = mesh_fn.getConnectedShaders(dag.instanceNumber())
                mat_dict = {}
                for shader_index, shader_obj in enumerate(shaders):
                    sg = om.MFnDependencyNode(shader_obj).name()
                    faces = [
                        face_id for face_id, assigned_index in enumerate(face_shader_indices)
                        if int(assigned_index) == shader_index
                    ]
                    if faces:
                        mat_dict[sg] = faces
                return mat_dict
            except Exception as exc:
                cmds.warning("Fast material lookup failed for '{}': {}; using compatibility path.".format(
                    mesh_transform, exc))

        return FinalExportProcessor._get_mesh_materials_and_faces_legacy(mesh_transform, shape)

    @staticmethod
    def _get_mesh_materials_and_faces_legacy(mesh_transform, shape):
        """Compatibility fallback for Maya builds where API lookup is unavailable."""
        sgs = list(set(cmds.listConnections(shape, type='shadingEngine') or []))
        mat_dict = {}
        total_faces = cmds.polyEvaluate(mesh_transform, face=True)

        for sg in sgs:
            members = cmds.sets(sg, q=True) or []
            mesh_components = []

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
                
            if mesh_transform in mesh_components or shape in mesh_components:
                mat_dict[sg] = list(range(total_faces))
                continue

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
        """Compatibility wrapper around the grouped material splitter."""
        new_lps, new_hps, _root = FinalExportProcessor._process_multimaterial_group(
            [lp_mesh], hp_meshes, {lp_mesh: mat_dict})
        return new_lps, new_hps

    @staticmethod
    def _process_multimaterial_group(lp_meshes, hp_meshes, material_maps):
        """Split LPs and duplicate HP once per unique material in the prefix.

        The former implementation duplicated every HP for every material of
        every LP, causing H * sum(materials-per-LP) copies. This version creates
        H * unique-materials copies and places every temporary under one root.
        """
        new_lp_meshes = []
        new_hp_meshes = []
        material_keys = sorted(set(
            sg for lp in lp_meshes for sg, faces in (material_maps.get(lp) or {}).items()
            if faces), key=lambda value: value.split('|')[-1].lower())
        if not material_keys:
            return new_lp_meshes, new_hp_meshes, None

        slot_by_material = {sg: index for index, sg in enumerate(material_keys, 1)}
        temp_root = cmds.group(em=True, name="BG_Material_Export_Temp#", world=True)
        temp_root = (cmds.ls(temp_root, long=True) or [temp_root])[0]

        for lp_mesh in lp_meshes:
            mat_dict = material_maps.get(lp_mesh) or {}
            short_name = lp_mesh.split('|')[-1]
            for sg in material_keys:
                face_indices = mat_dict.get(sg) or []
                if not face_indices:
                    continue
                new_lp_full = FinalExportProcessor._duplicate_under_temp_root(lp_mesh, temp_root)
                new_lp_full = cmds.rename(
                    new_lp_full, "{}_mat{}".format(short_name, slot_by_material[sg]))
                new_lp_full = FinalExportProcessor._resolve_child_under_parent(new_lp_full, temp_root)

                total_faces = cmds.polyEvaluate(new_lp_full, face=True)
                faces_to_keep = set(face_indices)
                faces_to_delete = [
                    "{}.f[{}]".format(new_lp_full, face_id)
                    for face_id in range(total_faces) if face_id not in faces_to_keep
                ]
                if faces_to_delete:
                    cmds.delete(faces_to_delete)

                uvs = cmds.ls("{}.map[*]".format(new_lp_full), flatten=True) or []
                if uvs:
                    try:
                        uv_bbox = cmds.polyEvaluate(new_lp_full, boundingBoxComponent2d=True)
                        if uv_bbox:
                            u_min, _u_max = uv_bbox[0]
                            v_min, _v_max = uv_bbox[1]
                            offset_u = -math.floor(u_min)
                            offset_v = -math.floor(v_min)
                            if offset_u != 0 or offset_v != 0:
                                cmds.polyEditUV(uvs, uValue=offset_u, vValue=offset_v)
                    except Exception as exc:
                        cmds.warning("UV shift failed for {}: {}".format(new_lp_full, exc))
                new_lp_meshes.append(new_lp_full)

        for sg in material_keys:
            slot = slot_by_material[sg]
            for hp in hp_meshes or []:
                if not cmds.objExists(hp):
                    continue
                hp_short = hp.split('|')[-1]
                dup_hp = FinalExportProcessor._duplicate_under_temp_root(hp, temp_root)
                dup_hp = cmds.rename(dup_hp, "{}_mat{}".format(hp_short, slot))
                new_hp_meshes.append(
                    FinalExportProcessor._resolve_child_under_parent(dup_hp, temp_root))

        return new_lp_meshes, new_hp_meshes, temp_root

    @staticmethod
    def build_chapter_snapshot(base_name, hp_main, lp_main, smooth_states=None,
                               final_mesh_widgets=None):
        """Collect and index a chapter once for reuse by every output file."""
        hp_all = FinalExportProcessor.get_valid_mesh_transforms(hp_main)
        lp_candidates = FinalExportProcessor.get_valid_mesh_transforms(lp_main)
        lp_all = [
            node for node in lp_candidates
            if "_low" in node.split('|')[-1].lower()
        ]

        hp_by_prefix = {}
        for node in hp_all:
            short_lower = node.split('|')[-1].lower()
            if "_high" in short_lower:
                prefix = short_lower.rsplit("_high", 1)[0]
                hp_by_prefix.setdefault(prefix, []).append(node)

        lp_by_prefix = {}
        prefix_order = []
        for node in lp_all:
            short_lower = node.split('|')[-1].lower()
            prefix = short_lower.rsplit("_low", 1)[0]
            if prefix not in lp_by_prefix:
                prefix_order.append(prefix)
            lp_by_prefix.setdefault(prefix, []).append(node)

        prefix_items = []
        if final_mesh_widgets:
            for item in final_mesh_widgets:
                prefix_items.append({
                    'prefix': item['full_prefix'].lower(),
                    'smooth_level': FinalExportProcessor._smooth_level_from_item(item),
                    'hp_nodes': item.get('hp_nodes') or []
                })
        else:
            states = smooth_states or {}
            for prefix in prefix_order:
                prefix_items.append({
                    'prefix': prefix,
                    'smooth_level': FinalExportProcessor._smooth_level_from_states(
                        states, base_name, prefix, default_level=0),
                    'hp_nodes': []
                })

        return {
            'base': base_name,
            'hp': hp_main,
            'lp': lp_main,
            'smooth': smooth_states or {},
            'hp_all': hp_all,
            'lp_meshes_all': lp_candidates,
            'lp_all': lp_all,
            'hp_by_prefix': hp_by_prefix,
            'lp_by_prefix': lp_by_prefix,
            'prefix_items': prefix_items,
            'material_faces': {},
        }

    @staticmethod
    def export_chapter(base_name, hp_main, lp_main, final_mesh_widgets,
                       parent_window=None, mode='both', export_dir=None,
                       smooth_states=None, extra_chapters=None,
                       export_name_override=None, prepared_chapters=None,
                       status_callback=None, file_callback=None,
                       cancel_check=None):
        """Prepare and export one file using reusable chapter snapshots."""
        if not export_dir:
            export_dirs = cmds.fileDialog2(fileMode=3, caption=bg_l10n.text("Select Export Directory"))
            if not export_dirs:
                return False
            export_dir = export_dirs[0]

        if prepared_chapters:
            chapters = list(prepared_chapters)
        else:
            chapters = [FinalExportProcessor.build_chapter_snapshot(
                base_name, hp_main, lp_main, smooth_states, final_mesh_widgets)]
            for chapter in extra_chapters or []:
                if 'hp_all' in chapter and 'lp_all' in chapter:
                    chapters.append(chapter)
                else:
                    chapters.append(FinalExportProcessor.build_chapter_snapshot(
                        chapter.get('base', 'Chapter'), chapter.get('hp'), chapter.get('lp'),
                        chapter.get('smooth') or {}, None))
        name_base = export_name_override or base_name

        hp_all = FinalExportProcessor._unique_existing(
            node for chapter in chapters for node in chapter.get('hp_all', []))
        lp_all = FinalExportProcessor._unique_existing(
            node for chapter in chapters for node in chapter.get('lp_all', []))

        hp_by_prefix = {}
        lp_by_prefix = {}
        lp_owner = {}
        prefixes_to_process = []
        seen_prefixes = set()
        for chapter in chapters:
            for prefix, nodes in chapter.get('hp_by_prefix', {}).items():
                hp_by_prefix.setdefault(prefix, []).extend(nodes)
            for prefix, nodes in chapter.get('lp_by_prefix', {}).items():
                lp_by_prefix.setdefault(prefix, []).extend(nodes)
            for node in chapter.get('lp_all', []):
                lp_owner[node] = chapter
            for item in chapter.get('prefix_items', []):
                prefix = item.get('prefix')
                if not prefix or prefix in seen_prefixes:
                    continue
                seen_prefixes.add(prefix)
                prefixes_to_process.append(item)

        def is_cancelled():
            try:
                return bool(cancel_check and cancel_check())
            except Exception:
                return False

        def notify_status(label):
            if status_callback:
                try:
                    status_callback(label)
                except Exception:
                    pass

        def notify_file(export_name):
            if file_callback:
                try:
                    file_callback(export_name)
                except Exception:
                    pass

        if mode == 'lp':
            if not lp_all or is_cancelled():
                return False
            export_name = "{}_LP".format(name_base).replace(".", "_")
            export_path = "{}/{}.fbx".format(export_dir.rstrip('/\\'), export_name).replace('\\', '/')
            notify_status(bg_l10n.text("Preparing: {name}").format(name=export_name))
            with FinalExportProcessor.export_session():
                if FinalExportProcessor._export_with_lp_triangulation_rollback(lp_all, export_path):
                    notify_file(export_name)
                    return export_name
            return False

        if not prefixes_to_process:
            prefixes_to_process.append({'prefix': '___dummy___', 'smooth_level': 0, 'hp_nodes': []})

        progress_dlg = None
        if status_callback is None and QtWidgets.QApplication.instance() is not None:
            progress_dlg = QtWidgets.QProgressDialog(
                bg_l10n.text("Exporting Chapter..."), bg_l10n.text("Cancel"),
                0, len(prefixes_to_process), parent_window)
            progress_dlg.setWindowModality(QtCore.Qt.WindowModal)
            progress_dlg.show()

        smooth_levels = {}
        temp_roots = []
        reusable_hp_nodes = set()
        reusable_lp_nodes = set()
        cancelled = False

        with FinalExportProcessor.export_session():
            try:
                hp_all_set = set(hp_all)
                exported_meshes = set()
                all_to_export = []

                for i, item in enumerate(prefixes_to_process):
                    if is_cancelled() or (progress_dlg and progress_dlg.wasCanceled()):
                        cancelled = True
                        break

                    full_prefix_lower = item['prefix']
                    try:
                        smooth_level_value = max(0, int(item.get('smooth_level') or 0))
                    except Exception:
                        smooth_level_value = 0

                    label = bg_l10n.text("Preparing: {name}").format(name=full_prefix_lower)
                    if progress_dlg:
                        progress_dlg.setLabelText(label)
                        progress_dlg.setValue(i)
                        QtWidgets.QApplication.processEvents()
                    elif i == 0 or i % 25 == 0:
                        notify_status(label)

                    hp_meshes = list(hp_by_prefix.get(full_prefix_lower, []))
                    lp_meshes = list(lp_by_prefix.get(full_prefix_lower, []))
                    item_hp_meshes = []
                    for hp_node in item.get('hp_nodes', []) or []:
                        for hp_match in cmds.ls(hp_node, long=True) or []:
                            if hp_match in hp_all_set:
                                item_hp_meshes.append(hp_match)
                    hp_meshes = FinalExportProcessor._unique_existing(hp_meshes + item_hp_meshes)

                    if not hp_meshes:
                        cmds.warning("Warning: No HP meshes found for LP prefix '{}'. Check naming.".format(
                            full_prefix_lower))

                    if smooth_level_value > 0 and mode in ('both', 'hp'):
                        key = "prefix:{}".format(full_prefix_lower)
                        smooth_levels[key] = max(smooth_levels.get(key, 0), smooth_level_value)

                    final_hp_for_export = list(hp_meshes)
                    final_lp_for_export = []

                    # Separate HP export does not need LP material splitting at
                    # all. Only a combined HP+LP file needs matched _mat copies.
                    if mode == 'both':
                        split_lps = []
                        material_maps = {}
                        for lp in lp_meshes:
                            owner = lp_owner.get(lp) or chapters[0]
                            cache = owner.setdefault('material_faces', {})
                            if lp not in cache:
                                cache[lp] = FinalExportProcessor._get_mesh_materials_and_faces(lp)
                            mat_dict = cache[lp]
                            if len(mat_dict) > 1:
                                split_lps.append(lp)
                                material_maps[lp] = mat_dict
                            else:
                                final_lp_for_export.append(lp)

                        if split_lps:
                            new_lps, new_hps, material_root = \
                                FinalExportProcessor._process_multimaterial_group(
                                    split_lps, hp_meshes, material_maps)
                            if material_root:
                                temp_roots.append(material_root)
                            reusable_lp_nodes.update(new_lps)
                            reusable_hp_nodes.update(new_hps)
                            final_lp_for_export.extend(new_lps)
                            final_hp_for_export = new_hps if new_hps else hp_meshes
                            exported_meshes.update(split_lps)
                            exported_meshes.update(hp_meshes)
                        else:
                            final_lp_for_export.extend(lp_meshes)

                    if mode in ('hp', 'both'):
                        for node in final_hp_for_export:
                            if node not in exported_meshes:
                                all_to_export.append(node)
                                exported_meshes.add(node)
                    if mode == 'both':
                        for node in final_lp_for_export:
                            if node not in exported_meshes:
                                all_to_export.append(node)
                                exported_meshes.add(node)

                if cancelled:
                    return False

                # Preserve unmatched geometry, but do not re-add originals that
                # were deliberately replaced by material-split temporary meshes.
                if mode in ('both', 'hp'):
                    for node in hp_all:
                        if node not in exported_meshes:
                            all_to_export.append(node)
                            exported_meshes.add(node)
                if mode == 'both':
                    for node in lp_all:
                        if node not in exported_meshes:
                            all_to_export.append(node)
                            exported_meshes.add(node)

                if not all_to_export:
                    return False

                export_nodes = all_to_export
                if mode in ('both', 'hp'):
                    export_nodes, temp_root = \
                        FinalExportProcessor._make_zero_transform_hp_export_copies(
                            all_to_export, smooth_levels, reusable_hp_nodes,
                            combine_non_zbrush=(mode == 'hp'))
                    if temp_root:
                        temp_roots.append(temp_root)

                suffix = ""
                if mode == 'hp':
                    suffix = "_HP"
                elif mode == 'lp':
                    suffix = "_LP"
                export_name = "{}{}".format(name_base, suffix).replace(".", "_")
                export_path = "{}/{}.fbx".format(export_dir.rstrip('/\\'), export_name).replace('\\', '/')
                notify_status(bg_l10n.text("Preparing: {name}").format(name=export_name))

                if FinalExportProcessor._export_with_lp_triangulation_rollback(
                        export_nodes, export_path,
                        reusable_temp_nodes=reusable_lp_nodes):
                    if progress_dlg:
                        progress_dlg.setValue(len(prefixes_to_process))
                        QtWidgets.QApplication.processEvents()
                    notify_file(export_name)
                    return export_name
                return False
            except Exception as exc:
                cmds.warning("Export failed: {}".format(exc))
                return False
            finally:
                FinalExportProcessor._delete_temp_nodes(reversed(temp_roots))
                cmds.select(clear=True)
                if progress_dlg:
                    progress_dlg.close()

        return False
