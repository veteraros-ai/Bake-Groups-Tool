"""Maya-compatible debug log and Blender support-package generation."""

from __future__ import annotations

from datetime import datetime
import json
import platform
from pathlib import Path
import sys
import zipfile

import bpy

from . import native_core


ADDON_VERSION = "1.0.0"
PLUGIN_NAME = "Bake Groups Tool"
AUTHOR_NAME = "Veteraros AI"


def _ref_names(refs):
    result = []
    for ref in refs:
        try:
            result.append(ref.target.name if ref.target else (ref.last_name or "<lost>"))
        except ReferenceError:
            result.append(ref.last_name or "<lost>")
    return result


def _root_name(pair, side):
    kind = str(getattr(pair, side.lower() + "_root_kind", "OBJECT") or "OBJECT")
    pointer = getattr(pair, side.lower() + ("_collection" if kind == "COLLECTION" else "_root"), None)
    try:
        return kind, pointer.name if pointer else "<missing>"
    except ReferenceError:
        return kind, "<lost>"


def pair_payload(pair):
    hp_kind, hp_root = _root_name(pair, "HP")
    lp_kind, lp_root = _root_name(pair, "LP")
    return {
        "id": pair.item_id,
        "name": pair.name,
        "book": pair.book,
        "visible": bool(pair.visible),
        "hp_root": {"kind": hp_kind, "name": hp_root},
        "lp_root": {"kind": lp_kind, "name": lp_root},
        "subgroups": [
            {
                "id": subgroup.item_id,
                "name": subgroup.name,
                "visible": bool(subgroup.visible),
                "locked": bool(subgroup.locked),
                "smooth_level": int(subgroup.smooth_level),
                "color_index": int(subgroup.color_index),
                "custom_color": (list(subgroup.custom_color) if subgroup.use_custom_color else None),
                "hp_members": _ref_names(subgroup.hp_members),
                "lp_members": _ref_names(subgroup.lp_members),
            }
            for subgroup in pair.subgroups
        ],
        "matcher_clusters": [
            {
                "id": cluster.item_id,
                "name": cluster.name,
                "title": cluster.title,
                "linked": bool(cluster.linked),
                "score": float(cluster.score),
                "hp_members": _ref_names(cluster.hp_members),
                "lp_members": _ref_names(cluster.lp_members),
            }
            for cluster in pair.matcher_clusters
        ],
    }


def scene_snapshot(state):
    lines = []
    active_id = str(state.active_pair_id or "")
    active = next((pair for pair in state.pairs if pair.item_id == active_id), None)
    lines.append("Active chapter: {}".format(active.name if active else "None"))
    lines.append("Chapters: {}".format(len(state.pairs)))
    for pair in state.pairs:
        hp_kind, hp_root = _root_name(pair, "HP")
        lp_kind, lp_root = _root_name(pair, "LP")
        lines.append("{}{} | book={} | HP {}:{} | LP {}:{} | groups={}".format(
            "* " if pair.item_id == active_id else "  ", pair.name, pair.book or "-",
            hp_kind, hp_root, lp_kind, lp_root, len(pair.subgroups),
        ))
        for subgroup in pair.subgroups:
            hp = _ref_names(subgroup.hp_members); lp = _ref_names(subgroup.lp_members)
            lines.append("    {} | visible={} locked={} smooth={} | HP={} LP={}".format(
                subgroup.name, bool(subgroup.visible), bool(subgroup.locked),
                int(subgroup.smooth_level), len(hp), len(lp),
            ))
            if hp:
                lines.append("      HP: " + ", ".join(hp[:40]))
            if lp:
                lines.append("      LP: " + ", ".join(lp[:40]))
        for cluster in pair.matcher_clusters:
            lines.append("    Matcher {} | linked={} score={:.3f} | HP={} LP={}".format(
                cluster.name, bool(cluster.linked), float(cluster.score),
                len(_ref_names(cluster.hp_members)), len(_ref_names(cluster.lp_members)),
            ))
    return lines


def collection_snapshot():
    lines = []
    for collection in sorted(bpy.data.collections, key=lambda item: item.name.casefold()):
        object_names = [obj.name for obj in collection.objects]
        child_names = [child.name for child in collection.children]
        lines.append("{} | objects={} children={} hidden_viewport={} hidden_render={}".format(
            collection.name, len(object_names), len(child_names),
            bool(collection.hide_viewport), bool(collection.hide_render),
        ))
        if object_names:
            lines.append("  objects: " + ", ".join(object_names[:40]))
        if child_names:
            lines.append("  children: " + ", ".join(child_names[:40]))
    return lines or ["(no collections)"]


