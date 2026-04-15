from typing import Any
"""
Progress dialog for 3D scene building.

Runs core.scene_builder.build_scene in a background thread and shows
step-by-step progress with a Cancel button.  On success the .blend path
is available via blend_path().
"""

import logging
import threading

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QProgressBar,
    QVBoxLayout,
)

from georeel.core.pipeline import Pipeline
from georeel.core.scene_builder import SceneBuildError, build_scene

_log = logging.getLogger(__name__)


class _Worker(QObject):
    status        = Signal(str)       # label update
    tile_progress = Signal(int, int)  # (current_tile, total_tiles)
    finished      = Signal(str)       # blend_path on success
    failed        = Signal(str)       # error message

    def __init__(self, pipeline: Pipeline, blender_exe: str | None,
                 settings: dict[str, Any] | None):
        super().__init__()
        self._pipeline    = pipeline
        self._blender_exe = blender_exe
        self._settings    = settings
        self._cancel      = threading.Event()

    def cancel(self):
        self._cancel.set()

    def run(self):
        try:
            path = build_scene(
                self._pipeline,
                blender_exe=self._blender_exe,
                settings=self._settings,
                tile_progress_cb=lambda cur, tot: self.tile_progress.emit(cur, tot),
                status_cb=lambda msg: self.status.emit(msg),
                cancel_check=self._cancel.is_set,
            )
            if self._cancel.is_set():
                self.failed.emit("Cancelled.")
            else:
                self.finished.emit(path)
        except SceneBuildError as e:
            self.failed.emit(str(e))
        except Exception as e:
            self.failed.emit(f"Unexpected error: {e}")


class SceneBuildDialog(QDialog):
    """Shows progress while build_scene() runs in a background thread."""

    def __init__(self, pipeline: Pipeline, blender_exe: str | None = None,
                 settings: dict[str, Any] | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Building 3D scene")
        self.setMinimumWidth(440)
        self.setModal(True)

        self._blend_path: str | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        self._label = QLabel("Preparing scene data…")
        layout.addWidget(self._label)

        self._bar = QProgressBar()
        self._bar.setRange(0, 0)   # indeterminate until tile progress arrives
        layout.addWidget(self._bar)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self._on_cancel)
        layout.addWidget(buttons)
        self._cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)

        self._thread = QThread(self)
        self._worker = _Worker(pipeline, blender_exe, settings)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.status.connect(self._on_status)
        self._worker.tile_progress.connect(self._on_tile_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)

        self._thread.start()

    # ------------------------------------------------------------------

    def blend_path(self) -> str | None:
        return self._blend_path

    # ------------------------------------------------------------------

    def _on_cancel(self):
        self._worker.cancel()
        self._label.setText("Cancelling…")
        self._cancel_btn.setEnabled(False)

    def _on_status(self, msg: str):
        self._label.setText(msg)
        self._bar.setRange(0, 0)   # back to indeterminate between tile phases

    def _on_tile_progress(self, current: int, total: int):
        self._bar.setRange(0, total)
        self._bar.setValue(current)
        self._label.setText(f"Writing texture tile {current} / {total}…")

    def _on_finished(self, path: str):
        self._blend_path = path
        self._thread.quit()
        self.accept()

    def _on_failed(self, message: str):
        _log.error("Scene build failed: %s", message)
        self._thread.quit()
        self._label.setText(f"Failed: {message}")
        self._label.setWordWrap(True)
        self.resize(600, self.height())
        self._cancel_btn.setText("Close")
        self._cancel_btn.clicked.disconnect()
        self._cancel_btn.clicked.connect(self.reject)

    def closeEvent(self, event):
        self._worker.cancel()
        self._thread.quit()
        self._thread.wait(3000)
        super().closeEvent(event)
