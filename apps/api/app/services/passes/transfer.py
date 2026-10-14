"""Transferring a ticket to somebody else, and the deadline that ends it.

The holder types a NetID. The ticket moves, the old QR dies on the spot, and
the new holder starts the ID check from the beginning — because the whole point
of the ID check is that the face at the door matches the person the pass says
it is, and transfer changes that person.

**A ticket may be transferred once, and once only.** Each transfer sends a new
person back through ID review, and a ticket that can keep moving is a ticket
nobody can be held to — by the third hop, nobody at the desk can say who is
supposed to be holding it. The cap lives on `tickets.transfer_count` as a CHECK
constraint, so it survives a bug in this file.

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
* write an audit row naming both sides;
* queue a "someone sent you a pass" email to the new holder, so they learn it
  from us and not only from whoever sent it. Same transaction: a transfer that
  rolls back mails nobody.
"""
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import hash_pii
from app.core.settings import get_settings
from app.models import (
    MAX_TRANSFERS_PER_TICKET,
    TICKET_LIVE_STATUSES,
    Event,
    PartnerMode,
    Pass,
    PassState,
    Registration,
    Ticket,
    TicketStatus,
    User,
)
from app.services.audit import write_audit
from app.services.identity.netid import is_net_id
from app.services.mailer.outbox import TEMPLATE_PASS_RECEIVED, QueuedEmail, queue_email

#: `pass_received` payload kinds: the holder's own pass, or a couple's partner seat.
KIND_TRANSFER = "transfer"
KIND_PARTNER_SEAT = "partner_seat"

#: Minutes before `events.starts_at` that transfer stops. Overridable per event
#: via `events.config.transfer.closes_before_minutes`; three hours is the
#: default because that is the answer the client gave.
DEFAULT_CLOSES_BEFORE_MINUTES = 180


class TransferRefused(Exception):
    """A real reason this transfer cannot happen, in the holder's words."""


#: A holder may pass anything on only once their own ID is approved (client,
#: 10 Oct 2026). Until then the pass page sends them to /verify anyway; this
#: makes the server agree rather than trusting the screen.
ID_APPROVED_STATUSES: frozenset[TicketStatus] = frozenset(
    {TicketStatus.VERIFIED, TicketStatus.PASS_ISSUED}
)
NOT_APPROVED_YET = (
    "You can pass a ticket on only after your own ID has been checked and approved. "
    "Upload your student ID first."
)


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


def _recipient(raw: str, *, holder: User) -> str:
    """The NetID a ticket is going to, or a refusal in the holder's words."""
    recipient = normalise_net_id(raw)
    if not is_net_id(recipient):
        raise TransferRefused("That doesn’t look like a NetID. Check and try again.")
    if recipient == (holder.net_id or "").lower():
        raise TransferRefused("That’s your own NetID.")
    return recipient


async def _refuse_unless_free(db: AsyncSession, *, event_id: UUID, recipient: str) -> None:
    # One person, one pass — the same invariant `tickets_one_per_net_id`
    # enforces in the database, checked here so the refusal is a sentence
    # rather than a constraint violation.
    if await _live_ticket_for(db, event_id=event_id, net_id=recipient) is not None:
        raise TransferRefused("That person already has a ticket.")


async def _hand_over(
    db: AsyncSession, *, ticket: Ticket, recipient: str, now: datetime, spend_transfer: bool = True
) -> bool:
    """Bind `ticket` to `recipient`, spending its one transfer unless told not
    to. Returns whether a pass existed (and is now dead).

    A PROXY partner seat being sent to the partner is the one hand-over that
    does not spend it: that is the seat reaching its first owner, not changing
    owners, and the client's rule is that every ticket -- the second person's
    included -- is freehold (9 Oct 2026).

    Bumping the serial is what makes every QR already on the old holder's
    phone stop working, immediately and without needing them to cooperate.
    Revoking alone would do it too; doing both means a reissued pass cannot
    collide with a token minted a second ago. The old registration is kept as
    the record that the previous holder's ID was checked.
    """
    issued = await db.scalar(select(Pass).where(Pass.ticket_id == ticket.id))
    if issued is not None:
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
        current.superseded_at = now

    ticket.bound_net_id = recipient
    ticket.user_id = None
    ticket.bound_at = now
    if spend_transfer:
        ticket.transfer_count += 1
    ticket.status = TicketStatus.PURCHASED
    await db.flush()
    return issued is not None


