"""UI and conservative cleanup helpers for duplicate-mesh preflight.

This module deliberately keeps detection in :mod:`bg_duplicates`; callers may
also use the legacy mixin method names as thin compatibility wrappers.
"""
from __future__ import division

try:
    import maya.cmds as cmds
except Exception:  # Importable in ordinary Python for unit tests.
    cmds = None

try:
    from PySide6 import QtCore, QtWidgets
except Exception:
    try:
        from PySide2 import QtCore, QtWidgets
    except Exception:
        QtCore = None
        QtWidgets = None

import bg_duplicates
import bg_core
import bg_localization as bg_l10n


def _stage(owner, status, **details):
    setter = getattr(owner, "_set_hp_check_stage", None)
    if callable(setter):
        try:
            setter("duplicates", status, **details)
        except Exception as exc:
            logger = getattr(owner, "log", None)
            if callable(logger):
                try:
                    logger(bg_l10n.text("Duplicate preflight status update failed: {error}").format(error=exc), "orange")
                except Exception:
                    pass
    logger = getattr(owner, "log", None)
    if callable(logger) and status in ("failed", "cancelled"):
        if status == "failed":
            error = details.get("error") or details.get("errors") or "Unknown duplicate-check error"
            if isinstance(error, (list, tuple)):
                error = "; ".join(str(item) for item in error) or "Unknown duplicate-check error"
            message = bg_l10n.text("Duplicate mesh check failed: {error}").format(error=error)
        else:
            message = bg_l10n.text("Duplicate mesh check cancelled.")
        try:
            logger(message, "orange")
        except Exception:
            pass


def _long(path):
    if cmds is None:
        return path
    matches = cmds.ls(path, long=True) or []
    return matches[0] if matches else path


def _pair_lookup(scan_result):
    result = {}
    for pair in (scan_result or {}).get("pairs", []):
        result[frozenset((pair.get("a"), pair.get("b")))] = pair
    return result


def _preference_keepers(owner, group):
    preferences = getattr(owner, "_duplicate_keep_preferences", {}) or {}
    return [path for path in group
            if preferences.get(path) or
            (_uuid(path) and preferences.get(_uuid(path)))]


def _opposed_open_groups(owner, found, scans):
    """List fully opposed groups needing an explicit keeper decision."""
    result = []
    for label, groups in found:
        pair_map = _pair_lookup((scans or {}).get(label))
        for index, group in enumerate(groups, 1):
            pairs = [pair_map.get(frozenset((left, right)))
                     for left_index, left in enumerate(group)
                     for right in group[left_index + 1:]]
            if not pairs or not all(pair and pair.get("normal_relation") == "opposed"
                                    for pair in pairs):
                continue
            orientation_keepers = {pair.get("orientation_keeper") for pair in pairs}
            if len(orientation_keepers) == 1 and None not in orientation_keepers:
                continue
            protected = [path for path in group
                         if _protected(owner, path) or _manual_conflict(owner, path)]
            if len(protected) == 1 or len(_preference_keepers(owner, group)) == 1:
                continue
            result.append((label, index, list(group)))
    return result


