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
    DOMAIN_NOT_ALLOWED = "DOMAIN_NOT_ALLOWED"
    OIDC_STATE_INVALID = "OIDC_STATE_INVALID"
    OIDC_TOKEN_INVALID = "OIDC_TOKEN_INVALID"  # noqa: S105 -- error code label, not a secret
    UNKNOWN_NET_ID = "UNKNOWN_NET_ID"
    EVENT_NOT_CONFIGURED = "EVENT_NOT_CONFIGURED"
    INTERNAL = "INTERNAL"

    # ── access core, M2 (LLD-v1 §13.6) ───────────────────────────────────
    # Each failure mode gets its own code: at a gate, "why was this refused"
    # decides whether the volunteer waves someone through, sends them to the
    # desk, or calls security.
    INVALID_TOKEN = "INVALID_TOKEN"  # noqa: S105 -- error code label, not a secret
    INVALID_SIGNATURE = "INVALID_SIGNATURE"
    TOKEN_WRONG_EVENT = "TOKEN_WRONG_EVENT"  # noqa: S105 -- error code label
    UNKNOWN_PASS = "UNKNOWN_PASS"  # noqa: S105 -- error code label
    PASS_EXPIRED = "PASS_EXPIRED"  # noqa: S105 -- error code label
    PASS_REVOKED = "PASS_REVOKED"  # noqa: S105 -- error code label
    PASS_SUPERSEDED = "PASS_SUPERSEDED"  # noqa: S105 -- error code label
    INVALID_STATE = "INVALID_STATE"
    GATE_NOT_PERMITTED = "GATE_NOT_PERMITTED"
    DECISION_TOKEN_INVALID = "DECISION_TOKEN_INVALID"  # noqa: S105 -- error code label
    DECISION_TOKEN_EXPIRED = "DECISION_TOKEN_EXPIRED"  # noqa: S105 -- error code label


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
    code: str,
    message: str,
    request: Request,
    status_code: int,
    details: dict | None = None,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    body = ErrorEnvelope(
        error=ErrorDetail(code=code, message=message, details=details, request_id=request_id)
    )
    return JSONResponse(status_code=status_code, content=body.model_dump())


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        log.warning("app_error", error_code=exc.code.value, path=request.url.path)
        return _envelope(exc.code.value, exc.message, request, exc.http_status, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _envelope(
            ErrorCode.VALIDATION_ERROR.value,
            "Request validation failed.",
            request,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"errors": exc.errors()},
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", path=request.url.path)
        return _envelope(
            ErrorCode.INTERNAL.value,
            "Something went wrong.",
            request,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
