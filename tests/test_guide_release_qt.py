"""Run with Maya mayapy and QT_QPA_PLATFORM=offscreen."""
import os
import sys
import tempfile

source = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "Bake_Groups"))
sys.path.insert(0, source)

import bg_guide
import bg_main_window


class FakeUI(object):
    def __init__(self):
        self.opened = []

    def show_guide(self, key):
        self.opened.append(key)


def run():
    with tempfile.TemporaryDirectory(prefix="bake_guide_release_") as folder:
        settings = bg_guide.QtCore.QSettings(
            os.path.join(folder, "seen.ini"), bg_guide.QtCore.QSettings.IniFormat)
        ui = FakeUI()
        version = bg_main_window.bg_update.bg_version.__version__
        assert bg_main_window.show_release_card_if_new(ui, settings)
        assert ui.opened == ["release.whats_new"]
        assert not bg_main_window.show_release_card_if_new(ui, settings)
        assert ui.opened == ["release.whats_new"]
        assert bg_guide.whats_new_due(version + ".next", settings)
    print("Guide release-once passed")


if __name__ == "__main__":
    run()