def _build_keeper_dialog(owner, groups):
    """Build a no-default-choice dialog; exposed separately for UI tests."""
    if QtWidgets is None:
        return None
    parent = owner if isinstance(owner, QtWidgets.QWidget) else None
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle(bg_l10n.text("Choose Keepers for Open Duplicate Meshes"))
    screen = QtWidgets.QApplication.primaryScreen()
    available = screen.availableGeometry() if screen else None
    max_width = max(1, min(760, int(available.width() * 0.85))) if available else 760
    max_height = max(1, min(620, int(available.height() * 0.80))) if available else 620
    dialog.setMaximumSize(max_width, max_height)
    layout = QtWidgets.QVBoxLayout(dialog)
    instruction = QtWidgets.QLabel(bg_l10n.text(
        "These open surfaces have opposed normals but no geometric outward direction. Choose a copy to keep for every group, or skip cleanup."), dialog)
    instruction.setWordWrap(True)
    instruction.setMaximumWidth(max(1, max_width - 32))
    layout.addWidget(instruction)
    combos = {}
    for label, index, paths in groups:
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel(bg_l10n.text(
            "Group {index}: {label}").format(index=index, label=label)))
        combo = QtWidgets.QComboBox(dialog)
        combo.addItem(bg_l10n.text("Choose a keeper..."), None)
        for path in paths:
            short_name = (cmds.ls(path, shortNames=True) or [path.split("|")[-1]])[0] \
                if cmds else path.split("|")[-1]
            combo.addItem(short_name, path)
            combo.setItemData(combo.count() - 1, path, QtCore.Qt.ToolTipRole)
        combo.setCurrentIndex(0)
        combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(22)
        combo.setMaximumWidth(max(100, min(420, max_width - 180)))
        combo.setMaxVisibleItems(12)
        row.addWidget(combo, 1)
        select = QtWidgets.QPushButton(bg_l10n.text("Select Group"), dialog)
        select.clicked.connect(lambda _checked=False, candidates=tuple(paths):
                               cmds.select(list(candidates), replace=True) if cmds else None)
        row.addWidget(select)
        layout.addLayout(row)
        combos[tuple(paths)] = combo

    buttons = QtWidgets.QHBoxLayout()
    continue_button = QtWidgets.QPushButton(bg_l10n.text("Continue Remove"), dialog)
    skip_button = QtWidgets.QPushButton(bg_l10n.text("Skip Duplicate Cleanup"), dialog)
    cancel_button = QtWidgets.QPushButton(bg_l10n.text("Cancel"), dialog)
    continue_button.setEnabled(False)
    buttons.addWidget(continue_button)
    buttons.addWidget(skip_button)
    buttons.addWidget(cancel_button)
    layout.addLayout(buttons)

    def update_continue(_index=None):
        continue_button.setEnabled(all(combo.currentIndex() > 0
                                       for combo in combos.values()))

    for combo in combos.values():
        combo.currentIndexChanged.connect(update_continue)
    continue_button.clicked.connect(lambda: dialog.done(1))
    skip_button.clicked.connect(lambda: dialog.done(2))
    cancel_button.clicked.connect(lambda: dialog.done(0))
    dialog._duplicate_keeper_combos = combos
    dialog._duplicate_instruction_label = instruction
    dialog._duplicate_continue_button = continue_button
    dialog._duplicate_skip_button = skip_button
    dialog._duplicate_cancel_button = cancel_button
    return dialog


_keeper_qapp = None


def _keeper_dialog(owner, groups):
    """Return ('continue', preferences), ('skip', {}), or ('cancel', {})."""
    global _keeper_qapp
    if QtWidgets is None:
        return "cancel", {}
    application = QtWidgets.QApplication.instance()
    if application is None:
        _keeper_qapp = QtWidgets.QApplication([])
    elif not isinstance(application, QtWidgets.QApplication):
        # Maya standalone may create a QGuiApplication but no QWidget-capable
        # QApplication. Fail closed instead of trying to replace that singleton.
        return "cancel", {}
    dialog = _build_keeper_dialog(owner, groups)
    result = dialog.exec_() if hasattr(dialog, "exec_") else dialog.exec()
    if result == 2:
        return "skip", {}
    if result != 1:
        return "cancel", {}
    choices = {}
    for paths, combo in dialog._duplicate_keeper_combos.items():
        keeper = combo.currentData()
        if keeper not in paths:
            return "cancel", {}
        choices[tuple(paths)] = keeper
    return "continue", choices


def _apply_keeper_choices(owner, choices):
    """Store transient UUID-only decisions for cleanup revalidation."""
    preferences = dict(getattr(owner, "_duplicate_keep_preferences", {}) or {})
    for group, keeper in choices.items():
        if keeper not in group:
            raise ValueError("The selected keeper is not a member of its duplicate group.")
        for path in group:
            preferences.pop(path, None)
            uid = _uuid(path)
            if uid:
                preferences.pop(uid, None)
        uid = _uuid(keeper)
        if not uid:
            raise RuntimeError("Could not resolve the selected keeper UUID.")
        preferences[uid] = True
    owner._duplicate_keep_preferences = preferences


def _flag(pair, key):
    value = pair.get(key) if pair else None
    if value is True:
        return "equal"
    if value is False:
        return "different"
    return "unknown"


