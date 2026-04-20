"""Video assembly endpoint."""

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from georeel.core.video_assembler import assemble_video
from georeel.server.jobs import get_registry, make_cancel_check, make_progress_cb
from georeel.server.workspace import get_manager

router = APIRouter(prefix="/video", tags=["video"])


class VideoAssembleRequest(BaseModel):
    workspace_id: str
    source_job_id: str   # compositor or render job id
    output_filename: str = "output.mp4"
    total_frames: int
    gpx_path: str | None = None
    settings: dict[str, object] = {}


class JobStarted(BaseModel):
    job_id: str


@router.post("/assemble", response_model=JobStarted, status_code=202)
async def assemble(body: VideoAssembleRequest) -> JobStarted:
    """Start an async video assembly job and return its job_id immediately."""
    ws = get_manager().get(body.workspace_id)
    if ws is None:
        raise HTTPException(status_code=404, detail="Workspace not found")

    source_job = get_registry().get(body.source_job_id)
    if source_job is None:
        raise HTTPException(status_code=404, detail="Source job not found")
    if source_job.status != "done":
        raise HTTPException(
            status_code=409,
            detail=f"Source job not done (status: {source_job.status})",
        )

    job = get_registry().create()
    get_manager().register_job(body.workspace_id, job.job_id)
    asyncio.create_task(_run(job.job_id, body, ws.directory))
    return JobStarted(job_id=job.job_id)


async def _run(job_id: str, body: VideoAssembleRequest, ws_dir: Path) -> None:
    job = get_registry().get(job_id)
    if job is None:
        return
    job.status = "running"
    job.message = "Assembling video…"

    source_job = get_registry().get(body.source_job_id)
    if source_job is None:
        job.status = "error"
        job.error = "Source job no longer available"
        return

    frames_dir = source_job.result
    if not isinstance(frames_dir, str):
        job.status = "error"
        job.error = "Source job result has unexpected type"
        return

    output_path = str(ws_dir / body.output_filename)
    progress_cb = make_progress_cb(job, "Frame", min_pct=1, max_pct=99)
    cancel_check = make_cancel_check(job)

    def _blocking() -> None:
        assemble_video(
            frames_dir=frames_dir,
            output_path=output_path,
            settings=dict(body.settings),
            total_frames=body.total_frames,
            gpx_path=body.gpx_path,
            progress_cb=progress_cb,
            cancel_check=cancel_check,
        )

    try:
        await asyncio.to_thread(_blocking)
        if cancel_check():
            job.status = "error"
            job.error = "Cancelled"
            return
        job.result = output_path
        job.status = "done"
        job.progress = 100
        job.message = f"Done — {body.output_filename}"
    except Exception as exc:
        job.status = "error"
        job.error = str(exc)


@router.get("/{job_id}/download")
async def download(job_id: str) -> FileResponse:
    """Download the assembled video file and delete it from disk afterwards."""
    job = get_registry().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "done":
        raise HTTPException(
            status_code=409, detail=f"Job not done yet (status: {job.status})"
        )
    video_path = job.result
    if not isinstance(video_path, str) or not Path(video_path).exists():
        raise HTTPException(status_code=404, detail="Video file not found on disk")
    filename = Path(video_path).name
    return FileResponse(
        video_path,
        media_type="video/mp4",
        filename=filename,
        background=BackgroundTask(Path(video_path).unlink, missing_ok=True),
    )
