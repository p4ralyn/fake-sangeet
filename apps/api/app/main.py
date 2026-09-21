import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates

from app.api.v1.routes import auth, health, scans
from app.api.v1.routes import me as me_routes
from app.core.deps import get_redis_client
from app.core.errors import register_error_handlers
from app.core.http import aclose_http_client
from app.core.logging import configure_logging
from app.core.settings import get_settings
from app.db.session import engine

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    log.info("startup", env=get_settings().app_env.value)
    yield
    await engine.dispose()
    await get_redis_client().aclose()
    await aclose_http_client()
    log.info("shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging()
    app = FastAPI(
        title="unigate",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.app_env.value == "dev" else None,
    )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id, path=request.url.path)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()
        response.headers["x-request-id"] = request_id
        return response

    register_error_handlers(app)
    app.state.templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(me_routes.router)
    app.include_router(scans.router)
    return app


app = create_app()
