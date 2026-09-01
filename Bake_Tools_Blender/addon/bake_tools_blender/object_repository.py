"""Blender Object adapter for chapter roots and subgroup membership.

Maya encodes membership by reparenting transforms under ``*_HP``/``*_LP``
groups.  Reparenting is destructive in Blender (rigs, constraints and collection
organization may depend on it), so this port stores durable Object pointers and
applies the same exclusive move/select/visibility contract through metadata.
"""

from __future__ import annotations

import bpy


class ObjectRepository:
    @staticmethod
    def _append_ref(refs, obj):
        if obj is None or any(ref.target == obj for ref in refs if ref.target is not None):
            return False
        ref = refs.add()
        ref.target = obj
        ref.last_name = obj.name
        return True

    @staticmethod
    def root(pair, side):
        role = side.lower()
        kind = getattr(pair, "{}_root_kind".format(role), "OBJECT")
        if kind == "COLLECTION":
            pointer = getattr(pair, "{}_collection".format(role), None)
            if pointer is not None:
                return pointer
            return bpy.data.collections.get(getattr(pair, "{}_object".format(role), ""))
        pointer = getattr(pair, "{}_root".format(role), None)
        if pointer is not None:
            return pointer
        return bpy.data.objects.get(getattr(pair, "{}_object".format(role), ""))

    @staticmethod
    def root_kind(pair, side):
        role = side.lower()
        root = ObjectRepository.root(pair, side)
        if isinstance(root, bpy.types.Collection):
            return "COLLECTION"
        return "OBJECT" if root is not None else getattr(pair, "{}_root_kind".format(role), "")

    @staticmethod
    def root_name(pair, side):
        root = ObjectRepository.root(pair, side)
        return root.name if root is not None else getattr(pair, "{}_object".format(side.lower()), "")

    @staticmethod
    def descendants(root):
        if root is None:
            return ()
        result = []
        stack = list(root.children)
        while stack:
            obj = stack.pop()
            result.append(obj)
            stack.extend(obj.children)
        return tuple(result)

    @classmethod
    def root_objects(cls, pair, side):
        if getattr(pair, "scope_by_members", False):
            refs = getattr(pair, "{}_scope_members".format(side.lower()))
            return tuple(ref.target for ref in refs if ref.target is not None)
        root = cls.root(pair, side)
        if root is None:
            return ()
        if isinstance(root, bpy.types.Collection):
            return tuple(root.all_objects)
        return (root,) + cls.descendants(root)

    @classmethod
    def is_in_root(cls, obj, root):
        if obj is None or root is None:
            return False
        if isinstance(root, bpy.types.Collection):
            return any(candidate == obj for candidate in root.all_objects)
        if obj == root:
            return obj.type == "MESH"
        current = obj.parent
        while current is not None:
            if current == root:
                return True
            current = current.parent
        return False

    @classmethod
    def selected_meshes(cls, context):
        """Expand selected hierarchy nodes to unique mesh Objects."""
        result = []
        seen = set()
        for selected in context.selected_objects:
            candidates = (selected,) + cls.descendants(selected)
            for obj in candidates:
                if obj.type != "MESH":
                    continue
                key = obj.as_pointer()
                if key not in seen:
                    seen.add(key)
                    result.append(obj)
        return result

    @classmethod
    def meshes_under_roots(cls, pair):
        result = []
        seen = set()
        for side in ("HP", "LP"):
            for obj in cls.root_objects(pair, side):
                if obj.type == "MESH" and obj.as_pointer() not in seen:
                    seen.add(obj.as_pointer())
                    result.append(obj)
        return result

    @classmethod
    def meshes_under_root(cls, pair, side):
        return tuple(
            obj for obj in cls.root_objects(pair, side)
            if obj.type == "MESH"
        )

    @classmethod
    def classify(cls, pair, obj):
        # Explicit subgroup membership is authoritative.  This matters for
        # meshes deliberately added from outside the original HP/LP roots:
        # Blender membership is metadata/Collection based and must not require
        # destructive parenting under an artist's root object.
        subgroup, side = cls.membership(pair, obj)
        if subgroup is not None:
            return side
        if getattr(pair, "scope_by_members", False):
            hp_refs = getattr(pair, "hp_scope_members")
            lp_refs = getattr(pair, "lp_scope_members")
            in_hp = any(ref.target == obj for ref in hp_refs if ref.target is not None)
            in_lp = any(ref.target == obj for ref in lp_refs if ref.target is not None)
            if in_hp == in_lp:
                return None
            return "HP" if in_hp else "LP"
        hp_root = cls.root(pair, "HP")
        lp_root = cls.root(pair, "LP")
        in_hp = cls.is_in_root(obj, hp_root)
        in_lp = cls.is_in_root(obj, lp_root)
        if in_hp == in_lp:
            return None
        return "HP" if in_hp else "LP"

    @classmethod
    def ensure_explicit_scope(cls, pair):
        """Freeze the current chapter boundary into durable Object refs.

        A normal chapter can derive its scope from Object/Collection roots.
        Once an outside mesh is added, root ancestry alone can no longer
        represent the chapter.  Converting once to explicit scope preserves all
        current root meshes and lets the new member join without reparenting.
        """
        if getattr(pair, "scope_by_members", False):
            return False
        captured = {
            side: tuple(obj for obj in cls.root_objects(pair, side) if obj.type == "MESH")
            for side in ("HP", "LP")
        }
        pair.hp_scope_members.clear()
        pair.lp_scope_members.clear()
        for side, objects in captured.items():
            refs = getattr(pair, "{}_scope_members".format(side.lower()))
            for obj in objects:
                cls._append_ref(refs, obj)
        pair.scope_by_members = True
        return True

    @classmethod
    def assign_scope_side(cls, pair, obj, side):
        """Place ``obj`` on exactly one side of an explicit chapter scope."""
        normalized = str(side or "").upper()
        if normalized not in {"HP", "LP"}:
            raise ValueError("Scope side must be HP or LP")
        cls.ensure_explicit_scope(pair)
        opposite = "LP" if normalized == "HP" else "HP"
        cls._remove_from_collection(
            getattr(pair, "{}_scope_members".format(opposite.lower())), obj
        )
        cls._append_ref(
            getattr(pair, "{}_scope_members".format(normalized.lower())), obj
        )

    @classmethod
    def remove_scope_member(cls, pair, obj):
        if not getattr(pair, "scope_by_members", False):
            return False
        removed = False
        for refs in (pair.hp_scope_members, pair.lp_scope_members):
            removed = cls._remove_from_collection(refs, obj) or removed
        return removed

    @staticmethod
    def valid_members(subgroup, side):
        refs = getattr(subgroup, "{}_members".format(side.lower()))
        return tuple(ref.target for ref in refs if ref.target is not None)

    @classmethod
    def all_members(cls, subgroup):
        return cls.valid_members(subgroup, "HP") + cls.valid_members(subgroup, "LP")

    @staticmethod
    def _remove_from_collection(refs, obj):
        removed = False
        for index in range(len(refs) - 1, -1, -1):
            target = refs[index].target
            if target is None or target == obj:
                refs.remove(index)
                removed = True
        return removed

    @classmethod
    def remove_member_from_pair(cls, pair, obj):
        removed = False
        for subgroup in pair.subgroups:
            removed = cls._remove_from_collection(subgroup.hp_members, obj) or removed
            removed = cls._remove_from_collection(subgroup.lp_members, obj) or removed
        return removed

    @classmethod
    def membership(cls, pair, obj):
        for subgroup in pair.subgroups:
            for side in ("HP", "LP"):
                if any(member == obj for member in cls.valid_members(subgroup, side)):
                    return subgroup, side
        return None, None

    @classmethod
    def assign_selected(cls, context, pair, target_subgroup, state=None, external_side=""):
        """Move selected meshes into the target subgroup.

        Objects already belonging to the chapter keep their classified HP/LP
        role.  ``external_side`` is used only for objects outside that chapter;
        adding the first such object converts the target chapter to explicit
        scope so the operation stays non-destructive in Blender.
        """
        fallback = str(external_side or "").upper()
        if fallback not in {"", "HP", "LP"}:
            raise ValueError("External side must be HP or LP")
        moved = []
        unchanged = []
        skipped = []
        for obj in cls.selected_meshes(context):
            side = cls.classify(pair, obj)
            if side is None:
                if not fallback:
                    skipped.append(obj.name)
                    continue
                side = fallback
                if state is not None:
                    for owner_pair in state.pairs:
                        if owner_pair.item_id != pair.item_id:
                            cls.remove_scope_member(owner_pair, obj)
                cls.assign_scope_side(pair, obj, side)
            current_subgroup, current_side = cls.membership(pair, obj)
            if current_subgroup == target_subgroup and current_side == side:
                unchanged.append(obj.name)
                continue
            for owner_pair in (state.pairs if state is not None else (pair,)):
                cls.remove_member_from_pair(owner_pair, obj)
            refs = getattr(target_subgroup, "{}_members".format(side.lower()))
            ref = refs.add()
            ref.target = obj
            ref.last_name = obj.name
            moved.append((obj, side))
        for owner_pair in (state.pairs if state is not None else (pair,)):
            cls.sync_counts(owner_pair)
        return moved, unchanged, skipped

    @classmethod
    def sync_counts(cls, pair):
        for subgroup in pair.subgroups:
            subgroup.hp_count = len(cls.valid_members(subgroup, "HP"))
            subgroup.lp_count = len(cls.valid_members(subgroup, "LP"))

    @classmethod
    def prune_missing(cls, pair):
        removed = 0
        for subgroup in pair.subgroups:
            for refs in (subgroup.hp_members, subgroup.lp_members):
                for index in range(len(refs) - 1, -1, -1):
                    if refs[index].target is None:
                        refs.remove(index)
                        removed += 1
        cls.sync_counts(pair)
        return removed

    @classmethod
    def _set_visible(cls, obj, visible, include_descendants=False):
        targets = (obj,) + cls.descendants(obj) if include_descendants else (obj,)
        for target in targets:
            target.hide_viewport = not visible
            try:
                target.hide_set(not visible)
            except RuntimeError:
                pass

    @classmethod
    def sync_pair_visibility(cls, state, pair):
        """Apply chapter/root/group/subgroup gates to Blender Objects."""
        isolated = bool(getattr(state, "chapter_isolated", False) and state.active_pair_id)
        active_gate = not isolated or pair.item_id == state.active_pair_id
        pair_enabled = bool(pair.visible and active_gate)
        roots = {
            "HP": (cls.root(pair, "HP"), pair_enabled and state.hp_visible),
            "LP": (cls.root(pair, "LP"), pair_enabled and state.lp_visible),
        }
        for side, (_root, enabled) in roots.items():
            for obj in cls.root_objects(pair, side):
                cls._set_visible(obj, enabled)
        for subgroup in pair.subgroups:
            for side, (_root, enabled) in roots.items():
                visible = enabled and state.groups_visible and subgroup.visible
                for obj in cls.valid_members(subgroup, side):
                    cls._set_visible(obj, visible)

    @classmethod
    def sync_all_pair_visibility(cls, state):
        """Apply chapter isolation deterministically across the whole scene.

        Inactive chapters are processed first and the active chapter last. This
        makes an active root win safely if older files contain overlapping roots.
        """
        pairs = tuple(state.pairs)
        active_id = getattr(state, "active_pair_id", "")
        ordered = tuple(pair for pair in pairs if pair.item_id != active_id) + tuple(
            pair for pair in pairs if pair.item_id == active_id
        )
        for pair in ordered:
            cls.sync_pair_visibility(state, pair)

    @classmethod
    def release_members(cls, state, pair, subgroup):
        """Deleting metadata never deletes meshes; released meshes return to roots."""
        pair_enabled = bool(pair.visible)
        for side, enabled in (("HP", state.hp_visible), ("LP", state.lp_visible)):
            for obj in cls.valid_members(subgroup, side):
                cls._set_visible(obj, pair_enabled and enabled)

    @classmethod
    def select_members(cls, context, subgroup):
        return cls.select_objects(context, cls.all_members(subgroup))

    @staticmethod
    def select_objects(context, objects):
        members = list(objects)
        for obj in context.selected_objects:
            obj.select_set(False)
        selectable = []
        for obj in members:
            if obj.name not in context.view_layer.objects:
                continue
            obj.hide_set(False)
            obj.select_set(True)
            selectable.append(obj)
        if selectable:
            context.view_layer.objects.active = selectable[0]
        return selectable

    @classmethod
    def isolate_visibility(cls, state, pair, target):
        visible = {subgroup.item_id for subgroup in pair.subgroups if subgroup.visible}
        show_all = bool(visible) and visible == {target.item_id}
        for subgroup in pair.subgroups:
            subgroup.visible = True if show_all else subgroup == target
        state.groups_visible = True
        cls.sync_pair_visibility(state, pair)
        return "show_all" if show_all else "isolated"

    @staticmethod
    def isolate_lock(pair, target):
        groups = list(pair.subgroups)
        locked = {subgroup.item_id for subgroup in groups if subgroup.locked}
        all_locked = bool(groups) and len(locked) == len(groups)
        only_target_unlocked = locked == {subgroup.item_id for subgroup in groups if subgroup != target}
        unlock_all = all_locked or only_target_unlocked
        for subgroup in groups:
            subgroup.locked = False if unlock_all else subgroup != target
        return "unlocked_all" if unlock_all else "isolated"

    @classmethod
    def find_membership_for_selection(cls, context, state):
        selected = cls.selected_meshes(context)
        if not selected:
            return None
        obj = selected[0]
        for pair_index, pair in enumerate(state.pairs):
            subgroup, side = cls.membership(pair, obj)
            if subgroup is not None:
                subgroup_index = next(i for i, item in enumerate(pair.subgroups) if item.item_id == subgroup.item_id)
                return pair_index, pair, subgroup_index, subgroup, side, obj
        return None
