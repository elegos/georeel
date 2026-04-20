"""Workspace: per-session temp directory that owns uploaded files and jobs."""

from __future__ import annotations

import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from georeel.core.photo_metadata import PhotoMetadata


@dataclass
class PhotoEntry:
    photo_id: str
    path: str  # absolute path inside workspace photos/ subdir
    metadata: PhotoMetadata


@dataclass
class Workspace:
    workspace_id: str
    directory: Path
    photos: dict[str, PhotoEntry] = field(default_factory=dict)
    job_ids: set[str] = field(default_factory=set)


class WorkspaceManager:
    """In-memory registry of server-side workspaces."""

    def __init__(self) -> None:
        self._workspaces: dict[str, Workspace] = {}

    def create(self) -> Workspace:
        ws_id = str(uuid.uuid4())
        ws_dir = Path(tempfile.mkdtemp(prefix="georeel_ws_"))
        (ws_dir / "photos").mkdir()
        ws = Workspace(workspace_id=ws_id, directory=ws_dir)
        self._workspaces[ws_id] = ws
        return ws

    def get(self, workspace_id: str) -> Workspace | None:
        return self._workspaces.get(workspace_id)

    def register_job(self, workspace_id: str, job_id: str) -> None:
        """Associate *job_id* with *workspace_id* so it is cleaned up together."""
        ws = self._workspaces.get(workspace_id)
        if ws is not None:
            ws.job_ids.add(job_id)

    def delete(self, workspace_id: str) -> bool:
        ws = self._workspaces.pop(workspace_id, None)
        if ws is None:
            return False
        _cancel_and_cleanup_jobs(ws)
        _rmtree(ws.directory)
        return True

    def cleanup_all(self) -> None:
        for ws in list(self._workspaces.values()):
            _cancel_and_cleanup_jobs(ws)
            _rmtree(ws.directory)
        self._workspaces.clear()


def _cancel_and_cleanup_jobs(ws: Workspace) -> None:
    """Cancel all jobs owned by *ws* and delete their output files."""
    from georeel.server.jobs import get_registry  # local import avoids circular dep
    registry = get_registry()
    for job_id in ws.job_ids:
        registry.cancel_and_delete(job_id)
    ws.job_ids.clear()


def _rmtree(path: Path) -> None:
    import shutil
    try:
        shutil.rmtree(path)
    except OSError:
        pass


# Module-level singleton — shared across all requests in a single server process.
_manager: WorkspaceManager | None = None


def get_manager() -> WorkspaceManager:
    global _manager
    if _manager is None:
        _manager = WorkspaceManager()
    return _manager


def reset_manager() -> None:
    """Replace the singleton with a fresh instance (used in tests)."""
    global _manager
    if _manager is not None:
        _manager.cleanup_all()
    _manager = WorkspaceManager()
