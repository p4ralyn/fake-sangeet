import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request

from app.api.v1.routes import health
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.settings import get_settings

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    log.info("startup", env=get_settings().app_env.value)
    yield
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
    app.include_router(health.router)
    return app


app = create_app()
