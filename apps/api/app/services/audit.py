from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, Event, FunnelEvent, User
from app.services.identity.roles import STAFF_ROLE_NAMES, Role


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
    """Append-only. Never store raw PII in `before`/`after` — hash it first."""
    db.add(
        AuditLog(
            event_id=event_id,
            actor_user_id=actor_user_id,
            actor_net_id=actor_net_id,
            actor_role=actor_role,
            action=action,
            object_type=object_type,
            object_id=object_id,
            before=before,
            after=after,
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
