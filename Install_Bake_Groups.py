# -*- coding: utf-8 -*-
"""Drop this file into Maya's viewport; keep Bake_Groups beside it.

Installs a versioned copy in Maya's user scripts and adds its Shelf button.
"""
from __future__ import absolute_import, print_function

import ast
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ANNOTATION = 'Bake Groups Tool for Maya'
LEGACY_ANNOTATIONS = ('RUN BAKE GROUPS', 'Bake Groups Tool', 'Bake Groups for Maya')
LABEL = 'BAKE GROUPS'


def runtime_folder(installer_path=None):
    """The installer is outside the runtime, not an importable runtime module."""
    location = os.path.realpath(installer_path or __file__)
    folder = os.path.join(os.path.dirname(location), 'Bake_Groups')
    missing = [name for name in ('launcher.py', 'bg_main_window.py', 'bg_version.py')
               if not os.path.isfile(os.path.join(folder, name))]
    if missing:
        raise RuntimeError('Keep Install_Bake_Groups.py beside the complete Bake_Groups '
                           'folder. Missing: ' + ', '.join(missing))
    return folder


def launch_command(folder):
    """Quote Unicode/spaces/apostrophes safely; never embed a shell command."""
    launcher = os.path.join(folder, 'launcher.py')
    return ("import traceback\n"
            "import maya.cmds as cmds\n"
            "try:\n"
            "    launcher = {!r}\n"
            "    namespace = {{'__file__': launcher, '__name__': '__main__'}}\n"
            "    with open(launcher, 'rb') as stream:\n"
            "        source = stream.read()\n"
            "    exec(compile(source, launcher, 'exec'), namespace, namespace)\n"
            "except Exception:\n"
            "    traceback.print_exc()\n"
            "    cmds.warning('Bake Groups failed to launch. See Script Editor.')\n").format(launcher)


def _is_ours(cmds, button):
    annotation = cmds.shelfButton(button, query=True, annotation=True) or ''
    if annotation == ANNOTATION or annotation in LEGACY_ANNOTATIONS:
        return True
    # A legacy user-edited tooltip is recognized only with a matching label AND
    # a Bake Groups launcher command; similarly named third-party tools stay intact.
    label = cmds.shelfButton(button, query=True, label=True) or ''
    command = cmds.shelfButton(button, query=True, command=True) or ''
    return (label in (LABEL, 'Bake Groups', 'Bake Groups Tool') and
            'bake_groups' in command.lower() and 'launcher.py' in command.lower())


def _set_folder_icon(folder, icon_relative):
    if os.name != "nt" or not (folder / icon_relative).is_file():
        return
    ini = folder / "desktop.ini"
    if not ini.exists():
        ini.write_text("[.ShellClassInfo]\nIconResource={},0\n".format(icon_relative),
                       encoding="utf-8-sig")
    processes = (["+r", str(folder)], ["+h", "+s", str(ini)])
    for args in processes:
        subprocess.run(["attrib", *args], check=False, creationflags=subprocess.CREATE_NO_WINDOW)


def _runtime_version(folder):
    source = ast.parse((folder / "bg_version.py").read_text(encoding="utf-8-sig"))
    for statement in source.body:
        if isinstance(statement, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "__version__"
                for target in statement.targets):
            version = ast.literal_eval(statement.value)
            if isinstance(version, str) and re.fullmatch(r"\d+\.\d+\.\d+", version):
                return version
    raise RuntimeError("Invalid Bake Groups package version.")