def _describe(owner, found, scans=None, limit=3):
    lines, full, paths = [], [], []
    scan_by_label = scans or {}
    for label, groups in found:
        lines.append(bg_l10n.text("{label}: {groups} duplicate group(s), {meshes} mesh(es)").format(
            label=label, groups=len(groups), meshes=sum(len(g) for g in groups)))
        pair_map = _pair_lookup(scan_by_label.get(label))
        for gi, group in enumerate(groups, 1):
            keep = _choose_keep(owner, group, pair_map)
            deletions, retained, skips = _plan_group(owner, group, pair_map)
            full.append("{} | {} {}".format(label, bg_l10n.text("Group"), gi))
            if len(retained) > 1:
                full.append("  {}".format(bg_l10n.text(
                    "Conflicting manual links; this group will not be changed.")))
            elif retained:
                full.append("  {}: {}".format(bg_l10n.text("Keep"), retained[0]))
            else:
                full.append("  {}".format(bg_l10n.text(
                    "No unambiguous safe keeper; this group will not be changed.")))
            for path in group:
                role = bg_l10n.text("Keep") if path in retained else (
                    bg_l10n.text("Remove") if path in deletions else bg_l10n.text("Skip"))
                full.append("  {}: {}".format(role, path))
            for path in group:
                if path not in paths:
                    paths.append(path)
            if gi <= limit:
                lines.append("  {} {}".format(bg_l10n.text("Group"), gi))
            preview = group[:4] if gi <= limit else []
            for path in preview:
                flags = []
                normal_pairs = [pair_map.get(frozenset((path, other)))
                                for other in group if other != path]
                relations = [pair.get("normal_relation") for pair in normal_pairs if pair]
                if relations and all(value == "opposed" for value in relations):
                    normal_state = "fully opposed"
                elif relations and any(value == "partial" for value in relations):
                    normal_state = "partial differences"
                else:
                    normal_state = _flag(
                        next((pair for pair in normal_pairs if pair), None),
                        "normals_equal")
                flags.append("{} {}".format(bg_l10n.text("normals"),
                                             bg_l10n.text(normal_state)))
                for key, title in (("uv_equal", "UV"), ("materials_equal", "materials")):
                    vals = [_flag(pair_map.get(frozenset((path, other))), key)
                            for other in group if other != path]
                    state = "equal" if vals and all(x == "equal" for x in vals) else (
                        "different" if "different" in vals else "unknown")
                    flags.append("{} {}".format(bg_l10n.text(title), bg_l10n.text(state)))
                role = bg_l10n.text("Keep") if path in retained else (
                    bg_l10n.text("Remove") if path in deletions else bg_l10n.text("Skip"))
                lines.append("    {}: {} [{}]".format(role, path, ", ".join(flags)))
            if gi == limit and len(groups) > limit:
                lines.append(bg_l10n.text("  ... {count} additional groups; see Details for full paths.").format(count=len(groups) - limit))
    return "\n".join(lines), paths, "\n".join(full)


def _uuid(path):
    if cmds is None:
        return None
    try:
        return (cmds.ls(path, uuid=True) or [None])[0]
    except Exception:
        return None


def _custom_linked(owner, path):
    nodes = ([path] + (cmds.listRelatives(path, allParents=True, fullPath=True) or [])
             if cmds else [path])
    uids = {_uuid(node) for node in nodes}
    uids.discard(None)
    for pair in getattr(owner, "root_pairs", []) or []:
        manual = {item for values in (pair.get("custom_grouping", {}) or {}).values()
                  for item in (values or [])}
        if uids.intersection(manual):
            return True
    return False


def _root_identity(owner, path):
    uid = _uuid(path)
    return bool(uid and any(uid in (pair.get("hp_uuid"), pair.get("lp_uuid"))
                            for pair in getattr(owner, "root_pairs", []) or []))


