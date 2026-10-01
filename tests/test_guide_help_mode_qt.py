"""Verify the ? click never invokes the selected Bake Tools command."""
import inspect
import os
import sys

source = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "Bake_Groups"))
sys.path.insert(0, source)

try:
    from PySide6 import QtWidgets, QtCore, QtGui
except ImportError:
    from PySide2 import QtWidgets, QtCore, QtGui
import bg_main_window


class HelpHarness(QtWidgets.QMainWindow):
    _help_topic_for = bg_main_window.BakeManagerUI._help_topic_for
    _help_context_for = bg_main_window.BakeManagerUI._help_context_for
    _handle_help_event = bg_main_window.BakeManagerUI._handle_help_event
    cancel_help_mode = bg_main_window.BakeManagerUI.cancel_help_mode

    def __init__(self):
        super(HelpHarness, self).__init__()
        self.btn_help = QtWidgets.QPushButton("Help", self)
        self.target = QtWidgets.QPushButton("Analyze HP", self)
        self.target.setProperty("bg_help_id", "hp.analyze")
        self.setCentralWidget(self.target)
        self._help_mode = True
        self._help_cursor_set = False
        self._help_swallow_release = False
        self.opened = []
        self.invoked = 0
        self.target.clicked.connect(self._invoke)

    def _invoke(self):
        self.invoked += 1

    def show_guide(self, topic, label="", description=""):
        self.opened.append(topic)

    def eventFilter(self, watched, event):
        return self._handle_help_event(watched, event)


def run():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ui = HelpHarness()
    ui.show()
    app.installEventFilter(ui)
    for kind, held in ((QtCore.QEvent.MouseButtonPress, QtCore.Qt.LeftButton),
                       (QtCore.QEvent.MouseButtonRelease, QtCore.Qt.NoButton)):
        event = QtGui.QMouseEvent(kind, QtCore.QPointF(5, 5),
                                  QtCore.Qt.LeftButton, held,
                                  QtCore.Qt.NoModifier)
        app.sendEvent(ui.target, event)
    app.processEvents()
    assert ui.invoked == 0, "Help click invoked Analyze HP"
    assert ui.opened == ["hp.analyze"]
    assert not ui._help_mode
    row_source = inspect.getsource(bg_main_window.BakeManagerUI.refresh_left_panel)
    assert 'btn_vis.setProperty("bg_help_id", "groups.row")' in row_source
    ui.target.setProperty("bg_help_id", "groups.row")
    ui._help_mode = True
    for kind, held in ((QtCore.QEvent.MouseButtonPress, QtCore.Qt.LeftButton),
                       (QtCore.QEvent.MouseButtonRelease, QtCore.Qt.NoButton)):
        event = QtGui.QMouseEvent(kind, QtCore.QPointF(5, 5),
                                  QtCore.Qt.LeftButton, held,
                                  QtCore.Qt.NoModifier)
        app.sendEvent(ui.target, event)
    app.processEvents()
    assert ui.opened == ["hp.analyze", "groups.row"]
    assert ui.invoked == 0, "Help click invoked subgroup visibility"
    app.removeEventFilter(ui)
    ui.close()
    print("Guide help click passed")


if __name__ == "__main__":
    run()
