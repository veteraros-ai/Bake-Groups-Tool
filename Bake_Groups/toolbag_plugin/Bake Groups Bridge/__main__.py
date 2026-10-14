# -*- coding: utf-8 -*-
"""Bake Groups Tool bridge for Marmoset Toolbag 5."""
from __future__ import print_function

import json
import os
import sys
import time
import uuid
import tempfile

import mset


BRIDGE_PREFIX = "BG | "
STORE_KEY = "bake_groups_bridge.last_manifest"
LINK_PROTOCOL = 4


def _link_dir():
    return os.environ.get("BG_TOOLBAG_LINK_DIR") or os.path.join(
        os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(),
        "BakeGroups", "ToolbagLink")


def _signature(path):
    stat = os.stat(path)
    return (stat.st_mtime_ns, stat.st_size)


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
    def __init__(self, window=None):
        self.manifest_path = ""
        self.manifest = {}
        self.mesh_by_name = {}
        self.record_by_name = {}
        self.overrides = {}
        self.last_manifest_mtime = None
        self.hp_visible = True
        self.lp_visible = False
        self._syncing = False
        self._sync_selection_ids = None
        self._scene_attach_pending = False
        self._model_signatures = {}
        self._mesh_uid_by_source = {}
        self.scene_token = uuid.uuid4().hex
        self.session_id = uuid.uuid4().hex
        self.session_dir = os.path.join(_link_dir(), self.session_id)

        # Toolbag requires unique UIWindow names. Reuse the existing window
        # during a script reload instead of creating a second native window.
        self.window = window if window is not None else mset.UIWindow("Bake Groups Bridge")
        if window is not None:
            self.window.clearElements()
            self.window.visible = True
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
            "onPeriodicUpdate": getattr(mset.callbacks, "onPeriodicUpdate", None),
        }
        mset.callbacks.onRegainFocus = self._on_regain_focus
        mset.callbacks.onSceneLoaded = self._on_scene_loaded
        mset.callbacks.onShutdownPlugin = self._on_shutdown
        mset.callbacks.onPeriodicUpdate = self._on_periodic_update
        os.makedirs(self.session_dir, exist_ok=True)
        _write_json(os.path.join(self.session_dir, "session.json"), {
            "protocol": LINK_PROTOCOL, "session": self.session_id, "pid": os.getpid()})

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
        self.mesh_by_name = {}
        meshes = _all_objects(mset.MeshObject)
        by_uid = {obj.uid: obj for obj in meshes}
        by_name = {}
        for obj in meshes:
            by_name.setdefault(obj.name, []).append(obj)
        for chapter in self.manifest.get("chapters", []):
            for record in chapter.get("meshes", []):
                name = str(record.get("export_name") or "")
                source_id = str(record.get("source_id") or name)
                obj = by_uid.get(self._mesh_uid_by_source.get(source_id))
                candidates = [o for o in by_name.get(name, []) if self._mesh_baker(o)]
                if obj is None and len(candidates) == 1:
                    obj = candidates[0]
                if obj is not None and self._mesh_baker(obj):
                    self._mesh_uid_by_source[source_id] = obj.uid
                    self.record_by_name[obj.name] = record
                    self.mesh_by_name[obj.name] = obj
                elif name:
                    self.record_by_name[name] = record

    @staticmethod
    def _mesh_baker(mesh):
        parent = mesh.parent
        while parent is not None:
            if isinstance(parent, mset.BakerObject):
                return parent
            parent = parent.parent
        return None

    def _find_baker(self, book_name):
        expected = BRIDGE_PREFIX + str(book_name)
        for obj in _all_objects(mset.BakerObject):
            if obj.name == expected:
                return obj
        # Renaming a project in Toolbag must not create a duplicate on re-export.
        for chapter in self.manifest.get("chapters", []):
            if str(chapter.get("book") or "Ungrouped") != str(book_name):
                continue
            for record in chapter.get("meshes", []):
                uid = self._mesh_uid_by_source.get(str(record.get("source_id") or ""))
                for mesh in self.mesh_by_name.values():
                    if mesh.uid == uid:
                        return self._mesh_baker(mesh)
        return None

    def _model_paths_under(self, baker):
        paths = {}
        for obj in _walk(baker):
            if isinstance(obj, mset.ExternalObject):
                paths[_norm(obj.path)] = obj
        return paths

    def _sync_book(self, book, import_all_roles=False):
        name = str(book.get("name") or "Ungrouped")
        baker = self._find_baker(name)
        created = baker is None
        if baker is None:
            baker = mset.BakerObject()
            baker.name = BRIDGE_PREFIX + name
        chapter_ids = set(str(value) for value in book.get("chapters", []))
        changed = 0
        for chapter in self.manifest.get("chapters", []):
            if str(chapter.get("id")) not in chapter_ids:
                continue
            # Old packages have one combined model; partial exports can retain
            # that model for the unchecked role and add a role-specific FBX.
            models = chapter.get("models") or [
                {"path": chapter.get("model", ""), "roles": ["high", "low"]}]
            update_roles = set(chapter.get("update_roles", ["high", "low"]))
            for model in models:
                roles = set(model.get("roles", ["high", "low"]))
                records = [r for r in chapter.get("meshes", [])
                           if r.get("role") in roles]
                if not records:
                    continue
                # An existing project never refreshes unchecked roles, even if
                # the user deleted a mesh or reopened the bridge (empty cache).
                # Only a fresh first import reads the entire merged package.
                if not import_all_roles and not roles.intersection(update_roles):
                    continue
                path = os.path.abspath(os.path.join(
                    os.path.dirname(self.manifest_path), model.get("path", "")))
                if not os.path.isfile(path):
                    raise IOError("Model file is missing: {}".format(path))
                signature = _signature(path)
                key = (_norm(path), tuple(sorted(roles)))
                if self._model_signatures.get(key) != signature:
                    part = dict(chapter, meshes=records)
                    changed += self._update_chapter_geometry(baker, part, path)
                    self._model_signatures[key] = signature
        if created:
            output_dir = os.path.join(os.path.dirname(self.manifest_path), "bakes", name)
            os.makedirs(output_dir, exist_ok=True)
            baker.outputPath = os.path.join(output_dir, name + ".psd")
        return changed

    def _role_target(self, baker, record):
        group_name = str(record.get("group") or "Group")
        group = next((o for o in baker.getChildren() if o.name == group_name), None)
        created = group is None
        if group is None:
            group = baker.addGroup(group_name)
        role_name = "High" if record.get("role") == "high" else "Low"
        target = next((o for o in group.getChildren() if o.name == role_name), None)
        if target is None:
            raise RuntimeError("Bake group has no {} target".format(role_name))
        if created:
            for child in group.getChildren():
                if child.name in ("High", "Low"):
                    child.visible = self.hp_visible if child.name == "High" else self.lp_visible
        return target

    @staticmethod
    def _replace_mesh_data(mesh, incoming):
        # The writable mesh property replaces the base buffer but does not
        # invalidate Toolbag 5.02's subdivision cache. Disable subdivision before
        # editing base submesh ranges, then restore it to rebuild from new data.
        subdivided = bool(mesh.subdivisionEnabled)
        if subdivided:
            mesh.subdivisionEnabled = False
        try:
            return BakeGroupsBridge._replace_base_mesh_data(mesh, incoming)
        finally:
            if subdivided:
                mesh.subdivisionEnabled = True

    @staticmethod
    def _replace_base_mesh_data(mesh, incoming):
        # Preserve the MeshObject, its parent, transforms, smoothing and bake target.
        # Quiesce old ranges before replacing the buffer, then attach the new
        # ranges. This is safe both when topology shrinks and when it grows.
        old_slots = list(mesh.getChildren())
        slots_by_name = {o.name: o for o in old_slots if isinstance(o, mset.SubMeshObject)}
        incoming_slots = [o for o in incoming.getChildren() if isinstance(o, mset.SubMeshObject)]
        live_slots = set()
        created_materials = set()
        old_slots = [o for o in old_slots if isinstance(o, mset.SubMeshObject)]
        materials_before = {o.uid: o.material for o in old_slots}
        for old in old_slots:
            old.indexCount = 0
        mesh.mesh = incoming.mesh
        for index, slot in enumerate(incoming_slots):
            old = slots_by_name.get(slot.name)
            # Maya may rename a material during FBX import/re-export. Preserve
            # the user's existing assignment by slot order in that case.
            if old is None or old.uid in live_slots:
                old = (old_slots[index] if index < len(old_slots)
                       and old_slots[index].uid not in live_slots else None)
            if old is None:
                old = mesh.addSubmesh(slot.name, slot.material, slot.startIndex, slot.indexCount)
                if slot.material is not None:
                    created_materials.add(slot.material.name)
            else:
                old.startIndex = slot.startIndex
                old.indexCount = slot.indexCount
            live_slots.add(old.uid)
        for old in old_slots:
            if old.uid not in live_slots:
                old.indexCount = 0
            # Mesh buffer assignment can inherit FBX material links in 5.02.
            # Restore the persistent slot's original material explicitly.
            material = materials_before[old.uid]
            current = old.material
            if material is not None and (current is None or current.name != material.name):
                old.material = material
        return created_materials

    def _update_chapter_geometry(self, baker, chapter, path):
        # Never call Baker.importModel again: Quick Loader can replace targets,
        # reset cage/settings and destabilize a large subdivided scene in 5.02.
        self._rebuild_indexes()
        existing = {str(self.record_by_name[n].get("source_id") or n): o
                    for n, o in self.mesh_by_name.items()}
        # 5.02 can return stale selection wrappers for a destroyed import root
        # until the next native frame. Keep identities, never those wrappers.
        selected_ids = self._sync_selection_ids
        if selected_ids is None:
            live_ids = {o.uid for o in mset.getAllObjects()}
            selected_ids = [o.uid for o in mset.getSelectedObjects() if o.uid in live_ids]
        material_names_before = {o.name for o in mset.getAllMaterials()}
        retained_new_materials = set()
        root = None
        prefs = mset.getPreferences()
        pref_name = "importFbxWithMaterials"
        pref_value = getattr(prefs, pref_name, None)
        try:
            if pref_value is not None:
                setattr(prefs, pref_name, False)
            root = mset.importModel(path)
            imported = {o.name: o for o in _walk(root) if isinstance(o, mset.MeshObject)}
            plan = []
            for record in chapter.get("meshes", []):
                incoming = imported.get(record.get("export_name"))
                if incoming is None:
                    raise RuntimeError("FBX mesh is missing: {}".format(record.get("export_name")))
                source_id = str(record.get("source_id") or incoming.name)
                plan.append((record, source_id, incoming, existing.get(source_id)))
            for record, source_id, incoming, mesh in plan:
                if mesh is not None:
                    retained_new_materials.update(self._replace_mesh_data(mesh, incoming))
                else:
                    mesh = incoming
                    mesh.parent = self._role_target(baker, record)
                    mesh.visible = self.hp_visible if record.get("role") == "high" else self.lp_visible
                    self._apply_record(mesh, record)
                    retained_new_materials.update(
                        s.material.name for s in mesh.getChildren()
                        if isinstance(s, mset.SubMeshObject) and s.material is not None)
                self._mesh_uid_by_source[source_id] = mesh.uid
            return len(plan)
        finally:
            if root is not None:
                root.destroy()
            # Importing FBX may create default material shells even with material
            # loading disabled. Remove only newly-created, now-unassigned shells.
            for material in mset.getAllMaterials():
                # Existing meshes keep original materials. New imported materials
                # are retained only for explicitly-created meshes/material slots,
                # never because a temporary importer/preview claims an assignment.
                assigned = material.name in retained_new_materials
                if material.name not in material_names_before and not assigned:
                    material.destroy()
            if pref_value is not None:
                setattr(prefs, pref_name, pref_value)
            # Toolbag 5.02 rejects an empty selection array. Destroying the
            # temporary import root already clears its selection in that case.
            live = {o.uid: o for o in mset.getAllObjects()}
            selected = [live[uid] for uid in selected_ids if uid in live
                        and isinstance(live[uid], mset.SceneObject)]
            if selected:
                mset.setSelectedObjects(selected)

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
        if self._syncing:
            return False
        self._syncing = True
        try:
            path = os.path.abspath(path)
            manifest = _read_json(path)
            self._validate_manifest(manifest)
            same_package = _norm(path) == _norm(self.manifest_path)
            if not same_package:
                self.hp_visible = True
                self.lp_visible = False
                self.hp_visibility_button.text = "Hide HP"
                self.lp_visibility_button.text = "Show LP"
                self._model_signatures = {}
                self._mesh_uid_by_source = {}
            self.manifest_path = path
            self.manifest = manifest
            self._load_overrides()
            self._rebuild_indexes()
            if sync_geometry:
                material_names_before_sync = {o.name for o in mset.getAllMaterials()}
                # Preserve one selection snapshot across all chapters, rather
                # than capturing the temporary importer selection each time.
                live_ids = {o.uid for o in mset.getAllObjects()}
                self._sync_selection_ids = [o.uid for o in mset.getSelectedObjects()
                                            if o.uid in live_ids]
                initial_import = not bool(self.mesh_by_name)
                for book in manifest.get("books", []):
                    self._sync_book(book, import_all_roles=initial_import)
                # Imported object wrappers are now out of scope. A final pass
                # over actual scene meshes (not internal import/preview caches)
                # removes delayed material shells without touching any original
                # material or newly assigned persistent material slot.
                used_materials = set()
                for obj in mset.getAllObjects():
                    if isinstance(obj, mset.MeshObject):
                        used_materials.update(
                            s.material.name for s in obj.getChildren()
                            if isinstance(s, mset.SubMeshObject) and s.material is not None)
                for material in mset.getAllMaterials():
                    if (material.name not in material_names_before_sync and
                            material.name not in used_materials):
                        material.destroy()
            else:
                # Attach to an already-open project without changing its settings.
                # A saved scene can contain an older revision of the FBX. Merely
                # attaching is read-only, but the first explicit sync must read
                # current model data instead of assuming the scene is up to date.
                self._model_signatures = {}
            self._rebuild_indexes()
            self.last_manifest_mtime = _signature(path)
            mset.setStoreItem(STORE_KEY, path)
            self.set_status("Synced {} chapter(s).".format(
                len(manifest.get("chapters", []))))
            return True
        except Exception as exc:
            self.set_status("Sync failed: {}".format(exc))
            if not os.environ.get("BG_MARMOSET_TEST_MODE"):
                mset.showOkDialog("Bake Groups Bridge", "Sync failed:\n{}".format(exc))
            return False
        finally:
            self._sync_selection_ids = None
            self._syncing = False

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
        # Wake the mailbox immediately in Toolbag 5.02. Never reload geometry
        # just because focus changed: only a validated sync may do that.
        self._on_periodic_update()

    def on_scene_loaded(self):
        self.scene_token = uuid.uuid4().hex
        self._mesh_uid_by_source = {}
        self._model_signatures = {}
        # SceneLoaded may run during native object teardown. Defer all scene
        # traversal until the next normal update; never touch stale handles here.
        self._scene_attach_pending = True

    def on_shutdown(self):
        for name, callback in self.previous_callbacks.items():
            setattr(mset.callbacks, name, callback)
        try:
            os.remove(os.path.join(self.session_dir, "session.json"))
        except OSError:
            pass

    def _project_status(self, requested_path, source_scene):
        self._rebuild_indexes()
        loaded = bool(self.mesh_by_name)
        if not loaded and requested_path and os.path.isfile(requested_path):
            # StoreItem is global, not scene-local. After restarting Toolbag its
            # last package may be unrelated. Recover the binding using stable
            # exported mesh names already present in the saved Baker hierarchy.
            manifest = _read_json(requested_path)
            self._validate_manifest(manifest)
            expected = str(manifest.get("source_scene") or "")
            if not source_scene or not expected or _norm(source_scene) == _norm(expected):
                names = {str(r.get("export_name") or "") for ch in manifest["chapters"]
                         for r in ch.get("meshes", [])}
                found = {}
                for mesh in _all_objects(mset.MeshObject):
                    if mesh.name in names and self._mesh_baker(mesh):
                        found[mesh.name] = found.get(mesh.name, 0) + 1
                if any(count > 1 for count in found.values()):
                    raise RuntimeError("Several meshes in this scene match the package. Binding is ambiguous.")
                if found and self.load_manifest(requested_path, sync_geometry=False):
                    loaded = bool(self.mesh_by_name)
        matches = loaded and _norm(requested_path) == _norm(self.manifest_path)
        expected_source = str(self.manifest.get("source_scene") or "")
        if source_scene and expected_source and _norm(source_scene) != _norm(expected_source):
            matches = False
        return {
            "state": "matching" if matches else ("other_project" if loaded else "empty"),
            "scene_path": mset.getScenePath(), "manifest_path": self.manifest_path,
            "scene_token": self.scene_token, "meshes": len(self.mesh_by_name)}

    def _handle_link_request(self, request):
        if request.get("protocol") != LINK_PROTOCOL or request.get("session") != self.session_id:
            raise ValueError("Invalid bridge session")
        if time.time() > request.get("expires", 0):
            raise ValueError("Bridge request expired")
        payload = request.get("payload") or {}
        path = payload.get("manifest_path") or ""
        if request.get("method") == "status":
            return self._project_status(path, payload.get("source_scene") or "")
        if request.get("method") != "sync":
            raise ValueError("Unsupported bridge operation")
        if payload.get("scene_token") != self.scene_token:
            raise RuntimeError("The Toolbag scene changed after export started. Nothing was synced.")
        status = self._project_status(path, "")
        if status["state"] != "matching" and not (
                payload.get("import_new") and status["state"] == "empty"):
            raise RuntimeError("The exported project is not open in this Toolbag scene.")
        if not self.load_manifest(path, sync_geometry=True):
            raise RuntimeError(self.status.text)
        return {"state": "synced", "meshes": len(self.mesh_by_name)}

    def _on_periodic_update(self):
        self._call_previous("onPeriodicUpdate")
        if self._syncing:
            return
        if self._scene_attach_pending:
            self._scene_attach_pending = False
            if self.manifest_path and os.path.isfile(self.manifest_path):
                self.load_manifest(self.manifest_path, sync_geometry=False)
        # This callback checks only the tiny mailbox; no geometry/property writes
        # and no scene traversal occur while idle.
        for name in os.listdir(self.session_dir):
            if not name.endswith(".request.json"):
                continue
            path = os.path.join(self.session_dir, name)
            request = _read_json(path)
            reply = {"id": request.get("id"), "session": self.session_id}
            try:
                reply.update(ok=True, result=self._handle_link_request(request))
            except Exception as exc:
                reply.update(ok=False, error=str(exc))
            _write_json(path.replace(".request.json", ".reply.json"), reply)
            try:
                os.remove(path)
            except OSError:
                pass

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


# A plugin reload must retire the old mailbox/callbacks, not stack pollers.
_previous_bridge = getattr(getattr(mset.callbacks, "onRegainFocus", None), "__self__", None)
_previous_window = None
if _previous_bridge is not None and _previous_bridge.__class__.__name__ == "BakeGroupsBridge":
    _previous_window = _previous_bridge.window
    _previous_bridge.on_shutdown()
BRIDGE = BakeGroupsBridge(window=_previous_window)
_startup_path = _startup_manifest_path()
if _startup_path:
    BRIDGE.load_manifest(_startup_path, sync_geometry=True)
