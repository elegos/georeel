"""Job management endpoints: status polling and SSE progress stream."""

import asyncio
import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from georeel.server.jobs import JobStatus, get_registry

router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    progress: int
    message: str
    error: str | None
    result_path: str | None = None  # populated when status=="done" and result is a path


@router.get("/{job_id}", response_model=JobStatusResponse)
async def get_job(job_id: str) -> JobStatusResponse:
    """Return current status and progress of a job."""
    job = get_registry().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    result_path = str(job.result) if isinstance(job.result, str) else None
    return JobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        progress=job.progress,
        message=job.message,
        error=job.error,
        result_path=result_path,
    )


@router.delete("/{job_id}", status_code=204)
async def cancel_job(job_id: str) -> None:
    """Cancel a running job at its next cancellation checkpoint."""
    job = get_registry().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    job.cancel()


@router.get("/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    """SSE stream: yields a JSON event each time progress or status changes."""

    async def _generate() -> AsyncGenerator[str, None]:
        last_progress = -1
        last_status = ""
        while True:
            job = get_registry().get(job_id)
            if job is None:
                yield f"data: {json.dumps({'error': 'job not found'})}\n\n"
                break
            if job.progress != last_progress or job.status != last_status:
                last_progress = job.progress
                last_status = job.status
                payload: dict[str, object] = {
                    "job_id": job.job_id,
                    "status": job.status,
                    "progress": job.progress,
                    "message": job.message,
                }
                if job.error:
                    payload["error"] = job.error
                yield f"data: {json.dumps(payload)}\n\n"
            if job.status in ("done", "error"):
                break
            await asyncio.sleep(0.2)

    return StreamingResponse(_generate(), media_type="text/event-stream")
