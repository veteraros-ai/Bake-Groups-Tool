from __future__ import print_function, division, absolute_import

import json
import os
import re

try:
    from urllib.request import Request, urlopen
except ImportError:
    from urllib2 import Request, urlopen

try:
    from PySide6 import QtWidgets, QtCore, QtGui
except ImportError:
    from PySide2 import QtWidgets, QtCore, QtGui

import bg_localization as bg_l10n
import bg_version


def _version_tuple(value):
    value = str(value or "").strip().lstrip("vV")
    numbers = re.findall(r"\d+", value.split("-", 1)[0].split("+", 1)[0])
    parts = [int(item) for item in numbers[:3]]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer_version(remote_version, current_version):
    return _version_tuple(remote_version) > _version_tuple(current_version)


def _read_url(url, timeout=4):
    request = Request(
        url,
        headers={
            "User-Agent": "Bake-Groups-Tool/{}".format(bg_version.__version__),
            "Accept": "application/json,text/plain",
        }
    )
    response = urlopen(request, timeout=timeout)
    data = response.read()
    if not isinstance(data, str):
        data = data.decode("utf-8", "replace")
    return data


def fetch_remote_version(timeout=4):
    data = _read_url(bg_version.VERSION_SOURCE_URL, timeout)
    match = re.search(r"__version__\s*=\s*[\"']([^\"']+)[\"']", data)
    if not match:
        raise ValueError("Remote version marker not found")
    return match.group(1).strip()


def _local_manifest_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "update_manifest.json")


def _manifest_info_from_text(data):
    manifest = json.loads(data)
    remote_version = str(manifest.get("latest_version") or manifest.get("version") or "").strip()
    if not remote_version:
        raise ValueError("Remote manifest version not found")
    return {
        "remote_version": remote_version,
        "github_url": manifest.get("github_url") or bg_version.GITHUB_URL,
        "releases_url": manifest.get("releases_url") or bg_version.RELEASES_URL,
    }


def fetch_update_info(timeout=4):
    try:
        return _manifest_info_from_text(_read_url(bg_version.UPDATE_MANIFEST_URL, timeout))
    except Exception:
        pass

    path = _local_manifest_path()
    if os.path.exists(path):
        with open(path, "r") as handle:
            return _manifest_info_from_text(handle.read())

    return {
        "remote_version": fetch_remote_version(timeout),
        "github_url": bg_version.GITHUB_URL,
        "releases_url": bg_version.RELEASES_URL,
    }


def check_for_update():
    update_info = fetch_update_info()
    current_version = bg_version.__version__
    return {
        "plugin_name": bg_version.PLUGIN_NAME,
        "author": bg_version.AUTHOR_NAME,
        "current_version": current_version,
        "remote_version": update_info.get("remote_version"),
        "is_update_available": is_newer_version(update_info.get("remote_version"), current_version),
        "github_url": update_info.get("github_url") or bg_version.GITHUB_URL,
        "releases_url": update_info.get("releases_url") or bg_version.RELEASES_URL,
    }


class UpdateCheckWorker(QtCore.QThread):
    update_result = QtCore.Signal(dict)

    def run(self):
        try:
            result = check_for_update()
        except Exception as exc:
            result = {"error": str(exc), "is_update_available": False}
        self.update_result.emit(result)


