from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import current_session, get_db, require_role
from app.schemas.me import EventSummary, MeResponse
from app.services.identity.roles import Role, get_current_event
from app.services.identity.sessions import SessionData

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
        roles=[Role(role) for role in session.roles],
        event=EventSummary(id=event.id, slug=event.slug, name=event.name),
        csrf_token=session.csrf_token,
    )


@router.get("/me/owner-only")
async def owner_only(session: SessionData = Depends(require_role(Role.OWNER))) -> dict[str, str]:
    """Smoke route proving deny-by-default works end to end. Kept — it is the
    regression test for `require_role` and costs three lines."""
    return {"status": "ok"}