async def tell_the_recipient(
    db: AsyncSession,
    *,
    event_id: UUID,
    ticket: Ticket,
    kind: str,
    brings_partner_seat: bool = False,
) -> bool:
    """Queue the "someone sent you a pass" email to `ticket`'s new holder.

    Keyed on the ticket ref AND the new holder. The ref alone was wrong: a
    seat can legitimately reach a second new holder (a partner seat sent and
    then passed on, a reversed transfer re-made), and the second email
    collided with the first one's key and was silently dropped -- four people
    found out on event day (15 Oct 2026). A repeat call for the same holder
    still queues nothing. Like every other email: a link, never the pass.
    """
    domain = get_settings().google_allowed_domain
    return await queue_email(
        db,
        event_id=event_id,
        message=QueuedEmail(
            to_email=f"{ticket.bound_net_id}@{domain}",
            template=TEMPLATE_PASS_RECEIVED,
            idempotency_key=f"{TEMPLATE_PASS_RECEIVED}:{ticket.ticket_ref}:{ticket.bound_net_id}",
            payload={"kind": kind, "brings_partner_seat": brings_partner_seat},
        ),
    )


async def has_unsent_partner_seat(db: AsyncSession, *, event_id: UUID, order_id: UUID) -> bool:
    found = await db.scalar(
        select(Ticket.id)
        .where(
            Ticket.event_id == event_id,
            Ticket.status.in_(TICKET_LIVE_STATUSES),
            unsent_partner_seat(order_ids=[order_id]),
        )
        .limit(1)
    )
    return found is not None


async def transfer_ticket(
    db: AsyncSession,
    *,
    event: Event,
    holder: User,
    ticket: Ticket,
    to_net_id: str,
    actor_role: str | None = None,
) -> TransferResult:
    """Hand `ticket` to `to_net_id`. Raises `TransferRefused` before writing.

    For a PROXY couple whose partner seat is still unsent, the seat goes too,
    without being touched: it hangs off the order rather than a person, so it
    follows whoever holds the order's bound seat — and because moving with
    the bundle is not its transfer, the new holder can still send it.
    """
    if not is_open(event):
        raise TransferRefused(
            "Transfers closed three hours before the event. Talk to the desk."
        )
    recipient = _recipient(to_net_id, holder=holder)
    if ticket.status not in TICKET_LIVE_STATUSES:
        raise TransferRefused("This ticket can’t be transferred.")
    if ticket.status not in ID_APPROVED_STATUSES:
        raise TransferRefused(NOT_APPROVED_YET)
    # One transfer, ever. Refused here so the holder reads a sentence; the
    # CHECK on `tickets.transfer_count` is what actually guarantees it.
    if ticket.transfer_count >= MAX_TRANSFERS_PER_TICKET:
        raise TransferRefused(
            "This pass has already been passed on once, and a pass can only be "
            "transferred once. Talk to the desk."
        )
    await _refuse_unless_free(db, event_id=event.id, recipient=recipient)

    from_net_id = ticket.bound_net_id or holder.net_id
    revoked = await _hand_over(db, ticket=ticket, recipient=recipient, now=datetime.now(UTC))

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
        after={
            "net_id": hash_pii(recipient),
            "pass_revoked": revoked,
            "transfer_count": ticket.transfer_count,
        },
    )
    await tell_the_recipient(
        db,
        event_id=event.id,
        ticket=ticket,
        kind=KIND_TRANSFER,
        brings_partner_seat=await has_unsent_partner_seat(
            db, event_id=event.id, order_id=ticket.order_id
        ),
    )
    return TransferResult(ticket_ref=ticket.ticket_ref, to_net_id=recipient)