def _safe_poly_history_connection(shape, transform):
    """Allow a private polyNormal history chain confined to one transform."""
    try:
        plug = shape + ".inMesh"
        sources = cmds.listConnections(plug, source=True, destination=False,
                                       plugs=True) or []
        if not sources:
            return True
        if len(sources) != 1:
            return False
        source_plug = sources[0]
        source_node = source_plug.split(".", 1)[0]
        if source_node not in (cmds.listHistory(shape, pruneDagObjects=True) or []):
            return False
        destinations = cmds.listConnections(source_plug, source=False,
                                             destination=True, plugs=True) or []
        if len(destinations) != 1:
            return False
        destination_node, destination_attr = destinations[0].rsplit(".", 1)
        destination_paths = cmds.ls(destination_node, long=True) or []
        if destination_attr != "inMesh" or shape not in destination_paths:
            return False
        history = set(cmds.listHistory(shape, pruneDagObjects=True) or [])
        allowed_types = {"polyNormal", "polyTweak", "transformGeometry",
                         "groupParts", "groupId"}
        if source_node not in history or not history:
            return False
        sibling_shapes = cmds.listRelatives(transform, shapes=True, fullPath=True) or []
        history_names = {node.split("|")[-1] for node in history}
        sibling_by_name = {path.split("|")[-1]: path for path in sibling_shapes}
        for node in history:
            if (cmds.nodeType(node) not in allowed_types or
                    cmds.referenceQuery(node, isNodeReferenced=True) or
                    cmds.listAttr(node, userDefined=True) or
                    any(cmds.lockNode(node, query=True, lock=True) or [])):
                return False
            connections = cmds.listConnections(node, source=True, destination=False,
                                               plugs=True, connections=True) or []
            if len(connections) % 2:
                return False
            for index in range(0, len(connections), 2):
                target, source = connections[index:index + 2]
                target_node, target_attr = target.rsplit(".", 1)
                source_node, source_attr = source.rsplit(".", 1)
                if target_node.split("|")[-1] != node.split("|")[-1]:
                    target, source = source, target
                    target_node, target_attr = target.rsplit(".", 1)
                    source_node, source_attr = source.rsplit(".", 1)
                if target_node.split("|")[-1] != node.split("|")[-1]:
                    return False
                source_leaf = source_node.split("|")[-1]
                source_type = cmds.nodeType(source_node)
                if source_leaf in history_names:
                    if cmds.nodeType(node) == "polyNormal":
                        valid_input = (target_attr == "inputPolymesh" and
                                       source_type == "groupParts" and
                                       source_attr == "outputGeometry")
                    elif cmds.nodeType(node) == "polyTweak":
                        valid_input = (target_attr == "inputPolymesh" and
                                       source_type == "polyNormal" and
                                       source_attr == "output")
                    elif cmds.nodeType(node) == "transformGeometry":
                        valid_input = (target_attr == "inputGeometry" and
                                       source_type == "polyTweak" and
                                       source_attr == "output")
                    elif cmds.nodeType(node) == "groupParts":
                        valid_input = (target_attr in ("groupId", "inputComponents") and
                                       source_type == "groupId" and
                                       source_attr == "groupId")
                    else:
                        valid_input = False
                elif source_leaf in sibling_by_name:
                    source_path = sibling_by_name[source_leaf]
                    parent = (cmds.listRelatives(source_path, parent=True,
                                                 fullPath=True) or [None])[0]
                    is_private_intermediate = (
                        parent == transform and
                        bool(cmds.getAttr(source_path + ".intermediateObject")) and
                        source_attr == "outMesh")
                    valid_input = (is_private_intermediate and
                                   ((cmds.nodeType(node) == "polyNormal" and
                                     target_attr == "inputPolymesh") or
                                    (cmds.nodeType(node) == "groupParts" and
                                     target_attr == "inputGeometry")))
                else:
                    # Reject animation/control connections, including drivers
                    # on normalMode and other non-geometry polyNormal inputs.
                    valid_input = False
                if not valid_input:
                    return False
            outputs = cmds.listConnections(node, source=False, destination=True,
                                           plugs=True) or []
            for output in outputs:
                output_node = output.split(".", 1)[0]
                output_leaf = output_node.split("|")[-1]
                output_paths = cmds.ls(output_node, long=True) or []
                if output_leaf in history_names or shape in output_paths:
                    continue
                # Maya's private groupId history node also registers its
                # polygon membership with the assigned shadingEngine.
                output_attr = output.rsplit(".", 1)[-1]
                if (cmds.nodeType(node) == "groupId" and
                        cmds.nodeType(output_node) == "shadingEngine" and
                        output_attr.startswith("groupNodes[")):
                    continue
                return False
        return True
    except Exception:
        return False


def _locked_subgroup(owner, path):
    if cmds is None:
        return False
    ancestors = [path] + (cmds.listRelatives(path, allParents=True, fullPath=True) or [])
    for pair in getattr(owner, "root_pairs", []) or []:
        locked = set(pair.get("locked", []) or [])
        if not locked:
            continue
        roots = []
        for key in ("hp_uuid", "lp_uuid"):
            value = pair.get(key)
            nodes = cmds.ls(value, long=True) or [] if value else []
            roots.extend(nodes)
        for node in ancestors:
            if node in roots:
                break
            if node.split("|")[-1] in locked:
                return True
    return False


def _manual_conflict(owner, path):
    return _root_identity(owner, path) or _custom_linked(owner, path) or _locked_subgroup(owner, path)


def _infer_root(owner, label, group):
    key = "lp_uuid" if str(label).upper().startswith("LP") else "hp_uuid"
    for pair in getattr(owner, "root_pairs", []) or []:
        value = pair.get(key)
        roots = (cmds.ls(value, long=True) or []) if cmds and value else []
        for root in roots:
            if all(path == root or path.startswith(root + "|") for path in group):
                return root
    for path in group:
        if path.startswith("|"):
            return "|" + path.split("|", 2)[1]
    return None


