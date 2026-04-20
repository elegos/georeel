from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from georeel.core.camera_path import CameraPathError, build_camera_path
from georeel.core.pipeline import Pipeline
from georeel.server.models.camera_keyframe import CameraKeyframeSchema
from georeel.server.models.elevation_grid import ElevationGridSchema
from georeel.server.models.match_result import MatchResultSchema
from georeel.server.models.trackpoint import TrackpointSchema

router = APIRouter(prefix="/camera", tags=["camera"])


class CameraKeyframesRequest(BaseModel):
    trackpoints: list[TrackpointSchema]
    elevation_grid: ElevationGridSchema
    match_results: list[MatchResultSchema] = []
    settings: dict[str, object] = {}


class CameraKeyframesResponse(BaseModel):
    keyframes: list[CameraKeyframeSchema]


@router.post("/keyframes", response_model=CameraKeyframesResponse)
async def keyframes(body: CameraKeyframesRequest) -> CameraKeyframesResponse:
    """Generate camera keyframes for the fly-through animation."""
    pipeline = Pipeline()
    pipeline.trackpoints = [tp.to_core() for tp in body.trackpoints]
    pipeline.elevation_grid = body.elevation_grid.to_core()
    pipeline.match_results = [mr.to_core() for mr in body.match_results]

    try:
        kfs = build_camera_path(pipeline, dict(body.settings))
    except CameraPathError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return CameraKeyframesResponse(
        keyframes=[CameraKeyframeSchema.from_core(kf) for kf in kfs]
    )