class UpdateAvailableDialog(QtWidgets.QDialog):
    update_requested = QtCore.Signal()
    release_notes_requested = QtCore.Signal()

    def __init__(self, update_info, parent=None):
        super(UpdateAvailableDialog, self).__init__(parent)
        self.update_info = update_info or {}
        self.setWindowTitle(bg_l10n.text("Bake Groups Tool Update"))
        self.setObjectName("BakeGroupsUpdateDialog")
        self.setModal(True)
        self.setMinimumWidth(520)
        self.setMaximumWidth(560)
        self._build_ui()
        self._apply_style()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(16)

        header_layout = QtWidgets.QHBoxLayout()
        header_layout.setSpacing(14)

        icon = QtWidgets.QLabel()
        icon.setFixedSize(52, 52)
        icon.setObjectName("UpdateIcon")
        icon.setAlignment(QtCore.Qt.AlignCenter)
        icon.setText("BG")
        header_layout.addWidget(icon)

        title_layout = QtWidgets.QVBoxLayout()
        title_layout.setSpacing(2)
        title = QtWidgets.QLabel(self.update_info.get("plugin_name") or bg_version.PLUGIN_NAME)
        title.setObjectName("UpdateTitle")
        author = QtWidgets.QLabel(bg_l10n.text("by {author}").format(author=self.update_info.get("author") or bg_version.AUTHOR_NAME))
        author.setObjectName("UpdateAuthor")
        title_layout.addWidget(title)
        title_layout.addWidget(author)
        header_layout.addLayout(title_layout, 1)
        layout.addLayout(header_layout)

        message = QtWidgets.QLabel(bg_l10n.text("New version available"))
        message.setObjectName("UpdateMessage")
        layout.addWidget(message)

        body = QtWidgets.QLabel(bg_l10n.text("A newer build is available for your current installation."))
        body.setObjectName("UpdateBody")
        body.setWordWrap(True)
        layout.addWidget(body)

        versions = QtWidgets.QFrame()
        versions.setObjectName("VersionPanel")
        versions_layout = QtWidgets.QGridLayout(versions)
        versions_layout.setContentsMargins(14, 10, 14, 10)
        versions_layout.setHorizontalSpacing(16)
        versions_layout.setVerticalSpacing(8)

        current_label = QtWidgets.QLabel(bg_l10n.text("Installed:"))
        latest_label = QtWidgets.QLabel(bg_l10n.text("Latest:"))
        current_value = QtWidgets.QLabel(str(self.update_info.get("current_version") or bg_version.__version__))
        latest_value = QtWidgets.QLabel(str(self.update_info.get("remote_version") or ""))
        current_label.setObjectName("VersionLabel")
        latest_label.setObjectName("VersionLabel")
        current_value.setObjectName("VersionValue")
        latest_value.setObjectName("VersionValueAccent")
        versions_layout.addWidget(current_label, 0, 0)
        versions_layout.addWidget(current_value, 0, 1)
        versions_layout.addWidget(latest_label, 1, 0)
        versions_layout.addWidget(latest_value, 1, 1)
        versions_layout.setColumnStretch(1, 1)
        layout.addWidget(versions)

        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)

        release_btn = QtWidgets.QPushButton(bg_l10n.text("Release Notes"))
        later_btn = QtWidgets.QPushButton(bg_l10n.text("Later"))
        update_btn = QtWidgets.QPushButton(bg_l10n.text("Update Now"))
        update_btn.setObjectName("PrimaryButton")
        release_btn.clicked.connect(self.release_notes_requested.emit)
        later_btn.clicked.connect(self.reject)
        update_btn.clicked.connect(self.update_requested.emit)
        buttons.addWidget(release_btn)
        buttons.addWidget(later_btn)
        buttons.addWidget(update_btn)
        layout.addLayout(buttons)

    def _apply_style(self):
        self.setStyleSheet("""
            QDialog#BakeGroupsUpdateDialog {
                background-color: #242424;
                color: #d7d7d7;
                font-family: 'Segoe UI', Arial, sans-serif;
                font-size: 12px;
            }
            QLabel#UpdateIcon {
                background-color: #202f32;
                border: 1px solid #3c7478;
                border-radius: 8px;
                color: #7de0d9;
                font-size: 18px;
                font-weight: 700;
            }
            QLabel#UpdateTitle {
                color: #f2f2f2;
                font-size: 20px;
                font-weight: 700;
            }
            QLabel#UpdateAuthor {
                color: #8da7aa;
                font-size: 11px;
            }
            QLabel#UpdateMessage {
                color: #ffffff;
                font-size: 16px;
                font-weight: 650;
            }
            QLabel#UpdateBody {
                color: #b9b9b9;
                line-height: 145%;
            }
            QFrame#VersionPanel {
                background-color: #1f1f1f;
                border: 1px solid #353535;
                border-radius: 6px;
            }
            QLabel#VersionLabel {
                color: #8d8d8d;
            }
            QLabel#VersionValue {
                color: #dcdcdc;
                font-weight: 600;
            }
            QLabel#VersionValueAccent {
                color: #83ddd6;
                font-weight: 700;
            }
            QPushButton {
                background-color: #333333;
                border: 1px solid #4a4a4a;
                border-radius: 4px;
                color: #dddddd;
                min-width: 92px;
                padding: 7px 10px;
            }
            QPushButton:hover {
                background-color: #3d3d3d;
                border-color: #666666;
            }
            QPushButton#PrimaryButton {
                background-color: #2c7775;
                border-color: #3baaa5;
                color: #ffffff;
                font-weight: 700;
            }
            QPushButton#PrimaryButton:hover {
                background-color: #318a87;
            }
        """)


def open_url(url):
    QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))


def show_update_dialog(update_info, parent=None):
    dialog = UpdateAvailableDialog(update_info, parent)
    dialog.update_requested.connect(lambda: open_url(update_info.get("github_url") or bg_version.GITHUB_URL))
    dialog.update_requested.connect(dialog.accept)
    dialog.release_notes_requested.connect(lambda: open_url(update_info.get("releases_url") or bg_version.RELEASES_URL))
    dialog.release_notes_requested.connect(dialog.accept)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog
