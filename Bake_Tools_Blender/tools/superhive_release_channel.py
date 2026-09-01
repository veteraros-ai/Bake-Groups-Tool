"""Superhive release channel without self-update or telemetry code."""

from __future__ import annotations

from pathlib import Path

from .dependencies import enable_pyside6


QtCore, QtGui, QtWidgets = enable_pyside6()

PLUGIN_NAME = "Bake Groups Tool"
AUTHOR_NAME = "Veteraros AI"
CONTACT_EMAIL = "veteraros@gmail.com"
VERSION = "1.0.0"
SUPERHIVE_URL = "https://superhivemarket.com/"


def _addon_root():
    return Path(__file__).resolve().parents[2]


def _manual_path():
    path = _addon_root() / "assets" / "Manual.pur"
    return path if path.is_file() else None


class SuperhiveAboutDialog(QtWidgets.QDialog):
    """Marketplace-safe About dialog; Superhive owns all updates."""

    def __init__(self, parent=None, translate=None):
        super().__init__(parent)
        self._tr = translate or (lambda value: value)
        self.setWindowTitle(self._tr("About Bake Groups Tool"))
        self.setObjectName("BakeGroupsSuperhiveAboutDialog")
        self.setWindowModality(QtCore.Qt.WindowModality.NonModal)
        self.setMinimumWidth(500)
        self.setMaximumWidth(560)
        self._build_ui()
        self._apply_style()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(14)
        icon = QtWidgets.QLabel()
        icon.setFixedSize(52, 52)
        icon.setObjectName("UpdateIcon")
        icon.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        pixmap = QtGui.QPixmap(str(_addon_root() / "assets" / "Bake_Group.png"))
        if pixmap.isNull():
            icon.setText("BG")
        else:
            icon.setPixmap(pixmap.scaled(
                42,
                42,
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            ))
        header.addWidget(icon)

        title_box = QtWidgets.QVBoxLayout()
        title_box.setSpacing(2)
        title = QtWidgets.QLabel(PLUGIN_NAME)
        title.setObjectName("UpdateTitle")
        author = QtWidgets.QLabel(self._tr("by {author}").format(author=AUTHOR_NAME))
        author.setObjectName("UpdateAuthor")
        title_box.addWidget(title)
        title_box.addWidget(author)
        header.addLayout(title_box, 1)
        layout.addLayout(header)

        message = QtWidgets.QLabel(self._tr("Superhive edition"))
        message.setObjectName("UpdateMessage")
        layout.addWidget(message)

        panel = QtWidgets.QFrame()
        panel.setObjectName("VersionPanel")
        grid = QtWidgets.QGridLayout(panel)
        grid.setContentsMargins(14, 10, 14, 10)
        grid.setHorizontalSpacing(16)
        label = QtWidgets.QLabel(self._tr("Installed:"))
        label.setObjectName("VersionLabel")
        value = QtWidgets.QLabel(VERSION)
        value.setObjectName("VersionValueAccent")
        grid.addWidget(label, 0, 0)
        grid.addWidget(value, 0, 1)
        grid.setColumnStretch(1, 1)
        layout.addWidget(panel)

        body = QtWidgets.QLabel(self._tr(
            "Updates for this edition are distributed exclusively through Superhive."
        ))
        body.setWordWrap(True)
        layout.addWidget(body)

        contact = QtWidgets.QLabel(self._tr(
            "If you have questions or ideas for the script, write to this email."
        ))
        contact.setWordWrap(True)
        layout.addWidget(contact)
        email = QtWidgets.QLabel('<a href="mailto:{0}">{0}</a>'.format(CONTACT_EMAIL))
        email.setTextFormat(QtCore.Qt.TextFormat.RichText)
        email.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextBrowserInteraction)
        email.setOpenExternalLinks(True)
        layout.addWidget(email)

        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(7)
        manual = QtWidgets.QPushButton(self._tr("Show manual"))
        manual.clicked.connect(self._open_manual)
        marketplace = QtWidgets.QPushButton(self._tr("Open Superhive"))
        marketplace.clicked.connect(self._open_superhive)
        close_button = QtWidgets.QPushButton(self._tr("Close"))
        close_button.clicked.connect(self.close)
        buttons.addWidget(manual)
        buttons.addWidget(marketplace)
        buttons.addStretch(1)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

    def _open_manual(self):
        path = _manual_path()
        if path is None:
            return
        opened = QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))
        if not opened:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path.parent)))

    def _open_superhive(self):
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(SUPERHIVE_URL))

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
            QLabel#VersionValueAccent { background:#303030; border:1px solid #393939;
                                        border-radius:3px; padding:2px 5px;
                                        color:#8ecfff; font-weight:bold; }
            QPushButton { background:#383838; border:1px solid #505050; border-radius:4px;
                          min-height:28px; padding:1px 12px; }
            QPushButton:hover { background:#484848; }
        """)


def create_about_dialog(parent=None, translate=None):
    return SuperhiveAboutDialog(parent, translate)


def schedule_post_show(_window):
    """Superhive package intentionally has no telemetry prompt."""
    return None
