"""
Preview Map Dialog.

Shows the top-down rendered preview frame in a resizable, zoomable dialog.
Default: image scaled to fit the window (zoom-to-fit).
Zoom in/out: Ctrl+scroll wheel, or the + / - / Fit buttons.
"""

from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QCursor, QMouseEvent, QPixmap, QWheelEvent
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class PreviewMapDialog(QDialog):
    _ZOOM_STEP   = 1.25
    _ZOOM_MIN    = 0.05
    _ZOOM_MAX    = 20.0

    def __init__(self, image_path: str, initial_dir: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preview Map")
        self.resize(960, 600)
        self.setSizeGripEnabled(True)

        self._source_path = image_path
        self._initial_dir = initial_dir
        self._pixmap      = QPixmap(image_path)
        self._zoom       = 1.0      # 1.0 = fit; set properly in showEvent
        self._fit_mode   = True
        self._drag_origin: QPoint | None = None

        # ------------------------------------------------------------------ #
        # Scroll area + image label                                           #
        # ------------------------------------------------------------------ #
        self._img_label = QLabel()
        self._img_label.setAlignment(Qt.AlignCenter)
        self._img_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self._scroll = QScrollArea()
        self._scroll.setAlignment(Qt.AlignCenter)
        self._scroll.setWidget(self._img_label)
        self._scroll.setWidgetResizable(False)

        # ------------------------------------------------------------------ #
        # Toolbar                                                             #
        # ------------------------------------------------------------------ #
        self._zoom_in_btn  = QPushButton("+")
        self._zoom_out_btn = QPushButton("−")
        self._fit_btn      = QPushButton("Fit")
        self._save_btn     = QPushButton("Save…")
        for btn in (self._zoom_in_btn, self._zoom_out_btn, self._fit_btn):
            btn.setFixedWidth(40)
        self._zoom_label = QLabel()
        self._zoom_in_btn .clicked.connect(self._on_zoom_in)
        self._zoom_out_btn.clicked.connect(self._on_zoom_out)
        self._fit_btn     .clicked.connect(self._on_fit)
        self._save_btn    .clicked.connect(self._on_save)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)
        toolbar.addWidget(self._zoom_out_btn)
        toolbar.addWidget(self._zoom_in_btn)
        toolbar.addWidget(self._fit_btn)
        toolbar.addWidget(self._zoom_label)
        toolbar.addStretch()
        toolbar.addWidget(self._save_btn)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        toolbar.addWidget(buttons)

        # ------------------------------------------------------------------ #
        # Layout                                                              #
        # ------------------------------------------------------------------ #
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        root.addLayout(toolbar)
        root.addWidget(self._scroll, 1)

        # Install event filter on the scroll area's viewport for mouse/wheel
        vp = self._scroll.viewport()
        vp.installEventFilter(self)
        vp.setCursor(Qt.OpenHandCursor)

    # ------------------------------------------------------------------
    # Qt overrides
    # ------------------------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_fit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit_mode:
            self._apply_fit()

    def eventFilter(self, obj, event):
        if obj is self._scroll.viewport():
            if isinstance(event, QWheelEvent):
                mods = event.modifiers()
                if mods & Qt.ControlModifier:
                    delta = event.angleDelta().y()
                    if delta > 0:
                        self._zoom_by(self._ZOOM_STEP, event.position().toPoint())
                    elif delta < 0:
                        self._zoom_by(1.0 / self._ZOOM_STEP, event.position().toPoint())
                    return True
                if mods & Qt.ShiftModifier:
                    delta = event.angleDelta().y()
                    bar = self._scroll.horizontalScrollBar()
                    bar.setValue(bar.value() - delta)
                    return True
            elif isinstance(event, QMouseEvent):
                if event.type() == event.Type.MouseButtonPress and event.button() == Qt.LeftButton:
                    self._drag_origin = event.position().toPoint()
                    self._scroll.viewport().setCursor(Qt.ClosedHandCursor)
                    return True
                elif event.type() == event.Type.MouseMove and self._drag_origin is not None:
                    delta = event.position().toPoint() - self._drag_origin
                    self._drag_origin = event.position().toPoint()
                    self._scroll.horizontalScrollBar().setValue(
                        self._scroll.horizontalScrollBar().value() - delta.x())
                    self._scroll.verticalScrollBar().setValue(
                        self._scroll.verticalScrollBar().value() - delta.y())
                    return True
                elif event.type() == event.Type.MouseButtonRelease and event.button() == Qt.LeftButton:
                    self._drag_origin = None
                    self._scroll.viewport().setCursor(Qt.OpenHandCursor)
                    return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # Zoom helpers
    # ------------------------------------------------------------------

    def _apply_fit(self):
        if self._pixmap.isNull():
            return
        vp = self._scroll.viewport().size()
        sx = vp.width()  / self._pixmap.width()
        sy = vp.height() / self._pixmap.height()
        self._zoom = min(sx, sy)
        self._render_at_zoom()

    def _zoom_by(self, factor: float, anchor: QPoint | None = None):
        """Zoom by *factor*, keeping the pixel under *anchor* stationary."""
        self._fit_mode = False
        new_zoom = max(self._ZOOM_MIN, min(self._ZOOM_MAX, self._zoom * factor))
        if new_zoom == self._zoom:
            return

        if anchor is not None:
            # Scroll position correction so the point under the cursor stays fixed
            bar_h = self._scroll.horizontalScrollBar()
            bar_v = self._scroll.verticalScrollBar()
            old_x = (bar_h.value() + anchor.x()) / self._zoom
            old_y = (bar_v.value() + anchor.y()) / self._zoom

        self._zoom = new_zoom
        self._render_at_zoom()

        if anchor is not None:
            bar_h.setValue(int(old_x * self._zoom - anchor.x()))
            bar_v.setValue(int(old_y * self._zoom - anchor.y()))

        self._update_zoom_label()

    def _render_at_zoom(self):
        if self._pixmap.isNull():
            return
        w = max(1, int(self._pixmap.width()  * self._zoom))
        h = max(1, int(self._pixmap.height() * self._zoom))
        scaled = self._pixmap.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._img_label.setPixmap(scaled)
        self._img_label.resize(scaled.size())
        self._update_zoom_label()

    def _update_zoom_label(self):
        self._zoom_label.setText(f"{self._zoom * 100:.0f} %")

    # ------------------------------------------------------------------
    # Button slots
    # ------------------------------------------------------------------

    def _on_zoom_in(self):
        self._zoom_by(self._ZOOM_STEP)

    def _on_zoom_out(self):
        self._zoom_by(1.0 / self._ZOOM_STEP)

    def _on_fit(self):
        self._fit_mode = True
        self._apply_fit()

    def _on_save(self):
        import shutil
        from pathlib import Path
        base_dir = self._initial_dir or str(Path(self._source_path).parent)
        dest, _ = QFileDialog.getSaveFileName(
            self, "Save Preview Map",
            str(Path(base_dir) / "preview_map.png"),
            "PNG image (*.png);;JPEG image (*.jpg *.jpeg)",
        )
        if not dest:
            return
        try:
            shutil.copy2(self._source_path, dest)
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
