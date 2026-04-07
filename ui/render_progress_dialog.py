import threading

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QProgressBar,
    QVBoxLayout,
)

from core.frame_renderer import FrameRenderError, render_frames
from core.pipeline import Pipeline


class _Worker(QObject):
    progress = Signal(int, int)   # current_frame, total_frames
    finished = Signal(str)        # frames_dir
    failed   = Signal(str)        # error message

    def __init__(self, pipeline: Pipeline, settings: dict, blender_exe: str | None):
        super().__init__()
        self._pipeline    = pipeline
        self._settings    = settings
        self._blender_exe = blender_exe
        self._cancel      = threading.Event()

    def cancel(self):
        self._cancel.set()

    def run(self):
        try:
            frames_dir = render_frames(
                self._pipeline,
                self._settings,
                blender_exe=self._blender_exe,
                progress_cb=lambda cur, tot: self.progress.emit(cur, tot),
                cancel_check=self._cancel.is_set,
            )
            self.finished.emit(frames_dir)
        except FrameRenderError as e:
            self.failed.emit(str(e))
        except Exception as e:
            self.failed.emit(f"Unexpected error: {e}")


class RenderProgressDialog(QDialog):
    """Shows frame-by-frame render progress with a cancel button."""

    def __init__(self, pipeline: Pipeline, settings: dict,
                 blender_exe: str | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Rendering frames")
        self.setMinimumWidth(440)
        self.setModal(True)

        self._frames_dir: str | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        self._label = QLabel("Initialising Blender…")
        layout.addWidget(self._label)

        self._bar = QProgressBar()
        self._bar.setRange(0, 0)
        layout.addWidget(self._bar)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.rejected.connect(self._cancel)
        layout.addWidget(buttons)
        self._cancel_btn = buttons.button(QDialogButtonBox.Cancel)

        self._thread = QThread(self)
        self._worker = _Worker(pipeline, settings, blender_exe)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)

        self._thread.start()

    # ------------------------------------------------------------------

    def frames_dir(self) -> str | None:
        return self._frames_dir

    # ------------------------------------------------------------------

    def _cancel(self):
        self._worker.cancel()
        self._label.setText("Cancelling…")
        self._cancel_btn.setEnabled(False)

    def _on_progress(self, current: int, total: int):
        self._bar.setRange(0, total)
        self._bar.setValue(current)
        self._label.setText(f"Rendering frame {current} / {total}…")

    def _on_finished(self, frames_dir: str):
        self._frames_dir = frames_dir
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
