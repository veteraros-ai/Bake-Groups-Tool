from __future__ import print_function, division, absolute_import

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime

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

PACKAGE_DOWNLOAD_TIMEOUT = 300
DEFAULT_PACKAGE_URL = "https://codeload.github.com/{}/zip/refs/heads/main".format(bg_version.GITHUB_REPOSITORY)


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
    try:
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
    except Exception:
        temp_dir = tempfile.mkdtemp(prefix="BakeGroupsNet_")
        try:
            path = os.path.join(temp_dir, "response.txt")
            _download_with_powershell(url, path, timeout)
            with open(path, "rb") as handle:
                return handle.read().decode("utf-8", "replace")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


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
    release_notes = manifest.get("release_notes") or manifest.get("notes") or ""
    if isinstance(release_notes, (list, tuple)):
        release_notes = "\n".join([str(item) for item in release_notes])
    return {
        "remote_version": remote_version,
        "github_url": manifest.get("github_url") or bg_version.GITHUB_URL,
        "releases_url": manifest.get("releases_url") or bg_version.RELEASES_URL,
        "package_url": manifest.get("package_url") or DEFAULT_PACKAGE_URL,
        "release_notes": release_notes,
    }


def _merge_source_version(update_info, timeout=4):
    try:
        source_version = fetch_remote_version(timeout)
        if is_newer_version(source_version, update_info.get("remote_version")):
            update_info["remote_version"] = source_version
    except Exception:
        pass
    return update_info


def fetch_update_info(timeout=4):
    try:
        return _merge_source_version(_manifest_info_from_text(_read_url(bg_version.UPDATE_MANIFEST_URL, timeout)), timeout)
    except Exception:
        pass

    path = _local_manifest_path()
    if os.path.exists(path):
        with open(path, "r") as handle:
            return _merge_source_version(_manifest_info_from_text(handle.read()), timeout)

    return _merge_source_version({
        "remote_version": fetch_remote_version(timeout),
        "github_url": bg_version.GITHUB_URL,
        "releases_url": bg_version.RELEASES_URL,
        "package_url": DEFAULT_PACKAGE_URL,
    }, timeout)


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
        "package_url": update_info.get("package_url") or DEFAULT_PACKAGE_URL,
        "release_notes": update_info.get("release_notes") or "",
    }


def _current_runtime_dir():
    return os.path.normpath(os.path.dirname(os.path.abspath(__file__)))


def _bootstrap_dir():
    runtime_dir = _current_runtime_dir()
    parent = os.path.dirname(runtime_dir)
    if os.path.basename(parent).lower() == "versions":
        return os.path.dirname(parent)
    return runtime_dir


def _progress(progress_callback, value, message_key):
    if progress_callback:
        progress_callback(value, bg_l10n.text(message_key))


def _read_bytes_url(url, timeout=PACKAGE_DOWNLOAD_TIMEOUT, progress_callback=None):
    try:
        request = Request(
            url,
            headers={
                "User-Agent": "Bake-Groups-Tool/{}".format(bg_version.__version__),
                "Accept": "application/zip,application/octet-stream,*/*",
            }
        )
        response = urlopen(request, timeout=timeout)
        total_raw = response.headers.get("Content-Length") if hasattr(response, "headers") else None
        try:
            total = int(total_raw) if total_raw else 0
        except Exception:
            total = 0
        chunks = []
        loaded = 0
        while True:
            chunk = response.read(1024 * 512)
            if not chunk:
                break
            chunks.append(chunk)
            loaded += len(chunk)
            if total > 0:
                value = 15 + int(min(40, (loaded * 40.0) / float(total)))
                _progress(progress_callback, value, "Downloading update package...")
        _progress(progress_callback, 55, "Downloading update package...")
        return b"".join(chunks)
    except Exception:
        temp_dir = tempfile.mkdtemp(prefix="BakeGroupsNet_")
        try:
            path = os.path.join(temp_dir, "package.bin")
            _progress(progress_callback, 20, "Downloading update package...")
            _download_with_powershell(url, path, timeout)
            _progress(progress_callback, 55, "Downloading update package...")
            with open(path, "rb") as handle:
                return handle.read()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


