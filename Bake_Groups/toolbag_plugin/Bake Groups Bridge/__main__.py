# -*- coding: utf-8 -*-
"""Bake Groups Tool bridge for Marmoset Toolbag 5."""
from __future__ import print_function

import json
import os
import sys

import mset


BRIDGE_PREFIX = "BG | "
STORE_KEY = "bake_groups_bridge.last_manifest"


def _startup_manifest_path():
    try:
        index = sys.argv.index("--bake-groups-manifest")
        return sys.argv[index + 1] if index + 1 < len(sys.argv) else ""
    except ValueError:
        return ""


def _norm(path):
    return os.path.normcase(os.path.abspath(path or ""))


def _read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8-sig") as stream:
            return json.load(stream)
    except Exception:
        return {} if default is None else default


def _write_json(path, payload):
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.flush()
        try:
            os.fsync(stream.fileno())
        except Exception:
            pass
    os.replace(temp_path, path)


def _walk(root):
    pending = list(root.getChildren() if root else [])
    while pending:
        obj = pending.pop(0)
        yield obj
        try:
            pending[0:0] = list(obj.getChildren())
        except Exception:
            pass


def _all_objects(object_type):
    """Toolbag 5.00-5.02 compatibility (getAllObjectsOfType arrived later)."""
    getter = getattr(mset, "getAllObjectsOfType", None)
    if getter:
        return list(getter(object_type))
    return [obj for obj in mset.getAllObjects() if isinstance(obj, object_type)]


def _refresh_ui():
    # refreshUI was added after Toolbag 5.02; property changes already repaint
    # correctly in older builds.
    refresh = getattr(mset, "refreshUI", None)
    if refresh:
        refresh()


