"""3D scene build endpoint (wraps build_scene + Blender)."""

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from PIL import Image

from georeel.core.pipeline import Pipeline
from georeel.core.satellite import SatelliteTexture
from georeel.core.scene_builder import SceneBuildError, build_scene
from georeel.core.elevation_grid import ElevationGrid
from georeel.server.jobs import get_registry, make_cancel_check, make_progress_cb
from georeel.server.models.match_result import MatchResultSchema
from georeel.server.models.trackpoint import TrackpointSchema
from georeel.server.routes.satellite import SatelliteJobResult
from georeel.server.workspace import get_manager

router = APIRouter(prefix="/scene", tags=["scene"])


class SceneBuildRequest(BaseModel):
    workspace_id: str
    dem_job_id: str
    satellite_job_id: str
    trackpoints: list[TrackpointSchema]
    match_results: list[MatchResultSchema] = []
    settings: dict[str, object] = {}
    blender_exe: str | None = None


class JobStarted(BaseModel):
    job_id: str


@router.post("/build", response_model=JobStarted, status_code=202)
async def build(body: SceneBuildRequest) -> JobStarted:
    """Start an async scene build job and return its job_id immediately."""
    if get_manager().get(body.workspace_id) is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    dem_job = get_registry().get(body.dem_job_id)
    sat_job = get_registry().get(body.satellite_job_id)
    if dem_job is None:
        raise HTTPException(status_code=404, detail="DEM job not found")
    if sat_job is None:
        raise HTTPException(status_code=404, detail="Satellite job not found")
    if dem_job.status != "done":
        raise HTTPException(
            status_code=409,
            detail=f"DEM job not done (status: {dem_job.status})",
        )
    if sat_job.status != "done":
        raise HTTPException(
            status_code=409,
            detail=f"Satellite job not done (status: {sat_job.status})",
        )

    job = get_registry().create()
    get_manager().register_job(body.workspace_id, job.job_id)
    asyncio.create_task(_run(job.job_id, body))
    return JobStarted(job_id=job.job_id)


async def _run(job_id: str, body: SceneBuildRequest) -> None:
    job = get_registry().get(job_id)
    if job is None:
        return
    job.status = "running"
    job.message = "Building 3D scene…"

    dem_job = get_registry().get(body.dem_job_id)
    sat_job = get_registry().get(body.satellite_job_id)
    if dem_job is None or sat_job is None:
        job.status = "error"
        job.error = "Upstream job no longer available"
        return

    grid = dem_job.result
    sat_result = sat_job.result
    if not isinstance(grid, ElevationGrid):
        job.status = "error"
        job.error = "DEM job result is not an ElevationGrid"
        return
    if not isinstance(sat_result, SatelliteJobResult):
        job.status = "error"
        job.error = "Satellite job result has unexpected type"
        return

    progress_cb = make_progress_cb(job, "Scene tile", min_pct=1, max_pct=90)
    cancel_check = make_cancel_check(job)

    def _blocking() -> str:
        # Rebuild SatelliteTexture from the saved PNG.
        img = Image.open(sat_result.png_path).convert("RGB")
        texture = SatelliteTexture(
            image=img,
            min_lat=sat_result.min_lat,
            max_lat=sat_result.max_lat,
            min_lon=sat_result.min_lon,
            max_lon=sat_result.max_lon,
            provider_id=sat_result.provider_id,
            quality=sat_result.quality,
        )
        pipeline = Pipeline()
        pipeline.trackpoints = [tp.to_core() for tp in body.trackpoints]
        pipeline.match_results = [mr.to_core() for mr in body.match_results]
        pipeline.elevation_grid = grid
        pipeline.satellite_texture = texture

        return build_scene(
            pipeline,
            blender_exe=body.blender_exe,
            settings=dict(body.settings),
            tile_progress_cb=progress_cb,
            cancel_check=cancel_check,
        )

    try:
        blend_path = await asyncio.to_thread(_blocking)
        if cancel_check():
            job.status = "error"
            job.error = "Cancelled"
            return
        job.result = blend_path
        job.cleanup_path = Path(blend_path).parent
        job.status = "done"
        job.progress = 100
        job.message = f"Done — {Path(blend_path).name}"
    except SceneBuildError as exc:
        job.status = "error"
        job.error = str(exc)
    except Exception as exc:
        job.status = "error"
        job.error = f"Unexpected error: {exc}"


@router.get("/{job_id}/download")
async def download_blend(job_id: str) -> FileResponse:
    """Download the built .blend file."""
    job = get_registry().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "done":
        raise HTTPException(
            status_code=409, detail=f"Job not done yet (status: {job.status})"
        )
    blend_path = job.result
    if not isinstance(blend_path, str) or not Path(blend_path).exists():
        raise HTTPException(status_code=404, detail=".blend file not found on disk")
    return FileResponse(
        blend_path,
        media_type="application/octet-stream",
        filename=Path(blend_path).name,
    )
