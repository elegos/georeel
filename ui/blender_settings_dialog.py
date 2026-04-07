from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from core.blender_runtime import (
    AVAILABLE_VERSIONS,
    DEFAULT_VERSION,
    BlenderVersion,
    find_blender,
    query_version,
)
from .blender_download_dialog import BlenderDownloadDialog

_SETTINGS_KEY_PATH    = "blender/executable_path"
_SETTINGS_KEY_VERSION = "blender/preferred_version"


def load_blender_path(settings: QSettings) -> str | None:
    return settings.value(_SETTINGS_KEY_PATH) or None


def save_blender_path(settings: QSettings, path: str | None):
    settings.setValue(_SETTINGS_KEY_PATH, path or "")


class BlenderSettingsDialog(QDialog):
    def __init__(self, settings: QSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Blender Settings")
        self.setMinimumWidth(500)
        self._settings = settings

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # --- Version selector ---
        version_group = QGroupBox("Portable Blender version")
        form = QFormLayout(version_group)
        self._version_combo = QComboBox()
        saved_version = settings.value(_SETTINGS_KEY_VERSION, DEFAULT_VERSION.version)
        selected_index = 0
        for i, v in enumerate(AVAILABLE_VERSIONS):
            self._version_combo.addItem(v.label, userData=v)
            if v.version == saved_version:
                selected_index = i
        self._version_combo.setCurrentIndex(selected_index)
        form.addRow("Version:", self._version_combo)
        layout.addWidget(version_group)

        # --- Current executable ---
        path_group = QGroupBox("Blender executable")
        path_layout = QVBoxLayout(path_group)

        self._status_label = QLabel()
        path_layout.addWidget(self._status_label)

        path_row = QHBoxLayout()
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("Auto-detected or manually set path")
        self._path_edit.setReadOnly(True)
        path_row.addWidget(self._path_edit)

        browse_btn = QPushButton("Browse…")
        browse_btn.setFixedWidth(80)
        browse_btn.clicked.connect(self._browse)
        path_row.addWidget(browse_btn)
        path_layout.addLayout(path_row)

        self._download_btn = QPushButton("Download selected version")
        self._download_btn.clicked.connect(self._download)
        path_layout.addWidget(self._download_btn)

        layout.addWidget(path_group)

        # --- Dialog buttons ---
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.accept)
        layout.addWidget(buttons)

        self._refresh_status()

    # ------------------------------------------------------------------

    def _selected_version(self) -> BlenderVersion:
        return self._version_combo.currentData()

    def _refresh_status(self):
        custom = load_blender_path(self._settings)
        exe = find_blender(custom)
        if exe:
            ver = query_version(exe)
            self._path_edit.setText(exe)
            self._status_label.setText(f"Found: {ver or exe}")
        else:
            self._path_edit.setText("")
            self._status_label.setText("Blender not found.")

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Blender executable", "",
            "Blender executable (blender blender.exe);;All files (*)",
        )
        if not path:
            return
        if not Path(path).is_file():
            QMessageBox.warning(self, "Invalid path", "The selected file does not exist.")
            return
        save_blender_path(self._settings, path)
        self._refresh_status()

    def _download(self):
        version = self._selected_version()
        self._settings.setValue(_SETTINGS_KEY_VERSION, version.version)
        dlg = BlenderDownloadDialog(version, parent=self)

        if dlg.exec() == BlenderDownloadDialog.Accepted:
            exe = dlg.executable()
            if exe:
                save_blender_path(self._settings, exe)
                self._refresh_status()
                QMessageBox.information(
                    self, "Download complete",
                    f"Blender {version.label} is ready.\n{exe}",
                )
        else:
            QMessageBox.information(self, "Cancelled", "Download was cancelled.")
