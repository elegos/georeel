from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .image_loader import load_qimage

_MAX_DEFAULT_WIDTH = 1080


class PhotoPreviewWindow(QMainWindow):
    photo_removed = Signal(str)

    def __init__(self, paths: list[str], index: int, parent=None):
        super().__init__(parent)
        self._paths = list(paths)
        self._index = index

        # --- central widget ---
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet("background-color: palette(window);")
        layout.addWidget(self._label, stretch=1)

        layout.addWidget(self._build_button_bar())

        self._register_shortcuts()
        self._load_current(first_load=True)

    # ------------------------------------------------------------------
    # Builders
    # ------------------------------------------------------------------

    def _build_button_bar(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 6, 8, 6)

        self._prev_btn = QPushButton("← Previous")
        self._prev_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._prev_btn.clicked.connect(self._go_prev)

        self._next_btn = QPushButton("Next →")
        self._next_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._next_btn.clicked.connect(self._go_next)

        self._del_btn = QPushButton("Delete")
        self._del_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._del_btn.clicked.connect(self._delete_current)

        self._exit_btn = QPushButton("Exit")
        self._exit_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._exit_btn.clicked.connect(self.close)

        row.addWidget(self._prev_btn)
        row.addWidget(self._next_btn)
        row.addStretch()
        row.addWidget(self._del_btn)
        row.addWidget(self._exit_btn)
        return bar

    def _register_shortcuts(self):
        QShortcut(QKeySequence(Qt.Key.Key_Left),   self, self._go_prev)
        QShortcut(QKeySequence(Qt.Key.Key_Right),  self, self._go_next)
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self, self._delete_current)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.close)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _go_prev(self):
        if self._index > 0:
            self._index -= 1
            self._load_current()

    def _go_next(self):
        if self._index < len(self._paths) - 1:
            self._index += 1
            self._load_current()

    def _delete_current(self):
        path = self._paths.pop(self._index)
        self.photo_removed.emit(path)
        if not self._paths:
            self.close()
            return
        if self._index >= len(self._paths):
            self._index = len(self._paths) - 1
        self._load_current()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load_current(self, first_load: bool = False):
        path = self._paths[self._index]
        self.setWindowTitle(Path(path).name)
        self._pixmap = QPixmap.fromImage(load_qimage(path))
        if first_load:
            self.resize(*self._initial_size())
        self._update_pixmap()
        self._update_buttons()

    def _initial_size(self) -> tuple[int, int]:
        if self._pixmap.isNull():
            return _MAX_DEFAULT_WIDTH, 720
        w = min(self._pixmap.width(), _MAX_DEFAULT_WIDTH)
        h = round(self._pixmap.height() * w / self._pixmap.width())
        return w, h

    def _update_pixmap(self):
        if self._pixmap.isNull():
            self._label.setText("Cannot load image.")
            return
        scaled = self._pixmap.scaled(
            self._label.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        self._label.setPixmap(scaled)

    def _update_buttons(self):
        self._prev_btn.setEnabled(self._index > 0)
        self._next_btn.setEnabled(self._index < len(self._paths) - 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_pixmap()
