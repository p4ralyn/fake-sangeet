import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from app.core.errors import AppError, ErrorCode, register_error_handlers


class _Body(BaseModel):
    name: str


def _build_app() -> FastAPI:
    """A tiny throwaway app with the real error handlers wired in.

    Never add test-only routes to the real `app.main.app` — this app exists only
    for this test module.
    """
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/boom-app-error")
    async def boom_app_error():
        raise AppError(ErrorCode.FORBIDDEN, "nope", http_status=403)

    @app.post("/boom-validation")
    async def boom_validation(body: _Body):
        return {"ok": True}

    @app.get("/boom-unhandled")
    async def boom_unhandled():
        raise RuntimeError("super secret internal detail")

    return app


@pytest.mark.asyncio
async def test_app_error_produces_envelope_with_its_http_status():
    app = _build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/boom-app-error")

    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "FORBIDDEN"
    assert body["error"]["message"] == "nope"


@pytest.mark.asyncio
async def test_validation_error_produces_422_envelope():
    app = _build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/boom-validation", json={})

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert "errors" in body["error"]["details"]


@pytest.mark.asyncio
async def test_unhandled_exception_produces_500_envelope_without_leaking_message():
    app = _build_app()
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/boom-unhandled")

    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "INTERNAL"
    assert "super secret internal detail" not in response.text
