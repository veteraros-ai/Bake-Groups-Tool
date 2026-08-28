"""FBX export planning/execution for Blender Bake Tools chapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import bpy

from .material_distribution import clean_material_name
from .mesh_tools import is_zbrush_object
from .object_repository import ObjectRepository
from .smooth_preview import restore_preview_render_state, set_preview_render_state


_SAFE_NAME = re.compile(r"[^0-9A-Za-zА-Яа-яЁё._-]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class ExportTask:
    name: str
    objects: tuple
    lp_objects: tuple
    filepath: str


@dataclass(frozen=True, slots=True)
class ExportPlan:
    pairs: tuple
    tasks: tuple[ExportTask, ...]
    warnings: tuple[str, ...]
    triangulate_lp: bool = True


def _safe_name(value, fallback="BakeGroup"):
    cleaned = _SAFE_NAME.sub("_", str(value or "").strip()).strip("._")
    return cleaned or fallback


def _unique(objects):
    result, seen = [], set()
    for obj in objects:
        if obj is None or obj.type != "MESH" or obj.as_pointer() in seen:
            continue
        seen.add(obj.as_pointer()); result.append(obj)
    return tuple(result)


def _side_objects(pair, side):
    members = _unique(
        obj for subgroup in pair.subgroups for obj in ObjectRepository.valid_members(subgroup, side)
    )
    return members or _unique(ObjectRepository.meshes_under_root(pair, side))


def finalize_subgroup_naming(pair):
    """Apply Maya's persistent final naming to distributed HP and LP objects.

    Maya finalizes transform names when Export Settings opens:
    ``{chapter}_{subgroup}_high_{NNN}`` and
    ``{chapter}_{subgroup}_low_{NNN}``.  Blender object names are global, so a
    two-phase temporary rename is required to avoid ``.001`` suffixes when two
    existing members exchange/order their final names.
    """
    if pair is None:
        return {"hp": 0, "lp": 0, "changed": 0, "collisions": (), "unassigned_hp": (), "unassigned_lp": ()}
    base = _safe_name(pair.name, "Chapter").replace(".", "_")
    entries = []
    seen = set()
    assigned = {"HP": set(), "LP": set()}
    for subgroup in pair.subgroups:
        subgroup_name = _safe_name(subgroup.name, "Group").replace(".", "_")
        for side, suffix in (("HP", "high"), ("LP", "low")):
            members = sorted(
                _unique(ObjectRepository.valid_members(subgroup, side)),
                key=lambda obj: (obj.name.casefold(), obj.name, obj.as_pointer()),
            )
            for index, obj in enumerate(members, 1):
                pointer = obj.as_pointer()
                assigned[side].add(pointer)
                if pointer in seen:
                    continue
                seen.add(pointer)
                desired = "{}_{}_{}_{:03d}".format(base, subgroup_name, suffix, index)
                entries.append((obj, desired, side, subgroup))

    changed_entries = [entry for entry in entries if entry[0].name != entry[1]]
    old_names = {obj.as_pointer(): obj.name for obj, _desired, _side, _subgroup in changed_entries}
    token = str(pair.item_id or "pair")[:10]
    for index, (obj, _desired, _side, _subgroup) in enumerate(changed_entries):
        obj.name = "__BakeTools_Finalize_{}_{:04d}".format(token, index)

    collisions = []
    for obj, desired, _side, subgroup in entries:
        existing = bpy.data.objects.get(desired)
        final_name = desired
        if existing is not None and existing != obj:
            # Never rename an unrelated artist object.  Keep the intended base
            # readable while recording the Blender-global name collision.
            suffix = 1
            while bpy.data.objects.get("{}_BT{:02d}".format(desired, suffix)) is not None:
                suffix += 1
            final_name = "{}_BT{:02d}".format(desired, suffix)
            collisions.append("{} -> {}".format(desired, final_name))
        obj.name = final_name
        if obj.data is not None and obj.data.users == 1:
            obj.data.name = final_name
        refs = subgroup.hp_members if _side == "HP" else subgroup.lp_members
        for ref in refs:
            if ref.target == obj:
                ref.last_name = final_name

    # Cages remember their LP source by object name.  Preserve that link when
    # an already-created Cage is revisited in Export Settings.
    if old_names:
        renamed_by_old = {
            old_names[obj.as_pointer()]: obj.name
            for obj, _desired, side, _subgroup in changed_entries if side == "LP"
        }
        for cage in bpy.data.objects:
            source_name = str(cage.get("bake_tools_cage_source", ""))
            pair_id = str(cage.get("bake_tools_pair_id", ""))
            if source_name in renamed_by_old and (not pair_id or pair_id == str(pair.item_id)):
                cage["bake_tools_cage_source"] = renamed_by_old[source_name]

    unassigned = {}
    for side in ("HP", "LP"):
        unassigned[side] = tuple(
            obj.name for obj in ObjectRepository.meshes_under_root(pair, side)
            if obj.as_pointer() not in assigned[side]
        )
    return {
        "hp": sum(1 for _obj, _desired, side, _subgroup in entries if side == "HP"),
        "lp": sum(1 for _obj, _desired, side, _subgroup in entries if side == "LP"),
        "changed": len(changed_entries),
        "collisions": tuple(collisions),
        "unassigned_hp": unassigned["HP"],
        "unassigned_lp": unassigned["LP"],
    }


def _cage_objects(pair):
    pair_id = str(pair.item_id)
    name = str(pair.name).lower()
    return _unique(
        obj for obj in bpy.data.objects
        if (
            bool(obj.get("bake_tools_cage", False))
            and (not obj.get("bake_tools_pair_id") or str(obj.get("bake_tools_pair_id")) == pair_id)
        ) or (obj.type == "MESH" and "cage" in obj.name.lower() and name in obj.name.lower())
    )


def _lp_material_names(pairs):
    names = set()
    for pair in pairs:
        for obj in _side_objects(pair, "LP"):
            for slot in obj.material_slots:
                if slot.material:
                    names.add(clean_material_name(slot.material.name).casefold())
    return names


def _append_separate_tasks(tasks, pair, directory, include_hp, include_lp, include_cage):
    base = _safe_name(pair.name)
    hp = _side_objects(pair, "HP") if include_hp else ()
    lp = _side_objects(pair, "LP") if include_lp else ()
    cage = _cage_objects(pair) if include_cage else ()
    for label, objects, lp_side in (("HP", hp, ()), ("LP", lp, lp), ("Cage", cage, ())):
        objects = _unique(objects)
        if objects:
            tasks.append(ExportTask(
                "{} {}".format(pair.name, label), objects, tuple(lp_side),
                str(directory / (base + "_" + label + ".fbx")),
            ))


def _append_material_book_tasks(tasks, pairs, book, directory, include_hp, include_lp, include_cage):
    """Mirror Maya: a material-named book is merged; a container book is not."""
    material_names = _lp_material_names(pairs)
    if clean_material_name(book).casefold() not in material_names:
        for pair in pairs:
            _append_separate_tasks(tasks, pair, directory, include_hp, include_lp, include_cage)
        return
    base = _safe_name(book)
    parts = (
        ("HP", _unique(obj for pair in pairs for obj in _side_objects(pair, "HP")) if include_hp else (), ()),
        ("LP", _unique(obj for pair in pairs for obj in _side_objects(pair, "LP")) if include_lp else (), "LP"),
        ("Cage", _unique(obj for pair in pairs for obj in _cage_objects(pair)) if include_cage else (), ()),
    )
    for label, objects, lp_marker in parts:
        if objects:
            lp_objects = tuple(objects) if lp_marker == "LP" else ()
            tasks.append(ExportTask(
                "{} {}".format(book, label), tuple(objects), lp_objects,
                str(directory / (base + "_" + label + ".fbx")),
            ))


def resolve_scope(state, active_pair):
    if active_pair is None:
        raise ValueError("No active chapter selected")
    if state.export_scope == "CHAPTER":
        return (active_pair,)
    if state.export_scope == "BOOK":
        if not active_pair.book:
            raise ValueError("Active chapter is not in a book")
        return tuple(pair for pair in state.pairs if pair.book == active_pair.book)
    return tuple(state.pairs)


def build_export_plan(state, active_pair, directory):
    raw_directory = str(directory or "").strip()
    if not raw_directory:
        raise ValueError("Select an export directory")
    directory = Path(bpy.path.abspath(raw_directory)).expanduser()
    pairs = resolve_scope(state, active_pair)
    if not pairs:
        raise ValueError("Nothing to export in this scope")
    include_hp = bool(state.export_include_hp)
    include_lp = bool(state.export_include_lp)
    include_cage = bool(state.export_include_cage and any(_cage_objects(pair) for pair in pairs))
    if not (include_hp or include_lp or include_cage):
        raise ValueError("Nothing selected to export (Include HP / LP / Cage)")

    tasks, warnings = [], []
    lp_combined = []
    if state.export_lp_one_file and state.export_scope != "CHAPTER" and include_lp:
        for pair in pairs:
            lp_combined.extend(_side_objects(pair, "LP"))
        base = active_pair.book if state.export_scope == "BOOK" else bpy.path.display_name_from_filepath(bpy.data.filepath) or bpy.context.scene.name
        objects = _unique(lp_combined)
        if objects:
            tasks.append(ExportTask("{} LP".format(base), objects, objects, str(directory / (_safe_name(base) + "_LP.fbx"))))
        include_lp = False

    for pair in pairs:
        if state.export_include_hp and not _side_objects(pair, "HP"):
            warnings.append("Chapter '{}': no HP meshes to export".format(pair.name))
        if state.export_include_lp and not _side_objects(pair, "LP"):
            warnings.append("Chapter '{}': no LP meshes to export".format(pair.name))
        if include_cage and not _cage_objects(pair):
            warnings.append("Chapter '{}': cage included but no cage exists".format(pair.name))

    if state.export_by_material and state.export_scope != "CHAPTER":
        books = []
        for pair in pairs:
            if pair.book and pair.book not in books:
                books.append(pair.book)
        unbooked = [pair.name for pair in pairs if not pair.book]
        if unbooked:
            warnings.append("By material skips chapter(s) outside books: {}".format(", ".join(unbooked)))
        for book in books:
            book_pairs = tuple(pair for pair in pairs if pair.book == book)
            _append_material_book_tasks(tasks, book_pairs, book, directory, include_hp, include_lp, include_cage)
    else:
        for pair in pairs:
            hp = _side_objects(pair, "HP") if include_hp else ()
            lp = _side_objects(pair, "LP") if include_lp else ()
            cage = _cage_objects(pair) if include_cage else ()
            base = _safe_name(pair.name)
            # Maya combines HP+LP only. Cage is always a dedicated FBX.
            if state.export_files == "ONE" and hp and lp:
                objects = _unique(hp + lp)
                tasks.append(ExportTask(pair.name, objects, tuple(lp), str(directory / (base + ".fbx"))))
                if cage:
                    tasks.append(ExportTask("{} Cage".format(pair.name), cage, (), str(directory / (base + "_Cage.fbx"))))
            else:
                _append_separate_tasks(tasks, pair, directory, include_hp, include_lp, include_cage)
    if not tasks:
        raise ValueError("No valid meshes found for export")
    return ExportPlan(
        tuple(pairs), tuple(tasks), tuple(warnings), bool(state.export_lp_triangulate)
    )


def _temporary_triangulate(objects):
    created = []
    for obj in objects:
        modifier = obj.modifiers.new("Bake Tools LP Export Triangulate", "TRIANGULATE")
        # Blender's FBX RNA still exposes ``use_mesh_modifiers_render`` but its
        # own description says that path has been disabled since Blender 2.8.
        # FBX therefore evaluates the viewport modifier stack.  Keep temporary
        # export modifiers visible there for the duration of the transaction;
        # _export_fbx removes them immediately in ``finally``.
        modifier.show_viewport = True
        modifier.show_render = True
        created.append((obj, modifier))
    return created


def _temporary_export_smoothing(objects, pairs, state):
    """Create render-only subdivision required by subgroup export settings.

    Smooth View is optional viewport feedback.  The FBX result must not depend
    on whether that preview happened to be enabled when Export was pressed.
    Explicitly registered ZBrush objects are the only HP objects excluded;
    subgroup names are intentionally ignored to match Maya's display-layer
    rule.
    """
    levels = {}
    for pair in pairs:
        for subgroup in pair.subgroups:
            level = int(subgroup.smooth_level)
            if level <= 0:
                continue
            for obj in ObjectRepository.valid_members(subgroup, "HP"):
                levels[obj.as_pointer()] = max(level, levels.get(obj.as_pointer(), 0))
    created = []
    for obj in objects:
        level = levels.get(obj.as_pointer(), 0)
        if level <= 0 or is_zbrush_object(state, obj):
            continue
        # An existing Smooth View modifier is temporarily made renderable by
        # set_preview_render_state(), so adding another would subdivide twice.
        if any(mod.name.startswith("Bake Tools Smooth Preview") for mod in obj.modifiers):
            continue
        modifier = obj.modifiers.new("Bake Tools Export Smooth", "SUBSURF")
        modifier.subdivision_type = "CATMULL_CLARK"
        modifier.levels = level
        modifier.render_levels = level
        # FBX uses viewport evaluation even when
        # ``use_mesh_modifiers_render=True`` is passed.  A render-only modifier
        # silently exports the original cage, which made ordinary HP meshes in
        # ZBrush-named subgroups look correct in Smooth View but remain coarse
        # in the FBX.
        modifier.show_viewport = True
        modifier.show_render = True
        created.append((obj, modifier))
    return created


def _remove_temporary_modifiers(created):
    for obj, modifier in created:
        try:
            obj.modifiers.remove(modifier)
        except (ReferenceError, RuntimeError):
            pass


def _export_fbx(context, task, pairs=(), state=None, triangulate_lp=True):
    previous_selected = tuple(context.selected_objects)
    previous_active = context.view_layer.objects.active
    visibility = []
    triangle_modifiers = []
    smooth_modifiers = []
    smooth_state = []
    try:
        for obj in previous_selected:
            obj.select_set(False)
        for obj in task.objects:
            if obj.name not in context.view_layer.objects:
                continue
            visibility.append((obj, bool(obj.hide_viewport), bool(obj.hide_get()), bool(obj.hide_render)))
            obj.hide_viewport = False; obj.hide_set(False); obj.hide_render = False
            obj.select_set(True)
        context.view_layer.objects.active = task.objects[0]
        if triangulate_lp:
            triangle_modifiers = _temporary_triangulate(task.lp_objects)
        smooth_modifiers = _temporary_export_smoothing(task.objects, pairs, state)
        smooth_state = set_preview_render_state(task.objects, True)
        Path(task.filepath).parent.mkdir(parents=True, exist_ok=True)
        result = bpy.ops.export_scene.fbx(
            filepath=task.filepath, check_existing=False, use_selection=True,
            object_types={"MESH", "EMPTY", "ARMATURE"}, use_mesh_modifiers=True,
            use_mesh_modifiers_render=True, use_triangles=False, use_custom_props=False,
            add_leaf_bones=False, bake_anim=False, path_mode="AUTO",
            axis_forward="-Z", axis_up="Y",
        )
        if "FINISHED" not in result:
            raise RuntimeError("FBX exporter returned {}".format(result))
    finally:
        restore_preview_render_state(smooth_state)
        _remove_temporary_modifiers(smooth_modifiers)
        _remove_temporary_modifiers(triangle_modifiers)
        for obj, hide_viewport, hidden, hide_render in visibility:
            obj.hide_viewport = hide_viewport; obj.hide_set(hidden); obj.hide_render = hide_render
            obj.select_set(False)
        for obj in previous_selected:
            if obj.name in context.view_layer.objects:
                obj.hide_set(False); obj.select_set(True)
        if previous_active and previous_active.name in context.view_layer.objects:
            context.view_layer.objects.active = previous_active


def execute_export(context, plan, progress=None):
    state = getattr(context.scene, "bake_tools_settings", None)
    exported = []
    for index, task in enumerate(plan.tasks):
        if progress:
            progress.update(int(index * 100 / max(1, len(plan.tasks))), "Exporting: {}".format(task.name))
        _export_fbx(context, task, plan.pairs, state, plan.triangulate_lp)
        exported.append(task.filepath)
    return tuple(exported)


def export_cage_only(context, state, pair, progress=None):
    """Export the active chapter cage through the normal FBX transaction."""
    raw_directory = str(state.export_directory or "").strip()
    if not raw_directory:
        raise ValueError("Select an export directory before exporting Cage")
    objects = _cage_objects(pair)
    if not objects:
        raise ValueError("Create Cage before exporting it")
    directory = Path(bpy.path.abspath(raw_directory)).expanduser()
    task = ExportTask(
        "{} Cage".format(pair.name), objects, (),
        str(directory / (_safe_name(pair.name) + "_Cage.fbx")),
    )
    if progress:
        progress.update(10, "Preparing Cage export")
    _export_fbx(context, task, (pair,), state)
    if progress:
        progress.update(100, "Cage export complete")
    return task.filepath
