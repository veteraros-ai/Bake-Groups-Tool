# -*- coding: utf-8 -*-
"""Maya-side exporter for the Bake Groups -> Marmoset Toolbag bridge.

The bridge deliberately exports base HP meshes without Maya polySmooth.  Each
mesh remains a separate object so Toolbag can apply per-object subdivision.
Cage data is intentionally outside the bridge schema.
"""
from __future__ import print_function, division, absolute_import

import io
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time

import maya.cmds as cmds

import bg_final_export


SCHEMA_NAME = "bake-groups-toolbag"
SCHEMA_VERSION = 1
MANIFEST_NAME = "BakeGroups_Marmoset.json"
OVERRIDES_NAME = "BakeGroups_Marmoset.overrides.json"


def toolbag_plugin_status():
    """Report whether the bundled bridge matches the per-user Toolbag 5 plugin."""
    source = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "toolbag_plugin",
        "Bake Groups Bridge", "__main__.py")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data or not os.path.isfile(source):
        raise RuntimeError("Bundled Marmoset Toolbag plugin was not found.")
    destination_dir = os.path.join(
        local_app_data, "Marmoset Toolbag 5", "plugins", "Bake Groups Bridge")
    destination = os.path.join(destination_dir, "__main__.py")
    if not os.path.isfile(destination):
        state = "missing"
    else:
        def digest(path):
            value = hashlib.sha256()
            with open(path, "rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    value.update(chunk)
            return value.digest()
        state = "current" if digest(source) == digest(destination) else "outdated"
    return {"state": state, "source": source, "destination": destination}


def install_toolbag_plugin():
    """Install after UI consent; preserve any existing user plugin as a backup."""
    status = toolbag_plugin_status()
    destination = status["destination"]
    if status["state"] == "current":
        return destination
    folder = os.path.dirname(destination)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=folder, prefix=".bg_bridge_",
                                         suffix=".tmp", delete=False) as stream:
            temp_path = stream.name
        shutil.copy2(status["source"], temp_path)
        if status["state"] == "outdated":
            backup = destination + ".bak-" + time.strftime("%Y%m%d-%H%M%S") + \
                "-{:06d}".format(int(time.time() * 1000000) % 1000000)
            shutil.copy2(destination, backup)
        os.replace(temp_path, destination)
        return destination
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


def toolbag_executable_path():
    """Find Toolbag 5 without searching or launching unrelated executables."""
    candidates = [os.environ.get("BG_TOOLBAG_EXE")]
    for program_files in (os.environ.get("PROGRAMFILES"),
                          os.environ.get("PROGRAMFILES(X86)")):
        if program_files:
            candidates.extend((
                os.path.join(program_files, "Marmoset", "Toolbag 5", "toolbag.exe"),
                os.path.join(program_files, "Marmoset Toolbag 5", "toolbag.exe"),
            ))
    from_path = shutil.which("toolbag.exe")
    if from_path:
        candidates.append(from_path)
    try:
        import winreg
        registry_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\toolbag.exe"
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, registry_path) as key:
                    candidates.append(winreg.QueryValueEx(key, None)[0])
            except OSError:
                pass
    except ImportError:
        pass
    for path in candidates:
        if path and os.path.isfile(path):
            return os.path.abspath(path)
    return None


def launch_toolbag_bridge(manifest_path):
    """Open a separate Toolbag session with this completed package loaded."""
    manifest_path = os.path.abspath(manifest_path)
    if not os.path.isfile(manifest_path):
        raise RuntimeError("Marmoset package manifest does not exist: {}".format(manifest_path))
    status = toolbag_plugin_status()
    if status["state"] != "current":
        raise RuntimeError("Bake Groups Bridge is not installed or needs updating.")
    executable = toolbag_executable_path()
    if not executable:
        raise RuntimeError("Marmoset Toolbag 5 executable was not found.")
    return subprocess.Popen(
        [executable, status["destination"], "--bake-groups-manifest", manifest_path],
        cwd=os.path.dirname(executable), close_fds=True)


def _safe_name(value, fallback="Item"):
    value = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or ""))
    return value.strip("_") or fallback


def _source_id(node):
    values = cmds.ls(node, uuid=True) or []
    if values:
        return str(values[0])
    return str(node)


def _id_token(source_id):
    return re.sub(r"[^A-Za-z0-9]+", "", str(source_id))[:16].lower() or "unknown"


