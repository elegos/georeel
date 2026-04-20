import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel

from georeel.core.exif_reader import read_photo_metadata
from georeel.core.photo_matcher import match_photos
from georeel.server.models.match_result import MatchResultSchema
from georeel.server.models.photo_metadata import PhotoMetadataSchema
from georeel.server.models.trackpoint import TrackpointSchema
from georeel.server.workspace import PhotoEntry, get_manager

router = APIRouter(prefix="/photos", tags=["photos"])


# ── Response / request models ──────────────────────────────────────────────────

class PhotosUploadResponse(BaseModel):
    photos: list[PhotoMetadataSchema]


class PhotosMatchRequest(BaseModel):
    workspace_id: str
    photo_ids: list[str]
    trackpoints: list[TrackpointSchema]
    mode: str = "both"
    tz_offset_hours: float = 0.0


class PhotosMatchResponse(BaseModel):
    match_results: list[MatchResultSchema]


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/upload", response_model=PhotosUploadResponse, status_code=201)
async def upload_photos(
    workspace_id: str,
    files: list[UploadFile],
) -> PhotosUploadResponse:
    """Upload photos into a workspace; reads EXIF metadata server-side."""
    ws = get_manager().get(workspace_id)
    if ws is None:
        raise HTTPException(status_code=404, detail="Workspace not found")

    results: list[PhotoMetadataSchema] = []
    photos_dir = ws.directory / "photos"

    for upload in files:
        photo_id = str(uuid.uuid4())
        suffix = Path(upload.filename or "photo.jpg").suffix or ".jpg"
        dest = photos_dir / f"{photo_id}{suffix}"
        dest.write_bytes(await upload.read())

        metadata = read_photo_metadata(str(dest))
        entry = PhotoEntry(
            photo_id=photo_id,
            path=str(dest),
            metadata=metadata,
        )
        ws.photos[photo_id] = entry
        results.append(
            PhotoMetadataSchema(
                photo_id=photo_id,
                path=str(dest),
                timestamp=metadata.timestamp,
                latitude=metadata.latitude,
                longitude=metadata.longitude,
            )
        )

    return PhotosUploadResponse(photos=results)


@router.post("/match", response_model=PhotosMatchResponse)
async def match(body: PhotosMatchRequest) -> PhotosMatchResponse:
    """Match uploaded photos to trackpoints using the given strategy."""
    ws = get_manager().get(body.workspace_id)
    if ws is None:
        raise HTTPException(status_code=404, detail="Workspace not found")

    missing = [pid for pid in body.photo_ids if pid not in ws.photos]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown photo_ids: {missing}",
        )

    photo_metadatas = [ws.photos[pid].metadata for pid in body.photo_ids]
    core_trackpoints = [tp.to_core() for tp in body.trackpoints]

    results = match_photos(
        photo_metadatas,
        core_trackpoints,
        mode=body.mode,
        tz_offset_hours=body.tz_offset_hours,
    )
    return PhotosMatchResponse(
        match_results=[MatchResultSchema.from_core(r) for r in results]
    )
