"""Resolve the bundled Qt runtime without touching Blender's Python install."""

from __future__ import annotations

import sys
from pathlib import Path


VENDOR_DIR = Path(__file__).resolve().parent / "vendor"


class QtDependencyError(RuntimeError):
    """Raised when a release does not contain its vendored Qt runtime."""


def enable_pyside6():
    """Add the bundled PySide6 wheels to sys.path and return Qt modules.

    The dependency lives in the add-on folder so artists never need to run pip
    against Blender's installation or their system Python.
    """
    vendor = str(VENDOR_DIR)
    if not VENDOR_DIR.is_dir():
        raise QtDependencyError(
            "Bundled PySide6 runtime is missing. Reinstall the full Bake Tools Blender release."
        )
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError as exc:
        raise QtDependencyError(
            "Bundled PySide6 runtime could not be loaded. Reinstall the Windows x64 release."
        ) from exc
    return QtCore, QtGui, QtWidgets