def held_by(user: User):
    """The SQL condition for "this ticket is this person's own"."""
    return (Ticket.user_id == user.id) | (Ticket.bound_net_id == user.net_id)


def unsent_partner_seat(*, order_ids):
    """A PROXY couple's second seat that nobody has been given yet."""
    return (
        Ticket.order_id.in_(order_ids)
        & (Ticket.partner_mode == PartnerMode.PROXY)
        & Ticket.user_id.is_(None)
        & Ticket.bound_net_id.is_(None)
    )


async def transfer_partner_seat(
    db: AsyncSession,
    *,
    event: Event,
    holder: User,
    to_net_id: str,
    actor_role: str | None = None,
) -> TransferResult:
    """Send a PROXY couple's unsent partner seat to the partner.

    Which seat moves is derived from who is asking — the unbound PROXY seat in
    an order where they hold the bound one — never named by the client. The
    row is locked so two quick taps cannot send one seat to two people.

    The send does NOT spend the seat's one transfer: the seat is reaching its
    first owner, and every ticket is freehold, the second person's included
    (client, 9 Oct 2026; it replaced the 1 Oct rule that the partner could
    never pass it on). So the partner may pass it on once, like anybody else.
    Their ID is checked like anybody else's before the seat has a QR.
    """
    if not is_open(event):
        raise TransferRefused(
            "Transfers closed three hours before the event. This pass can no longer be sent."
        )
    recipient = _recipient(to_net_id, holder=holder)

    own = await db.scalar(
        select(Ticket)
        .where(
            Ticket.event_id == event.id,
            Ticket.status.in_(TICKET_LIVE_STATUSES),
            held_by(holder),
        )
        .order_by(Ticket.seq_in_order)
        .limit(1)
    )
    if own is not None and own.status not in ID_APPROVED_STATUSES:
        raise TransferRefused(NOT_APPROVED_YET)

    my_orders = (
        select(Ticket.order_id)
        .where(
            Ticket.event_id == event.id,
            Ticket.status.in_(TICKET_LIVE_STATUSES),
            held_by(holder),
        )
        .scalar_subquery()
    )
    seat = await db.scalar(
        select(Ticket)
        .where(
            Ticket.event_id == event.id,
            Ticket.status.in_(TICKET_LIVE_STATUSES),
            unsent_partner_seat(order_ids=my_orders),
        )
        .order_by(Ticket.seq_in_order)
        .limit(1)
        .with_for_update()
    )
    if seat is None:
        raise TransferRefused("There’s no partner pass on this account to send.")
    # Unbound implies unsent, so this cannot fire today; kept so a future
    # admin "unbind" cannot hand the seat a second transfer.
    if seat.transfer_count >= MAX_TRANSFERS_PER_TICKET:  # pragma: no cover
        raise TransferRefused("This pass has already been sent once.")
    await _refuse_unless_free(db, event_id=event.id, recipient=recipient)

    await _hand_over(
        db, ticket=seat, recipient=recipient, now=datetime.now(UTC), spend_transfer=False
    )

    await write_audit(
        db,
        event_id=event.id,
        actor_user_id=holder.id,
        actor_net_id=holder.net_id,
        actor_role=actor_role,
        action="PARTNER_SEAT_TRANSFERRED",
        object_type="ticket",
        object_id=seat.id,
        before={"held_by": hash_pii(holder.net_id or "")},
        after={"net_id": hash_pii(recipient), "transfer_count": seat.transfer_count},
    )
    await tell_the_recipient(db, event_id=event.id, ticket=seat, kind=KIND_PARTNER_SEAT)
    return TransferResult(ticket_ref=seat.ticket_ref, to_net_id=recipient)
