"""Progress dialog for photo overlay compositing via the georeel-server job API."""

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
    progress = Signal(int, int)  # (pct, 100)
    finished = Signal(str)       # composited_frames_dir
    failed = Signal(str)

    def __init__(self, client: ServerClient, job_id: str) -> None:
        super().__init__()
        self._client = client
        self._job_id = job_id
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
            job = self._client.get_job(self._job_id)
            out_dir = job.get("result_path") or ""
            if not out_dir:
                self.failed.emit("Server did not return a composited frames directory")
                return
            self.finished.emit(str(out_dir))
        except ServerError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"Unexpected error: {exc}")


class CompositorProgressDialog(QDialog):
    """Shows photo compositing progress with a cancel button."""

    def __init__(
        self,
        client: ServerClient,
        job_id: str,
        parent: QWidget | None = None,
    ) -> None:
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
        self._bar.setRange(0, 100)
        layout.addWidget(self._bar)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self._cancel)
        layout.addWidget(buttons)
        self._cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)

        self._thread = QThread(self)
        self._worker = _Worker(client, job_id)
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

    def _cancel(self) -> None:
        self._worker.cancel()
        self._label.setText("Cancelling…")
        self._cancel_btn.setEnabled(False)

    def _on_progress(self, pct: int, _total: int) -> None:
        self._bar.setValue(pct)
        self._label.setText(f"Compositing… {pct}%")

    def _on_finished(self, out_dir: str) -> None:
        self._out_dir = out_dir
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
