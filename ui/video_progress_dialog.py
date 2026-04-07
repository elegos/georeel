import threading

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QProgressBar,
    QVBoxLayout,
)

from core.video_assembler import VideoAssembleError, assemble_video


class _Worker(QObject):
    progress = Signal(int, int)   # current_frame, total_frames
    finished = Signal()
    failed   = Signal(str)

    def __init__(self, frames_dir: str, output_path: str,
                 settings: dict, total_frames: int, gpx_path: str | None = None):
        super().__init__()
        self._frames_dir   = frames_dir
        self._output_path  = output_path
        self._settings     = settings
        self._total_frames = total_frames
        self._gpx_path     = gpx_path
        self._cancel       = threading.Event()

    def cancel(self):
        self._cancel.set()

    def run(self):
        try:
            assemble_video(
                self._frames_dir,
                self._output_path,
                self._settings,
                self._total_frames,
                gpx_path=self._gpx_path,
                progress_cb=lambda cur, tot: self.progress.emit(cur, tot),
                cancel_check=self._cancel.is_set,
            )
            self.finished.emit()
        except VideoAssembleError as e:
            self.failed.emit(str(e))
        except Exception as e:
            self.failed.emit(f"Unexpected error: {e}")


class VideoProgressDialog(QDialog):
    """Shows FFmpeg encoding progress with a cancel button."""

    def __init__(self, frames_dir: str, output_path: str,
                 settings: dict, total_frames: int,
                 gpx_path: str | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Encoding video")
        self.setMinimumWidth(440)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        encoder = settings.get("output/encoder", "")
        self._label = QLabel(f"Encoding with {encoder}…")
        layout.addWidget(self._label)

        self._bar = QProgressBar()
        self._bar.setRange(0, max(total_frames, 1))
        layout.addWidget(self._bar)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.rejected.connect(self._cancel)
        layout.addWidget(buttons)
        self._cancel_btn = buttons.button(QDialogButtonBox.Cancel)

        self._thread = QThread(self)
        self._worker = _Worker(frames_dir, output_path, settings, total_frames, gpx_path)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)

        self._thread.start()

    # ------------------------------------------------------------------

    def _cancel(self):
        self._worker.cancel()
        self._label.setText("Cancelling…")
        self._cancel_btn.setEnabled(False)

    def _on_progress(self, current: int, total: int):
        self._bar.setRange(0, total)
        self._bar.setValue(current)
        self._label.setText(f"Encoding frame {current} / {total}…")

    def _on_finished(self):
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
