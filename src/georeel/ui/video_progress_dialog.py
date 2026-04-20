"""Progress dialog for video assembly via the georeel-server job API.

The server assembles the video inside its workspace.  When the job is done the
worker downloads the MP4 to the user-chosen *output_path*.
"""

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from georeel.ui.server_client import ServerClient, ServerError


class _Worker(QObject):
    progress = Signal(int, int)   # (pct, 100)
    finished = Signal()
    failed = Signal(str)

    def __init__(
        self, client: ServerClient, job_id: str, output_path: str
    ) -> None:
        super().__init__()
        self._client = client
        self._job_id = job_id
        self._output_path = output_path
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        def _progress(pct: int, _msg: str) -> None:
            self.progress.emit(pct, 100)

        try:
            self._client.poll_job(
                self._job_id,
                progress_cb=_progress,
                cancel_check=lambda: self._cancelled,
            )
            self._client.download_video(self._job_id, self._output_path)
            self.finished.emit()
        except ServerError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"Unexpected error: {exc}")


class VideoProgressDialog(QDialog):
    """Shows FFmpeg encoding progress with a cancel button."""

    def __init__(
        self,
        client: ServerClient,
        job_id: str,
        output_path: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Encoding video")
        self.setMinimumWidth(440)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        self._label = QLabel("Encoding…")
        layout.addWidget(self._label)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        layout.addWidget(self._bar)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self._cancel)
        layout.addWidget(buttons)
        self._cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)

        self._thread = QThread(self)
        self._worker = _Worker(client, job_id, output_path)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)

        self._thread.start()

    # ------------------------------------------------------------------

    def _cancel(self) -> None:
        self._worker.cancel()
        self._label.setText("Cancelling…")
        self._cancel_btn.setEnabled(False)

    def _on_progress(self, pct: int, _total: int) -> None:
        self._bar.setValue(pct)
        self._label.setText(f"Encoding… {pct}%")

    def _on_finished(self) -> None:
        self._thread.quit()
        self.accept()

    def _on_failed(self, message: str) -> None:
        self._thread.quit()
        self._label.setText(f"Failed: {message}")
        self._cancel_btn.setText("Close")
        self._cancel_btn.clicked.disconnect()
        self._cancel_btn.clicked.connect(self.reject)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._worker.cancel()
        self._thread.quit()
        self._thread.wait(3000)
        super().closeEvent(event)
