"""DEM (elevation) fetch endpoint."""

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from georeel.core.dem_fetcher import fetch_dem
from georeel.core.elevation_grid import ElevationGrid
from georeel.server.jobs import get_registry, make_cancel_check, make_progress_cb
from georeel.server.models.bounding_box import BoundingBoxSchema
from georeel.server.models.elevation_grid import ElevationGridSchema
from georeel.server.workspace import get_manager

router = APIRouter(prefix="/dem", tags=["dem"])


class DemFetchRequest(BaseModel):
    workspace_id: str
    bounding_box: BoundingBoxSchema


class JobStarted(BaseModel):
    job_id: str


@router.post("/fetch", response_model=JobStarted, status_code=202)
async def fetch(body: DemFetchRequest) -> JobStarted:
    """Start an async DEM fetch job and return its job_id immediately."""
    if get_manager().get(body.workspace_id) is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    job = get_registry().create()
    get_manager().register_job(body.workspace_id, job.job_id)
    asyncio.create_task(_run(job.job_id, body))
    return JobStarted(job_id=job.job_id)


async def _run(job_id: str, body: DemFetchRequest) -> None:
    job = get_registry().get(job_id)
    if job is None:
        return
    job.status = "running"
    job.message = "Downloading SRTM elevation tiles…"
    progress_cb = make_progress_cb(job, "DEM tile")
    cancel_check = make_cancel_check(job)

    def _blocking() -> ElevationGrid:
        return fetch_dem(body.bounding_box.to_core(), progress_callback=progress_cb)

    try:
        grid = await asyncio.to_thread(_blocking)
        if cancel_check():
            job.status = "error"
            job.error = "Cancelled"
            return
        job.result = grid
        job.status = "done"
        job.progress = 100
        job.message = f"Done — {grid.rows}×{grid.cols} elevation grid"
    except Exception as exc:
        job.status = "error"
        job.error = str(exc)


@router.get("/{job_id}/result", response_model=ElevationGridSchema)
async def get_result(job_id: str) -> ElevationGridSchema:
    """Return the serialised ElevationGrid once the fetch job is done."""
    job = get_registry().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "done":
        raise HTTPException(
            status_code=409,
            detail=f"Job not done yet (status: {job.status})",
        )
    grid = job.result
    if not isinstance(grid, ElevationGrid):
        raise HTTPException(status_code=500, detail="Unexpected result type")
    return ElevationGridSchema.from_core(grid)
