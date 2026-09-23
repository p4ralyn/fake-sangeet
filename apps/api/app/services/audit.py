from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, Event, FunnelEvent, User
from app.services.identity.roles import STAFF_ROLE_NAMES, Role


def json_safe(value: object) -> object:
    """Coerce an audit payload into something JSONB can actually store.

    `before`/`after` are JSONB, and psycopg serialises them with the stdlib
    encoder, which raises on a UUID. Every caller passing one would have to
    remember to `str()` it, and **one of them forgot**: a raw `device_id` in
    the ID_IMAGE_VIEWED row turned every scan of a real attendee's pass into a
    500 at the gate. CLAUDE.md is explicit that a rule which matters gets
    enforced rather than asked for politely, so the coercion lives here, once,
    where no call site can skip it.

    Unknown types fall back to `repr` rather than raising. That is a deliberate
    trade: an audit row is a side record of something that already happened,
    and failing the scan because the note about it would not serialise is
    strictly worse than storing an imperfect note. The row is still written,
    still findable, and still says what happened.
    """
    if value is None or isinstance(value, str | bool | int | float):
        return value
    if isinstance(value, Enum):
        return json_safe(value.value)
    if isinstance(value, UUID | datetime | date | Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [json_safe(item) for item in value]
    return repr(value)


async def write_audit(
    db: AsyncSession,
    *,
    event_id: UUID | None,
    actor_user_id: UUID | None,
    actor_net_id: str | None,
    actor_role: str | None,
    action: str,
    object_type: str | None = None,
    object_id: UUID | None = None,
    before: dict | None = None,
    after: dict | None = None,
    ip_hash: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Append-only. Never store raw PII in `before`/`after` — hash it first.

    Payloads are coerced by `json_safe`, so a caller may hand this a UUID, a
    datetime or an enum without the write failing underneath them.
    """
    db.add(
        AuditLog(
            event_id=event_id,
            actor_user_id=actor_user_id,
            actor_net_id=actor_net_id,
            actor_role=actor_role,
            action=action,
            object_type=object_type,
            object_id=object_id,
            before=json_safe(before),
            after=json_safe(after),
            ip_hash=ip_hash,
            user_agent=user_agent,
        )
    )


async def write_funnel_event(
    db: AsyncSession, *, event_id: UUID, user_id: UUID | None, step: str
) -> None:
    """Which step each person reached. Unbackfillable, so it starts at M1."""
    db.add(FunnelEvent(event_id=event_id, user_id=user_id, step=step))


async def record_login(
    db: AsyncSession,
    *,
    event: Event,
    user: User,
    roles: list[Role],
    ip_hash: str | None,
    user_agent: str | None,
) -> None:
    """Both rows a login writes, in one call so `auth_callback` stays thin.

    `actor_role` is the `staff_role` PG enum column — only `STAFF_ROLE_NAMES`
    (derived from that same enum at import time, never hardcoded here) or NULL
    are valid values there, so a VISITOR login must write NULL, never the
    string "VISITOR".
    """
    await write_audit(
        db,
        event_id=event.id,
        actor_user_id=user.id,
        actor_net_id=user.net_id,
        actor_role=next((r.value for r in roles if r.value in STAFF_ROLE_NAMES), None),
        action="LOGIN",
        object_type="user",
        object_id=user.id,
        after={"provider": user.auth_method, "roles": [r.value for r in roles]},
        ip_hash=ip_hash,
        user_agent=user_agent,
    )
    await write_funnel_event(db, event_id=event.id, user_id=user.id, step="LOGIN")
