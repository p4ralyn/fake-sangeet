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
    SEVERITY_ORDER,
    IngestionErrorItem,
    IngestionTray,
    MailHealth,
    MailProblem,
    Notification,
    NotificationFeed,
    Occupancy,
    OpsSummary,
    Queues,
    Seats,
    Severity,
)

#: How far back "recent" looks on the Tonight panel. Long enough to survive a
#: lull between arrivals, short enough that a dead gate shows up quickly.
RECENT_WINDOW_MINUTES = 15

#: An ID waiting longer than this means the review desk is behind. Fifteen
#: minutes is roughly how long somebody will wait before asking a volunteer
#: what is happening.
IDS_STALE_MINUTES = 15

#: Occupancy alert threshold, as LLD-v1 §6's `warn_at_pct`.
OCCUPANCY_WARN_PCT = 90


def plural(count: int, singular: str, many: str | None = None) -> str:
    """"1 email" / "2 emails". These strings are read at a glance by somebody
    deciding whether to act, and "1 emails gave up" reads as a broken page."""
    return f"{count} {singular if count == 1 else (many or singular + 's')}"


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


async def notifications(db: AsyncSession, *, event_id: UUID) -> NotificationFeed:
    """The derived feed. Each rule reads one fact and says what to do about it.

    Ordered worst-first. A feed that buries a dead gate under three information
    lines is a feed nobody reads twice.
    """
    facts = await summary(db, event_id=event_id)
    items: list[Notification] = []

    if facts.queues.mail_dead:
        items.append(
            Notification(
                kind="MAIL_DEAD",
                severity=Severity.CRITICAL,
                title=f"{plural(facts.queues.mail_dead, 'email')} gave up",
                detail=(
                    "These people were never told their ticket is ready. "
                    "Check the error and re-queue or contact them."
                ),
                href="/ops/mail",
                count=facts.queues.mail_dead,
            )
        )

    if facts.refusals_recent:
        items.append(
            Notification(
                kind="SCAN_REFUSALS",
                severity=Severity.WARNING,
                title=f"{plural(facts.refusals_recent, 'scan')} refused at the gate",
                detail=(
                    f"In the last {facts.scans_window_minutes} minutes. Revoked, "
                    "unknown or superseded passes — worth a look if it keeps climbing."
                ),
                count=facts.refusals_recent,
            )
        )

    stale = facts.queues.ids_oldest_minutes
    if facts.queues.ids_waiting and stale is not None and stale >= IDS_STALE_MINUTES:
        items.append(
            Notification(
                kind="IDS_STALE",
                severity=Severity.WARNING,
                title=f"An ID has waited {stale} minutes",
                detail=(
                    f"{facts.queues.ids_waiting} waiting. Somebody is refreshing a page "
                    "wondering whether it worked."
                ),
                href="/ops/ids",
                count=facts.queues.ids_waiting,
            )
        )
    elif facts.queues.ids_waiting:
        items.append(
            Notification(
                kind="IDS_WAITING",
                severity=Severity.INFO,
                title=f"{plural(facts.queues.ids_waiting, 'ID')} to review",
                detail="The queue is keeping up.",
                href="/ops/ids",
                count=facts.queues.ids_waiting,
            )
        )

    if facts.queues.ingestion_unresolved:
        items.append(
            Notification(
                kind="INGESTION_ERRORS",
                severity=Severity.WARNING,
                title=(
                    f"{plural(facts.queues.ingestion_unresolved, 'sheet row')} "
                    f"{'needs' if facts.queues.ingestion_unresolved == 1 else 'need'} a human"
                ),
                detail=(
                    "These people have no ticket until the row is fixed and the "
                    "ingester is run again."
                ),
                href="/ops/ingestion",
                count=facts.queues.ingestion_unresolved,
            )
        )

    if facts.seats.total:
        filled_pct = round(100 * facts.occupancy.inside / facts.seats.total)
        if filled_pct >= OCCUPANCY_WARN_PCT:
            items.append(
                Notification(
                    kind="OCCUPANCY_HIGH",
                    severity=Severity.WARNING,
                    title=f"Room is {filled_pct}% full",
                    detail=(
                        f"{facts.occupancy.inside} of {facts.seats.total} inside. "
                        "The cap is a fire limit, not a target."
                    ),
                    count=facts.occupancy.inside,
                )
            )

    if facts.queues.mail_queued:
        items.append(
            Notification(
                kind="MAIL_QUEUED",
                severity=Severity.INFO,
                title=f"{plural(facts.queues.mail_queued, 'email')} waiting to send",
                detail="Run scripts/mailer.py, or leave it if the worker is running.",
                href="/ops/mail",
                count=facts.queues.mail_queued,
            )
        )

    items.sort(key=lambda item: SEVERITY_ORDER[item.severity])
    return NotificationFeed(items=items, generated_at=facts.generated_at)


async def ingestion_tray(
    db: AsyncSession, *, event_id: UUID, limit: int = 100
) -> IngestionTray:
    """The rows M3 could not mint. Until this screen existed they sat in a
    table nobody could see."""
    rows = (
        await db.scalars(
            select(IngestionError)
            .where(
                IngestionError.event_id == event_id,
                IngestionError.resolved_at.is_(None),
            )
            .order_by(IngestionError.created_at)
            .limit(limit)
        )
    ).all()
    return IngestionTray(
        unresolved=await _count(
            db,
            IngestionError,
            IngestionError.event_id == event_id,
            IngestionError.resolved_at.is_(None),
        ),
        items=[
            IngestionErrorItem(
                id=row.id,
                row_ref=row.row_ref,
                reason=row.reason,
                raw=row.raw,
                created_at=row.created_at,
            )
            for row in rows
        ],
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
