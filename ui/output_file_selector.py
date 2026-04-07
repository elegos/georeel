from pathlib import Path

from PySide6.QtCore import QSettings, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QWidget,
)

_DEFAULT_FILENAME = "output.mkv"
_SETTINGS_KEY = "output/last_directory"


class OutputFileSelector(QWidget):
    path_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("Output video path…")

        self._browse_btn = QPushButton("Browse")
        self._browse_btn.clicked.connect(self._browse)

        self._path_edit.textChanged.connect(self.path_changed)

        layout.addWidget(self._path_edit)
        layout.addWidget(self._browse_btn)

    def _browse(self):
        settings = QSettings("georeel", "georeel")
        last_dir = settings.value(_SETTINGS_KEY, "")

        current = self._path_edit.text().strip()
        if current:
            start = str(Path(current).parent)
        elif last_dir:
            start = last_dir
        else:
            start = str(Path.home())

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save output video",
            str(Path(start) / _DEFAULT_FILENAME),
            "Matroska video (*.mkv)",
        )
        if path:
            if not path.lower().endswith(".mkv"):
                path += ".mkv"
            settings.setValue(_SETTINGS_KEY, str(Path(path).parent))
            self._path_edit.setText(path)

    def output_path(self) -> str | None:
        text = self._path_edit.text().strip()
        return text if text else None

    def set_path(self, path: str):
        self._path_edit.setText(path)

    def clear(self):
        self._path_edit.clear()