def _choose_keep(owner, group, pair_map=None):
    """Prefer protected manual identity, then explicit preference/selection, then UUID."""
    candidates = list(group)
    protected = [p for p in candidates if _protected(owner, p) or _manual_conflict(owner, p)]
    if len(protected) == 1:
        return protected[0]
    pair_map = pair_map or {}
    opposed = [pair_map.get(frozenset((left, right)))
               for index, left in enumerate(candidates)
               for right in candidates[index + 1:]]
    if opposed and all(pair and pair.get("normal_relation") == "opposed"
                       for pair in opposed):
        orientation_keepers = {pair.get("orientation_keeper") for pair in opposed}
        if len(orientation_keepers) == 1 and None not in orientation_keepers:
            return next(iter(orientation_keepers))
        preferred = _preference_keepers(owner, candidates)
        if len(preferred) == 1:
            return preferred[0]
        if cmds is not None:
            selected = set(cmds.ls(selection=True, long=True) or [])
            chosen = [p for p in candidates if _long(p) in selected]
            if len(chosen) == 1:
                return chosen[0]
        # Open surfaces have no reliable geometric outward direction. Do not
        # let UUID/name order silently choose which normal field survives.
        return None
    preferences = getattr(owner, "_duplicate_keep_preferences", {}) or {}
    preferred = []
    for path in candidates:
        uid = _uuid(path)
        if preferences.get(path) or (uid and preferences.get(uid)):
            preferred.append(path)
    if preferred:
        return sorted(preferred, key=lambda p: (_uuid(p) or "", p.lower()))[0]
    if cmds is not None:
        selected = set(cmds.ls(selection=True, long=True) or [])
        chosen = [p for p in candidates if _long(p) in selected]
        if len(chosen) == 1:
            return chosen[0]
    if cmds is not None:
        def key(path):
            uuid = _uuid(path) or ""
            return (not bool(uuid), uuid, path.lower())
        return sorted(candidates, key=key)[0]
    return sorted(candidates, key=lambda p: p.lower())[0]


def _dialog(owner, found, scans=None):
    if QtWidgets is None:
        return "select"
    details, _all_paths, complete_plan = _describe(owner, found, scans)
    box = QtWidgets.QMessageBox(owner if isinstance(owner, QtWidgets.QWidget) else None)
    box.setWindowTitle(bg_l10n.text("Duplicate Meshes Found"))
    box.setIcon(QtWidgets.QMessageBox.Warning)
    box.setText(bg_l10n.text("Duplicate meshes were found before Analyze HP."))
    box.setInformativeText(
        bg_l10n.text("Resolve duplicates before running Analyze HP. Select the found meshes, remove extra copies, or skip this warning and continue.")
        + "\n\n" + bg_l10n.text("UV and material differences are retained. Fully opposed normals are removable only when a closed mesh proves the outward copy, or exactly one candidate is selected as keeper. Other normal differences are retained.")
        + "\n\n" + details)
    box.setDetailedText(complete_plan)
    select = box.addButton(bg_l10n.text("Select"), QtWidgets.QMessageBox.ActionRole)
    remove = box.addButton(bg_l10n.text("Remove Extra Copies"), getattr(
        QtWidgets.QMessageBox, "DestructiveRole", QtWidgets.QMessageBox.ActionRole))
    skip = box.addButton(bg_l10n.text("Skip"), QtWidgets.QMessageBox.AcceptRole)
    cancel = box.addButton(bg_l10n.text("Cancel"), QtWidgets.QMessageBox.RejectRole)
    box.setDefaultButton(select)
    box.exec_()
    if box.clickedButton() is remove:
        return "remove"
    if box.clickedButton() is skip:
        return "skip"
    if box.clickedButton() is cancel:
        return "cancel"
    return "select"


