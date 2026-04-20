from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI

from georeel.core import temp_manager
from georeel.server.routes import (
    camera,
    compositor,
    dem,
    gpx,
    health,
    jobs,
    photos,
    project,
    render,
    satellite,
    scene,
    video,
    workspaces,
)
from georeel.server.workspace import get_manager


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    temp_manager.cleanup_stale()   # sweep georeel_* dirs left by any crashed prior run
    yield
    get_manager().cleanup_all()    # cancel jobs, delete their files, rmtree workspaces


def create_app() -> FastAPI:
    application = FastAPI(
        title="GeoReel API",
        description="REST interface for the GeoReel pipeline",
        lifespan=_lifespan,
    )
    prefix = "/api/v1"
    application.include_router(health.router, prefix=prefix)
    application.include_router(workspaces.router, prefix=prefix)
    application.include_router(gpx.router, prefix=prefix)
    application.include_router(photos.router, prefix=prefix)
    application.include_router(camera.router, prefix=prefix)
    application.include_router(jobs.router, prefix=prefix)
    application.include_router(dem.router, prefix=prefix)
    application.include_router(satellite.router, prefix=prefix)
    application.include_router(scene.router, prefix=prefix)
    application.include_router(render.router, prefix=prefix)
    application.include_router(compositor.router, prefix=prefix)
    application.include_router(video.router, prefix=prefix)
    application.include_router(project.router, prefix=prefix)
    return application


app: FastAPI = create_app()
