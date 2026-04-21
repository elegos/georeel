"""Photo overlay compositor endpoint."""

import asyncio
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from georeel.core import temp_manager
from georeel.core.photo_compositor import composite_photos
from georeel.core.pipeline import Pipeline
from georeel.core.video_assembler import composite_locality_frames
from georeel.server.jobs import get_registry, make_cancel_check, make_progress_cb
from georeel.server.models.camera_keyframe import CameraKeyframeSchema
from georeel.server.models.match_result import MatchResultSchema
from georeel.server.workspace import get_manager

router = APIRouter(prefix="/compositor", tags=["compositor"])


class CompositorRunRequest(BaseModel):
    workspace_id: str
    render_job_id: str
    match_results: list[MatchResultSchema]
    keyframes: list[CameraKeyframeSchema]
    settings: dict[str, object] = {}


class JobStarted(BaseModel):
    job_id: str


@router.post("/run", response_model=JobStarted, status_code=202)
async def run(body: CompositorRunRequest) -> JobStarted:
    """Start an async photo compositor job and return its job_id immediately."""
    if get_manager().get(body.workspace_id) is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    render_job = get_registry().get(body.render_job_id)
    if render_job is None:
        raise HTTPException(status_code=404, detail="Render job not found")
    if render_job.status != "done":
        raise HTTPException(
            status_code=409,
            detail=f"Render job not done (status: {render_job.status})",
        )
    job = get_registry().create()
    get_manager().register_job(body.workspace_id, job.job_id)
    asyncio.create_task(_run(job.job_id, body))
    return JobStarted(job_id=job.job_id)


async def _run(job_id: str, body: CompositorRunRequest) -> None:
    job = get_registry().get(job_id)
    if job is None:
        return
    job.status = "running"
    job.message = "Compositing photo overlays…"

    render_job = get_registry().get(body.render_job_id)
    if render_job is None:
        job.status = "error"
        job.error = "Render job no longer available"
        return

    frames_dir = render_job.result
    if not isinstance(frames_dir, str):
        job.status = "error"
        job.error = "Render job result has unexpected type"
        return

    progress_cb = make_progress_cb(job, "Frame", min_pct=1, max_pct=99)
    cancel_check = make_cancel_check(job)

    def _blocking() -> str:
        settings = dict(body.settings)
        fps_raw = settings.get("render/fps", 30)
        fps = int(fps_raw) if isinstance(fps_raw, (int, float, str)) else 30
        src_dir = frames_dir
        locality_temp: Path | None = None
        try:
            if (bool(settings.get("locality_names/enabled", False))
                    and settings.get("locality_names/timeline_json")):
                locality_temp = temp_manager.make_temp_dir("georeel_locality_")
                composite_locality_frames(src_dir, locality_temp, settings, fps)
                src_dir = str(locality_temp)

            pipeline = Pipeline()
            pipeline.rendered_frames_dir = src_dir
            pipeline.match_results = [mr.to_core() for mr in body.match_results]
            pipeline.camera_keyframes = [kf.to_core() for kf in body.keyframes]
            return composite_photos(
                pipeline,
                settings=settings,
                progress_cb=progress_cb,
                cancel_check=cancel_check,
            )
        finally:
            if locality_temp is not None:
                shutil.rmtree(locality_temp, ignore_errors=True)

    try:
        comp_dir = await asyncio.to_thread(_blocking)
        if cancel_check():
            job.status = "error"
            job.error = "Cancelled"
            return
        job.result = comp_dir
        job.cleanup_path = Path(comp_dir)
        job.status = "done"
        job.progress = 100
        job.message = "Done — compositor finished"
    except Exception as exc:
        job.status = "error"
        job.error = str(exc)
