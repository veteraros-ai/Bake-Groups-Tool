"""Controller translating Qt intent into undoable Blender operators."""

from __future__ import annotations

import bpy

from .blender_bridge import operator_context
from .material_distribution import inspect_picked_lp
from .object_repository import ObjectRepository
from .store import BlenderStateStore


class ManagerController:
    def __init__(self, changed=None):
        self.store = BlenderStateStore()
        self._changed = changed or (lambda: None)

    def snapshot(self):
        return self.store.snapshot()

    def _call(self, namespace, operator, **kwargs):
        try:
            with operator_context():
                result = getattr(getattr(bpy.ops, namespace), operator)("EXEC_DEFAULT", **kwargs)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            print("Bake Tools operator failed: {}.{}: {}".format(namespace, operator, exc))
            return False
        finally:
            self._changed()
        return "FINISHED" in result

    def pick(self, role, target_kind="AUTO"):
        return self._call("bake_tools", "pick_object", role=role, target_kind=target_kind)

    def lp_material_summary(self):
        state = self.store.settings()
        return inspect_picked_lp(state) if state is not None else None

    def create_pair(self, name_choice="HP", custom_name="", material_slots=False):
        return self._call(
            "bake_tools",
            "create_pair",
            name_choice=name_choice,
            custom_name=custom_name,
            material_slots=material_slots,
        )

    def create_pairs_by_material(self):
        return self._call("bake_tools", "create_pairs_by_material")

    def pair_action(self, action, pair_id="", value=""):
        return self._call("bake_tools", "pair_action", action=action, pair_id=pair_id, value=value)

    def subgroup_action(self, action, subgroup_id="", value=""):
        return self._call(
            "bake_tools", "subgroup_action", action=action, subgroup_id=subgroup_id, value=value
        )

    def subgroup_add_selection_status(self, subgroup_id=""):
        """Return ``(mesh_count, has_outside_members)`` for the Qt role prompt."""
        state = self.store.settings()
        if state is None:
            return 0, False
        pair = next(
            (
                item for item in state.pairs
                if item.item_id == state.active_pair_id
                and any(group.item_id == subgroup_id for group in item.subgroups)
            ),
            None,
        )
        if pair is None:
            return 0, False
        try:
            with operator_context():
                selected = tuple(ObjectRepository.selected_meshes(bpy.context))
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            selected = ()
        return len(selected), any(ObjectRepository.classify(pair, obj) is None for obj in selected)

    def set_setting(self, setting, value):
        if isinstance(value, bool):
            encoded = "1" if value else "0"
        else:
            encoded = str(value)
        return self._call("bake_tools", "set_setting", setting=setting, value=encoded)

    def action(self, action, value=""):
        return self._call("bake_tools", "action", action=action, value=str(value or ""))

    def zbrush_threshold(self):
        state = self.store.settings()
        return int(state.zbrush_triangle_threshold) if state is not None else 50

    def mesh_check_result(self):
        state = self.store.settings()
        if state is None:
            return "", 0
        return str(state.mesh_check_report), int(state.mesh_check_issue_count)

    def mesh_check_payload(self):
        state = self.store.settings()
        if state is None:
            return {}
        import json
        try:
            return json.loads(state.mesh_check_payload or "{}")
        except (TypeError, ValueError):
            return {}

    def analyze_hp(self):
        return self._call("bake_tools", "analyze_hp")

    def save_diagnostics(self, kind, filepath):
        return self._call(
            "bake_tools", "save_diagnostics", kind=str(kind), filepath=str(filepath)
        )
