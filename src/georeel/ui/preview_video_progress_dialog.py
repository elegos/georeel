"""
Progress dialog for preview video rendering.

Runs core.preview_video.render_preview_video in a background thread and shows
frame-by-frame progress.  On success the output path is available via
output_path().
"""

import tempfile
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
from georeel.core.preview_video import PreviewVideoError, render_preview_video


class _Worker(QObject):
    progress = Signal(int, int)  # current, total
    finished = Signal(str)       # output_path
    failed   = Signal(str)       # error message

    def __init__(self, pipeline: Pipeline, settings: dict,
                 output_path: str, blender_exe: str | None):
        super().__init__()
        self._pipeline    = pipeline
        self._settings    = settings
        self._output_path = output_path
        self._blender_exe = blender_exe
        self._cancel      = threading.Event()

    def cancel(self):
        self._cancel.set()

    def run(self):
        try:
            path = render_preview_video(
                self._pipeline,
                self._settings,
                self._output_path,
                blender_exe=self._blender_exe,
                progress_cb=lambda cur, tot: self.progress.emit(cur, tot),
                cancel_check=self._cancel.is_set,
            )
            if self._cancel.is_set():
                self.failed.emit("Cancelled.")
            else:
                self.finished.emit(path)
        except PreviewVideoError as e:
            self.failed.emit(str(e))
        except Exception as e:
            self.failed.emit(f"Unexpected error: {e}")


class PreviewVideoProgressDialog(QDialog):
    """Shows rendering progress for the preview video."""

    def __init__(self, pipeline: Pipeline, settings: dict,
                 blender_exe: str | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Rendering preview video")
        self.setMinimumWidth(440)
        self.setModal(True)

        self._output_path: str | None = None

        # Temp output file
        import os
        fd, tmp = tempfile.mkstemp(prefix="georeel_preview_", suffix=".mp4")
        os.close(fd)
        self._tmp_path = tmp

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        self._label = QLabel("Initialising Blender…")
        layout.addWidget(self._label)

        self._bar = QProgressBar()
        self._bar.setRange(0, 0)
        layout.addWidget(self._bar)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.rejected.connect(self._on_cancel)
        layout.addWidget(buttons)
        self._cancel_btn = buttons.button(QDialogButtonBox.Cancel)

        self._thread = QThread(self)
        self._worker = _Worker(pipeline, settings, self._tmp_path, blender_exe)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)

        self._thread.start()

    # ------------------------------------------------------------------

    def output_path(self) -> str | None:
        return self._output_path

    # ------------------------------------------------------------------

    def _on_cancel(self):
        self._worker.cancel()
        self._label.setText("Cancelling…")
        self._cancel_btn.setEnabled(False)

    def _on_progress(self, current: int, total: int):
        self._bar.setRange(0, total)
        self._bar.setValue(current)
        self._label.setText(f"Rendering preview frame {current} / {total}…")

    def _on_finished(self, path: str):
        self._output_path = path
        self._thread.quit()
        self.accept()

    def _on_failed(self, message: str):
        import logging
        logging.getLogger(__name__).error("Preview video failed: %s", message)
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
