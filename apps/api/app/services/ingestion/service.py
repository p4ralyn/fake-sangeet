"""Turn source rows into orders, tickets, seats and queued mail (M3).

This is the only path by which cohort A — the ~441 seats already sold offline —
enters the system. Nothing about those people exists here until this runs.

Three properties it must have, and each is enforced by a mechanism rather than
by care:

* **Idempotent.** Every row hashes to a stable `orders.external_id`, unique per
  event. A second run of the same workbook inserts nothing. A run after the
  client repairs four broken rows mints exactly four orders, because only those
  four rows produce a hash that is not already there.
* **Row-tolerant.** A defective row writes an `ingestion_errors` row and the
  batch carries on (LLD-v1 §7.2). 295 rows with 4 bad ones mint 291 orders.
  Each row is its own transaction, so a failure rolls back that row and nothing
  else.
* **Seat-exact.** Seats move only through `services/inventory.py`, in the same
  transaction as the order that consumed them, under the addendum §A1.2 row
  lock. Running out of seats is not a row defect — it halts the batch, because
  continuing would silently admit whichever rows happened to be small.

The hash covers the *identity-bearing* fields only — source, ticket type, buyer
and partner NetID — never name or phone. That is LLD-v1 §7.2's split: identity
decides whether a row is the same row, mutable columns are updated in place. So
correcting a mangled phone number updates an order; correcting an unreadable
NetID mints a new one.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime
from uuid import UUID

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from app.models import (
    TICKET_LIVE_STATUSES,
    EmailOutbox,
    EmailStatus,
    Event,
    GuestTag,
    IngestionError,
    LedgerReason,
    Order,
    PartnerMode,
    Ticket,
    TicketStatus,
)
from app.models import TicketSource as TicketSourceRecord
from app.services.ingestion.normalise import Identity, TicketRow, looks_packed
from app.services.ingestion.sources import TicketSource
from app.services.inventory import SoldOut, commit_seats
from app.services.mailer.outbox import TEMPLATE_TICKET_READY, QueuedEmail, queue_email

log = structlog.get_logger(__name__)

# The `ingestion_errors.reason` vocabulary. Constants rather than inline strings
# because the reason is part of the partial unique index that de-duplicates an
# open problem across runs: a typo here silently doubles a row in the admin tray.
REASON_BUYER_UNREADABLE = "buyer identity unreadable"
REASON_BUYER_TAKEN = "buyer already holds a live ticket"
REASON_PARTNER_TAKEN = "partner already holds a live ticket"
REASON_PARTNER_IS_BUYER = "partner identity is the buyer"
REASON_NO_SEATS = "no seats remain for this row"
REASON_CONFLICT = "row conflicted with an existing ticket"

#: Prefix for `tickets.ticket_ref`, which appears in the claim URL `/r/<ref>`.
TICKET_REF_PREFIX = "FS26"
#: How much of the row hash goes into a ticket_ref. 10 hex chars = 40 bits; the
#: `tickets_event_ref_key` UNIQUE constraint is what actually guarantees it.
TICKET_REF_HASH_CHARS = 10


def _salvage_reason(who: str, identity: Identity) -> str:
    return f"{who} identity salvaged from a malformed value ({identity.status})"


class IngestReport(BaseModel):
    """What one run did. Counts only — never attendee data, so it is safe to log."""

    source_key: str
    rows_seen: int = 0
    orders_created: int = 0
    orders_updated: int = 0
    tickets_minted: int = 0
    seats_committed: int = 0
    emails_queued: int = 0
    rows_flagged: int = 0
    rows_skipped: int = 0
    #: Set when the batch stopped early. Absent on a clean run.
    halted_reason: str | None = None
    errors_recorded: int = 0

    @property
    def halted(self) -> bool:
        return self.halted_reason is not None


class _RowVerdict(BaseModel):
    """Blocking problems refuse the row; flags mint it and tell an admin."""

    blocking: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)

    @property
    def mintable(self) -> bool:
        return not self.blocking


def row_external_id(source_key: str, row: TicketRow) -> str:
    """The stable row hash (LLD-v1 §7.2).

    Deliberately built from identity, not from position: the client inserts and
    deletes rows while fixing the sheet, and hashing the row number would re-mint
    everything below the edit. Deliberately excludes name and phone, which are
    the mutable columns a re-sync is allowed to update in place.
    """
    partner = row.partner.net_id if row.partner and row.partner.usable else ""
    material = "|".join(
        (source_key, row.ticket_type, row.buyer.net_id or "", partner)
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def ticket_ref(external_id: str, seq: int) -> str:
    """Deterministic, so a ref is reproducible from the source row alone."""
    return f"{TICKET_REF_PREFIX}-{external_id[:TICKET_REF_HASH_CHARS].upper()}-{seq}"


def generate_claim_code() -> str:
    """A bearer credential: whoever holds it can bind the partner ticket to their
    own account. Never logged, never derived from the row."""
    return secrets.token_urlsafe(12)


def _row_raw(row: TicketRow) -> dict:
    """The normaliser's view of the row, for the admin tray.

    This DOES contain attendee data — an admin has to read it to repair the
    sheet. It goes to `ingestion_errors.raw` (ADMIN-only) and nowhere else.
    """
    return {
        "source": row.source,
        "source_row": row.source_row,
        "ticket_type": row.ticket_type,
        "name": row.name,
        "phone": row.phone,
        "buyer_raw": row.buyer.raw,
        "buyer_status": row.buyer.status,
        "partner_raw": row.partner.raw if row.partner else None,
        "partner_status": row.partner.status if row.partner else None,
        "partner_mode": row.partner_mode,
        "problems": list(row.problems),
    }


def row_ref_of(row: TicketRow) -> str:
    """Human-addressable position, e.g. `EARLY_BIRD/INFERNO:37`. Safe to log."""
    return f"{row.source}:{row.source_row}"


async def get_or_create_source(
    db: AsyncSession, *, event_id: UUID, source: TicketSource
) -> TicketSourceRecord:
    """One `ticket_sources` row per adapter, keyed on its logical `source_key`
    so renaming the workbook never orphans the orders it produced."""
    record = await db.scalar(
        select(TicketSourceRecord).where(
            TicketSourceRecord.event_id == event_id,
            TicketSourceRecord.source_key == source.source_key,
        )
    )
    if record is None:
        record = TicketSourceRecord(
            event_id=event_id,
            source_type=source.source_type,
            source_key=source.source_key,
            config={"identity_is_email": source.identity_is_email},
        )
        db.add(record)
        await db.flush()
    return record


async def _net_id_is_taken(db: AsyncSession, *, event_id: UUID, net_id: str) -> bool:
    """One identity, one live ticket. Derived from `TICKET_LIVE_STATUSES` rather
    than a hand-listed set, so this and `tickets_one_per_net_id` cannot drift."""
    found = await db.scalar(
        select(Ticket.id)
        .where(
            Ticket.event_id == event_id,
            Ticket.bound_net_id == net_id,
            Ticket.status.in_(TICKET_LIVE_STATUSES),
        )
        .limit(1)
    )
    return found is not None


async def record_error(
    db: AsyncSession,
    *,
    event_id: UUID,
    source_id: UUID | None,
    row_ref: str,
    reason: str,
    raw: dict | None,
) -> bool:
    """Append one row to the admin tray. Returns True if this call created it.

    `ON CONFLICT DO NOTHING` against the partial unique index means the same
    open problem on the same row exists once however many times the ingester
    runs — a re-run is the normal case, not the exception.
    """
    statement = (
        insert(IngestionError)
        .values(
            id=uuid7(),
            event_id=event_id,
            source_id=source_id,
            row_ref=row_ref,
            reason=reason,
            raw=raw,
        )
        .on_conflict_do_nothing(
            index_elements=[
                IngestionError.event_id,
                IngestionError.source_id,
                IngestionError.row_ref,
                IngestionError.reason,
            ],
            index_where=IngestionError.resolved_at.is_(None),
        )
        .returning(IngestionError.id)
    )
    created = await db.execute(statement)
    return created.scalar_one_or_none() is not None


async def row_verdict(
    db: AsyncSession, *, event_id: UUID, source: TicketSource, row: TicketRow
) -> _RowVerdict:
    """Decide whether a row may mint, per docs/design/cohort-and-verification.md.

    A couple row whose partner is unusable is NOT blocked — it mints as PROXY
    with a flag, because the buyer paid for two seats and dropping one would put
    the seat count out of step with the sheet. A couple row whose partner is
    *somebody else's* identity IS blocked for the same reason in reverse:
    minting half of it would silently sell one of the two seats twice.
    """
    verdict = _RowVerdict(flags=list(row.problems))

    if not row.buyer.usable:
        verdict.blocking.append(REASON_BUYER_UNREADABLE)
        return verdict

    # A bare NetID in Early Bird's NetID column is the documented shape. The same
    # value in Phase 1's email column is a row somebody typed wrong, so only the
    # email-shaped source treats a salvage as worth an admin's attention.
    if source.identity_is_email and row.buyer.status.startswith("SALVAGED"):
        verdict.flags.append(_salvage_reason("buyer", row.buyer))

    if await _net_id_is_taken(db, event_id=event_id, net_id=row.buyer.net_id):
        verdict.blocking.append(REASON_BUYER_TAKEN)

    partner = row.partner
    if partner is not None and partner.usable:
        if partner.net_id == row.buyer.net_id:
            # Minting this would bind both seats to one identity and trip
            # `tickets_one_per_identity` at claim time instead of here.
            verdict.blocking.append(REASON_PARTNER_IS_BUYER)
        elif await _net_id_is_taken(db, event_id=event_id, net_id=partner.net_id):
            verdict.blocking.append(REASON_PARTNER_TAKEN)
        elif source.identity_is_email and partner.status.startswith("SALVAGED"):
            verdict.flags.append(_salvage_reason("partner", partner))

    return verdict


def _mint_tickets(
    *, event_id: UUID, order: Order, row: TicketRow, external_id: str, guest_tag: GuestTag
) -> list[Ticket]:
    """One ticket per seat. A COUPLE mints two, counted separately, always.

    SELF_CLAIM gives the partner ticket a claim code and its own `bound_net_id`,
    so the partner signs in as themselves. PROXY leaves the second ticket
    unbound — there is nobody to claim it, and `tickets_one_per_net_id` would
    refuse a second ticket bound to the buyer anyway.
    """
    now = datetime.now(UTC)
    mode = PartnerMode(row.partner_mode)
    self_claim = mode is PartnerMode.SELF_CLAIM

    # Ids are assigned here rather than at flush so the partner ticket can point
    # at the buyer's in the same statement: `partner_of` is what makes the pair a
    # pair, and a second flush just to learn one uuid buys nothing.
    first = Ticket(
        id=uuid7(),
        event_id=event_id,
        order_id=order.id,
        ticket_ref=ticket_ref(external_id, 1),
        seq_in_order=1,
        status=TicketStatus.PURCHASED,
        ticket_type=row.ticket_type,
        partner_mode=mode if row.seats > 1 else None,
        guest_tag=guest_tag,
        bound_net_id=row.buyer.net_id,
        bound_at=now,
    )
    if row.seats == 1:
        return [first]

    second = Ticket(
        id=uuid7(),
        event_id=event_id,
        order_id=order.id,
        ticket_ref=ticket_ref(external_id, 2),
        seq_in_order=2,
        partner_of=first.id,
        status=TicketStatus.PURCHASED,
        ticket_type=row.ticket_type,
        partner_mode=mode,
        claim_code=generate_claim_code() if self_claim else None,
        guest_tag=guest_tag,
        bound_net_id=row.partner.net_id if self_claim and row.partner else None,
        bound_at=now if self_claim and row.partner else None,
    )
    return [first, second]


async def _queue_ticket_ready(
    db: AsyncSession, *, event_id: UUID, row: TicketRow, tickets: list[Ticket]
) -> int:
    """"Your ticket is ready, log in here" (LLD-v2 §5). Queued only; M6 sends.

    Keyed on `ticket_ref`, which is derived from the row hash, so the second run
    of a workbook queues nothing and nobody gets a duplicate.
    """
    queued = 0
    recipients: list[tuple[Identity, Ticket]] = [(row.buyer, tickets[0])]
    if len(tickets) > 1 and tickets[1].bound_net_id and row.partner:
        recipients.append((row.partner, tickets[1]))
    greetings = _greetings(row)

    for (identity, ticket), greeting in zip(recipients, greetings, strict=False):
        if not identity.email:
            continue
        created = await queue_email(
            db,
            event_id=event_id,
            message=QueuedEmail(
                to_email=identity.email,
                template=TEMPLATE_TICKET_READY,
                idempotency_key=f"{TEMPLATE_TICKET_READY}:{ticket.ticket_ref}",
                payload={
                    "ticket_ref": ticket.ticket_ref,
                    "ticket_type": ticket.ticket_type,
                    "name": greeting,
                    "claim_code": ticket.claim_code,
                    "seats_on_this_order": len(tickets),
                },
            ),
        )
        queued += int(created)
    return queued


def _greetings(row: TicketRow) -> tuple[str, str]:
    """Who each email on this row says "Hi" to: (buyer, partner).

    Each person by their own name, never the other's. A name cell that still
    holds two people (one the initials could not untangle) greets nobody by
    name, and a partner the sheet does not name gets "Hi," too.
    """
    buyer = "" if looks_packed(row.name) else row.name
    return buyer, row.partner_name


async def _refresh_queued_greetings(db: AsyncSession, *, order: Order, row: TicketRow) -> bool:
    """Bring the greeting on this order's still-QUEUED mail in line with the
    sheet. Sent mail is history and is never edited. True if anything changed.

    This is how the emails queued before names were split get corrected: run
    the same sheet again.
    """
    tickets = (
        await db.scalars(
            select(Ticket).where(Ticket.order_id == order.id).order_by(Ticket.seq_in_order)
        )
    ).all()
    changed = False
    for ticket, greeting in zip(tickets, _greetings(row), strict=False):
        mail = await db.scalar(
            select(EmailOutbox).where(
                EmailOutbox.idempotency_key == f"{TEMPLATE_TICKET_READY}:{ticket.ticket_ref}",
                EmailOutbox.status == EmailStatus.QUEUED.value,
            )
        )
        if mail is not None and (mail.payload or {}).get("name") != greeting:
            # A new dict, not an in-place edit: JSONB change tracking only
            # notices reassignment.
            mail.payload = {**(mail.payload or {}), "name": greeting}
            changed = True
    return changed


def _update_mutable(order: Order, row: TicketRow) -> bool:
    """§7.2: a re-sync updates name and phone, and nothing else. It never
    resurrects a cancelled ticket and never moves a seat."""
    changed = False
    if row.name and order.purchaser_name != row.name:
        order.purchaser_name = row.name
        changed = True
    if row.phone and order.purchaser_phone != row.phone:
        order.purchaser_phone = row.phone
        changed = True
    return changed


async def ingest_source(
    db: AsyncSession,
    *,
    event: Event,
    source: TicketSource,
    actor_user_id: UUID | None = None,
    queue_mail: bool = True,
    guest_tag: GuestTag = GuestTag.BAARATI,
) -> IngestReport:
    """Run one source to completion. Commits per row; never raises on bad data.

    The only exceptions that escape are programming errors and a dead database.
    Everything the sheet can do to us lands in `ingestion_errors`.

    `guest_tag` stamps every ticket this run mints. It defaults to BAARATI for
    the scripts and tests that mint baaratis; `scripts/ingest.py` makes the
    operator choose it explicitly.
    """
    report = IngestReport(source_key=source.source_key)
    # Snapshot the id: a rollback on the row below expires every loaded ORM
    # object, and reading `event.id` afterwards would attempt lazy IO from an
    # exception handler — which asyncpg cannot service.
    event_id = event.id
    record = await get_or_create_source(db, event_id=event_id, source=source)
    source_id = record.id
    await db.commit()

    # Hashes already handled this run. The same purchase written twice hashes
    # identically and is one order (the identity is the hash); without this,
    # each copy would also take its turn refreshing name and phone, so copies
    # that differ -- `zz107` sent one form three times with three numbers --
    # flip the stored details on every run. First row in the sheet wins.
    seen: set[str] = set()

    async for row in source.fetch():
        report.rows_seen += 1
        row_ref = row_ref_of(row)
        external_id = row_external_id(source.source_key, row)
        if external_id in seen:
            continue
        seen.add(external_id)

        existing = await db.scalar(
            select(Order).where(Order.event_id == event_id, Order.external_id == external_id)
        )
        if existing is not None:
            details = _update_mutable(existing, row)
            greetings = await _refresh_queued_greetings(db, order=existing, row=row)
            if details or greetings:
                report.orders_updated += 1
            await db.commit()
            continue

        verdict = await row_verdict(db, event_id=event_id, source=source, row=row)
        for reason in verdict.flags:
            report.errors_recorded += int(
                await record_error(
                    db,
                    event_id=event_id,
                    source_id=source_id,
                    row_ref=row_ref,
                    reason=reason,
                    raw=_row_raw(row),
                )
            )
        if verdict.flags:
            report.rows_flagged += 1

        if not verdict.mintable:
            for reason in verdict.blocking:
                report.errors_recorded += int(
                    await record_error(
                        db,
                        event_id=event_id,
                        source_id=source_id,
                        row_ref=row_ref,
                        reason=reason,
                        raw=_row_raw(row),
                    )
                )
            report.rows_skipped += 1
            await db.commit()
            continue

        try:
            order = Order(
                event_id=event_id,
                source_id=source_id,
                external_id=external_id,
                purchaser_name=row.name or None,
                purchaser_email=row.buyer.email,
                purchaser_phone=row.phone or None,
                net_id_hint=row.buyer.net_id,
                quantity=row.seats,
                ticket_type=row.ticket_type,
                payment_status="PAID",
                raw_payload=_row_raw(row),
            )
            db.add(order)
            await db.flush()

            tickets = _mint_tickets(
                event_id=event_id, order=order, row=row, external_id=external_id,
                guest_tag=guest_tag,
            )
            db.add_all(tickets)
            await db.flush()

            # Seats move in the same transaction as the order that consumed
            # them, under the §A1.2 row lock. Never before, never after.
            await commit_seats(
                db,
                event_id=event_id,
                seats=row.seats,
                reason=LedgerReason.INGEST,
                order_id=order.id,
                actor_user_id=actor_user_id,
            )
            # Off only for scripts/demo_ticket.py: a demo seat must never ride
            # along in the one-time send to every real attendee.
            queued = (
                await _queue_ticket_ready(db, event_id=event_id, row=row, tickets=tickets)
                if queue_mail
                else 0
            )
            await db.commit()
        except SoldOut as exc:
            await db.rollback()
            report.errors_recorded += int(
                await record_error(
                    db,
                    event_id=event_id,
                    source_id=source_id,
                    row_ref=row_ref,
                    reason=REASON_NO_SEATS,
                    raw=_row_raw(row),
                )
            )
            await db.commit()
            # Not a row defect. Carrying on would admit whichever rows happened
            # to fit and leave the rest out, which is worse than stopping.
            report.halted_reason = f"{REASON_NO_SEATS}: {exc}"
            log.warning(
                "ingest.halted",
                source=source.source_key,
                row_ref=row_ref,
                requested=exc.requested,
                available=exc.available,
            )
            break
        except IntegrityError as exc:
            # A constraint we did not anticipate — `tickets_one_per_net_id`
            # losing a race, most likely. The row is the casualty, not the batch.
            await db.rollback()
            report.errors_recorded += int(
                await record_error(
                    db,
                    event_id=event_id,
                    source_id=source_id,
                    row_ref=row_ref,
                    reason=REASON_CONFLICT,
                    raw=_row_raw(row),
                )
            )
            await db.commit()
            report.rows_skipped += 1
            log.warning(
                "ingest.row_conflict",
                source=source.source_key,
                row_ref=row_ref,
                constraint=getattr(getattr(exc, "orig", None), "constraint_name", None),
            )
            continue

        report.orders_created += 1
        report.tickets_minted += len(tickets)
        report.seats_committed += row.seats
        report.emails_queued += queued

    record = await db.get(TicketSourceRecord, source_id)
    if record is not None:
        record.last_synced_at = datetime.now(UTC)
    await db.commit()

    log.info("ingest.complete", **report.model_dump(exclude_none=True))
    return report
