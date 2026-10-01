# -*- coding: utf-8 -*-
from __future__ import print_function

import io
import os
import shutil
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import maya.standalone
maya.standalone.initialize(name="python")
import maya.cmds as cmds

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_DIR = os.path.join(ROOT, "Bake_Groups")
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import bg_core


def run():
    root = tempfile.mkdtemp(prefix="BakeGroupsSession_")
    try:
        scene_path = os.path.join(root, "session_test.ma")
        cmds.file(new=True, force=True)
        cmds.file(rename=scene_path)
        cmds.file(save=True, type="mayaAscii")

        first = [{"id": "first", "name": "First"}]
        second = [{"id": "second", "name": "Second"}]
        bg_core.BakeSessionModel.save(first)
        bg_core.BakeSessionModel.save(second)

        json_path = bg_core.BakeSessionModel.get_json_path()
        assert os.path.isfile(json_path)
        assert os.path.isfile(json_path + ".bak")

        with io.open(json_path, "w", encoding="utf-8") as handle:
            handle.write("{broken")
        recovered = bg_core.BakeSessionModel.load()
        assert recovered[0]["id"] == "first"
    finally:
        cmds.file(new=True, force=True)
        shutil.rmtree(root, ignore_errors=True)
    print("Session persistence tests passed")


if __name__ == "__main__":
    run()
