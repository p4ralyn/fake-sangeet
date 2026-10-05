"""Several admins, one ID card, decided at the same instant.

The shape on the night: three people have the review queue open, everyone's
oldest card is the same card, and two of them press different buttons within
milliseconds of each other. Exactly one decision may land. The rest must be
told "somebody already decided this one" — never applied on top, because an
approve-then-refuse on top of each other leaves a REFUSED person holding a
pass.

Like the scan race, this needs real separate connections: the shared
`db_session` fixture is one connection in one transaction and would serialise
the test by accident.
"""
import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Event, EventInventory, Order, Pass, Ticket, User
from app.models.ticketing import TicketStatus
from app.models.verification import Verification, VerifyOutcome
from app.services.storage.provider import get_object_store
from app.services.verification import service
from tests.images import jpeg_bytes

CONCURRENT_REVIEWERS = 6


@pytest_asyncio.fixture
async def committed_review(db_engine):
    """A real, committed open review, plus reviewers, visible to every connection."""
    maker = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime.now(UTC)
    suffix = uuid4().hex[:6]
    async with maker() as session:
        event = Event(
            slug=f"review-race-{suffix}",
            name="Review Race",
            starts_at=now + timedelta(days=30),
            ends_at=now + timedelta(days=30, hours=5),
            status="LIVE",
            config={},
        )
        session.add(event)
        await session.flush()
        session.add(EventInventory(event_id=event.id, seats_total=10))
        holder = User(net_id=f"hr{suffix[:3]}", email=f"hr{suffix[:3]}@snu.edu.in",
                      display_name="Aarav Sharma")
        reviewers = [
            User(net_id=f"rv{suffix[:2]}{i}", email=f"rv{suffix[:2]}{i}@snu.edu.in",
                 display_name=f"Reviewer {i}")
            for i in range(CONCURRENT_REVIEWERS)
        ]
        session.add_all([holder, *reviewers])
        order = Order(event_id=event.id, external_id=f"race-{suffix}",
                      purchaser_email=holder.email, purchaser_name="Aarav Sharma", quantity=1)
        session.add(order)
        await session.flush()
        ticket = Ticket(event_id=event.id, order_id=order.id, ticket_ref=f"FS26-RACE-{suffix}",
                        status=TicketStatus.PURCHASED, bound_net_id=holder.net_id)
        session.add(ticket)
        await session.flush()
        pending = await service.submit_id_image(
            session, get_object_store(), event=event, user=holder,
            raw=jpeg_bytes(), consented=True,
        )
        await session.commit()

    yield maker, event, pending.id, ticket.id, reviewers

    async with maker() as session:
        # Everything this fixture committed, in the order the foreign keys
        # allow: the audit rows name the reviewers, and `scans` (append-only
        # by trigger) is reached by the event's cascade.
        user_ids = [holder.id, *[r.id for r in reviewers]]
        await session.execute(
            text("DELETE FROM audit_log WHERE event_id = :e OR actor_user_id = ANY(:u)"),
            {"e": event.id, "u": user_ids},
        )
        await session.execute(text("ALTER TABLE scans DISABLE TRIGGER USER"))
        await session.execute(text("DELETE FROM events WHERE id = :id"), {"id": event.id})
        await session.execute(text("ALTER TABLE scans ENABLE TRIGGER USER"))
        await session.execute(text("DELETE FROM users WHERE id = ANY(:u)"), {"u": user_ids})
        await session.commit()


async def _decide(maker, event, verification_id, reviewer, approve):
    async with maker() as session:
        try:
            decided = await service.review_verification(
                session, event=event, verification_id=verification_id, reviewer=reviewer,
                reviewer_role="ADMIN", approve=approve, allow_resubmit=False,
            )
            await session.commit()
            return decided.outcome
        except service.AlreadyDecided as exc:
            await session.rollback()
            return exc


async def test_exactly_one_of_several_simultaneous_decisions_lands(committed_review):
    maker, event, verification_id, ticket_id, reviewers = committed_review

    results = await asyncio.gather(*[
        # Alternate approve / refuse, so a lost race shows up as a contradiction.
        _decide(maker, event, verification_id, reviewer, approve=index % 2 == 0)
        for index, reviewer in enumerate(reviewers)
    ])

    landed = [r for r in results if isinstance(r, VerifyOutcome)]
    refused = [r for r in results if isinstance(r, service.AlreadyDecided)]
    assert len(landed) == 1, results
    assert len(refused) == CONCURRENT_REVIEWERS - 1

    async with maker() as session:
        verification = await session.get(Verification, verification_id)
        ticket = await session.get(Ticket, ticket_id)
        passes = await session.scalar(
            select(func.count()).select_from(Pass).where(Pass.ticket_id == ticket_id)
        )
    # Whatever won is the whole story — the ticket and the pass agree with it.
    assert verification.outcome is landed[0]
    if landed[0] is VerifyOutcome.APPROVED:
        assert ticket.status is TicketStatus.PASS_ISSUED and passes == 1
    else:
        assert ticket.status is TicketStatus.REJECTED and passes == 0
