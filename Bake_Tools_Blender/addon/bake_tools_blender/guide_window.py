# -*- coding: utf-8 -*-
"""Local illustrated Bake Groups guide hosted by Blender's Qt event pump."""
from __future__ import absolute_import, division, print_function

import base64
import io
import json
import math
import os
import re
import shutil
import uuid

from .guide_manual import build_document as _build_manual_document
from .guide_manual import guide_font_family as _guide_font_family
from .guide_manual import CURRENT_VERSION as _CURRENT_VERSION
from .guide_translations import CARDS as _TRANSLATED_CARDS, RELEASE as _TRANSLATED_RELEASE

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    QAction = QtGui.QAction
    QShortcut = QtGui.QShortcut
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets
    QAction = QtWidgets.QAction
    QShortcut = QtWidgets.QShortcut


TOPICS = (
    ("release.whats_new", "What's new", "Что нового",
     "Changes in the installed version of Bake Groups.",
     "Изменения в установленной версии Bake Groups."),
    ("overview", "Start here", "Начните здесь",
     "Choose a control in Bake Groups with the ? cursor. This board will jump to its explanation. Pan with the middle mouse button and zoom with the wheel.",
     "Выберите элемент Bake Groups курсором ?. Доска откроет его пояснение. Перемещайте полотно средней кнопкой мыши, масштабируйте колесом."),
    ("pick.hp", "Pick HP", "Выбрать HP",
     "Select the high-poly Object or Collection in Blender, then press Pick HP.",
     "Выберите HP Object или Collection в Blender и нажмите Pick HP."),
    ("pick.lp", "Pick LP", "Выбрать LP",
     "Select the low-poly Object or Collection in Blender, then press Pick LP.",
     "Выберите LP Object или Collection в Blender и нажмите Pick LP."),
    ("chapter.create", "Create chapter", "Создать главу",
     "After picking HP and LP roots, Create builds a chapter. Multiple LP materials can be split into separate chapters.",
     "После выбора HP и LP кнопка Create создаёт главу. Несколько материалов LP можно разделить на отдельные главы."),
    ("hp.check", "Check Before Analyze", "Проверка перед анализом",
     "Check scene geometry before analyzing HP. Review the reported duplicates and invalid transforms before continuing.",
     "Проверьте геометрию сцены до анализа HP. Просмотрите найденные дубликаты и некорректные трансформации."),
    ("hp.analyze", "Analyze HP", "Анализ HP",
     "Distributes HP meshes into subgroups. Run it after the scene is prepared; inspect the subgroup list before export.",
     "Распределяет HP-меши по сабгруппам. Запускайте после подготовки сцены и проверьте список сабгрупп до экспорта."),
    ("lp.assign", "Assign LP Meshes", "Назначить LP-меши",
     "Matches LP meshes to the HP subgroups for baking. Check the resulting groups in the Table of Contents.",
     "Сопоставляет LP-меши с сабгруппами HP для запекания. Проверьте результат в оглавлении."),
    ("hp.find_similar", "Find Sim", "Найти похожие",
     "Finds meshes similar to the selected mesh. Use it to inspect repeated fasteners and their subgroup settings.",
     "Находит меши, похожие на выделенный. Полезно для проверки повторяющегося крепежа и его настроек."),
    ("hp.zbrush", "Find ZBrush", "Найти ZBrush",
     "Marks high-poly meshes already detailed in ZBrush so the export path does not subdivide them again.",
     "Помечает детализированные в ZBrush HP-меши, чтобы экспорт не сглаживал их повторно."),
    ("hp.visibility", "HP visibility", "Видимость HP",
     "Shows or hides the high-poly geometry of the active chapter.",
     "Показывает или скрывает HP-геометрию активной главы."),
    ("lp.visibility", "LP visibility", "Видимость LP",
     "Shows or hides the low-poly geometry of the active chapter.",
     "Показывает или скрывает LP-геометрию активной главы."),
    ("export.settings", "Export Settings", "Настройки экспорта",
     "Opens export controls for the active chapter, including subgroup smoothing and target format.",
     "Открывает настройки экспорта активной главы, включая сглаживание сабгрупп и формат."),
    ("export.smooth", "Smooth View", "Предпросмотр сглаживания",
     "Preview the smoothing level before export. The exported HP can use a separate level for each subgroup.",
     "Проверьте степень сглаживания до экспорта. Для каждой сабгруппы HP можно задать собственный уровень."),
    ("export.run", "Export", "Экспорт",
     "Exports the active chapter with its current settings. Check the output path and target format first.",
     "Экспортирует активную главу с текущими настройками. Сначала проверьте путь и формат."),
    ("toc", "Table of Contents", "Оглавление",
     "Select a book, chapter or subgroup here to inspect it. The eye icons control scene visibility.",
     "Здесь можно выбрать книгу, главу или сабгруппу. Значки глаза управляют видимостью в сцене."),
)


def _language():
    try:
        import bpy
        from .localization import canonical_language
        language = canonical_language(bpy.context.scene.bake_tools_settings.language).lower()
        if language.startswith("ru"):
            return "ru"
        if language.startswith("ja"):
            return "ja"
        if language.startswith("zh"):
            return "zh-CN"
        return "en"
    except Exception:
        return "en"


