# -*- coding: utf-8 -*-
"""Executed by Toolbag, not CPython. Writes a machine-readable smoke result."""
from __future__ import print_function

import json
import os
import runpy
import sys
import traceback

import mset


fixture_dir = os.environ.get("BG_MARMOSET_FIXTURE_DIR", "")
plugin_path = os.environ.get("BG_MARMOSET_PLUGIN_PATH", "")
result_path = os.environ.get("BG_MARMOSET_RESULT", "")
result = {"ok": False}

try:
    mset.newScene()
    namespace = runpy.run_path(plugin_path)
    bridge = namespace["BRIDGE"]
    simplified_ui_ok = (
        not hasattr(bridge, "book_list") and
        not hasattr(bridge, "reset_selected") and
        not hasattr(bridge, "bake_selected_book") and
        not hasattr(bridge, "bake_all_books"))
    manifest_path = os.path.join(fixture_dir, "BakeGroups_Marmoset.json")
    autoload = "--bake-groups-manifest" in sys.argv
    if autoload:
        if os.path.normcase(os.path.abspath(bridge.manifest_path)) != \
                os.path.normcase(os.path.abspath(manifest_path)):
            raise RuntimeError("Bridge did not auto-load the CLI manifest")
    else:
        bridge.load_manifest(manifest_path, sync_geometry=True)
    # A second sync must use the existing ExternalObject references, not create
    # duplicate bake groups or duplicate geometry.
    bridge.load_manifest(manifest_path, sync_geometry=True)
    idle_changes = bridge.apply_manifest_settings()
    focus_apply_calls = [0]
    original_apply = bridge.apply_manifest_settings

    def counted_apply():
        focus_apply_calls[0] += 1
        return original_apply()

    bridge.apply_manifest_settings = counted_apply
    bridge.on_regain_focus()
    bridge.apply_manifest_settings = original_apply
    meshes = {
        obj.name: obj for obj in mset.getAllObjects()
        if isinstance(obj, mset.MeshObject)
        if obj.name in bridge.record_by_name
    }
    bakers = [
        obj for obj in mset.getAllObjects()
        if isinstance(obj, mset.BakerObject)
        if obj.name == "BG | Book"
    ]
    levels = {}
    for name, mesh in meshes.items():
        record = bridge.record_by_name[name]
        levels[name] = {
            "role": record.get("role"),
            "zbrush": bool(record.get("zbrush")),
            "enabled": bool(mesh.subdivisionEnabled),
            "level": int(mesh.subdivisionLevel),
            "mode": str(mesh.subdivisionMode),
            "sharpen_corners": bool(mesh.subdivisionSharpenCorners),
        }
    default_visibility = {
        name: bool(mesh.visible) for name, mesh in meshes.items()
    }
    default_ancestors = {}
    for name, mesh in meshes.items():
        ancestors = []
        parent = mesh.parent
        while parent is not None:
            ancestors.append({"name": parent.name, "visible": bool(parent.visible)})
            parent = parent.parent
        default_ancestors[name] = ancestors
    default_visibility_ok = all(
        default_visibility[name] == (
            bridge.record_by_name[name].get("role") == "high")
        for name in meshes
    )
    bridge.toggle_hp_visibility()
    bridge.toggle_lp_visibility()
    toggled_visibility_ok = all(
        bool(mesh.visible) == (
            bridge.record_by_name[name].get("role") == "low")
        for name, mesh in meshes.items()
    )
    toggled_labels_ok = (
        bridge.hp_visibility_button.text == "Show HP" and
        bridge.lp_visibility_button.text == "Hide LP")
    bridge.load_manifest(manifest_path, sync_geometry=True)
    # Toolbag may replace mesh objects during a package sync; reacquire handles.
    meshes = dict(bridge.mesh_by_name)
    same_package_visibility_ok = all(
        bool(mesh.visible) == (
            bridge.record_by_name[name].get("role") == "low")
        for name, mesh in meshes.items()
    )
    bridge.toggle_hp_visibility()
    bridge.toggle_lp_visibility()
    restored_visibility_ok = all(
        bool(mesh.visible) == (
            bridge.record_by_name[name].get("role") == "high")
        for name, mesh in meshes.items()
    )
    regular_hp = next(
        mesh for name, mesh in meshes.items()
        if bridge.record_by_name[name].get("role") == "high"
        and not bridge.record_by_name[name].get("zbrush"))
    mset.setSelectedObjects([regular_hp])
    bridge.level.value = 3
    bridge.apply_selected()
    bridge.apply_manifest_settings()
    override_level = int(regular_hp.subdivisionLevel)
    external_count = len([
        obj for obj in mset.getAllObjects()
        if isinstance(obj, mset.ExternalObject)
    ])
    hierarchy = [
        {"type": type(obj).__name__, "name": obj.name}
        for obj in namespace["_walk"](bakers[0])
    ] if bakers else []
    result = {
        "ok": (
            len(bakers) == 1 and len(meshes) == 3 and
            override_level == 3 and idle_changes == 0 and
            focus_apply_calls[0] == 0 and default_visibility_ok and
            toggled_visibility_ok and toggled_labels_ok and
            same_package_visibility_ok and restored_visibility_ok and
            simplified_ui_ok),
        "bakers": len(bakers),
        "meshes": len(meshes),
        "external_objects": external_count,
        "override_level": override_level,
        "default_visibility": default_visibility,
        "default_ancestors": default_ancestors,
        "default_visibility_ok": default_visibility_ok,
        "toggled_visibility_ok": toggled_visibility_ok,
        "toggled_labels_ok": toggled_labels_ok,
        "same_package_visibility_ok": same_package_visibility_ok,
        "restored_visibility_ok": restored_visibility_ok,
        "simplified_ui_ok": simplified_ui_ok,
        "idle_changes": idle_changes,
        "focus_apply_calls": focus_apply_calls[0],
        "hierarchy": hierarchy,
        "levels": levels,
    }
    if not result["ok"]:
        raise RuntimeError("Unexpected Toolbag hierarchy: {}".format(result))
except Exception as exc:
    result = {
        "ok": False,
        "error": str(exc),
        "traceback": traceback.format_exc(),
    }
finally:
    if result_path:
        with open(result_path, "w", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
    mset.quit(0 if result.get("ok") else 1)