def confirm(owner, hp_main, lp_main=None, excluded_paths=()):
    """Skip/select continue the parent flow; failures and cancel stop it."""
    found, scans = [], {}
    roots = [("HP", hp_main)] + ([ ("LP", lp_main) ] if lp_main else [])
    try:
        for label, root in roots:
            options = dict(excluded_paths=excluded_paths) if excluded_paths else {}
            result = bg_duplicates.scan(root, tolerance=0.001, **options)
            scans[label] = result
            status = result.get("status")
            if status not in ("passed", "found"):
                if status not in ("failed", "cancelled"):
                    status = "failed"
                _stage(owner, status, errors=result.get("errors", []))
                return False
            if result.get("groups"):
                found.append((label, result["groups"]))
    except Exception as exc:
        _stage(owner, "failed", error=str(exc))
        return False
    if not found:
        _stage(owner, "passed")
        return True
    decision = _dialog(owner, found, scans)
    if decision == "select":
        paths = _describe(owner, found, scans)[1]
        live = [p for p in paths if cmds and cmds.objExists(p)]
        if live:
            cmds.select(live, replace=True)
        _stage(owner, "found", groups=sum(len(g) for _, g in found))
        # Report the finding through the stage, but allow the parent preflight
        # to continue its other checks; the parent owns combined-check policy.
        return True
    if decision == "skip":
        _stage(owner, "skipped", groups=sum(len(g) for _, g in found),
               explicit_skip=True)
        return True
    if decision == "cancel":
        _stage(owner, "cancelled", groups=sum(len(g) for _, g in found))
        return False
    ambiguous = _opposed_open_groups(owner, found, scans)
    old_preferences = getattr(owner, "_duplicate_keep_preferences", None)
    had_preferences = hasattr(owner, "_duplicate_keep_preferences")
    if ambiguous:
        try:
            keeper_decision, choices = _keeper_dialog(owner, ambiguous)
        except Exception as exc:
            _stage(owner, "failed", error=str(exc))
            return False
        if keeper_decision == "skip":
            _stage(owner, "skipped", groups=sum(len(g) for _, g in found),
                   explicit_skip=True, remaining_groups=len(ambiguous))
            return True
        if keeper_decision != "continue":
            _stage(owner, "cancelled", groups=sum(len(g) for _, g in found))
            return False
        try:
            _apply_keeper_choices(owner, choices)
        except Exception as exc:
            _stage(owner, "failed", error=str(exc))
            return False
    try:
        owner._duplicate_cleanup_error = None
        owner._duplicate_cleanup_status = None
    except Exception:
        pass
    try:
        try:
            removed, kept, skipped = remove(owner, found, scans=scans,
                                            roots={label: root for label, root in roots})
        except Exception as exc:
            _stage(owner, "failed", error=str(exc))
            return False
    finally:
        if ambiguous:
            if had_preferences:
                owner._duplicate_keep_preferences = old_preferences
            else:
                try:
                    del owner._duplicate_keep_preferences
                except Exception:
                    pass
    cleanup_status = getattr(owner, "_duplicate_cleanup_status", None)
    if cleanup_status or getattr(owner, "_duplicate_cleanup_error", None):
        _stage(owner, cleanup_status or "failed",
               error=getattr(owner, "_duplicate_cleanup_error", None),
               removed=removed, kept=kept, skipped=skipped)
        return False
    if not removed:
        _stage(owner, "found" if skipped else "failed", kept=kept, skipped=skipped)
        return False
    status, remaining = _rescan_found(found, {
        label: root for label, root in (("HP", hp_main), ("LP", lp_main)) if root
    })
    if status == "failed":
        _stage(owner, "failed", removed=removed, errors=remaining)
        return False
    if status == "cancelled":
        _stage(owner, "cancelled", removed=removed)
        return False
    if status == "passed":
        _stage(owner, "fixed", removed=removed, kept=kept, skipped=skipped)
        return True
    _stage(owner, "found", removed=removed, remaining=remaining, skipped=skipped)
    return False


def _protected(owner, path):
    """Fail closed for references, locks, animation, hierarchy, and metadata."""
    try:
        ancestors = [path] + (cmds.listRelatives(path, allParents=True, fullPath=True) or [])
        # Deleting an instanced transform node can remove its other DAG paths,
        # including the advertised keeper. Keep such nodes for manual cleanup.
        if len(cmds.listRelatives(path, allParents=True, fullPath=True) or []) > 1:
            return True
        for node in ancestors:
            if cmds.referenceQuery(node, isNodeReferenced=True):
                return True
            try:
                if any(cmds.lockNode(node, query=True, lock=True) or []):
                    return True
            except Exception:
                return True
        if not (cmds.listRelatives(path, parent=True, fullPath=True) or []):
            return True
        if cmds.ls(path, type="transform") and cmds.nodeType(path) != "transform":
            return True
        if cmds.listRelatives(path, children=True, fullPath=True, type="transform"):
            return True
        if cmds.listAttr(path, userDefined=True):
            return True
        if cmds.listRelatives(path, shapes=True, fullPath=True):
            shapes = cmds.listRelatives(path, shapes=True, fullPath=True) or []
            history_nodes = {node for shape in shapes
                             for node in (cmds.listHistory(shape, pruneDagObjects=True) or [])}
            for shape in shapes:
                if cmds.referenceQuery(shape, isNodeReferenced=True):
                    return True
                if cmds.listAttr(shape, userDefined=True):
                    return True
                if cmds.getAttr(shape + ".intermediateObject", lock=True):
                    return True
                if (cmds.getAttr(shape + ".visibility", lock=True) or
                        cmds.connectionInfo(shape + ".visibility", isDestination=True) or
                        cmds.connectionInfo(shape + ".visibility", isSource=True) or
                        cmds.getAttr(shape + ".inMesh", lock=True)):
                    return True
                if not _safe_poly_history_connection(shape, path):
                    return True
                # A mesh output consumed outside this transform is a live
                # dependency, even if its input history is an ordinary filter.
                for attr in ("outMesh", "worldMesh[0]"):
                    destinations = cmds.listConnections(
                        shape + "." + attr, source=False, destination=True,
                        plugs=True) or []
                    for destination in destinations:
                        consumer = destination.split(".", 1)[0]
                        if consumer not in history_nodes:
                            return True
        for attr in ("translate", "translateX", "translateY", "translateZ",
                     "rotate", "rotateX", "rotateY", "rotateZ",
                     "scale", "scaleX", "scaleY", "scaleZ", "visibility"):
            plug = path + "." + attr
            if cmds.getAttr(plug, lock=True):
                return True
            if cmds.connectionInfo(plug, isDestination=True) or cmds.connectionInfo(plug, isSource=True):
                return True
        return False
    except Exception:
        return True


