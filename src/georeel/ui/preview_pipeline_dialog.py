"""Progress dialog for preview video rendering via the server pipeline.

Chains the three server jobs (render_frames → compositor → video_assemble)
in a single background thread so preview and final render use identical
server-side code.  The only difference from the final render is that
settings contains ``render/frame_limit`` to cap the rendered frames.
"""

from __future__ import annotations

import os
import tempfile
import threading
from typing import Any, final, override

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


@final
class _Worker(QObject):
    progress = Signal(str, int)  # (message, pct 0-100)
    finished = Signal(str)       # output_path
    failed   = Signal(str)       # error message

    def __init__(
        self,
        client: ServerClient,
        workspace_id: str,
        blend_path: str,
        keyframes: list[dict[str, Any]],
        match_results: list[dict[str, Any]],
        settings: dict[str, Any],
        frame_limit: int,
        output_path: str,
        blender_exe: str | None,
    ) -> None:
        super().__init__()
        self._client        = client
        self._workspace_id  = workspace_id
        self._blend_path    = blend_path
        self._keyframes     = keyframes
        self._match_results = match_results
        self._settings      = settings
        self._frame_limit   = frame_limit
        self._output_path   = output_path
        self._blender_exe   = blender_exe
        self._cancel        = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def _cancelled(self) -> bool:
        return self._cancel.is_set()

    def run(self) -> None:
        try:
            self._do_run()
        except ServerError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"Unexpected error: {exc}")

    def _do_run(self) -> None:
        client = self._client

        # ── Stage 7: render frames ────────────────────────────────────── #
        self.progress.emit("Rendering frames…", 0)
        render_job_id = client.start_render_frames(
            workspace_id=self._workspace_id,
            blend_path=self._blend_path,
            keyframes=self._keyframes,
            settings=self._settings,
            blender_exe=self._blender_exe,
        )

        def _render_cb(pct: int, msg: str) -> None:
            self.progress.emit(msg or "Rendering frames…", pct // 3)

        client.poll_job(render_job_id, progress_cb=_render_cb,
                        cancel_check=self._cancelled)
        if self._cancelled():
            self.failed.emit("Cancelled.")
            return
        render_job = client.get_job(render_job_id)
        if render_job.get("status") != "done":
            self.failed.emit(render_job.get("error") or "Render failed")
            return

        # ── Stage 8: compositor ───────────────────────────────────────── #
        # Send only the rendered keyframes (first frame_limit of them) so
        # get_photo_frame_numbers sees a consistent keyframe list.
        self.progress.emit("Compositing photo overlays…", 33)
        comp_keyframes = self._keyframes[:self._frame_limit]
        comp_job_id = client.start_compositor(
            workspace_id=self._workspace_id,
            render_job_id=render_job_id,
            match_results=self._match_results,
            keyframes=comp_keyframes,
            settings=self._settings,
        )

        def _comp_cb(pct: int, msg: str) -> None:
            self.progress.emit(msg or "Compositing…", 33 + pct // 3)

        client.poll_job(comp_job_id, progress_cb=_comp_cb,
                        cancel_check=self._cancelled)
        if self._cancelled():
            self.failed.emit("Cancelled.")
            return
        comp_job = client.get_job(comp_job_id)
        if comp_job.get("status") != "done":
            self.failed.emit(comp_job.get("error") or "Compositor failed")
            return

        # ── Stage 9: video assembly ───────────────────────────────────── #
        self.progress.emit("Assembling video…", 66)
        video_job_id = client.start_video_assemble(
            workspace_id=self._workspace_id,
            source_job_id=comp_job_id,
            total_frames=self._frame_limit,
            settings=self._settings,
        )

        def _video_cb(pct: int, msg: str) -> None:
            self.progress.emit(msg or "Assembling video…", 66 + pct // 3)

        client.poll_job(video_job_id, progress_cb=_video_cb,
                        cancel_check=self._cancelled)
        if self._cancelled():
            self.failed.emit("Cancelled.")
            return
        video_job = client.get_job(video_job_id)
        if video_job.get("status") != "done":
            self.failed.emit(video_job.get("error") or "Video assembly failed")
            return

        client.download_video(video_job_id, self._output_path)
        self.progress.emit("Done.", 100)
        self.finished.emit(self._output_path)


@final
class PreviewPipelineDialog(QDialog):
    """Single dialog that runs all three preview pipeline stages via the server."""

    def __init__(
        self,
        client: ServerClient,
        workspace_id: str,
        blend_path: str,
        keyframes: list[dict[str, Any]],
        match_results: list[dict[str, Any]],
        settings: dict[str, Any],
        frame_limit: int,
        blender_exe: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Rendering preview video")
        self.setMinimumWidth(440)
        self.setModal(True)

        self._output_path: str | None = None

        from georeel.core import temp_manager
        base = temp_manager.get_base_dir()
        fd, tmp = tempfile.mkstemp(
            prefix="georeel_preview_", suffix=".mp4",
            dir=str(base) if base is not None else None,
        )
        os.close(fd)
        self._tmp_path = tmp

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        self._label = QLabel("Initialising…")
        layout.addWidget(self._label)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        layout.addWidget(self._bar)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self._on_cancel)
        layout.addWidget(buttons)
        self._cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)

        self._thread = QThread(self)
        self._worker = _Worker(
            client=client,
            workspace_id=workspace_id,
            blend_path=blend_path,
            keyframes=keyframes,
            match_results=match_results,
            settings=settings,
            frame_limit=frame_limit,
            output_path=self._tmp_path,
            blender_exe=blender_exe,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._thread.start()

    def output_path(self) -> str | None:
        return self._output_path

    def _on_cancel(self) -> None:
        self._worker.cancel()
        self._label.setText("Cancelling…")
        self._cancel_btn.setEnabled(False)

    def _on_progress(self, message: str, pct: int) -> None:
        self._bar.setValue(pct)
        self._label.setText(message)

    def _on_finished(self, path: str) -> None:
        self._output_path = path
        self._thread.quit()
        self.accept()

    def _on_failed(self, message: str) -> None:
        import logging
        logging.getLogger(__name__).error("Preview video failed: %s", message)
        self._thread.quit()
        self._label.setText(f"Failed: {message}")
        self._label.setWordWrap(True)
        self.resize(600, self.height())
        self._cancel_btn.setText("Close")
        self._cancel_btn.clicked.disconnect()
        self._cancel_btn.clicked.connect(self.reject)

    @override
    def closeEvent(self, arg__1: QCloseEvent) -> None:
        self._worker.cancel()
        self._thread.quit()
        self._thread.wait(3000)
        super().closeEvent(arg__1)
