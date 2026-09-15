from collections.abc import Iterable
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.settings import get_settings
from app.models import Event, EventStaff, StaffRole, User

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
    """Staff roles come from `event_staff`. Everyone else is a VISITOR.

    PARTICIPANT is added at M2, when `tickets` exists and an approved ticket can
    be looked up. Until then a ticket cannot exist, so nobody can be one.
    """
    rows = await db.execute(
        select(EventStaff.role).where(
            EventStaff.event_id == event_id,
            EventStaff.net_id == user.net_id,
            EventStaff.is_active.is_(True),
        )
    )
    staff_roles = [Role(row[0].value) for row in rows]
    if staff_roles:
        order = list(Role)
        return sorted(set(staff_roles), key=order.index)
    return [Role.VISITOR]


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
