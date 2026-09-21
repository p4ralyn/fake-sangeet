from collections.abc import Iterable
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.settings import get_settings
from app.models import TICKET_LIVE_STATUSES, Event, EventStaff, StaffRole, Ticket, User

# Staff roles are the source of truth; the derived roles extend it.
STAFF_ROLE_NAMES = frozenset(role.value for role in StaffRole)


class Role(StrEnum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    SECURITY = "SECURITY"
    PARTICIPANT = "PARTICIPANT"   # authenticated + holds an approved ticket (from M2)
    VISITOR = "VISITOR"           # authenticated, no ticket


# Fail loudly at import if the two enums ever drift apart.
assert STAFF_ROLE_NAMES <= {role.value for role in Role}

# LLD-v1 §11.5: SECURITY sees the narrow scanner card and nothing else -- that
# card is the entire, audited channel through which a volunteer ever sees PII.
# Anything above it sees the full scan record. Classification is explicit and
# defaults to restricted; the assert below fails at import if a new staff role
# is ever added without someone deciding which side of this line it sits on.
SCAN_FULL_VIEW_ROLES = frozenset({Role.OWNER, Role.ADMIN})
SCAN_RESTRICTED_ROLES = frozenset({Role.SECURITY})
assert {r.value for r in SCAN_FULL_VIEW_ROLES | SCAN_RESTRICTED_ROLES} == STAFF_ROLE_NAMES
assert not (SCAN_FULL_VIEW_ROLES & SCAN_RESTRICTED_ROLES)


def parse_roles(values: Iterable[str]) -> list[Role]:
    """Best-effort parse of role strings carried in a session cookie.

    A session can outlive a deploy: staff sessions live 12h, so if a `Role`
    member is ever renamed or removed, a still-live session's cookie can carry
    a string that no longer matches any member. Raising there would turn every
    request on that session into a 500 until it expires. Skipping an
    unrecognised value instead grants it nothing -- consistent with
    deny-by-default, since "holds an unknown role" and "holds no role" must
    both fail the same `require_role` check. Single source of truth so
    `require_role` and `/api/v1/me` cannot drift on how they handle this.
    """
    parsed = []
    for value in values:
        try:
            parsed.append(Role(value))
        except ValueError:
            continue
    return parsed


async def get_current_event(db: AsyncSession) -> Event:
    """One event. Resolved by slug from settings, never passed in by a client."""
    slug = get_settings().event_slug
    event = await db.scalar(select(Event).where(Event.slug == slug))
    if event is None:
        raise AppError(
            ErrorCode.EVENT_NOT_CONFIGURED,
            f"No event with slug '{slug}'. Run `make seed`.",
            http_status=500,
        )
    return event


async def resolve_roles(db: AsyncSession, event_id, user: User) -> list[Role]:
    """Staff roles from `event_staff`, plus the one participant-side role.

    Everyone carries exactly one of VISITOR or PARTICIPANT: they are mutually
    exclusive by definition (LLD-v2 §2 -- VISITOR is authenticated with no
    ticket, PARTICIPANT holds an approved one). Staff carry theirs *alongside*
    their staff roles, so an OWNER can also buy a ticket and walk the
    participant flow. This amends §2's matrix, which read the staff and
    participant columns as exclusive.

    Granting VISITOR to staff escalates nothing -- it is the least-privileged
    role in the system. `is_staff` is derived from STAFF_ROLE_NAMES, so session
    TTL and audit attribution are unaffected by the extra member.

    As of M2 `tickets` exists, so PARTICIPANT is resolved for real: holding any
    ticket that is not dead (`TICKET_LIVE_STATUSES`, the same set the
    `tickets_one_per_identity` index is built from) swaps VISITOR out.
    """
    rows = await db.execute(
        select(EventStaff.role).where(
            EventStaff.event_id == event_id,
            EventStaff.net_id == user.net_id,
            EventStaff.is_active.is_(True),
        )
    )
    staff_roles = {Role(row[0].value) for row in rows}
    holds_ticket = await db.scalar(
        select(Ticket.id)
        .where(
            Ticket.event_id == event_id,
            Ticket.user_id == user.id,
            Ticket.status.in_(TICKET_LIVE_STATUSES),
        )
        .limit(1)
    )
    participant_role = Role.PARTICIPANT if holds_ticket else Role.VISITOR
    order = list(Role)
    return sorted(staff_roles | {participant_role}, key=order.index)


async def list_active_staff(db: AsyncSession, event_id) -> list[tuple[str, str]]:
    """Active staff for the dev picker. A deactivated staff member should not
    be offered as a sign-in option."""
    rows = await db.execute(
        select(EventStaff.net_id, EventStaff.role).where(
            EventStaff.event_id == event_id,
            EventStaff.is_active.is_(True),
        )
    )
    return [(net_id, role.value) for net_id, role in rows]
