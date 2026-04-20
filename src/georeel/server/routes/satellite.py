"""Satellite imagery fetch endpoint."""

import asyncio
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from georeel.core.satellite import SatelliteTexture, build_source
from georeel.server.jobs import get_registry, make_cancel_check, make_progress_cb
from georeel.server.models.bounding_box import BoundingBoxSchema
from georeel.server.workspace import get_manager

router = APIRouter(prefix="/satellite", tags=["satellite"])


@dataclass
class SatelliteJobResult:
    png_path: str
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float
    provider_id: str
    quality: str
    width: int
    height: int


class SatelliteFetchRequest(BaseModel):
    workspace_id: str
    bounding_box: BoundingBoxSchema
    provider_id: str = "esri_world"
    api_key: str = ""
    custom_url: str = ""
    quality: str = "standard"


class JobStarted(BaseModel):
    job_id: str


class SatelliteResultResponse(BaseModel):
    png_path: str
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float
    provider_id: str
    quality: str
    width: int
    height: int


@router.post("/fetch", response_model=JobStarted, status_code=202)
async def fetch(body: SatelliteFetchRequest) -> JobStarted:
    """Start an async satellite tile fetch and return its job_id immediately."""
    ws = get_manager().get(body.workspace_id)
    if ws is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    job = get_registry().create()
    get_manager().register_job(body.workspace_id, job.job_id)
    asyncio.create_task(_run(job.job_id, body, ws.directory))
    return JobStarted(job_id=job.job_id)


async def _run(job_id: str, body: SatelliteFetchRequest, ws_dir: Path) -> None:
    job = get_registry().get(job_id)
    if job is None:
        return
    job.status = "running"
    job.message = "Downloading satellite tiles…"
    progress_cb = make_progress_cb(job, "Satellite tile")
    cancel_check = make_cancel_check(job)

    def _blocking() -> SatelliteTexture:
        source = build_source(
            provider_id=body.provider_id,
            api_key=body.api_key,
            custom_url=body.custom_url,
            quality=body.quality,
        )
        return source.fetch(body.bounding_box.to_core(), progress_callback=progress_cb)

    try:
        texture = await asyncio.to_thread(_blocking)
        if cancel_check():
            job.status = "error"
            job.error = "Cancelled"
            return

        png_path = str(ws_dir / "satellite_texture.png")
        with open(png_path, "wb") as fh:
            texture.write_png(fh)

        job.result = SatelliteJobResult(
            png_path=png_path,
            min_lat=texture.min_lat,
            max_lat=texture.max_lat,
            min_lon=texture.min_lon,
            max_lon=texture.max_lon,
            provider_id=texture.provider_id,
            quality=texture.quality,
            width=texture.width,
            height=texture.height,
        )
        job.status = "done"
        job.progress = 100
        job.message = f"Done — {texture.width}×{texture.height} px"
    except Exception as exc:
        job.status = "error"
        job.error = str(exc)


def _get_result(job_id: str) -> SatelliteJobResult:
    job = get_registry().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "done":
        raise HTTPException(
            status_code=409, detail=f"Job not done yet (status: {job.status})"
        )
    result = job.result
    if not isinstance(result, SatelliteJobResult):
        raise HTTPException(status_code=500, detail="Unexpected result type")
    return result


@router.get("/{job_id}/result", response_model=SatelliteResultResponse)
async def get_result(job_id: str) -> SatelliteResultResponse:
    """Return satellite texture metadata once the fetch job is done."""
    r = _get_result(job_id)
    return SatelliteResultResponse(
        png_path=r.png_path,
        min_lat=r.min_lat,
        max_lat=r.max_lat,
        min_lon=r.min_lon,
        max_lon=r.max_lon,
        provider_id=r.provider_id,
        quality=r.quality,
        width=r.width,
        height=r.height,
    )


@router.get("/{job_id}/texture.png")
async def get_texture_png(job_id: str) -> FileResponse:
    """Download the stitched satellite texture as a PNG."""
    r = _get_result(job_id)
    png = Path(r.png_path)
    if not png.exists():
        raise HTTPException(status_code=404, detail="Texture PNG not found on disk")
    return FileResponse(str(png), media_type="image/png", filename="texture.png")
