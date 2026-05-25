"""Satellite imagery fetch endpoint."""

import asyncio
import logging
import traceback
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger(__name__)

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
    texture: SatelliteTexture   # kept alive so tile_cache stays on disk
    texture_path: str           # on-disk TIFF composite (written during fetch)
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
    progress_cb = make_progress_cb(job, "Satellite tile", min_pct=1, max_pct=95)
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

        texture_path = str(ws_dir / "satellite_texture.tif")
        job.message = "Compositing satellite texture…"

        def _composite_progress(done: int, total: int) -> None:
            job.progress = 95 + int((done / total) * 4)  # 95–99 %
            job.message = f"Compositing {done}/{total} tiles"

        def _save_tiff() -> tuple[int, int]:
            import numpy as np
            import tifffile
            from georeel.core.bounding_box import BoundingBox
            from georeel.core.pil_lock import PIL_LOCK
            assert texture.tile_cache is not None
            bbox = BoundingBox(
                texture.min_lat, texture.max_lat,
                texture.min_lon, texture.max_lon,
            )
            img = texture.tile_cache.composite(bbox, progress_callback=_composite_progress)
            with PIL_LOCK:
                if img.mode != "RGB":
                    img = img.convert("RGB")
                w, h = img.size
                # np.asarray shares PIL's internal buffer (zero-copy for RGB images).
                # tifffile writes pure-Python TIFF (no libtiff) with per-tile DEFLATE,
                # avoiding the libtiff 32-bit strip-size overflow on very large images.
                arr = np.asarray(img)
                tifffile.imwrite(
                    texture_path,
                    arr,
                    bigtiff=True,
                    compression="deflate",
                    tile=(256, 256),
                    photometric="rgb",
                )
                return w, h

        saved_w, saved_h = await asyncio.to_thread(_save_tiff)

        job.result = SatelliteJobResult(
            texture=texture,
            texture_path=texture_path,
            width=saved_w,
            height=saved_h,
        )
        job.status = "done"
        job.progress = 100
        job.message = f"Done — {saved_w}×{saved_h} px"
    except Exception as exc:
        tb = traceback.format_exc()
        _log.error("[satellite] job %s failed:\n%s", job_id, tb)
        job.status = "error"
        job.error = f"{exc}\n\n{tb}"


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
    t = r.texture
    return SatelliteResultResponse(
        min_lat=t.min_lat,
        max_lat=t.max_lat,
        min_lon=t.min_lon,
        max_lon=t.max_lon,
        provider_id=t.provider_id,
        quality=t.quality,
        width=r.width,
        height=r.height,
    )


@router.get("/{job_id}/texture.png")
async def get_texture_png(job_id: str) -> FileResponse:
    """Download the stitched satellite texture as a TIFF."""
    r = _get_result(job_id)
    path = Path(r.texture_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Texture file not found on disk")
    return FileResponse(str(path), media_type="image/tiff", filename="texture.tif")
