import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel

from georeel.core.gpx_cleaner import CleanStats, REPAIR_NONE, _DEFAULT_MAX_GAP_S, _DEFAULT_MAX_JUMP_M, _DEFAULT_MAX_SPEED_MPS, detect_and_repair
from georeel.core.gpx_parser import GpxParseError, parse_gpx
from georeel.core.gpx_stats import compute_stats
from georeel.server.models.bounding_box import BoundingBoxSchema
from georeel.server.models.gpx_stats import GpxStatsSchema
from georeel.server.models.trackpoint import TrackpointSchema

router = APIRouter(prefix="/gpx", tags=["gpx"])


# ── Response / request models ──────────────────────────────────────────────────

class GpxParseResponse(BaseModel):
    trackpoints: list[TrackpointSchema]
    bounding_box: BoundingBoxSchema
    stats: GpxStatsSchema


class CleanStatsSchema(BaseModel):
    nullified_removed: int
    holes_filled: int
    street_fallbacks: int

    @classmethod
    def from_core(cls, s: CleanStats) -> "CleanStatsSchema":
        return cls(
            nullified_removed=s.nullified_removed,
            holes_filled=s.holes_filled,
            street_fallbacks=s.street_fallbacks,
        )


class GpxCleanRequest(BaseModel):
    trackpoints: list[TrackpointSchema]
    mode: str = "none"
    max_speed_mps: float = _DEFAULT_MAX_SPEED_MPS
    max_gap_s: float = _DEFAULT_MAX_GAP_S
    max_jump_m: float = _DEFAULT_MAX_JUMP_M
    osrm_profile: str = "driving"


class GpxCleanResponse(BaseModel):
    trackpoints: list[TrackpointSchema]
    stats: CleanStatsSchema


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/parse", response_model=GpxParseResponse)
async def parse(file: UploadFile) -> GpxParseResponse:
    """Upload a .gpx file and receive its trackpoints, bounding box, and stats."""
    raw = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".gpx", delete=False) as tmp:
        tmp.write(raw)
        tmp_path = tmp.name
    try:
        trackpoints, bbox = parse_gpx(tmp_path)
    except GpxParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    # Always sanitize: remove (0,0) nulls, speed outliers, and GPS spikes.
    # Hole filling (gap interpolation) is handled separately by /gpx/clean.
    trackpoints, _ = detect_and_repair(trackpoints, mode=REPAIR_NONE)

    stats = compute_stats(trackpoints)
    return GpxParseResponse(
        trackpoints=[TrackpointSchema.from_core(tp) for tp in trackpoints],
        bounding_box=BoundingBoxSchema.from_core(bbox),
        stats=GpxStatsSchema.from_core(stats),
    )


@router.post("/clean", response_model=GpxCleanResponse)
async def clean(body: GpxCleanRequest) -> GpxCleanResponse:
    """Remove bad points and optionally fill gaps in a trackpoint list."""
    core_points = [tp.to_core() for tp in body.trackpoints]
    cleaned, stats = detect_and_repair(
        core_points,
        mode=body.mode,
        max_speed_mps=body.max_speed_mps,
        max_gap_s=body.max_gap_s,
        max_jump_m=body.max_jump_m,
        osrm_profile=body.osrm_profile,
    )
    return GpxCleanResponse(
        trackpoints=[TrackpointSchema.from_core(tp) for tp in cleaned],
        stats=CleanStatsSchema.from_core(stats),
    )