def _install_runtime(source, scripts, year):
    source = Path(source).resolve()
    scripts = Path(scripts).resolve()
    version = _runtime_version(source)
    required = ("bg_main_window.py", "launcher.py", "bg_version.py", "bin/{}/bg_math_core.pyd".format(year))
    if not all((source / item).is_file() for item in required):
        raise RuntimeError("Extract the complete Bake Groups package for Maya {}.".format(year))
    root = scripts / "Bake_Groups"
    if root.is_symlink() or (root / "versions").is_symlink():
        raise RuntimeError("Invalid Bake Groups installation directory.")
    versions = root / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    runtime = versions / version
    active_file = root / "active_version.json"
    previous_state = json.loads(active_file.read_text(encoding="utf-8-sig")) if active_file.exists() else {}
    previous = previous_state.get("active_version") or previous_state.get("version")
    if previous and tuple(map(int, previous.split("."))) > tuple(map(int, version.split("."))):
        raise RuntimeError("A newer Bake Groups version is already active.")
    with tempfile.TemporaryDirectory(prefix=".bg_install_", dir=str(versions)) as work:
        stage = Path(work)
        if runtime.is_symlink():
            raise RuntimeError("Invalid Bake Groups runtime directory.")
        if not runtime.exists():
            shutil.copytree(str(source), str(stage / "runtime"), ignore=shutil.ignore_patterns(
                "versions", "__pycache__", "active_version.json", "*.pyc", "*.bak"))
            os.replace(str(stage / "runtime"), str(runtime))
        if _runtime_version(runtime) != version or not all((runtime / item).is_file() for item in required):
            raise RuntimeError("The installed version is incomplete: " + str(runtime))
        shutil.copy2(str(source / "launcher.py"), str(stage / "launcher.py"))
        os.replace(str(stage / "launcher.py"), str(root / "launcher.py"))
        if (source / "Bake_Group.ico").is_file() and not (root / "Bake_Group.ico").exists():
            shutil.copy2(str(source / "Bake_Group.ico"), str(root / "Bake_Group.ico"))
        next_state = dict(active_version=version, path=str(runtime),
                          previous_version=previous if previous != version else previous_state.get("previous_version"))
        (stage / "active.json").write_text(json.dumps(next_state, indent=2), encoding="utf-8")
        os.replace(str(stage / "active.json"), str(active_file))
    return root, runtime


def install(open_tool=True):
    import maya.cmds as cmds
    import maya.mel as mel

    if cmds.about(batch=True):
        raise RuntimeError('Install Bake Groups in an interactive Maya window.')
    source = Path(runtime_folder())
    maya_version = re.search(r"\d{4}", str(cmds.about(version=True)))
    if not maya_version:
        raise RuntimeError("Could not determine the Maya version.")
    shelf_top = mel.eval('$tmpBakeGroupsShelf = $gShelfTopLevel')
    shelf = cmds.tabLayout(shelf_top, query=True, selectTab=True)
    if not shelf or not cmds.shelfLayout(shelf, exists=True):
        raise RuntimeError('Select a Shelf tab before installing Bake Groups.')

    folder, runtime = _install_runtime(source, cmds.internalVar(userScriptDir=True), maya_version.group(0))
    _set_folder_icon(source.parent, "Bake_Groups\\Bake_Group.ico")
    _set_folder_icon(source, "Bake_Group.ico")
    _set_folder_icon(folder, "Bake_Group.ico")
    _set_folder_icon(runtime, "Bake_Group.ico")

    children = cmds.shelfLayout(shelf, query=True, childArray=True) or []
    existing = next((item for item in children
                     if cmds.objectTypeUI(item) == 'shelfButton' and _is_ours(cmds, item)), None)
    icon = os.path.join(runtime, 'Bake_Group.png')
    options = dict(label=LABEL, annotation=ANNOTATION, command=launch_command(folder),
                   sourceType='python', image=icon if os.path.isfile(icon) else 'commandButton.png')
    if existing:
        cmds.shelfButton(existing, edit=True, **options)
        button = existing
    else:
        button = cmds.shelfButton(parent=shelf, **options)

    mel.eval('saveAllShelves $gShelfTopLevel')
    print('Bake Groups installed in {}. The Shelf button was saved.'.format(runtime))
    if open_tool:
        # A launch error must not remove an otherwise valid shelf registration.
        exec(compile(options['command'], '<Bake Groups shelf>', 'exec'), {})
    return button


def onMayaDroppedPythonFile(*args):
    return install()


if __name__ == '__main__':
    onMayaDroppedPythonFile()
