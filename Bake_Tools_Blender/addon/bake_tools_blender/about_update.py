"""Maya-style About / updater dialog adapted to Blender and bundled PySide6."""

from __future__ import annotations

import threading

from .dependencies import enable_pyside6
from . import telemetry, update_service


QtCore, QtGui, QtWidgets = enable_pyside6()


class _ResultRelay(QtCore.QObject):
    result = QtCore.Signal(dict)


class AboutUpdateDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, translate=None):
        super().__init__(parent)
        self._tr = translate or (lambda value: value)
        self._relay = _ResultRelay(self)
        self._relay.result.connect(self._set_result)
        self._checking = False
        self._installing = False
        self._update_info = {}
        self.setWindowTitle(self._tr("Bake Groups Tool Update"))
        self.setObjectName("BakeGroupsUpdateDialog")
        self.setWindowModality(QtCore.Qt.WindowModality.NonModal)
        self.setMinimumWidth(520)
        self.setMaximumWidth(560)
        self._build_ui()
        self._apply_style()
        self._set_idle()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        header = QtWidgets.QHBoxLayout(); header.setSpacing(14)
        icon = QtWidgets.QLabel(); icon.setFixedSize(52, 52); icon.setObjectName("UpdateIcon")
        icon.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        pixmap = QtGui.QPixmap(str(update_service.addon_root() / "assets" / "Bake_Group.png"))
        if pixmap.isNull():
            icon.setText("BG")
        else:
            icon.setPixmap(pixmap.scaled(42, 42, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                                         QtCore.Qt.TransformationMode.SmoothTransformation))
        header.addWidget(icon)
        title_box = QtWidgets.QVBoxLayout(); title_box.setSpacing(2)
        title = QtWidgets.QLabel(update_service.PLUGIN_NAME); title.setObjectName("UpdateTitle")
        author = QtWidgets.QLabel(self._tr("by {author}").format(author=update_service.AUTHOR_NAME))
        author.setObjectName("UpdateAuthor")
        title_box.addWidget(title); title_box.addWidget(author); header.addLayout(title_box, 1)
        layout.addLayout(header)

        self.message = QtWidgets.QLabel(); self.message.setObjectName("UpdateMessage")
        self.body = QtWidgets.QLabel(); self.body.setWordWrap(True)
        layout.addWidget(self.message); layout.addWidget(self.body)

        panel = QtWidgets.QFrame(); panel.setObjectName("VersionPanel")
        grid = QtWidgets.QGridLayout(panel); grid.setContentsMargins(14, 10, 14, 10)
        grid.setHorizontalSpacing(16); grid.setVerticalSpacing(7)
        labels = (self._tr("Installed:"), self._tr("Latest:"), self._tr("Previous:"))
        self.installed = QtWidgets.QLabel(update_service.VERSION)
        self.latest = QtWidgets.QLabel("?"); self.latest.setObjectName("VersionValueAccent")
        self.previous = QtWidgets.QLabel("")
        for row, (label, value) in enumerate(zip(labels, (self.installed, self.latest, self.previous))):
            name = QtWidgets.QLabel(label); name.setObjectName("VersionLabel")
            value.setObjectName(value.objectName() or "VersionValue")
            grid.addWidget(name, row, 0); grid.addWidget(value, row, 1)
        grid.setColumnStretch(1, 1); layout.addWidget(panel)

        self.notes = QtWidgets.QTextEdit(); self.notes.setReadOnly(True); self.notes.setMaximumHeight(105)
        self.notes.hide(); layout.addWidget(self.notes)
        contact = QtWidgets.QLabel(self._tr("If you have questions or ideas for the script, write to this email."))
        contact.setWordWrap(True); layout.addWidget(contact)
        email = QtWidgets.QLabel('<a href="mailto:{0}">{0}</a>'.format(update_service.CONTACT_EMAIL))
        email.setTextFormat(QtCore.Qt.TextFormat.RichText)
        email.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextBrowserInteraction)
        email.setOpenExternalLinks(True); layout.addWidget(email)

        self.telemetry = QtWidgets.QCheckBox(self._tr("Send anonymous usage statistics"))
        self.telemetry.setToolTip(self._tr(
            "Send one opt-in installation/update event per version. No scene data, names, or file paths."
        ))
        self.telemetry.setChecked(telemetry.consent_value() is True)
        self.telemetry.toggled.connect(self._set_telemetry_consent)
        layout.addWidget(self.telemetry)

        self.status = QtWidgets.QLabel(); self.status.setWordWrap(True); self.status.hide(); layout.addWidget(self.status)
        self.progress = QtWidgets.QProgressBar(); self.progress.setRange(0, 0); self.progress.hide(); layout.addWidget(self.progress)

        buttons = QtWidgets.QHBoxLayout(); buttons.setSpacing(7)
        self.check = QtWidgets.QPushButton(self._tr("Check")); self.check.clicked.connect(self._start_check)
        self.update = QtWidgets.QPushButton(self._tr("Update")); self.update.clicked.connect(self._start_install)
        self.manual = QtWidgets.QPushButton(self._tr("Show manual")); self.manual.clicked.connect(self._open_manual)
        self.rollback = QtWidgets.QPushButton(self._tr("Rollback")); self.rollback.clicked.connect(self._request_rollback)
        self.release = QtWidgets.QPushButton(self._tr("Release Notes")); self.release.clicked.connect(self._open_releases)
        self.close_button = QtWidgets.QPushButton(self._tr("Close")); self.close_button.clicked.connect(self.close)
        buttons.addWidget(self.check); buttons.addWidget(self.update); buttons.addWidget(self.manual); buttons.addWidget(self.rollback)
        buttons.addStretch(1); buttons.addWidget(self.release); buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

    def _apply_style(self):
        self.setStyleSheet("""
            QDialog { background:#252525; color:#d7d7d7; font:11px 'Segoe UI'; }
            QLabel { background:transparent; }
            QLabel#UpdateIcon { background:#1d2b2d; border:1px solid #287581; border-radius:8px; }
            QLabel#UpdateTitle { background:#292929; border:1px solid #383838; border-radius:4px;
                                 color:white; font-size:18px; font-weight:bold; padding:4px; }
            QLabel#UpdateAuthor, QLabel#UpdateMessage { background:#292929; border:1px solid #383838;
                                                       border-radius:3px; padding:3px; }
            QFrame#VersionPanel { border:1px solid #393939; border-radius:5px; }
            QLabel#VersionLabel { color:#a7a7a7; }
            QLabel#VersionValue, QLabel#VersionValueAccent { background:#303030; border:1px solid #393939;
                                                             border-radius:3px; padding:2px 5px; }
            QLabel#VersionValueAccent { color:#8ecfff; font-weight:bold; }
            QPushButton { background:#383838; border:1px solid #505050; border-radius:4px;
                          min-height:28px; padding:1px 12px; }
            QPushButton:hover { background:#484848; }
            QTextEdit { background:#191919; border:1px solid #393939; }
        """)

    def _set_idle(self):
        info = update_service.rollback_info()
        self.message.setText(update_service.PLUGIN_NAME)
        self.body.setText(self._tr("Click Check to look for a newer build on GitHub."))
        self.installed.setText(update_service.VERSION); self.latest.setText("?")
        self.previous.setText(info.get("previous_version") or "—")
        self.rollback.setVisible(bool(info.get("available")))
        self.update.hide(); self.release.hide(); self.notes.hide(); self.status.hide(); self.progress.hide()

    def _start_check(self):
        if self._checking:
            return
        self._checking = True; self.check.setEnabled(False)
        self.message.setText(self._tr("Checking for updates..."))
        self.body.setText(self._tr("Looking for a newer build on GitHub..."))
        self.latest.setText("..."); self.progress.show(); self.status.hide()

        def work():
            self._relay.result.emit(update_service.check_for_update())
        threading.Thread(target=work, name="BakeToolsUpdateCheck", daemon=True).start()

    def _set_result(self, result):
        operation = result.get("operation")
        if operation == "update_progress":
            self.progress.setRange(0, 100)
            self.progress.setValue(int(result.get("value") or 0))
            self.status.setText(str(result.get("message") or ""))
            self.status.show()
            return
        if operation == "update_complete":
            self._installing = False
            self.progress.hide(); self.update.setEnabled(True); self.check.setEnabled(True)
            if result.get("error"):
                self.message.setText(self._tr("Update installation failed"))
                self.body.setText(str(result["error"]))
            else:
                self.message.setText(self._tr("Update ready"))
                self.body.setText(self._tr("Restart Blender to finish installing version {version}.").format(
                    version=self._update_info.get("remote_version", "?")))
                self.update.hide()
            return
        self._checking = False; self.check.setEnabled(True); self.progress.hide()
        if result.get("error"):
            self.message.setText(self._tr("Update check failed"))
            self.body.setText(self._tr("Update check failed: {error}").format(error=result["error"]))
            self.latest.setText("?"); self.release.show(); return
        self.latest.setText(str(result.get("remote_version") or "?"))
        if result.get("is_update_available"):
            self._update_info = dict(result)
            self.message.setText(self._tr("New version available"))
            self.body.setText(self._tr("A newer build is available for your current installation."))
            self.notes.setPlainText(str(result.get("release_notes") or self._tr("Release notes are not available for this build.")))
            self.notes.show(); self.release.show(); self.update.show()
        else:
            self.message.setText(self._tr("You're up to date"))
            self.body.setText(self._tr("No newer build was found.")); self.notes.hide(); self.release.show()

    def _start_install(self):
        if self._installing or not self._update_info.get("is_update_available"):
            return
        self._installing = True
        self.check.setEnabled(False); self.update.setEnabled(False)
        self.progress.setRange(0, 100); self.progress.setValue(0); self.progress.show()
        self.status.setText(self._tr("Preparing verified update...")); self.status.show()

        def progress(value, message):
            self._relay.result.emit({
                "operation": "update_progress", "value": value, "message": message,
            })

        def work():
            try:
                update_service.download_and_stage_update(self._update_info, progress)
                result = {"operation": "update_complete"}
            except Exception as exc:
                result = {"operation": "update_complete", "error": str(exc)}
            self._relay.result.emit(result)

        threading.Thread(target=work, name="BakeToolsUpdateInstall", daemon=True).start()

    def _open_manual(self):
        path = update_service.manual_path()
        if path:
            opened = QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))
            if not opened:
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path.parent)))

    def _open_releases(self):
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(update_service.RELEASES_URL))

    def _set_telemetry_consent(self, enabled):
        telemetry.set_consent(bool(enabled))
        if enabled:
            try:
                import bpy
                state = getattr(bpy.context.scene, "bake_tools_settings", None)
                language = getattr(state, "language", "")
            except (ImportError, AttributeError):
                language = ""
            telemetry.report_async(language)

    def _request_rollback(self):
        info = update_service.rollback_info()
        if not info.get("available"):
            return
        box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Icon.Question,
            self._tr("Rollback"),
            self._tr("Restore version {version} on the next Blender start?").format(
                version=info["previous_version"]),
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.Cancel,
            self)

        def finished(result):
            if result != int(QtWidgets.QMessageBox.StandardButton.Yes):
                return
            try:
                update_service.stage_rollback(info["archive"])
                self.status.setText(self._tr("Rollback prepared. Restart Blender to install version {version}.").format(
                    version=info["previous_version"]))
            except (OSError, ValueError) as exc:
                self.status.setText(self._tr("Rollback failed: {error}").format(error=exc))
            self.status.show()
        box.finished.connect(finished); box.open()
