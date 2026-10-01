from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import current_session, get_db, require_role
from app.models import User
from app.schemas.me import EventSummary, MeResponse
from app.services.identity.roles import Role, get_current_event, parse_roles
from app.services.identity.sessions import SessionData

router = APIRouter(prefix="/api/v1", tags=["me"])


@router.get("/me", response_model=MeResponse)
async def me(
    session: SessionData = Depends(current_session),
    db: AsyncSession = Depends(get_db),
) -> MeResponse:
    event = await get_current_event(db)
    # Read from the row rather than the session: a session outlives a sign-in,
    # so an avatar that arrived on the latest login would otherwise not show up
    # until the cookie expired.
    account = await db.get(User, session.user_id)
    return MeResponse(
        user_id=session.user_id,
        net_id=session.net_id,
        display_name=session.display_name,
        email=session.email,
        avatar_url=account.avatar_url if account else None,
        roles=parse_roles(session.roles),
        event=EventSummary(id=event.id, slug=event.slug, name=event.name),
        csrf_token=session.csrf_token,
    )


@router.get("/me/owner-only")
async def owner_only(session: SessionData = Depends(require_role(Role.OWNER))) -> dict[str, str]:
    """Smoke route proving deny-by-default works end to end. Kept — it is the
    regression test for `require_role` and costs three lines."""
    return {"status": "ok"}