def _document_path(language):
    root = QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.AppDataLocation)
    if not root:
        root = os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    return os.path.join(root, "BakeGroupsBlender", "guide_{}.json".format(language))


def _guide_settings():
    return QtCore.QSettings("BakeGroups", "BlenderBakeGuide")


WHATS_NEW_CARD_REVISION = 5


def _whats_new_marker(version):
    # Refresh the one-time card when its content changes without a plugin bump.
    return "{}:card{}".format(version, WHATS_NEW_CARD_REVISION)


def whats_new_due(version, settings=None):
    settings = settings if settings is not None else _guide_settings()
    return str(settings.value("whats_new_seen_version", "")) != _whats_new_marker(version)


def mark_whats_new_seen(version, settings=None):
    settings = settings if settings is not None else _guide_settings()
    settings.setValue("whats_new_seen_version", _whats_new_marker(version))
    settings.sync()


def _default_document(language):
    if _build_manual_document is not None:
        return _build_manual_document(language)
    russian = language == "ru"
    entries = []
    anchors = {}
    for index, (key, en_title, ru_title, en_body, ru_body) in enumerate(TOPICS):
        x = 80 + (index % 3) * 440
        y = 80 + (index // 3) * 310
        title = ru_title if russian else en_title
        body = ru_body if russian else en_body
        entries.append({"type": "rect", "x": x, "y": y, "w": 390, "h": 246,
                        "color": "#536f88", "fill": "#242d39", "anchor": key})
        entries.append({"type": "text", "x": x + 20, "y": y + 16,
                        "w": 350, "text": title, "size": 20,
                        "color": "#f0f4ff", "anchor": key})
        entries.append({"type": "text", "x": x + 20, "y": y + 69,
                        "w": 350, "text": body, "size": 13,
                        "color": "#d1dbe5", "anchor": key})
        if index % 3 < 2 and index < len(TOPICS) - 1:
            entries.append({"type": "arrow", "x": x + 399, "y": y + 122,
                            "w": 30, "h": 0, "color": "#39b86a", "thickness": 3})
        anchors[key] = [x, y, 390, 246]
    return {"schema": 1, "language": language, "items": entries,
            "anchors": anchors}


def _upgrade_document(saved, language):
    """Upgrade the manual while retaining edits on an already-current board."""
    bundled = _default_document(language)
    version = bundled.get("manual_version", 0)
    if not version:
        return saved, False
    if saved.get("manual_version", 0) < version:
        return bundled, True

    # The release banner changes without a manual schema bump. Update only
    # its shipped heading so a user's saved Guide layout and notes survive.
    heading = next((entry.get("text") for entry in bundled.get("items", [])
                    if entry.get("type") == "text" and
                    entry.get("anchor") == "release.whats_new" and
                    re.match(r"^(?:New in version|Что нового в версии) \d+\.\d+\.\d+$|^バージョン \d+\.\d+\.\d+ の新機能$|^版本 \d+\.\d+\.\d+ 的新功能$",
                             entry.get("text", ""))), None)
    if heading is None:
        return saved, False
    for index, entry in enumerate(saved.get("items", [])):
        if (entry.get("type") == "text" and
                entry.get("anchor") == "release.whats_new" and
                re.match(r"^(?:New in version|Что нового в версии) \d+\.\d+\.\d+$|^バージョン \d+\.\d+\.\d+ の新機能$|^版本 \d+\.\d+\.\d+ 的新功能$",
                         entry.get("text", ""))):
            if entry["text"] == heading:
                return saved, False
            updated = dict(saved)
            updated["items"] = list(saved["items"])
            updated["items"][index] = dict(entry, text=heading)
            return updated, True
    return saved, False


class GuideStroke(QtWidgets.QGraphicsPathItem):
    def __init__(self, kind, width, height, color="#39b86a", thickness=3):
        super(GuideStroke, self).__init__()
        self.kind = kind
        self.stroke_width = float(width)
        self.stroke_height = float(height)
        self.thickness = float(thickness)
        self.color = str(color)
        self.rebuild()

    def rebuild(self):
        w, h = self.stroke_width, self.stroke_height
        path = QtGui.QPainterPath(QtCore.QPointF(0, 0))
        if self.kind == "wavy":
            length = max(1.0, math.hypot(w, h))
            normal_x, normal_y = -h / length, w / length
            for index in range(1, max(12, int(length / 5)) + 1):
                t = index / float(max(12, int(length / 5)))
                offset = math.sin(t * length / 18.0 * math.pi * 2) * 7
                path.lineTo(w * t + normal_x * offset,
                            h * t + normal_y * offset)
        else:
            path.lineTo(w, h)
        if self.kind == "arrow" and abs(w) + abs(h) > 1:
            angle = math.atan2(h, w)
            for sign in (-1, 1):
                a = angle + math.pi + sign * 0.48
                path.moveTo(w, h)
                path.lineTo(w + math.cos(a) * 20,
                            h + math.sin(a) * 20)
        self.setPath(path)
        pen = QtGui.QPen(QtGui.QColor(self.color), self.thickness)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)
        if self.kind == "dashed":
            pen.setStyle(QtCore.Qt.DashLine)
        self.setPen(pen)


