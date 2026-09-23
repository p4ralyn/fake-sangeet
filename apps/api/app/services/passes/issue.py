"""Pass issuance — the row a QR token points at.

M2 owns the minimum: mint a pass for a ticket, with a unique human-readable
short code for the paper fallback. Rendering the QR PNG and emailing it is M6;
`qr_object` stays NULL until then.
"""
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, Pass, PassState, Ticket

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


def refresh_in_place(pass_: Pass) -> Pass:
    """Bump the serial and wind the pass back to "not arrived".

    `serial` is what makes this a refresh rather than a reset (§10.1): the pass
    id and short code survive, and **every token ever minted against the old
    serial stops verifying**. A QR already on somebody's screen, screenshotted,
    or forwarded is dead the moment this runs. That is the point.

    The short code is deliberately kept — it is printed on the paper fallback
    list against this ticket, and changing it would silently invalidate a sheet
    somebody is holding.

    The caller flushes; the caller also decides whether this deserves an audit
    row (it does, everywhere it is used).
    """
    pass_.serial += 1
    pass_.current_state = PassState.NOT_ARRIVED
    pass_.breaks_used = 0
    pass_.revoked_at = None
    pass_.revoked_reason = None
    pass_.first_checkin_at = None
    pass_.last_exit_at = None
    pass_.checked_out_at = None
    return pass_


async def issue_pass(
    db: AsyncSession, *, event: Event, ticket: Ticket, expires_at: datetime | None = None
) -> Pass:
    """One pass per ticket. A COUPLE order has two tickets, so it gets two
    passes, two QR codes and two headcounts — never one shared pass."""
    existing = await db.scalar(select(Pass).where(Pass.ticket_id == ticket.id))
    if existing is not None:
        if existing.current_state is PassState.REVOKED:
            # A transferred ticket left its pass revoked. `passes.ticket_id` is
            # UNIQUE, so reissuing means reviving this row rather than minting
            # a second one -- which is exactly what `serial` is for.
            refresh_in_place(existing)
            await db.flush()
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
