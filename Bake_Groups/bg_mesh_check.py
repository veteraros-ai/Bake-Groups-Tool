"""Read-only mesh checks and chapter reports for the Maya window."""
from time import monotonic

from maya import cmds
from maya.api import OpenMaya as om

from bg_ui_widgets import QtCore, QtGui, QtWidgets, configure_square_icon_button
import bg_core
import bg_duplicates
import bg_duplicate_ui
from bg_localization import text as tr


def is_in_zbrush_layer(path):
    if not path or not cmds.objExists(path):
        return False
    path = (cmds.ls(path, long=True) or [path])[0]
    nodes = [path] + (cmds.listRelatives(path, shapes=True, noIntermediate=True,
                                       fullPath=True, type="mesh") or [])
    nodes.extend("|".join(path.split("|")[:i]) for i in range(2, path.count("|") + 1))
    return any("zbrush" in layer.lower() for node in nodes
               for layer in (cmds.listConnections(node + ".drawOverride", source=True,
                                                 destination=False, type="displayLayer") or []))


def is_zbrush(path):
    # Export still recognizes the explicit naming convention for detailed HP.
    return bool(path and cmds.objExists(path) and
                ("_zbrush" in path.rsplit("|", 1)[-1].lower() or is_in_zbrush_layer(path)))


def triangle_counts(path):
    triangles = faces = 0
    details = []
    for shape in cmds.listRelatives(path, shapes=True, noIntermediate=True,
                                    fullPath=True, type="mesh") or []:
        selection = om.MSelectionList()
        selection.add(shape)
        mesh = om.MFnMesh(selection.getDagPath(0))
        counts, _ = mesh.getVertices()
        count = sum(size == 3 for size in counts)
        triangles += count
        faces += len(counts)
        details.append((shape, count, len(counts)))
    return triangles, faces, details