def _download_with_powershell(url, target_path, timeout=60):
    script = (
        "& { "
        "param([string]$u,[string]$o) "
        "$ErrorActionPreference='Stop'; "
        "$ProgressPreference='SilentlyContinue'; "
        "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; "
        "Invoke-WebRequest -Uri $u -OutFile $o -UseBasicParsing "
        "}"
    )
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-Command", script,
        url,
        target_path,
    ]
    kwargs = {"stderr": subprocess.STDOUT}
    if os.name == "nt":
        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
            kwargs["startupinfo"] = startupinfo
        except Exception:
            pass
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    if sys.version_info[0] >= 3:
        kwargs["timeout"] = max(30, int(timeout or 60))
    try:
        subprocess.check_output(cmd, **kwargs)
    except subprocess.CalledProcessError as exc:
        output = exc.output
        if not isinstance(output, str):
            output = output.decode("utf-8", "replace")
        raise RuntimeError("PowerShell download failed: {}".format(output.strip()))
    if not os.path.exists(target_path) or os.path.getsize(target_path) <= 0:
        raise RuntimeError("PowerShell download produced an empty file")


def _copy_runtime_tree(source_dir, target_dir):
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".git", ".vs", "build")
    shutil.copytree(source_dir, target_dir, ignore=ignore)


def _find_runtime_source(extract_dir):
    for root, dirs, files in os.walk(extract_dir):
        if os.path.basename(root) == "Bake_Groups" and "bg_main_window.py" in files and "launcher.py" in files:
            return root
    raise RuntimeError("Bake_Groups runtime folder not found in update package")


def _write_active_version(bootstrap_dir, version, target_dir):
    data = {
        "active_version": version,
        "installed_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "path": os.path.normpath(target_dir),
    }
    path = os.path.join(bootstrap_dir, "active_version.json")
    with open(path, "w") as handle:
        json.dump(data, handle, indent=2)


def _copy_bootstrap_launcher(source_dir, bootstrap_dir):
    source_launcher = os.path.join(source_dir, "launcher.py")
    target_launcher = os.path.join(bootstrap_dir, "launcher.py")
    if os.path.exists(source_launcher):
        shutil.copy2(source_launcher, target_launcher)
    target_main_window = os.path.join(bootstrap_dir, "bg_main_window.py")
    bootstrap_source = """from __future__ import print_function, division, absolute_import

import os


def main():
    script_dir = os.path.normpath(os.path.dirname(os.path.abspath(__file__)))
    launcher_path = os.path.join(script_dir, "launcher.py")
    if not os.path.exists(launcher_path):
        raise RuntimeError("Bake Groups launcher not found: {}".format(launcher_path))
    namespace = {"__file__": launcher_path, "__name__": "__main__"}
    with open(launcher_path, "rb") as handle:
        source = handle.read()
    if not isinstance(source, str):
        source = source.decode("utf-8", "replace")
    exec(compile(source, launcher_path, "exec"), namespace, namespace)


if __name__ == "__main__":
    main()
"""
    with open(target_main_window, "w") as handle:
        handle.write(bootstrap_source)


