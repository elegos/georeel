"""In-memory job registry for long-running pipeline tasks."""

import shutil
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

JobStatus = Literal["pending", "running", "done", "error"]


@dataclass
class JobRecord:
    job_id: str
    status: JobStatus = "pending"
    progress: int = 0           # 0–100
    message: str = ""
    result: object = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    # Directory to rmtree when this job is cleaned up.  Set by routes that
    # write intermediate output outside the workspace (scene, render, compositor).
    cleanup_path: Path | None = field(default=None, repr=False)

    def cancel(self) -> None:
        """Signal the running job to stop at its next cancellation checkpoint."""
        self._cancel.set()

    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    def cleanup_files(self) -> None:
        """Delete on-disk output produced by this job, if any."""
        if self.cleanup_path is not None:
            try:
                shutil.rmtree(self.cleanup_path, ignore_errors=True)
            finally:
                self.cleanup_path = None


class JobRegistry:
    """Thread-safe in-memory store for JobRecord instances."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._lock: threading.Lock = threading.Lock()

    def create(self) -> JobRecord:
        job = JobRecord(job_id=str(uuid.uuid4()))
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            return self._jobs.get(job_id)

    def delete(self, job_id: str) -> bool:
        with self._lock:
            return self._jobs.pop(job_id, None) is not None

    def cancel_and_delete(self, job_id: str) -> None:
        """Cancel a running job, clean its output files, and remove it from the registry."""
        with self._lock:
            job = self._jobs.pop(job_id, None)
        if job is not None:
            job.cancel()
            job.cleanup_files()

    def all(self) -> list[JobRecord]:
        with self._lock:
            return list(self._jobs.values())


# ── Per-job helpers ────────────────────────────────────────────────────────────

def make_progress_cb(
    job: JobRecord,
    label: str = "",
    *,
    min_pct: int = 1,
    max_pct: int = 99,
) -> Callable[[int, int], None]:
    """Return a progress_cb(done, total) that updates *job*."""
    def cb(done: int, total: int) -> None:
        if total > 0:
            pct = min_pct + int((done / total) * (max_pct - min_pct))
            job.progress = max(min_pct, min(max_pct, pct))
        msg = f"{label} {done}/{total}" if label else f"{done}/{total}"
        job.message = msg.strip()
    return cb


def make_cancel_check(job: JobRecord) -> Callable[[], bool]:
    """Return a cancel_check() that returns True when the job is cancelled."""
    return job.is_cancelled


# ── Module-level singleton ─────────────────────────────────────────────────────

_registry: JobRegistry | None = None


def get_registry() -> JobRegistry:
    global _registry
    if _registry is None:
        _registry = JobRegistry()
    return _registry


def reset_registry() -> None:
    """Replace the singleton with a fresh instance (used in tests)."""
    global _registry
    _registry = JobRegistry()