def _plan_group(owner, group, pair_map):
    keep = _choose_keep(owner, group, pair_map)
    protected = [p for p in group if _protected(owner, p) or _manual_conflict(owner, p)]
    deletions, skipped = [], []
    if keep is None:
        return deletions, [], list(group)
    if len(protected) > 1:
        return deletions, protected, list(group)
    # A protected/manual keeper takes precedence only when it does not
    # contradict a closed-mesh outward-orientation result.
    for left_index, left in enumerate(group):
        for right in group[left_index + 1:]:
            pair = pair_map.get(frozenset((left, right)))
            if pair and pair.get("normal_relation") == "opposed":
                outward = pair.get("orientation_keeper")
                if outward and keep != outward:
                    return [], [p for p in group if p in protected], list(group)
    for path in group:
        if path == keep:
            continue
        conflicts = False
        for other in group:
            if other == path:
                continue
            pair = pair_map.get(frozenset((path, other)))
            normal_ok = _pair_normals_allow_keep(pair, keep, group, owner)
            if (not pair or not normal_ok or any(pair.get(k) is not True for k in
                                                  ("uv_equal", "materials_equal"))):
                conflicts = True
                break
            # Pair-wide safe_attributes can be false because the chosen keeper
            # is protected; candidate-side protection is evaluated separately.
        if conflicts or _protected(owner, path) or _manual_conflict(owner, path):
            skipped.append(path)
        else:
            deletions.append(path)
    return deletions, [keep], skipped


def _selected_keeper(group):
    if cmds is None:
        return None
    selected = set(cmds.ls(selection=True, long=True) or [])
    chosen = [path for path in group if _long(path) in selected]
    return chosen[0] if len(chosen) == 1 else None


def _pair_normals_allow_keep(pair, keep, group, owner=None):
    if not pair:
        return False
    if pair.get("normals_equal") is True:
        return True
    if pair.get("normal_relation") != "opposed":
        return False
    orientation_keeper = pair.get("orientation_keeper")
    if orientation_keeper:
        return keep == orientation_keeper
    preferred = _preference_keepers(owner, group) if owner is not None else []
    keeper = preferred[0] if len(preferred) == 1 else _selected_keeper(group)
    return bool(keeper and keep == keeper)


def plan_cleanup(owner, found, scans=None):
    """Return (deletions, retained, skips); differences/protection fail closed."""
    deletions, retained, skipped = [], [], []
    for label, groups in found:
        pair_map = _pair_lookup((scans or {}).get(label))
        for group in groups:
            group_delete, group_keep, group_skip = _plan_group(owner, group, pair_map)
            deletions.extend(group_delete)
            retained.extend(group_keep)
            skipped.extend(group_skip)
    return deletions, retained, skipped


