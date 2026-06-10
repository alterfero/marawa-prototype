from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.staticfiles import StaticFiles
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import (
    http_exception_handler,
    request_validation_exception_handler,
    unhandled_exception_handler,
)
from app.api.router import api_router
from app.compute.embeddings import EmbeddingBackend
from app.compute.job_runner import JobRunner
from app.core.config import get_settings
from app.db import initialize_database
from app.db.session import SessionLocal, engine
from app.services.jobs import requeue_stale_running_jobs
from app.services.search_service import SearchService


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_database(app.state.db_engine)
    with app.state.session_factory() as session:
        requeue_stale_running_jobs(session)
    if app.state.job_runner_enabled:
        await app.state.job_runner.start()
    yield
    if app.state.job_runner_enabled:
        await app.state.job_runner.stop()


def _mount_frontend(app: FastAPI, frontend_dist_dir: Path) -> None:
    if not frontend_dist_dir.exists():
        return

    @app.get("/experimental/trope-force-3d", include_in_schema=False)
    def experimental_trope_force_redirect():
        return RedirectResponse(url="/#/experimental/trope-force-3d")

    app.mount("/", StaticFiles(directory=frontend_dist_dir, html=True), name="frontend")


def create_app(
    *,
    db_engine: Engine | None = None,
    session_factory: sessionmaker[Session] | None = None,
    job_runner_enabled: bool = True,
    job_runner_poll_interval_seconds: float = 0.25,
    job_handlers: dict | None = None,
    embedding_backend: EmbeddingBackend | None = None,
    frontend_dist_dir: Path | None = None,
) -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.db_engine = db_engine or engine
    app.state.session_factory = session_factory or SessionLocal
    app.state.search_service = SearchService(
        embedding_backend=embedding_backend,
        model_name=settings.model_name,
        embedding_cache_dir=str(settings.model_cache_dir),
    )
    app.state.job_runner_enabled = job_runner_enabled
    configured_handlers = {
        "full_rebuild": app.state.search_service.handle_full_rebuild_job,
    }
    if job_handlers:
        configured_handlers.update(job_handlers)
    app.state.job_runner = JobRunner(
        app.state.session_factory,
        handlers=configured_handlers,
        poll_interval_seconds=job_runner_poll_interval_seconds,
    )
    app.include_router(api_router, prefix=settings.api_prefix)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, request_validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
    _mount_frontend(app, frontend_dist_dir or settings.frontend_dist_dir)
    return app


app = create_app()
