import threading

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QProgressBar,
    QVBoxLayout,
)

from georeel.core.photo_compositor import CompositorError, composite_photos
from georeel.core.pipeline import Pipeline


class _Worker(QObject):
    progress = Signal(int, int)   # done, total
    finished = Signal(str)        # composited_frames_dir
    failed   = Signal(str)        # error message

    def __init__(self, pipeline: Pipeline, settings: dict):
        super().__init__()
        self._pipeline = pipeline
        self._settings = settings
        self._cancel   = threading.Event()

    def cancel(self):
        self._cancel.set()

    def run(self):
        try:
            out_dir = composite_photos(
                self._pipeline,
                self._settings,
                progress_cb=lambda done, total: self.progress.emit(done, total),
                cancel_check=self._cancel.is_set,
            )
            self.finished.emit(out_dir)
        except CompositorError as e:
            self.failed.emit(str(e))
        except Exception as e:
            self.failed.emit(f"Unexpected error: {e}")


class CompositorProgressDialog(QDialog):
    """Shows photo compositing progress with a cancel button."""

    def __init__(self, pipeline: Pipeline, settings: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Compositing photo overlays")
        self.setMinimumWidth(440)
        self.setModal(True)

        self._out_dir: str | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        self._label = QLabel("Preparing…")
        layout.addWidget(self._label)

        self._bar = QProgressBar()
        self._bar.setRange(0, 0)
        layout.addWidget(self._bar)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.rejected.connect(self._cancel)
        layout.addWidget(buttons)
        self._cancel_btn = buttons.button(QDialogButtonBox.Cancel)

        self._thread = QThread(self)
        self._worker = _Worker(pipeline, settings)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)

        self._thread.start()

    # ------------------------------------------------------------------

    def composited_frames_dir(self) -> str | None:
        return self._out_dir

    # ------------------------------------------------------------------

    def _cancel(self):
        self._worker.cancel()
        self._label.setText("Cancelling…")
        self._cancel_btn.setEnabled(False)

    def _on_progress(self, done: int, total: int):
        self._bar.setRange(0, total)
        self._bar.setValue(done)
        self._label.setText(f"Compositing frame {done} / {total}…")

    def _on_finished(self, out_dir: str):
        self._out_dir = out_dir
        self._thread.quit()
        self.accept()

    def _on_failed(self, message: str):
        self._thread.quit()
        self._label.setText(f"Failed: {message}")
        self._cancel_btn.setText("Close")
        self._cancel_btn.clicked.disconnect()
        self._cancel_btn.clicked.connect(self.reject)

    def closeEvent(self, event):
        self._worker.cancel()
        self._thread.quit()
        self._thread.wait(3000)
        super().closeEvent(event)
