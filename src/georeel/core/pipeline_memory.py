"""
Memory diagnostics for the GeoReel pipeline.

Call log_pipeline_memory(pipeline, label) at any point in the pipeline
to get a breakdown of what each object owns, alongside the process RSS.
"""

import logging
import sys

_log = logging.getLogger(__name__)


def _rss_mb() -> float:
    try:
        import psutil
        return psutil.Process().memory_info().rss / 1024 ** 2
    except Exception:
        try:
            import resource
            return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        except Exception:
            return float("nan")


def _fmt(mb: float) -> str:
    if mb >= 1024:
        return f"{mb / 1024:.2f} GB"
    return f"{mb:.1f} MB"


def log_pipeline_memory(pipeline: object, label: str = "") -> None:
    """Log the approximate RAM used by each major pipeline object.

    ``pipeline`` is a Pipeline instance but typed as object to avoid a
    circular import; we access attributes by name.
    """
    lines: list[str] = []

    # Trackpoints
    tps = getattr(pipeline, "trackpoints", None)
    if tps is not None:
        # rough estimate: each Trackpoint ~200 bytes
        tp_mb = sys.getsizeof(tps) / 1024 ** 2
        lines.append(f"  trackpoints   : {len(tps):,} pts  ~{_fmt(tp_mb)}")

    # Camera keyframes
    kfs = getattr(pipeline, "camera_keyframes", None)
    if kfs is not None:
        kf_mb = sys.getsizeof(kfs) / 1024 ** 2
        lines.append(f"  camera_kf     : {len(kfs):,} frames  ~{_fmt(kf_mb)}")

    # Elevation grid
    grid = getattr(pipeline, "elevation_grid", None)
    if grid is not None:
        data = getattr(grid, "data", None)
        if data is not None:
            grid_mb = data.nbytes / 1024 ** 2
            lines.append(
                f"  elevation_grid: {grid.rows}×{grid.cols}  {_fmt(grid_mb)}"
            )

    # Satellite texture
    sat = getattr(pipeline, "satellite_texture", None)
    if sat is not None:
        img = getattr(sat, "image", None)
        if img is not None:
            w, h = img.size
            bands = len(img.getbands())
            sat_mb = w * h * bands / 1024 ** 2
            lines.append(
                f"  satellite     : {w}×{h} px  {bands}-band  {_fmt(sat_mb)}"
            )
        else:
            src_zip = getattr(sat, "_source_zip", None)
            td = getattr(sat, "_tiles_dir", None)
            if src_zip is not None:
                lines.append(
                    f"  satellite     : lazy (not decoded, source: {src_zip.name})"
                )
            else:
                lines.append(
                    f"  satellite     : image freed (tiles on disk: {td})"
                )

    rss = _rss_mb()
    header = f"[memory] {label}  process RSS={_fmt(rss)}"
    if lines:
        _log.info("%s\n%s", header, "\n".join(lines))
    else:
        _log.info("%s  (pipeline empty)", header)