class BakeGroupsBridge(object):
    def __init__(self):
        self.manifest_path = ""
        self.manifest = {}
        self.mesh_by_name = {}
        self.record_by_name = {}
        self.overrides = {}
        self.last_manifest_mtime = None
        self.hp_visible = True
        self.lp_visible = False

        self.window = mset.UIWindow("Bake Groups Bridge")
        self.window.width = 360
        self.status = mset.UILabel("Open a BakeGroups_Marmoset.json package.")
        self.status.fixedWidth = 340
        self.level = mset.UISliderInt(min=0, max=4, name="Smoothing Level")
        self.level.value = 2

        open_button = mset.UIButton("Open / Sync Package")
        open_button.onClick = self.choose_manifest
        apply_button = mset.UIButton("Apply Smooth to Selection")
        apply_button.onClick = self.apply_selected
        self.hp_visibility_button = mset.UIButton("Hide HP")
        self.hp_visibility_button.onClick = self.toggle_hp_visibility
        self.lp_visibility_button = mset.UIButton("Show LP")
        self.lp_visibility_button.onClick = self.toggle_lp_visibility

        self.window.addElement(open_button)
        self.window.addReturn()
        self.window.addElement(self.level)
        self.window.addReturn()
        self.window.addElement(apply_button)
        self.window.addReturn()
        self.window.addElement(self.hp_visibility_button)
        self.window.addElement(self.lp_visibility_button)
        self.window.addReturn()
        self.window.addElement(self.status)

        try:
            previous = mset.getStoreItem(STORE_KEY)
        except Exception:
            previous = ""
        if not _startup_manifest_path() and previous and os.path.isfile(previous):
            self.load_manifest(previous, sync_geometry=False)

        self.previous_callbacks = {
            "onRegainFocus": getattr(mset.callbacks, "onRegainFocus", None),
            "onSceneLoaded": getattr(mset.callbacks, "onSceneLoaded", None),
            "onShutdownPlugin": getattr(mset.callbacks, "onShutdownPlugin", None),
        }
        mset.callbacks.onRegainFocus = self._on_regain_focus
        mset.callbacks.onSceneLoaded = self._on_scene_loaded
        mset.callbacks.onShutdownPlugin = self._on_shutdown

    def set_status(self, text):
        self.status.text = str(text)
        mset.log(str(text))

    def choose_manifest(self):
        path = mset.showOpenFileDialog(fileTypes=["json"], multiple=False)
        if path:
            self.load_manifest(path, sync_geometry=True)

    def _validate_manifest(self, manifest):
        if manifest.get("schema") != "bake-groups-toolbag":
            raise ValueError("This is not a Bake Groups Marmoset package.")
        if int(manifest.get("schema_version", 0)) != 1:
            raise ValueError("Unsupported package schema version.")
        if not isinstance(manifest.get("chapters"), list):
            raise ValueError("Package has no chapters list.")

    def _override_path(self):
        name = self.manifest.get("overrides_file") or "BakeGroups_Marmoset.overrides.json"
        return os.path.join(os.path.dirname(self.manifest_path), name)

    def _load_overrides(self):
        payload = _read_json(self._override_path(), {"schema_version": 1, "meshes": {}})
        meshes = payload.get("meshes", {}) if isinstance(payload, dict) else {}
        self.overrides = meshes if isinstance(meshes, dict) else {}

    def _save_overrides(self):
        _write_json(self._override_path(), {
            "schema_version": 1,
            "meshes": self.overrides,
        })

    def _rebuild_indexes(self):
        self.record_by_name = {}
        for chapter in self.manifest.get("chapters", []):
            for record in chapter.get("meshes", []):
                name = str(record.get("export_name") or "")
                if name:
                    self.record_by_name[name] = record
        self.mesh_by_name = {}
        for obj in _all_objects(mset.MeshObject):
            if obj.name in self.record_by_name:
                self.mesh_by_name[obj.name] = obj

    def _find_baker(self, book_name):
        expected = BRIDGE_PREFIX + str(book_name)
        for obj in _all_objects(mset.BakerObject):
            if obj.name == expected:
                return obj
        return None

    def _model_paths_under(self, baker):
        paths = {}
        for obj in _walk(baker):
            if isinstance(obj, mset.ExternalObject):
                paths[_norm(obj.path)] = obj
        return paths

    def _sync_book(self, book):
        name = str(book.get("name") or "Ungrouped")
        baker = self._find_baker(name)
        if baker is None:
            baker = mset.BakerObject()
            baker.name = BRIDGE_PREFIX + name
        existing = self._model_paths_under(baker)
        chapter_ids = set(str(value) for value in book.get("chapters", []))
        for chapter in self.manifest.get("chapters", []):
            if str(chapter.get("id")) not in chapter_ids:
                continue
            path = os.path.join(
                os.path.dirname(self.manifest_path), chapter.get("model", ""))
            path = os.path.abspath(path)
            if not os.path.isfile(path):
                raise IOError("Model file is missing: {}".format(path))
            if _norm(path) not in existing:
                baker.importModel(path)
        for external in self._model_paths_under(baker).values():
            external.autoReload = True
            external.replaceTransforms = True
        output_dir = os.path.join(os.path.dirname(self.manifest_path), "bakes", name)
        if not os.path.isdir(output_dir):
            os.makedirs(output_dir)
        baker.outputPath = os.path.join(output_dir, name + ".psd")
        return baker

    def _apply_record(self, mesh, record):
        source_id = str(record.get("source_id") or "")
        override = self.overrides.get(source_id, {})
        level = override.get("subdivision_level", record.get("subdivision_level", 0))
        if record.get("zbrush"):
            level = 0
        level = max(0, min(4, int(level or 0)))
        enabled = level > 0
        changed = False

        # Toolbag treats assigning subdivision properties as a geometry change,
        # even when the assigned value is identical.  Avoid no-op writes so an
        # idle bridge cannot repeatedly invalidate and rebuild the scene.
        if enabled:
            values = (
                ("subdivisionMode",
                 str(record.get("subdivision_mode") or "Catmull-Clark")),
                ("subdivisionSharpenCorners",
                 bool(record.get("sharpen_corners", False))),
                ("subdivisionLevel", level),
            )
            for attribute, value in values:
                if getattr(mesh, attribute) != value:
                    setattr(mesh, attribute, value)
                    changed = True

        # For LP and ZBrush meshes only disable subdivision.  In Toolbag 5.02
        # a disabled mesh can still report level 1; writing level 0 needlessly
        # dirties geometry and may start an expensive background rebuild.
        if bool(mesh.subdivisionEnabled) != enabled:
            mesh.subdivisionEnabled = enabled
            changed = True
        return changed

    def apply_manifest_settings(self):
        self._rebuild_indexes()
        changed = 0
        for name, mesh in self.mesh_by_name.items():
            if self._apply_record(mesh, self.record_by_name[name]):
                changed += 1
        if changed:
            _refresh_ui()
        return changed

    def _apply_role_visibility(self):
        changed = False
        for name, mesh in self.mesh_by_name.items():
            role = self.record_by_name[name].get("role")
            if role not in ("high", "low"):
                continue
            visible = self.hp_visible if role == "high" else self.lp_visible
            # Imported bake groups keep HP under an invisible "High" parent.
            # A visible child alone therefore cannot make HP appear in Toolbag.
            parent = mesh.parent
            role_parent = "High" if role == "high" else "Low"
            if parent is not None and parent.name == role_parent:
                if bool(parent.visible) != visible:
                    parent.visible = visible
                    changed = True
            if bool(mesh.visible) != visible:
                mesh.visible = visible
                changed = True
        if changed:
            _refresh_ui()

    def toggle_hp_visibility(self):
        self.hp_visible = not self.hp_visible
        self.hp_visibility_button.text = "Hide HP" if self.hp_visible else "Show HP"
        self._rebuild_indexes()
        self._apply_role_visibility()

    def toggle_lp_visibility(self):
        self.lp_visible = not self.lp_visible
        self.lp_visibility_button.text = "Hide LP" if self.lp_visible else "Show LP"
        self._rebuild_indexes()
        self._apply_role_visibility()

    def load_manifest(self, path, sync_geometry=True):
        try:
            path = os.path.abspath(path)
            manifest = _read_json(path)
            self._validate_manifest(manifest)
            if _norm(path) != _norm(self.manifest_path):
                self.hp_visible = True
                self.lp_visible = False
                self.hp_visibility_button.text = "Hide HP"
                self.lp_visibility_button.text = "Show LP"
            self.manifest_path = path
            self.manifest = manifest
            self.last_manifest_mtime = os.path.getmtime(path)
            self._load_overrides()
            if sync_geometry:
                for book in manifest.get("books", []):
                    self._sync_book(book)
            self.apply_manifest_settings()
            self._apply_role_visibility()
            mset.setStoreItem(STORE_KEY, path)
            self.set_status("Synced {} chapter(s).".format(
                len(manifest.get("chapters", []))))
        except Exception as exc:
            self.set_status("Sync failed: {}".format(exc))
            if not os.environ.get("BG_MARMOSET_TEST_MODE"):
                mset.showOkDialog("Bake Groups Bridge", "Sync failed:\n{}".format(exc))

    def _selected_hp(self):
        selected = []
        for obj in mset.getSelectedObjects():
            candidates = [obj]
            candidates.extend(list(_walk(obj)))
            for candidate in candidates:
                if not isinstance(candidate, mset.MeshObject):
                    continue
                record = self.record_by_name.get(candidate.name)
                if record and record.get("role") == "high" and candidate not in selected:
                    selected.append(candidate)
        return selected

    def apply_selected(self):
        meshes = self._selected_hp()
        if not meshes:
            return self.set_status("Select one or more imported HP meshes.")
        changed = 0
        for mesh in meshes:
            record = self.record_by_name[mesh.name]
            if record.get("zbrush"):
                continue
            source_id = str(record.get("source_id") or "")
            self.overrides[source_id] = {
                "subdivision_level": int(self.level.value),
            }
            self._apply_record(mesh, record)
            changed += 1
        self._save_overrides()
        _refresh_ui()
        self.set_status("Updated {} selected HP mesh(es).".format(changed))

    def on_regain_focus(self):
        if not self.manifest_path or not os.path.isfile(self.manifest_path):
            return
        try:
            mtime = os.path.getmtime(self.manifest_path)
            if mtime != self.last_manifest_mtime:
                self.load_manifest(self.manifest_path, sync_geometry=True)
        except Exception as exc:
            self.set_status("Refresh failed: {}".format(exc))

    def on_scene_loaded(self):
        if self.manifest_path:
            self.load_manifest(self.manifest_path, sync_geometry=False)

    def on_shutdown(self):
        for name, callback in self.previous_callbacks.items():
            setattr(mset.callbacks, name, callback)

    def _call_previous(self, name):
        callback = self.previous_callbacks.get(name)
        if callable(callback):
            try:
                callback()
            except Exception as exc:
                mset.err("Previous {} callback failed: {}".format(name, exc))

    def _on_regain_focus(self):
        self._call_previous("onRegainFocus")
        self.on_regain_focus()

    def _on_scene_loaded(self):
        self._call_previous("onSceneLoaded")
        self.on_scene_loaded()

    def _on_shutdown(self):
        self._call_previous("onShutdownPlugin")
        self.on_shutdown()


BRIDGE = BakeGroupsBridge()
_startup_path = _startup_manifest_path()
if _startup_path:
    BRIDGE.load_manifest(_startup_path, sync_geometry=True)
