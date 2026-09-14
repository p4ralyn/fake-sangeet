from enum import StrEnum

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.schemas.common import ErrorDetail, ErrorEnvelope

log = structlog.get_logger()


class ErrorCode(StrEnum):
    """Stable contract. Extended per milestone; never renumbered."""

    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    CSRF_INVALID = "CSRF_INVALID"
    AUTH_PROVIDER_DISABLED = "AUTH_PROVIDER_DISABLED"
    DOMAIN_NOT_ALLOWED = "DOMAIN_NOT_ALLOWED"
    OIDC_STATE_INVALID = "OIDC_STATE_INVALID"
    OIDC_TOKEN_INVALID = "OIDC_TOKEN_INVALID"  # noqa: S105 -- error code label, not a secret
    UNKNOWN_NET_ID = "UNKNOWN_NET_ID"
    EVENT_NOT_CONFIGURED = "EVENT_NOT_CONFIGURED"
    INTERNAL = "INTERNAL"


class AppError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        http_status: int = status.HTTP_400_BAD_REQUEST,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = details


def _envelope(
    code: str, message: str, request: Request, details: dict | None = None
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    body = ErrorEnvelope(
        error=ErrorDetail(code=code, message=message, details=details, request_id=request_id)
    )
    status_code = getattr(request.state, "_error_status", status.HTTP_400_BAD_REQUEST)
    return JSONResponse(status_code=status_code, content=body.model_dump())


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        request.state._error_status = exc.http_status
        log.warning("app_error", code=exc.code.value, path=request.url.path)
        return _envelope(exc.code.value, exc.message, request, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        request.state._error_status = status.HTTP_422_UNPROCESSABLE_ENTITY
        return _envelope(
            ErrorCode.VALIDATION_ERROR.value,
            "Request validation failed.",
            request,
            {"errors": exc.errors()},
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        request.state._error_status = status.HTTP_500_INTERNAL_SERVER_ERROR
        log.exception("unhandled_error", path=request.url.path)
        return _envelope(ErrorCode.INTERNAL.value, "Something went wrong.", request)
