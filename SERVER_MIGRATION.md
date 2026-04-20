# Server Migration Plan

Migrate GeoReel from a monolithic desktop app to a client-server architecture:
FastAPI backend + PySide6 GUI client.

---

## Goals

- `georeel` (GUI) remains the user-facing entry point
- All pipeline logic moves to a FastAPI service (`georeel-server`)
- GUI communicates with service via REST (HTTP/JSON + multipart file uploads)
- Service can run standalone (`georeel-server`) or be spawned as a subprocess by GUI
- Long-running pipeline stages report progress via job polling or SSE

---

## Default Port

**8765** — fixed, configurable via `--port` on `georeel-server` and `--server-port` on `georeel`.

---

## Server Startup Logic (GUI side)

```
GUI starts
  │
  ├─ GET http://localhost:8765/health
  │    ├─ 200 OK → attach to existing server (use port 8765)
  │    └─ connection refused
  │         └─ bind random OS port
  │              └─ spawn subprocess: georeel-server --port <random>
  │                   └─ poll /health until ready (timeout 10 s)
  │                        └─ use random port for session
  │
  └─ at GUI exit: if subprocess was spawned → terminate it
```

External `georeel-server` (user-started) takes precedence. GUI never kills a
pre-existing server.

---

## New Entry Points (`pyproject.toml`)

```toml
[project.scripts]
georeel        = "georeel.main:main"          # GUI (existing)
georeel-server = "georeel.server.main:main"   # standalone server (new)
```

---

## New Source Layout

```
src/georeel/
├── server/
│   ├── __init__.py
│   ├── main.py              # CLI entry point: parse --host/--port, run uvicorn
│   ├── app.py               # FastAPI app factory, lifespan, middleware
│   ├── jobs.py              # In-memory job registry (JobStatus, progress, cancel)
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── health.py        # GET /health
│   │   ├── gpx.py           # /gpx/*
│   │   ├── photos.py        # /photos/*
│   │   ├── dem.py           # /dem/*
│   │   ├── satellite.py     # /satellite/*
│   │   ├── scene.py         # /scene/*
│   │   ├── camera.py        # /camera/*
│   │   ├── render.py        # /render/*
│   │   ├── compositor.py    # /compositor/*
│   │   ├── video.py         # /video/*
│   │   ├── project.py       # /project/*
│   │   └── jobs.py          # /jobs/{job_id}
│   └── models/
│       ├── __init__.py
│       └── *.py             # Pydantic request/response schemas
│
└── ui/
    ├── server_client.py     # HTTP client wrapper (httpx, auth, retry)
    ├── server_manager.py    # Subprocess lifecycle (spawn, health-poll, terminate)
    └── ... (existing UI files, refactored to use server_client)
```

---

## REST API

All endpoints under `/api/v1/`.

### Health

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness + version. Returns `{"status":"ok","version":"x.y.z"}` |

### File workspace

The server manages a **workspace** concept: uploaded files are stored server-side and
referenced by `workspace_id`. Since GUI and server run on the same machine, local file
paths are also accepted as an alternative to upload (avoids copying large files).

| Method | Path | Description |
|--------|------|-------------|
| POST | `/workspaces` | Create workspace → `{"workspace_id": "..."}` |
| DELETE | `/workspaces/{id}` | Delete workspace + all temp files |

### GPX

| Method | Path | Description |
|--------|------|-------------|
| POST | `/gpx/parse` | Multipart: upload `.gpx`. Returns trackpoints + bounding box + stats |
| POST | `/gpx/clean` | Body: trackpoints + cleaner settings. Returns cleaned trackpoints |

### Photos

| Method | Path | Description |
|--------|------|-------------|
| POST | `/photos/upload` | Multipart: upload photo files into workspace. Returns photo_ids |
| POST | `/photos/match` | Body: photo_ids + trackpoints + match settings. Returns match_results |

### DEM (async job)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/dem/fetch` | Body: bounding_box + settings → `{"job_id":"..."}` |
| GET | `/dem/{job_id}/result` | Returns serialised ElevationGrid when done |

### Satellite (async job)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/satellite/fetch` | Body: bounding_box + settings → `{"job_id":"..."}` |
| GET | `/satellite/{job_id}/result` | Returns texture metadata; texture PNG downloadable via next endpoint |
| GET | `/satellite/{job_id}/texture.png` | Binary PNG download |

### Scene (async job)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/scene/build` | Body: workspace_id + dem_job_id + satellite_job_id + match_results + settings → `{"job_id":"..."}` |
| GET | `/scene/{job_id}/download` | Download `.blend` file |

### Camera

| Method | Path | Description |
|--------|------|-------------|
| POST | `/camera/keyframes` | Body: trackpoints + elevation_grid + match_results + settings. Sync (fast). Returns keyframes |

### Render (async job)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/render/frames` | Body: scene_job_id + keyframes + settings → `{"job_id":"..."}` |

### Compositor (async job)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/compositor/run` | Body: render_job_id + match_results + keyframes + photo_ids + settings → `{"job_id":"..."}` |

### Video (async job)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/video/assemble` | Body: compositor_job_id + settings → `{"job_id":"..."}` |
| GET | `/video/{job_id}/download` | Download final video file |

