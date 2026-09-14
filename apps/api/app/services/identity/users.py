from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.settings import get_settings
from app.models import EventStaff, User
from app.services.identity.provider import IdentityClaims


def enforce_allowed_domain(claims: IdentityClaims) -> None:
    """Reject sign-in from outside the university's email domains.

    A no-op when `ALLOWED_DOMAINS` is unset (e.g. the dev provider's default
    "localhost" email) or the provider didn't supply an email at all.
    """
    domains = get_settings().allowed_domains
    if not domains or not claims.email:
        return
    domain = claims.email.rsplit("@", 1)[-1].lower()
    if domain not in {d.lower() for d in domains}:
        raise AppError(
            ErrorCode.DOMAIN_NOT_ALLOWED,
            "Use your university account to sign in.",
            http_status=403,
        )


async def upsert_user(db: AsyncSession, claims: IdentityClaims) -> User:
    """`subject` is the identity key — `net_id` can change when a roll format does."""
    user = await db.scalar(select(User).where(User.ms_object_id == claims.subject))
    if user is None:
        user = User(ms_object_id=claims.subject, auth_method=claims.auth_method)
        db.add(user)
    user.net_id = claims.net_id
    user.email = claims.email
    user.display_name = claims.display_name
    user.last_login_at = datetime.now(UTC)
    await db.flush()
    return user


async def bind_staff_rows(db: AsyncSession, event_id, user: User) -> None:
    """Staff are pre-provisioned by NetID; first login attaches the user row."""
    await db.execute(
        update(EventStaff)
        .where(
            EventStaff.event_id == event_id,
            EventStaff.net_id == user.net_id,
            EventStaff.user_id.is_(None),
        )
        .values(user_id=user.id)
    )
    await db.flush()
