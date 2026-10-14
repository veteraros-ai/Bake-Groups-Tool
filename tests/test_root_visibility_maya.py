"""Regression: HP/LP root toggles must preserve descendant visibility."""
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
source = (sys.argv[1] if len(sys.argv) > 1 else
          os.path.join(os.path.dirname(__file__), "..", "Bake_Groups"))
sys.path.insert(0, os.path.abspath(source))
sys.path.insert(0, os.path.join(os.path.abspath(source), "bin", "2027", "runtime"))

import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds
from bg_main_window import BakeManagerUI


class Button:
    def setStyleSheet(self, style):
        self.style = style


class Harness:
    toggle_root_vis = BakeManagerUI.toggle_root_vis
    _hide_cage_root_if_not_export = BakeManagerUI._hide_cage_root_if_not_export
    # Bind the old implementation too, so the test reproduces the regression
    # before that helper is removed.
    if hasattr(BakeManagerUI, "_reveal_hidden_transforms_under"):
        _reveal_hidden_transforms_under = BakeManagerUI._reveal_hidden_transforms_under

    def __init__(self, hp, lp):
        self.root_pairs = [{"id": "test", "base": "VisibilityTest"}]
        self.active_root_id = "test"
        self.core = SimpleNamespace(resolve_main_nodes=lambda pair: (hp, lp, None))
        self.btn_toggle_hp, self.btn_toggle_lp = Button(), Button()
        self.is_final_view = False
        self.cage = None
        self.refreshes = 0

    def _cage_toggle_node(self):
        return self.cage

    def set_localized_button_state(self, button, text):
        button.text = text

    def refresh_left_panel(self):
        self.refreshes += 1


def descendants_snapshot(*roots):
    return {node: bool(cmds.getAttr(node + ".visibility"))
            for root in roots
            for node in (cmds.listRelatives(root, allDescendents=True, fullPath=True) or [])}


def run():
    cmds.file(new=True, force=True)
    hp = cmds.group(empty=True, name="VisibilityTest_HP")
    lp = cmds.group(empty=True, name="VisibilityTest_LP")
    for root, side in ((hp, "HP"), (lp, "LP")):
        for index in range(3):
            group = cmds.group(empty=True, name="Group%d_%s" % (index, side), parent=root)
            mesh = cmds.polyCube(name="Mesh%d_%s" % (index, side), constructionHistory=False)[0]
            mesh = cmds.parent(mesh, group)[0]
            cmds.setAttr(group + ".visibility", index == 0)
            if index == 1:
                cmds.setAttr(mesh + ".visibility", False)

    owner = Harness(hp, lp)
    expected = descendants_snapshot(hp, lp)
    for final_view in (False, True):
        owner.is_final_view = final_view
        for side, root in (("HP", hp), ("LP", lp)):
            for state in (False, True, False, True):
                owner.toggle_root_vis(side, state)
                assert bool(cmds.getAttr(root + ".visibility")) == state
                assert descendants_snapshot(hp, lp) == expected, (side, state, final_view)
    assert owner.refreshes == 0

    cage = cmds.group(empty=True, name="VisibilityTest_Cage")
    cage_child = cmds.group(empty=True, name="Hidden_Cage_Subgroup", parent=cage)
    cmds.setAttr(cage_child + ".visibility", False)
    owner.cage = cage
    for state in (False, True):
        owner.toggle_root_vis("LP", state)
        assert bool(cmds.getAttr(cage + ".visibility")) == state
        assert not cmds.getAttr(cage_child + ".visibility")
        assert descendants_snapshot(hp, lp) == expected
    print("ROOT_VISIBILITY_PASS: HP/LP toggles preserve subgroup and mesh states")


if __name__ == "__main__":
    try:
        run()
    finally:
        maya.standalone.uninitialize()
