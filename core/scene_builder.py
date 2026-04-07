import json
import shlex
import subprocess
import tempfile
from pathlib import Path

from .blender_runtime import find_blender
from .elevation_grid import ElevationGrid
from .sun_position import sun_angles, sun_direction_vector
from .pipeline import Pipeline
from .satellite import SatelliteTexture

_BLENDER_SCRIPT = Path(__file__).parent / "blender_scripts" / "build_scene.py"
_TIMEOUT_SECONDS = 300   # 5 minutes


class SceneBuildError(Exception):
    pass


def build_scene(pipeline: Pipeline, blender_exe: str | None = None) -> str:
    """Build a 3D terrain .blend from the pipeline's elevation grid and satellite texture.

    *blender_exe* overrides auto-detection (pass the value from QSettings).

    Returns the absolute path to the saved .blend file.
    The file lives in a temporary directory that persists for the OS session;
    the texture is packed inside the .blend so the directory can safely be
    discarded once stage 7 (rendering) is complete.
    """
    if pipeline.elevation_grid is None:
        raise SceneBuildError("Elevation grid is required (run DEM fetcher first).")
    if pipeline.satellite_texture is None:
        raise SceneBuildError("Satellite texture is required (run satellite fetcher first).")

    exe = find_blender(blender_exe)
    if exe is None:
        raise SceneBuildError(
            "Blender executable not found. "
            "Install Blender or download it via Options → Blender…"
        )

    work_dir = Path(tempfile.mkdtemp(prefix="georeel_scene_"))
    meta_path, data_path = _write_dem(pipeline.elevation_grid, work_dir)
    tex_path = _write_texture(pipeline.satellite_texture, work_dir)
    blend_path = work_dir / "scene.blend"

    cmd = [
        exe,
        "--background",
        "--python", str(_BLENDER_SCRIPT),
        "--",
        str(meta_path),
        str(data_path),
        str(tex_path),
        str(blend_path),
    ] + _sun_args(pipeline)

    try:
        result = subprocess.run(
            shlex.join(cmd),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            shell=True,
        )
    except subprocess.TimeoutExpired:
        raise SceneBuildError(
            f"Blender timed out after {_TIMEOUT_SECONDS // 60} minutes."
        )

    if result.returncode != 0 or not blend_path.is_file():
        tail = (result.stderr or result.stdout or "")[-2000:]
        raise SceneBuildError(
            f"Blender scene build failed (exit {result.returncode}).\n{tail}"
        )

    return str(blend_path)


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------

def _sun_args(pipeline: "Pipeline") -> list[str]:
    """Return [sun_x, sun_y, sun_z] strings if a timestamp is available, else []."""
    ts = next(
        (tp.timestamp for tp in pipeline.trackpoints if tp.timestamp is not None),
        None,
    )
    if ts is None or pipeline.bounding_box is None:
        return []
    bb = pipeline.bounding_box
    lat = (bb.min_lat + bb.max_lat) / 2
    lon = (bb.min_lon + bb.max_lon) / 2
    az, el = sun_angles(lat, lon, ts)
    sx, sy, sz = sun_direction_vector(az, el)
    return [str(sx), str(sy), str(sz)]


def _write_dem(grid: ElevationGrid, work_dir: Path) -> tuple[Path, Path]:
    meta = {
        "rows": grid.rows,
        "cols": grid.cols,
        "min_lat": grid.min_lat,
        "max_lat": grid.max_lat,
        "min_lon": grid.min_lon,
        "max_lon": grid.max_lon,
    }
    meta_path = work_dir / "dem_meta.json"
    data_path = work_dir / "dem_data.bin"
    meta_path.write_text(json.dumps(meta))
    data_path.write_bytes(grid.to_bytes())
    return meta_path, data_path


def _write_texture(texture: SatelliteTexture, work_dir: Path) -> Path:
    tex_path = work_dir / "satellite.png"
    tex_path.write_bytes(texture.to_png_bytes())
    return tex_path
