# GeoReel

An open-source alternative to Relive-like services. Given a GPX track and photos, it generates a timelapse fly-through video of the track rendered on a 3D terrain with satellite imagery.

## Project Goals

- Input: GPX track file + geotagged photos
- Output: timelapse fly-through video of the track on 3D terrain with satellite imagery
- Prefer open data sources (OSM, Copernicus, SRTM, etc.) over commercial APIs
- Minimize custom code by leveraging existing open-source tools

## Photo Integration

Photos are placed as waypoints along the track. When the fly-through camera reaches a photo's position, the video cuts to a full-screen display of that photo before resuming the fly-through.

### Photo placement strategy (configurable, must support all three modes)

- **Timestamp**: match photo EXIF timestamp against GPX track timestamps to find the closest trackpoint
- **GPS coordinates**: match photo EXIF GPS coordinates against trackpoints by nearest geographic distance
- **Both**: use GPS coordinates as primary match, fall back to timestamp when GPS EXIF data is missing; warn if the two methods disagree beyond a configurable threshold

The placement mode is a user-facing option (e.g. `--photo-match timestamp|gps|both`). Default: `both`.

### Photo display behaviour

- Full-screen overlay, preserving aspect ratio with letterboxing/pillarboxing as needed
- Configurable display duration per photo (default: 3 seconds)
- Smooth transition in/out (fade or cut, configurable)
- Photos are sorted by their resolved track position, not by filename or input order

## Tech Stack

- **Language**: Python 3.14
- **Package manager**: `uv` (never use pip directly)
- **3D rendering**: prefer open-source tools (e.g., QGIS, Blender, CesiumJS, or similar)
- **Map/satellite data**: open datasets only (OSM tiles, Copernicus, SRTM/ASTER DEMs, etc.)

## Development Conventions

- Run scripts via `uv run <script>`
- Add dependencies with `uv add <package>`, never edit `pyproject.toml` dependencies by hand
- Entry point: `main.py`
- Keep pipeline stages modular: parsing → data fetching → 3D scene construction → rendering → video assembly

## Data Sources (preferred)

- **Elevation/DEM**: SRTM (NASA), ASTER, or Copernicus DEM (30m resolution)
- **Satellite imagery**: Copernicus Sentinel-2, NASA Earthdata, or WMTS open tile services
- **Base maps**: OpenStreetMap / WMTS endpoints

## Out of Scope

- No commercial map APIs (Google Maps, Mapbox, etc.) unless there is no open alternative
- No cloud-only services as hard dependencies
