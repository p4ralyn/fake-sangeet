"""Opaque, server-side sessions in Redis.

Opaque and not JWT because revocation must be instant — a stolen scanner phone
or a staff member removed mid-event (LLD-v1 §8.2).
"""
from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.core.security import new_token
from app.core.settings import get_settings

SESSION_PREFIX = "sess:"
USER_INDEX_PREFIX = "sess:user:"


class SessionData(BaseModel):
    sid: str
    user_id: UUID
    net_id: str
    display_name: str
    email: str | None = None
    roles: list[str] = Field(default_factory=list)
    csrf_token: str
    is_staff: bool
    created_at: datetime
    ip_hash: str | None = None
    ua_hash: str | None = None


def _ttl_for(is_staff: bool) -> int:
    settings = get_settings()
    return (
        settings.session_ttl_staff_seconds
        if is_staff
        else settings.session_ttl_participant_seconds
    )


def _user_index_ttl() -> int:
    """Fixed TTL for the per-user session index — independent of which session is
    being created.

    Redis EXPIRE *replaces* a key's TTL, it does not extend it. If this were
    derived from the TTL of the session currently being created, a later
    session with a shorter TTL (e.g. a staff member re-signing in and being
    resolved as a plain VISITOR after their `event_staff` row is deactivated)
    would shorten the index's expiry below that of an older, still-live
    session -- silently expiring `sess:user:<id>` while a live sid it should
    still track is sitting untouched in `sess:<sid>`. `destroy_user_sessions`
    would then find nothing and report 0 kills, i.e. it would silently fail to
    revoke a still-live session. Using the max of every possible session TTL
    makes that shrink unrepresentable rather than merely unlikely.
    """
    settings = get_settings()
    return (
        max(settings.session_ttl_staff_seconds, settings.session_ttl_participant_seconds)
        + 3600
    )


async def create_session(
    redis: Redis,
    *,
    user_id: UUID,
    net_id: str,
    display_name: str,
    email: str | None,
    roles: list[str],
    is_staff: bool,
    ip_hash: str | None = None,
    ua_hash: str | None = None,
) -> SessionData:
    session = SessionData(
        sid=new_token(32),
        user_id=user_id,
        net_id=net_id,
        display_name=display_name,
        email=email,
        roles=roles,
        csrf_token=new_token(24),
        is_staff=is_staff,
        created_at=datetime.now(UTC),
        ip_hash=ip_hash,
        ua_hash=ua_hash,
    )
    ttl = _ttl_for(is_staff)
    async with redis.pipeline(transaction=True) as pipe:
        pipe.set(f"{SESSION_PREFIX}{session.sid}", session.model_dump_json(), ex=ttl)
        pipe.sadd(f"{USER_INDEX_PREFIX}{user_id}", session.sid)
        # Fixed TTL, NOT derived from this session's own ttl -- see _user_index_ttl.
        pipe.expire(f"{USER_INDEX_PREFIX}{user_id}", _user_index_ttl())
        await pipe.execute()
    return session


async def load_session(redis: Redis, sid: str) -> SessionData | None:
    raw = await redis.get(f"{SESSION_PREFIX}{sid}")
    if raw is None:
        return None
    session = SessionData.model_validate_json(raw)
    # Participants slide (8h of activity); staff sessions are fixed to a shift (12h).
    if not session.is_staff:
        await redis.expire(f"{SESSION_PREFIX}{sid}", _ttl_for(False))
    return session


async def destroy_session(redis: Redis, sid: str) -> None:
    raw = await redis.get(f"{SESSION_PREFIX}{sid}")
    async with redis.pipeline(transaction=True) as pipe:
        pipe.delete(f"{SESSION_PREFIX}{sid}")
        if raw is not None:
            user_id = SessionData.model_validate_json(raw).user_id
            pipe.srem(f"{USER_INDEX_PREFIX}{user_id}", sid)
        await pipe.execute()


async def destroy_user_sessions(redis: Redis, user_id: UUID) -> int:
    """Kill every live session for one identity. The device-list 'revoke' button."""
    index_key = f"{USER_INDEX_PREFIX}{user_id}"
    sids = await redis.smembers(index_key)
    if not sids:
        return 0
    async with redis.pipeline(transaction=True) as pipe:
        for sid in sids:
            pipe.delete(f"{SESSION_PREFIX}{sid}")
        pipe.delete(index_key)
        results = await pipe.execute()
    return sum(1 for deleted in results[:-1] if deleted)
