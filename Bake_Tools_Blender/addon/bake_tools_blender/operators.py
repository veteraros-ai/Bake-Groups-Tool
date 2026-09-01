"""Undoable Blender mutations used by both the Qt and native Blender views."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4
import json

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from . import blender_bridge
from .object_repository import ObjectRepository
from .material_distribution import (
    add_object_refs,
    analyze_material_distribution,
    clean_material_name,
    inspect_root_materials,
    next_book_name,
    target_meshes,
)
from .properties import ensure_state_ids
from .progress import ProgressCancelled, progress_scope


def settings(context):
    state = context.scene.bake_tools_settings
    ensure_state_ids(state)
    return state


def active_pair(state):
    if state.active_pair_id:
        for index, pair in enumerate(state.pairs):
            if pair.item_id == state.active_pair_id:
                state.active_pair = index
                return pair
    if 0 <= state.active_pair < len(state.pairs):
        pair = state.pairs[state.active_pair]
        state.active_pair_id = pair.item_id
        return pair
    return None


def _sync_cages(state, pair=None):
    """Keep managed Cage visibility aligned with chapter/subgroup gates."""
    try:
        from .cage_service import sync_visibility
        sync_visibility(state, pair)
    except (AttributeError, ReferenceError, RuntimeError):
        # Cage metadata from an older file may temporarily reference an object
        # that Blender has just removed. Visibility must never block UI actions.
        pass


def pair_by_id(state, pair_id):
    for index, pair in enumerate(state.pairs):
        if pair.item_id == pair_id:
            return index, pair
    return -1, None


def subgroup_by_id(state, subgroup_id):
    for pair_index, pair in enumerate(state.pairs):
        for subgroup_index, subgroup in enumerate(pair.subgroups):
            if subgroup.item_id == subgroup_id:
                return pair_index, pair, subgroup_index, subgroup
    return -1, None, -1, None


def log(state, message):
    lines = [line for line in state.log_text.splitlines() if line.strip()]
    if lines == ["No log messages yet."]:
        lines = []
    lines.append(str(message))
    state.log_text = "\n".join(lines[-120:])
    history = [line for line in state.action_history.splitlines() if line.strip()]
    history.append("[{}] {}".format(datetime.now().strftime("%H:%M:%S"), message))
    state.action_history = "\n".join(history[-500:])


def _unique_name(existing, requested, fallback):
    base = (requested or fallback).strip() or fallback
    if base not in existing:
        return base
    number = 2
    while "{}_{:02d}".format(base, number) in existing:
        number += 1
    return "{}_{:02d}".format(base, number)


def _set_active_pair(state, index, pair, toggle_same=False):
    is_switch = state.active_pair_id != pair.item_id
    state.active_pair = index
    state.active_pair_id = pair.item_id
    if is_switch:
        # Maya clears the active subgroup whenever another chapter is chosen.
        # Carrying an index across chapters made an unanalysed chapter appear to
        # inherit the previous chapter's subgroup state.
        state.active_subgroup = 0
        state.chapter_isolated = True
    else:
        state.active_subgroup = min(state.active_subgroup, max(0, len(pair.subgroups) - 1))
        if toggle_same:
            state.chapter_isolated = not state.chapter_isolated
    ObjectRepository.sync_all_pair_visibility(state)
    if is_switch:
        _refresh_smooth_preview(state)
    return is_switch


def _refresh_color_preview(state):
    """Keep the viewport preview synchronized with undoable metadata changes."""
    from .color_preview import refresh_color_preview

    return refresh_color_preview(state, active_pair(state))


def _refresh_smooth_preview(state):
    if not state.preview_smoothing:
        return (0, 0)
    from .smooth_preview import apply_preview, clear_preview

    pair = active_pair(state)
    if pair is None:
        clear_preview(state)
        state.preview_smoothing = False
        return (0, 0)
    return apply_preview(state, pair, True, None)


def _assign_pair_roots(pair, hp_kind, hp_root, lp_kind, lp_root):
    pair.lp_root = lp_root if lp_kind == "OBJECT" else None
    pair.hp_root = hp_root if hp_kind == "OBJECT" else None
    pair.hp_collection = hp_root if hp_kind == "COLLECTION" else None
    pair.lp_collection = lp_root if lp_kind == "COLLECTION" else None
    pair.hp_root_kind = hp_kind
    pair.lp_root_kind = lp_kind
    pair.hp_object = hp_root.name
    pair.lp_object = lp_root.name


def _selected_outliner_collection(context):
    for candidate_context in (context, bpy.context):
        try:
            selected_ids = candidate_context.selected_ids
        except (AttributeError, RuntimeError):
            selected_ids = ()
        collections = [item for item in selected_ids if isinstance(item, bpy.types.Collection)]
        if collections:
            return collections[0]
    return None


def _active_collection(context):
    """Resolve an Outliner collection when the operator is called from Qt/3D View."""
    selected = _selected_outliner_collection(context)
    if selected is not None:
        return selected
    try:
        layer_collection = context.view_layer.active_layer_collection
        collection = layer_collection.collection if layer_collection is not None else None
    except (AttributeError, RuntimeError):
        collection = None
    if collection is not None and collection != context.scene.collection:
        return collection
    return None


def _picked_root(state, role):
    key = role.lower()
    kind = getattr(state, "{}_root_kind".format(key), "")
    if kind == "COLLECTION":
        pointer = getattr(state, "{}_collection".format(key), None)
    else:
        kind = "OBJECT"
        pointer = getattr(state, "{}_root".format(key), None)
    return kind, pointer


def _clear_picked_roots(state):
    """Consume the temporary HP/LP picks after a successful chapter create."""
    for role in ("hp", "lp"):
        setattr(state, "{}_object".format(role), "")
        setattr(state, "{}_root".format(role), None)
        setattr(state, "{}_collection".format(role), None)
        setattr(state, "{}_root_kind".format(role), "")


class BAKE_TOOLS_OT_pick_object(bpy.types.Operator):
    bl_idname = "bake_tools.pick_object"
    bl_label = "Pick Object"
    bl_options = {"REGISTER", "UNDO"}

    role: EnumProperty(
        name="Role",
        items=(("HP", "HP", "Pick HP root"), ("LP", "LP", "Pick LP root")),
    )
    target_kind: EnumProperty(
        name="Root Type",
        items=(
            ("AUTO", "Auto", "Prefer a selected object, otherwise use the active collection"),
            ("OBJECT", "Object", "Pick the active Blender Object"),
            ("COLLECTION", "Collection", "Pick the active Outliner collection"),
        ),
        default="AUTO",
    )

    def execute(self, context):
        # Selection can originate in VIEW_3D or Outliner.  The bridge keeps the
        # last explicit Blender context so a stale active Object cannot mask a
        # newly activated Collection.
        blender_bridge.capture_context(context)
        selected_object = blender_bridge.resolve_object(context)
        selected_collection = _selected_outliner_collection(context)
        collection = blender_bridge.resolve_collection(context) or _active_collection(context)
        kind = self.target_kind
        if kind == "AUTO":
            kind, target = blender_bridge.resolve_auto(context)
        else:
            target = None
        if kind == "OBJECT":
            target = target or selected_object
        elif kind == "COLLECTION":
            target = target or selected_collection or collection
        else:
            target = None
        if target is None:
            message = "Select an object or activate a collection in the Outliner first"
            self.report({"WARNING"}, message)
            state = settings(context)
            log(state, message)
            return {"CANCELLED"}
        state = settings(context)
        key = self.role.lower()
        setattr(state, "{}_object".format(key), target.name)
        setattr(state, "{}_root_kind".format(key), kind)
        if kind == "COLLECTION":
            setattr(state, "{}_collection".format(key), target)
            setattr(state, "{}_root".format(key), None)
        else:
            setattr(state, "{}_root".format(key), target)
            setattr(state, "{}_collection".format(key), None)
        message = "Picked {} {}: {}".format(self.role, kind.title(), target.name)
        log(state, message)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class BAKE_TOOLS_OT_create_pair(bpy.types.Operator):
    bl_idname = "bake_tools.create_pair"
    bl_label = "Create Chapter"
    bl_description = "Choose a chapter name and create an HP/LP pair"
    bl_options = {"REGISTER", "UNDO"}

    name_choice: EnumProperty(
        name="Name Source",
        items=(
            ("HP", "HP", "Use the HP root base name"),
            ("LP", "LP", "Use the LP root base name"),
            ("CUSTOM", "Custom", "Enter a custom chapter name"),
        ),
        default="HP",
    )
    custom_name: StringProperty(name="Custom Name", default="")
    hp_base: StringProperty(name="HP Base", default="", options={"HIDDEN", "SKIP_SAVE"})
    lp_base: StringProperty(name="LP Base", default="", options={"HIDDEN", "SKIP_SAVE"})
    material_slots: BoolProperty(
        name="LP Material Slots",
        description="Keep several LP materials in one chapter for material-aware HP analysis",
        default=False,
        options={"HIDDEN", "SKIP_SAVE"},
    )

    @staticmethod
    def _base_name(root, role):
        name = root.name.strip()
        suffix = "_{}".format(role.upper())
        if name.upper().endswith(suffix):
            candidate = name[:-len(suffix)].rstrip("_.- ")
            if candidate:
                return candidate
        # A root literally named HP or LP still needs a useful visible choice.
        return name or "BakeGroup"

    def invoke(self, context, _event):
        state = settings(context)
        _hp_kind, hp_root = _picked_root(state, "HP")
        _lp_kind, lp_root = _picked_root(state, "LP")
        if hp_root is None or lp_root is None:
            return self.execute(context)
        self.hp_base = self._base_name(hp_root, "HP")
        self.lp_base = self._base_name(lp_root, "LP")
        self.name_choice = "HP"
        self.custom_name = ""
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, _context):
        layout = self.layout
        layout.label(text="Choose name for chapter:", icon="OUTLINER_OB_GROUP_INSTANCE")
        info = layout.box()
        info.label(text="HP: {}".format(self.hp_base or "None"), icon="MESH_DATA")
        info.label(text="LP: {}".format(self.lp_base or "None"), icon="MESH_DATA")
        layout.prop(self, "name_choice", expand=True)
        custom = layout.row()
        custom.enabled = self.name_choice == "CUSTOM"
        custom.prop(self, "custom_name", text="Chapter")

    def execute(self, context):
        state = settings(context)
        hp_kind, hp_root = _picked_root(state, "HP")
        lp_kind, lp_root = _picked_root(state, "LP")
        if hp_root is None or lp_root is None:
            self.report({"WARNING"}, "Pick HP and LP before creating a group")
            log(state, "Pick HP and LP before creating a group")
            return {"CANCELLED"}
        if hp_kind == lp_kind and hp_root == lp_root:
            self.report({"WARNING"}, "HP and LP roots must be different")
            log(state, "HP and LP roots must be different")
            return {"CANCELLED"}
        if self.hp_base or self.lp_base:
            if self.name_choice == "LP":
                requested_name = self.lp_base
            elif self.name_choice == "CUSTOM":
                requested_name = self.custom_name.strip()
            else:
                requested_name = self.hp_base
        else:
            # EXEC_DEFAULT remains deterministic for scripts/headless tests.
            requested_name = state.group_name.strip() or self._base_name(hp_root, "HP")
        if not requested_name:
            self.report({"WARNING"}, "Enter a chapter name")
            return {"CANCELLED"}

        pair = state.pairs.add()
        pair.item_id = uuid4().hex
        pair.name = _unique_name({item.name for item in state.pairs[:-1]}, requested_name, "BakeGroup")
        _assign_pair_roots(pair, hp_kind, hp_root, lp_kind, lp_root)
        pair.material_slots = bool(self.material_slots)
        if not pair.material_slots:
            material_summary = inspect_root_materials(lp_root)
            if material_summary.count == 1:
                pair.book = clean_material_name(material_summary.names[0])
        _set_active_pair(state, len(state.pairs) - 1, pair)
        material_note = " | LP materials: {}".format(
            inspect_root_materials(lp_root).count
        )
        log(state, "Created chapter: {}{}".format(pair.name, material_note))
        _clear_picked_roots(state)
        return {"FINISHED"}


class BAKE_TOOLS_OT_create_pairs_by_material(bpy.types.Operator):
    bl_idname = "bake_tools.create_pairs_by_material"
    bl_label = "Create Chapters by LP Materials"
    bl_description = "Create scoped chapters from the LP material layout"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        state = settings(context)
        hp_kind, hp_root = _picked_root(state, "HP")
        lp_kind, lp_root = _picked_root(state, "LP")
        if hp_root is None or lp_root is None:
            self.report({"WARNING"}, "Pick HP and LP before creating chapters")
            return {"CANCELLED"}
        if hp_kind == lp_kind and hp_root == lp_root:
            self.report({"WARNING"}, "HP and LP roots must be different")
            return {"CANCELLED"}
        if not target_meshes(lp_root):
            self.report({"WARNING"}, "No LP meshes found under the picked root")
            log(state, "Create by material: no LP meshes found")
            return {"CANCELLED"}
        if not target_meshes(hp_root):
            self.report({"WARNING"}, "No HP meshes found under the picked root")
            log(state, "Create by material: no HP meshes found")
            return {"CANCELLED"}

        existing = {pair.name for pair in state.pairs}
        book = next_book_name(state)
        first_index = len(state.pairs)
        try:
            with progress_scope("Creating chapters by LP materials", "Scanning LP materials") as progress:
                progress.update(5, "Scanning LP materials")
                distribution = analyze_material_distribution(
                    hp_root, lp_root, progress=progress, context=context
                )
                buckets = distribution.buckets
                if not buckets:
                    self.report({"WARNING"}, "No LP meshes found under the picked root")
                    log(state, "Create by material: no LP meshes found")
                    return {"CANCELLED"}
                progress.update(82, "Creating material chapters")
                for bucket_index, bucket in enumerate(buckets):
                    progress.update(82 + int(bucket_index * 17 / max(1, len(buckets))), "Creating chapter: {}".format(bucket.label))
                    pair = state.pairs.add()
                    pair.item_id = uuid4().hex
                    pair.name = _unique_name(existing, bucket.label, "Material")
                    existing.add(pair.name)
                    pair.book = book
                    pair.scope_by_members = True
                    _assign_pair_roots(pair, hp_kind, hp_root, lp_kind, lp_root)
                    add_object_refs(pair.hp_scope_members, bucket.hp_objects)
                    add_object_refs(pair.lp_scope_members, bucket.lp_objects)
                progress.update(100, "Material chapters created")
        except ProgressCancelled:
            while len(state.pairs) > first_index:
                state.pairs.remove(len(state.pairs) - 1)
            log(state, "Create by material canceled")
            self.report({"WARNING"}, "Create by material canceled")
            return {"CANCELLED"}
        except Exception:
            while len(state.pairs) > first_index:
                state.pairs.remove(len(state.pairs) - 1)
            raise

        first_pair = state.pairs[first_index]
        _set_active_pair(state, first_index, first_pair)
        material_count = inspect_root_materials(lp_root).count
        message = "Create by material: {} chapter(s) in {} from {} LP material(s)".format(
            len(buckets), book, material_count
        )
        log(state, message)
        diagnostics = distribution.diagnostics
        log(state, "Create by material: built {} LP match proxy region(s).".format(
            diagnostics.lp_proxy_count
        ))
        if diagnostics.container_count:
            log(state, "Create by material: detected {} large multi-material LP container(s).".format(
                diagnostics.container_count
            ))
        log(state, (
            "Create by material HP ownership: direct={}, container_fallback={}, "
            "floater_assigned={}, floater_reassigned={}, lp_audit_assigned={}, "
            "lp_audit_reassigned={}, review={}."
        ).format(
            diagnostics.direct_hp, diagnostics.container_hp,
            diagnostics.floater_assigned, diagnostics.floater_reassigned,
            diagnostics.lp_audit_assigned, diagnostics.lp_audit_reassigned,
            len(diagnostics.review_hp),
        ))
        log(state, (
            "Create by material LP audit: checked={}, candidates={}, container_conflicts={}."
        ).format(
            diagnostics.lp_audit_checked, diagnostics.lp_audit_candidates,
            diagnostics.lp_audit_container_conflicts,
        ))
        if diagnostics.low_confidence_hp:
            log(state, "Create by material: {} HP mesh(es) assigned by close LP proxy without bbox overlap.".format(
                diagnostics.low_confidence_hp
            ))
        if diagnostics.review_hp:
            log(state, "Create by material: {} HP mesh(es) moved to Review_Unmatched for manual check: {}".format(
                len(diagnostics.review_hp), ", ".join(diagnostics.review_hp[:8])
            ))
        _clear_picked_roots(state)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class BAKE_TOOLS_OT_about(bpy.types.Operator):
    bl_idname = "bake_tools.about"
    bl_label = "About Bake Tools"
    bl_options = {"INTERNAL"}

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        from . import native_core

        layout = self.layout
        layout.label(text="Bake Group Manager Pro", icon="MODIFIER")
        layout.label(text="Blender 1.0.0")
        layout.label(text="Math: {}".format(native_core.backend_name()))
        layout.separator()
        layout.label(text="Original PySide6 UI edition")
        layout.label(text="Blender {}.{}.{}".format(*bpy.app.version))
        layout.label(text="Scene: {}".format(context.scene.name), icon="SCENE_DATA")

    def execute(self, _context):
        return {"FINISHED"}


class BAKE_TOOLS_OT_save_diagnostics(bpy.types.Operator):
    """Write the Maya-equivalent debug log or a self-contained support ZIP."""

    bl_idname = "bake_tools.save_diagnostics"
    bl_label = "Save Bake Tools Diagnostics"
    bl_options = {"INTERNAL"}

    kind: EnumProperty(
        name="Kind",
        items=(("DEBUG", "Debug Log", ""), ("SUPPORT", "Support Package", "")),
        default="DEBUG",
    )
    filepath: StringProperty(name="File Path", subtype="FILE_PATH")

    def execute(self, context):
        from .diagnostics import save_debug_log, save_support_package

        state = settings(context)
        try:
            if self.kind == "SUPPORT":
                result = save_support_package(bpy.path.abspath(self.filepath), state)
                message = "Support package saved: {}".format(result)
            else:
                result = save_debug_log(bpy.path.abspath(self.filepath), state)
                message = "Debug log saved: {}".format(result)
        except (OSError, ValueError, TypeError) as exc:
            message = "Failed to save diagnostics: {}".format(exc)
            log(state, message)
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        log(state, message)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class BAKE_TOOLS_OT_remove_pair(bpy.types.Operator):
    bl_idname = "bake_tools.remove_pair"
    bl_label = "Delete Bake Group"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        state = settings(context)
        pair = active_pair(state)
        if pair is None:
            return {"CANCELLED"}
        index = state.active_pair
        name = pair.name
        try:
            from .cage_service import delete_cages
            delete_cages(state, pair)
        except (AttributeError, ReferenceError, RuntimeError):
            pass
        state.pairs.remove(index)
        if state.pairs:
            index = min(index, len(state.pairs) - 1)
            _set_active_pair(state, index, state.pairs[index])
        else:
            state.active_pair = 0
            state.active_pair_id = ""
        _refresh_smooth_preview(state)
        _refresh_color_preview(state)
        log(state, "Deleted bake group: {}".format(name))
        return {"FINISHED"}


class BAKE_TOOLS_OT_add_subgroup(bpy.types.Operator):
    bl_idname = "bake_tools.add_subgroup"
    bl_label = "Add Subgroup"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(name="Name", default="")

    def execute(self, context):
        state = settings(context)
        pair = active_pair(state)
        if pair is None:
            self.report({"WARNING"}, "Create a bake group first")
            log(state, "Create or select a bake group first")
            return {"CANCELLED"}
        subgroup = pair.subgroups.add()
        subgroup.item_id = uuid4().hex
        subgroup.name = _unique_name(
            {item.name for item in pair.subgroups[:-1]}, self.name or state.group_name, "Subgroup"
        )
        state.active_subgroup = len(pair.subgroups) - 1
        moved, unchanged, skipped = ObjectRepository.assign_selected(context, pair, subgroup, state)
        ObjectRepository.sync_pair_visibility(state, pair)
        _refresh_smooth_preview(state)
        _refresh_color_preview(state)
        hp_count = sum(1 for _obj, side in moved if side == "HP")
        lp_count = sum(1 for _obj, side in moved if side == "LP")
        detail = " (HP {}, LP {})".format(hp_count, lp_count) if moved else ""
        if skipped:
            detail += " | skipped outside roots: {}".format(", ".join(skipped[:8]))
        log(state, "Added subgroup: {}{}".format(subgroup.name, detail))
        return {"FINISHED"}


class BAKE_TOOLS_OT_toggle_visibility(bpy.types.Operator):
    bl_idname = "bake_tools.toggle_visibility"
    bl_label = "Toggle Visibility"
    bl_options = {"REGISTER", "UNDO"}

    role: EnumProperty(
        name="Role",
        items=(
            ("HP", "HP", "Toggle HP visibility"),
            ("LP", "LP", "Toggle LP visibility"),
            ("GROUPS", "Groups", "Toggle subgroup visibility"),
        ),
    )

    def execute(self, context):
        state = settings(context)
        attr = {"HP": "hp_visible", "LP": "lp_visible", "GROUPS": "groups_visible"}[self.role]
        value = not getattr(state, attr)
        setattr(state, attr, value)
        pair = active_pair(state)
        if pair:
            if self.role == "GROUPS":
                for subgroup in pair.subgroups:
                    subgroup.visible = value
            ObjectRepository.sync_pair_visibility(state, pair)
            _sync_cages(state, pair)
        log(state, "{} {}".format(self.role, "Visible" if value else "Hidden"))
        return {"FINISHED"}


class BAKE_TOOLS_OT_pair_action(bpy.types.Operator):
    bl_idname = "bake_tools.pair_action"
    bl_label = "Bake Group Action"
    bl_options = {"REGISTER", "UNDO"}

    action: EnumProperty(
        items=(
            ("ACTIVATE", "Activate", "Activate chapter"),
            ("RENAME", "Rename", "Rename chapter"),
            ("DELETE", "Delete", "Delete chapter"),
            ("TOGGLE_VISIBLE", "Visibility", "Toggle chapter visibility"),
            ("SET_BOOK", "Set Book", "Move chapter to a book"),
            ("EXTRACT_BOOK", "Extract", "Extract chapter from its book"),
            ("SELECT_MESHES", "Select Meshes", "Select chapter roots"),
        )
    )
    pair_id: StringProperty(default="")
    value: StringProperty(default="")

    def execute(self, context):
        state = settings(context)
        index, pair = pair_by_id(state, self.pair_id)
        if pair is None:
            return {"CANCELLED"}
        _set_active_pair(state, index, pair, toggle_same=self.action == "ACTIVATE")
        if self.action == "ACTIVATE":
            if not state.final_view and not pair.visible:
                pair.visible = True
                ObjectRepository.sync_all_pair_visibility(state)
            isolation = "isolated" if state.chapter_isolated else "all chapters shown"
            message = "Active chapter: {} ({})".format(pair.name, isolation)
        elif self.action == "RENAME":
            old = pair.name
            pair.name = _unique_name({p.name for p in state.pairs if p.item_id != pair.item_id}, self.value, old)
            message = "Renamed {} to {}".format(old, pair.name)
        elif self.action == "DELETE":
            name = pair.name
            try:
                from .cage_service import delete_cages
                delete_cages(state, pair)
            except (AttributeError, ReferenceError, RuntimeError):
                pass
            state.pairs.remove(index)
            if state.pairs:
                new_index = min(index, len(state.pairs) - 1)
                _set_active_pair(state, new_index, state.pairs[new_index])
            else:
                state.active_pair = 0
                state.active_pair_id = ""
            ObjectRepository.sync_all_pair_visibility(state)
            _refresh_smooth_preview(state)
            message = "Deleted chapter: {}".format(name)
        elif self.action == "TOGGLE_VISIBLE":
            pair.visible = not pair.visible
            ObjectRepository.sync_pair_visibility(state, pair)
            message = "{}: {}".format(pair.name, "shown" if pair.visible else "hidden")
        elif self.action == "SET_BOOK":
            pair.book = self.value.strip()
            message = "Added {} to book {}".format(pair.name, pair.book)
        elif self.action == "EXTRACT_BOOK":
            pair.book = ""
            message = "Extracted {} from book".format(pair.name)
        else:
            objects = ObjectRepository.select_objects(context, ObjectRepository.meshes_under_roots(pair))
            message = "Selected chapter meshes: {}".format(pair.name)
        _refresh_color_preview(state)
        _sync_cages(state)
        log(state, message)
        return {"FINISHED"}


class BAKE_TOOLS_OT_subgroup_action(bpy.types.Operator):
    bl_idname = "bake_tools.subgroup_action"
    bl_label = "Subgroup Action"
    bl_options = {"REGISTER", "UNDO"}

    action: EnumProperty(
        items=(
            ("ACTIVATE", "Activate", "Activate subgroup"),
            ("RENAME", "Rename", "Rename subgroup"),
            ("DELETE", "Delete", "Delete subgroup"),
            ("TOGGLE_VISIBLE", "Visibility", "Toggle subgroup visibility"),
            ("ISOLATE_VISIBLE", "Isolate Visibility", "Isolate subgroup visibility"),
            ("TOGGLE_LOCK", "Lock", "Toggle subgroup lock"),
            ("ISOLATE_LOCK", "Isolate Lock", "Lock all subgroups except this one"),
            ("ADD_SELECTED", "Add Selected", "Add selected meshes"),
            ("SELECT_MESHES", "Select Meshes", "Select subgroup meshes"),
            ("SMOOTH_UP", "Smooth +", "Increase smoothing"),
            ("SMOOTH_DOWN", "Smooth -", "Decrease smoothing"),
            ("SET_SMOOTH", "Set Smooth", "Set smoothing level"),
            ("SET_CAGE", "Set Cage", "Set cage override"),
            ("SET_COLOR", "Set Color", "Set subgroup color"),
        )
    )
    subgroup_id: StringProperty(default="")
    value: StringProperty(default="")

    def execute(self, context):
        state = settings(context)
        pair_index, pair, subgroup_index, subgroup = subgroup_by_id(state, self.subgroup_id)
        if subgroup is None:
            return {"CANCELLED"}
        _set_active_pair(state, pair_index, pair)
        state.active_subgroup = subgroup_index
        if self.action == "ACTIVATE":
            message = "Active subgroup: {}".format(subgroup.name)
        elif self.action == "RENAME":
            old = subgroup.name
            subgroup.name = _unique_name(
                {item.name for item in pair.subgroups if item.item_id != subgroup.item_id}, self.value, old
            )
            message = "Renamed {} to {}".format(old, subgroup.name)
        elif self.action == "DELETE":
            name = subgroup.name
            try:
                from .cage_service import delete_cages
                delete_cages(state, pair, (subgroup.item_id,))
            except (AttributeError, ReferenceError, RuntimeError):
                pass
            ObjectRepository.release_members(state, pair, subgroup)
            pair.subgroups.remove(subgroup_index)
            state.active_subgroup = min(subgroup_index, max(0, len(pair.subgroups) - 1))
            message = "Deleted subgroup: {}".format(name)
        elif self.action == "TOGGLE_VISIBLE":
            subgroup.visible = not subgroup.visible
            ObjectRepository.sync_pair_visibility(state, pair)
            message = "{}: {}".format(subgroup.name, "shown" if subgroup.visible else "hidden")
        elif self.action == "ISOLATE_VISIBLE":
            result = ObjectRepository.isolate_visibility(state, pair, subgroup)
            message = "{} visibility: {}".format(subgroup.name, result.replace("_", " "))
        elif self.action == "TOGGLE_LOCK":
            subgroup.locked = not subgroup.locked
            message = "{}: {}".format(subgroup.name, "locked" if subgroup.locked else "unlocked")
        elif self.action == "ISOLATE_LOCK":
            result = ObjectRepository.isolate_lock(pair, subgroup)
            message = "{} lock: {}".format(subgroup.name, result.replace("_", " "))
        elif self.action == "SMOOTH_UP":
            subgroup.smooth_level = min(3, subgroup.smooth_level + 1)
            message = "{} smooth: {}".format(subgroup.name, subgroup.smooth_level)
        elif self.action == "SMOOTH_DOWN":
            subgroup.smooth_level = max(0, subgroup.smooth_level - 1)
            message = "{} smooth: {}".format(subgroup.name, subgroup.smooth_level)
        elif self.action == "SET_SMOOTH":
            subgroup.smooth_level = min(3, max(0, int(float(self.value))))
            message = "{} smooth: {}".format(subgroup.name, subgroup.smooth_level)
        elif self.action == "SET_CAGE":
            subgroup.cage_override = max(-1.0, float(self.value))
            message = "{} cage override: {}".format(
                subgroup.name, "global" if subgroup.cage_override < 0 else subgroup.cage_override
            )
        elif self.action == "SET_COLOR":
            channels = tuple(float(value) for value in self.value.split(",")[:3])
            if len(channels) != 3:
                raise ValueError("Subgroup color needs three channels")
            subgroup.custom_color = tuple(max(0.0, min(1.0, value)) for value in channels)
            subgroup.use_custom_color = True
            message = "{} color updated".format(subgroup.name)
        elif self.action == "ADD_SELECTED":
            external_side = str(self.value or "").upper()
            moved, unchanged, skipped = ObjectRepository.assign_selected(
                context, pair, subgroup, state,
                external_side=external_side if external_side in {"HP", "LP"} else "",
            )
            ObjectRepository.sync_pair_visibility(state, pair)
            hp_count = sum(1 for _obj, side in moved if side == "HP")
            lp_count = sum(1 for _obj, side in moved if side == "LP")
            message = "Added to {}: HP {}, LP {}, unchanged {}, skipped {}".format(
                subgroup.name, hp_count, lp_count, len(unchanged), len(skipped)
            )
            if not moved and not unchanged:
                self.report({"WARNING"}, "Select mesh objects under the active HP/LP roots")
        else:
            selected = ObjectRepository.select_members(context, subgroup)
            message = "Selected {} member(s) from {}".format(len(selected), subgroup.name)
        if self.action in {"SMOOTH_UP", "SMOOTH_DOWN", "SET_SMOOTH"}:
            from .smooth_preview import refresh_subgroup_preview
            refresh_subgroup_preview(state, pair, subgroup)
        elif self.action in {"DELETE", "ADD_SELECTED"}:
            _refresh_smooth_preview(state)
        _refresh_color_preview(state)
        _sync_cages(state, pair)
        log(state, message)
        return {"FINISHED"}


_SETTING_TYPES = {
    "group_name": str, "show_algorithm": bool, "color_subgroups": bool,
    "keep_hp_structure": bool, "hp_visible": bool, "lp_visible": bool,
    "cage_visible": bool,
    "groups_visible": bool, "final_view": bool, "preview_smoothing": bool,
    "find_mode": str, "language": str, "hp_strategy": str,
    "optimization": str, "collision_pct": int, "ignore_floaters": bool,
    "adjacent_link": bool, "link_vertex": int, "link_distance": float,
    "matcher_tolerance": float, "matcher_min_hp_lp": int, "matcher_mode": str,
    "strict_geo_check": bool, "cage_wire": bool, "export_scope": str,
    "export_include_hp": bool, "export_include_lp": bool,
    "export_include_cage": bool, "export_lp_triangulate": bool, "export_files": str,
    "export_by_material": bool, "export_lp_one_file": bool,
    "zbrush_triangle_threshold": int, "export_directory": str,
}


class BAKE_TOOLS_OT_set_setting(bpy.types.Operator):
    bl_idname = "bake_tools.set_setting"
    bl_label = "Set Bake Tools Setting"
    bl_options = {"REGISTER", "UNDO"}

    setting: StringProperty(default="")
    value: StringProperty(default="")

    def execute(self, context):
        state = settings(context)
        converter = _SETTING_TYPES.get(self.setting)
        if converter is None:
            self.report({"ERROR"}, "Unknown setting: {}".format(self.setting))
            return {"CANCELLED"}
        try:
            if converter is bool:
                converted = self.value.lower() in {"1", "true", "yes", "on"}
            else:
                converted = converter(self.value)
            target = active_pair(state) if self.setting == "cage_visible" else state
            if target is None:
                raise ValueError("Select a chapter before changing Cage visibility")
            setattr(target, self.setting, converted)
        except (TypeError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if self.setting in {"hp_visible", "lp_visible", "cage_visible", "groups_visible"}:
            pair = active_pair(state)
            if pair:
                if self.setting == "groups_visible":
                    for subgroup in pair.subgroups:
                        subgroup.visible = converted
                if self.setting != "cage_visible":
                    ObjectRepository.sync_pair_visibility(state, pair)
                _sync_cages(state, pair)
        if self.setting == "cage_wire":
            from .cage_service import apply_display
            apply_display(state, active_pair(state))
        if self.setting == "color_subgroups":
            colored = _refresh_color_preview(state)
            log(state, "Color HP: {}".format(
                "colored {} HP mesh(es)".format(colored) if converted else "restored original colors"
            ))
        return {"FINISHED"}


class BAKE_TOOLS_OT_analyze_hp(bpy.types.Operator):
    """Build and atomically apply real HP subgroup membership."""

    bl_idname = "bake_tools.analyze_hp"
    bl_label = "Analyze HP"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .analysis_adapter import apply_analysis_result, capture_analysis_input
        from .analysis_service import AnalysisService

        state = settings(context)
        pair = active_pair(state)
        if pair is None:
            message = "Create or select a bake group before Analyze HP"
            log(state, message)
            self.report({"WARNING"}, message)
            return {"CANCELLED"}
        if ObjectRepository.root(pair, "HP") is None:
            message = "Active chapter has no HP root"
            log(state, message)
            self.report({"WARNING"}, message)
            return {"CANCELLED"}
        if state.keep_hp_structure:
            from .structure_adapter import preserve_hp_structure

            groups, meshes, imported = preserve_hp_structure(state, pair)
            _refresh_smooth_preview(state)
            _refresh_color_preview(state)
            if groups:
                message = "{} HP structure: {} group(s), {} mesh(es)".format(
                    "Imported" if imported else "Kept existing", groups, meshes
                )
            else:
                message = "No existing HP hierarchy groups found to keep"
            log(state, message)
            self.report({"INFO"} if groups else {"WARNING"}, message)
            return {"FINISHED"} if groups else {"CANCELLED"}

        try:
            with progress_scope("Analyze HP", "Extracting scene geometry") as progress:
                progress.update(2, "Extracting scene geometry")
                hp, lp, analysis_settings, reserved_names, object_by_key = capture_analysis_input(
                    context, pair, state
                )
                result = AnalysisService().analyze(
                    hp, lp, analysis_settings, reserved_names=reserved_names, progress=progress
                )
                progress.update(99, "Applying HP subgroup membership")
                apply_analysis_result(state, pair, result, object_by_key)
        except ProgressCancelled:
            message = "Analyze HP canceled"
            log(state, message)
            self.report({"WARNING"}, message)
            return {"CANCELLED"}
        except (RuntimeError, TypeError, ValueError) as exc:
            message = "Analyze HP failed: {}".format(exc)
            log(state, message)
            self.report({"ERROR"}, message)
            return {"CANCELLED"}

        state.active_subgroup = min(state.active_subgroup, max(0, len(pair.subgroups) - 1))
        _refresh_smooth_preview(state)
        _refresh_color_preview(state)
        state.debug_text = "\n".join(result.debug_lines)
        message = "Analyze HP: {} HP -> {} group(s); LP matched {}, unmatched {}".format(
            result.processed_hp, len(result.groups), result.matched_hp, result.unmatched_hp
        )
        log(state, message)
        if result.compound_links:
            log(state, "Compound linking: {} link(s) / {} component(s)".format(
                result.compound_links, result.compound_components
            ))
        if result.floater_links:
            log(state, "Floater/decal linking: {} link(s)".format(result.floater_links))
        for warning in result.warnings:
            log(state, "Analyze HP warning: {}".format(warning))
        self.report({"INFO"}, message)
        return {"FINISHED"}


class BAKE_TOOLS_OT_assign_lp(bpy.types.Operator):
    """Match LP root meshes to the existing HP subgroup membership."""

    bl_idname = "bake_tools.assign_lp"
    bl_label = "Assign LP Meshes"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .lp_matching_adapter import apply_lp_matching_result, capture_lp_matching_input
        from .lp_matching_service import LPMatchingService

        state = settings(context)
        pair = active_pair(state)
        if pair is None:
            message = "Create or select a bake group before Assign LP"
            log(state, message)
            self.report({"WARNING"}, message)
            return {"CANCELLED"}
        if ObjectRepository.root(pair, "LP") is None:
            message = "Active chapter has no LP root"
            log(state, message)
            self.report({"WARNING"}, message)
            return {"CANCELLED"}
        if not ObjectRepository.meshes_under_root(pair, "LP"):
            message = "No valid LP meshes found under the active LP root"
            log(state, message)
            self.report({"WARNING"}, message)
            return {"CANCELLED"}

        try:
            with progress_scope("Assign LP", "Extracting scene geometry") as progress:
                progress.update(2, "Extracting scene geometry")
                groups, lp_meshes, match_settings, materials, object_by_key, locked_count = (
                    capture_lp_matching_input(context, pair, state)
                )
                result = LPMatchingService().match(
                    groups, lp_meshes, match_settings,
                    material_key_by_lp=materials, progress=progress,
                )
                progress.update(99, "Applying LP subgroup membership")
                apply_lp_matching_result(state, pair, result, object_by_key)
        except ProgressCancelled:
            message = "Assign LP canceled"
            log(state, message)
            self.report({"WARNING"}, message)
            return {"CANCELLED"}
        except (RuntimeError, TypeError, ValueError) as exc:
            message = "Assign LP failed: {}".format(exc)
            log(state, message)
            self.report({"ERROR"}, message)
            return {"CANCELLED"}

        state.active_subgroup = min(state.active_subgroup, max(0, len(pair.subgroups) - 1))
        _refresh_smooth_preview(state)
        state.debug_text = "\n".join(result.debug_lines)
        unmatched = len(result.unmatched_lp_keys)
        message = "Assign LP: matched {} of {} mesh(es); unmatched {}".format(
            result.matched_lp, result.processed_lp, unmatched
        )
        if locked_count:
            message += "; preserved locked {}".format(locked_count)
        log(state, message)
        if result.material_repairs:
            log(state, "Assign LP material check: repaired {} mesh(es)".format(
                result.material_repairs
            ))
        for warning in result.warnings:
            log(state, "Assign LP warning: {}".format(warning))
        self.report({"INFO"}, message)
        return {"FINISHED"}


class BAKE_TOOLS_OT_action(bpy.types.Operator):
    bl_idname = "bake_tools.action"
    bl_label = "Bake Tools Action"
    bl_options = {"REGISTER", "UNDO"}

    action: EnumProperty(
        name="Action",
        items=tuple(
            (name, label, label)
            for name, label in (
                ("COMBINE", "Combine"), ("SEPARATE", "Separate"),
                ("FIND_ZBRUSH", "Find ZBrush"), ("CHECK", "Check Before Analyze"),
                ("CHECK_SELECT_TRANSFORMS", "Select Unapplied Transforms"),
                ("CHECK_APPLY_TRANSFORMS", "Apply Transforms"),
                ("CHECK_SELECT_DUPLICATES", "Select Duplicate Meshes"),
                ("CHECK_REMOVE_DUPLICATES", "Remove Extra Copies"),
                ("CHECK_SELECT_ZBRUSH", "Select Possible ZBrush Meshes"),
                ("CHECK_ADD_ZBRUSH", "Add to ZBrush Layer"),
                ("CHECK_SELECT_COMBINED", "Select Combined Meshes"),
                ("CHECK_SEPARATE_COMBINED", "Separate Combined Meshes"),
                ("ZBRUSH_ADD_SELECTED", "Add Selected to ZBrush Layer"),
                ("ZBRUSH_SELECT_LAYER", "Select ZBrush Layer Meshes"),
                ("ANALYZE_HP", "Analyze HP"), ("ASSIGN_LP", "Assign LP Meshes"),
                ("FIND_SIM", "Find Similar"), ("SMOOTH", "Smooth View"),
                ("EXPORT_SETTINGS", "Export Settings"), ("EXPORT", "Export"),
                ("GT_MATCH", "GT Match"), ("FIND_GROUPS", "Find Groups"),
                ("RELOCATE", "Relocate"), ("LINK", "Link"), ("UNLINK", "Unlink"),
                ("NEW", "New"), ("SAVE_SESSION", "Save Session"),
                ("SAVE_DEBUG", "Save Debug"), ("CLEAR_LOG", "Clear Log"),
                ("CAGE_CREATE", "Create Cage"), ("CAGE_SCULPT", "Sculpt Cage"),
                ("CAGE_FIND", "Find Cage Intersections"), ("CAGE_EXPORT", "Export Cage"),
                ("CAGE_DELETE", "Delete Cage"), ("CAGE_EXPANSION", "Cage Expansion"),
                ("CAGE_NORMAL", "Cage Normal Move"),
                ("SUBGROUP_SMOOTH_BATCH", "Batch Subgroup Smooth"),
                ("FIND_SUBGROUP", "Find Subgroup by Mesh"),
                ("OPTIMIZE_GROUPS", "Delete Empty Subgroups"),
            )
        ),
    )
    value: StringProperty(default="")

    def execute(self, context):
        state = settings(context)
        if self.action == "SUBGROUP_SMOOTH_BATCH":
            pair = active_pair(state)
            if pair is None:
                return {"CANCELLED"}
            try:
                payload = json.loads(self.value or "{}")
            except (TypeError, ValueError):
                payload = {}
            subgroup_ids = {str(value) for value in payload.get("subgroups", ()) if value}
            mode = str(payload.get("mode") or "SET").upper()
            level = min(3, max(0, int(payload.get("level", 0) or 0)))
            from .smooth_preview import refresh_subgroup_preview
            changed = []
            for subgroup in pair.subgroups:
                if subgroup.item_id not in subgroup_ids:
                    continue
                if mode == "UP":
                    subgroup.smooth_level = min(3, subgroup.smooth_level + 1)
                elif mode == "DOWN":
                    subgroup.smooth_level = max(0, subgroup.smooth_level - 1)
                else:
                    subgroup.smooth_level = level
                refresh_subgroup_preview(state, pair, subgroup)
                changed.append(subgroup)
            if not changed:
                return {"CANCELLED"}
            message = "Smooth {}: {} subgroup(s)".format(mode.lower(), len(changed))
            log(state, message); self.report({"INFO"}, message)
            return {"FINISHED"}
        if self.action in {"GT_MATCH", "FIND_GROUPS", "RELOCATE", "LINK", "UNLINK", "NEW"}:
            from .matcher import (
                find_groups, link_clusters, new_cluster, relocate_clusters,
                select_clusters, unlink_clusters,
            )
            pair = active_pair(state)
            if pair is None:
                message = "Create or select a chapter before HP-LP Matcher"
                log(state, message); self.report({"WARNING"}, message)
                return {"CANCELLED"}
            cluster_ids = tuple(value for value in self.value.split("|") if value)
            try:
                if self.action == "FIND_GROUPS":
                    with progress_scope("HP -> LP Matcher", "Collecting meshes") as progress:
                        found = find_groups(context, state, pair, progress)
                    message = "HP-LP Matcher: found {} proposal(s)".format(found)
                elif self.action == "GT_MATCH":
                    selected = select_clusters(context, pair, cluster_ids)
                    return {"FINISHED"}
                elif self.action == "LINK":
                    groups, meshes, overridden = link_clusters(context, pair, cluster_ids)
                    message = "Matcher: {} {} group(s), {} HP mesh(es)".format(
                        "overrode" if overridden else "linked", groups, meshes
                    )
                elif self.action == "UNLINK":
                    count = unlink_clusters(pair, cluster_ids)
                    message = "Matcher: unlinked {} group(s)".format(count)
                elif self.action == "NEW":
                    name, count = new_cluster(context, pair)
                    message = "Matcher: created {} with {} selected HP mesh(es)".format(name, count)
                else:
                    moved, skipped = relocate_clusters(state, pair)
                    message = "Matcher Relocate: moved {} membership(s), skipped {} link(s) without target".format(moved, skipped)
            except ProgressCancelled:
                message = "HP-LP Matcher canceled"
                log(state, message); self.report({"WARNING"}, message)
                return {"CANCELLED"}
            except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
                message = "HP-LP Matcher failed: {}".format(exc)
                log(state, message); self.report({"WARNING"}, message)
                return {"CANCELLED"}
            _refresh_color_preview(state)
            log(state, message); self.report({"INFO"}, message)
            return {"FINISHED"}
        if self.action in {
            "COMBINE", "SEPARATE", "FIND_ZBRUSH", "CHECK",
            "CHECK_SELECT_TRANSFORMS", "CHECK_APPLY_TRANSFORMS",
            "CHECK_SELECT_DUPLICATES", "CHECK_REMOVE_DUPLICATES",
            "CHECK_SELECT_ZBRUSH", "CHECK_ADD_ZBRUSH",
            "CHECK_SELECT_COMBINED", "CHECK_SEPARATE_COMBINED",
            "ZBRUSH_ADD_SELECTED", "ZBRUSH_SELECT_LAYER",
        }:
            from .mesh_tools import (
                add_check_zbrush_candidates,
                add_selected_to_zbrush,
                apply_check_transforms,
                check_active_pair,
                combine_selected,
                encode_check_payload,
                find_zbrush_candidates,
                remove_duplicate_copies,
                select_check_category,
                select_zbrush_objects,
                separate_check_candidates,
                separate_selected,
            )
            try:
                if self.action == "COMBINE":
                    with progress_scope("Combine", "Combining selected meshes", cancellable=False) as progress:
                        progress.update(10, "Combining selected meshes")
                        combined, input_count = combine_selected(context, state)
                        progress.update(100, "Combine complete")
                    message = "Combine: {} mesh(es) -> {}".format(input_count, combined.name)
                elif self.action == "SEPARATE":
                    with progress_scope("Separate", "Reading loose mesh parts", cancellable=False) as progress:
                        parts, source_count = separate_selected(context, state, progress)
                    message = "Separate: {} source mesh(es) -> {} part(s)".format(
                        source_count, len(parts)
                    )
                elif self.action == "FIND_ZBRUSH":
                    pair = active_pair(state)
                    if pair is None:
                        raise ValueError("Create or select a chapter before Find ZBrush")
                    with progress_scope("Find ZBrush", "Checking triangular faces") as progress:
                        found, best = find_zbrush_candidates(context, state, pair, progress)
                    if found:
                        message = "Find ZBrush: selected {} HP mesh(es), threshold {}%, best {:.1f}%".format(
                            len(found), state.zbrush_triangle_threshold, best
                        )
                    else:
                        message = "Find ZBrush: no HP meshes with {}%+ triangular faces".format(
                            state.zbrush_triangle_threshold
                        )
                elif self.action == "ZBRUSH_ADD_SELECTED":
                    selected, added = add_selected_to_zbrush(context, state)
                    message = "ZBrush layer: {} selected mesh(es), {} newly added".format(
                        len(selected), len(added)
                    )
                elif self.action == "ZBRUSH_SELECT_LAYER":
                    selected = select_zbrush_objects(context, state)
                    message = "ZBrush layer: selected {} remembered mesh(es)".format(len(selected))
                elif self.action == "CHECK":
                    pair = active_pair(state)
                    if pair is None:
                        raise ValueError("Create or select a chapter before Check Mesh")
                    with progress_scope("Mesh Check", "Reading evaluated geometry") as progress:
                        result = check_active_pair(context, state, pair, progress)
                    state.mesh_check_report = result.report
                    state.mesh_check_issue_count = result.issue_count
                    state.mesh_check_payload = encode_check_payload(result, pair)
                    message = "Check Mesh: {} issue mesh(es) found".format(
                        result.issue_count
                    )
                else:
                    pair = active_pair(state)
                    if pair is None:
                        raise ValueError("Create or select a chapter before resolving Mesh Check")
                    if self.action.startswith("CHECK_SELECT_"):
                        category = self.action[len("CHECK_SELECT_"):]
                        selected = select_check_category(context, state, pair, category)
                        message = "Mesh Check: selected {} {} mesh(es)".format(
                            len(selected), category.lower()
                        )
                    elif self.action == "CHECK_APPLY_TRANSFORMS":
                        fixed, skipped = apply_check_transforms(context, state, pair)
                        message = "Apply Transforms: fixed {} object(s)".format(len(fixed))
                        if skipped:
                            message += "; skipped {} linked object(s)".format(len(skipped))
                    elif self.action == "CHECK_REMOVE_DUPLICATES":
                        removed, kept, skipped = remove_duplicate_copies(context, state, pair)
                        message = "Duplicate mesh cleanup: removed {} extra copy/copies, kept {}".format(
                            len(removed), len(kept)
                        )
                        if skipped:
                            message += "; skipped {} with children or linked data".format(len(skipped))
                    elif self.action == "CHECK_ADD_ZBRUSH":
                        candidates, added = add_check_zbrush_candidates(context, state, pair)
                        message = "ZBrush layer: {} candidate(s), {} newly added".format(
                            len(candidates), len(added)
                        )
                    elif self.action == "CHECK_SEPARATE_COMBINED":
                        with progress_scope("Separate", "Reading loose mesh parts", cancellable=False) as progress:
                            parts, source_count = separate_check_candidates(
                                context, state, pair, progress
                            )
                        message = "Combined mesh cleanup: {} source mesh(es) -> {} part(s)".format(
                            source_count, len(parts)
                        )
                    else:
                        raise ValueError("Unsupported Mesh Check action: {}".format(self.action))
                if self.action in {
                    "COMBINE", "SEPARATE", "CHECK_REMOVE_DUPLICATES",
                    "CHECK_SEPARATE_COMBINED",
                }:
                    ObjectRepository.sync_all_pair_visibility(state)
                    _refresh_color_preview(state)
            except ProgressCancelled:
                message = "{} canceled".format(self.action.replace("_", " ").title())
                log(state, message); self.report({"WARNING"}, message)
                return {"CANCELLED"}
            except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
                message = "{} failed: {}".format(self.action.replace("_", " ").title(), exc)
                log(state, message)
                self.report({"WARNING"}, message)
                return {"CANCELLED"}
            log(state, message)
            self.report({"INFO"}, message)
            return {"FINISHED"}
        if self.action == "ANALYZE_HP":
            return bpy.ops.bake_tools.analyze_hp("EXEC_DEFAULT")
        if self.action == "ASSIGN_LP":
            return bpy.ops.bake_tools.assign_lp("EXEC_DEFAULT")
        if self.action.startswith("CAGE_"):
            from .cage_service import (
                apply_display, create_cages, delete_cages, expand_cages,
                find_intersections, move_intersections, sculpt_cage, sync_visibility,
            )
            pair = active_pair(state)
            if pair is None:
                message = "Create or select a chapter before using Cage"
                log(state, message); self.report({"WARNING"}, message)
                return {"CANCELLED"}
            try:
                payload = json.loads(self.value) if self.value else {}
                if not isinstance(payload, dict):
                    payload = {}
            except (TypeError, ValueError):
                payload = {}
            subgroup_ids = tuple(str(value) for value in payload.get("subgroups", ()) if value)
            delta = float(payload.get("delta", 0.0) or 0.0)
            try:
                if self.action == "CAGE_CREATE":
                    with progress_scope("Create Cage", "Duplicating LP meshes", cancellable=False) as progress:
                        created = create_cages(context, state, pair, subgroup_ids, progress)
                    message = "Cage: created {} deflated mesh(es)".format(len(created))
                elif self.action == "CAGE_SCULPT":
                    cage = sculpt_cage(context, state, pair, subgroup_ids)
                    message = "Cage Sculpt Mode: {}".format(cage.name)
                elif self.action == "CAGE_FIND":
                    with progress_scope("Cage Intersections", "Building HP acceleration structures") as progress:
                        islands = find_intersections(context, state, pair, subgroup_ids, progress)
                    message = "Cage: found {} HP intersection island(s)".format(islands)
                elif self.action == "CAGE_EXPORT":
                    from .export_service import export_cage_only
                    with progress_scope("Export Cage", "Preparing Cage FBX") as progress:
                        path = export_cage_only(context, state, pair, progress)
                    message = "Cage exported: {}".format(path)
                elif self.action == "CAGE_DELETE":
                    count = delete_cages(state, pair, subgroup_ids)
                    message = "Cage: deleted {} mesh(es)".format(count)
                elif self.action == "CAGE_EXPANSION":
                    count = expand_cages(state, pair, delta, subgroup_ids)
                    message = "Cage Expansion: {} mesh(es), delta {}".format(count, delta)
                elif self.action == "CAGE_NORMAL":
                    moved = move_intersections(state, pair, delta, subgroup_ids)
                    message = "Cage Normal move: {} vertex operation(s), delta {}".format(moved, delta)
                else:
                    raise ValueError("Unsupported Cage action: {}".format(self.action))
                apply_display(state, pair)
                sync_visibility(state, pair)
            except ProgressCancelled:
                message = "{} canceled".format(self.action.replace("_", " ").title())
                log(state, message); self.report({"WARNING"}, message)
                return {"CANCELLED"}
            except (AttributeError, OSError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
                message = "{} failed: {}".format(self.action.replace("_", " ").title(), exc)
                state.cage_status = message
                log(state, message); self.report({"WARNING"}, message)
                return {"CANCELLED"}
            log(state, message); self.report({"INFO"}, message)
            return {"FINISHED"}
        if self.action == "CLEAR_LOG":
            state.log_text = "No log messages yet."
            return {"FINISHED"}
        if self.action == "SMOOTH":
            from .smooth_preview import apply_preview
            pair = active_pair(state)
            if pair is None:
                message = "No active chapter selected"
                log(state, message); self.report({"WARNING"}, message)
                return {"CANCELLED"}
            target = not state.preview_smoothing
            try:
                with progress_scope("Smooth View", "Preparing viewport smoothing") as progress:
                    applied, skipped = apply_preview(state, pair, target, progress)
            except ProgressCancelled:
                # Preview toggles are transactional: an interrupted enable is
                # removed; an interrupted disable restores the previous view.
                from .smooth_preview import clear_preview
                if target:
                    clear_preview(state)
                else:
                    apply_preview(state, pair, True, None)
                message = "Smooth View canceled"
                log(state, message); self.report({"WARNING"}, message)
                return {"CANCELLED"}
            state.preview_smoothing = target
            message = "Smooth View {}: {} applied, {} skipped".format(
                "enabled" if target else "disabled", applied, skipped
            )
        elif self.action == "EXPORT_SETTINGS":
            state.final_view = not state.final_view
            naming = None
            grouping = None
            if state.final_view:
                pair = active_pair(state)
                if pair is not None:
                    from .export_grouping import synchronize_export_grouping
                    from .export_service import finalize_subgroup_naming
                    grouping = synchronize_export_grouping(context.scene, pair)
                    naming = finalize_subgroup_naming(pair)
            if not state.final_view and state.preview_smoothing:
                from .smooth_preview import clear_preview
                clear_preview(state)
                state.preview_smoothing = False
            _sync_cages(state)
            message = "Export Settings {}".format("opened" if state.final_view else "closed")
            if naming is not None:
                message += "; finalized naming: HP {}, LP {}, renamed {}".format(
                    naming["hp"], naming["lp"], naming["changed"]
                )
                if grouping is not None:
                    message += "; regrouped: HP {}, LP {}, collections {}".format(
                        grouping["hp"], grouping["lp"], grouping["collections"]
                    )
                if naming["unassigned_hp"]:
                    log(state, "Export naming warning: {} unassigned HP mesh(es)".format(len(naming["unassigned_hp"])))
                if naming["unassigned_lp"]:
                    log(state, "Export naming warning: {} unassigned LP mesh(es)".format(len(naming["unassigned_lp"])))
                for collision in naming["collisions"]:
                    log(state, "Export naming collision: " + collision)
        elif self.action == "FIND_SIM":
            from .find_similar import find_similar
            pair = active_pair(state)
            if pair is None:
                message = "No active chapter selected"
                log(state, message); self.report({"WARNING"}, message)
                return {"CANCELLED"}
            try:
                with progress_scope(
                    "Find All" if state.find_mode == "ALL" else "Find Sim",
                    "Reading selected meshes",
                ) as progress:
                    found, side = find_similar(context, state, pair, state.find_mode, progress)
            except ProgressCancelled:
                message = "Find {} canceled".format("All" if state.find_mode == "ALL" else "Sim")
                log(state, message); self.report({"WARNING"}, message)
                return {"CANCELLED"}
            except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
                message = "Find {} failed: {}".format("All" if state.find_mode == "ALL" else "Sim", exc)
                log(state, message); self.report({"WARNING"}, message)
                return {"CANCELLED"}
            ObjectRepository.select_objects(context, found)
            message = "Find {}: selected {} {} mesh(es)".format(
                "All" if state.find_mode == "ALL" else "Sim", len(found), side
            )
        elif self.action == "EXPORT":
            from .export_service import build_export_plan, execute_export, finalize_subgroup_naming, resolve_scope
            from .export_grouping import synchronize_export_grouping
            pair = active_pair(state)
            try:
                for export_pair in resolve_scope(state, pair):
                    synchronize_export_grouping(context.scene, export_pair)
                    finalize_subgroup_naming(export_pair)
                plan = build_export_plan(state, pair, state.export_directory)
                with progress_scope("Export", "Preparing FBX export") as progress:
                    exported = execute_export(context, plan, progress)
            except ProgressCancelled:
                message = "Export canceled"
                state.export_status = message
                log(state, message); self.report({"WARNING"}, message)
                return {"CANCELLED"}
            except (AttributeError, OSError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
                message = "Export failed: {}".format(exc)
                state.export_status = message
                log(state, message); self.report({"ERROR"}, message)
                return {"CANCELLED"}
            for warning in plan.warnings:
                log(state, "Export warning: " + warning)
            message = "Exported {} file(s) to {}".format(len(exported), state.export_directory)
            state.export_status = message
        elif self.action == "FIND_SUBGROUP":
            found = ObjectRepository.find_membership_for_selection(context, state)
            if found is None:
                message = "Selected mesh is not assigned to a Bake Tools subgroup"
                self.report({"WARNING"}, message)
            else:
                pair_index, pair, subgroup_index, subgroup, side, obj = found
                _set_active_pair(state, pair_index, pair)
                state.active_subgroup = subgroup_index
                message = "Found {} in {} / {} ({})".format(obj.name, pair.name, subgroup.name, side)
        elif self.action == "OPTIMIZE_GROUPS":
            pair = active_pair(state)
            if pair is None:
                message = "No active chapter for subgroup optimization"
            else:
                stale = ObjectRepository.prune_missing(pair)
                removed = 0
                for index in range(len(pair.subgroups) - 1, -1, -1):
                    subgroup = pair.subgroups[index]
                    if not ObjectRepository.all_members(subgroup):
                        pair.subgroups.remove(index)
                        removed += 1
                state.active_subgroup = min(state.active_subgroup, max(0, len(pair.subgroups) - 1))
                message = "Optimized subgroups: removed {} empty, {} stale reference(s)".format(removed, stale)
        elif self.action == "SAVE_DEBUG":
            message = "Debug report ready"
        else:
            message = "{} action queued".format(self.action.replace("_", " ").title())
        log(state, message)
        self.report({"INFO"}, message)
        return {"FINISHED"}


OPERATOR_CLASSES = (
    BAKE_TOOLS_OT_pick_object, BAKE_TOOLS_OT_create_pair,
    BAKE_TOOLS_OT_create_pairs_by_material, BAKE_TOOLS_OT_about,
    BAKE_TOOLS_OT_save_diagnostics,
    BAKE_TOOLS_OT_remove_pair, BAKE_TOOLS_OT_add_subgroup, BAKE_TOOLS_OT_toggle_visibility,
    BAKE_TOOLS_OT_pair_action, BAKE_TOOLS_OT_subgroup_action,
    BAKE_TOOLS_OT_set_setting, BAKE_TOOLS_OT_analyze_hp, BAKE_TOOLS_OT_assign_lp,
    BAKE_TOOLS_OT_action,
)