def environment_snapshot(state):
    scene_path = bpy.data.filepath or "Untitled"
    scene = bpy.context.scene
    from .mesh_tools import duplicate_check_tolerance
    return {
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "plugin_name": PLUGIN_NAME,
        "plugin_version": ADDON_VERSION,
        "author": AUTHOR_NAME,
        "language": state.language,
        "blender_version": bpy.app.version_string,
        "blender_build_hash": bpy.app.build_hash.decode("ascii", "replace") if isinstance(bpy.app.build_hash, bytes) else str(bpy.app.build_hash),
        "python_version": sys.version,
        "platform": platform.platform(),
        "native_core": native_core.backend_name(),
        "scene": scene_path,
        "active_chapter_id": state.active_pair_id or None,
        "units": {
            "system": scene.unit_settings.system,
            "scale_length": float(scene.unit_settings.scale_length),
            "duplicate_tolerance_bu": duplicate_check_tolerance(scene),
            "duplicate_tolerance_reference": "Maya 0.001 cm",
        },
        "final_view": bool(state.final_view),
        "preview_smoothing": bool(state.preview_smoothing),
        "settings": {
            "hp_strategy": state.hp_strategy,
            "optimization": state.optimization,
            "collision_pct": int(state.collision_pct),
            "ignore_floaters": bool(state.ignore_floaters),
            "adjacent_link": bool(state.adjacent_link),
            "link_vertex": int(state.link_vertex),
            "link_distance": float(state.link_distance),
            "color_subgroups": bool(state.color_subgroups),
            "keep_hp_structure": bool(state.keep_hp_structure),
        },
    }


def _debug_report(state):
    environment = environment_snapshot(state)
    action_lines = state.action_history.splitlines() or ["(no recorded user actions)"]
    debug_lines = state.debug_text.splitlines() or ["(Analyze/Assign debug is not available for this session.)"]
    snapshot = scene_snapshot(state)
    report = [
        "Bake Groups Debug Log",
        "Saved: {}".format(environment["saved_at"]),
        "Scene: {}".format(environment["scene"]),
        "Plugin: {} {}".format(PLUGIN_NAME, ADDON_VERSION),
        "Blender: {}".format(environment["blender_version"]), "",
        "=== Visible Log ===", state.log_text or "(empty)", "",
        "=== User Actions ===", *action_lines, "",
        "=== Current Scene Snapshot ===", *snapshot, "",
        "=== Analyze / Assign Debug ===", *debug_lines,
    ]
    return report


def save_debug_log(filepath, state):
    path = Path(filepath).expanduser()
    if not path.suffix:
        path = path.with_suffix(".txt")
    path.write_text("\n".join(_debug_report(state)), encoding="utf-8")
    return str(path)


def save_support_package(filepath, state):
    path = Path(filepath).expanduser()
    if not path.suffix:
        path = path.with_suffix(".zip")
    environment = environment_snapshot(state)
    snapshot = scene_snapshot(state)
    collections = collection_snapshot()
    pairs = [pair_payload(pair) for pair in state.pairs]
    file_names = [
        "support_report.txt", "visible_log.txt", "user_actions.txt",
        "scene_snapshot.txt", "collections.txt", "analyze_assign_debug.txt",
        "environment.json", "session_pairs.json",
    ]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("support_report.txt", "\n".join(_debug_report(state)))
        archive.writestr("visible_log.txt", state.log_text or "")
        archive.writestr("user_actions.txt", state.action_history or "")
        archive.writestr("scene_snapshot.txt", "\n".join(snapshot))
        archive.writestr("collections.txt", "\n".join(collections))
        archive.writestr("analyze_assign_debug.txt", state.debug_text or "")
        archive.writestr("environment.json", json.dumps(environment, indent=2, ensure_ascii=False))
        archive.writestr("session_pairs.json", json.dumps(pairs, indent=2, ensure_ascii=False))
        update_manifest = Path(__file__).resolve().parents[2] / "update_manifest.json"
        if update_manifest.is_file():
            archive.write(update_manifest, "update_manifest.json")
            file_names.append("update_manifest.json")
        archive.writestr("package_manifest.json", json.dumps({
            "plugin": PLUGIN_NAME, "version": ADDON_VERSION,
            "files": file_names,
        }, indent=2))
    return str(path)