class GuideView(QtWidgets.QGraphicsView):
    def __init__(self, scene, owner):
        super(GuideView, self).__init__(scene)
        self.owner = owner
        self.setRenderHint(QtGui.QPainter.Antialiasing, True)
        self.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        self.setViewportUpdateMode(QtWidgets.QGraphicsView.BoundingRectViewportUpdate)
        self.setAcceptDrops(True)
        self.setBackgroundBrush(QtGui.QColor("#171b22"))
        # The bars are still used internally for panning; only their chrome is hidden.
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._panning = False
        self._last_pan = None

    def wheelEvent(self, event):
        factor = 1.16 if event.angleDelta().y() > 0 else 1 / 1.16
        current = self.transform().m11()
        if 0.12 <= current * factor <= 5.0:
            # AnchorUnderMouse uses Qt's last mouse-move position, which can
            # still belong to the manager after Help opened this window.
            # Use the actual wheel position so the clicked card stays put.
            position = event.position().toPoint()
            before = self.mapToScene(position)
            self.setTransformationAnchor(QtWidgets.QGraphicsView.NoAnchor)
            self.scale(factor, factor)
            after = self.mapToScene(position)
            self.centerOn(self.mapToScene(self.viewport().rect().center()) + before - after)
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MiddleButton:
            self._panning = True
            self._last_pan = event.pos()
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            event.accept()
            return
        super(GuideView, self).mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning:
            delta = event.pos() - self._last_pan
            self._last_pan = event.pos()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        super(GuideView, self).mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.MiddleButton and self._panning:
            self._panning = False
            self.unsetCursor()
            event.accept()
            return
        super(GuideView, self).mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if not self.owner.edit_mode:
            item = self.itemAt(event.pos())
            if item is not None and item.data(0) == "image":
                self.fitInView(item.sceneBoundingRect().adjusted(-20, -20, 20, 20),
                               QtCore.Qt.KeepAspectRatio)
                event.accept()
                return
        super(GuideView, self).mouseDoubleClickEvent(event)

    def dragEnterEvent(self, event):
        if self.owner.edit_mode and event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super(GuideView, self).dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self.owner.edit_mode and event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super(GuideView, self).dragMoveEvent(event)

    def dropEvent(self, event):
        if not self.owner.edit_mode:
            return super(GuideView, self).dropEvent(event)
        point = self.mapToScene(event.pos())
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path and self.owner.add_image_file(path, point):
                event.acceptProposedAction()
                return
        super(GuideView, self).dropEvent(event)


