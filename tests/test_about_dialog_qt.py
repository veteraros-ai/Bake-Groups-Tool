"""Smoke-test the About / Updates window without an update network request."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
source = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "Bake_Groups"))
sys.path.insert(0, os.environ.get("BG_TEST_RUNTIME", source))

import bg_update


class GuideHost(bg_update.QtWidgets.QWidget):
    def __init__(self):
        super(GuideHost, self).__init__()
        self.opened_topics = []

    def show_guide(self, topic):
        self.opened_topics.append(topic)


def run():
    app = bg_update.QtWidgets.QApplication.instance() or bg_update.QtWidgets.QApplication([])
    host = GuideHost()
    checks = []
    dialog = bg_update.open_updates_window(host, lambda: checks.append(True))
    app.processEvents()
    assert dialog.isVisible()
    assert dialog.manual_btn.isVisible()
    dialog.manual_btn.click()
    assert host.opened_topics == ["overview"]
    dialog.check_btn.click()
    assert checks == [True]
    assert dialog.close()

    no_host_dialog = bg_update.open_updates_window(None, lambda: None)
    app.processEvents()
    assert no_host_dialog.isVisible()
    assert not no_host_dialog.manual_btn.isVisible()
    assert no_host_dialog.close()
    print("About dialog opens and controls work")


if __name__ == "__main__":
    run()
