import sys
import os
import re
import maya.cmds as cmds

if sys.version_info[0] >= 3:
    from importlib import reload


def _prepare_versioned_math_core_path():
    script_dir = os.path.normpath(os.path.dirname(os.path.abspath(__file__)))
    maya_version_raw = str(cmds.about(version=True))
    version_match = re.search(r"\d{4}", maya_version_raw)
    maya_version = version_match.group(0) if version_match else maya_version_raw
    bin_dir = os.path.join(script_dir, "bin", maya_version)
    if os.path.exists(os.path.join(bin_dir, "bg_math_core.pyd")):
        if bin_dir in sys.path:
            sys.path.remove(bin_dir)
        sys.path.insert(0, bin_dir)
    if script_dir not in sys.path:
        sys.path.append(script_dir)


_prepare_versioned_math_core_path()

modules_to_reload = [
    'bg_core',
    'bg_worker_hp',
    'bg_worker_lp',
    'bg_gt_matcher',
    'bg_final_export',
    'bg_ui_widgets',
    'bg_localization',
    'bg_mixins',
    'bg_main_window'
]

for mod_name in modules_to_reload:
    if mod_name in sys.modules:
        reload(sys.modules[mod_name])
        print("Reloaded {}".format(mod_name))
    else:
        print("{} not loaded yet".format(mod_name))

import bg_main_window
bg_main_window.main()
