import json
import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .elevation_grid import ElevationGrid
from .photo_metadata import PhotoMetadata
from .satellite import SatelliteTexture
from . import temp_manager

# ------------------------------------------------------------------
# ZIP entry paths (v2 format)
# ------------------------------------------------------------------
_MANIFEST          = "manifest.json"
_PROJECT           = "project.json"
_DEM_BIN           = "dem/data.bin"              # raw float32; metadata in project.json
_SAT_TEXTURE       = "satellite/texture.png"     # RGB PNG; metadata in project.json
_LOCALITY_TIMELINE = "locality/timeline.json"    # pre-computed Nominatim entries
_GPX_ENTRY         = "gpx/track.gpx"             # embedded GPX track
_PHOTOS_DIR        = "photos/"                   # embedded photos: photos/0000.jpg, etc.
_FONT_ENTRY        = "font/title"                # embedded font; extension appended at save time
_MUSIC_DIR         = "music/"                    # embedded music files (original filenames preserved)

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
    render_settings: dict[str, Any] | None = None   # camera + imagery settings at fetch time
    clip_effects: dict[str, Any] | None = None      # fade-in/out, title, music settings
    locality_names: dict[str, Any] | None = None    # locality names overlay settings
    locality_timeline: list[dict[str, Any]] | None = None  # cached Nominatim timeline
    # Temporary directory created when embedded files are extracted on load.
    # The caller is responsible for deleting it (shutil.rmtree) when done.
    temp_dir: Path | None = field(default=None, compare=False, repr=False)


# ------------------------------------------------------------------
# Save (always v2 ZIP)
# ------------------------------------------------------------------

def autosave_tilde(
    state: ProjectState,
    path: str,
    *,
    update_dem: bool = False,
    update_sat: bool = False,
) -> None:
    """Atomically build / update *path~* with the latest DEM and/or satellite data.

    Unlike the old append-mode approach, this always produces a clean ZIP with
    no shadow / duplicate entries:

    1. Choose a **base** ZIP to copy unchanged entries from:
       - *path~* if it already exists (preserves any data written by a
         previous autosave call, e.g. a freshly fetched satellite, while now
         also updating the DEM).
       - *path* otherwise (the last explicit save).
       - If neither exists (project never saved), fall back to a full
         ``save_project`` call.
    2. Write a new *path~.tmp*: copy all entries from the base except those
       being replaced, then write the new versions.
    3. Atomically rename *path~.tmp* → *path~*.

    The large satellite texture (ZIP_STORED) is streamed entry-by-entry
    without decoding when it is not being updated, keeping I/O overhead at a
    raw-copy level.  When it IS being updated the new texture is written via
    ``SatelliteTexture.write_png``, which itself streams from its source ZIP
    if the image is lazy-loaded — and because we write to a *different* file
    (*tmp*) the source ZIP is never truncated mid-read.
    """
    tilde = path + "~"
    tmp   = tilde + ".tmp"

    tilde_exists = Path(tilde).is_file()
    path_exists  = Path(path).is_file()

    # Choose the base: prefer the existing tilde so incremental data (e.g. a
    # freshly fetched satellite written by a previous call) is not lost.
    if tilde_exists:
        base = tilde
    elif path_exists:
        base = path
    else:
        # First-ever save: no existing project to derive from.
        save_project(state, tilde)
        return

    with zipfile.ZipFile(base, "r") as src_zf:
        # Build the updated project.json by patching the base payload.
        try:
            payload: dict[str, Any] = json.loads(src_zf.read(_PROJECT))
        except KeyError:
            payload = {}

        if state.elevation_grid is not None:
            g = state.elevation_grid
            payload["dem"] = {
                "rows": g.rows, "cols": g.cols,
                "min_lat": g.min_lat, "max_lat": g.max_lat,
                "min_lon": g.min_lon, "max_lon": g.max_lon,
            }
        elif update_dem:
            payload.pop("dem", None)

        if state.satellite_texture is not None:
            t = state.satellite_texture
            payload["satellite"] = {
                "min_lat": t.min_lat, "max_lat": t.max_lat,
                "min_lon": t.min_lon, "max_lon": t.max_lon,
                "provider_id": t.provider_id, "quality": t.quality,
            }
        elif update_sat:
            payload.pop("satellite", None)

        if state.render_settings is not None:
            payload["render_settings"] = state.render_settings

        manifest = {
            "version": _FORMAT_VERSION,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        }

        # Entries we are replacing — always replace the JSON metadata.
        skip: set[str] = {_MANIFEST, _PROJECT}
        if update_dem:
            skip.add(_DEM_BIN)
        if update_sat:
            skip.add(_SAT_TEXTURE)

        try:
            with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as dst_zf:
                # Copy every unchanged entry from the base ZIP.
                for info in src_zf.infolist():
                    if info.filename not in skip:
                        _copy_zip_entry(src_zf, dst_zf, info)

                # Write the updated entries.
                dst_zf.writestr(_MANIFEST, json.dumps(manifest, indent=2))
                dst_zf.writestr(_PROJECT,  json.dumps(payload,  indent=2))

                if update_dem and state.elevation_grid is not None:
                    dst_zf.writestr(_DEM_BIN, state.elevation_grid.to_bytes())
                if update_sat and state.satellite_texture is not None:
                    _write_sat_png(dst_zf, state.satellite_texture)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise

    Path(tmp).replace(tilde)


