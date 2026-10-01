"""Run with Maya mayapy and QT_QPA_PLATFORM=offscreen."""
import os
import sys

source = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "Bake_Groups"))
sys.path.insert(0, source)

import bg_mixins

QtWidgets = bg_mixins.QtWidgets


class Harness(QtWidgets.QWidget, bg_mixins.FinalViewMixin, bg_mixins.ExportMixin):
    def __init__(self, pair):
        super(Harness, self).__init__()
        self.pair = pair
        self.exp_inc_cage = QtWidgets.QCheckBox("Cage")
        self.exp_target = QtWidgets.QComboBox()
        self.exp_target.addItems(["Standard FBX", "Marmoset Toolbag"])

    def _active_pair(self):
        return self.pair


def run():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    pair = {"base": "Test", "cage_settings": {"export_enabled": False}}
    owner = Harness(pair)
    panel = owner.build_export_panel(pair)
    assert not owner.exp_inc_cage.isChecked(), "Cage must start unchecked"
    assert not owner._enable_cage_export_on_creation(pair, {})
    assert not owner.exp_inc_cage.isChecked()
    assert owner._enable_cage_export_on_creation(pair, {"made": 1})
    assert pair["cage_settings"]["export_enabled"]
    assert owner.exp_inc_cage.isChecked()
    owner.exp_target.setCurrentIndex(1)
    owner.exp_inc_cage.setChecked(False)
    assert owner._enable_cage_export_on_creation(pair, {"made": 1})
    assert not owner.exp_inc_cage.isChecked(), "Marmoset has no Cage export"
    owner.exp_target.setCurrentIndex(0)
    assert owner.exp_inc_cage.isChecked(), "Standard FBX must restore Cage after creation"
    panel.close()
    owner.close()
    app.processEvents()
    print("Cage export toggle passed")


if __name__ == "__main__":
    run()
