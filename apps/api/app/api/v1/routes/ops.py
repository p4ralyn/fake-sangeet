"""The ops dashboard (ADMIN/OWNER).

Thin: authorize, call a read, return it. Every rule about what a number means
lives in `services/ops/dashboard.py`.

Two endpoints were removed rather than left unused. `/notifications` derived a
feed entirely from numbers `/summary` already returns, so it was a second
answer that could disagree with the first; `/ingestion-errors` listed rows that
are repaired in the spreadsheet, where `scripts/ingest.py --dry-run` names them
better. `/summary` still counts both, so nothing became invisible.

**SECURITY may read `/summary` and nothing else here.** That amends the
original "not allowed at all", and the line it draws is per-attendee data, not
the area: `/summary` is counts — how many are inside, how many scans in the
last window, seats sold. `/mail` names people who never got their ticket, which
is exactly the 600-attendee state §11.5 keeps off a borrowed gate phone.

The guard is therefore split per route rather than applied once to the router.
That was avoided before on the grounds that a split invites someone to widen
one endpoint without noticing the others; the answer is that the role tests
enumerate the router itself, so a new endpoint cannot be added without landing
in them.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, require_role
from app.schemas.ops import MailHealth, OpsSummary
from app.services.identity.roles import Role, get_current_event
from app.services.identity.sessions import SessionData
from app.services.ops import dashboard

router = APIRouter(prefix="/api/v1/ops", tags=["ops"])

#: Anything that names an attendee.
staff = require_role(Role.OWNER, Role.ADMIN)

#: Counts only. A gate volunteer needs to know the room is filling and that the
#: door is alive; neither fact identifies anybody.
gate_view = require_role(Role.OWNER, Role.ADMIN, Role.SECURITY)


@router.get("/summary", response_model=OpsSummary)
async def summary(
    session: SessionData = Depends(gate_view),
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
