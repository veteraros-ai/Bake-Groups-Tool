import maya.cmds as cmds
import maya.mel as mel
import os

script_dir = os.path.normpath(os.path.dirname(os.path.abspath(__file__)))

button_label = "BAKE GROUPS"
icon_name = "Bake_Group.png"
icon_path = os.path.join(script_dir, icon_name)
tooltip = "RUN BAKE GROUPS"

final_icon = icon_path if os.path.exists(icon_path) else "commandButton.png"

command_code = """import os
import traceback
import maya.cmds as cmds

try:
    s_dir = r"{script_dir}"
    launcher_path = os.path.join(s_dir, "launcher.py")
    namespace = {{"__file__": launcher_path, "__name__": "__main__"}}
    with open(launcher_path, "rb") as handle:
        source = handle.read()
    if not isinstance(source, str):
        source = source.decode("utf-8", "replace")
    exec(compile(source, launcher_path, "exec"), namespace, namespace)
except Exception:
    traceback.print_exc()
    cmds.warning("Bake Groups failed to launch. See Script Editor for traceback.")
""".format(script_dir=script_dir)

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
