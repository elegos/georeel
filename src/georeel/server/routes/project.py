"""Project save and load endpoints.

POST /project/save — accepts full project state as JSON; returns a .georeel ZIP download.
POST /project/load — accepts a .georeel ZIP upload; extracts it into a new workspace and
                     returns the project state as JSON.
"""

from __future__ import annotations

import asyncio
import base64
import io
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import Response
from PIL import Image
from pydantic import BaseModel

from georeel.core.elevation_grid import ElevationGrid
from georeel.core.photo_metadata import PhotoMetadata
from georeel.core.project import ProjectState, load_project, save_project
from georeel.core.satellite.texture import SatelliteTexture
from georeel.server.workspace import get_manager

router = APIRouter(prefix="/project", tags=["project"])


# ── Request / Response models ─────────────────────────────────────────────────

class ElevationGridData(BaseModel):
    rows: int
    cols: int
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float
    data_b64: str


class SatelliteData(BaseModel):
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float
    provider_id: str = ""
    quality: str = "standard"
    png_b64: str


class PhotoSaveData(BaseModel):
    photo_id: str
    timestamp: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class ProjectSaveRequest(BaseModel):
    workspace_id: str
    gpx_path: str | None = None
    match_mode: str = "both"
    output_path: str | None = None
    photos: list[PhotoSaveData] = []
    elevation_grid: ElevationGridData | None = None
    satellite: SatelliteData | None = None
    render_settings: dict[str, Any] | None = None
    clip_effects: dict[str, Any] | None = None
    locality_names: dict[str, Any] | None = None


class PhotoLoadData(BaseModel):
    photo_id: str
    path: str
    timestamp: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class ProjectLoadResponse(BaseModel):
    workspace_id: str
    gpx_path: str | None = None
    match_mode: str
    output_path: str | None = None
    photos: list[PhotoLoadData] = []
    elevation_grid: ElevationGridData | None = None
    satellite: SatelliteData | None = None
    render_settings: dict[str, Any] | None = None
    clip_effects: dict[str, Any] | None = None
    locality_names: dict[str, Any] | None = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/save")
async def save_project_endpoint(body: ProjectSaveRequest) -> Response:
    """Build a .georeel ZIP from the provided project state and return it."""
    ws = get_manager().get(body.workspace_id)
    if ws is None:
        raise HTTPException(status_code=404, detail="Workspace not found")

    # Resolve photos: look up each photo_id in the workspace.
    photos: list[PhotoMetadata] = []
    for pd in body.photos:
        entry = ws.photos.get(pd.photo_id)
        if entry is None:
            raise HTTPException(
                status_code=400,
                detail=f"Photo '{pd.photo_id}' not found in workspace",
            )
        ts = datetime.fromisoformat(pd.timestamp) if pd.timestamp else None
        photos.append(PhotoMetadata(
            path=entry.path,
            timestamp=ts,
            latitude=pd.latitude,
            longitude=pd.longitude,
        ))

    elevation_grid: ElevationGrid | None = None
    if body.elevation_grid is not None:
        gd = body.elevation_grid
        raw = base64.b64decode(gd.data_b64)
        data = np.frombuffer(raw, dtype=np.float32).reshape(gd.rows, gd.cols).copy()
        elevation_grid = ElevationGrid(
            data=data,
            min_lat=gd.min_lat,
            max_lat=gd.max_lat,
            min_lon=gd.min_lon,
            max_lon=gd.max_lon,
        )

    satellite_texture: SatelliteTexture | None = None
    if body.satellite is not None:
        sd = body.satellite
        png_bytes = base64.b64decode(sd.png_b64)
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        satellite_texture = SatelliteTexture(
            image=img,
            min_lat=sd.min_lat,
            max_lat=sd.max_lat,
            min_lon=sd.min_lon,
            max_lon=sd.max_lon,
            provider_id=sd.provider_id,
            quality=sd.quality,
        )

    state = ProjectState(
        gpx_path=body.gpx_path,
        match_mode=body.match_mode,
        output_path=body.output_path,
        photos=photos,
        elevation_grid=elevation_grid,
        satellite_texture=satellite_texture,
        render_settings=body.render_settings,
        clip_effects=body.clip_effects,
        locality_names=body.locality_names,
    )

    with tempfile.NamedTemporaryFile(suffix=".georeel", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        await asyncio.get_event_loop().run_in_executor(
            None, save_project, state, tmp_path
        )
        zip_bytes = Path(tmp_path).read_bytes()
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=\"project.georeel\""},
    )


@router.post("/load", response_model=ProjectLoadResponse)
async def load_project_endpoint(file: UploadFile) -> ProjectLoadResponse:
    """Upload a .georeel ZIP; extract it into a new workspace and return state JSON."""
    with tempfile.NamedTemporaryFile(suffix=".georeel", delete=False) as tmp:
        tmp_path = tmp.name
        tmp.write(await file.read())

    try:
        state = await asyncio.get_event_loop().run_in_executor(
            None, load_project, tmp_path
        )
    except Exception as exc:
        Path(tmp_path).unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=f"Failed to load project: {exc}")

    # NOTE: tmp_path is deleted *after* the satellite PNG is encoded below,
    # because SatelliteTexture loaded from a project ZIP holds a lazy reference
    # to the source ZIP file path — deleting it too early causes FileNotFoundError.

    # Copy extracted photos into a fresh workspace.
    ws = get_manager().create()
    photos_out: list[PhotoLoadData] = []
    for i, pm in enumerate(state.photos):
        src = Path(pm.path)
        ext = src.suffix or ".jpg"
        photo_id = f"{i:04d}"
        dest = ws.directory / "photos" / f"{photo_id}{ext}"
        if src.is_file():
            dest.write_bytes(src.read_bytes())
        photos_out.append(PhotoLoadData(
            photo_id=photo_id,
            path=str(dest),
            timestamp=pm.timestamp.isoformat() if pm.timestamp else None,
            latitude=pm.latitude,
            longitude=pm.longitude,
        ))

    elevation_grid_out: ElevationGridData | None = None
    if state.elevation_grid is not None:
        g = state.elevation_grid
        data_b64 = base64.b64encode(
            g.data.astype(np.float32).tobytes()
        ).decode()
        elevation_grid_out = ElevationGridData(
            rows=g.rows,
            cols=g.cols,
            min_lat=g.min_lat,
            max_lat=g.max_lat,
            min_lon=g.min_lon,
            max_lon=g.max_lon,
            data_b64=data_b64,
        )

    satellite_out: SatelliteData | None = None
    if state.satellite_texture is not None:
        t = state.satellite_texture
        buf = io.BytesIO()
        t.write_png(buf)
        png_b64 = base64.b64encode(buf.getvalue()).decode()
        satellite_out = SatelliteData(
            min_lat=t.min_lat,
            max_lat=t.max_lat,
            min_lon=t.min_lon,
            max_lon=t.max_lon,
            provider_id=t.provider_id,
            quality=t.quality,
            png_b64=png_b64,
        )

    # Safe to delete now — satellite PNG has been encoded into memory above.
    Path(tmp_path).unlink(missing_ok=True)

    return ProjectLoadResponse(
        workspace_id=ws.workspace_id,
        gpx_path=state.gpx_path,
        match_mode=state.match_mode,
        output_path=state.output_path,
        photos=photos_out,
        elevation_grid=elevation_grid_out,
        satellite=satellite_out,
        render_settings=state.render_settings,
        clip_effects=state.clip_effects,
        locality_names=state.locality_names,
    )
