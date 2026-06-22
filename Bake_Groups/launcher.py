import sys
import os
import re
import json
import maya.cmds as cmds

try:
    from PySide6 import QtWidgets, QtCore
except ImportError:
    try:
        from PySide2 import QtWidgets, QtCore
    except ImportError:
        QtWidgets = None
        QtCore = None


WORKSPACE_CONTROL_NAME = "BakeManagerUIWorkspaceControl"


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


def _process_qt_events(cycles=3):
    if not QtWidgets:
        return
    app = QtWidgets.QApplication.instance()
    if not app:
        return
    for _ in range(max(1, int(cycles or 1))):
        try:
            app.processEvents(QtCore.QEventLoop.AllEvents, 50)
        except Exception:
            try:
                app.processEvents()
            except Exception:
                break


def _delete_workspace_control():
    try:
        if cmds.workspaceControl(WORKSPACE_CONTROL_NAME, exists=True):
            cmds.deleteUI(WORKSPACE_CONTROL_NAME, control=True)
    except Exception as exc:
        print("Bake Groups workspace control delete failed: {}".format(exc))
    try:
        if cmds.workspaceControlState(WORKSPACE_CONTROL_NAME, exists=True):
            cmds.workspaceControlState(WORKSPACE_CONTROL_NAME, remove=True)
    except Exception:
        pass


def _delete_old_qt_widgets():
    if not QtWidgets:
        return
    app = QtWidgets.QApplication.instance()
    if not app:
        return
    for widget in list(app.allWidgets()):
        try:
            object_name = widget.objectName()
        except RuntimeError:
            continue
        if object_name not in ("BakeManagerUI", WORKSPACE_CONTROL_NAME):
            continue
        try:
            widget.close()
        except RuntimeError:
            pass
        try:
            widget.setParent(None)
        except RuntimeError:
            pass
        try:
            widget.deleteLater()
        except RuntimeError:
            pass


def _shutdown_existing_bake_groups_ui():
    old_mod = sys.modules.get("bg_main_window")
    old_ui = getattr(old_mod, "bake_manager_ui", None) if old_mod else None
    if old_ui:
        try:
            if hasattr(old_ui, "shutdown_for_reload"):
                old_ui.shutdown_for_reload()
        except Exception as exc:
            print("Bake Groups UI shutdown failed: {}".format(exc))
        try:
            old_ui.close()
        except RuntimeError:
            pass
        try:
            old_ui.setParent(None)
        except RuntimeError:
            pass
        try:
            old_ui.deleteLater()
        except RuntimeError:
            pass
        try:
            setattr(old_mod, "bake_manager_ui", None)
        except Exception:
            pass
    _delete_old_qt_widgets()
    _process_qt_events(4)
    _delete_workspace_control()
    _process_qt_events(2)


runtime_dir = _prepare_versioned_math_core_path()
print("Bake Groups runtime: {}".format(runtime_dir))

_shutdown_existing_bake_groups_ui()

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
