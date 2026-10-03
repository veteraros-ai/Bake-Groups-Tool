"""Blender producer for the existing Bake Groups Toolbag Bridge schema."""

from __future__ import annotations

from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid

import bpy

from .export_service import ExportTask, _export_fbx, _safe_name, _side_objects, resolve_scope
from .mesh_tools import is_zbrush_object
from .object_repository import ObjectRepository


SCHEMA_NAME = "bake-groups-toolbag"
SCHEMA_VERSION = 1
MANIFEST_NAME = "BakeGroups_Marmoset.json"


def _plugin_source():
    return Path(__file__).resolve().parents[2] / "toolbag_plugin" / "Bake Groups Bridge" / "__main__.py"


def _plugin_destination():
    return (Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") /
            "Marmoset Toolbag 5" / "plugins" / "Bake Groups Bridge" / "__main__.py")


def bridge_status():
    source, destination = _plugin_source(), _plugin_destination()
    if not source.is_file():
        raise RuntimeError("Bundled Bake Groups Bridge plugin is missing")
    if not destination.is_file():
        return "missing"
    return ("current" if hashlib.sha256(source.read_bytes()).digest() ==
            hashlib.sha256(destination.read_bytes()).digest() else "outdated")


def install_bridge():
    source, destination = _plugin_source(), _plugin_destination()
    if bridge_status() == "current":
        return str(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        backup = destination.with_name(destination.name + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(destination, backup)
    with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return str(destination)


def _source_id(obj, used):
    value = str(obj.get("bake_tools_source_id", ""))
    if not value or (value in used and used[value] is not obj):
        value = "blender:" + uuid.uuid4().hex
        obj["bake_tools_source_id"] = value
    used[value] = obj
    return value


def _chapter_records(pair, state, used):
    records, objects, lp = [], [], []
    seen = set()
    for subgroup in pair.subgroups:
        for role in ("high", "low"):
            if not (state.export_include_hp if role == "high" else state.export_include_lp):
                continue
            side = "HP" if role == "high" else "LP"
            for obj in ObjectRepository.valid_members(subgroup, side):
                if obj.as_pointer() in seen:
                    continue
                seen.add(obj.as_pointer())
                source_id = _source_id(obj, used)
                zbrush = role == "high" and is_zbrush_object(state, obj)
                records.append({
                    "source_id": source_id,
                    "source_name": obj.name,
                    "export_name": obj.name,
                    "role": role,
                    "group": _safe_name(subgroup.name),
                    "subdivision_level": (0 if role == "low" or zbrush else int(subgroup.smooth_level)),
                    "subdivision_mode": "Catmull-Clark",
                    "sharpen_corners": False,
                    "zbrush": bool(zbrush),
                })
                objects.append(obj)
                if role == "low":
                    lp.append(obj)
    for side, role in (("HP", "high"), ("LP", "low")):
        if not (state.export_include_hp if side == "HP" else state.export_include_lp):
            continue
        for obj in _side_objects(pair, side):
            if obj.as_pointer() in seen:
                continue
            seen.add(obj.as_pointer())
            source_id = _source_id(obj, used)
            records.append({"source_id": source_id, "source_name": obj.name,
                            "export_name": obj.name, "role": role,
                            "group": _safe_name(pair.name), "subdivision_level": 0,
                            "subdivision_mode": "Catmull-Clark", "sharpen_corners": False,
                            "zbrush": bool(role == "high" and is_zbrush_object(state, obj))})
            objects.append(obj)
            if role == "low":
                lp.append(obj)
    return records, tuple(objects), tuple(lp)


def _atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(payload, stream, ensure_ascii=False, indent=2)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def export_package(context, state, active_pair, directory, progress=None):
    pairs = resolve_scope(state, active_pair)
    root = Path(bpy.path.abspath(str(directory))).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    models = root / "models"
    chapters, books, used = [], {}, {}
    with tempfile.TemporaryDirectory(prefix="bake_groups_marmoset_", dir=root) as staging:
        staged_models = Path(staging) / "models"
        staged_models.mkdir()
        for index, pair in enumerate(pairs):
            records, objects, lp_objects = _chapter_records(pair, state, used)
            if not records:
                continue
            filename = "{}_{}_Marmoset.fbx".format(_safe_name(pair.name), pair.item_id[:8])
            output = staged_models / filename
            if progress:
                progress.update(int(index * 80 / max(1, len(pairs))), "Marmoset: {}".format(pair.name))
            task = ExportTask(pair.name, objects, lp_objects, str(output))
            _export_fbx(context, task, (pair,), state, bool(state.export_lp_triangulate),
                        apply_smoothing=False)
            chapter_id = str(pair.item_id)
            book = str(pair.book or "Ungrouped")
            chapters.append({"id": chapter_id, "name": pair.name, "book": book,
                             "model": "models/" + filename, "meshes": records})
            books.setdefault(book, []).append(chapter_id)
        if not chapters:
            raise ValueError("No HP/LP meshes to export for Marmoset")
        manifest = {"schema": SCHEMA_NAME, "schema_version": SCHEMA_VERSION,
                    "created_utc": int(time.time()),
                    "source_scene": bpy.data.filepath.replace("\\", "/"),
                    "overrides_file": "BakeGroups_Marmoset.overrides.json",
                    "books": [{"name": name, "chapters": ids} for name, ids in sorted(books.items())],
                    "chapters": chapters}
        models.mkdir(parents=True, exist_ok=True)
        for output in sorted(staged_models.iterdir()):
            os.replace(output, models / output.name)
        path = root / MANIFEST_NAME
        _atomic_json(path, manifest)
    if progress:
        progress.update(100, "Marmoset package ready")
    return str(path)


def _toolbag_executable():
    choices = [os.environ.get("BG_TOOLBAG_EXE")]
    for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)")):
        if base:
            choices.extend((os.path.join(base, "Marmoset", "Toolbag 5", "toolbag.exe"),
                            os.path.join(base, "Marmoset Toolbag 5", "toolbag.exe")))
    found = shutil.which("toolbag.exe")
    if found:
        choices.append(found)
    return next((path for path in choices if path and os.path.isfile(path)), None)


def launch_bridge(manifest_path):
    if bridge_status() != "current":
        raise RuntimeError("Bake Groups Bridge is not installed")
    executable = _toolbag_executable()
    if not executable:
        raise RuntimeError("Marmoset Toolbag 5 executable was not found")
    return subprocess.Popen([executable, str(_plugin_destination()),
                             "--bake-groups-manifest", str(Path(manifest_path).resolve())],
                            cwd=str(Path(executable).parent), close_fds=True)
