"""The ops dashboard (ADMIN/OWNER).

Thin: authorize, call a read, return it. Every rule about what a number means
lives in `services/ops/dashboard.py`.

Two endpoints were removed rather than left unused. `/notifications` derived a
feed entirely from numbers `/summary` already returns, so it was a second
answer that could disagree with the first; `/ingestion-errors` listed rows that
are repaired in the spreadsheet, where `scripts/ingest.py --dry-run` names them
better. `/summary` still counts both, so nothing became invisible.

**SECURITY is deliberately not allowed here**, even for notifications. A gate
volunteer's tool is `/scan`; their phone is often borrowed and their scan
response is narrowed on purpose (LLD-v1 §11.5). Handing that same device a
screen with 600 attendees' queue state would undo the narrowing.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, require_role
from app.schemas.ops import MailHealth, OpsSummary
from app.services.identity.roles import Role, get_current_event
from app.services.identity.sessions import SessionData
from app.services.ops import dashboard

router = APIRouter(prefix="/api/v1/ops", tags=["ops"])

#: One gate for the whole area rather than per-route roles: every panel here
#: shows the same class of operational data, and a split would invite someone
#: to widen one endpoint without noticing the others.
staff = require_role(Role.OWNER, Role.ADMIN)


@router.get("/summary", response_model=OpsSummary)
async def summary(
    session: SessionData = Depends(staff),
    db: AsyncSession = Depends(get_db),
) -> OpsSummary:
    event = await get_current_event(db)
    return await dashboard.summary(db, event_id=event.id)


@router.get("/mail", response_model=MailHealth)
async def mail(
    session: SessionData = Depends(staff),
    db: AsyncSession = Depends(get_db),
) -> MailHealth:
    """Widened from the proposed OWNER-only to ADMIN as well: "I never got my
    email" is a review-desk question, and these rows expose no more than the
    review queue already does."""
    event = await get_current_event(db)
    return await dashboard.mail_health(db, event_id=event.id)
