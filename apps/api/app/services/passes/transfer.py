"""Transferring a ticket to somebody else, and the deadline that ends it.

The holder types a NetID. The ticket moves, the old QR dies on the spot, and
the new holder starts the ID check from the beginning — because the whole point
of the ID check is that the face at the door matches the person the pass says
it is, and transfer changes that person.

**Transfer closes three hours before doors.** Not as a reminder for somebody to
switch off at 3pm on the day, but as a rule the server enforces on every
request. The cutoff is derived from `events.starts_at` minus a configured lead
time, so moving the event moves the deadline with it and the two can never
disagree. For Fake Sangeet — 15 October, 18:00 IST — that lands at 15:00 IST.

Why a deadline exists at all: a transfer sends the new holder back through ID
review, and a review queue has people in it. A pass transferred at 5:45pm is a
person at the door at 6pm with an unverified ID and nobody left to check it.

What a transfer must do, all in one transaction:

* move `bound_net_id` to the recipient and clear `user_id`;
* **revoke the pass and bump its serial**, which is what kills every QR the
  previous holder already has on their phone (LLD-v1 §10.1);
* supersede the old registration, keeping it as history rather than deleting
  somebody's verification record;
* put the ticket back to PURCHASED so the new holder is asked for an ID;
* write an audit row naming both sides.
"""
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import hash_pii
from app.models import (
    TICKET_LIVE_STATUSES,
    Event,
    Pass,
    PassState,
    Registration,
    Ticket,
    TicketStatus,
    User,
)
from app.services.audit import write_audit

#: Minutes before `events.starts_at` that transfer stops. Overridable per event
#: via `events.config.transfer.closes_before_minutes`; three hours is the
#: default because that is the answer the client gave.
DEFAULT_CLOSES_BEFORE_MINUTES = 180

#: A NetID as this university issues them, and as `normalise.py` recognises
#: them. Kept loose on purpose — the authority on whether a NetID exists is
#: whether it holds a ticket, not a regex.
NET_ID_MIN = 3
NET_ID_MAX = 32


class TransferRefused(Exception):
    """A real reason this transfer cannot happen, in the holder's words."""


class TransferConfig(BaseModel):
    closes_before_minutes: int = Field(
        default=DEFAULT_CLOSES_BEFORE_MINUTES, ge=0, le=60 * 24 * 7
    )


def transfer_config(event: Event) -> TransferConfig:
    return TransferConfig.model_validate((event.config or {}).get("transfer") or {})


def closes_at(event: Event) -> datetime:
    """The moment transfer stops, derived rather than written down."""
    return event.starts_at - timedelta(minutes=transfer_config(event).closes_before_minutes)


def is_open(event: Event, *, now: datetime | None = None) -> bool:
    return (now or datetime.now(UTC)) < closes_at(event)


class TransferResult(BaseModel):
    ticket_ref: str
    to_net_id: str
    #: True when the recipient must now upload an ID before they have a pass.
    verification_required: bool = True


async def _live_ticket_for(db: AsyncSession, *, event_id: UUID, net_id: str) -> Ticket | None:
    return await db.scalar(
        select(Ticket)
        .where(
            Ticket.event_id == event_id,
            Ticket.bound_net_id == net_id,
            Ticket.status.in_(TICKET_LIVE_STATUSES),
        )
        .limit(1)
    )


def normalise_net_id(raw: str) -> str:
    """Lowercase and trim. An email is accepted and reduced to its local part,
    because half the people typing this will paste an address."""
    value = (raw or "").strip().lower()
    if "@" in value:
        value = value.split("@", 1)[0]
    return value


async def transfer_ticket(
    db: AsyncSession,
    *,
    event: Event,
    holder: User,
    ticket: Ticket,
    to_net_id: str,
    actor_role: str | None = None,
) -> TransferResult:
    """Hand `ticket` to `to_net_id`. Raises `TransferRefused` before writing."""
    recipient = normalise_net_id(to_net_id)

    if not is_open(event):
        raise TransferRefused(
            "Transfers closed three hours before the event. Talk to the desk."
        )
    if not NET_ID_MIN <= len(recipient) <= NET_ID_MAX:
        raise TransferRefused("That doesn’t look like a NetID. Check and try again.")
    if recipient == (holder.net_id or "").lower():
        raise TransferRefused("That’s your own NetID.")
    if ticket.status not in TICKET_LIVE_STATUSES:
        raise TransferRefused("This ticket can’t be transferred.")
    # One person, one pass — the same invariant `tickets_one_per_net_id`
    # enforces in the database, checked here so the refusal is a sentence
    # rather than a constraint violation.
    if await _live_ticket_for(db, event_id=event.id, net_id=recipient) is not None:
        raise TransferRefused("That person already has a ticket.")

    now = datetime.now(UTC)
    from_net_id = ticket.bound_net_id or holder.net_id

    issued = await db.scalar(select(Pass).where(Pass.ticket_id == ticket.id))
    if issued is not None:
        # Bumping the serial is what makes every QR already on the old
        # holder's phone stop working, immediately and without needing them to
        # cooperate. Revoking alone would do it too; doing both means a
        # reissued pass cannot collide with a token minted a second ago.
        issued.serial += 1
        issued.current_state = PassState.REVOKED
        issued.revoked_at = now
        issued.revoked_reason = "TRANSFERRED"

    current = await db.scalar(
        select(Registration).where(
            Registration.ticket_id == ticket.id, Registration.superseded_at.is_(None)
        )
    )
    if current is not None:
        # Kept, not deleted. It is the record that the previous holder's ID was
        # checked, and that record outlives their claim to the ticket.
        current.superseded_at = now

    ticket.bound_net_id = recipient
    ticket.user_id = None
    ticket.bound_at = now
    ticket.status = TicketStatus.PURCHASED
    await db.flush()

    await write_audit(
        db,
        event_id=event.id,
        actor_user_id=holder.id,
        actor_net_id=holder.net_id,
        actor_role=actor_role,
        action="PASS_TRANSFERRED",
        object_type="ticket",
        object_id=ticket.id,
        # Hashed on both sides: an audit row is not a place to keep a list of
        # who gave their ticket to whom in plain text.
        before={"net_id": hash_pii(from_net_id)},
        after={"net_id": hash_pii(recipient), "pass_revoked": issued is not None},
    )
    return TransferResult(ticket_ref=ticket.ticket_ref, to_net_id=recipient)
