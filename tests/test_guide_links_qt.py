"""Exercise Help-click routing against all static Guide anchors (offscreen Maya Qt)."""
import json
import os
import sys
import tempfile

source = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "Bake_Groups"))
sys.path.insert(0, source)

import bg_guide
import bg_main_window
from bg_mixins import ExportMixin
from bg_ui_widgets import CollapsibleSection

QtCore, QtGui, QtWidgets = bg_guide.QtCore, bg_guide.QtGui, bg_guide.QtWidgets


class HelpBindingHarness(QtWidgets.QMainWindow):
    _bind_help_targets = bg_main_window.BakeManagerUI._bind_help_targets
    _help_topic_for = bg_main_window.BakeManagerUI._help_topic_for
    _help_context_for = bg_main_window.BakeManagerUI._help_context_for
    _handle_help_event = bg_main_window.BakeManagerUI._handle_help_event
    cancel_help_mode = bg_main_window.BakeManagerUI.cancel_help_mode

    def __init__(self):
        super(HelpBindingHarness, self).__init__()
        self.left_panel = QtWidgets.QWidget(self)
        self.setCentralWidget(self.left_panel)
        self.algo_group = CollapsibleSection("Algorithm", self.left_panel)
        for name in bg_main_window.HELP_TARGETS:
            if name == "algo_group":
                continue
            if name.startswith(("le_", "input_")):
                widget = QtWidgets.QLineEdit(self.left_panel)
            elif name.startswith("lbl_"):
                widget = QtWidgets.QLabel(name, self.left_panel)
            elif name.startswith("combo_"):
                widget = QtWidgets.QComboBox(self.left_panel)
            elif name.startswith("spin_"):
                widget = QtWidgets.QSpinBox(self.left_panel)
            elif name.startswith(("chk_", "cb_")):
                widget = QtWidgets.QCheckBox(name, self.left_panel)
            else:
                widget = QtWidgets.QPushButton(name, self.left_panel)
            setattr(self, name, widget)
        self._help_mode = False
        self._help_cursor_set = False
        self._help_swallow_release = False
        self.opened = []
        self.invoked = 0
        self._bind_help_targets()

    def show_guide(self, topic, label="", description=""):
        self.opened.append(topic)

    def eventFilter(self, watched, event):
        return self._handle_help_event(watched, event)


class ExportPanelHarness(object):
    build_export_panel = ExportMixin.build_export_panel

    def _update_export_target_controls(self):
        pass


def click_help(app, harness, widget, expected):
    harness._help_mode = True
    for kind, held in ((QtCore.QEvent.MouseButtonPress, QtCore.Qt.LeftButton),
                       (QtCore.QEvent.MouseButtonRelease, QtCore.Qt.NoButton)):
        event = QtGui.QMouseEvent(kind, QtCore.QPointF(5, 5),
                                  QtCore.Qt.LeftButton, held, QtCore.Qt.NoModifier)
        app.sendEvent(widget, event)
    app.processEvents()
    assert harness.opened[-1] == expected, (widget.objectName(), harness.opened)
    assert harness.invoked == 0, "Help click invoked the normal command"


def run():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    with tempfile.TemporaryDirectory(prefix="bake_guide_links_") as folder:
        bg_guide._document_path = lambda language: os.path.join(folder, "guide.json")
        guide = bg_guide.GuideWindow()
        harness = HelpBindingHarness()
        for name, topic in bg_main_window.HELP_TARGETS.items():
            widget = getattr(harness, name)
            assert widget.property("bg_help_id") == topic, name
            assert harness._help_topic_for(widget) == topic, name
            guide.go_to(topic)
            assert guide.topic_box.currentData() == topic, (name, topic)
            assert any(item.data(1) == topic and item.data(0) == "rect"
                       for item in guide.scene.items()), topic
        assert harness.algo_group.toggle_button.property("bg_help_id") == "hp.algorithm"
        assert harness._help_topic_for(harness.algo_group.toggle_button) == "hp.algorithm"
        app.installEventFilter(harness)
        for widget, topic in (
                (harness.btn_combine_mesh, "hp.prepare.combine"),
                (harness.btn_separate_mesh, "hp.prepare.separate"),
                (harness.algo_group.toggle_button, "hp.algorithm"),
                (harness.btn_create_group, "groups.create"),
                (harness.combo_hp_strategy, "hp.algorithm.strategy"),
                (harness.btn_toggle_groups, "groups.visibility")):
            if isinstance(widget, QtWidgets.QAbstractButton):
                widget.clicked.connect(lambda checked=False: setattr(
                    harness, "invoked", harness.invoked + 1))
            click_help(app, harness, widget, topic)
        app.removeEventFilter(harness)

        panel_owner = ExportPanelHarness()
        panel = panel_owner.build_export_panel(None)
        panel.setParent(harness.left_panel)
        assert panel.property("bg_help_id") == "export.settings"
        for name, topic in (
                ("exp_scope", "export.scope"), ("exp_target", "export.target"),
                ("exp_inc_hp", "export.files"), ("exp_inc_lp", "export.files"),
                ("exp_inc_cage", "export.files"), ("exp_files_sep", "export.files"),
                ("exp_files_one", "export.files"), ("exp_bymat", "export.files"),
                ("exp_lp_one", "export.files")):
            assert getattr(panel_owner, name).property("bg_help_id") == topic, name
            assert harness._help_topic_for(getattr(panel_owner, name)) == topic, name
            guide.go_to(topic)
            assert guide.topic_box.currentData() == topic, name
        panel.close()
        harness.close()
        guide.close()
        app.processEvents()

        # An obsolete saved board is replaced with the shipped manual only.
        old = {"schema": 1, "language": "en", "items": [
            {"type": "rect", "x": 20, "y": 20, "w": 300, "h": 180,
             "color": "#536f88", "fill": "#242d39", "anchor": "overview"},
            {"type": "text", "x": 30, "y": 30, "w": 250,
             "text": "User-edited content", "anchor": "overview"}]}
        with open(os.path.join(folder, "guide.json"), "w", encoding="utf-8") as stream:
            json.dump(old, stream)
        restored = bg_guide.GuideWindow()
        restored.go_to("hp.algorithm.strategy")
        assert restored.topic_box.currentData() == "hp.algorithm.strategy"
        assert any(item.data(1) == "hp.algorithm.strategy" for item in restored.scene.items())
        assert not any(item.data(0) == "text" and item.toPlainText() == "User-edited content"
                       for item in restored.scene.items())
        restored.close()
        app.processEvents()
    print("Guide links and Help clicks passed")


if __name__ == "__main__":
    run()
