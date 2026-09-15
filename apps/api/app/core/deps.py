from collections.abc import AsyncIterator, Callable

from fastapi import Depends, Request, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.security import constant_time_equals
from app.core.settings import get_settings
from app.db.session import session_factory
from app.services.identity.roles import Role
from app.services.identity.sessions import SessionData, load_session

_redis: Redis | None = None


def get_redis_client() -> Redis:
    """Process-wide pooled Redis client."""
    global _redis
    if _redis is None:
        _redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis


async def get_db() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


async def get_redis() -> Redis:
    return get_redis_client()


SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
FORM_CONTENT_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data")


async def optional_session(
    request: Request, redis: Redis = Depends(get_redis)
) -> SessionData | None:
    sid = request.cookies.get(get_settings().session_cookie_name)
    if not sid:
        return None
    return await load_session(redis, sid)


async def current_session(
    session: SessionData | None = Depends(optional_session),
) -> SessionData:
    if session is None:
        raise AppError(
            ErrorCode.UNAUTHENTICATED,
            "Sign in to continue.",
            http_status=status.HTTP_401_UNAUTHORIZED,
        )
    return session


async def require_csrf(
    request: Request, session: SessionData = Depends(current_session)
) -> None:
    """Double-submit, cookie-to-header/form only — never cookie-to-cookie.

    A cross-site request carries a victim's cookies automatically, so comparing
    the CSRF cookie against the session (cookie-to-cookie) proves nothing beyond
    "this browser is logged in" -- exactly what every CSRF attack already has.
    The token must instead arrive somewhere a cross-site form/script cannot put
    it on its own: a custom header (blocked by CORS preflight for other origins)
    or a form field the attacker cannot read back out of the session (LLD-v1 §8.2:
    SameSite=Lax alone is not enough for the scanner PWA's POSTs).
    """
    if request.method in SAFE_METHODS:
        return

    presented = request.headers.get("x-csrf-token")
    if not presented:
        content_type = request.headers.get("content-type", "").lower()
        if any(content_type.startswith(ct) for ct in FORM_CONTENT_TYPES):
            # A malformed body must not escape as Starlette's own HTTPException
            # (which would bypass our error envelope) or as an unread stream
            # that surfaces as a 500 later. A body that fails to parse has not
            # proven it carries the token either way, so treat it as absent
            # and fail closed into the normal 403 CSRF_INVALID path below.
            try:
                form = await request.form()
            except Exception:
                form = None
            value = form.get("csrf_token") if form is not None else None
            presented = value if isinstance(value, str) else None

    if not presented or not constant_time_equals(presented, session.csrf_token):
        raise AppError(
            ErrorCode.CSRF_INVALID,
            "Your session token did not match. Reload the page and try again.",
            http_status=status.HTTP_403_FORBIDDEN,
        )


def require_role(*allowed: Role) -> Callable:
    """Deny by default. Every protected route declares its roles explicitly."""

    async def _dependency(session: SessionData = Depends(current_session)) -> SessionData:
        held = {Role(role) for role in session.roles}
        if not held & set(allowed):
            raise AppError(
                ErrorCode.FORBIDDEN,
                "You do not have access to this.",
                http_status=status.HTTP_403_FORBIDDEN,
            )
        return session

    return _dependency
