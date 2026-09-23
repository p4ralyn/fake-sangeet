from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import current_session, get_db, require_role
from app.core.errors import AppError, ErrorCode
from app.models import User
from app.schemas.me import EventSummary, MeResponse
from app.schemas.passes import MyPassResponse
from app.services.identity.roles import Role, get_current_event, parse_roles
from app.services.identity.sessions import SessionData
from app.services.passes.view import my_pass_view

router = APIRouter(prefix="/api/v1", tags=["me"])


@router.get("/me", response_model=MeResponse)
async def me(
    session: SessionData = Depends(current_session),
    db: AsyncSession = Depends(get_db),
) -> MeResponse:
    event = await get_current_event(db)
    return MeResponse(
        user_id=session.user_id,
        net_id=session.net_id,
        display_name=session.display_name,
        email=session.email,
        roles=parse_roles(session.roles),
        event=EventSummary(id=event.id, slug=event.slug, name=event.name),
        csrf_token=session.csrf_token,
    )


@router.get("/me/pass", response_model=MyPassResponse)
async def my_pass(
    session: SessionData = Depends(current_session),
    db: AsyncSession = Depends(get_db),
) -> MyPassResponse:
    """This person's own tickets and their QR tokens (M6).

    Served under `/api/v1`, not the contract's original bare `/my/pass`: the
    dev server proxies only `/api` and `/auth` to the backend, so the bare path
    would have been answered by Vite with the SPA's index.html. The contract's
    own transport rules win over its path sketch.
    """
    event = await get_current_event(db)
    user = await db.get(User, session.user_id)
    if user is None:  # pragma: no cover -- a live session implies a live user
        raise AppError(
            ErrorCode.UNAUTHENTICATED, "Sign in to continue.", http_status=401
        )
    return await my_pass_view(db, event_id=event.id, user=user)


@router.get("/me/owner-only")
async def owner_only(session: SessionData = Depends(require_role(Role.OWNER))) -> dict[str, str]:
    """Smoke route proving deny-by-default works end to end. Kept — it is the
    regression test for `require_role` and costs three lines."""
    return {"status": "ok"}