def _atomic_write_json(path, payload):
    folder = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(folder):
        os.makedirs(folder)
    temp_path = path + ".tmp"
    with io.open(temp_path, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.flush()
        try:
            os.fsync(stream.fileno())
        except Exception:
            pass
    os.replace(temp_path, path)


class MarmosetPackageExporter(object):
    """Write FBX model references and an atomic Toolbag bridge manifest."""

    @staticmethod
    def _group_level(snapshot, prefix):
        for item in snapshot.get("prefix_items", []) or []:
            if str(item.get("prefix", "")).lower() == str(prefix).lower():
                try:
                    return max(0, min(4, int(item.get("smooth_level") or 0)))
                except Exception:
                    return 0
        level = bg_final_export.FinalExportProcessor._smooth_level_from_states(
            snapshot.get("smooth") or {}, snapshot.get("base", ""), prefix,
            default_level=0)
        return max(0, min(4, int(level or 0)))

    @staticmethod
    def _mesh_record(node, role, group_name, smooth_level, zbrush=False):
        source_id = _source_id(node)
        role_token = "high" if role == "high" else "low"
        export_name = "{}_{}_bg{}".format(
            _safe_name(group_name), role_token, _id_token(source_id))
        return {
            "source_id": source_id,
            "source_name": node.split("|")[-1],
            "export_name": export_name,
            "role": role,
            "group": _safe_name(group_name),
            "subdivision_level": 0 if zbrush else int(smooth_level),
            "subdivision_mode": "Catmull-Clark",
            "sharpen_corners": False,
            "zbrush": bool(zbrush),
        }

    @staticmethod
    def _chapter_records(snapshot):
        records = []
        seen = set()
        prefixes = sorted(set(
            list((snapshot.get("hp_by_prefix") or {}).keys()) +
            list((snapshot.get("lp_by_prefix") or {}).keys())))
        for prefix in prefixes:
            level = MarmosetPackageExporter._group_level(snapshot, prefix)
            for node in snapshot.get("hp_by_prefix", {}).get(prefix, []) or []:
                if node in seen or not cmds.objExists(node):
                    continue
                seen.add(node)
                records.append(MarmosetPackageExporter._mesh_record(
                    node, "high", prefix, level,
                    bg_final_export.FinalExportProcessor._is_zbrush_mesh(node)))
            for node in snapshot.get("lp_by_prefix", {}).get(prefix, []) or []:
                if node in seen or not cmds.objExists(node):
                    continue
                seen.add(node)
                records.append(MarmosetPackageExporter._mesh_record(
                    node, "low", prefix, 0, False))
        return records

    @staticmethod
    def _duplicate_named(node, temp_root, export_name):
        duplicate = cmds.duplicate(node, returnRootsOnly=True)[0]
        duplicate = (cmds.ls(duplicate, long=True) or [duplicate])[0]
        duplicate = cmds.parent(duplicate, temp_root, absolute=True)[0]
        duplicate = cmds.rename(duplicate, export_name)
        duplicate = bg_final_export.FinalExportProcessor._resolve_child_under_parent(
            duplicate, temp_root)
        try:
            cmds.makeIdentity(
                duplicate, apply=True, t=True, r=True, s=True, n=False, pn=True)
        except Exception as exc:
            cmds.warning("Could not zero Marmoset export copy '{}': {}".format(
                export_name, exc))
        return duplicate

    @classmethod
    def export_chapter(cls, snapshot, models_dir, status_callback=None):
        records = cls._chapter_records(snapshot)
        if not records:
            return None
        if not os.path.isdir(models_dir):
            os.makedirs(models_dir)
        base = _safe_name(snapshot.get("base"), "Chapter")
        file_name = "{}_Marmoset.fbx".format(base)
        export_path = os.path.join(models_dir, file_name).replace("\\", "/")
        if status_callback:
            status_callback("Marmoset: {}".format(base))

        source_by_id = {}
        for node in (snapshot.get("hp_all") or []) + (snapshot.get("lp_all") or []):
            if node and cmds.objExists(node):
                source_by_id[_source_id(node)] = node

        temp_root = cmds.group(
            empty=True, world=True, name="BG_Marmoset_Export_Temp#")
        temp_root = (cmds.ls(temp_root, long=True) or [temp_root])[0]
        prepared = []
        low_nodes = []
        try:
            for record in records:
                source = source_by_id.get(record["source_id"])
                if not source:
                    continue
                duplicate = cls._duplicate_named(
                    source, temp_root, record["export_name"])
                # Maya may suffix a name on an unexpected collision. Record the
                # exact FBX object name so Toolbag never has to guess.
                record["export_name"] = duplicate.split("|")[-1]
                prepared.append(duplicate)
                if record["role"] == "low":
                    low_nodes.append(duplicate)

            ok = bg_final_export.FinalExportProcessor._export_with_lp_triangulation_rollback(
                prepared, export_path, reusable_temp_nodes=low_nodes)
            if not ok:
                return None
            return {
                "name": base,
                "model": os.path.join("models", file_name).replace("\\", "/"),
                "meshes": records,
            }
        finally:
            if temp_root and cmds.objExists(temp_root):
                try:
                    cmds.delete(temp_root)
                except Exception:
                    pass

    @classmethod
    def export_package(cls, targets, snapshots, export_dir,
                       status_callback=None, cancel_check=None):
        models_dir = os.path.join(export_dir, "models")
        books = {}
        chapters = []
        for pair in targets or []:
            if cancel_check and cancel_check():
                return None
            key = pair.get("id") if pair.get("id") is not None else id(pair)
            snapshot = snapshots.get(key)
            if not snapshot:
                continue
            chapter = cls.export_chapter(
                snapshot, models_dir, status_callback=status_callback)
            if not chapter:
                continue
            chapter["id"] = str(pair.get("id") or pair.get("base") or chapter["name"])
            chapter["book"] = str(pair.get("book") or "Ungrouped")
            chapters.append(chapter)
            books.setdefault(chapter["book"], []).append(chapter["id"])

        if not chapters:
            return None
        manifest = {
            "schema": SCHEMA_NAME,
            "schema_version": SCHEMA_VERSION,
            "created_utc": int(time.time()),
            "source_scene": (cmds.file(query=True, sceneName=True) or "").replace("\\", "/"),
            "overrides_file": OVERRIDES_NAME,
            "books": [
                {"name": name, "chapters": chapter_ids}
                for name, chapter_ids in sorted(books.items())
            ],
            "chapters": chapters,
        }
        manifest_path = os.path.join(export_dir, MANIFEST_NAME)
        _atomic_write_json(manifest_path, manifest)
        return manifest_path
