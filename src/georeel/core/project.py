import json
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

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
_GPX_ENTRY    = "gpx/track.gpx"          # embedded GPX track
_PHOTOS_DIR   = "photos/"                # embedded photos: photos/0000.jpg, etc.
_FONT_ENTRY   = "font/title"             # embedded font; extension appended at save time

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
    render_settings: dict | None = None   # camera + imagery settings at fetch time
    clip_effects: dict | None = None      # fade-in/out, title, music settings
    # Temporary directory created when embedded files are extracted on load.
    # The caller is responsible for deleting it (shutil.rmtree) when done.
    temp_dir: Path | None = field(default=None, compare=False, repr=False)


# ------------------------------------------------------------------
# Save (always v2 ZIP)
# ------------------------------------------------------------------

def save_project(state: ProjectState, path: str) -> None:
    # Serialise photos first; the list is mutated below to add embedded names.
    serialised_photos = [_serialise_photo(p) for p in state.photos]

    project_payload: dict = {
        "gpx_path": state.gpx_path,
        "match_mode": state.match_mode,
        "output_path": state.output_path,
        "photos": serialised_photos,
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

    if state.render_settings is not None:
        project_payload["render_settings"] = state.render_settings

    if state.clip_effects is not None:
        # Strip any runtime-only font_path key — the font file is re-embedded below.
        safe_ce = {k: v for k, v in state.clip_effects.items()
                   if k != "clip_effects/title_font_path"}
        project_payload["clip_effects"] = safe_ce

    if state.satellite_texture is not None:
        t = state.satellite_texture
        project_payload["satellite"] = {
            "min_lat": t.min_lat, "max_lat": t.max_lat,
            "min_lon": t.min_lon, "max_lon": t.max_lon,
            "provider_id": t.provider_id,
            "quality": t.quality,
        }

    manifest = {
        "version": _FORMAT_VERSION,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(_MANIFEST, json.dumps(manifest, indent=2))

        # ── Embed GPX ────────────────────────────────────────────────
        if state.gpx_path and Path(state.gpx_path).is_file():
            zf.write(state.gpx_path, _GPX_ENTRY)
            project_payload["gpx_embedded"] = True

        # ── Embed photos ─────────────────────────────────────────────
        for i, (photo, ser) in enumerate(zip(state.photos, serialised_photos)):
            if photo.path and Path(photo.path).is_file():
                ext = Path(photo.path).suffix.lower() or ".jpg"
                entry = f"{_PHOTOS_DIR}{i:04d}{ext}"
                zf.write(photo.path, entry)
                ser["embedded"] = entry   # mutates serialised_photos[i]

        # ── Embed title font ─────────────────────────────────────────
        if _should_embed_font(state.clip_effects):
            font_name = (state.clip_effects or {}).get(
                "clip_effects/title_font", "Noto Serif"
            )
            font_file = _resolve_fontfile(font_name)
            if font_file:
                ext = Path(font_file).suffix.lower() or ".ttf"
                zf.write(font_file, f"{_FONT_ENTRY}{ext}")
                project_payload["font_embedded"] = True

        # project.json written last so it captures all embedded flags above.
        zf.writestr(_PROJECT, json.dumps(project_payload, indent=2))

        if state.elevation_grid is not None:
            zf.writestr(_DEM_BIN, state.elevation_grid.to_bytes())
        if state.satellite_texture is not None:
            zf.writestr(_SAT_TEXTURE, state.satellite_texture.to_png_bytes())


# ------------------------------------------------------------------
# Load (v2 ZIP)
# ------------------------------------------------------------------

def load_project(path: str) -> ProjectState:
    with zipfile.ZipFile(path, "r") as zf:
        return _load_v2(zf)


def _load_v2(zf: zipfile.ZipFile) -> ProjectState:
    payload  = json.loads(zf.read(_PROJECT))
    namelist = set(zf.namelist())
    temp_dir: Path | None = None

    def _tmpdir() -> Path:
        nonlocal temp_dir
        if temp_dir is None:
            temp_dir = Path(tempfile.mkdtemp(prefix="georeel_proj_"))
        return temp_dir

    # ── Extract GPX ──────────────────────────────────────────────────
    gpx_path = payload.get("gpx_path")
    if payload.get("gpx_embedded") and _GPX_ENTRY in namelist:
        dest = _tmpdir() / _GPX_ENTRY
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(zf.read(_GPX_ENTRY))
        gpx_path = str(dest)

    # ── Extract photos ───────────────────────────────────────────────
    raw_photos = payload.get("photos", [])
    for p in raw_photos:
        entry = p.get("embedded")
        if entry and entry in namelist:
            dest = _tmpdir() / entry
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(entry))
            p["path"] = str(dest)

    # ── Extract font ─────────────────────────────────────────────────
    clip_effects = payload.get("clip_effects") or {}
    if payload.get("font_embedded"):
        font_entries = [n for n in namelist if n.startswith(_FONT_ENTRY)]
        if font_entries:
            entry = font_entries[0]
            dest = _tmpdir() / entry
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(entry))
            clip_effects["clip_effects/title_font_path"] = str(dest)

    # ── DEM & satellite (unchanged) ──────────────────────────────────
    elevation_grid = None
    dem_meta = payload.get("dem")
    if dem_meta and _DEM_BIN in namelist:
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
    if sat_meta and _SAT_TEXTURE in namelist:
        satellite_texture = SatelliteTexture.from_png_bytes(
            zf.read(_SAT_TEXTURE),
            min_lat=sat_meta["min_lat"],
            max_lat=sat_meta["max_lat"],
            min_lon=sat_meta["min_lon"],
            max_lon=sat_meta["max_lon"],
            provider_id=sat_meta.get("provider_id", ""),
            quality=sat_meta.get("quality", "standard"),
        )

    return ProjectState(
        gpx_path=gpx_path,
        match_mode=payload.get("match_mode", "timestamp"),
        output_path=payload.get("output_path"),
        photos=_deserialise_photos(raw_photos),
        elevation_grid=elevation_grid,
        satellite_texture=satellite_texture,
        render_settings=payload.get("render_settings"),
        clip_effects=clip_effects or None,
        temp_dir=temp_dir,
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


def _should_embed_font(clip_effects: dict | None) -> bool:
    if not clip_effects:
        return False
    return bool(clip_effects.get("clip_effects/title_enabled")) and bool(
        clip_effects.get("clip_effects/title_text", "").strip()
    )


def _resolve_fontfile(font_name: str) -> str | None:
    """Return the absolute font file path for *font_name* via fc-match, or None."""
    try:
        r = subprocess.run(
            ["fc-match", "--format=%{file}", font_name],
            capture_output=True, text=True, timeout=5,
        )
        p = r.stdout.strip()
        return p if p and Path(p).is_file() else None
    except Exception:
        return None
