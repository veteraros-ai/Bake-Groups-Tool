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
import csv
import uuid

import maya.cmds as cmds

import bg_final_export


SCHEMA_NAME = "bake-groups-toolbag"
SCHEMA_VERSION = 1
MANIFEST_NAME = "BakeGroups_Marmoset.json"
OVERRIDES_NAME = "BakeGroups_Marmoset.overrides.json"
LINK_PROTOCOL = 4


def toolbag_link_dir():
    return os.environ.get("BG_TOOLBAG_LINK_DIR") or os.path.join(
        os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(),
        "BakeGroups", "ToolbagLink")


def toolbag_running_pids():
    """Query the actual process list, not the existence of an installed EXE."""
    if os.name != "nt":
        return set()
    result = subprocess.check_output(
        ["tasklist", "/FI", "IMAGENAME eq toolbag.exe", "/FO", "CSV", "/NH"],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    rows = csv.reader(io.StringIO(result.decode("utf-8", errors="replace")))
    return {int(row[1]) for row in rows
            if len(row) > 1 and row[0].lower() == "toolbag.exe" and row[1].isdigit()}


def _process_alive(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, int(pid))
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def _wake_toolbag(pid):
    """Toolbag 5.02 suspends plugin polling in the background; wake this PID only.

    Called as part of an explicit export/request, never on an idle timer. Do not
    launch an EXE or send keys. Return the previously-active native window so
    status checks can restore Maya's focus after receiving the response.
    """
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.IsIconic.argtypes = [wintypes.HWND]
    user.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetWindow.restype = wintypes.HWND
    user.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user.SetForegroundWindow.argtypes = [wintypes.HWND]
    user.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
    user.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
    foreground = user.GetForegroundWindow()
    foreground_pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(foreground, ctypes.byref(foreground_pid))
    if foreground_pid.value == int(pid):
        return None
    if foreground_pid.value == os.getpid():
        # Retain permission to restore Maya after temporarily activating Toolbag.
        user.AllowSetForegroundWindow(os.getpid())
    windows = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def collect(hwnd, _param):
        owner_pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
        if owner_pid.value == int(pid) and user.IsWindowVisible(hwnd):
            windows.append((bool(user.GetWindow(hwnd, 4)),
                            -user.GetWindowTextLengthW(hwnd), hwnd))
        return True

    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.EnumWindows(collect, 0)
    if not windows:
        raise RuntimeError("The running Marmoset session has no available window.")
    # Prefer the unowned main window rather than a plugin/tool popup.
    target = sorted(windows)[0][2]
    if user.IsIconic(target):
        user.ShowWindowAsync(target, 9)  # SW_RESTORE, only when minimized.
    activated = bool(user.SetForegroundWindow(target))
    current_pid = wintypes.DWORD()
    # Cross-process activation/restoring a minimized window can be queued.
    # Do not mistake the old foreground handle for an immediate failure.
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        user.GetWindowThreadProcessId(user.GetForegroundWindow(), ctypes.byref(current_pid))
        if current_pid.value == int(pid):
            break
        time.sleep(0.02)
    if current_pid.value != int(pid):
        raise RuntimeError("Windows could not activate Marmoset (PID {}, foreground {}, accepted {}). "
                           "Make Toolbag active and retry export.".format(
                               pid, current_pid.value, activated))
    return foreground


def _restore_foreground(hwnd):
    if os.name == "nt" and hwnd:
        import ctypes
        from ctypes import wintypes
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.SetForegroundWindow.argtypes = [wintypes.HWND]
        user.SetForegroundWindow(hwnd)


def request_toolbag(session, method, payload=None, timeout=8.0, process_events=None):
    """Request only known bridge operations; scene access stays on Toolbag's UI thread."""
    folder = session["folder"]
    request_id = uuid.uuid4().hex
    request_path = os.path.join(folder, request_id + ".request.json")
    reply_path = os.path.join(folder, request_id + ".reply.json")
    _atomic_write_json(request_path, {
        "protocol": LINK_PROTOCOL, "session": session["session"],
        "id": request_id, "method": method, "payload": payload or {},
        "expires": time.time() + timeout})
    deadline = time.monotonic() + timeout
    previous_window = None
    try:
        previous_window = _wake_toolbag(session["pid"])
        while time.monotonic() < deadline:
            if not _process_alive(session["pid"]):
                raise RuntimeError("Marmoset closed before the bridge could finish.")
            if os.path.isfile(reply_path):
                with io.open(reply_path, "r", encoding="utf-8") as stream:
                    reply = json.load(stream)
                if reply.get("id") != request_id or reply.get("session") != session["session"]:
                    raise RuntimeError("Invalid Marmoset bridge response")
                if not reply.get("ok"):
                    raise RuntimeError(reply.get("error") or "Marmoset sync failed")
                return reply.get("result") or {}
            if process_events:
                process_events()
            time.sleep(0.04)
        raise RuntimeError("Marmoset bridge did not respond. Open Bake Groups Bridge in Toolbag.")
    finally:
        if method == "status":
            _restore_foreground(previous_window)
        for path in (request_path, reply_path):
            try:
                os.remove(path)
            except OSError:
                pass


def find_toolbag_project(manifest_path, source_scene="", process_events=None):
    pids = toolbag_running_pids()
    if not pids:
        return {"state": "not_running"}
    folder = toolbag_link_dir()
    responses = []
    failures = []
    for name in sorted(os.listdir(folder) if os.path.isdir(folder) else []):
        session_dir = os.path.join(folder, name)
        session = None
        try:
            with io.open(os.path.join(session_dir, "session.json"), "r", encoding="utf-8") as stream:
                session = json.load(stream)
            if session.get("pid") not in pids:
                continue
            if session.get("protocol") != LINK_PROTOCOL:
                failures.append("The running Bake Groups Bridge is outdated. "
                                "Reopen Bake Groups Bridge after installing the update.")
                continue
            session["folder"] = session_dir
            result = request_toolbag(session, "status", {
                "manifest_path": os.path.abspath(manifest_path),
                "source_scene": source_scene}, process_events=process_events)
            result["session_info"] = session
            responses.append(result)
        except (IOError, OSError, ValueError, RuntimeError) as exc:
            if (session and session.get("pid") in pids and
                    session.get("protocol") == LINK_PROTOCOL):
                failures.append(str(exc))
            continue
    matched = [r for r in responses if r.get("state") == "matching"]
    if len(matched) == 1:
        return matched[0]
    if len(matched) > 1:
        return {"state": "ambiguous"}
    if failures:
        return {"state": "bridge_unavailable", "detail": "\n".join(failures)}
    if len(responses) == 1:
        if failures:
            responses[0]["detail"] = "\n".join(failures)
        return responses[0]
    return {"state": "ambiguous" if responses else "bridge_unavailable",
            "detail": "\n".join(failures)}


def sync_toolbag_project(connection, manifest_path, import_new=False, process_events=None):
    return request_toolbag(connection["session_info"], "sync", {
        "manifest_path": os.path.abspath(manifest_path),
        "scene_token": connection["scene_token"],
        "import_new": bool(import_new)}, timeout=120.0, process_events=process_events)


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
    def _chapter_records(snapshot, inc_hp=True, inc_lp=True):
        records = []
        seen = set()
        prefixes = sorted(set(
            list((snapshot.get("hp_by_prefix") or {}).keys()) +
            list((snapshot.get("lp_by_prefix") or {}).keys())))
        for prefix in prefixes:
            level = MarmosetPackageExporter._group_level(snapshot, prefix)
            for node in (snapshot.get("hp_by_prefix", {}).get(prefix, []) or []) if inc_hp else []:
                if node in seen or not cmds.objExists(node):
                    continue
                seen.add(node)
                records.append(MarmosetPackageExporter._mesh_record(
                    node, "high", prefix, level,
                    bg_final_export.FinalExportProcessor._is_zbrush_mesh(node)))
            for node in (snapshot.get("lp_by_prefix", {}).get(prefix, []) or []) if inc_lp else []:
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
        # FBXExportSmoothMesh=False does NOT mean "export the base mesh": an
        # inherited Maya 2/3 preview is tessellated into the FBX in that mode.
        # Disable preview on the copies themselves, including manually-enabled
        # previews and chapters not covered by the active UI preview toggle.
        # Toolbag then applies the requested subdivision exactly once.
        for shape in cmds.listRelatives(
                duplicate, shapes=True, noIntermediate=True, fullPath=True, type='mesh') or []:
            cmds.setAttr(shape + ".displaySmoothMesh", 0)
        try:
            cmds.makeIdentity(
                duplicate, apply=True, t=True, r=True, s=True, n=False, pn=True)
        except Exception as exc:
            cmds.warning("Could not zero Marmoset export copy '{}': {}".format(
                export_name, exc))
        return duplicate

    @classmethod
    def export_chapter(cls, snapshot, models_dir, status_callback=None,
                       inc_hp=True, inc_lp=True):
        records = cls._chapter_records(snapshot, inc_hp=inc_hp, inc_lp=inc_lp)
        if not records:
            return None
        if not os.path.isdir(models_dir):
            os.makedirs(models_dir)
        base = _safe_name(snapshot.get("base"), "Chapter")
        # Partial exports must not overwrite the combined FBX referenced by
        # the untouched role in an existing package.
        suffix = "" if inc_hp and inc_lp else ("_HP" if inc_hp else "_LP")
        file_name = "{}_Marmoset{}.fbx".format(base, suffix)
        export_path = os.path.join(models_dir, file_name).replace("\\", "/")
        staging_path = os.path.join(
            models_dir, "." + base + "." + uuid.uuid4().hex + ".fbx").replace("\\", "/")
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
                prepared, staging_path, reusable_temp_nodes=low_nodes)
            if not ok:
                return None
            # Toolbag must never see an FBX that is only partially written.
            os.replace(staging_path, export_path)
            roles = (["high"] if inc_hp else []) + (["low"] if inc_lp else [])
            model = os.path.join("models", file_name).replace("\\", "/")
            return {
                "name": base,
                "model": model,
                "models": [{"path": model, "roles": roles}],
                "update_roles": roles,
                "meshes": records,
            }
        finally:
            if os.path.exists(staging_path):
                os.remove(staging_path)
            if temp_root and cmds.objExists(temp_root):
                try:
                    cmds.delete(temp_root)
                except Exception:
                    pass

    @classmethod
    def export_package(cls, targets, snapshots, export_dir,
                       status_callback=None, cancel_check=None,
                       inc_hp=True, inc_lp=True):
        if not (inc_hp or inc_lp):
            return None
        manifest_path = os.path.join(export_dir, MANIFEST_NAME)
        source_scene = (cmds.file(query=True, sceneName=True) or "").replace("\\", "/")
        previous = {}
        if os.path.isfile(manifest_path):
            with io.open(manifest_path, "r", encoding="utf-8") as stream:
                previous = json.load(stream)
            if (previous.get("schema") != SCHEMA_NAME or
                    previous.get("schema_version") != SCHEMA_VERSION):
                raise ValueError("Export folder contains an incompatible Marmoset package.")
            old_scene = previous.get("source_scene") or ""
            if (old_scene and source_scene and
                    os.path.normcase(os.path.abspath(old_scene)) !=
                    os.path.normcase(os.path.abspath(source_scene))):
                raise ValueError("Export folder belongs to another Maya scene.")
        roles = (["high"] if inc_hp else []) + (["low"] if inc_lp else [])
        models_dir = os.path.join(export_dir, "models")
        books = {}
        chapters_by_id = {}
        for old in previous.get("chapters", []):
            old = dict(old)
            old["update_roles"] = []
            chapters_by_id[str(old.get("id"))] = old
        exported = 0
        for pair in targets or []:
            if cancel_check and cancel_check():
                return None
            key = pair.get("id") if pair.get("id") is not None else id(pair)
            snapshot = snapshots.get(key)
            if not snapshot:
                continue
            chapter = cls.export_chapter(
                snapshot, models_dir, status_callback=status_callback,
                inc_hp=inc_hp, inc_lp=inc_lp)
            if not chapter:
                continue
            chapter["id"] = str(pair.get("id") or pair.get("base") or chapter["name"])
            chapter["book"] = str(pair.get("book") or "Ungrouped")
            old = chapters_by_id.get(chapter["id"])
            if old and not (inc_hp and inc_lp):
                chapter["meshes"] += [
                    record for record in old.get("meshes", [])
                    if record.get("role") not in roles]
                old_models = old.get("models") or [
                    {"path": old.get("model"), "roles": ["high", "low"]}]
                for model in old_models:
                    retained = [role for role in model.get("roles", [])
                                if role not in roles]
                    if retained and model.get("path"):
                        chapter["models"].append({
                            "path": model["path"], "roles": retained})
            chapters_by_id[chapter["id"]] = chapter
            exported += 1

        if not exported or (cancel_check and cancel_check()):
            return None
        chapters = list(chapters_by_id.values())
        for chapter in chapters:
            books.setdefault(chapter["book"], []).append(chapter["id"])
        manifest = {
            "schema": SCHEMA_NAME,
            "schema_version": SCHEMA_VERSION,
            "created_utc": int(time.time()),
            "generation": uuid.uuid4().hex,
            "source_scene": source_scene,
            "overrides_file": OVERRIDES_NAME,
            "books": [
                {"name": name, "chapters": chapter_ids}
                for name, chapter_ids in sorted(books.items())
            ],
            "chapters": chapters,
        }
        _atomic_write_json(manifest_path, manifest)
        return manifest_path