def install_update(update_info, progress_callback=None):
    _progress(progress_callback, 5, "Preparing update...")
    version = str(update_info.get("remote_version") or "").strip()
    if not version:
        raise RuntimeError("Update version is not available")

    bootstrap_dir = _bootstrap_dir()
    versions_dir = os.path.join(bootstrap_dir, "versions")
    target_dir = os.path.join(versions_dir, version)
    package_url = update_info.get("package_url") or DEFAULT_PACKAGE_URL

    if os.path.exists(os.path.join(target_dir, "bg_main_window.py")):
        _progress(progress_callback, 90, "Activating update...")
        _write_active_version(bootstrap_dir, version, target_dir)
        _copy_bootstrap_launcher(target_dir, bootstrap_dir)
        _progress(progress_callback, 100, "Update installed.")
        return {
            "success": True,
            "version": version,
            "target_dir": target_dir,
            "already_installed": True,
        }

    if not os.path.exists(versions_dir):
        os.makedirs(versions_dir)

    work_dir = tempfile.mkdtemp(prefix="BakeGroupsUpdate_")
    staging_dir = os.path.join(versions_dir, ".install_{}".format(version))
    try:
        if os.path.exists(staging_dir):
            shutil.rmtree(staging_dir)

        zip_path = os.path.join(work_dir, "package.zip")
        with open(zip_path, "wb") as handle:
            handle.write(_read_bytes_url(package_url, PACKAGE_DOWNLOAD_TIMEOUT, progress_callback))

        _progress(progress_callback, 60, "Extracting update package...")
        extract_dir = os.path.join(work_dir, "extract")
        with zipfile.ZipFile(zip_path, "r") as archive:
            archive.extractall(extract_dir)

        _progress(progress_callback, 75, "Installing update files...")
        source_dir = _find_runtime_source(extract_dir)
        _copy_runtime_tree(source_dir, staging_dir)

        if os.path.exists(target_dir):
            shutil.rmtree(target_dir)
        os.rename(staging_dir, target_dir)
        _progress(progress_callback, 90, "Activating update...")
        _write_active_version(bootstrap_dir, version, target_dir)
        _copy_bootstrap_launcher(target_dir, bootstrap_dir)
        _progress(progress_callback, 100, "Update installed.")
        return {
            "success": True,
            "version": version,
            "target_dir": target_dir,
            "already_installed": False,
        }
    finally:
        if os.path.exists(staging_dir):
            shutil.rmtree(staging_dir, ignore_errors=True)
        shutil.rmtree(work_dir, ignore_errors=True)


class UpdateCheckWorker(QtCore.QThread):
    update_result = QtCore.Signal(dict)

    def run(self):
        try:
            result = check_for_update()
        except Exception as exc:
            result = {"error": str(exc), "is_update_available": False}
        self.update_result.emit(result)


class UpdateInstallWorker(QtCore.QThread):
    install_progress = QtCore.Signal(int, str)
    install_result = QtCore.Signal(dict)

    def __init__(self, update_info, parent=None):
        super(UpdateInstallWorker, self).__init__(parent)
        self.update_info = update_info or {}

    def run(self):
        try:
            result = install_update(self.update_info, self.install_progress.emit)
        except Exception as exc:
            result = {"success": False, "error": str(exc)}
        self.install_result.emit(result)


