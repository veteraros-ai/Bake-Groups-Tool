# Установка Bake Groups для Maya 2022-2027

## Что находится в пакете

- `Bake_Groups` - рабочая папка скрипта: Python-файлы, локализация, иконки и C++ ядро `bg_math_core.pyd` для Maya 2022-2027.
- `_Documentation` - русская и английская документация по установке и работе.

## Куда положить скрипт

1. Открой папку Maya scripts:


C:\Users\<USER>\Documents\maya\scripts


Итоговый путь должен быть таким:


C:\Users\<USER>\Documents\maya\scripts\Bake_Groups


## Как добавить shelf-кнопку BAKE GROUPS

1. Запусти Maya.
2. Выбери shelf-полку, на которой должна появиться кнопка.
3. Внизу Maya переключи командную строку с `MEL` на `Python`.
4. Вставь и выполни код:

________________________________________________________________________

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

________________________________________________________________________

После выполнения на текущей shelf-полке появится кнопка `BAKE GROUPS`.

## Первый запуск

Нажми shelf-кнопку `BAKE GROUPS`. Откроется главное окно Bake Groups.

Язык интерфейса выбирается кнопкой `Language / Язык` рядом с `Save` и `Load`. Выбранный язык сохраняется в настройках Maya и будет использоваться при следующем запуске.

## Если кнопка не появилась

- Проверь, что команда выполнялась как `Python`, а не как `MEL`.
