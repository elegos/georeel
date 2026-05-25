"""Typed synchronous HTTP client for the GeoReel REST API.

Used by GUI workers and dialogs that run in QThreads.  All methods block until
the server responds; callers are responsible for running them off the main
Qt thread (or using the poll_job helper which processes Qt events via a
caller-supplied callback).
"""

from __future__ import annotations

import base64
import io
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, final

import httpx
import numpy as np
from PIL import Image

from georeel.core.bounding_box import BoundingBox
from georeel.core.gpx_cleaner import _DEFAULT_MAX_GAP_S, _DEFAULT_MAX_JUMP_M, _DEFAULT_MAX_SPEED_MPS
from georeel.core.camera_keyframe import CameraKeyframe
from georeel.core.elevation_grid import ElevationGrid
from georeel.core.match_result import MatchResult
from georeel.core.satellite import SatelliteTexture
from georeel.core.trackpoint import Trackpoint


class ServerError(RuntimeError):
    """Raised on HTTP error status or server-side job failure."""


@final
class ServerClient:
    """Synchronous httpx wrapper for georeel-server REST endpoints."""

    def __init__(self, base_url: str, timeout: float = 120.0) -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self._base, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ServerClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ── Health ─────────────────────────────────────────────────────────

    def is_alive(self) -> bool:
        """Return True when the server health endpoint is reachable."""
        try:
            r = self._client.get("/api/v1/health")
            return r.status_code == 200
        except Exception:
            return False

    # ── Workspace ──────────────────────────────────────────────────────

    def create_workspace(self) -> str:
        return str(self._post("/api/v1/workspaces")["workspace_id"])

    def delete_workspace(self, workspace_id: str) -> None:
        self._client.delete(f"/api/v1/workspaces/{workspace_id}")

    # ── GPX ────────────────────────────────────────────────────────────

    def parse_gpx(
        self, gpx_path: str
    ) -> tuple[list[Trackpoint], BoundingBox]:
        with open(gpx_path, "rb") as fh:
            data = self._raw(
                "POST",
                "/api/v1/gpx/parse",
                files={"file": (Path(gpx_path).name, fh, "application/octet-stream")},
            )
        trackpoints = [_trackpoint_from_dict(d) for d in data["trackpoints"]]  # type: ignore[arg-type]
        bbox = _bbox_from_dict(data["bounding_box"])  # type: ignore[arg-type]
        return trackpoints, bbox

    def clean_gpx(
        self,
        trackpoints: list[Trackpoint],
        mode: str,
        max_speed_mps: float = _DEFAULT_MAX_SPEED_MPS,
        max_gap_s: float = _DEFAULT_MAX_GAP_S,
        max_jump_m: float = _DEFAULT_MAX_JUMP_M,
        osrm_profile: str = "driving",
    ) -> tuple[list[Trackpoint], dict[str, Any]]:
        data = self._post(
            "/api/v1/gpx/clean",
            json={
                "trackpoints": [trackpoint_to_dict(tp) for tp in trackpoints],
                "mode": mode,
                "max_speed_mps": max_speed_mps,
                "max_gap_s": max_gap_s,
                "max_jump_m": max_jump_m,
                "osrm_profile": osrm_profile,
            },
        )
        cleaned = [_trackpoint_from_dict(d) for d in data["trackpoints"]]  # type: ignore[arg-type]
        return cleaned, data["stats"]  # type: ignore[return-value]

    # ── Photos ─────────────────────────────────────────────────────────

    def upload_photos(
        self, workspace_id: str, photo_paths: list[str]
    ) -> list[dict[str, Any]]:
        """Upload photos to workspace; returns list of {photo_id, path, ...}."""
        handles = []
        try:
            files = []
            for p in photo_paths:
                fh = open(p, "rb")  # noqa: SIM115
                handles.append(fh)
                files.append(("files", (Path(p).name, fh, "image/jpeg")))
            data = self._raw(
                "POST",
                "/api/v1/photos/upload",
                params={"workspace_id": workspace_id},
                files=files,
            )
        finally:
            for fh in handles:
                fh.close()
        return data["photos"]  # type: ignore[return-value]

    def match_photos(
        self,
        workspace_id: str,
        photo_ids: list[str],
        trackpoints: list[dict[str, Any]],
        mode: str,
        tz_offset_hours: float = 0.0,
    ) -> list[dict[str, Any]]:
        data = self._post(
            "/api/v1/photos/match",
            json={
                "workspace_id": workspace_id,
                "photo_ids": photo_ids,
                "trackpoints": trackpoints,
                "mode": mode,
                "tz_offset_hours": tz_offset_hours,
            },
        )
        return data["match_results"]  # type: ignore[return-value]

    # ── Camera ─────────────────────────────────────────────────────────

    def build_camera_keyframes(
        self,
        trackpoints: list[dict[str, Any]],
        elevation_grid: dict[str, Any],
        match_results: list[dict[str, Any]],
        settings: dict[str, Any],
    ) -> list[CameraKeyframe]:
        data = self._post(
            "/api/v1/camera/keyframes",
            json={
                "trackpoints": trackpoints,
                "elevation_grid": elevation_grid,
                "match_results": match_results,
                "settings": settings,
            },
        )
        return [_keyframe_from_dict(kf) for kf in data["keyframes"]]  # type: ignore[arg-type]

    # ── DEM ────────────────────────────────────────────────────────────

    def start_dem_fetch(self, workspace_id: str, bbox: dict[str, Any]) -> str:
        return str(
            self._post(
                "/api/v1/dem/fetch",
                json={"workspace_id": workspace_id, "bounding_box": bbox},
            )["job_id"]
        )

    def get_dem_result(self, job_id: str) -> ElevationGrid:
        data = self._raw("GET", f"/api/v1/dem/{job_id}/result")
        return _grid_from_dict(data)

    # ── Satellite ──────────────────────────────────────────────────────

    def start_satellite_fetch(
        self,
        workspace_id: str,
        bbox: dict[str, Any],
        provider_id: str = "esri_world",
        api_key: str = "",
        custom_url: str = "",
        quality: str = "standard",
    ) -> str:
        return str(
            self._post(
                "/api/v1/satellite/fetch",
                json={
                    "workspace_id": workspace_id,
                    "bounding_box": bbox,
                    "provider_id": provider_id,
                    "api_key": api_key,
                    "custom_url": custom_url,
                    "quality": quality,
                },
            )["job_id"]
        )

    def get_satellite_metadata(self, job_id: str) -> dict[str, Any]:
        return self._raw("GET", f"/api/v1/satellite/{job_id}/result")  # type: ignore[return-value]

    def download_satellite_texture(self, job_id: str) -> SatelliteTexture:
        """Download the PNG and reconstruct a SatelliteTexture object."""
        meta = self.get_satellite_metadata(job_id)
        png_r = self._client.get(f"/api/v1/satellite/{job_id}/texture.png")
        _raise(png_r)
        img = Image.open(io.BytesIO(png_r.content)).convert("RGB")
        return SatelliteTexture(
            image=img,
            min_lat=float(meta["min_lat"]),
            max_lat=float(meta["max_lat"]),
            min_lon=float(meta["min_lon"]),
            max_lon=float(meta["max_lon"]),
            provider_id=str(meta["provider_id"]),
            quality=str(meta["quality"]),
        )

    # ── Scene ──────────────────────────────────────────────────────────

    def start_scene_build(
        self,
        workspace_id: str,
        dem_job_id: str,
        satellite_job_id: str,
        trackpoints: list[dict[str, Any]],
        match_results: list[dict[str, Any]],
        settings: dict[str, Any],
        blender_exe: str | None = None,
    ) -> str:
        body: dict[str, Any] = {
            "workspace_id": workspace_id,
            "dem_job_id": dem_job_id,
            "satellite_job_id": satellite_job_id,
            "trackpoints": trackpoints,
            "match_results": match_results,
            "settings": settings,
        }
        if blender_exe:
            body["blender_exe"] = blender_exe
        return str(self._post("/api/v1/scene/build", json=body)["job_id"])

    # ── Render ─────────────────────────────────────────────────────────

    def start_render_frames(
        self,
        workspace_id: str,
        keyframes: list[dict[str, Any]],
        settings: dict[str, Any],
        scene_job_id: str | None = None,
        blend_path: str | None = None,
        blender_exe: str | None = None,
    ) -> str:
        body: dict[str, Any] = {
            "workspace_id": workspace_id,
            "keyframes": keyframes,
            "settings": settings,
        }
        if scene_job_id is not None:
            body["scene_job_id"] = scene_job_id
        if blend_path is not None:
            body["blend_path"] = blend_path
        if blender_exe:
            body["blender_exe"] = blender_exe
        return str(self._post("/api/v1/render/frames", json=body)["job_id"])

    # ── Compositor ─────────────────────────────────────────────────────

    def start_compositor(
        self,
        workspace_id: str,
        render_job_id: str,
        match_results: list[dict[str, Any]],
        keyframes: list[dict[str, Any]],
        settings: dict[str, Any],
    ) -> str:
        return str(
            self._post(
                "/api/v1/compositor/run",
                json={
                    "workspace_id": workspace_id,
                    "render_job_id": render_job_id,
                    "match_results": match_results,
                    "keyframes": keyframes,
                    "settings": settings,
                },
            )["job_id"]
        )

    # ── Video ──────────────────────────────────────────────────────────

    def start_video_assemble(
        self,
        workspace_id: str,
        source_job_id: str,
        total_frames: int,
        settings: dict[str, Any],
    ) -> str:
        return str(
            self._post(
                "/api/v1/video/assemble",
                json={
                    "workspace_id": workspace_id,
                    "source_job_id": source_job_id,
                    "total_frames": total_frames,
                    "settings": settings,
                },
            )["job_id"]
        )

    def download_video(self, job_id: str, dest_path: str) -> None:
        r = self._client.get(f"/api/v1/video/{job_id}/download")
        _raise(r)
        Path(dest_path).write_bytes(r.content)

    # ── Project ────────────────────────────────────────────────────────

    def project_save(
        self,
        workspace_id: str,
        gpx_path: str | None,
        match_mode: str,
        output_path: str | None,
        photos: list[dict[str, Any]],
        elevation_grid: dict[str, Any] | None,
        satellite: dict[str, Any] | None,
        dest_path: str,
        render_settings: dict[str, Any] | None = None,
        clip_effects: dict[str, Any] | None = None,
        locality_names: dict[str, Any] | None = None,
    ) -> None:
        """POST /project/save and write the returned ZIP to *dest_path*."""
        body: dict[str, Any] = {
            "workspace_id": workspace_id,
            "gpx_path": gpx_path,
            "match_mode": match_mode,
            "output_path": output_path,
            "photos": photos,
        }
        if elevation_grid is not None:
            body["elevation_grid"] = elevation_grid
        if satellite is not None:
            body["satellite"] = satellite
        if render_settings is not None:
            body["render_settings"] = render_settings
        if clip_effects is not None:
            body["clip_effects"] = clip_effects
        if locality_names is not None:
            body["locality_names"] = locality_names
        r = self._client.post("/api/v1/project/save", json=body)
        _raise(r)
        Path(dest_path).write_bytes(r.content)

    def project_load(self, georeel_path: str) -> dict[str, Any]:
        """POST /project/load and return the ProjectLoadResponse as a dict."""
        with open(georeel_path, "rb") as fh:
            r = self._client.post(
                "/api/v1/project/load",
                files={"file": (Path(georeel_path).name, fh, "application/zip")},
            )
        _raise(r)
        return r.json()  # type: ignore[no-any-return]

    # ── Jobs ───────────────────────────────────────────────────────────

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self._raw("GET", f"/api/v1/jobs/{job_id}")  # type: ignore[return-value]

    def cancel_job(self, job_id: str) -> None:
        self._client.delete(f"/api/v1/jobs/{job_id}")

    def poll_job(
        self,
        job_id: str,
        progress_cb: Callable[[int, str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
        process_events: Callable[[], None] | None = None,
        interval_s: float = 0.3,
    ) -> dict[str, Any]:
        """Block until job is done or errored, returning the final job dict.

        *progress_cb(pct, message)* is called on every poll cycle.
        *cancel_check()* returning True sends a cancel to the server and
        raises ``ServerError("Cancelled")``.
        *process_events()* is called during each sleep to allow the caller
        to keep a Qt event loop alive when polling from the main thread.
        """
        while True:
            if cancel_check and cancel_check():
                self.cancel_job(job_id)
                raise ServerError("Cancelled")
            job = self.get_job(job_id)
            if progress_cb is not None:
                progress_cb(int(job["progress"]), str(job.get("message", "")))
            status = str(job["status"])
            if status == "done":
                return job
            if status == "error":
                raise ServerError(str(job.get("error", "Unknown server error")))
            # Sleep in short ticks so process_events stays responsive.
            deadline = time.monotonic() + interval_s
            while time.monotonic() < deadline:
                if process_events:
                    process_events()
                time.sleep(0.05)

    # ── Internal ───────────────────────────────────────────────────────

    def _post(self, path: str, **kwargs: Any) -> dict[str, Any]:
        return self._raw("POST", path, **kwargs)  # type: ignore[return-value]

    def _raw(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        r = self._client.request(method, path, **kwargs)
        _raise(r)
        return r.json()  # type: ignore[no-any-return]


# ── Serialization helpers (core objects → JSON-friendly dicts) ─────────────────

def trackpoint_to_dict(tp: Trackpoint) -> dict[str, Any]:
    ts = tp.timestamp.isoformat() if tp.timestamp else None
    return {
        "latitude": tp.latitude,
        "longitude": tp.longitude,
        "elevation": tp.elevation,
        "timestamp": ts,
        "is_reconstructed": tp.is_reconstructed,
    }


def match_result_to_dict(mr: MatchResult) -> dict[str, Any]:
    return {
        "photo_path": mr.photo_path,
        "trackpoint_index": mr.trackpoint_index,
        "error": mr.error,
        "warning": mr.warning,
        "position": mr.position,
        "sort_key": mr.sort_key,
    }


def keyframe_to_dict(kf: CameraKeyframe) -> dict[str, Any]:
    return {
        "frame": kf.frame,
        "x": kf.x,
        "y": kf.y,
        "z": kf.z,
        "look_at_x": kf.look_at_x,
        "look_at_y": kf.look_at_y,
        "look_at_z": kf.look_at_z,
        "is_pause": kf.is_pause,
        "photo_path": kf.photo_path,
        "is_intro": kf.is_intro,
    }


def bbox_to_dict(bbox: BoundingBox) -> dict[str, Any]:
    return {
        "min_lat": bbox.min_lat,
        "max_lat": bbox.max_lat,
        "min_lon": bbox.min_lon,
        "max_lon": bbox.max_lon,
    }


def elevation_grid_to_dict(grid: ElevationGrid) -> dict[str, Any]:
    data_b64 = base64.b64encode(
        grid.data.astype(np.float32).tobytes()
    ).decode()
    return {
        "rows": grid.rows,
        "cols": grid.cols,
        "min_lat": grid.min_lat,
        "max_lat": grid.max_lat,
        "min_lon": grid.min_lon,
        "max_lon": grid.max_lon,
        "data_b64": data_b64,
    }


# ── Private helpers ────────────────────────────────────────────────────────────

def _grid_from_dict(d: dict[str, Any]) -> ElevationGrid:
    rows, cols = int(d["rows"]), int(d["cols"])
    raw = base64.b64decode(str(d["data_b64"]))
    data = np.frombuffer(raw, dtype=np.float32).reshape(rows, cols).copy()
    return ElevationGrid(
        data=data,
        min_lat=float(d["min_lat"]),
        max_lat=float(d["max_lat"]),
        min_lon=float(d["min_lon"]),
        max_lon=float(d["max_lon"]),
    )


def _keyframe_from_dict(d: dict[str, Any]) -> CameraKeyframe:
    return CameraKeyframe(
        frame=int(d["frame"]),
        x=float(d["x"]),
        y=float(d["y"]),
        z=float(d["z"]),
        look_at_x=float(d["look_at_x"]),
        look_at_y=float(d["look_at_y"]),
        look_at_z=float(d["look_at_z"]),
        is_pause=bool(d.get("is_pause", False)),
        is_intro=bool(d.get("is_intro", False)),
        photo_path=d.get("photo_path"),  # type: ignore[arg-type]
    )


def _trackpoint_from_dict(d: dict[str, Any]) -> Trackpoint:
    from datetime import datetime
    ts_raw = d.get("timestamp")
    ts = datetime.fromisoformat(ts_raw) if ts_raw else None
    return Trackpoint(
        latitude=float(d["latitude"]),
        longitude=float(d["longitude"]),
        elevation=float(d["elevation"]) if d.get("elevation") is not None else None,
        timestamp=ts,
        is_reconstructed=bool(d.get("is_reconstructed", False)),
    )


def _bbox_from_dict(d: dict[str, Any]) -> BoundingBox:
    return BoundingBox(
        min_lat=float(d["min_lat"]),
        max_lat=float(d["max_lat"]),
        min_lon=float(d["min_lon"]),
        max_lon=float(d["max_lon"]),
    )


def match_result_from_dict(d: dict[str, Any]) -> MatchResult:
    return MatchResult(
        photo_path=str(d["photo_path"]),
        trackpoint_index=d.get("trackpoint_index"),
        error=d.get("error"),
        warning=d.get("warning"),
        position=str(d.get("position", "track")),
        sort_key=float(d.get("sort_key", 0.0)),
    )


def _raise(r: httpx.Response) -> None:
    if r.is_error:
        try:
            detail = r.json().get("detail", r.text)
        except Exception:
            detail = r.text
        raise ServerError(f"HTTP {r.status_code}: {detail}")
