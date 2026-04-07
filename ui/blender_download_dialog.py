import threading

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QProgressBar,
    QVBoxLayout,
)

from core.blender_runtime import BlenderDownloadError, BlenderVersion, download_blender


class _Worker(QObject):
    progress = Signal(int, int)   # downloaded, total
    finished = Signal(str)        # executable path
    failed = Signal(str)          # error message

    def __init__(self, version: BlenderVersion):
        super().__init__()
        self._version = version
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    def run(self):
        try:
            path = download_blender(
                self._version,
                progress_cb=lambda dl, tot: self.progress.emit(dl, tot),
                cancel_check=self._cancel.is_set,
            )
            self.finished.emit(path)
        except BlenderDownloadError as e:
            self.failed.emit(str(e))
        except Exception as e:
            self.failed.emit(f"Unexpected error: {e}")


class BlenderDownloadDialog(QDialog):
    """Shows download progress and allows cancellation."""

    def __init__(self, version: BlenderVersion, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Downloading Blender {version.label}")
        self.setMinimumWidth(420)
        self.setModal(True)

        self._executable: str | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        self._label = QLabel(f"Downloading Blender {version.label}…")
        layout.addWidget(self._label)

        self._bar = QProgressBar()
        self._bar.setRange(0, 0)   # indeterminate until we have Content-Length
        layout.addWidget(self._bar)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.rejected.connect(self._cancel)
        layout.addWidget(buttons)
        self._cancel_btn = buttons.button(QDialogButtonBox.Cancel)

        # Worker + thread
        self._thread = QThread(self)
        self._worker = _Worker(version)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)

        self._thread.start()

    # ------------------------------------------------------------------

    def executable(self) -> str | None:
        return self._executable

    # ------------------------------------------------------------------

    def _cancel(self):
        self._worker.cancel()
        self._label.setText("Cancelling…")
        self._cancel_btn.setEnabled(False)

    def _on_progress(self, downloaded: int, total: int):
        if total > 0:
            self._bar.setRange(0, total)
            self._bar.setValue(downloaded)
            mb = downloaded / 1_048_576
            total_mb = total / 1_048_576
            self._label.setText(f"Downloading… {mb:.1f} / {total_mb:.1f} MB")
        else:
            mb = downloaded / 1_048_576
            self._label.setText(f"Downloading… {mb:.1f} MB")

    def _on_finished(self, path: str):
        self._executable = path
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