class MeshCheckTabBar(QtWidgets.QTabBar):
    def tabSizeHint(self, index):
        size = super().tabSizeHint(index)
        if self.count() == 2:
            width = self.parentWidget().contentsRect().width()
            size.setWidth(max(1, (width + index) // 2))
        size.setHeight(max(size.height(), 22))
        return size

    def minimumTabSizeHint(self, index):
        return QtCore.QSize(0, self.tabSizeHint(index).height())


class MeshCheckTabs(QtWidgets.QTabWidget):
    def __init__(self):
        super().__init__()
        self.setTabBar(MeshCheckTabBar(self))
        self.setDocumentMode(True)
        self.tabBar().setExpanding(True)
        self.tabBar().setUsesScrollButtons(False)
        self.tabBar().setElideMode(QtCore.Qt.ElideRight)
        self.setIconSize(QtCore.QSize(8, 8))
        self.setObjectName("meshCheckTabs")
        self.setStyleSheet("""
            QTabWidget#meshCheckTabs::pane { border: 1px solid #414141; top: -1px; }
            QTabWidget#meshCheckTabs QTabBar::tab {
                background: #2b2b2b; color: #c9c9c9;
                border: 1px solid #414141; padding: 3px 8px;
            }
            QTabWidget#meshCheckTabs QTabBar::tab:selected {
                background: #424242; color: #ffffff; border-bottom-color: #8a8a8a;
            }
        """)
        self.status_icons = {}
        for has_issues, color in ((False, "#a4d8ac"), (True, "#ff9e96")):
            pixmap = QtGui.QPixmap(8, 8)
            pixmap.fill(QtCore.Qt.transparent)
            painter = QtGui.QPainter(pixmap)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(color))
            painter.drawEllipse(0, 0, 8, 8)
            painter.end()
            self.status_icons[has_issues] = QtGui.QIcon(pixmap)

    def set_check_status(self, panel, has_issues):
        index = self.indexOf(panel)
        if index >= 0:
            self.setTabIcon(index, self.status_icons.get(has_issues, QtGui.QIcon()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        bar = self.tabBar()
        QtWidgets.QApplication.sendEvent(bar, QtCore.QEvent(QtCore.QEvent.StyleChange))
        bar.updateGeometry()


class MeshCheckTree(QtWidgets.QTreeWidget):
    def drawRow(self, painter, option, index):
        super().drawRow(painter, option, index)
        background = index.data(QtCore.Qt.BackgroundRole)
        if not isinstance(background, QtGui.QBrush):
            return
        rect = self.visualRect(index)
        rect.setRight(self.viewport().width() - 4)
        rect.adjust(0, 1, 0, -1)
        painter.save()
        painter.setPen(QtGui.QPen(QtGui.QColor("#8bb9ef")) if
                       option.state & QtWidgets.QStyle.State_Selected else QtCore.Qt.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(rect, 3, 3)
        painter.setFont(option.font)
        painter.setPen(QtGui.QColor("#f4f4f4"))
        painter.drawText(rect.adjusted(4, 0, -4, 0),
                         QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft,
                         option.fontMetrics.elidedText(index.data(), QtCore.Qt.ElideRight,
                                                       max(0, rect.width() - 8)))
        painter.restore()

    def mouseDoubleClickEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            item = self.itemAt(event.pos())
            if item is not None:
                self.itemDoubleClicked.emit(item, 0)
        event.accept()


class ThresholdSpinBox(QtWidgets.QSpinBox):
    def __init__(self):
        super().__init__()
        self.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.lineEdit().installEventFilter(self)
        self._drag_start = None
        self._dragging = False
        self._drag_label = None

    def set_drag_label(self, label):
        self._drag_label = label
        label.setCursor(QtCore.Qt.SizeHorCursor)
        label.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self.lineEdit() or watched is self._drag_label:
            if event.type() == QtCore.QEvent.MouseButtonPress and event.button() == QtCore.Qt.LeftButton:
                self._drag_start = (event.globalPos().x(), self.value())
                self._dragging = False
                if watched is self._drag_label:
                    return True
            elif event.type() == QtCore.QEvent.MouseMove and self._drag_start is not None:
                delta = event.globalPos().x() - self._drag_start[0]
                if self._dragging or abs(delta) >= QtWidgets.QApplication.startDragDistance():
                    self._dragging = True
                    self.setValue(self._drag_start[1] + int(delta / 3))
                    return True
            elif event.type() == QtCore.QEvent.MouseButtonRelease:
                self._drag_start = None
                if self._dragging:
                    self._dragging = False
                    return True
        return super().eventFilter(watched, event)


class MeshCheckPanel(QtWidgets.QWidget):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.reports = {}
        self.report_pairs = {}
        self.expanded = {}
        self.rendered_chapter = None
        self.running = False
        self.cancelled = False
        self.layout = QtWidgets.QVBoxLayout(self)
        self.status = QtWidgets.QLabel(tr("Run Mesh Check for the active chapter."))
        self.status.setWordWrap(True)
        self.status.setProperty("bg_preserve_min_width", True)
        self.layout.addWidget(self.status)
        self.tree = MeshCheckTree()
        self.tree.setExpandsOnDoubleClick(False)
        self.tree.itemDoubleClicked.connect(self.select_item)
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.tree.itemSelectionChanged.connect(self.update_actions)
        self.layout.addWidget(self.tree, 1)
        self.progress = QtWidgets.QProgressBar()
        self.layout.addWidget(self.progress)
        self.progress.hide()
        toolbar = QtWidgets.QHBoxLayout()
        self.threshold_label = QtWidgets.QLabel(tr("ZBrush threshold"))
        self.threshold_label.setProperty("bg_preserve_min_width", True)
        toolbar.addWidget(self.threshold_label)
        self.threshold = ThresholdSpinBox()
        self.threshold.setRange(1, 100)
        self.threshold.setSuffix("%")
        self.threshold.setValue(getattr(owner, "zbrush_triangle_threshold", 50))
        self.threshold.valueChanged.connect(self.set_threshold)
        toolbar.addWidget(self.threshold)
        self.threshold_label.setBuddy(self.threshold)
        self.threshold.set_drag_label(self.threshold_label)
        self.threshold_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.threshold_slider.setRange(self.threshold.minimum(), self.threshold.maximum())
        self.threshold_slider.setValue(self.threshold.value())
        self.threshold_slider.valueChanged.connect(self.threshold.setValue)
        self.threshold.valueChanged.connect(self.threshold_slider.setValue)
        self.threshold_slider.setMinimumWidth(45)
        self.threshold_slider.setStyleSheet(
            "QSlider::groove:horizontal { height: 4px; background: #252525; border-radius: 2px; }"
            "QSlider::handle:horizontal { width: 7px; margin: -5px 0; "
            "background: #bfbfbf; border-radius: 2px; }")
        toolbar.addWidget(self.threshold_slider, 1)
        self.run_button = QtWidgets.QPushButton(tr("Run check"))
        self.run_button.clicked.connect(self.run)
        self.cancel_button = QtWidgets.QPushButton(tr("Cancel"))
        self.cancel_button.clicked.connect(self.cancel)
        self.cancel_button.hide()
        self.layout.addLayout(toolbar)
        actions = QtWidgets.QHBoxLayout()
        self.back_button = QtWidgets.QPushButton()
        configure_square_icon_button(self.back_button, "Back_button.png", "Back", size=30, icon_size=20)
        self.back_button.clicked.connect(lambda: owner.mesh_check_tabs.setCurrentWidget(owner.subgroups_scroll))
        actions.addWidget(self.back_button)
        actions.addWidget(self.run_button)
        actions.addWidget(self.cancel_button)
        self.fix_button = QtWidgets.QPushButton(tr("Fix…"))
        self.fix_button.clicked.connect(self.fix)
        actions.addWidget(self.fix_button)
        self.layout.addLayout(actions)
        self.tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.show_actions)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.rendered = None
        self.retranslate()

    def retranslate(self):
        self.back_button.setToolTip(tr("Back"))
        self.back_button.setAccessibleName(tr("Back"))
        self.threshold_label.setText(tr("ZBrush threshold"))
        self.threshold.setToolTip(tr("Minimum percentage of triangular faces for an HP candidate"))
        self.threshold_slider.setToolTip(self.threshold.toolTip())
        self.threshold_slider.setAccessibleName(self.threshold_label.text())
        self.run_button.setText(tr("Run check"))
        self.cancel_button.setText(tr("Cancel"))
        self.tree.setToolTip(tr("Double-click a row to select its objects in Maya."))
        if self.rendered is None and not self.reports:
            self.status.setText(tr("Run Mesh Check for the active chapter."))
        self.rendered = None
        self.refresh()
        self.update_actions()

    def show_actions(self, position):
        item = self.tree.itemAt(position)
        if item is None or self.running:
            return
        pair, roots = self.context()
        report = self.reports.get(pair["id"]) if pair else None
        if not report or report["roots"] != roots or self.rendered_chapter != pair["id"]:
            return
        heading = item.parent() or item
        stage = heading.data(0, QtCore.Qt.UserRole + 1)
        if not item.isSelected():
            self.tree.clearSelection()
            self.tree.setCurrentItem(item)
        selected_paths = {}
        for row in self.tree.selectedItems():
            data = row.data(0, QtCore.Qt.UserRole)
            if data:
                name, paths, _ = data
                selected_paths.setdefault(name, set()).update(paths)
        section = self.exclusions(pair, stage)
        menu = QtWidgets.QMenu(self)
        mark = menu.addAction(tr("Add to ZBrush layer…")) if (
            selected_paths.keys() == {"zbrush"} and self.current(report, pair, roots)) else None
        meshes = menu.addAction(tr("Skip selected meshes")) if selected_paths else None
        check = menu.addAction(tr("Restore check") if section.get("skip") else tr("Skip check"))
        restore = menu.addAction(tr("Restore skipped meshes")) if section.get("paths") else None
        action = menu.exec_(self.tree.viewport().mapToGlobal(position))
        active_pair, active_roots = self.context()
        if action is None or active_pair is not pair or active_roots != roots:
            return
        if action == mark:
            self.fix()
            return
        exclusions = pair.setdefault("mesh_check_exclusions", {})
        if action == meshes:
            for name, paths in selected_paths.items():
                entry = exclusions.setdefault(name, {})
                entry["paths"] = sorted(set(entry.get("paths", ())) | paths)
        elif action == check:
            exclusions.setdefault(stage, {})["skip"] = not section.get("skip", False)
        elif action == restore:
            exclusions[stage].pop("paths", None)
        bg_core.BakeSessionModel.save(self.owner.root_pairs)
        self.rendered = None
        self.refresh()

    @staticmethod
    def exclusions(pair, stage):
        return pair.get("mesh_check_exclusions", {}).get(stage, {})

    def visible_rows(self, report, pair, stage):
        section = self.exclusions(pair, stage)
        rows = report["sections"].get(stage)
        if section.get("skip"):
            return None
        excluded = set(section.get("paths", ()))
        if not excluded or rows is None:
            return rows
        remaining = []
        for label, paths, payload in rows:
            kept = [path for path in paths if path not in excluded]
            if not kept or (stage == "duplicates" and len(kept) < 2):
                continue
            if stage == "duplicates" and len(kept) != len(paths):
                label = "{} · {}".format(payload, ", ".join(path.rsplit("|", 1)[-1] for path in kept))
            remaining.append((label, kept, payload))
        return remaining

    def visible_errors(self, report, pair):
        return {stage: error for stage, error in report["errors"].items()
                if not self.exclusions(pair, stage).get("skip")}

    def save_report(self, pair, report):
        if report["cancelled"] or not any(item is pair for item in self.owner.root_pairs):
            return
        pair["mesh_check_report"] = {
            key: report[key] for key in ("roots", "sections", "counts", "errors", "name")
        }
        bg_core.BakeSessionModel.save(self.owner.root_pairs)

    def context(self):
        owner = self.owner
        pair = next((item for item in owner.root_pairs if item["id"] == owner.active_root_id), None)
        if not pair:
            return None, ()
        chapter_id = pair["id"]
        if self.report_pairs.get(chapter_id) is not pair:
            self.report_pairs[chapter_id] = pair
            self.reports.pop(chapter_id, None)
            self.expanded.pop(chapter_id, None)
            saved = pair.get("mesh_check_report")
            if saved:
                self.reports[chapter_id] = dict(
                    saved, roots=tuple(tuple(root) for root in saved["roots"]),
                    settings=None, revision=-1, cancelled=False)
        hp, lp, _ = owner.core.resolve_main_nodes(pair)
        return pair, tuple((role, root) for role, root in (("HP", hp), ("LP", lp))
                           if root and cmds.objExists(root))

    def set_threshold(self, value):
        self.owner.zbrush_triangle_threshold = value
        self.refresh()

    def cancel(self):
        self.cancelled = True

    def current(self, report, pair, roots):
        return bool(report and report["roots"] == roots and
                    report["settings"] == self.owner._hp_preflight_settings(pair) and
                    report["revision"] == self.owner._get_hp_preflight_cache().generation)

    def refresh(self, show_running=False):
        if self.running and not show_running:
            return
        pair, roots = self.context()
        report = self.reports.get(pair["id"]) if pair else None
        current = self.current(report, pair, roots)
        has_issues = None
        if current and not self.running and not report["cancelled"]:
            has_issues = bool(self.visible_errors(report, pair) or any(
                self.visible_rows(report, pair, stage) for stage in report["sections"]
            ))
        self.owner.mesh_check_tabs.set_check_status(self, has_issues)
        if not self.isVisible():
            return
        key = (id(report), pair["id"] if pair else None, roots, current,
               len(report["sections"]) if report else 0,
               len(report["errors"]) if report else 0, self.running)
        if key == self.rendered:
            return
        selected_keys = set()
        if self.rendered_chapter == (pair["id"] if pair else None):
            for item in self.tree.selectedItems():
                data = item.data(0, QtCore.Qt.UserRole)
                selected_keys.add((data[0], tuple(data[1])) if data else
                                  (item.data(0, QtCore.Qt.UserRole + 1), None))
        self.rendered = key
        if self.rendered_chapter is not None and self.tree.topLevelItemCount():
            self.expanded[self.rendered_chapter] = {
                i for i in range(self.tree.topLevelItemCount())
                if self.tree.topLevelItem(i).isExpanded()
            }
        self.rendered_chapter = pair["id"] if pair else None
        self.tree.clear()
        if not report:
            self.status.setText(tr("Run Mesh Check for the active chapter.") if pair else tr("Select a chapter."))
        else:
            sections = {stage: self.visible_rows(report, pair, stage)
                        for stage in report["sections"]}
            errors = self.visible_errors(report, pair)
            warnings = sum(len(sections.get(name) or ()) for name in ("zbrush", "duplicates", "shells", "empty"))
            blockers = len(sections.get("transforms") or ()) + len(errors)
            summary = (tr("{} blocking · {} warnings").format(blockers, warnings)
                       if blockers or warnings else tr("No issues found"))
            if report["cancelled"]:
                summary = tr("Check cancelled")
            elif not current:
                summary += " · " + tr("Needs recheck")
            scope = " / ".join("{} {}".format(role, count) for role, count in report["counts"].items())
            self.status.setText("{}\n{}{}".format(summary, report["name"], " · " + scope if scope else ""))
            for stage, title in (("transforms", "Transforms"), ("zbrush", "ZBrush candidates"),
                                 ("duplicates", "Duplicates"), ("shells", "Disconnected geometry"),
                                 ("empty", "Empty transforms")):
                if self.running and stage not in sections and stage not in report["errors"]:
                    continue
                rows = sections.get(stage)
                exclusion = self.exclusions(pair, stage)
                skipped = exclusion.get("skip")
                outcome = ("Skipped" if skipped else "Error" if stage in errors
                           else "Not checked" if rows is None else str(len(rows)) if rows else "Clear")
                title = tr(title) + "  ·  " + tr(outcome)
                if not skipped and exclusion.get("paths"):
                    title += " · " + tr("Meshes skipped")
                item = QtWidgets.QTreeWidgetItem(self.tree, [title])
                item.setData(0, QtCore.Qt.UserRole + 1, stage)
                item.setSelected((stage, None) in selected_keys)
                if skipped or stage in errors or rows is not None:
                    color = ("#484848" if skipped else "#73591e" if rows or stage in errors
                             else "#28553a")
                    item.setBackground(0, QtGui.QBrush(QtGui.QColor(color)))
                if stage == "shells":
                    item.setToolTip(0, tr("Counts disconnected polygon parts without using UVs."))
                for label, paths, payload in rows or ():
                    row = QtWidgets.QTreeWidgetItem(item, [tr(label[0]).format(*(tr(arg) if arg == "Freeze required" else arg for arg in label[1:])) if isinstance(label, (tuple, list)) else label])
                    row.setData(0, QtCore.Qt.UserRole, (stage, paths, payload))
                    row.setSelected((stage, tuple(paths)) in selected_keys)
                    detail = "\n".join(paths)
                    if stage == "zbrush":
                        detail = "\n".join(tr("{} · {}/{} triangles").format(*shape)
                                           for shape in payload)
                    elif stage == "shells" and "shapes" in payload:
                        detail = "\n".join(payload["shapes"])
                    elif stage == "shells":
                        detail += "\n" + tr("Spread {:.2f} · Fill {:.2f}").format(
                            payload.get("spread_ratio", 1), payload.get("fill_ratio", 1))
                    row.setToolTip(0, detail)
                if stage in errors:
                    QtWidgets.QTreeWidgetItem(item, [tr(errors[stage])])
                index = self.tree.indexOfTopLevelItem(item)
                if stage in errors or index in self.expanded.get(self.rendered_chapter, ()):
                    item.setExpanded(True)
        self.update_actions()

    def transform_rows(self, roots):
        paths = [root for _, root in roots]
        nodes = self.owner._freeze_transform_hierarchy_nodes(paths, paths)
        invalid = self.owner._unfrozen_transforms(nodes)
        reasons = dict(self.owner._freeze_transform_blockers(nodes, invalid)) if invalid else {}
        return [(("{} · {}", node.split("|")[-1], reasons.get(node, "Freeze required")), [node], None)
                for node in dict.fromkeys(list(invalid) + list(reasons))]

    def scan(self, pair, roots):
        owner = self.owner
        cache = owner._get_hp_preflight_cache()
        paths = [root for _, root in roots]
        watched = cache.watch(paths)
        report = dict(roots=roots, settings=owner._hp_preflight_settings(pair), revision=cache.generation,
                      sections={}, counts={}, scans={}, errors={}, cancelled=False,
                      name=str(pair.get("base") or pair["id"]))
        meshes = {}
        stages = report["sections"]
        self.reports[pair["id"]] = report
        self.refresh(show_running=True)

        last_update = 0.0

        def progress(label, done, total):
            nonlocal last_update
            now = monotonic()
            if last_update == 0 or now - last_update >= 0.04:
                last_update = now
                self.status.setText(label)
                self.progress.setRange(0, total)
                self.progress.setValue(done)
                QtWidgets.QApplication.processEvents()
            return (self.cancelled or owner.active_root_id != pair["id"] or
                    cache.generation != report["revision"] or
                    not any(item is pair for item in owner.root_pairs))

        def transforms():
            return self.transform_rows(roots)

        def zbrush():
            rows = []
            for index, node in enumerate(meshes.get("HP", [])):
                if progress(tr("Reading HP topology…"), index, len(meshes["HP"])):
                    return None
                if is_in_zbrush_layer(node):
                    continue
                triangles, faces, details = triangle_counts(node)
                if faces and triangles * 100 >= self.threshold.value() * faces:
                    ratio = 100 * triangles / faces
                    rows.append(("HP · {}  ·  {:.1f}%".format(node.split("|")[-1], ratio), [node], details))
            return rows

        def duplicates():
            rows = []
            for role, root in roots:
                result = bg_duplicates.scan(root, tolerance=getattr(owner, "duplicate_mesh_tolerance", 0.001),
                                            progress_callback=lambda done, total: not progress(
                                                tr("Checking {} duplicates…").format(role), done, total))
                report["scans"][role] = result
                if result["status"] == "cancelled":
                    return None
                if result["status"] == "failed":
                    raise RuntimeError("; ".join(result["errors"]))
                for group in result["groups"]:
                    rows.append(("{} · {}".format(role, ", ".join(p.split("|")[-1] for p in group)),
                                 group, role))
            return rows

        def shells():
            rows = []
            for role, nodes in meshes.items():
                done = 0
                single_shape = []
                for node in nodes:
                    if progress(tr("Checking {} shells…").format(role), done, len(nodes)):
                        return None
                    shapes = cmds.listRelatives(node, shapes=True, noIntermediate=True,
                                                type="mesh", fullPath=True) or []
                    if len(shapes) < 2:
                        single_shape.append(node)
                        continue
                    done += 1
                    per_shape = [(shape, owner._combined_check_mesh_shells(node, shape))
                                 for shape in shapes]
                    results = [(shape, info) for shape, info in per_shape if info]
                    count = sum(info["meaningful_shell_count"] for _, info in results)
                    if count > 1:
                        rows.append((("{} · {} · {} shapes / {} parts",
                            role, node.split("|")[-1], len(shapes), count), [node],
                            {"shapes": [shape for shape, _ in results]}))

                def tick(_):
                    nonlocal done
                    done += 1
                    return progress(tr("Checking {} shells…").format(role), done, len(nodes))

                candidates, _ = owner._find_combined_mesh_candidates_under_root(
                    dict(roots)[role], single_shape, tick)
                if candidates is None:
                    return None
                for node, info in candidates:
                    rows.append((("{} · {} · {} parts",
                        role, node.split("|")[-1], info["meaningful_shell_count"]), [node], info))
            return rows

        def empty():
            rows = []
            for role, root in roots:
                for node in cmds.listRelatives(root, allDescendents=True, fullPath=True,
                                               type="transform") or []:
                    if self.empty_safe(node):
                        rows.append(("{} · {}".format(role, node.split("|")[-1]), [node], None))
            return rows

        if "HP" not in dict(roots):
            report["errors"]["transforms"] = "HP root is missing."
            return report
        if not watched:
            report["errors"]["transforms"] = "Could not track changes to this chapter."
            return report
        try:
            for role, root in roots:
                meshes[role] = owner._mesh_transforms_under_root(root)
                report["counts"][role] = len(meshes[role])
        except Exception as exc:
            report["errors"]["transforms"] = str(exc)
            return report
        if not meshes["HP"]:
            report["errors"]["transforms"] = "No mesh shapes found under the HP root."
        for stage, title, check in (("transforms", "Transforms", transforms),
                                    ("zbrush", "ZBrush candidates", zbrush),
                                    ("duplicates", "Duplicates", duplicates),
                                    ("shells", "Disconnected geometry", shells),
                                    ("empty", "Empty transforms", empty)):
            if progress(tr("Checking {}…").format(tr(title)), 0, 0):
                report["cancelled"] = True
                break
            try:
                rows = check()
                if rows is None:
                    report["cancelled"] = True
                    break
                stages[stage] = rows
            except Exception as exc:
                report["errors"][stage] = str(exc)
            self.refresh(show_running=True)
            QtWidgets.QApplication.processEvents()
        return report

    def run(self):
        if self.running:
            return None
        self.owner.mesh_check_tabs.setCurrentWidget(self)
        pair, roots = self.context()
        if not pair:
            self.refresh()
            return None
        self.running = True
        self.cancelled = False
        self.run_button.hide()
        self.threshold.setEnabled(False)
        self.threshold_slider.setEnabled(False)
        self.fix_button.setEnabled(False)
        self.cancel_button.show()
        self.progress.show()
        try:
            report = self.scan(pair, roots)
            self.save_report(pair, report)
            return report
        finally:
            self.running = False
            self.run_button.show()
            self.threshold.setEnabled(True)
            self.threshold_slider.setEnabled(True)
            self.cancel_button.hide()
            self.progress.hide()
            self.rendered = None
            self.refresh()

    @staticmethod
    def item_rows(item):
        items = [item] if item.parent() else [item.child(i) for i in range(item.childCount())]
        return [data for row in items if (data := row.data(0, QtCore.Qt.UserRole))]

    def selected(self):
        return [row for item in self.tree.selectedItems() for row in self.item_rows(item)]

    def update_actions(self):
        rows = self.selected()
        pair, roots = self.context()
        report = self.reports.get(pair["id"]) if pair else None
        current = (not self.running and report and report["roots"] == roots and
                   self.rendered_chapter == (pair["id"] if pair else None))
        stages = {row[0] for row in rows}
        self.fix_button.setEnabled(bool(current and len(stages) == 1))
        labels = {"transforms": tr("Freeze…"), "zbrush": tr("Add to ZBrush layer…"),
                  "duplicates": tr("Review duplicates…"), "shells": tr("Separate…"), "empty": tr("Remove…")}
        self.fix_button.setText(labels.get(next(iter(stages), None), tr("Fix…")))

    def select_item(self, item, _column):
        pair, roots = self.context()
        report = self.reports.get(pair["id"]) if pair else None
        if self.running or self.rendered_chapter != (pair["id"] if pair else None):
            return
        # Selecting existing objects is safe even when geometry checks are stale.
        # Keep correction actions gated on current(), but allow repeated inspection.
        if not report or report["roots"] != roots:
            return
        paths = list(dict.fromkeys(path for _, nodes, _ in self.item_rows(item)
                                  for path in nodes if cmds.objExists(path)))
        if paths:
            cmds.select(paths, replace=True)

    @staticmethod
    def empty_safe(node):
        return bool(cmds.objExists(node) and cmds.nodeType(node) == "transform" and
                    not cmds.listRelatives(node, children=True) and
                    not cmds.referenceQuery(node, isNodeReferenced=True) and
                    not any(cmds.lockNode(node, query=True, lock=True) or []) and
                    not cmds.listAttr(node, userDefined=True) and not cmds.listConnections(node))

    def fix(self):
        pair, roots = self.context()
        report = self.reports.get(pair["id"]) if pair else None
        rows = self.selected()
        if self.running or not rows:
            return
        if (not report or report["roots"] != roots or
                self.rendered_chapter != (pair["id"] if pair else None)):
            self.refresh()
            return
        stage = rows[0][0]
        if any(row[0] != stage for row in rows):
            return
        if not self.current(report, pair, roots):
            requested = {path for _, nodes, _ in rows for path in nodes}
            report = self.run()
            active_pair, active_roots = self.context()
            if (not active_pair or active_pair["id"] != pair["id"] or
                    not self.current(report, active_pair, active_roots) or report["cancelled"]):
                return
            rows = [(stage, [path for path in nodes if path in requested], payload)
                    for _, nodes, payload in (self.visible_rows(report, pair, stage) or ())
                    if any(path in requested for path in nodes)]
        paths = list(dict.fromkeys(path for _, nodes, _ in rows for path in nodes))
        if not paths:
            return
        if not all(cmds.objExists(path) for path in paths):
            self.run()
            return
        owner = self.owner
        if stage == "duplicates":
            bg_duplicate_ui.confirm(owner, dict(roots)["HP"], dict(roots).get("LP"),
                                    excluded_paths=MeshCheckPanel.exclusions(pair, stage).get("paths", ()))
        elif stage == "transforms":
            root_nodes = [root for _, root in roots]
            owner.validate_frozen_transforms(
                root_nodes, root_nodes, "Mesh Check", confirm=False,
                excluded_paths=MeshCheckPanel.exclusions(pair, stage).get("paths", ()))
        elif stage == "zbrush":
            with bg_core.undo_chunk("Mark ZBrush"):
                layer = owner._choose_zbrush_display_layer(owner._find_zbrush_display_layers())
                if layer:
                    cmds.editDisplayLayerMembers(layer, paths, noRecurse=True)
        else:
            if not self.current(report, pair, roots):
                return
            with bg_core.undo_chunk("Mesh Check correction"):
                if stage == "empty":
                    safe = [path for path in paths if self.empty_safe(path)]
                    if safe:
                        cmds.delete(safe)
                else:
                    safe = [path for path in paths if not bg_duplicate_ui._protected(owner, path)
                            and not bg_duplicate_ui._manual_conflict(owner, path)]
                    if safe:
                        owner._separate_mesh_transforms(safe, select_result=True)
                    if len(safe) != len(paths):
                        owner.log(tr("Mesh Check: protected meshes were left in place."), "orange")
        if stage in ("transforms", "zbrush", "empty"):
            self.refresh_after_fix(report, pair, roots, stage)
        else:
            self.run()

    def refresh_after_fix(self, report, pair, roots, stage):
        """Keep geometry results after corrections that preserve mesh geometry."""
        active_pair, active_roots = self.context()
        if (not active_pair or active_pair["id"] != pair["id"] or active_roots != roots or
                self.owner._hp_preflight_settings(active_pair) != report["settings"]):
            self.refresh()
            return
        try:
            if stage == "transforms":
                report["sections"][stage] = self.transform_rows(roots)
            else:
                remaining = []
                for label, paths, payload in report["sections"].get(stage, []):
                    paths = [path for path in paths if cmds.objExists(path) and
                             (stage != "zbrush" or not is_in_zbrush_layer(path))]
                    if paths:
                        remaining.append((label, paths, payload))
                report["sections"][stage] = remaining
            # Removed empty nodes may also have appeared in transform findings.
            if stage == "empty":
                report["sections"]["transforms"] = self.transform_rows(roots)
            cache = self.owner._get_hp_preflight_cache()
            if cache.watch([root for _, root in roots]):
                report["revision"] = cache.generation
            else:
                report["revision"] = -1
            if "name" in report:
                self.save_report(pair, report)
            self.rendered = None
            self.refresh()
        except Exception:
            # A failed local update must not authorize analysis.
            report["revision"] = -1
            self.rendered = None
            self.refresh()

    def ready_for_analysis(self, report, pair, roots):
        # Analyze HP uses the last completed check for these roots.
        # Fix operations still require a live revision before changing geometry.
        return bool(report and report["roots"] == roots and not report["cancelled"] and
                    not self.visible_errors(report, pair) and
                    all(stage in report["sections"] or self.exclusions(pair, stage).get("skip")
                        for stage in ("transforms", "zbrush", "duplicates", "shells", "empty")))

    def allow_analysis(self):
        if self.running:
            return False
        pair, roots = self.context()
        report = self.reports.get(pair["id"]) if pair else None
        if not self.ready_for_analysis(report, pair, roots):
            report = self.run()
        active_pair, active_roots = self.context()
        if (active_pair is not pair or active_roots != roots or
                not self.ready_for_analysis(report, pair, roots) or
                self.visible_rows(report, pair, "transforms")):
            self.owner.mesh_check_tabs.setCurrentWidget(self)
            return False
        if any(self.visible_rows(report, pair, stage) for stage in ("zbrush", "duplicates", "shells")):
            self.owner.mesh_check_tabs.setCurrentWidget(self)
            answer = QtWidgets.QMessageBox.question(
                self, tr("Mesh Check"), tr("Continue Analyze HP once with the warnings shown?"))
            active_pair, active_roots = self.context()
            proceed = bool(answer == QtWidgets.QMessageBox.Yes and active_pair and
                           active_pair is pair and active_roots == roots and
                           self.ready_for_analysis(report, active_pair, active_roots))
            if proceed:
                self.owner.mesh_check_tabs.setCurrentWidget(self.owner.subgroups_scroll)
            return proceed
        return True
