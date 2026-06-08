import maya.cmds as cmds
import maya.mel as mel
import os
import sys
import re
import shutil  # НОВАЯ СТРОКА

maya_dir = cmds.internalVar(userAppDir=True)
script_dir = os.path.normpath(os.path.dirname(os.path.abspath(__file__)))


def maya_version_folder():
    maya_version_raw = str(cmds.about(version=True))
    version_match = re.search(r"\d{4}", maya_version_raw)
    return version_match.group(0) if version_match else maya_version_raw


try:
    maya_version = maya_version_folder()
    version_bin_dir = os.path.join(script_dir, "bin", maya_version)
    source_pyd = os.path.join(version_bin_dir, "bg_math_core.pyd")

    if os.path.exists(source_pyd):
        if version_bin_dir in sys.path:
            sys.path.remove(version_bin_dir)
        sys.path.insert(0, version_bin_dir)
        print("Bake Groups: C++ core path enabled for Maya {}: {}".format(maya_version, source_pyd))
    else:
        print("Bake Groups WARNING: Compiled bg_math_core.pyd for Maya {} not found at {}".format(maya_version, source_pyd))
except Exception as e:
    print("Bake Groups ERROR: Failed to prepare C++ core path: {}".format(e))


script_name = "bg_main_window"
button_label = "BAKE GROUPS"
icon_name = "BAKE_GROUP.PNG"
icon_path = os.path.join(script_dir, icon_name)
tooltip = "RUN BAKE GROUPS"

final_icon = icon_path if os.path.exists(icon_path) else "commandButton.png"

command_code = """import sys
import os
import traceback
import maya.cmds as cmds

try:
    if sys.version_info[0] >= 3:
        from importlib import reload

    s_dir = r"{script_dir}"
    maya_version_raw = str(cmds.about(version=True))
    version_match = __import__("re").search(r"\d{{4}}", maya_version_raw)
    maya_version = version_match.group(0) if version_match else maya_version_raw
    bin_dir = os.path.join(s_dir, "bin", maya_version)
    if os.path.exists(os.path.join(bin_dir, "bg_math_core.pyd")):
        if bin_dir in sys.path:
            sys.path.remove(bin_dir)
        sys.path.insert(0, bin_dir)
    else:
        print("Bake Groups WARNING: bg_math_core.pyd for Maya {{}} not found at {{}}".format(maya_version, bin_dir))

    if s_dir not in sys.path:
        sys.path.append(s_dir)

    modules_to_delete = [
        m for m in sys.modules
        if m == "{script_name}"
        or m.startswith("bg_")
        or m in ("launcher", "install_shelf")
    ]
    for mod in modules_to_delete:
        del sys.modules[mod]

    import {script_name}
    {script_name}.main()
except Exception:
    traceback.print_exc()
    cmds.warning("Bake Groups failed to launch. See Script Editor for traceback.")
""".format(script_name=script_name, script_dir=script_dir)

try:
    gShelfTopLevel = mel.eval('$tmpVar=$gShelfTopLevel')
    current_shelf = cmds.tabLayout(gShelfTopLevel, query=True, selectTab=True)
except Exception:
    current_shelf = "Shelf1"

shelf_buttons = cmds.shelfLayout(current_shelf, query=True, childArray=True) or []
for btn in shelf_buttons:
    if cmds.objectTypeUI(btn) == "shelfButton":
        if cmds.shelfButton(btn, query=True, label=True) == button_label:
            cmds.deleteUI(btn)

cmds.shelfButton(
    parent=current_shelf,
    label=button_label,
    image=final_icon,
    annotation=tooltip,
    command=command_code,
    sourceType="python"
)

if final_icon == "commandButton.png":
    print("Button created, but icon not found at path: {}".format(icon_path))
else:
    print("Button '{}' with icon successfully added to shelf '{}'".format(button_label, current_shelf))
