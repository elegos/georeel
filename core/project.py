import json
import zipfile
from dataclasses import dataclass
from datetime import datetime

from .elevation_grid import ElevationGrid
from .photo_metadata import PhotoMetadata
from .satellite import SatelliteTexture

# ------------------------------------------------------------------
# ZIP entry paths (v2 format)
# ------------------------------------------------------------------
_MANIFEST     = "manifest.json"
_PROJECT      = "project.json"
_DEM_BIN      = "dem/data.bin"           # raw float32; metadata in project.json
_SAT_TEXTURE  = "satellite/texture.png"  # RGB PNG; metadata in project.json

_FORMAT_VERSION = 2


# ------------------------------------------------------------------
# Public data container
# ------------------------------------------------------------------

@dataclass
class ProjectState:
    gpx_path: str | None
    match_mode: str
    output_path: str | None
    photos: list[PhotoMetadata]
    elevation_grid: ElevationGrid | None = None
    satellite_texture: SatelliteTexture | None = None


# ------------------------------------------------------------------
# Save (always v2 ZIP)
# ------------------------------------------------------------------

def save_project(state: ProjectState, path: str) -> None:
    project_payload: dict = {
        "gpx_path": state.gpx_path,
        "match_mode": state.match_mode,
        "output_path": state.output_path,
        "photos": [_serialise_photo(p) for p in state.photos],
    }

    if state.elevation_grid is not None:
        g = state.elevation_grid
        project_payload["dem"] = {
            "rows": g.rows,
            "cols": g.cols,
            "min_lat": g.min_lat,
            "max_lat": g.max_lat,
            "min_lon": g.min_lon,
            "max_lon": g.max_lon,
        }

    if state.satellite_texture is not None:
        t = state.satellite_texture
        project_payload["satellite"] = {
            "min_lat": t.min_lat, "max_lat": t.max_lat,
            "min_lon": t.min_lon, "max_lon": t.max_lon,
        }

    manifest = {
        "version": _FORMAT_VERSION,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(_MANIFEST, json.dumps(manifest, indent=2))
        zf.writestr(_PROJECT,  json.dumps(project_payload, indent=2))
        if state.elevation_grid is not None:
            zf.writestr(_DEM_BIN, state.elevation_grid.to_bytes())
        if state.satellite_texture is not None:
            zf.writestr(_SAT_TEXTURE, state.satellite_texture.to_png_bytes())


# ------------------------------------------------------------------
# Load (auto-detects v1 JSON or v2 ZIP)
# ------------------------------------------------------------------

def load_project(path: str) -> ProjectState:
    with zipfile.ZipFile(path, "r") as zf:
        return _load_v2(zf)


def _load_v2(zf: zipfile.ZipFile) -> ProjectState:
    payload = json.loads(zf.read(_PROJECT))

    elevation_grid = None
    dem_meta = payload.get("dem")
    if dem_meta and _DEM_BIN in zf.namelist():
        elevation_grid = ElevationGrid.from_bytes(
            zf.read(_DEM_BIN),
            rows=dem_meta["rows"],
            cols=dem_meta["cols"],
            min_lat=dem_meta["min_lat"],
            max_lat=dem_meta["max_lat"],
            min_lon=dem_meta["min_lon"],
            max_lon=dem_meta["max_lon"],
        )

    satellite_texture = None
    sat_meta = payload.get("satellite")
    if sat_meta and _SAT_TEXTURE in zf.namelist():
        satellite_texture = SatelliteTexture.from_png_bytes(
            zf.read(_SAT_TEXTURE),
            min_lat=sat_meta["min_lat"],
            max_lat=sat_meta["max_lat"],
            min_lon=sat_meta["min_lon"],
            max_lon=sat_meta["max_lon"],
        )

    return ProjectState(
        gpx_path=payload.get("gpx_path"),
        match_mode=payload.get("match_mode", "timestamp"),
        output_path=payload.get("output_path"),
        photos=_deserialise_photos(payload.get("photos", [])),
        elevation_grid=elevation_grid,
        satellite_texture=satellite_texture,
    )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _serialise_photo(p: PhotoMetadata) -> dict:
    return {
        "path": p.path,
        "timestamp": p.timestamp.isoformat() if p.timestamp else None,
        "latitude": p.latitude,
        "longitude": p.longitude,
    }


def _deserialise_photos(raw: list[dict]) -> list[PhotoMetadata]:
    photos = []
    for p in raw:
        ts_raw = p.get("timestamp")
        photos.append(PhotoMetadata(
            path=p["path"],
            timestamp=datetime.fromisoformat(ts_raw) if ts_raw else None,
            latitude=p.get("latitude"),
            longitude=p.get("longitude"),
        ))
    return photos
