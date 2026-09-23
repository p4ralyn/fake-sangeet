"""What the ops dashboard reads. All queries, no writes.

Two things this deliberately does NOT do:

* **It does not keep a `notifications` table.** Every alert is computed from
  rows that already exist — the review queue, the ingestion tray, the outbox,
  the scan log. There is no write path for a service to forget, and the feed
  cannot drift out of sync with the thing it describes. The price is no
  read/unread and no history once a condition clears, which is the right trade
  for a one-night event.
* **It does not cache.** Every number is a count against an indexed column on a
  database holding hundreds of rows, not millions. A cache here would add a way
  for the occupancy figure to be wrong, which is the one thing it must not be.
"""
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    VERIFY_OPEN_OUTCOMES,
    EmailOutbox,
    EmailStatus,
    EventInventory,
    IngestionError,
    Pass,
    PassState,
    Scan,
    ScanResult,
    Verification,
)
from app.schemas.ops import (
    MailHealth,
    MailProblem,
    Occupancy,
    OpsSummary,
    Queues,
    Seats,
)

#: How far back "recent" looks on the Tonight panel. Long enough to survive a
#: lull between arrivals, short enough that a dead gate shows up quickly.
RECENT_WINDOW_MINUTES = 15


async def occupancy(db: AsyncSession, *, event_id: UUID) -> Occupancy:
    rows = await db.execute(
        select(Pass.current_state, func.count())
        .where(Pass.event_id == event_id)
        .group_by(Pass.current_state)
    )
    counts = {state: total for state, total in rows.all()}
    return Occupancy(
        inside=counts.get(PassState.INSIDE, 0),
        on_break=counts.get(PassState.ON_BREAK, 0),
        departed=counts.get(PassState.DEPARTED, 0),
        not_arrived=counts.get(PassState.NOT_ARRIVED, 0),
        revoked=counts.get(PassState.REVOKED, 0),
    )


async def seats(db: AsyncSession, *, event_id: UUID) -> Seats:
    inventory = await db.get(EventInventory, event_id)
    if inventory is None:
        return Seats(committed=0, total=0)
    return Seats(committed=inventory.seats_committed, total=inventory.seats_total)


async def _count(db: AsyncSession, model, *where) -> int:
    return await db.scalar(select(func.count()).select_from(model).where(*where)) or 0


async def queues(db: AsyncSession, *, event_id: UUID) -> Queues:
    ids_waiting = await _count(
        db,
        Verification,
        Verification.event_id == event_id,
        Verification.outcome.in_(VERIFY_OPEN_OUTCOMES),
    )
    oldest = await db.scalar(
        select(func.min(Verification.created_at)).where(
            Verification.event_id == event_id,
            Verification.outcome.in_(VERIFY_OPEN_OUTCOMES),
        )
    )
    oldest_minutes = (
        int((datetime.now(UTC) - oldest).total_seconds() // 60) if oldest else None
    )
    return Queues(
        ids_waiting=ids_waiting,
        ids_oldest_minutes=oldest_minutes,
        ingestion_unresolved=await _count(
            db,
            IngestionError,
            IngestionError.event_id == event_id,
            IngestionError.resolved_at.is_(None),
        ),
        mail_queued=await _count(
            db,
            EmailOutbox,
            EmailOutbox.event_id == event_id,
            EmailOutbox.status == EmailStatus.QUEUED.value,
        ),
        mail_dead=await _count(
            db,
            EmailOutbox,
            EmailOutbox.event_id == event_id,
            EmailOutbox.status == EmailStatus.DEAD.value,
        ),
    )


async def summary(db: AsyncSession, *, event_id: UUID) -> OpsSummary:
    since = datetime.now(UTC) - timedelta(minutes=RECENT_WINDOW_MINUTES)
    return OpsSummary(
        occupancy=await occupancy(db, event_id=event_id),
        seats=await seats(db, event_id=event_id),
        queues=await queues(db, event_id=event_id),
        scans_recent=await _count(
            db, Scan, Scan.event_id == event_id, Scan.occurred_at >= since
        ),
        refusals_recent=await _count(
            db,
            Scan,
            Scan.event_id == event_id,
            Scan.occurred_at >= since,
            Scan.result == ScanResult.DENIED.value,
        ),
        scans_window_minutes=RECENT_WINDOW_MINUTES,
        generated_at=datetime.now(UTC),
    )


async def mail_health(db: AsyncSession, *, event_id: UUID, limit: int = 50) -> MailHealth:
    """Outbox counts, plus the rows that need a person.

    "Problems" is dead rows and rows that have failed at least once — a row
    sitting in QUEUED having never been tried is not a problem, it is a queue.
    """
    rows = await db.execute(
        select(EmailOutbox.status, func.count())
        .where(EmailOutbox.event_id == event_id)
        .group_by(EmailOutbox.status)
    )
    counts = {status: total for status, total in rows.all()}

    problems = (
        await db.scalars(
            select(EmailOutbox)
            .where(
                EmailOutbox.event_id == event_id,
                (EmailOutbox.status == EmailStatus.DEAD.value)
                | (EmailOutbox.attempts > 0),
            )
            .order_by(EmailOutbox.attempts.desc(), EmailOutbox.next_attempt_at)
            .limit(limit)
        )
    ).all()

    return MailHealth(
        queued=counts.get(EmailStatus.QUEUED.value, 0),
        sending=counts.get(EmailStatus.SENDING.value, 0),
        sent=counts.get(EmailStatus.SENT.value, 0),
        failed=counts.get(EmailStatus.FAILED.value, 0),
        dead=counts.get(EmailStatus.DEAD.value, 0),
        problems=[
            MailProblem(
                id=row.id,
                to_email=str(row.to_email),
                template=row.template,
                status=row.status,
                attempts=row.attempts,
                last_error=row.last_error,
                next_attempt_at=row.next_attempt_at,
            )
            for row in problems
        ],
    )
