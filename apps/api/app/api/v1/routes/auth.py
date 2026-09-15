import structlog
from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import current_session, get_db, get_redis, optional_session, require_csrf
from app.core.errors import AppError, ErrorCode
from app.core.security import client_ip_prefix, hash_value
from app.core.settings import AuthProviderName, get_settings
from app.services.identity.oidc_state import DEFAULT_NEXT, consume_state, safe_next, start_state
from app.services.identity.provider import get_auth_provider
from app.services.identity.roles import (
    STAFF_ROLE_NAMES,
    get_current_event,
    list_active_staff,
    resolve_roles,
)
from app.services.identity.sessions import SessionData, create_session, destroy_session
from app.services.identity.users import bind_staff_rows, enforce_allowed_domain, upsert_user

router = APIRouter(tags=["auth"])
log = structlog.get_logger()


def _set_auth_cookies(response: Response, session: SessionData) -> None:
    settings = get_settings()
    response.set_cookie(
        settings.session_cookie_name, session.sid,
        httponly=True, secure=settings.cookie_secure, samesite="lax", path="/",
    )
    # Readable by JS on purpose — this is the "double" of double-submit CSRF.
    response.set_cookie(
        settings.csrf_cookie_name, session.csrf_token,
        httponly=False, secure=settings.cookie_secure, samesite="lax", path="/",
    )


@router.get("/", response_class=HTMLResponse)
async def login_page(request: Request, session=Depends(optional_session)) -> Response:
    if session is not None:
        return RedirectResponse(DEFAULT_NEXT, status_code=status.HTTP_303_SEE_OTHER)
    settings = get_settings()
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"is_dev_provider": settings.auth_provider is AuthProviderName.DEV},
    )


@router.get("/auth/start")
async def auth_start(
    next: str = DEFAULT_NEXT,
    redis: Redis = Depends(get_redis),
) -> RedirectResponse:
    provider = get_auth_provider()
    stored = {
        "next": safe_next(next),
        **(await provider.state_payload()),
    }
    state = await start_state(redis, stored, ttl_seconds=get_settings().oidc_state_ttl_seconds)
    url = await provider.authorization_url(state=state, next_url=stored["next"], stored=stored)
    return RedirectResponse(url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get("/auth/dev/picker", response_class=HTMLResponse)
async def dev_picker(request: Request, state: str, db: AsyncSession = Depends(get_db)) -> Response:
    """Only exists while the dev provider is active. 404 otherwise — not 403,
    because the route's existence is itself information."""
    if get_settings().auth_provider is not AuthProviderName.DEV:
        raise AppError(ErrorCode.NOT_FOUND, "Not found.", http_status=status.HTTP_404_NOT_FOUND)
    event = await get_current_event(db)
    staff = await list_active_staff(db, event.id)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="dev_picker.html",
        context={"state": state, "staff": staff},
    )


@router.api_route("/auth/callback", methods=["GET", "POST"])
async def auth_callback(
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> RedirectResponse:
    """One callback for every provider. Entra arrives as GET with query params;
    the dev picker posts a form. Both land here."""
    params = dict(request.query_params)
    if request.method == "POST":
        params.update({k: str(v) for k, v in (await request.form()).items()})

    stored = await consume_state(redis, params.get("state", ""))
    if stored is None:
        raise AppError(
            ErrorCode.OIDC_STATE_INVALID,
            "That sign-in link has expired. Start again.",
        )

    claims = await get_auth_provider().exchange(params=params, stored=stored)
    enforce_allowed_domain(claims)

    event = await get_current_event(db)
    user = await upsert_user(db, claims)
    await bind_staff_rows(db, event.id, user)
    roles = await resolve_roles(db, event.id, user)
    await db.commit()

    client_host = request.client.host if request.client else ""
    session = await create_session(
        redis,
        user_id=user.id,
        net_id=user.net_id or claims.net_id,
        display_name=user.display_name or claims.display_name,
        email=user.email,
        roles=[role.value for role in roles],
        is_staff=any(role.value in STAFF_ROLE_NAMES for role in roles),
        ip_hash=hash_value(client_ip_prefix(client_host)) if client_host else None,
        ua_hash=hash_value(request.headers.get("user-agent", "")),
    )
    log.info(
        "login", user_id=str(user.id), roles=[r.value for r in roles], provider=claims.auth_method
    )

    response = RedirectResponse(
        safe_next(stored.get("next")), status_code=status.HTTP_303_SEE_OTHER
    )
    _set_auth_cookies(response, session)
    return response


@router.post("/auth/logout", dependencies=[Depends(require_csrf)])
async def logout(
    session: SessionData = Depends(current_session),
    redis: Redis = Depends(get_redis),
) -> RedirectResponse:
    await destroy_session(redis, session.sid)
    settings = get_settings()
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")
    return response