class GuideWindow(QtWidgets.QMainWindow):
    """An owned, decorated Blender tool window with a normal close button."""
    def __init__(self, parent=None, initial_topic="overview"):
        super(GuideWindow, self).__init__(parent)
        self.setObjectName("BakeGroupsGuide")
        self.setWindowTitle("Bake Guide")
        # Qt ownership keeps the guide above its host without a global topmost flag.
        self.setWindowFlag(QtCore.Qt.Tool, parent is not None)
        self.setWindowModality(QtCore.Qt.NonModal)
        self.setWindowFlag(QtCore.Qt.WindowCloseButtonHint, True)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        self.resize(1100, 720)
        self._release_size_active = False
        self._normal_size = QtCore.QSize(self.size())
        self.edit_mode = False
        self._loading = False
        self._flash = None
        self._dialogs = []
        self.language = _language()
        self.path = _document_path(self.language)
        self._migration_pending = False
        self.document_data = self._read_document()
        self.history = []
        self.history_index = -1

        self.scene = QtWidgets.QGraphicsScene(self)
        self.scene.setSceneRect(-50000, -50000, 100000, 100000)
        self.view = GuideView(self.scene, self)
        self.setCentralWidget(self.view)
        self.setStyleSheet("QMainWindow, QToolBar { background: #242831; color: #e8edf4; }"
                           "QToolButton, QLineEdit, QComboBox, QDoubleSpinBox {"
                           " background: #333a46; color: #f2f4f7; border: 1px solid #536071; padding: 4px; }")
        bar = self.addToolBar("Guide")
        bar.setMovable(False)
        self.topic_box = QtWidgets.QComboBox()
        self.topic_box.activated.connect(self._choose_topic)
        bar.addWidget(self.topic_box)
        self.search_box = QtWidgets.QLineEdit()
        self.search_box.setPlaceholderText("Search / Поиск")
        self.search_box.setMaximumWidth(200)
        self.search_box.returnPressed.connect(self._search)
        bar.addWidget(self.search_box)
        fit_action = QAction("Fit all", self)
        fit_action.triggered.connect(self.fit_all)
        bar.addAction(fit_action)
        self.pin_action = QAction("Stay on top", self)
        self.pin_action.setCheckable(True)
        self.pin_action.toggled.connect(self.set_stay_on_top)
        bar.addAction(self.pin_action)
        old_manual = os.path.join(os.path.dirname(__file__), "Manual", "Manual.pur")
        if os.path.isfile(old_manual):
            legacy_action = QAction("Old manual / Старый мануал", self)
            legacy_action.triggered.connect(lambda checked=False, p=old_manual:
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(os.path.dirname(p))))
            bar.addAction(legacy_action)
        self.edit_action = QAction("Edit / Правка", self)
        self.edit_action.setCheckable(True)
        self.edit_action.toggled.connect(self.set_edit_mode)
        bar.addAction(self.edit_action)
        self.edit_bar = self.addToolBar("Drawing")
        self.edit_bar.setMovable(False)
        self._edit_actions = []
        for label, kind in (("Text", "text"), ("Image", "image"),
                            ("Line", "line"), ("Arrow", "arrow"),
                            ("Dashed", "dashed"), ("Wavy", "wavy"),
                            ("Box", "rect"), ("Ellipse", "ellipse")):
            action = QAction(label, self)
            action.triggered.connect(lambda checked=False, k=kind: self.add_kind(k))
            self.edit_bar.addAction(action)
            self._edit_actions.append(action)
        self.edit_bar.addSeparator()
        self.color_action = QAction("Color", self)
        self.color_action.triggered.connect(self.change_color)
        self.edit_bar.addAction(self.color_action)
        self.fill_action = QAction("Fill", self)
        self.fill_action.triggered.connect(self.change_fill)
        self.edit_bar.addAction(self.fill_action)
        self.bind_action = QAction("Link topic", self)
        self.bind_action.triggered.connect(self.bind_selected)
        self.edit_bar.addAction(self.bind_action)
        for label, degrees in (("↶ 15°", -15), ("↷ 15°", 15)):
            action = QAction(label, self)
            action.triggered.connect(lambda checked=False, amount=degrees: self.rotate_selected(amount))
            self.edit_bar.addAction(action)
        self.delete_action = QAction("Delete", self)
        self.delete_action.triggered.connect(self.delete_selected)
        self.edit_bar.addAction(self.delete_action)
        self.edit_bar.addSeparator()
        self.x_spin = self._spin("X", -100000, 100000)
        self.y_spin = self._spin("Y", -100000, 100000)
        self.w_spin = self._spin("W", 1, 100000)
        self.h_spin = self._spin("H", 1, 100000)
        self.size_spin = self._spin("Size", 1, 100)
        self.scene.selectionChanged.connect(self._sync_inspector)
        self.scene.changed.connect(self._schedule_save)
        self.edit_bar.setVisible(False)
        bar.setVisible(False)
        self.save_timer = QtCore.QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self._commit)
        for key, callback in (("Ctrl+V", self.paste),
                              ("Ctrl+Z", self.undo), ("Ctrl+Y", self.redo),
                              ("Ctrl+Shift+Z", self.redo),
                              ("Ctrl+S", self._commit)):
            shortcut = QShortcut(QtGui.QKeySequence(key), self)
            shortcut.activated.connect(callback)
        self._load_items(self.document_data.get("items", []))
        self._populate_topics()
        self._update_chrome_language()
        self.history = [self._serialize()]
        self.history_index = 0
        self.go_to(initial_topic)

    def _populate_topics(self):
        self.topic_box.blockSignals(True)
        self.topic_box.clear()
        for key, en, ru, _, _ in TOPICS:
            if key == "release.whats_new" and self.language in _TRANSLATED_RELEASE:
                title = _TRANSLATED_RELEASE[self.language]["title"].format(_CURRENT_VERSION)
            elif self.language in _TRANSLATED_CARDS:
                title = _TRANSLATED_CARDS[self.language].get(key, (en,))[0]
            else:
                title = ru if self.language == "ru" else en
            self.topic_box.addItem(title, key)
        known = {key for key, _, _, _, _ in TOPICS}
        custom_titles = {}
        for entry in self.document_data.get("items", []):
            if entry.get("type") == "text" and entry.get("anchor"):
                custom_titles.setdefault(entry["anchor"], entry.get("text"))
        for entry in self.document_data.get("items", []):
            key = entry.get("anchor")
            if key and key not in known:
                self.topic_box.addItem(custom_titles.get(key) or key, key)
                known.add(key)
        self.topic_box.blockSignals(False)

    def _update_chrome_language(self):
        self.search_box.setPlaceholderText({
            "ru": "Поиск", "ja": "検索", "zh-CN": "搜索"}.get(self.language, "Search"))
        self.setWindowTitle({
            "ru": "Bake Guide — Руководство",
            "ja": "Bake Guide — ガイド",
            "zh-CN": "Bake Guide — 指南"}.get(self.language, "Bake Guide"))

    def switch_language(self, language):
        """Keep the Guide open while loading the board for another language."""
        language = ("zh-CN" if str(language).lower().startswith("zh") else
                    "ja" if str(language).lower().startswith("ja") else
                    "ru" if str(language).lower().startswith("ru") else "en")
        if language == self.language:
            return
        topic = self.topic_box.currentData() or "overview"
        self._commit()
        self.language = language
        self.path = _document_path(language)
        self._migration_pending = False
        self.document_data = self._read_document()
        self._load_items(self.document_data.get("items", []))
        self._populate_topics()
        self.search_box.clear()
        self._update_chrome_language()
        self.history = [self._serialize()]
        self.history_index = 0
        self.go_to(topic)

    def _spin(self, title, minimum, maximum):
        self.edit_bar.addWidget(QtWidgets.QLabel(title))
        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(0)
        spin.setMaximumWidth(75)
        spin.valueChanged.connect(self._apply_inspector)
        self.edit_bar.addWidget(spin)
        return spin

    def _read_document(self):
        for candidate in (self.path, self.path + ".bak"):
            try:
                if os.path.getsize(candidate) > 100 * 1024 * 1024:
                    raise ValueError("Guide file is too large")
                with io.open(candidate, "r", encoding="utf-8") as stream:
                    data = json.load(stream)
                if data.get("schema") == 1 and isinstance(data.get("items"), list):
                    document, migrated = _upgrade_document(data, self.language)
                    self._migration_pending = migrated
                    return document
            except (IOError, OSError, ValueError, TypeError, AttributeError):
                pass
        return _default_document(self.language)

    def _load_items(self, entries):
        self._loading = True
        self._flash = None
        self.scene.clear()
        for entry in entries:
            try:
                item = self._make_item(entry)
                if item is not None:
                    self.scene.addItem(item)
                    item.setPos(float(entry.get("x", 0)), float(entry.get("y", 0)))
            except Exception:
                continue
        self._loading = False
        self.set_edit_mode(self.edit_mode)

    def _make_item(self, entry):
        kind = entry.get("type")
        w = float(entry.get("w", 240))
        h = float(entry.get("h", 120))
        color = QtGui.QColor(entry.get("color", "#dbe4ee"))
        if kind == "text":
            item = QtWidgets.QGraphicsTextItem()
            item.setPlainText(str(entry.get("text", "")))
            item.setTextWidth(w)
            font = QtGui.QFont(_guide_font_family(self.language), int(entry.get("size", 14)))
            item.setFont(font)
            item.setDefaultTextColor(color)
            item.document().contentsChanged.connect(self._schedule_save)
        elif kind == "image":
            asset = entry.get("asset", "")
            if asset:
                # Bundled manual images are read-only assets, never arbitrary paths.
                if not isinstance(asset, str) or os.path.basename(asset) != asset or not asset.endswith(".png"):
                    return None
                image = QtGui.QImage(os.path.join(os.path.dirname(__file__), "guide_assets", asset))
            else:
                raw = base64.b64decode(entry.get("png", ""))
                image = QtGui.QImage.fromData(raw, "PNG")
            if image.isNull() or image.width() * image.height() > 100000000:
                return None
            item = QtWidgets.QGraphicsPixmapItem(QtGui.QPixmap.fromImage(image))
            item.setTransformationMode(QtCore.Qt.SmoothTransformation)
            item.setScale(w / max(1, image.width()))
            item.setData(2, entry.get("png"))
            item.setData(3, asset)
        elif kind in ("line", "arrow", "dashed", "wavy"):
            item = GuideStroke(kind, w, h, color.name(), entry.get("thickness", 3))
        elif kind in ("rect", "ellipse"):
            cls = QtWidgets.QGraphicsRectItem if kind == "rect" else QtWidgets.QGraphicsEllipseItem
            item = cls(0, 0, w, h)
            item.setPen(QtGui.QPen(color, float(entry.get("thickness", 2))))
            item.setBrush(QtGui.QBrush(QtGui.QColor(entry.get("fill", "#242d39"))))
        else:
            return None
        item.setData(0, kind)
        item.setData(1, entry.get("anchor", ""))
        item.setZValue(float(entry.get("z", 0)))
        item.setRotation(float(entry.get("rotation", 0)))
        return item

    def _serialize(self):
        items = []
        for item in reversed(self.scene.items()):
            kind = item.data(0)
            if kind not in ("text", "image", "line", "arrow", "dashed", "wavy", "rect", "ellipse"):
                continue
            rect = item.boundingRect()
            entry = {"type": kind, "x": round(item.pos().x(), 2),
                     "y": round(item.pos().y(), 2), "z": item.zValue(),
                     "rotation": item.rotation(),
                     "w": round(rect.width(), 2), "h": round(rect.height(), 2),
                     "anchor": item.data(1) or ""}
            if kind == "text":
                entry.update(text=item.toPlainText(), w=item.textWidth(),
                             size=item.font().pointSize(),
                             color=item.defaultTextColor().name())
            elif kind == "image":
                source = {"asset": item.data(3)} if item.data(3) else {"png": item.data(2)}
                entry.update(source, w=round(item.pixmap().width() * item.scale(), 2),
                             h=round(item.pixmap().height() * item.scale(), 2))
            elif isinstance(item, GuideStroke):
                entry.update(w=item.stroke_width, h=item.stroke_height,
                             color=item.color, thickness=item.thickness)
            else:
                entry.update(w=round(item.rect().width(), 2),
                             h=round(item.rect().height(), 2),
                             color=item.pen().color().name(),
                             fill=item.brush().color().name(),
                             thickness=item.pen().widthF())
            items.append(entry)
        return {"schema": 1, "language": self.language, "items": items,
                "manual_version": self.document_data.get("manual_version", 0)}

    def _schedule_save(self, *args):
        if not self._loading and self.edit_mode:
            self.save_timer.start(1200)

    def _commit(self):
        if self._loading:
            return
        self.save_timer.stop()
        data = self._serialize()
        if (not self._migration_pending and self.history and
                data == self.history[self.history_index]):
            return
        folder = os.path.dirname(self.path)
        if not os.path.isdir(folder):
            os.makedirs(folder)
        tmp = self.path + ".tmp." + uuid.uuid4().hex
        try:
            with io.open(tmp, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            if os.path.isfile(self.path):
                if self._migration_pending:
                    archive = self.path + ".pre_manual_v5.json"
                    if not os.path.exists(archive):
                        shutil.copy2(self.path, archive)
                shutil.copy2(self.path, self.path + ".bak")
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        self.history = self.history[:self.history_index + 1] + [data]
        self.history_index += 1
        self._migration_pending = False

    def set_edit_mode(self, enabled):
        self.edit_mode = bool(enabled)
        self.edit_bar.setVisible(False)
        self.view.setDragMode(QtWidgets.QGraphicsView.RubberBandDrag if enabled
                              else QtWidgets.QGraphicsView.NoDrag)
        for item in self.scene.items():
            if item.data(0) not in ("text", "image", "line", "arrow", "dashed", "wavy", "rect", "ellipse"):
                continue
            item.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, enabled)
            item.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, enabled)
            if isinstance(item, QtWidgets.QGraphicsTextItem):
                item.setTextInteractionFlags(QtCore.Qt.TextEditorInteraction if enabled
                                             else QtCore.Qt.NoTextInteraction)
        if not enabled and self.history:
            self._commit()

    def go_to(self, key):
        matches = [item for item in self.scene.items()
                   if item.data(1) == key and item.data(0) == "rect"]
        if not matches:
            self._ensure_bundled_card(key)
            matches = [item for item in self.scene.items()
                       if item.data(1) == key and item.data(0) == "rect"]
        if not matches:
            matches = [item for item in self.scene.items() if item.data(1) == key]
        if not matches:
            key = "overview"
            matches = [item for item in self.scene.items()
                       if item.data(1) == key and item.data(0) == "rect"]
        if not matches:
            return
        self._resize_for_topic(key)
        rect = matches[0].sceneBoundingRect().adjusted(-25, -25, 25, 25)
        if rect.height() > rect.width() * 1.15:
            # Tall illustrated cards must remain readable. Start at the top;
            # the user can pan down instead of seeing a tiny full-page preview.
            self.view.resetTransform()
            scale = min(1.25, self.view.viewport().width() / max(1.0, rect.width()))
            self.view.scale(scale, scale)
            self.view.centerOn(rect.center().x(), rect.top() +
                               self.view.viewport().height() / (2.0 * scale))
        else:
            self.view.fitInView(rect, QtCore.Qt.KeepAspectRatio)
        index = self.topic_box.findData(key)
        if index >= 0:
            self.topic_box.setCurrentIndex(index)
        if self._flash is not None:
            self._clear_flash()
        self._flash = QtWidgets.QGraphicsRectItem(rect)
        self._flash.setPen(QtGui.QPen(QtGui.QColor("#53e88b"), 5))
        self._flash.setBrush(QtCore.Qt.NoBrush)
        self._flash.setAcceptedMouseButtons(QtCore.Qt.NoButton)
        self._flash.setZValue(10000)
        self.scene.addItem(self._flash)
        marker = self._flash
        QtCore.QTimer.singleShot(1800, lambda: self._clear_flash(marker))
        self.show()
        self.raise_()
        self.activateWindow()

    def _resize_for_topic(self, key):
        if key == "release.whats_new":
            if not self._release_size_active:
                self._normal_size = QtCore.QSize(self.size())
            host = self.parentWidget()
            screen = (host.screen() if host is not None else
                      QtWidgets.QApplication.primaryScreen())
            if screen is not None:
                available = screen.availableGeometry()
                width = min(1375, max(640, available.width() - 48))
                height = min(900, max(480, available.height() - 80))
            else:
                width, height = 1375, 900
            self.resize(width, height)
            self._release_size_active = True
            # Fit after the larger viewport has been laid out, including when
            # this is the first card shown during plugin startup.
            self.show()
        elif self._release_size_active:
            self.resize(self._normal_size)
            self._release_size_active = False

    def _ensure_bundled_card(self, key):
        """Restore a missing shipped topic at its proper position in the guide."""
        if not key or any(item.data(1) == key for item in self.scene.items()):
            return
        if not any(entry.get("anchor") == key and entry.get("type") == "rect"
                   for entry in _default_document(self.language).get("items", [])):
            return
        current = self._serialize()
        current["manual_version"] = 0
        rebuilt, _ = _upgrade_document(current, self.language)
        self.document_data = rebuilt
        self._load_items(rebuilt["items"])
        self._migration_pending = True
        if self.topic_box.findData(key) < 0:
            title = next((entry.get("text") for entry in rebuilt["items"]
                          if entry.get("anchor") == key and entry.get("type") == "text"), key)
            self.topic_box.addItem(title, key)

    def _clear_flash(self, marker=None):
        if self._flash is not None and (marker is None or marker is self._flash):
            try:
                self.scene.removeItem(self._flash)
            except RuntimeError:
                pass
            self._flash = None

    def ensure_context_card(self, key, title, description):
        """Compatibility shim: unknown controls no longer create guide topics."""
        return False

    def _choose_topic(self, index):
        self.go_to(self.topic_box.itemData(index))

    def fit_all(self):
        rect = self.scene.itemsBoundingRect().adjusted(-40, -40, 40, 40)
        if rect.isValid():
            self.view.fitInView(rect, QtCore.Qt.KeepAspectRatio)

    def set_stay_on_top(self, enabled):
        # Kept for old saved actions; global topmost would cover other apps.
        self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, False)
        self.setWindowFlag(QtCore.Qt.WindowCloseButtonHint, True)
        self.show()

    def _show_dialog(self, dialog):
        """Keep editing dialogs visible without disabling the Guide title bar."""
        dialog.setModal(False)
        dialog.setWindowModality(QtCore.Qt.NonModal)
        dialog.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        dialog.setWindowFlag(QtCore.Qt.WindowCloseButtonHint, True)
        self._dialogs.append(dialog)
        dialog.destroyed.connect(lambda *args, d=dialog: self._forget_dialog(d))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _forget_dialog(self, dialog):
        self._dialogs = [entry for entry in self._dialogs if entry is not dialog]

    def _search(self):
        query = self.search_box.text().strip().lower()
        if not query:
            return
        for key, en, ru, en_body, ru_body in TOPICS:
            if query in " ".join((en, ru, en_body, ru_body)).lower():
                self.go_to(key)
                return
        for item in self.scene.items():
            if isinstance(item, QtWidgets.QGraphicsTextItem) and query in item.toPlainText().lower():
                self.view.centerOn(item)
                return

    def _new_position(self):
        return self.view.mapToScene(self.view.viewport().rect().center())

    def add_kind(self, kind):
        if not self.edit_mode:
            return
        if kind == "image":
            dialog = QtWidgets.QFileDialog(self, "Add image", "", "Images (*.png *.jpg *.jpeg *.bmp *.webp)")
            dialog.setFileMode(QtWidgets.QFileDialog.ExistingFile)
            dialog.fileSelected.connect(lambda path: self.add_image_file(path, self._new_position()))
            self._show_dialog(dialog)
            return
        entry = {"type": kind, "x": 0, "y": 0, "w": 300, "h": 140,
                 "color": "#57d984", "fill": "#263a40"}
        if kind == "text":
            entry.update(text="Double-click to edit / Двойной щелчок для правки",
                         color="#f0f4ff", size=16)
        item = self._make_item(entry)
        self.scene.addItem(item)
        item.setPos(self._new_position())
        item.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, True)
        item.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, True)
        if isinstance(item, QtWidgets.QGraphicsTextItem):
            item.setTextInteractionFlags(QtCore.Qt.TextEditorInteraction)
        self.scene.clearSelection()
        item.setSelected(True)
        self._schedule_save()

    def add_image_file(self, path, position=None):
        try:
            if os.path.getsize(path) > 40 * 1024 * 1024:
                raise ValueError("Image file is too large")
            reader = QtGui.QImageReader(path)
            reader.setAutoTransform(True)
            image = reader.read()
            if image.isNull() or image.width() * image.height() > 100000000:
                raise ValueError("Cannot open this image")
            return self._add_image(image, position)
        except (OSError, ValueError) as exc:
            dialog = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Warning,
                                            "Bake Guide", str(exc),
                                            QtWidgets.QMessageBox.Ok, self)
            self._show_dialog(dialog)
            return False

    def _add_image(self, image, position=None):
        buffer = QtCore.QBuffer()
        buffer.open(QtCore.QIODevice.WriteOnly)
        image.save(buffer, "PNG")
        png = base64.b64encode(bytes(buffer.data())).decode("ascii")
        factor = min(1.0, 600.0 / max(image.width(), image.height()))
        entry = {"type": "image", "png": png,
                 "w": image.width() * factor, "h": image.height() * factor}
        item = self._make_item(entry)
        if item is None:
            return False
        self.scene.addItem(item)
        item.setPos(position if position is not None else self._new_position())
        item.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, True)
        item.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, True)
        self.scene.clearSelection()
        item.setSelected(True)
        self._schedule_save()
        return True

    def paste(self):
        if not self.edit_mode:
            return
        clipboard = QtWidgets.QApplication.clipboard().mimeData()
        focused = self.scene.focusItem()
        if isinstance(focused, QtWidgets.QGraphicsTextItem) and clipboard.hasText():
            cursor = focused.textCursor()
            cursor.insertText(clipboard.text())
            focused.setTextCursor(cursor)
            return
        if clipboard.hasImage():
            image = QtGui.QImage(QtWidgets.QApplication.clipboard().image())
            if not image.isNull() and image.width() * image.height() <= 100000000:
                self._add_image(image)
        elif clipboard.hasUrls():
            for url in clipboard.urls():
                if self.add_image_file(url.toLocalFile()):
                    break
        elif clipboard.hasText():
            self.add_kind("text")
            selected = self.scene.selectedItems()
            if selected and isinstance(selected[0], QtWidgets.QGraphicsTextItem):
                selected[0].setPlainText(clipboard.text())

    def _selected(self):
        selected = self.scene.selectedItems()
        return selected[0] if selected else None

    def _sync_inspector(self):
        item = self._selected()
        if item is None:
            return
        rect = item.boundingRect()
        width = item.pixmap().width() * item.scale() if item.data(0) == "image" else rect.width()
        height = item.pixmap().height() * item.scale() if item.data(0) == "image" else rect.height()
        if isinstance(item, GuideStroke):
            width, height = item.stroke_width, item.stroke_height
        for spin in (self.w_spin, self.h_spin):
            spin.blockSignals(True)
            spin.setMinimum(-100000 if isinstance(item, GuideStroke) else 1)
            spin.blockSignals(False)
        if item.data(0) == "text":
            width = item.textWidth()
        size = (item.font().pointSize() if item.data(0) == "text" else
                item.thickness if isinstance(item, GuideStroke) else
                item.pen().widthF() if item.data(0) in ("rect", "ellipse") else 1)
        for spin, value in ((self.x_spin, item.pos().x()), (self.y_spin, item.pos().y()),
                            (self.w_spin, width), (self.h_spin, height),
                            (self.size_spin, size)):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

    def _apply_inspector(self, *args):
        if not self.edit_mode or self._loading:
            return
        item = self._selected()
        if item is None:
            return
        item.setPos(self.x_spin.value(), self.y_spin.value())
        kind = item.data(0)
        if isinstance(item, GuideStroke):
            item.stroke_width = self.w_spin.value()
            item.stroke_height = self.h_spin.value()
            item.thickness = self.size_spin.value()
            item.rebuild()
        elif kind in ("rect", "ellipse"):
            item.setRect(0, 0, self.w_spin.value(), self.h_spin.value())
            item.setPen(QtGui.QPen(item.pen().color(), self.size_spin.value()))
        elif kind == "text":
            item.setTextWidth(self.w_spin.value())
            font = item.font()
            font.setPointSize(int(self.size_spin.value()))
            item.setFont(font)
        elif kind == "image":
            item.setScale(self.w_spin.value() / max(1, item.pixmap().width()))
        self._schedule_save()

    def change_color(self):
        item = self._selected()
        if item is None or not self.edit_mode:
            return
        dialog = QtWidgets.QColorDialog(self)
        dialog.setCurrentColor(item.defaultTextColor() if item.data(0) == "text" else
                               QtGui.QColor(item.color) if isinstance(item, GuideStroke) else
                               item.pen().color() if item.data(0) in ("rect", "ellipse") else
                               QtGui.QColor("#dbe4ee"))
        dialog.colorSelected.connect(lambda color, target=item: self._apply_color(target, color))
        self._show_dialog(dialog)

    def _apply_color(self, item, color):
        if not color.isValid() or item.scene() is not self.scene:
            return
        kind = item.data(0)
        if kind == "text":
            item.setDefaultTextColor(color)
        elif isinstance(item, GuideStroke):
            item.color = color.name()
            item.rebuild()
        elif kind in ("rect", "ellipse"):
            item.setPen(QtGui.QPen(color, item.pen().widthF()))
        self._schedule_save()

    def change_fill(self):
        item = self._selected()
        if not self.edit_mode or item is None or item.data(0) not in ("rect", "ellipse"):
            return
        dialog = QtWidgets.QColorDialog(self)
        dialog.setCurrentColor(item.brush().color())
        dialog.colorSelected.connect(lambda color, target=item: self._apply_fill(target, color))
        self._show_dialog(dialog)

    def _apply_fill(self, item, color):
        if color.isValid() and item.scene() is self.scene:
            item.setBrush(QtGui.QBrush(color))
            self._schedule_save()

    def bind_selected(self):
        if not self.edit_mode:
            return
        key = self.topic_box.currentData()
        for item in self.scene.selectedItems():
            item.setData(1, key)
        self._schedule_save()

    def rotate_selected(self, amount):
        if not self.edit_mode:
            return
        for item in self.scene.selectedItems():
            item.setRotation(item.rotation() + amount)
        self._schedule_save()

    def delete_selected(self):
        if not self.edit_mode:
            return
        for item in self.scene.selectedItems():
            self.scene.removeItem(item)
        self._schedule_save()

    def undo(self):
        if not self.edit_mode:
            return
        focused = self.scene.focusItem()
        if isinstance(focused, QtWidgets.QGraphicsTextItem) and focused.document().isUndoAvailable():
            focused.document().undo()
            return
        self._commit()
        if self.history_index > 0:
            self.history_index -= 1
            self._load_items(self.history[self.history_index]["items"])
            self._write_current_history()

    def redo(self):
        focused = self.scene.focusItem()
        if self.edit_mode and isinstance(focused, QtWidgets.QGraphicsTextItem) and focused.document().isRedoAvailable():
            focused.document().redo()
            return
        if self.edit_mode and self.history_index < len(self.history) - 1:
            self.history_index += 1
            self._load_items(self.history[self.history_index]["items"])
            self._write_current_history()

    def _write_current_history(self):
        folder = os.path.dirname(self.path)
        if not os.path.isdir(folder):
            os.makedirs(folder)
        temp = self.path + ".tmp." + uuid.uuid4().hex
        try:
            with io.open(temp, "w", encoding="utf-8") as stream:
                json.dump(self.history[self.history_index], stream, ensure_ascii=False, indent=2)
            os.replace(temp, self.path)
        finally:
            if os.path.exists(temp):
                os.remove(temp)

    def closeEvent(self, event):
        self._commit()
        super(GuideWindow, self).closeEvent(event)
