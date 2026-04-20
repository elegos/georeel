"""Progress dialog for 3D scene building via the georeel-server job API.

The caller must start the scene-build job *before* opening the dialog and pass
the resulting ``job_id``.  The worker thread polls the server every ~300 ms
and updates the label / progress bar.  On completion ``blend_path()`` returns
the server-side ``.blend`` file path (valid since server is co-located).
"""

import logging

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

_log = logging.getLogger(__name__)


class _Worker(QObject):
    status = Signal(str)
    tile_progress = Signal(int, int)  # (pct, 100) reused for bar range
    finished = Signal(str)           # blend_path on success
    failed = Signal(str)             # error message

    def __init__(self, client: ServerClient, job_id: str) -> None:
        super().__init__()
        self._client = client
        self._job_id = job_id
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        def _progress(pct: int, msg: str) -> None:
            self.status.emit(msg or "Building scene…")
            self.tile_progress.emit(pct, 100)

        try:
            self._client.poll_job(
                self._job_id,
                progress_cb=_progress,
                cancel_check=lambda: self._cancelled,
            )
            job = self._client.get_job(self._job_id)
            blend_path = job.get("result_path") or ""
            if not blend_path:
                self.failed.emit("Server did not return a blend path")
                return
            self.finished.emit(str(blend_path))
        except ServerError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"Unexpected error: {exc}")


class SceneBuildDialog(QDialog):
    """Shows progress while the server builds the 3D scene."""

    def __init__(
        self,
        client: ServerClient,
        job_id: str,
        parent: QWidget | None = None,
    ) -> None:
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
        self._bar.setRange(0, 0)
        layout.addWidget(self._bar)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self._on_cancel)
        layout.addWidget(buttons)
        self._cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)

        self._thread = QThread(self)
        self._worker = _Worker(client, job_id)
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

    def _on_cancel(self) -> None:
        self._worker.cancel()
        self._label.setText("Cancelling…")
        self._cancel_btn.setEnabled(False)

    def _on_status(self, msg: str) -> None:
        self._label.setText(msg)

    def _on_tile_progress(self, pct: int, total: int) -> None:
        self._bar.setRange(0, total)
        self._bar.setValue(pct)

    def _on_finished(self, path: str) -> None:
        self._blend_path = path
        self._thread.quit()
        self.accept()

    def _on_failed(self, message: str) -> None:
        _log.error("Scene build failed: %s", message)
        self._thread.quit()
        self._label.setText(f"Failed: {message}")
        self._label.setWordWrap(True)
        self.resize(600, self.height())
        self._cancel_btn.setText("Close")
        self._cancel_btn.clicked.disconnect()
        self._cancel_btn.clicked.connect(self.reject)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._worker.cancel()
        self._thread.quit()
        self._thread.wait(3000)
        super().closeEvent(event)
