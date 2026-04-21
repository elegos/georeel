"""Frame rendering endpoint (wraps render_frames + Blender)."""

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from georeel.core.frame_renderer import render_frames
from georeel.core.pipeline import Pipeline
from georeel.server.jobs import get_registry, make_cancel_check, make_progress_cb
from georeel.server.models.camera_keyframe import CameraKeyframeSchema
from georeel.server.workspace import get_manager

router = APIRouter(prefix="/render", tags=["render"])


class RenderFramesRequest(BaseModel):
    workspace_id: str
    scene_job_id: str
    keyframes: list[CameraKeyframeSchema]
    settings: dict[str, object] = {}
    blender_exe: str | None = None


class JobStarted(BaseModel):
    job_id: str


@router.post("/frames", response_model=JobStarted, status_code=202)
async def frames(body: RenderFramesRequest) -> JobStarted:
    """Start an async frame render job and return its job_id immediately."""
    if get_manager().get(body.workspace_id) is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    scene_job = get_registry().get(body.scene_job_id)
    if scene_job is None:
        raise HTTPException(status_code=404, detail="Scene job not found")
    if scene_job.status != "done":
        raise HTTPException(
            status_code=409,
            detail=f"Scene job not done (status: {scene_job.status})",
        )
    job = get_registry().create()
    get_manager().register_job(body.workspace_id, job.job_id)
    asyncio.create_task(_run(job.job_id, body))
    return JobStarted(job_id=job.job_id)


async def _run(job_id: str, body: RenderFramesRequest) -> None:
    job = get_registry().get(job_id)
    if job is None:
        return
    job.status = "running"
    job.message = "Rendering frames…"

    scene_job = get_registry().get(body.scene_job_id)
    if scene_job is None:
        job.status = "error"
        job.error = "Scene job no longer available"
        return

    blend_path = scene_job.result
    if not isinstance(blend_path, str):
        job.status = "error"
        job.error = "Scene job result has unexpected type"
        return

    total = len(body.keyframes)
    progress_cb = make_progress_cb(job, "Frame", min_pct=1, max_pct=99)
    cancel_check = make_cancel_check(job)

    def _blocking() -> str:
        pipeline = Pipeline()
        pipeline.scene = blend_path
        pipeline.camera_keyframes = [kf.to_core() for kf in body.keyframes]
        return render_frames(
            pipeline,
            settings=dict(body.settings),
            blender_exe=body.blender_exe,
            progress_cb=progress_cb,
            cancel_check=cancel_check,
        )

    try:
        frames_dir = await asyncio.to_thread(_blocking)
        if cancel_check():
            job.status = "error"
            job.error = "Cancelled"
            return
        job.result = frames_dir
        job.cleanup_path = Path(frames_dir)
        job.status = "done"
        job.progress = 100
        job.message = f"Done — {total} frames rendered"
    except Exception as exc:
        job.status = "error"
        job.error = str(exc)
