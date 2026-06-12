import sys
import os
import re
import json
import maya.cmds as cmds


def _active_runtime_dir(script_dir):
    active_path = os.path.join(script_dir, "active_version.json")
    if os.path.exists(active_path):
        try:
            with open(active_path, "r") as handle:
                data = json.load(handle)
            active_version = data.get("active_version") or data.get("version")
            if active_version:
                candidate = os.path.normpath(os.path.join(script_dir, "versions", str(active_version)))
                if os.path.exists(os.path.join(candidate, "bg_main_window.py")):
                    return candidate
        except Exception as exc:
            print("Bake Groups active version load failed: {}".format(exc))
    return script_dir


def _prepare_versioned_math_core_path():
    script_dir = os.path.normpath(os.path.dirname(os.path.abspath(__file__)))
    runtime_dir = _active_runtime_dir(script_dir)
    maya_version_raw = str(cmds.about(version=True))
    version_match = re.search(r"\d{4}", maya_version_raw)
    maya_version = version_match.group(0) if version_match else maya_version_raw
    bin_dir = os.path.join(runtime_dir, "bin", maya_version)
    if os.path.exists(os.path.join(bin_dir, "bg_math_core.pyd")):
        if bin_dir in sys.path:
            sys.path.remove(bin_dir)
        sys.path.insert(0, bin_dir)
    for path in (runtime_dir, script_dir):
        if path in sys.path:
            sys.path.remove(path)
    sys.path.insert(0, runtime_dir)
    return runtime_dir


runtime_dir = _prepare_versioned_math_core_path()
print("Bake Groups runtime: {}".format(runtime_dir))

modules_to_reload = [
    'bg_version',
    'bg_core',
    'bg_worker_hp',
    'bg_worker_lp',
    'bg_gt_matcher',
    'bg_final_export',
    'bg_ui_widgets',
    'bg_localization',
    'bg_update',
    'bg_mixins',
    'bg_main_window'
]

for mod_name in modules_to_reload:
    if mod_name in sys.modules:
        del sys.modules[mod_name]
        print("Unloaded {}".format(mod_name))
    else:
        print("{} not loaded yet".format(mod_name))

import bg_main_window
bg_main_window.main()
