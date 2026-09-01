"""GitHub release-channel integration for About, updates and opt-in telemetry.

The Superhive package replaces this module at build time.  Keeping the channel
boundary here prevents marketplace packaging rules from leaking into the main
UI and keeps the ordinary GitHub build fully featured.
"""

from __future__ import annotations

import bpy

from .about_update import AboutUpdateDialog
from .dependencies import enable_pyside6


QtCore, _QtGui, QtWidgets = enable_pyside6()


def create_about_dialog(parent=None, translate=None):
    """Create the normal GitHub update/About dialog."""
    return AboutUpdateDialog(parent, translate)


def schedule_post_show(window):
    """Offer telemetry consent after the main window has finished opening."""
    QtCore.QTimer.singleShot(250, lambda: _offer_telemetry_consent(window))


def _offer_telemetry_consent(window):
    """Ask once, non-modally, before the Blender port sends any telemetry."""
    from . import telemetry

    if telemetry.consent_value() is not None:
        if telemetry.consent_value() is True:
            try:
                state = getattr(bpy.context.scene, "bake_tools_settings", None)
                telemetry.report_async(getattr(state, "language", ""))
            except (AttributeError, RuntimeError):
                pass
        return
    existing = getattr(window, "_telemetry_consent_box", None)
    if existing is not None and existing.isVisible():
        return
    box = QtWidgets.QMessageBox(window)
    box.setWindowTitle(window._tr("Anonymous usage statistics"))
    box.setIcon(QtWidgets.QMessageBox.Icon.Information)
    box.setText(window._tr("Help improve Bake Groups Tool?"))
    box.setInformativeText(window._tr(
        "Allow one installation/update event per version. The event contains a random client ID, "
        "product and host versions, interface language, and platform. It never contains scene data, "
        "names, or file paths. You can change this later in About."
    ))
    box.setStandardButtons(
        QtWidgets.QMessageBox.StandardButton.Yes |
        QtWidgets.QMessageBox.StandardButton.No
    )
    box.setDefaultButton(QtWidgets.QMessageBox.StandardButton.No)
    box.setWindowModality(QtCore.Qt.WindowModality.NonModal)
    window._telemetry_consent_box = box

    def finished(result):
        enabled = result == int(QtWidgets.QMessageBox.StandardButton.Yes)
        telemetry.set_consent(enabled)
        if enabled:
            try:
                state = getattr(bpy.context.scene, "bake_tools_settings", None)
                telemetry.report_async(getattr(state, "language", ""))
            except (AttributeError, RuntimeError):
                pass
        window._telemetry_consent_box = None

    box.finished.connect(finished)
    box.open()