def remove(owner, found, scans=None, roots=None):
    """Explicitly remove validated extras, returning (removed, kept, skipped)."""
    if scans is None:
        scans = {}
        rescanned = []
        try:
            for label, groups in found:
                fresh_groups = {}
                root_scans = {}
                for group in groups:
                    root = (roots or {}).get(label) or _infer_root(owner, label, group)
                    if not root:
                        owner._duplicate_cleanup_status = "failed"
                        owner._duplicate_cleanup_error = "Could not resolve the duplicate scan root."
                        return 0, [], list(group)
                    root_key = _long(root)
                    result = root_scans.get(root_key)
                    if result is None:
                        result = bg_duplicates.scan(root_key, tolerance=0.001)
                        root_scans[root_key] = result
                    status = result.get("status")
                    if status not in ("passed", "found"):
                        if status not in ("failed", "cancelled"):
                            status = "failed"
                        owner._duplicate_cleanup_status = status
                        owner._duplicate_cleanup_error = "; ".join(result.get("errors", []))
                        return 0, [], list(group)
                    for fresh_group in result.get("groups", []):
                        fresh_groups[tuple(fresh_group)] = fresh_group
                    scans[label] = result
                if fresh_groups:
                    rescanned.append((label, list(fresh_groups.values())))
            found = rescanned
        except Exception as exc:
            owner._duplicate_cleanup_status = "failed"
            owner._duplicate_cleanup_error = str(exc)
            return 0, [], [p for _label, groups in found for group in groups for p in group]
        if not found:
            owner._duplicate_cleanup_status = "passed"
            return 0, [], []
    planned, kept, skipped = plan_cleanup(owner, found, scans)
    if not planned or cmds is None:
        return 0, kept, skipped + planned
    verifier = getattr(bg_duplicates, "inspect_pair", None)
    fresh_pairs = {}
    if not callable(verifier):
        # The scanner may expose no pair-level verifier. Re-scan each owning
        # root immediately before planning deletion so stale dialog data cannot
        # authorize removal after edits between detection and confirmation.
        for label, groups in found:
            scan_root = (roots or {}).get(label)
            for group in groups:
                root = scan_root or _infer_root(owner, label, group)
                if not root:
                    owner._duplicate_cleanup_status = "failed"
                    owner._duplicate_cleanup_error = "Could not resolve the duplicate scan root."
                    return 0, kept, skipped + [p for p in planned if p not in skipped]
                root_path = _long(root)
                if root_path in fresh_pairs:
                    continue
                current_scan = bg_duplicates.scan(root_path, tolerance=0.001)
                if current_scan.get("status") in ("failed", "cancelled"):
                    owner._duplicate_cleanup_status = current_scan.get("status")
                    owner._duplicate_cleanup_error = "; ".join(current_scan.get("errors", []))
                    return 0, kept, skipped + [p for p in planned if p not in skipped]
                fresh_pairs[root_path] = _pair_lookup(current_scan)
    safe = []
    for label, groups in found:
        scan_root = (roots or {}).get(label)
        for group in groups:
            for path in group:
                if path not in planned:
                    continue
                if not cmds.objExists(path) or _protected(owner, path) or _manual_conflict(owner, path):
                    skipped.append(path)
                    continue
                good = True
                keep_for_group = next((candidate for candidate in kept
                                       if candidate in group), None)
                expected_pairs = _pair_lookup((scans or {}).get(label))
                root = _long(scan_root) if scan_root else _infer_root(owner, label, group)
                pair_map = fresh_pairs.get(root, {})
                for other in group:
                    if other == path:
                        continue
                    current = verifier(path, other, 0.001) if callable(verifier) else pair_map.get(
                        frozenset((path, other)))
                    expected = expected_pairs.get(frozenset((path, other)))
                    if (not current or current.get("geometry") is not True or
                            not expected or current.get("normal_relation") !=
                            expected.get("normal_relation") or
                            not _pair_normals_allow_keep(current, keep_for_group,
                                                         [path, other], owner) or
                            any(current.get(k) is not True for k in
                                ("uv_equal", "materials_equal"))):
                        good = False
                        break
                    if current.get("safe_attributes") is not True and not (
                            _protected(owner, other) or _manual_conflict(owner, other)):
                        good = False
                        break
                if good:
                    safe.append(path)
                else:
                    skipped.append(path)
    removed = 0
    try:
        with bg_core.undo_chunk("RemoveDuplicateMeshes"):
            for path in safe:
                if not cmds.objExists(path) or _protected(owner, path) or _manual_conflict(owner, path):
                    skipped.append(path)
                    continue
                try:
                    cmds.delete(path)
                except Exception as exc:
                    try:
                        owner._duplicate_cleanup_error = str(exc)
                    except Exception:
                        pass
                    logger = getattr(owner, "log", None)
                    if callable(logger):
                        try:
                            logger(bg_l10n.text("Duplicate mesh cleanup failed: {error}").format(error=exc), "red")
                        except Exception:
                            pass
                    break
                removed += 1
        if removed:
            if hasattr(owner, "_invalidate_hp_preflight"):
                owner._invalidate_hp_preflight()
            cache = getattr(owner, "_analyze_geo_cache", None)
            if isinstance(cache, dict):
                cache.clear()
            for name in ("refresh_groups_ui", "refresh_ui", "update_ui"):
                callback = getattr(owner, name, None)
                if callable(callback):
                    try:
                        callback()
                    except Exception:
                        pass
                    break
    except Exception:
        raise
    return removed, kept, skipped


def _rescan_found(found, roots=None):
    remain = []
    try:
        scan_roots = roots if roots is not None else {}
        if roots is None:
            for label, groups in found:
                values = set()
                for group in groups:
                    for path in group:
                        root = path.split("|", 2)[1] if path.startswith("|") else None
                        if root:
                            values.add("|" + root)
                scan_roots[label] = sorted(values)
        for label, _groups in found:
            current_roots = scan_roots.get(label, [])
            if isinstance(current_roots, str):
                current_roots = [current_roots]
            for root in current_roots:
                result = bg_duplicates.scan(root, tolerance=0.001)
                if result.get("status") == "failed":
                    return "failed", result.get("errors", [])
                if result.get("status") == "cancelled":
                    return "cancelled", []
                if result.get("groups"):
                    remain.extend(result["groups"])
        return ("found", remain) if remain else ("passed", [])
    except Exception as exc:
        return "failed", [str(exc)]


def _remove_duplicate_mesh_copies(owner, found):
    return remove(owner, found)


def _confirm_duplicate_meshes_before_hp_analysis(owner, hp_main, lp_main=None):
    return confirm(owner, hp_main, lp_main)
