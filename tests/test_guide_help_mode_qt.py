"""Verify the ? click never invokes the selected Bake Tools command."""
import inspect
import os
import sys

source = os.environ.get("BAKE_GROUPS_TEST_SCRIPT_DIR") or os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "Bake_Groups"))
sys.path.insert(0, source)

try:
    from PySide6 import QtWidgets, QtCore, QtGui
except ImportError:
    from PySide2 import QtWidgets, QtCore, QtGui
import bg_main_window


class HelpHarness(QtWidgets.QMainWindow):
    toggle_help_mode = bg_main_window.BakeManagerUI.toggle_help_mode
    _help_topic_for = bg_main_window.BakeManagerUI._help_topic_for
    _help_context_for = bg_main_window.BakeManagerUI._help_context_for
    _handle_help_event = bg_main_window.BakeManagerUI._handle_help_event
    cancel_help_mode = bg_main_window.BakeManagerUI.cancel_help_mode

    def __init__(self):
        super(HelpHarness, self).__init__()
        self.btn_help = QtWidgets.QPushButton("Help", self)
        self.target = QtWidgets.QPushButton("Analyze HP", self)
        self.target.setProperty("bg_help_id", "hp.analyze")
        central = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(central)
        layout.addWidget(self.target)
        layout.addWidget(self.btn_help)
        self.setCentralWidget(central)
        self._help_mode = False
        self._help_cursor_set = False
        self._help_swallow_release = False
        self.opened = []
        self.invoked = 0
        self.target.clicked.connect(self._invoke)
        self.btn_help.clicked.connect(self.toggle_help_mode)

    def _invoke(self):
        self.invoked += 1

    def show_guide(self, topic, label="", description=""):
        self.opened.append(topic)

    def eventFilter(self, watched, event):
        return self._handle_help_event(watched, event)


def click(app, widget, button=QtCore.Qt.LeftButton):
    for kind, held in ((QtCore.QEvent.MouseButtonPress, button),
                       (QtCore.QEvent.MouseButtonRelease, QtCore.Qt.NoButton)):
        event = QtGui.QMouseEvent(kind, QtCore.QPointF(5, 5),
                                  button, held, QtCore.Qt.NoModifier)
        app.sendEvent(widget, event)
    app.processEvents()


def run():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ui = HelpHarness()
    ui.show()
    app.installEventFilter(ui)
    click(app, ui.btn_help)
    assert ui.opened == ["guide.help"]
    assert ui._help_mode and app.overrideCursor().shape() == QtCore.Qt.WhatsThisCursor
    click(app, ui.target)
    assert ui.invoked == 0, "Help click invoked Analyze HP"
    assert ui.opened == ["guide.help", "hp.analyze"]
    assert not ui._help_mode
    assert app.overrideCursor() is None
    row_source = inspect.getsource(bg_main_window.BakeManagerUI.refresh_left_panel)
    assert 'btn_vis.setProperty("bg_help_id", "groups.row")' in row_source
    ui.target.setProperty("bg_help_id", "groups.row")
    click(app, ui.btn_help)
    click(app, ui.target)
    assert ui.opened == ["guide.help", "hp.analyze", "guide.help", "groups.row"]
    assert ui.invoked == 0, "Help click invoked subgroup visibility"
    assert app.overrideCursor() is None
    for cancel in ("help", "escape", "right"):
        before = len(ui.opened)
        click(app, ui.btn_help)
        assert ui._help_mode
        if cancel == "help":
            click(app, ui.btn_help)
        elif cancel == "escape":
            app.sendEvent(ui, QtGui.QKeyEvent(QtCore.QEvent.KeyPress,
                                              QtCore.Qt.Key_Escape, QtCore.Qt.NoModifier))
        else:
            click(app, ui.target, QtCore.Qt.RightButton)
        assert not ui._help_mode and app.overrideCursor() is None
        assert len(ui.opened) == before + 1 and ui.opened[-1] == "guide.help"
    app.removeEventFilter(ui)
    ui.close()
    print("Guide help click passed")


if __name__ == "__main__":
    run()
