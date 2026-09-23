"""Two mail workers, one queue — the property `FOR UPDATE SKIP LOCKED` exists for.

Run it; don't reason about it. These open real connections and fire real
transactions at the same rows at the same instant. They cannot use the shared
`db_session` fixture, which is one connection inside one outer transaction —
that would serialise the test by accident and prove nothing, which is exactly
what happened when this file did not exist: deleting the `SENDING` claim from
the worker broke nothing, because every other test drains single-threaded.

The invariant: **every row is sent exactly once, no matter how many workers
run.** For a "your ticket is ready" mail to 441 people, a second copy is an
embarrassment and a third is a support queue.
"""
import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import EmailOutbox, EmailStatus, Event
from app.services.mailer.outbox import TEMPLATE_TICKET_READY, QueuedEmail, queue_email
from app.services.mailer.provider import EmailMessage
from app.services.mailer.worker import drain

WORKERS = 4
QUEUED_ROWS = 12


class CountingMailer:
    """Shared across workers, so a double-send is visible as a duplicate here.

    Sleeps a tick inside `send` on purpose: without it each worker would drain
    the whole queue before the next one started, and the race this test exists
    to create would never happen.
    """

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, message: EmailMessage) -> None:
        await asyncio.sleep(0.01)
        self.sent.append(str(message.to))


@pytest_asyncio.fixture
async def committed_queue(db_engine):
    """Real, committed outbox rows that other connections can actually see."""
    maker = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with maker() as session:
        event = Event(
            slug=f"mail-concurrency-{uuid4().hex[:8]}",
            name="Mail Rehearsal",
            starts_at=now + timedelta(days=1),
            ends_at=now + timedelta(days=1, hours=5),
            status="LIVE",
            config={},
        )
        session.add(event)
        await session.flush()
        for index in range(QUEUED_ROWS):
            await queue_email(
                session,
                event_id=event.id,
                message=QueuedEmail(
                    to_email=f"person{index}@snu.edu.in",
                    template=TEMPLATE_TICKET_READY,
                    idempotency_key=f"{event.slug}:{index}",
                    payload={"name": f"Person {index}"},
                ),
            )
        await session.commit()

    yield maker, event

    async with maker() as session:
        # ON DELETE CASCADE from `events` still issues a DELETE against
        # `scans`, which the append-only trigger refuses. Same dance as
        # test_scan_concurrency.py, and lifting the trigger to tear down is
        # itself evidence that the trigger is what makes the table immutable.
        await session.execute(text("ALTER TABLE scans DISABLE TRIGGER USER"))
        await session.execute(text("DELETE FROM events WHERE id = :id"), {"id": event.id})
        await session.execute(text("ALTER TABLE scans ENABLE TRIGGER USER"))
        await session.commit()


async def _drain_once(maker, mailer):
    async with maker() as session:
        return await drain(session, mailer, limit=5)


async def test_four_workers_send_each_message_exactly_once(committed_queue):
    maker, event = committed_queue
    mailer = CountingMailer()

    reports = await asyncio.gather(
        *(_drain_once(maker, mailer) for _ in range(WORKERS))
    )

    # Nobody got two copies.
    assert len(mailer.sent) == len(set(mailer.sent)) == QUEUED_ROWS
    # ...and between them the workers accounted for every row exactly once.
    assert sum(report.sent for report in reports) == QUEUED_ROWS
    assert sum(report.failed for report in reports) == 0

    async with maker() as session:
        remaining = await session.scalar(
            select(func.count())
            .select_from(EmailOutbox)
            .where(
                EmailOutbox.event_id == event.id,
                EmailOutbox.status != EmailStatus.SENT.value,
            )
        )
    assert remaining == 0


async def test_a_second_worker_walks_past_locked_rows_instead_of_blocking(committed_queue):
    """SKIP LOCKED is what makes a second worker useful rather than merely
    patient: it takes different rows, it does not queue behind the first."""
    maker, _ = committed_queue
    mailer = CountingMailer()

    first, second = await asyncio.gather(
        _drain_once(maker, mailer), _drain_once(maker, mailer)
    )

    # Both did real work — if the second had blocked on the first's lock it
    # would have woken to an empty queue and claimed nothing.
    assert first.sent > 0
    assert second.sent > 0
    assert first.sent + second.sent == QUEUED_ROWS
