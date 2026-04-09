from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QFileDialog, QLabel, QPushButton, QVBoxLayout

from .drop_area import DropArea


class GpxDropArea(DropArea):
    def __init__(self, on_file_selected: Callable[[str], None], parent=None):
        super().__init__(parent)
        self._on_file_selected = on_file_selected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._label = QLabel("Drop GPX file here or click Browse")
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setStyleSheet("color: palette(placeholder-text);")

        self._browse_btn = QPushButton("Browse…")
        self._browse_btn.clicked.connect(self._browse)

        layout.addWidget(self._label)
        layout.addWidget(self._browse_btn, alignment=Qt.AlignCenter)

        self.setStyleSheet(
            "GpxDropArea { border: 2px dashed palette(mid); border-radius: 6px; }"
        )
        self.setMinimumHeight(80)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select GPX file", "", "GPX files (*.gpx)"
        )
        if path:
            self._set_file(path)

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path.lower().endswith(".gpx"):
                self._set_file(path)
                event.acceptProposedAction()
            else:
                event.ignore()

    def set_file(self, path: str):
        self._label.setText(Path(path).name)
        self._label.setStyleSheet("color: palette(window-text); font-weight: bold;")
        self._on_file_selected(path)

    def _set_file(self, path: str):
        self.set_file(path)

    def clear(self):
        self._label.setText("Drop GPX file here or click Browse")
        self._label.setStyleSheet("color: palette(placeholder-text);")