### Project

| Method | Path | Description |
|--------|------|-------------|
| POST | `/project/save` | Body: full ProjectState. Returns `.georeel` ZIP as binary download |
| POST | `/project/load` | Multipart: upload `.georeel`. Returns ProjectState JSON |

### Jobs (progress polling)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/jobs/{job_id}` | `{"status": "pending|running|done|error", "progress": 0–100, "message": "..."}` |
| DELETE | `/jobs/{job_id}` | Cancel running job |
| GET | `/jobs/{job_id}/events` | SSE stream: `data: {"progress":42,"message":"..."}` per tick |

---

## Job Registry (`server/jobs.py`)

```python
@dataclass
class JobRecord:
    job_id: str
    status: Literal["pending", "running", "done", "error"]
    progress: int          # 0–100
    message: str
    result: Any            # set when done
    error: str | None
    cancel_event: asyncio.Event
    created_at: datetime
```

- In-memory dict, keyed by UUID
- Long-running pipeline functions accept a `progress_cb(pct, msg)` callback
- Background tasks (FastAPI `BackgroundTasks` or `asyncio.create_task`) run pipeline in thread pool
- Completed jobs retained for 30 min, then evicted
- `DELETE /jobs/{job_id}` sets `cancel_event`; pipeline stages check it periodically

---

## Progress Reporting Strategy

Two options exposed to clients:

1. **Polling** — `GET /jobs/{job_id}` every ~500 ms. Simple, works everywhere.
2. **SSE** — `GET /jobs/{job_id}/events`. Real-time stream, closes when job ends. GUI uses this.

GUI replaces current `QThread` workers with:
- HTTP POST to start job → get `job_id`
- SSE connection to `/jobs/{job_id}/events` → update progress bar
- On `done` event: fetch result endpoint

---

## GUI Refactor Plan

### `ui/server_client.py`

Thin wrapper over `httpx.AsyncClient`:
- Base URL from `ServerManager`
- JSON serialisation helpers
- Retry on `503` (server still starting)
- Raises typed exceptions mapped from HTTP error codes

### `ui/server_manager.py`

```python
class ServerManager:
    async def connect_or_spawn(self, preferred_port=8765) -> int:
        """Try preferred_port; if unavailable, spawn subprocess on random port."""

    async def shutdown(self):
        """Terminate subprocess if we own it."""
```

Uses `asyncio.subprocess` to launch `georeel-server --port <port>`.

### Worker thread replacement

Current `ScenePrepWorker` and `KeyframeCalcWorker` (`QThread`) are replaced by:
- `QNetworkAccessManager` or `httpx` async calls wrapped in `asyncio`-aware Qt integration
- Progress dialogs subscribe to SSE job events via a lightweight Qt-compatible SSE reader

Existing `ui/` dialogs keep their signatures; only the *trigger* mechanism changes
from "call core function in thread" to "POST to server + poll SSE".

---

## New Dependencies

```
# runtime (server)
fastapi>=0.115
uvicorn[standard]>=0.32
httpx>=0.28          # GUI HTTP client (already useful for nominatim/osrm, add here)
pydantic>=2.10       # already pulled by fastapi

# dev/test
httpx                # TestClient for API tests
pytest-asyncio>=0.25
```

Add via `uv add fastapi uvicorn httpx`.

---

## Migration Phases

### Phase 1 — Server scaffold (no GUI changes)

1. Create `src/georeel/server/` package
2. Implement `app.py`, `jobs.py`, health route
3. Add `georeel-server` entry point with `--host`/`--port` args
4. Write API tests for health endpoint
5. Verify `basedpyright` passes

### Phase 2 — Sync/fast pipeline endpoints

1. `/gpx/parse`, `/gpx/clean`
2. `/photos/upload`, `/photos/match`
3. `/camera/keyframes`
4. Pydantic models for all request/response schemas
5. Tests for each route (target: routes covered ≥ 85%)

### Phase 3 — Async job endpoints

1. Job registry
2. `/dem/fetch`, `/satellite/fetch` with SSE progress
3. `/scene/build`, `/render/frames`, `/compositor/run`, `/video/assemble`
4. Job cancel support
5. Tests with mock pipeline functions

### Phase 4 — GUI client

1. `server_client.py` + `server_manager.py`
2. Replace `ScenePrepWorker` → server calls
3. Replace `KeyframeCalcWorker` → server calls
4. Replace all direct `core.*` calls in `main_window.py` → server calls
5. Progress dialogs wired to SSE

### Phase 5 — Project API + polish

1. `/project/save`, `/project/load`
2. Workspace cleanup on GUI exit / server shutdown
3. Error handling: server error → GUI error dialog (preserve UX)
4. Update docs: ARCHITECTURE.md, README.md, INSTALL.md

---

## Invariants to Preserve

- All open data sources stay open; no new commercial API calls introduced
- `.georeel` project file format unchanged
- `basedpyright` passes with no errors after each phase
- `pytest` ≥ 85% coverage maintained; new routes fully tested
- `uv` used for all dependency management
- `uv run georeel` still works unchanged from user perspective