class UpdateAvailableDialog(QtWidgets.QDialog):
    update_requested = QtCore.Signal()
    release_notes_requested = QtCore.Signal()

    def __init__(self, update_info, parent=None):
        super(UpdateAvailableDialog, self).__init__(parent)
        self.update_info = update_info or {}
        self.install_worker = None
        self.install_success = False
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
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Bake_Group.png")
        pixmap = QtGui.QPixmap(icon_path)
        if pixmap.isNull():
            icon.setText("BG")
        else:
            icon.setPixmap(pixmap.scaled(40, 40, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
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

        self.status_label = QtWidgets.QLabel("")
        self.status_label.setObjectName("UpdateStatus")
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        layout.addWidget(self.status_label)

        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setObjectName("UpdateProgress")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

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

        notes_title = QtWidgets.QLabel(bg_l10n.text("What's New in {version}").format(version=str(self.update_info.get("remote_version") or "")))
        notes_title.setObjectName("ReleaseNotesTitle")
        layout.addWidget(notes_title)

        notes_text = self.update_info.get("release_notes") or bg_l10n.text("Release notes are not available for this build.")
        self.release_notes_box = QtWidgets.QTextEdit()
        self.release_notes_box.setObjectName("ReleaseNotesBox")
        self.release_notes_box.setReadOnly(True)
        self.release_notes_box.setPlainText(str(notes_text))
        self.release_notes_box.setMaximumHeight(110)
        layout.addWidget(self.release_notes_box)

        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)

        self.release_btn = QtWidgets.QPushButton(bg_l10n.text("Release Notes"))
        self.later_btn = QtWidgets.QPushButton(bg_l10n.text("Later"))
        self.update_btn = QtWidgets.QPushButton(bg_l10n.text("Update Now"))
        self.update_btn.setObjectName("PrimaryButton")
        self.release_btn.clicked.connect(self.release_notes_requested.emit)
        self.later_btn.clicked.connect(self.reject)
        self.update_btn.clicked.connect(self.update_requested.emit)
        buttons.addWidget(self.release_btn)
        buttons.addWidget(self.later_btn)
        buttons.addWidget(self.update_btn)
        layout.addLayout(buttons)

    def set_installing(self):
        self._set_status_warning(False)
        self.status_label.setText(bg_l10n.text("Installing update..."))
        self.status_label.show()
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.update_btn.setEnabled(False)
        self.release_btn.setEnabled(False)
        self.later_btn.setEnabled(False)
        self.later_btn.show()

    def set_install_progress(self, value, message):
        self._set_status_warning(False)
        self.progress_bar.setValue(max(0, min(100, int(value or 0))))
        if message:
            self.status_label.setText(message)
            self.status_label.show()

    def set_install_result(self, result):
        self.release_btn.setEnabled(True)
        self.later_btn.setEnabled(True)
        self.later_btn.setText(bg_l10n.text("Close"))
        if result.get("success"):
            self.install_success = True
            self.progress_bar.setValue(100)
            self.update_btn.setEnabled(True)
            self.update_btn.setText(bg_l10n.text("Close"))
            self.later_btn.hide()
            self._set_status_warning(True)
            self.status_label.setText(bg_l10n.text("Update installed. Restart Maya to complete the update."))
        else:
            self.install_success = False
            self.update_btn.setEnabled(True)
            self.update_btn.setText(bg_l10n.text("Retry"))
            self.later_btn.show()
            self._set_status_warning(False)
            self.status_label.setText(bg_l10n.text("Update installation failed: {error}").format(error=result.get("error", "")))
        self.status_label.show()

    def closeEvent(self, event):
        worker = self.install_worker
        if worker and worker.isRunning():
            event.ignore()
            return
        super(UpdateAvailableDialog, self).closeEvent(event)

    def _set_status_warning(self, enabled):
        object_name = "UpdateStatusWarning" if enabled else "UpdateStatus"
        if self.status_label.objectName() == object_name:
            return
        self.status_label.setObjectName(object_name)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.status_label.update()

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
            QLabel#UpdateStatus {
                color: #83ddd6;
                background-color: #1f2b2c;
                border: 1px solid #34595c;
                border-radius: 5px;
                padding: 8px;
            }
            QLabel#UpdateStatusWarning {
                color: #f3ca63;
                background-color: #332c1b;
                border: 1px solid #8a6b25;
                border-radius: 5px;
                font-weight: 700;
                padding: 8px;
            }
            QProgressBar#UpdateProgress {
                background-color: #1f1f1f;
                border: 1px solid #353535;
                border-radius: 5px;
                color: #dcdcdc;
                height: 14px;
                text-align: center;
            }
            QProgressBar#UpdateProgress::chunk {
                background-color: #2c7775;
                border-radius: 4px;
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
            QLabel#ReleaseNotesTitle {
                color: #f2f2f2;
                font-size: 12px;
                font-weight: 700;
            }
            QTextEdit#ReleaseNotesBox {
                background-color: #1f1f1f;
                border: 1px solid #353535;
                border-radius: 5px;
                color: #cfcfcf;
                padding: 8px;
                selection-background-color: #2c7775;
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
    dialog.release_notes_requested.connect(lambda: open_url(update_info.get("releases_url") or bg_version.RELEASES_URL))

    def start_install():
        if getattr(dialog, "install_success", False):
            dialog.accept()
            return
        if dialog.install_worker and dialog.install_worker.isRunning():
            return
        dialog.set_installing()
        worker = UpdateInstallWorker(update_info, dialog)
        dialog.install_worker = worker
        worker.install_progress.connect(dialog.set_install_progress)
        worker.install_result.connect(dialog.set_install_result)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(lambda: setattr(dialog, "install_worker", None))
        worker.start()

    dialog.update_requested.connect(start_install)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog
