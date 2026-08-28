"""Read-only adapter from Blender RNA properties to UI view models."""

from __future__ import annotations

import bpy

from .domain import ChapterView, ManagerView, MatcherClusterView, ObjectRefView, SubgroupView
from .cage_service import cage_objects
from .object_repository import ObjectRepository
from .properties import ensure_state_ids


class BlenderStateStore:
    """The only read path used by the Qt window.

    Operators own writes.  Keeping reads here makes scene switching and
    undo/redo refreshes deterministic and keeps Qt free from Blender RNA data.
    """

    @staticmethod
    def settings():
        scene = bpy.context.scene
        if scene is None or not hasattr(scene, "bake_tools_settings"):
            return None
        state = scene.bake_tools_settings
        ensure_state_ids(state)
        return state

    def snapshot(self):
        state = self.settings()
        if state is None:
            return None
        chapters = tuple(
            ChapterView(
                item_id=pair.item_id,
                name=pair.name,
                book=pair.book,
                hp_object=ObjectRepository.root_name(pair, "HP"),
                lp_object=ObjectRepository.root_name(pair, "LP"),
                hp_root_kind=ObjectRepository.root_kind(pair, "HP"),
                lp_root_kind=ObjectRepository.root_kind(pair, "LP"),
                visible=pair.visible,
                groups_visible=pair.groups_visible,
                expanded=pair.expanded,
                subgroups=tuple(
                    SubgroupView(
                        item_id=subgroup.item_id,
                        name=subgroup.name,
                        visible=subgroup.visible,
                        locked=subgroup.locked,
                        hp_members=tuple(
                            ObjectRefView(ref.target.name, ref.target.type)
                            for ref in subgroup.hp_members if ref.target is not None
                        ),
                        lp_members=tuple(
                            ObjectRefView(ref.target.name, ref.target.type)
                            for ref in subgroup.lp_members if ref.target is not None
                        ),
                        smooth_level=subgroup.smooth_level,
                        cage_override=subgroup.cage_override,
                        color_index=subgroup.color_index,
                        custom_color=(tuple(subgroup.custom_color) if subgroup.use_custom_color else None),
                    )
                    for subgroup in pair.subgroups
                ),
                matcher_clusters=tuple(
                    MatcherClusterView(
                        item_id=cluster.item_id,
                        name=cluster.name,
                        title=cluster.title,
                        linked=cluster.linked,
                        already_grouped=cluster.already_grouped,
                        score=cluster.score,
                        hp_members=tuple(
                            ObjectRefView(ref.target.name, ref.target.type)
                            for ref in cluster.hp_members if ref.target is not None
                        ),
                        lp_members=tuple(
                            ObjectRefView(ref.target.name, ref.target.type)
                            for ref in cluster.lp_members if ref.target is not None
                        ),
                    )
                    for cluster in pair.matcher_clusters
                ),
            )
            for pair in state.pairs
        )
        active_pair = next((pair for pair in state.pairs if pair.item_id == state.active_pair_id), None)
        if state.export_scope == "BOOK" and active_pair is not None:
            export_pairs = tuple(pair for pair in state.pairs if pair.book == active_pair.book)
        elif state.export_scope == "ALL":
            export_pairs = tuple(state.pairs)
        else:
            export_pairs = (active_pair,) if active_pair is not None else ()
        export_has_cage = any(cage_objects(pair) for pair in export_pairs)
        return ManagerView(
            scene_name=bpy.context.scene.name,
            hp_object=(state.hp_collection.name if state.hp_root_kind == "COLLECTION" and state.hp_collection is not None
                       else state.hp_root.name if state.hp_root is not None else state.hp_object),
            lp_object=(state.lp_collection.name if state.lp_root_kind == "COLLECTION" and state.lp_collection is not None
                       else state.lp_root.name if state.lp_root is not None else state.lp_object),
            hp_root_kind=state.hp_root_kind or ("OBJECT" if state.hp_root is not None else ""),
            lp_root_kind=state.lp_root_kind or ("OBJECT" if state.lp_root is not None else ""),
            active_pair_id=state.active_pair_id,
            active_subgroup=state.active_subgroup,
            chapters=chapters,
            group_name=state.group_name,
            show_algorithm=state.show_algorithm,
            color_subgroups=state.color_subgroups,
            keep_hp_structure=state.keep_hp_structure,
            hp_visible=state.hp_visible,
            lp_visible=state.lp_visible,
            cage_visible=(active_pair.cage_visible if active_pair is not None else True),
            active_has_cage=bool(active_pair is not None and cage_objects(active_pair)),
            groups_visible=state.groups_visible,
            final_view=state.final_view,
            preview_smoothing=state.preview_smoothing,
            find_mode=state.find_mode,
            language=state.language,
            hp_strategy=state.hp_strategy,
            optimization=state.optimization,
            collision_pct=state.collision_pct,
            ignore_floaters=state.ignore_floaters,
            adjacent_link=state.adjacent_link,
            link_vertex=state.link_vertex,
            link_distance=state.link_distance,
            matcher_tolerance=state.matcher_tolerance,
            matcher_min_hp_lp=state.matcher_min_hp_lp,
            matcher_mode=state.matcher_mode,
            strict_geo_check=state.strict_geo_check,
            cage_wire=state.cage_wire,
            cage_status=state.cage_status,
            export_scope=state.export_scope,
            export_include_hp=state.export_include_hp,
            export_include_lp=state.export_include_lp,
            export_include_cage=state.export_include_cage,
            export_has_cage=export_has_cage,
            export_lp_triangulate=state.export_lp_triangulate,
            export_files=state.export_files,
            export_by_material=state.export_by_material,
            export_lp_one_file=state.export_lp_one_file,
            export_directory=state.export_directory,
            export_status=state.export_status,
            log_text=state.log_text,
        )
