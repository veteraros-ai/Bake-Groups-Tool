# Installing Bake Groups for Maya 2022-2027

## Package Contents

- `Bake_Groups` - the runtime tool folder: Python files, localization, icons, and the compiled C++ core `bg_math_core.pyd` for Maya 2022-2027.
- `_Documentation` - Russian and English installation and user documentation.

## Where To Copy The Tool

1. Open your Maya scripts folder:


C:\Users\<USER>\Documents\maya\scripts


2. Copy the package folder `Bake_Groups` into that folder.


The final path must look like this:


C:\Users\<USER>\Documents\maya\scripts\Bake_Groups



## How To Add The BAKE GROUPS Shelf Button

1. Start Maya.
2. Select the shelf tab where you want the button to appear.
3. At the bottom of Maya, switch the command line from `MEL` to `Python`.
4. Paste and run this code:

____________________________________________________________________________________

import os
import sys
import maya.cmds as cmds

maya_dir = cmds.internalVar(userAppDir=True)
target_dir = os.path.normpath(os.path.join(maya_dir, "scripts", "Bake_Groups"))
file_path = os.path.join(target_dir, "install_shelf.py")

if not os.path.exists(target_dir):
    cmds.error("Folder not found: {0}".format(target_dir))

if not os.path.exists(file_path):
    cmds.error("File install_shelf.py missing in: {0}".format(target_dir))

if target_dir not in sys.path:
    sys.path.append(target_dir)

import install_shelf

if sys.version_info[0] >= 3:
    import importlib
    importlib.reload(install_shelf)
else:
    reload(install_shelf)

____________________________________________________________________________________


After the command runs, the `BAKE GROUPS` button will appear on the current shelf.

## First Launch

Click the `BAKE GROUPS` shelf button. The main Bake Groups window will open.

The interface language is selected with the `Language` button next to `Save` and `Load`. The selected language is stored in Maya preferences and will be restored on the next launch.

## If The Button Does Not Appear

- Make sure the command was executed as `Python`, not `MEL`.
