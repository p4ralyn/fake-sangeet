"""Pass issuance — the row a QR token points at.

M2 owns the minimum: mint a pass for a ticket, with a unique human-readable
short code for the paper fallback. Rendering the QR PNG and emailing it is M6;
`qr_object` stays NULL until then.
"""
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, Pass, Ticket

# Crockford-ish: no I, L, O, U, 0 or 1. A volunteer reads these off a printed
# list at a dark door and types them into a phone.
SHORT_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
SHORT_CODE_GROUPS = (3, 3)

# A pass stays scannable past the last bus home — someone checking out at 01:00
# after a 22:00 finish must not be turned away by their own QR.
PASS_GRACE_HOURS = 6


def generate_short_code() -> str:
    groups = [
        "".join(secrets.choice(SHORT_CODE_ALPHABET) for _ in range(size))
        for size in SHORT_CODE_GROUPS
    ]
    return "-".join(groups)


async def issue_pass(
    db: AsyncSession, *, event: Event, ticket: Ticket, expires_at: datetime | None = None
) -> Pass:
    """One pass per ticket. A COUPLE order has two tickets, so it gets two
    passes, two QR codes and two headcounts — never one shared pass."""
    existing = await db.scalar(select(Pass).where(Pass.ticket_id == ticket.id))
    if existing is not None:
        return existing

    for _ in range(8):
        code = generate_short_code()
        clash = await db.scalar(
            select(Pass).where(Pass.event_id == event.id, Pass.short_code == code)
        )
        if clash is None:
            break
    else:  # pragma: no cover -- 30^6 codes; reaching this means something is wrong
        raise RuntimeError("Could not allocate a unique pass short code.")

    issued = Pass(
        event_id=event.id,
        ticket_id=ticket.id,
        short_code=code,
        expires_at=expires_at or event.ends_at + timedelta(hours=PASS_GRACE_HOURS),
    )
    db.add(issued)
    await db.flush()
    return issued