def save_project(state: ProjectState, path: str) -> None:
    """Save a project to *path* atomically.

    Writes to a temporary file in the same directory first, then:
      1. Renames the existing *path* (if any) to *path~* — instant on the same
         filesystem, gives a recovery copy without duplicating data on disk.
      2. Renames the temporary file to *path*.

    This matters especially for the lazy-loaded satellite texture: when a
    project is loaded, ``state.satellite_texture._source_zip`` points at
    *path* itself.  If we opened *path* for writing directly we would truncate
    the source while still trying to stream from it, producing a 0-byte
    ``satellite/texture.png`` in the saved ZIP.  Writing to a distinct temp
    file avoids this, because the original *path* stays intact until the rename.
    """
    # Serialise photos first; the list is mutated below to add embedded names.
    serialised_photos = [_serialise_photo(p) for p in state.photos]

    project_payload: dict[str, Any] = {
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
        # Strip runtime-only path keys — files are re-embedded below.
        _runtime_keys = {"clip_effects/title_font_path", "clip_effects/music_paths"}
        safe_ce = {k: v for k, v in state.clip_effects.items()
                   if k not in _runtime_keys}
        project_payload["clip_effects"] = safe_ce

    if state.locality_names is not None:
        project_payload["locality_names"] = state.locality_names

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

    # Write to a sibling temp file so the original (which may be the satellite
    # texture's _source_zip) stays intact during the entire write.
    tmp_path = path + ".tmp"
    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(_MANIFEST, json.dumps(manifest, indent=2))

            # ── Embed GPX ────────────────────────────────────────────────
            if state.gpx_path and Path(state.gpx_path).is_file():
                zf.write(state.gpx_path, _GPX_ENTRY)
                project_payload["gpx_embedded"] = True

            # ── Embed photos ─────────────────────────────────────────────
            _seen_photo_names: set[str] = set()
            for photo, ser in zip(state.photos, serialised_photos):
                if photo.path and Path(photo.path).is_file():
                    p = Path(photo.path)
                    stem, ext = p.stem, p.suffix.lower() or ".jpg"
                    name = f"{stem}{ext}"
                    if name in _seen_photo_names:
                        counter = 1
                        while f"{stem}_{counter}{ext}" in _seen_photo_names:
                            counter += 1
                        name = f"{stem}_{counter}{ext}"
                    _seen_photo_names.add(name)
                    entry = f"{_PHOTOS_DIR}{name}"
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

            # ── Embed music ──────────────────────────────────────────────
            ce = state.clip_effects or {}
            if ce.get("clip_effects/music_enabled"):
                paths_raw = ce.get("clip_effects/music_paths", "[]")
                try:
                    music_paths: list[str] = (
                        json.loads(paths_raw) if isinstance(paths_raw, str) else list(paths_raw)
                    )
                except (ValueError, TypeError):
                    music_paths = []
                _seen_music_names: set[str] = set()
                embedded_music_entries: list[str] = []
                for mpath in music_paths:
                    if mpath and Path(mpath).is_file():
                        p = Path(mpath)
                        stem, ext = p.stem, p.suffix.lower() or ".mp3"
                        name = f"{stem}{ext}"
                        if name in _seen_music_names:
                            counter = 1
                            while f"{stem}_{counter}{ext}" in _seen_music_names:
                                counter += 1
                            name = f"{stem}_{counter}{ext}"
                        _seen_music_names.add(name)
                        entry = f"{_MUSIC_DIR}{name}"
                        zf.write(mpath, entry)
                        embedded_music_entries.append(entry)
                if embedded_music_entries:
                    project_payload["music_embedded"] = embedded_music_entries

            # project.json written last so it captures all embedded flags above.
            zf.writestr(_PROJECT, json.dumps(project_payload, indent=2))

            if state.elevation_grid is not None:
                zf.writestr(_DEM_BIN, state.elevation_grid.to_bytes())
            if state.satellite_texture is not None:
                _write_sat_png(zf, state.satellite_texture)
            if state.locality_timeline is not None:
                zf.writestr(_LOCALITY_TIMELINE, json.dumps(state.locality_timeline))

    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise

    # Atomic placement: tmp → path.
    # The original file stays intact throughout the write (above), so any
    # lazy satellite texture that references path as its source ZIP was read
    # from the still-intact file.  The tilde (if any, built by autosave_tilde)
    # is left for _on_save_complete to remove after this call succeeds.
    Path(tmp_path).replace(path)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _copy_zip_entry(
    src_zf: zipfile.ZipFile,
    dst_zf: zipfile.ZipFile,
    info: zipfile.ZipInfo,
) -> None:
    """Stream one ZIP entry from *src_zf* into *dst_zf*, preserving its compression type.

    For ZIP_STORED entries (e.g. the satellite PNG, which is already a
    compressed format) this is a raw byte copy with no codec overhead.
    For ZIP_DEFLATED entries the decompressed bytes are re-deflated, which
    is fast for the small JSON / DEM entries that use deflate.
    """
    out_info = zipfile.ZipInfo(info.filename)
    out_info.compress_type = info.compress_type
    with src_zf.open(info) as src_f, dst_zf.open(out_info, "w", force_zip64=True) as dst_f:
        shutil.copyfileobj(src_f, dst_f, 1 << 20)  # 1 MiB chunks


def _write_sat_png(zf: zipfile.ZipFile, texture: SatelliteTexture) -> None:
    """Stream the satellite texture PNG directly into the ZIP archive.

    Uses ZIP_STORED so the PNG (already compressed) is not re-deflated —
    this halves the in-memory overhead compared to writestr() with ZIP_DEFLATED,
    and avoids materialising the entire compressed image as a Python bytes object.
    """
    info = zipfile.ZipInfo(_SAT_TEXTURE)
    info.compress_type = zipfile.ZIP_STORED
    with zf.open(info, "w", force_zip64=True) as f:
        texture.write_png(f)


# ------------------------------------------------------------------
# Load (v2 ZIP)
# ------------------------------------------------------------------

def load_project(path: str) -> ProjectState:
    with zipfile.ZipFile(path, "r") as zf:
        return _load_v2(zf, Path(path))


def _load_v2(zf: zipfile.ZipFile, zip_path: Path) -> ProjectState:
    payload  = json.loads(zf.read(_PROJECT))
    namelist = set(zf.namelist())
    temp_dir: Path | None = None

    def _tmpdir() -> Path:
        nonlocal temp_dir
        if temp_dir is None:
            temp_dir = temp_manager.make_temp_dir("georeel_proj_")
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

    # ── Extract music ────────────────────────────────────────────────
    music_embedded = payload.get("music_embedded")
    if music_embedded:
        if isinstance(music_embedded, bool):
            # Legacy v2 format: single file stored at "music/audio<ext>".
            old_entries = [n for n in namelist if n.startswith("music/audio")]
            entries_to_load = old_entries[:1]
        else:
            # Current format: list of zip entries with original filenames.
            entries_to_load = [e for e in music_embedded if e in namelist]
        restored_paths: list[str] = []
        for entry in entries_to_load:
            dest = _tmpdir() / entry
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(entry))
            restored_paths.append(str(dest))
        if restored_paths:
            clip_effects["clip_effects/music_paths"] = json.dumps(restored_paths)

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
        # Lazy reference — do NOT decode the PNG at load time.  The pixels are
        # only needed if the user re-saves without fetching a new texture.
        satellite_texture = SatelliteTexture.from_zip_lazy(
            zip_path=zip_path,
            entry=_SAT_TEXTURE,
            min_lat=sat_meta["min_lat"],
            max_lat=sat_meta["max_lat"],
            min_lon=sat_meta["min_lon"],
            max_lon=sat_meta["max_lon"],
            provider_id=sat_meta.get("provider_id", ""),
            quality=sat_meta.get("quality", "standard"),
        )
        if satellite_texture._dim_width is None:
            # The PNG header was unreadable (0-byte or corrupted).
            # Discard it so the pipeline safely falls back to re-downloading it.
            satellite_texture = None

    locality_timeline: list[dict[str, Any]] | None = None
    if _LOCALITY_TIMELINE in namelist:
        try:
            locality_timeline = json.loads(zf.read(_LOCALITY_TIMELINE))
        except Exception:
            locality_timeline = None

    return ProjectState(
        gpx_path=gpx_path,
        match_mode=payload.get("match_mode", "timestamp"),
        output_path=payload.get("output_path"),
        photos=_deserialise_photos(raw_photos),
        elevation_grid=elevation_grid,
        satellite_texture=satellite_texture,
        render_settings=payload.get("render_settings"),
        clip_effects=clip_effects or None,
        locality_names=payload.get("locality_names"),
        locality_timeline=locality_timeline,
        temp_dir=temp_dir,
    )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _serialise_photo(p: PhotoMetadata) -> dict[str, Any]:
    return {
        "path": p.path,
        "timestamp": p.timestamp.isoformat() if p.timestamp else None,
        "latitude": p.latitude,
        "longitude": p.longitude,
    }


def _deserialise_photos(raw: list[dict[str, Any]]) -> list[PhotoMetadata]:
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


def _should_embed_font(clip_effects: dict[str, Any] | None) -> bool:
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
